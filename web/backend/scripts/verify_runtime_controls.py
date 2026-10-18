"""Offline runtime, per-account costs and alerts acceptance with temporary stores."""

import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from fastapi.testclient import TestClient

from quant_assistant.analysis.advisor_config import AdvisorSettings
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.controllers.auth_app import create_authenticated_app
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.advisor_models import AdvisorModels
from quant_assistant.services.advisor_scheduler import AdvisorScheduler
from quant_assistant.services.model_costs import ModelCostService


class RuntimeControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"

    def login(self, client, username):
        body = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver"},
            json={"username": username, "password": "acceptance password 123456"},
        ).json()
        return {"Origin": "http://testserver", "X-CSRF-Token": body["csrf_token"]}

    def test_cost_budget_unknown_usage_and_account_isolation(self):
        app = create_authenticated_app(Settings.for_data_dir(self.root))
        app.state.auth.bootstrap("owner", "acceptance password 123456")
        alice = app.state.auth.register("alice", "acceptance password 123456")
        app.state.auth.register("bobby", "acceptance password 123456")
        with TestClient(app) as client:
            headers = self.login(client, "alice")
            saved = client.put(
                "/api/v1/runtime/costs",
                headers=headers,
                json={
                    "revision": 0,
                    "daily_budget": "0.01",
                    "currency": "CNY",
                    "prices": {"unit-model": {"input_per_million": "1", "output_per_million": "2"}},
                },
            )
            self.assertEqual(saved.status_code, 200)
            costs = app.state.user_contexts.get(alice).services.api_queries.market_advisor.costs
            stale = client.put(
                "/api/v1/runtime/costs",
                headers=headers,
                json={
                    "revision": 0,
                    "daily_budget": "1",
                    "prices": {},
                },
            )
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["error_code"], "cost_settings_conflict")
            self.assertIn("已变化", stale.json()["message"])
            reservation = costs.reserve("unit-model", max_input_tokens=1000, max_output_tokens=3000)
            costs.finish(reservation, {"prompt_tokens": 1000, "completion_tokens": 2000})
            result = client.get("/api/v1/runtime/costs").json()
            self.assertEqual(result["today"]["total_cost"], "0.005")
            reservation = costs.reserve("unit-model", max_input_tokens=100, max_output_tokens=100)
            costs.finish(reservation, None)
            with self.assertRaises(ValueError):
                costs.reserve("unit-model", max_input_tokens=1, max_output_tokens=1)
            result = client.get("/api/v1/runtime/costs").json()
            self.assertIsNone(result["today"]["total_cost"])
            self.assertEqual(result["today"]["unknown_calls"], 1)
            self.login(client, "bobby")
            result = client.get("/api/v1/runtime/costs").json()
            self.assertEqual(result["settings"]["prices"], {})
            self.assertEqual(result["today"]["calls"], 0)

    def test_provider_records_usage_and_blocks_next_call_after_unknown_spend(self):
        db = Database(self.root / "quant_assistant.duckdb")
        self.addCleanup(db.close)
        db.initialize()
        key = self.root / "synthetic.key"
        key.write_text("synthetic-not-a-real-key", encoding="utf-8")
        costs = ModelCostService(db)
        costs.save(
            {
                "revision": 0,
                "prices": {"unit-model": {"input_per_million": "1", "output_per_million": "2"}},
            }
        )
        responses = [{"usage": {"input_tokens": 1000, "output_tokens": 2000}}, {}]

        def local_response(*args, **kwargs):
            return type(
                "Response",
                (),
                {"status_code": 200, "content": json.dumps(responses.pop(0)).encode()},
            )()

        provider = AdvisorModels(AdvisorSettings(key_file=key), post=local_response)
        provider.costs = costs
        provider._request(
            "https://example.com/fixture", key, {"model": "unit-model", "max_tokens": 3000}
        )
        self.assertEqual(costs.read()["today"]["total_cost"], "0.005")
        provider._request(
            "https://example.com/fixture", key, {"model": "unit-model", "max_tokens": 3000}
        )
        costs.save(
            {
                "revision": 1,
                "daily_budget": "1",
                "prices": {"unit-model": {"input_per_million": "1", "output_per_million": "2"}},
            }
        )
        with self.assertRaises(ValueError):
            provider._request(
                "https://example.com/fixture", key, {"model": "unit-model", "max_tokens": 3000}
            )

    def test_provider_budget_rejects_unverified_input_token_ceiling(self):
        db = Database(self.root / "quant_assistant.duckdb")
        self.addCleanup(db.close)
        db.initialize()
        key = self.root / "synthetic.key"
        key.write_text("synthetic-not-a-real-key", encoding="utf-8")
        costs = ModelCostService(db)
        costs.save(
            {
                "revision": 0,
                "daily_budget": "1",
                "prices": {"unit-model": {"input_per_million": "1", "output_per_million": "2"}},
            }
        )
        response = type(
            "Response",
            (),
            {"status_code": 200, "content": b'{"usage":{"input_tokens":1,"output_tokens":1}}'},
        )()
        provider = AdvisorModels(AdvisorSettings(key_file=key), post=lambda *a, **k: response)
        provider.costs = costs
        with self.assertRaises(ValueError):
            provider._request(
                "https://example.com/fixture", key, {"model": "unit-model", "max_tokens": 3000}
            )
        self.assertEqual(costs.read()["today"]["calls"], 0)

    def test_multirole_usage_is_summed_only_when_every_role_is_known(self):
        db = Database(self.root / "quant_assistant.duckdb")
        self.addCleanup(db.close)
        db.initialize()
        costs = ModelCostService(db)
        costs.save(
            {
                "revision": 0,
                "prices": {"unit-model": {"input_per_million": "1", "output_per_million": "2"}},
            }
        )
        call = costs.reserve("unit-model")
        costs.finish(
            call,
            [{"prompt_tokens": 2, "completion_tokens": 3}, {"input_tokens": 1, "output_tokens": 1}],
        )
        self.assertEqual(costs.read()["today"]["total_cost"], "0.000011")
        call = costs.reserve("unit-model")
        costs.finish(call, [{"prompt_tokens": 2, "completion_tokens": 3}, None])
        self.assertIsNone(costs.read()["today"]["total_cost"])

    def test_restart_marks_unfinished_paid_reservation_as_unknown(self):
        app = create_authenticated_app(Settings.for_data_dir(self.root))
        owner = app.state.auth.bootstrap("owner", "acceptance password 123456")
        with TestClient(app):
            costs = app.state.user_contexts.get(owner).services.api_queries.market_advisor.costs
            costs.save(
                {
                    "revision": 0,
                    "daily_budget": "1",
                    "prices": {"unit-model": {"input_per_million": "1", "output_per_million": "2"}},
                }
            )
            costs.reserve("unit-model", max_input_tokens=100, max_output_tokens=100)
        restarted = create_authenticated_app(Settings.for_data_dir(self.root))
        with TestClient(restarted) as client:
            self.login(client, "owner")
            today = client.get("/api/v1/runtime/costs").json()["today"]
            self.assertEqual(today["pending_calls"], 0)
            self.assertEqual(today["unknown_calls"], 1)
            self.assertEqual(today["budget_blocker"], "unknown_usage_or_price")

    def test_photo_snapshot_does_not_borrow_main_ledger_drawdown(self):
        from quant_assistant.services.alert_center import AlertCenter

        db = Database(self.root / "quant_assistant.duckdb")
        self.addCleanup(db.close)
        db.initialize()
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO portfolio_accounts(account_id,name,platform) "
                "VALUES('photo','fixture','photo_import')"
            )
            conn.execute(
                "INSERT INTO account_positions "
                "VALUES('photo','000001','fund',10,1,10,current_date,now())"
            )
            conn.execute(
                "INSERT INTO "
                "portfolio_valuations(run_id,as_of_date,generated_at,data_snapshot_id,"
                "total_value,cash,unit_nav,high_water_mark) "
                "VALUES('fixture',current_date,now(),1,850,0,0.85,1)"
            )
        kinds = {item["kind"] for item in AlertCenter(db).read()["items"]}
        self.assertNotIn("drawdown", kinds)
        self.assertIn("drawdown_unknown", kinds)

    def test_model_gate_drain_waits_for_manual_job(self):
        from quant_assistant.services.runtime_limits import ModelJobGate

        gate = ModelJobGate(1)
        entered, release = Event(), Event()

        def work():
            with gate.acquire(lambda: True) as accepted:
                self.assertTrue(accepted)
                entered.set()
                release.wait(3)

        worker = Thread(target=work)
        worker.start()
        entered.wait(1)
        gate.stopping = True
        try:
            self.assertFalse(gate.drain(timeout=0.02))
        finally:
            release.set()
            worker.join()
        self.assertTrue(gate.drain(timeout=0.02))

    def test_close_keeps_storage_and_site_lock_if_a_worker_cannot_drain(self):
        from quant_assistant.site_backup import SiteLock

        app = create_authenticated_app(Settings.for_data_dir(self.root))
        owner = app.state.auth.bootstrap("owner", "acceptance password 123456")
        contexts = app.state.user_contexts
        contexts.start()
        context = contexts.get(owner)
        scheduler = context.services.api_queries.market_advisor.scheduler
        scheduler.stop()
        release, done = Event(), Event()

        def work():
            release.wait(5)
            with context.services.database.transaction() as conn:
                conn.execute(
                    "INSERT INTO advisor_schedule_runs VALUES "
                    "('2026-10-05','morning','failed',now(),NULL,'interrupted')"
                )
            done.set()

        scheduler.thread = Thread(target=work)
        scheduler.thread.start()
        original = scheduler.stop
        try:
            with (
                patch.object(scheduler, "stop", side_effect=lambda: original(timeout=0.02)),
                self.assertRaises(RuntimeError),
            ):
                contexts.close()
            self.assertIsNotNone(context.services.database._connection)
            self.assertIsNotNone(contexts.site_lock)
            with self.assertRaises(RuntimeError):
                SiteLock(self.root).acquire()
        finally:
            release.set()
            contexts.close()
        self.assertTrue(done.is_set())
        with SiteLock(self.root):
            pass

    def test_drawdown_and_staleness_thresholds_and_future_evidence(self):
        from datetime import timedelta

        from quant_assistant.services.alert_center import AlertCenter

        db = Database(self.root / "quant_assistant.duckdb")
        self.addCleanup(db.close)
        db.initialize()
        now = datetime(2026, 10, 5, 10, tzinfo=SHANGHAI_TZ)
        center = AlertCenter(db, lambda: now)
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO positions VALUES('000001','fund',10,1,10,?,?)", [now.date(), now]
            )
            conn.execute(
                "INSERT INTO fund_nav(symbol,nav_date,unit_nav) VALUES('000001',?,1)",
                [now.date() - timedelta(days=5)],
            )
            conn.execute(
                "INSERT INTO portfolio_valuations(run_id,as_of_date,generated_at,data_snapshot_id,"
                "total_value,cash,unit_nav,high_water_mark) VALUES('fixture',?,?,1,850,0,0.85,1)",
                [now.date(), now],
            )
        first = center.read()
        self.assertIn("drawdown", {item["kind"] for item in first["items"]})
        center.save(
            {
                "revision": 0,
                "stale_after_days": 3,
                "drawdown_threshold": 0.2,
                "task_failure_days": 7,
            }
        )
        second = center.read()
        kinds = {item["kind"] for item in second["items"]}
        self.assertIn("data_stale", kinds)
        self.assertNotIn("drawdown", kinds)
        with db.transaction() as conn:
            conn.execute("UPDATE positions SET as_of_date=?", [now.date() + timedelta(days=1)])
            conn.execute("UPDATE fund_nav SET nav_date=?", [now.date()])
        self.assertIn("data_unknown", {item["kind"] for item in center.read()["items"]})

    def test_runtime_requires_admin_and_reports_degraded_accounts(self):
        app = create_authenticated_app(Settings.for_data_dir(self.root))
        app.state.auth.bootstrap("owner", "acceptance password 123456")
        user = app.state.auth.register("alice", "acceptance password 123456")
        path = self.root / "users" / user["id"]
        path.mkdir(parents=True)
        (path / "quant_assistant.duckdb").write_bytes(b"corrupted")
        with TestClient(app) as client:
            self.login(client, "alice")
            self.assertEqual(client.get("/api/v1/admin/runtime").status_code, 403)
            self.login(client, "owner")
            response = client.get("/api/v1/admin/runtime")
            self.assertEqual(response.status_code, 200)
            runtime = response.json()
            self.assertFalse(runtime["business_ready"])
            self.assertEqual(
                next(x for x in runtime["accounts"] if x["user_id"] == user["id"])["data_status"],
                "unavailable",
            )
            audit = app.state.auth.repository.audit()["items"]
            self.assertTrue(
                any(
                    item["action"] == "runtime_view" and item["target"] == user["username"]
                    for item in audit
                )
            )

    def test_alert_threshold_acknowledgment_and_account_isolation(self):
        app = create_authenticated_app(Settings.for_data_dir(self.root))
        app.state.auth.bootstrap("owner", "acceptance password 123456")
        alice = app.state.auth.register("alice", "acceptance password 123456")
        app.state.auth.register("bobby", "acceptance password 123456")
        with TestClient(app) as client:
            headers = self.login(client, "alice")
            self.assertEqual(client.get("/api/v1/alerts").status_code, 200)
            context = app.state.user_contexts.get(alice)
            now = datetime.now(SHANGHAI_TZ)
            with context.services.database.transaction() as conn:
                conn.execute(
                    "INSERT INTO positions VALUES('000001','fund',10,1,10,?,?)", [now.date(), now]
                )
                conn.execute(
                    "INSERT INTO market_advisor_reports "
                    "(report_id,report_date,kind,status,created_at,updated_at,error_code) "
                    "VALUES('fixture-failure',?,'morning','failed',?,?,'model_timeout')",
                    [now.date(), now, now],
                )
            first = client.get("/api/v1/alerts").json()
            kinds = {item["kind"] for item in first["items"]}
            self.assertTrue(
                {"data_unknown", "drawdown_unknown", "fee_evidence", "task_failure"} <= kinds
            )
            second = client.get("/api/v1/alerts").json()
            self.assertEqual({x["id"] for x in first["items"]}, {x["id"] for x in second["items"]})
            item = next(x for x in first["items"] if x["kind"] == "task_failure")
            self.assertEqual(
                client.post(
                    f"/api/v1/alerts/{item['id']}/acknowledge", headers=headers
                ).status_code,
                200,
            )
            self.assertTrue(
                next(
                    x for x in client.get("/api/v1/alerts").json()["items"] if x["id"] == item["id"]
                )["acknowledged"]
            )
            self.login(client, "bobby")
            self.assertEqual(client.get("/api/v1/alerts").json()["items"], [])
            self.assertEqual(
                client.post(
                    f"/api/v1/alerts/{item['id']}/acknowledge", headers=self.login(client, "bobby")
                ).status_code,
                404,
            )

    def test_identical_alerts_have_account_specific_identifiers(self):
        from quant_assistant.services.alert_center import AlertCenter

        ids = []
        for index in range(2):
            db = Database(self.root / str(index) / "quant_assistant.duckdb")
            self.addCleanup(db.close)
            db.initialize()
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO positions VALUES('000001','fund',10,1,10,current_date,now())"
                )
            ids.append({item["id"] for item in AlertCenter(db).read()["items"]})
        self.assertFalse(ids[0] & ids[1], "two accounts must not share private alert identifiers")

    def test_stop_waits_until_worker_releases_database(self):
        db = Database(self.root / "quant_assistant.duckdb")
        self.addCleanup(db.close)
        db.initialize()
        service = type("Service", (), {"database": db})()
        scheduler = AdvisorScheduler(service)
        started, release, finished = Event(), Event(), Event()

        def task():
            started.set()
            release.wait(5)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO advisor_schedule_runs VALUES "
                    "('2026-10-05','morning','failed',now(),NULL,'interrupted')"
                )
            finished.set()

        scheduler.thread = Thread(target=task)
        scheduler.thread.start()
        started.wait(1)
        timer = Thread(target=lambda: (time.sleep(2.2), release.set()))
        timer.start()
        try:
            scheduler.stop()
            self.assertTrue(finished.is_set(), "stop returned before a worker finished its write")
        finally:
            release.set()
            scheduler.thread.join() if scheduler.thread else None
            timer.join()

    def test_broken_business_database_fails_readiness_without_public_identity(self):
        app = create_authenticated_app(Settings.for_data_dir(self.root))
        owner = app.state.auth.bootstrap("owner", "acceptance password 123456")
        (self.root / "quant_assistant.duckdb").write_bytes(b"corrupted")
        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            response = client.get("/readyz")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(owner["id"], response.text)
            self.assertNotIn("owner", response.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
