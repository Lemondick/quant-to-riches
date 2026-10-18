"""Static attribution must use original account evidence and verified exact-date marks."""

import hashlib
import os
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis_context import FACTS
from operations import execute

from quant_assistant.data.snapshots import _frame_sha256
from quant_assistant.models.repositories.database import Database


class AutomaticAttributionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quant-auto-attribution-")
        self.root = Path(self.temporary.name).resolve()
        self.environment = patch.dict(os.environ, QUANT_SITE_ROOT=str(self.root))
        self.environment.start()
        self.database = Database(self.root / "quant_assistant.duckdb")
        self.database.initialize()
        self.tables = {table: [] for table in FACTS}
        self.tables["portfolio_events"] = [
            {
                "event_id": "baseline",
                "event_type": "baseline",
                "effective_date": "2026-01-02",
                "amount": "0",
                "status": "confirmed",
                "reason": "synthetic",
                "recorded_at": "2026-01-02T18:00:00+08:00",
                "details_json": {},
            }
        ]
        positions = [
            {
                "symbol": "600000.SH",
                "asset_type": "stock",
                "quantity": "20",
                "average_cost": "10",
                "market_value": "200",
                "as_of_date": "2026-01-02",
            },
            {
                "symbol": "600001.SH",
                "asset_type": "stock",
                "quantity": "30",
                "average_cost": "10",
                "market_value": "300",
                "as_of_date": "2026-01-02",
            },
            {
                "symbol": "CASH.CNY",
                "asset_type": "cash",
                "quantity": "500",
                "average_cost": "1",
                "market_value": "500",
                "as_of_date": "2026-01-02",
            },
        ]
        self.tables["audit_log"] = [
            {
                "id": 1,
                "entity_type": "portfolio_account_import",
                "entity_id": "baseline",
                "changed_at": "2026-01-02T18:00:00+08:00",
                "before_json": None,
                "after_json": {"positions": positions},
                "actor": "synthetic",
            }
        ]
        # Current holdings deliberately differ: they cannot be used for the period.
        self.tables["positions"] = [
            {**positions[0], "quantity": "999", "market_value": "9990", "as_of_date": "2026-02-05"}
        ]
        self.tables["benchmark_configs"] = [
            {
                "benchmark_id": "basket",
                "name": "合成固定基准",
                "components_json": {"600000.SH": 0.4, "600002.SH": 0.6},
                "is_default": True,
                "created_at": "2026-01-01T00:00:00+08:00",
                "updated_at": "2026-01-01T00:00:00+08:00",
            }
        ]
        frame = pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "trade_date": date.fromisoformat(day),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 100.0,
                    "amount": 1000.0,
                }
                for symbol, end in (("600000.SH", 11.0), ("600001.SH", 12.0), ("600002.SH", 10.5))
                for day, price in (("2026-01-02", 10.0), ("2026-01-30", end))
            ]
        )
        self.price_file = self.root / "verified-prices.parquet"
        frame.to_parquet(self.price_file, index=False)
        self.tables["data_snapshots"] = [
            {
                "id": 7,
                "dataset": "daily_prices",
                "source": "synthetic-acceptance",
                "as_of_date": "2026-01-30",
                "fetched_at": "2026-01-30T18:00:00+08:00",
                "path": str(self.price_file),
                "sha256": _frame_sha256(frame),
                "row_count": len(frame),
                "status": "valid",
                "issues_json": [],
            }
        ]
        with self.database.connection() as connection:
            connection.register("fixture_prices", frame.assign(source_snapshot_id=7))
            connection.execute("INSERT INTO daily_prices BY NAME SELECT * FROM fixture_prices")
            connection.unregister("fixture_prices")
            for symbol, industry in (
                ("600000.SH", "科技"),
                ("600001.SH", "科技"),
                ("600002.SH", "金融"),
            ):
                connection.execute(
                    "INSERT INTO instrument_history(symbol,asset_type,name,industry,as_of_date) "
                    "VALUES(?,'stock',?,?,?)",
                    [symbol, symbol, industry, date(2026, 1, 2)],
                )

    def tearDown(self):
        self.database.close()
        self.environment.stop()
        self.temporary.cleanup()

    def run_operation(self, ledger=False):
        self.database.close()
        payload = {
            "snapshot": {"version": 1, "user_id": "a" * 32, "role": "admin", "tables": self.tables},
            "benchmark_id": "basket",
            "start_date": "2026-01-02",
            "end_date": "2026-01-30",
            "ledger": ledger,
        }
        try:
            return execute("analysis.brinson", payload)
        except ValueError as failure:
            self.fail(f"automatic attribution should return a ready/blocked result: {failure}")

    def test_original_holdings_exact_prices_and_source_fingerprints(self):
        result = self.run_operation()
        self.assertEqual(result["status"], "ready")
        rows = {row["symbol"]: row for row in result["portfolio"]}
        self.assertEqual(rows["600000.SH"]["initial_value"], "200")
        self.assertEqual(rows["600001.SH"]["period_return"], "0.2")
        self.assertEqual(rows["CASH.CNY"]["period_return"], "0")
        self.assertEqual(rows["600000.SH"]["sector_name"], "科技")
        self.assertEqual(result["provenance"]["baseline_id"], "baseline")
        self.assertEqual(result["provenance"]["source_snapshots"][0]["id"], 7)
        self.assertEqual(
            result["private_files"][0]["sha256"],
            hashlib.sha256(self.price_file.read_bytes()).hexdigest(),
        )

    def test_missing_original_audit_blocks_instead_of_using_current_positions(self):
        self.tables["audit_log"] = []
        result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("原始" in text for text in result["blockers"]))

    def test_period_trade_blocks_static_model(self):
        self.tables["transactions"] = [
            {
                "id": 1,
                "symbol": "600000.SH",
                "asset_type": "stock",
                "action": "buy",
                "quantity": "1",
                "price": "10",
                "fees": "0",
                "traded_at": "2026-01-10T10:00:00+08:00",
                "recorded_at": "2026-01-10T10:00:00+08:00",
                "external_id": "synthetic",
                "source": "synthetic",
            }
        ]
        result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("交易" in text for text in result["blockers"]))

    def test_ledger_mode_reads_verified_marks_and_accounts_for_sale_fee(self):
        self.tables["transactions"] = [
            {
                "id": 1,
                "symbol": "600000.SH",
                "asset_type": "stock",
                "action": "sell",
                "quantity": "10",
                "price": "11",
                "fees": "1",
                "cash_delta": "109",
                "traded_at": "2026-01-30T10:00:00+08:00",
                "recorded_at": "2026-01-30T10:01:00+08:00",
                "external_id": "synthetic-sale",
                "source": "synthetic",
                "cash_symbol": "CASH.CNY",
            }
        ]
        self.tables["audit_log"].append(
            {
                "id": 2,
                "entity_type": "transaction",
                "entity_id": "1",
                "changed_at": "2026-01-30T10:01:00+08:00",
                "before_json": None,
                "after_json": {},
                "actor": "synthetic",
            }
        )
        result = self.run_operation(ledger=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["provenance"]["method"], "ledger_close_periods_v1")
        self.assertEqual(result["periods"][0]["profit"], "79")
        self.assertEqual(result["periods"][0]["ending_value"], "1079")
        self.assertEqual(result["periods"][0]["portfolio_return"], "0.079")
        self.assertTrue(result["private_files"])

    def test_future_classification_is_not_backfilled(self):
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE instrument_history SET as_of_date='2026-02-01' WHERE symbol='600000.SH'"
            )
        result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("行业" in text and "600000.SH" in text for text in result["blockers"]))

    def test_empty_current_industry_does_not_override_verified_history(self):
        self.tables["instruments"] = [
            {
                "symbol": "600000.SH",
                "asset_type": "stock",
                "name": "Synthetic",
                "industry": None,
                "as_of_date": "2026-01-02",
            }
        ]
        result = self.run_operation()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["portfolio"][0]["sector_name"], "科技")

    def test_corrupted_price_file_blocks(self):
        frame = pd.read_parquet(self.price_file)
        frame.loc[0, "close"] = 999.0
        frame.to_parquet(self.price_file, index=False)
        result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("校验" in text for text in result["blockers"]))

    def test_missing_exact_date_never_uses_previous_close(self):
        with self.database.connection() as connection:
            connection.execute(
                "DELETE FROM daily_prices WHERE symbol='600000.SH' AND trade_date='2026-01-30'"
            )
        result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("2026-01-30" in text for text in result["blockers"]))

    def test_another_accounts_snapshot_path_blocks(self):
        with tempfile.TemporaryDirectory(prefix="quant-other-evidence-") as other:
            outside = Path(other) / "prices.parquet"
            outside.write_bytes(self.price_file.read_bytes())
            self.tables["data_snapshots"][0]["path"] = str(outside)
            result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("目录" in text for text in result["blockers"]))

    def test_industry_cache_changes_during_read_blocks(self):
        from attribution_operations import build_context

        def copy_then_change(snapshot):
            result = build_context(snapshot)
            with self.database.connection() as connection:
                connection.execute(
                    "UPDATE instrument_history SET industry='changed' WHERE symbol='600000.SH'"
                )
            self.database.close()
            return result

        with patch("attribution_operations.build_context", side_effect=copy_then_change):
            result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("缓存发生变化" in text for text in result["blockers"]))

    def seed_fund(self, observed="2026-01-01T18:00:00+08:00", weight=30):
        original = self.tables["audit_log"][0]["after_json"]["positions"][0]
        original.update(symbol="F", asset_type="fund")
        frame = pd.DataFrame(
            [
                {"symbol": "F", "nav_date": date(2026, 1, 2), "unit_nav": 10.0},
                {"symbol": "F", "nav_date": date(2026, 1, 30), "unit_nav": 11.0},
            ]
        )
        path = self.root / "verified-fund-nav.parquet"
        frame.to_parquet(path, index=False)
        self.tables["data_snapshots"].append(
            {
                "id": 8,
                "dataset": "fund_history_F",
                "source": "synthetic",
                "as_of_date": "2026-01-30",
                "fetched_at": "2026-01-30T18:00:00+08:00",
                "path": str(path),
                "sha256": _frame_sha256(frame),
                "row_count": 2,
                "status": "valid",
                "issues_json": [],
            }
        )
        with self.database.connection() as connection:
            connection.register("fixture_nav", frame.assign(source_snapshot_id=8))
            connection.execute("INSERT INTO fund_nav BY NAME SELECT * FROM fixture_nav")
            connection.unregister("fixture_nav")
            for target, asset, ratio in (("600000.SH", "stock", weight), ("BOND", "bond", 20)):
                connection.execute(
                    "INSERT INTO fund_holding_history(symbol,report_date,holding_symbol,"
                    "holding_name,"
                    "asset_type,weight,source,observed_at) "
                    "VALUES('F','2025-12-31',?,?,?,?,'synthetic',?)",
                    [target, target, asset, ratio, observed],
                )
        return path

    def test_fund_disclosed_weights_reconcile_nav_return_and_keep_unknown_exposure_visible(self):
        self.seed_fund()
        result = self.run_operation()
        self.assertEqual(result["status"], "ready")
        fund = [row for row in result["portfolio"] if row["symbol"].startswith("F:")]
        self.assertEqual(
            sum((Decimal(row["initial_value"]) for row in fund), Decimal(0)), Decimal(200)
        )
        self.assertEqual(
            {row["sector_name"]: row["initial_value"] for row in fund},
            {"科技": "60", "债券": "40", "基金未披露": "100"},
        )
        self.assertTrue(all(row["period_return"] == "0.1" for row in fund))
        self.assertEqual(result["provenance"]["fund_disclosures"][0]["coverage"], "0.5")
        self.assertEqual(len(result["private_files"]), 3)

    def test_fund_future_disclosures_and_invalid_coverage_block(self):
        self.seed_fund(observed="2026-02-01T18:00:00+08:00")
        result = self.run_operation(ledger=True)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("后来披露" in text for text in result["blockers"]))
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE fund_holding_history SET observed_at='2026-01-01T18:00:00+08:00',weight=80"
            )
        result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("覆盖比例" in text for text in result["blockers"]))

    def test_fund_corrupted_nav_blocks(self):
        path = self.seed_fund()
        frame = pd.read_parquet(path)
        frame.loc[1, "unit_nav"] = 999
        frame.to_parquet(path, index=False)
        result = self.run_operation()
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("校验" in text for text in result["blockers"]))


if __name__ == "__main__":
    unittest.main()
