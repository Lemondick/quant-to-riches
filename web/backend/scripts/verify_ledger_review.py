"""Offline reconciliation and historical replay acceptance with temporary accounts."""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from shutil import copy2
from unittest.mock import patch

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.data.snapshots import SnapshotWriter
from quant_assistant.models.entities import Action, AssetType
from quant_assistant.models.repositories.database import Database
from quant_assistant.services.account_registry import AccountInput, AccountRegistryService
from quant_assistant.services.portfolio_commands import (
    CashFlowType,
    CorporateActionInput,
    ExternalCashFlowInput,
    InstrumentInput,
    LedgerCorrectionInput,
    PortfolioCalibrationInput,
    PortfolioCommandService,
    PositionInput,
    TransactionInput,
)

NOW = datetime(2026, 10, 5, 10, tzinfo=SHANGHAI_TZ)


class LedgerReviewAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = Database(self.root / "quant_assistant.duckdb")
        self.addCleanup(self.db.close)
        self.commands = PortfolioCommandService(self.db, lambda: NOW)
        baseline = date(2026, 9, 1)
        self.commands.import_initial_account(
            "base",
            baseline,
            (
                PositionInput(
                    "CASH", AssetType.CASH, Decimal(1000), Decimal(1), Decimal(1000), baseline
                ),
                PositionInput(
                    "000001", AssetType.FUND, Decimal(100), Decimal(1), Decimal(100), baseline
                ),
            ),
            reason="confirmed complete statement",
            instrument_metadata=(
                InstrumentInput("CASH", AssetType.CASH, "Cash", "LOCAL", baseline),
                InstrumentInput("000001", AssetType.FUND, "Fund", "OTC", baseline),
            ),
        )
        self._nav(date(2026, 9, 1), 1, 1100, 1000, True)
        self.commands.record_transaction(
            TransactionInput(
                "000001",
                AssetType.FUND,
                Action.BUY,
                Decimal(20),
                Decimal("1.1"),
                Decimal(1),
                datetime(2026, 9, 8, 10, tzinfo=SHANGHAI_TZ),
                "existing",
            )
        )
        self._nav(date(2026, 9, 10), 1.2, 1121, 977, False)

    def _nav(self, day, price, total, cash, baseline):
        snapshot_record = SnapshotWriter(
            self.root / "snapshots", "statement", clock=lambda: NOW
        ).write(
            "fund_nav",
            day,
            pd.DataFrame({"symbol": ["000001"], "nav_date": [day], "unit_nav": [float(price)]}),
        )
        with self.db.transaction() as conn:
            snapshot = conn.execute(
                "INSERT INTO data_snapshots(dataset,source,as_of_date,fetched_at,path,"
                "sha256,row_count,status) "
                "VALUES('fund_nav','statement',?,?,?,?,1,'valid') RETURNING id",
                [day, NOW, str(snapshot_record.path), snapshot_record.sha256],
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO fund_nav VALUES('000001',?,?,?,?)", [day, price, price, snapshot]
            )
            conn.execute(
                "INSERT INTO portfolio_valuations(run_id,as_of_date,generated_at,"
                "data_snapshot_id,total_value,cash,"
                "unit_nav,high_water_mark,is_baseline) VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    f"nav-{day}",
                    day,
                    NOW,
                    snapshot,
                    total,
                    cash,
                    Decimal(total) / 1100,
                    max(Decimal(1), Decimal(total) / 1100),
                    baseline,
                ],
            )

    def _missing(self, quantity="10"):
        return TransactionInput(
            "000001",
            AssetType.FUND,
            Action.BUY,
            Decimal(quantity),
            Decimal(1),
            Decimal("0.5"),
            datetime(2026, 9, 3, 10, tzinfo=SHANGHAI_TZ),
            "missing",
            source="historical-manual",
        )

    def test_batch_replay_is_readonly_and_rebuilds_both_trades(self):
        from dataclasses import replace

        from quant_assistant.services.ledger_replay_engine import load_source, simulate

        before = self.db.query_df("SELECT * FROM positions ORDER BY symbol").to_json()
        with self.db.connection() as conn:
            source = load_source(conn)
        sell = replace(
            self._missing(),
            action=Action.SELL,
            quantity=Decimal(5),
            traded_at=datetime(2026, 9, 5, 10, tzinfo=SHANGHAI_TZ),
            external_id="missing-sell",
        )
        result = simulate(source, [self._missing(), sell], self.root / "batch", NOW)
        self.assertEqual(
            next(p["quantity"] for p in result["positions"] if p["symbol"] == "CASH"),
            Decimal("971"),
        )
        self.assertEqual(
            next(p["quantity"] for p in result["positions"] if p["symbol"] == "000001"),
            Decimal(125),
        )
        self.assertEqual(result["valuations"][-1]["total_value"], Decimal("1121"))
        self.assertEqual(len(result["transaction_audits"]), 3)
        self.assertEqual(
            self.db.query_df("SELECT * FROM positions ORDER BY symbol").to_json(), before
        )

    def test_batch_replay_rejects_one_bad_trade_and_ambiguous_order(self):
        from dataclasses import replace

        from quant_assistant.services.ledger_replay_engine import load_source, simulate

        with self.db.connection() as conn:
            source = load_source(conn)
        sell = replace(
            self._missing(),
            action=Action.SELL,
            quantity=Decimal(2000),
            traded_at=datetime(2026, 9, 5, 10, tzinfo=SHANGHAI_TZ),
            external_id="bad-sell",
        )
        with self.assertRaises(ValueError):
            simulate(source, [self._missing(), sell], self.root / "bad-batch", NOW)
        with self.assertRaisesRegex(ValueError, "同时|顺序"):
            simulate(
                source,
                [
                    self._missing(),
                    replace(sell, quantity=Decimal(5), traded_at=self._missing().traded_at),
                ],
                self.root / "tied-batch",
                NOW,
            )
        self.assertEqual(
            int(self.db.query_df("SELECT count(*) n FROM transactions").iloc[0]["n"]), 1
        )

    def test_snapshot_digest_matches_persisted_rows_with_non_default_index(self):
        from quant_assistant.data.snapshots import _frame_sha256

        frame = pd.DataFrame({"symbol": ["000001"], "unit_nav": [1.2]}, index=[42])
        snapshot = SnapshotWriter(self.root / "snapshots", "statement", clock=lambda: NOW).write(
            "fund_nav", date(2026, 9, 15), frame
        )
        self.assertEqual(snapshot.sha256, _frame_sha256(pd.read_parquet(snapshot.path)))
        self.assertEqual(frame.index.tolist(), [42])

    def test_equal_timestamp_actions_follow_global_audit_order(self):
        from dataclasses import replace

        from quant_assistant.services.ledger_replay import LedgerReplayService

        instant = datetime(2026, 9, 11, 10, tzinfo=SHANGHAI_TZ)
        self.commands.record_transaction(
            TransactionInput(
                "000001",
                AssetType.FUND,
                Action.SELL,
                Decimal(10),
                Decimal("1.2"),
                Decimal(1),
                instant,
                "sell-before-split",
            )
        )
        self.commands.record_corporate_action(
            CorporateActionInput(
                "split",
                "000001",
                AssetType.FUND,
                instant,
                "split-after-sale",
                quantity=Decimal(50),
                ratio=Decimal(2),
            )
        )
        original = self.db.query_df(
            "SELECT average_cost FROM positions WHERE symbol='000001'"
        ).iloc[0]["average_cost"]
        self._nav(date(2026, 9, 12), 1.3, 1196, 988, False)
        missing = replace(self._missing(), traded_at=datetime(2026, 9, 13, 10, tzinfo=SHANGHAI_TZ))
        preview = LedgerReplayService(self.db, lambda: NOW).preview(missing, reason="statement")
        self.assertEqual(preview["status"], "ready", preview)
        fund = next(row for row in preview["positions"] if row["symbol"] == "000001")
        expected = ((Decimal(str(original)) * 160 + Decimal("10.5")) / 170).quantize(
            Decimal("0.000001")
        )
        self.assertEqual(Decimal(fund["average_cost"]), expected)

    def test_missing_trade_cannot_guess_tied_event_order_or_company_entitlement(self):
        from dataclasses import replace

        from quant_assistant.services.ledger_replay import LedgerReplayService

        service = LedgerReplayService(self.db, lambda: NOW)
        tied = replace(self._missing(), traded_at=datetime(2026, 9, 8, 10, tzinfo=SHANGHAI_TZ))
        result = service.preview(tied, reason="statement")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("顺序", result["blockers"][0])
        self.commands.record_corporate_action(
            CorporateActionInput(
                "split",
                "000001",
                AssetType.FUND,
                datetime(2026, 9, 11, 10, tzinfo=SHANGHAI_TZ),
                "split",
                ratio=Decimal(2),
            )
        )
        result = service.preview(self._missing(), reason="statement")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("权益", result["blockers"][0])

    def test_replay_preview_and_atomic_confirmation(self):
        from quant_assistant.services.ledger_replay import LedgerReplayService

        service = LedgerReplayService(self.db, lambda: NOW)
        before = self.db.query_df("SELECT * FROM positions ORDER BY symbol").to_json()
        preview = service.preview(self._missing(), reason="missing confirmation found")
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(
            self.db.query_df("SELECT * FROM positions ORDER BY symbol").to_json(), before
        )
        cash = next(item for item in preview["positions"] if item["symbol"] == "CASH")
        fund = next(item for item in preview["positions"] if item["symbol"] == "000001")
        self.assertEqual(Decimal(cash["quantity"]), Decimal("966.5"))
        self.assertEqual(Decimal(fund["quantity"]), Decimal(130))
        self.assertEqual(Decimal(preview["valuations"][-1]["total_value"]), Decimal("1122.5"))
        result = service.confirm(preview["preview_id"])
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(
            service.confirm(preview["preview_id"])["transaction_id"], result["transaction_id"]
        )
        self.assertEqual(
            int(self.db.query_df("SELECT count(*) n FROM transactions").iloc[0]["n"]), 2
        )
        self.assertEqual(
            Decimal(
                str(
                    self.db.query_df(
                        "SELECT cash FROM portfolio_valuations ORDER BY as_of_date DESC"
                    ).iloc[0]["cash"]
                )
            ),
            Decimal("966.5"),
        )
        self.assertEqual(
            int(
                self.db.query_df(
                    "SELECT count(*) n FROM audit_log WHERE entity_type='ledger_replay'"
                ).iloc[0]["n"]
            ),
            1,
        )

    def test_stale_and_insufficient_replay_do_not_write_balances(self):
        from quant_assistant.services.ledger_replay import LedgerReplayService

        service = LedgerReplayService(self.db, lambda: NOW)
        preview = service.preview(self._missing(), reason="missing statement")
        self.assertEqual(preview["status"], "ready")
        with self.db.transaction() as conn:
            conn.execute("UPDATE positions SET market_value=market_value+1 WHERE symbol='000001'")
        before = self.db.query_df("SELECT * FROM positions ORDER BY symbol").to_json()
        with self.assertRaises(ValueError):
            service.confirm(preview["preview_id"])
        self.assertEqual(
            service.preview(self._missing("2000"), reason="missing statement")["status"], "blocked"
        )
        self.assertEqual(
            self.db.query_df("SELECT * FROM positions ORDER BY symbol").to_json(), before
        )

    def test_missing_price_evidence_blocks_replay(self):
        from quant_assistant.services.ledger_replay import LedgerReplayService

        with self.db.transaction() as conn:
            conn.execute("UPDATE data_snapshots SET status='stale' WHERE as_of_date='2026-09-10'")
        preview = LedgerReplayService(self.db, lambda: NOW).preview(
            self._missing(), reason="missing statement"
        )
        self.assertEqual(preview["status"], "blocked")
        self.assertTrue(preview["blockers"])

    def test_sell_reversal_uses_revised_cost_after_historical_replay(self):
        from quant_assistant.services.ledger_replay import LedgerReplayService

        sold = self.commands.record_transaction(
            TransactionInput(
                "000001",
                AssetType.FUND,
                Action.SELL,
                Decimal(10),
                Decimal("1.2"),
                Decimal(1),
                datetime(2026, 9, 11, 10, tzinfo=SHANGHAI_TZ),
                "existing-sale",
            )
        )
        self._nav(date(2026, 9, 12), 1.3, 1131, 988, False)
        service = LedgerReplayService(self.db, lambda: NOW)
        preview = service.preview(self._missing(), reason="missing confirmation found")
        self.assertEqual(preview["status"], "ready")
        service.confirm(preview["preview_id"])
        expected = self.db.query_df(
            "SELECT average_cost FROM positions WHERE symbol='000001'"
        ).iloc[0]["average_cost"]
        self.commands.correct_ledger_entry(
            LedgerCorrectionInput(
                "transaction",
                sold,
                "incorrect sale",
                datetime(2026, 9, 13, 10, tzinfo=SHANGHAI_TZ),
                "reverse-sale",
            )
        )
        actual = self.db.query_df("SELECT average_cost FROM positions WHERE symbol='000001'").iloc[
            0
        ]["average_cost"]
        self.assertEqual(actual, expected)

    def test_replay_preserves_original_event_fingerprints(self):
        from quant_assistant.portfolio.nav_continuity import _portfolio_events_fingerprint
        from quant_assistant.services.ledger_replay import LedgerReplayService

        with self.db.transaction() as conn:
            conn.execute("UPDATE portfolio_events SET recorded_at=?", [NOW - timedelta(days=10)])
        self.commands.record_external_cash_flow(
            ExternalCashFlowInput(
                "deposit", CashFlowType.DEPOSIT, Decimal(50), date(2026, 9, 11), "statement"
            )
        )
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE portfolio_events SET recorded_at=? WHERE event_id='deposit'",
                [NOW - timedelta(days=5)],
            )
        self._nav(date(2026, 9, 12), 1.3, 1183, 1027, False)
        preview = LedgerReplayService(self.db, lambda: NOW).preview(
            self._missing(), reason="statement"
        )
        self.assertEqual(preview["status"], "ready")
        for index, event_id in ((0, "base"), (-1, "deposit")):
            actual = self.db.query_df("SELECT * FROM portfolio_events WHERE event_id=?", [event_id])
            self.assertEqual(
                preview["valuations"][index]["events_fingerprint"],
                _portfolio_events_fingerprint(actual),
            )

    def test_same_day_calibration_order_without_evidence_is_blocked(self):
        from quant_assistant.services.ledger_replay import LedgerReplayService

        day = date(2026, 9, 11)
        self.commands.record_portfolio_calibration(
            PortfolioCalibrationInput(
                "calibrate",
                day,
                (
                    PositionInput(
                        "CASH", AssetType.CASH, Decimal(977), Decimal(1), Decimal(977), day
                    ),
                    PositionInput(
                        "000001", AssetType.FUND, Decimal(120), Decimal(1), Decimal(144), day
                    ),
                ),
                Decimal(0),
                "confirmed statement",
            )
        )
        missing = self._missing()
        from dataclasses import replace

        missing = replace(missing, traded_at=datetime(2026, 9, 11, 10, tzinfo=SHANGHAI_TZ))
        result = LedgerReplayService(self.db, lambda: NOW).preview(missing, reason="statement")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("校准", result["blockers"][0])

    def test_mid_commit_failure_rolls_back_and_expired_preview_is_rejected(self):
        from quant_assistant.services.ledger_replay import LedgerReplayService

        service = LedgerReplayService(self.db, lambda: NOW)
        preview = service.preview(self._missing(), reason="statement")
        self.assertEqual(preview["status"], "ready")
        before = self.db.query_df("SELECT * FROM positions ORDER BY symbol").to_json()
        with (
            patch(
                "quant_assistant.services.ledger_replay.audit",
                side_effect=RuntimeError("write failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            service.confirm(preview["preview_id"])
        self.assertEqual(
            self.db.query_df("SELECT * FROM positions ORDER BY symbol").to_json(), before
        )
        self.assertEqual(len(self.db.query_df("SELECT * FROM transactions")), 1)
        self.assertTrue(
            self.db.query_df("SELECT * FROM ledger_replay_previews").iloc[0]["consumed_at"]
            is pd.NaT
        )
        expired = LedgerReplayService(self.db, lambda: NOW + timedelta(minutes=16))
        with self.assertRaisesRegex(ValueError, "过期"):
            expired.confirm(preview["preview_id"])

    def test_reconciliation_scope_differences_and_resolution(self):
        from quant_assistant.services.reconciliation import ReconciliationService

        registry = AccountRegistryService(self.db, lambda: NOW)
        registry.register(AccountInput("platform", "Platform", "statement"))
        day = date(2026, 9, 8)
        registry.import_snapshot(
            "platform",
            day,
            (
                PositionInput("CASH", AssetType.CASH, Decimal(977), Decimal(1), Decimal(977), day),
                PositionInput(
                    "000001", AssetType.FUND, Decimal(125), Decimal(1), Decimal("137.5"), day
                ),
            ),
            event_id="platform-snapshot",
            reason="confirmed statement",
        )
        service = ReconciliationService(self.db, lambda: NOW)
        result = service.compare(
            ["platform"],
            complete_scope=True,
            expected_fees=Decimal(1),
            start=day,
            end=day,
            evidence="confirmed fee statement",
        )
        fund = next(item for item in result["items"] if item["symbol"] == "000001")
        self.assertEqual(Decimal(fund["quantity_delta"]), Decimal(5))
        self.assertEqual(Decimal(result["fees"]["difference"]), Decimal(0))
        self.assertEqual(result["status"], "differences")
        cash_before = self.db.query_df(
            "SELECT market_value FROM positions WHERE symbol='CASH'"
        ).iloc[0]["market_value"]
        service.resolve(
            result["reconciliation_id"], "follow_up", "missing trade requires verification"
        )
        self.assertEqual(service.history()["items"][0]["resolution"], "follow_up")
        self.assertEqual(
            self.db.query_df("SELECT market_value FROM positions WHERE symbol='CASH'").iloc[0][
                "market_value"
            ],
            cash_before,
        )
        partial = service.compare(["platform"], complete_scope=False)
        self.assertIn("scope_unconfirmed", partial["blockers"])

    def test_reconciliation_rejects_unknown_or_foreign_currency(self):
        from quant_assistant.services.reconciliation import ReconciliationService

        service = ReconciliationService(self.db, lambda: NOW)
        with self.assertRaises(ValueError):
            service.compare(["unknown"], complete_scope=True)
        registry = AccountRegistryService(self.db, lambda: NOW)
        registry.register(AccountInput("foreign", "Foreign", "statement", "USD"))
        with self.assertRaises(ValueError):
            service.compare(["foreign"], complete_scope=True)

    def test_reconciliation_missing_cost_and_fee_evidence_cannot_be_matched(self):
        from quant_assistant.services.reconciliation import ReconciliationService

        day = date(2026, 9, 8)
        registry = AccountRegistryService(self.db, lambda: NOW)
        registry.register(AccountInput("platform", "Platform", "statement"))
        registry.import_snapshot(
            "platform",
            day,
            (
                PositionInput("CASH", AssetType.CASH, Decimal(977), Decimal(1), Decimal(977), day),
                PositionInput(
                    "000001", AssetType.FUND, Decimal(120), Decimal(0), Decimal(132), day
                ),
            ),
            event_id="statement",
            reason="confirmed statement",
        )
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE positions SET as_of_date=?,market_value=132 WHERE symbol='000001'", [day]
            )
        result = ReconciliationService(self.db, lambda: NOW).compare(
            ["platform"], complete_scope=True
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("cost_evidence_missing", result["blockers"])
        self.assertIn("fee_evidence_missing", result["blockers"])

    def test_authenticated_api_isolates_previews_and_requires_csrf_and_confirmation(self):
        from fastapi.testclient import TestClient

        from quant_assistant.config import Settings
        from quant_assistant.controllers.auth_app import create_authenticated_app
        from quant_assistant.services.auth import COOKIE_NAME

        app = create_authenticated_app(Settings.for_data_dir(self.root / "site"))
        self.addCleanup(app.state.user_contexts.close)
        app.state.auth.bootstrap("owner", "test account password 123456")
        alice = app.state.auth.register("alice", "test account password 123456")
        app.state.auth.register("bob", "test account password 123456")
        directory = app.state.user_contexts.directory(alice)
        directory.mkdir(parents=True, exist_ok=True)
        self.db.close()
        copy2(self.db.path, directory / "quant_assistant.duckdb")
        client = TestClient(app)
        self.addCleanup(client.close)
        origin = {"Origin": "http://testserver"}

        def login(username):
            response = client.post(
                "/api/v1/auth/login",
                headers=origin,
                json={"username": username, "password": "test account password 123456"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            return {
                **origin,
                "X-CSRF-Token": response.json()["csrf_token"],
                "Cookie": COOKIE_NAME + "=" + client.cookies.get(COOKIE_NAME),
            }

        self.assertEqual(client.get("/api/v1/ledger/replay/history").status_code, 401)
        alice_headers = login("alice")
        body = {
            "symbol": "000001",
            "asset_type": "fund",
            "action": "buy",
            "quantity": "10",
            "price": "1",
            "fees": "0.5",
            "occurred_at": "2026-09-03T10:00:00+08:00",
            "external_id": "api-missing",
            "reason": "confirmed statement",
        }
        self.assertEqual(
            client.post("/api/v1/ledger/replay/preview", headers=origin, json=body).status_code, 403
        )
        response = client.post("/api/v1/ledger/replay/preview", headers=alice_headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ready", response.text)
        identity = response.json()["preview_id"]
        bob_headers = login("bob")
        response = client.post(
            "/api/v1/ledger/replay/confirm",
            headers=bob_headers,
            json={"preview_id": identity, "confirmed": True},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            client.get("/api/v1/ledger/replay/history", headers=bob_headers).json()["total"], 0
        )
        self.assertEqual(
            client.post(
                "/api/v1/ledger/replay/confirm",
                headers=alice_headers,
                json={"preview_id": identity},
            ).status_code,
            422,
        )
        response = client.post(
            "/api/v1/ledger/replay/confirm",
            headers=alice_headers,
            json={"preview_id": identity, "confirmed": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            client.get("/api/v1/ledger/replay/history", headers=alice_headers).json()["total"], 1
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
