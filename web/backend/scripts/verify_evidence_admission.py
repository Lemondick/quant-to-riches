"""Acceptance checks for forward-session and full-site backup admission in temporary stores."""

import json
import sqlite3
import unittest
import zipfile
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pandas as pd

from quant_assistant.backup import create_backup, restore_backup
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.data.snapshots import SnapshotWriter
from quant_assistant.models.entities import DataStatus, MarketRegime, ReadinessStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.portfolio.models import PortfolioState, PortfolioValuationDraft, RiskPolicy
from quant_assistant.services.daily_decision import (
    DailyDecisionService,
    DefaultStrategyRunner,
    ReadinessGate,
)
from quant_assistant.services.decision_models import DecisionInputs
from quant_assistant.services.decision_repository import DuckDBDecisionRepository
from quant_assistant.services.forward_sessions import (
    evaluate_observation,
    freeze_forward_strategy,
    strategy_fingerprint,
)
from quant_assistant.services.production_readiness import _operations_check, _paper_check
from quant_assistant.site_backup import create_site_backup
from scripts.paper_readiness_report import build_report
from scripts.record_forward_observation import observe, record


class EvidenceAdmissionChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="quant-admission-")
        self.root = Path(self.temporary.name) / "data"
        self.database = Database(self.root / "quant_assistant.duckdb")
        self.database.initialize()

    def tearDown(self):
        self.database.close()
        self.temporary.cleanup()

    def run_row(self, run_id, day, *, status="valid", generated=None, version="stock-base-v1"):
        generated = generated or datetime.combine(day, datetime.min.time(), SHANGHAI_TZ).replace(
            hour=17
        )
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO strategy_runs(run_id,as_of_date,generated_at,strategy_version,"
                "data_status,readiness,status) VALUES(?,?,?,?,?,'paper','completed')",
                [run_id, day, generated, version, status],
            )

    def test_backdated_stale_runs_do_not_pass_reports(self):
        for day in range(1, 21):
            self.run_row(
                f"replay-{day}",
                date(2026, 9, day),
                status="stale",
                generated=datetime(2026, 10, 5, 17, tzinfo=SHANGHAI_TZ),
            )
        self.assertFalse(_paper_check(self.database, 20)["passed"])
        self.assertFalse(build_report(self.database, 20)["ready_for_reference"])

    def test_unobserved_weekend_and_unreconciled_runs_do_not_earn_credit(self):
        self.run_row("weekend", date(2026, 9, 5))
        self.run_row("unreconciled", date(2026, 9, 7))
        repository = DuckDBDecisionRepository(self.database)
        self.assertEqual(
            repository.valid_paper_session_dates("stock-base-v1", date(2026, 10, 5)), ()
        )

    def test_legacy_backup_is_readable_but_not_site_admission(self):
        self.database.close()
        directory = Path(self.temporary.name) / "backups"
        archive = directory / "legacy.zip"
        create_backup(Settings.for_data_dir(self.root), archive)
        restored = Path(self.temporary.name) / "legacy-restored"
        restore_backup(archive, restored)
        self.assertTrue((restored / "quant_assistant.duckdb").is_file())
        check = _operations_check(directory, 1)
        self.assertFalse(check["passed"])
        self.assertFalse(check["restore_verified"])

    def forward_fixture(self, *, order=True):
        day = date(2026, 9, 7)
        now = datetime(2026, 9, 7, 18, tzinfo=SHANGHAI_TZ)
        freeze_forward_strategy(
            self.root, ("stock-base-v1",), datetime(2026, 9, 1, 12, tzinfo=SHANGHAI_TZ)
        )
        self.run_row("forward", day)
        writer = SnapshotWriter(
            self.root / "raw",
            "fixture",
            clock=lambda: datetime(2026, 9, 7, 16, 30, tzinfo=SHANGHAI_TZ),
        )
        with self.database.connection() as conn:
            for dataset in ("daily_prices", "instruments", "index_history"):
                snapshot = writer.write(dataset, day, pd.DataFrame({"fixture_value": [1]}))
                conn.execute(
                    "INSERT INTO data_snapshots(dataset,source,as_of_date,fetched_at,"
                    "path,sha256,row_count,status) "
                    "VALUES(?,'fixture',?,?,?,?,?,'valid')",
                    [
                        dataset,
                        day,
                        snapshot.fetched_at,
                        str(snapshot.path),
                        snapshot.sha256,
                        snapshot.row_count,
                    ],
                )
            conn.execute(
                "UPDATE strategy_runs SET data_snapshot_id=(SELECT max(id) FROM data_snapshots),"
                "parameters_json=?",
                [json.dumps({"strategy_fingerprint": strategy_fingerprint(("stock-base-v1",))})],
            )
            if order:
                conn.execute(
                    "INSERT INTO proposed_orders(id,run_id,symbol,asset_type,action,amount,"
                    "quantity,target_weight,reasons_json,as_of_date,valid_until,"
                    "data_snapshot_id,strategy_version) VALUES(1,'forward','000001','stock','buy',"
                    "100,1,0.1,'[]','2026-09-07','2026-09-08T15:00:00+08:00',3,'stock-base-v1')"
                )
                conn.execute(
                    "INSERT INTO paper_orders VALUES('order',1,'forward','000001',"
                    "'stock','buy',100,1,'filled',"
                    "'2026-09-08T15:00:00+08:00','fixture-order',NULL,"
                    "'2026-09-07T17:00:00+08:00','2026-09-07T17:30:00+08:00')"
                )
                conn.execute(
                    "INSERT INTO paper_reconciliations VALUES('matched','order','matched','{}',"
                    "'2026-09-07T17:40:00+08:00')"
                )
        return day, now

    def save_observation(self, report):
        directory = self.root / "forward-observations" / report["as_of_date"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "fixture.json").write_text(json.dumps(report, default=str), "utf-8")

    def test_observation_script_records_valid_live_workspace(self):
        _, now = self.forward_fixture()
        self.database.close()
        try:
            report = observe(self.root, now)
        except (OSError, ValueError):
            report = {"eligible_paper_session": False}
        self.assertTrue(
            report["eligible_paper_session"],
            "live account observation must not require research manifest",
        )

    def test_valid_session_passes_all_consumers_and_tampered_source_revokes_credit(self):
        day, now = self.forward_fixture()
        with self.database.connection() as conn:
            report = evaluate_observation(conn, self.root, now)
        self.assertTrue(report["eligible_paper_session"], report["blockers"])
        self.save_observation(report)
        repository = DuckDBDecisionRepository(self.database)
        self.assertEqual(
            repository.valid_paper_session_dates("stock-base-v1", date(2026, 10, 5)), (day,)
        )
        self.assertTrue(_paper_check(self.database, 1)["passed"])
        self.assertTrue(build_report(self.database, 1)["ready_for_reference"])
        path = Path(
            self.database.query_df(
                "SELECT path FROM data_snapshots WHERE dataset='daily_prices'"
            ).iloc[0]["path"]
        )
        path.write_bytes(b"changed after observation")
        self.assertFalse(_paper_check(self.database, 1)["passed"])

    def test_latest_reconciliation_is_required_and_no_order_is_explicit(self):
        _, now = self.forward_fixture()
        with self.database.connection() as conn:
            conn.execute("DELETE FROM paper_reconciliations")
            missing = evaluate_observation(conn, self.root, now)
            self.assertIn("paper_reconciliation_missing", missing["blockers"])
            conn.execute("DELETE FROM paper_orders")
            conn.execute("DELETE FROM proposed_orders")
            no_order = evaluate_observation(conn, self.root, now)
        self.assertTrue(no_order["eligible_paper_session"])
        self.assertEqual(no_order["order_count"], 0)
        self.assertFalse(_paper_check(self.database, 1)["passed"])
        self.save_observation(no_order)
        self.assertTrue(_paper_check(self.database, 1)["passed"])

    def test_wrong_strategy_and_retroactive_freeze_are_rejected(self):
        _, now = self.forward_fixture()
        with self.database.connection() as conn:
            conn.execute("UPDATE strategy_runs SET strategy_version='wrong-version'")
            wrong = evaluate_observation(conn, self.root, now)
            self.assertIn("frozen_strategy_mismatch", wrong["blockers"])
            conn.execute("UPDATE strategy_runs SET strategy_version='stock-base-v1'")
            freeze_path = self.root / "forward-freeze.json"
            freeze = json.loads(freeze_path.read_text("utf-8"))
            freeze["frozen_at"] = "2026-09-07T17:30:00+08:00"
            freeze_path.write_text(json.dumps(freeze), "utf-8")
            retroactive = evaluate_observation(conn, self.root, now)
        self.assertIn("strategy_frozen_after_run", retroactive["blockers"])

    def test_snapshot_issues_and_unknown_strategy_fingerprint_fail_closed(self):
        _, now = self.forward_fixture(order=False)
        with self.database.connection() as conn:
            conn.execute("UPDATE data_snapshots SET issues_json='[\"unverified_source\"]'")
            issues = evaluate_observation(conn, self.root, now)
            self.assertFalse(issues["eligible_paper_session"])
            conn.execute("UPDATE data_snapshots SET issues_json='[]'")
            freeze_path = self.root / "forward-freeze.json"
            freeze = json.loads(freeze_path.read_text("utf-8"))
            freeze["strategy_fingerprint"] = "unknown-implementation"
            freeze_path.write_text(json.dumps(freeze), "utf-8")
            conn.execute(
                "UPDATE strategy_runs SET parameters_json=?",
                [json.dumps({"strategy_fingerprint": "unknown-implementation"})],
            )
            unknown = evaluate_observation(conn, self.root, now)
        self.assertFalse(unknown["eligible_paper_session"])

    def test_calendar_provenance_and_prior_day_gate(self):
        day, now = self.forward_fixture(order=False)
        with self.database.connection() as conn:
            report = evaluate_observation(conn, self.root, now)
            conn.execute(
                "INSERT INTO backtest_runs VALUES('qualified','2026-08-01T00:00:00+08:00',"
                "'2026-08-31T00:00:00+08:00','2026-01-01','2026-08-31','stock-base-v1',"
                "'{}','{}','paper','[]','completed')"
            )
        self.save_observation(report)
        repository = DuckDBDecisionRepository(self.database)
        gate = ReadinessGate(repository, minimum_paper_sessions=1)
        self.assertEqual(
            gate.evaluate(("stock-base-v1",), DataStatus.VALID, day), ReadinessStatus.PAPER
        )
        self.assertEqual(
            gate.evaluate(("stock-base-v1",), DataStatus.VALID, date(2026, 9, 8)),
            ReadinessStatus.REFERENCE,
        )
        self.assertEqual(
            DefaultStrategyRunner().evidence_fingerprint(("stock-base-v1",)),
            strategy_fingerprint(("stock-base-v1",)),
        )
        report["mode"] = "historical_replay"
        self.save_observation(report)
        self.assertFalse(_paper_check(self.database, 1)["passed"])
        self.assertFalse(
            evaluate_observation(None, self.root, datetime(2026, 9, 5, 18, tzinfo=SHANGHAI_TZ))[
                "eligible_paper_session"
            ]
        )
        self.assertEqual(
            evaluate_observation(None, self.root, datetime(2027, 1, 4, 18, tzinfo=SHANGHAI_TZ))[
                "blockers"
            ],
            ["calendar_unavailable"],
        )

    def test_replayed_account_history_revokes_affected_observation(self):
        _, now = self.forward_fixture(order=False)
        with self.database.connection() as conn:
            report = evaluate_observation(conn, self.root, now)
        self.save_observation(report)
        self.assertTrue(_paper_check(self.database, 1)["passed"])
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO audit_log(entity_type,entity_id,changed_at,before_json,after_json) "
                "VALUES('ledger_replay','fixture','2026-09-08T18:00:00+08:00','{}',?)",
                [json.dumps({"effective_date": "2026-09-01"})],
            )
        self.assertFalse(_paper_check(self.database, 1)["passed"])

    def test_unexecuted_proposal_cannot_claim_zero_order_session(self):
        _, now = self.forward_fixture()
        with self.database.connection() as conn:
            conn.execute("DELETE FROM paper_orders")
            report = evaluate_observation(conn, self.root, now)
        self.assertFalse(report["eligible_paper_session"])

    def test_archived_research_workspace_cannot_be_written_or_credited(self):
        _, now = self.forward_fixture(order=False)
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"research_only": True, "status": "completed"}), "utf-8")
        original = manifest.read_bytes()
        self.database.close()
        self.assertFalse(observe(self.root, now)["eligible_paper_session"])
        with self.assertRaises(ValueError):
            record(self.root, now)
        self.assertFalse((self.root / "forward-observations").exists())
        self.assertEqual(manifest.read_bytes(), original)

    def test_changed_risk_policy_cannot_share_frozen_strategy_fingerprint(self):
        runner = DefaultStrategyRunner()
        frozen = runner.evidence_fingerprint(("stock-base-v1",))
        try:
            changed = runner.evidence_fingerprint(
                ("stock-base-v1",), policy=RiskPolicy(max_stock_bucket="0.40")
            )
        except TypeError:
            self.fail("forward provenance must include the risk policy actually used for orders")
        self.assertNotEqual(frozen, changed)

    def test_snapshot_metadata_hash_and_row_count_must_match_production_parquet(self):
        _, now = self.forward_fixture(order=False)
        with self.database.connection() as conn:
            conn.execute("UPDATE data_snapshots SET sha256=repeat('0',64)")
            mismatched = evaluate_observation(conn, self.root, now)
        self.assertFalse(mismatched["eligible_paper_session"])

    def test_snapshot_row_count_must_match_production_parquet(self):
        _, now = self.forward_fixture(order=False)
        with self.database.connection() as conn:
            conn.execute("UPDATE data_snapshots SET row_count=row_count+1")
            mismatched = evaluate_observation(conn, self.root, now)
        self.assertFalse(mismatched["eligible_paper_session"])

    def test_unknown_execution_cannot_inherit_reference_readiness(self):
        day = date(2026, 9, 7)
        empty = pd.DataFrame()
        inputs = DecisionInputs(
            day,
            1,
            DataStatus.VALID,
            (),
            empty,
            empty,
            empty,
            empty,
            empty,
            {},
            (),
            PortfolioState(Decimal(100), Decimal(100), Decimal(1), Decimal(1), (), day),
            PortfolioValuationDraft(Decimal(0), None, "fixture", "fixture", True, True),
            {},
            MarketRegime.GREEN,
            ("stock-base-v1",),
        )
        saved = []
        repository = SimpleNamespace(
            load_decision_inputs=lambda _: inputs,
            save_decision=lambda result, *_: saved.append(result),
        )
        service = DailyDecisionService(
            repository,
            clock=lambda: datetime(2026, 9, 7, 18, tzinfo=SHANGHAI_TZ),
            strategy_runner=SimpleNamespace(run=lambda _: []),
            order_proposer=lambda *_: [],
            readiness_gate=SimpleNamespace(evaluate=lambda *_: ReadinessStatus.REFERENCE),
        )
        result = service.run(day)
        self.assertEqual(result.readiness, ReadinessStatus.RESEARCH_ONLY)
        self.assertEqual(saved, [result])

    def test_full_site_archive_passes_and_missing_user_storage_fails(self):
        auth_path = self.root / "auth/accounts.sqlite3"
        auth_path.parent.mkdir()
        with closing(sqlite3.connect(auth_path)) as conn, conn:
            conn.execute("CREATE TABLE users(id TEXT,role TEXT)")
            conn.executemany(
                "INSERT INTO users VALUES(?,?)", [("a" * 32, "admin"), ("b" * 32, "user")]
            )
            conn.execute("CREATE TABLE sessions(token_hash TEXT)")
            conn.execute("INSERT INTO sessions VALUES('source-session-preserved')")
        user_database = Database(self.root / "users" / ("b" * 32) / "quant_assistant.duckdb")
        user_database.initialize()
        user_database.close()
        self.database.close()
        directory = Path(self.temporary.name) / "site-backups"
        archive = directory / "site.zip"
        create_site_backup(self.root, archive)
        check = _operations_check(directory, 1, site_directory=self.root)
        self.assertTrue(check["passed"], check["blockers"])
        with closing(sqlite3.connect(auth_path)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        incomplete = directory / "incomplete.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(incomplete, "w") as target:
            manifest = json.loads(source.read("site-manifest.json"))
            omitted = "users/" + "b" * 32 + "/quant_assistant.duckdb"
            manifest["files"].pop(omitted)
            for name in source.namelist():
                if name != omitted:
                    target.writestr(
                        name,
                        json.dumps(manifest) if name == "site-manifest.json" else source.read(name),
                    )
        self.assertFalse(_operations_check(directory, 1, site_directory=self.root)["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
