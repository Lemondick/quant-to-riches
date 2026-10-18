"""Offline acceptance checks using temporary stores and mock model providers only."""

import json
import tempfile
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from unittest.mock import patch

from fastapi.testclient import TestClient

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.controllers.auth_app import create_authenticated_app
from quant_assistant.models.entities import Action, AssetType
from quant_assistant.models.repositories.database import Database
from quant_assistant.services.auth import AuthError, AuthService
from quant_assistant.services.history import csv_text, history
from quant_assistant.services.portfolio_commands import (
    InstrumentInput,
    PortfolioCommandService,
    TransactionInput,
)
from quant_assistant.services.runtime_limits import ModelJobGate, PublicReadCache
from quant_assistant.services.trading_calendar import TradingCalendar
from quant_assistant.site_backup import SiteLock, create_site_backup, restore_site_backup

PASSWORD = "acceptance password 123456"
NOW = datetime(2026, 10, 5, 10, tzinfo=SHANGHAI_TZ)


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "data"

    def database(self):
        db = Database(self.root / "quant_assistant.duckdb")
        self.addCleanup(db.close)
        db.initialize()
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO positions VALUES('CASH','cash',10000,1,10000,'2026-10-03',?)", [NOW]
            )
        return db

    def test_atomic_batch_and_distinct_stock_fund(self):
        db = self.database()
        service = PortfolioCommandService(db, lambda: NOW)
        metadata = (
            InstrumentInput("000001", AssetType.FUND, "基金", "OTC", date(2026, 10, 4)),
            InstrumentInput("000001", AssetType.STOCK, "股票", None, date(2026, 10, 4)),
        )
        items = (
            TransactionInput(
                "000001",
                AssetType.FUND,
                Action.BUY,
                Decimal(10),
                Decimal(1),
                Decimal(0),
                NOW.replace(day=4),
                "one",
            ),
            TransactionInput(
                "000001",
                AssetType.STOCK,
                Action.BUY,
                Decimal(100),
                Decimal(10),
                Decimal(0),
                NOW.replace(day=4),
                "two",
            ),
        )
        original = service._record_transaction

        def fail_second(item, conn):
            if item.external_id == "two":
                raise RuntimeError("simulated disk failure")
            return original(item, conn)

        with (
            patch.object(service, "_record_transaction", side_effect=fail_second),
            self.assertRaises(RuntimeError),
        ):
            service.record_transaction_batch(items, instrument_metadata=metadata)
        with db.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM transactions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM instruments").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM audit_log").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT market_value FROM positions WHERE symbol='CASH'").fetchone()[
                    0
                ],
                10000,
            )
        ids = service.record_transaction_batch(items, instrument_metadata=metadata)
        self.assertEqual(service.record_transaction_batch(items, instrument_metadata=metadata), ids)
        with db.connection() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT exchange FROM instruments WHERE symbol='000001.SZ'"
                ).fetchone()[0],
                "SZ",
            )
            self.assertEqual(
                set(
                    row[0]
                    for row in conn.execute(
                        "SELECT symbol FROM positions WHERE asset_type!='cash'"
                    ).fetchall()
                ),
                {"000001", "000001.SZ"},
            )
            self.assertEqual(
                conn.execute("SELECT market_value FROM positions WHERE symbol='CASH'").fetchone()[
                    0
                ],
                8990,
            )

    def test_legacy_stock_migration_preserves_sell_cost_snapshot(self):
        db = self.database()
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO instruments(symbol,asset_type,name,exchange,as_of_date) "
                "VALUES('000001','stock','旧股票','SZ','2026-10-04')"
            )
            conn.execute(
                "INSERT INTO positions VALUES('000001','stock',100,10,1000,'2026-10-04',?)", [NOW]
            )
            conn.execute(
                "INSERT INTO audit_log(entity_type,entity_id,changed_at,before_json,after_json) "
                "VALUES('transaction','1',?,?,?)",
                [
                    NOW,
                    json.dumps({"symbol": "000001", "asset_type": "stock", "average_cost": "10"}),
                    json.dumps({"symbol": "000001", "asset_type": "stock"}),
                ],
            )
        db.close()
        db.initialize()
        with db.connection() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT symbol,average_cost FROM positions WHERE asset_type='stock'"
                ).fetchone(),
                ("000001.SZ", Decimal(10)),
            )
            before = json.loads(conn.execute("SELECT before_json FROM audit_log").fetchone()[0])
            self.assertEqual((before["symbol"], before["average_cost"]), ("000001.SZ", "10"))

    def test_paging_filter_export_and_health(self):
        db = self.database()
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO transactions(symbol,asset_type,action,quantity,price,fees,traded_at,"
                "external_id,gross_amount,cash_delta,cash_symbol) "
                "SELECT '000001','fund','buy',1,1,0,?,cast(i AS VARCHAR),1,-1,'CASH' "
                "FROM range(501) t(i)",
                [NOW],
            )
        with db.connection() as conn:
            result = history(
                conn,
                "ledger",
                page=11,
                limit=50,
                symbol="000001",
                start=date(2026, 10, 5),
                end=date(2026, 10, 5),
            )
            self.assertEqual(
                (result["total"], len(result["items"]), result["has_more"]), (501, 1, False)
            )
            self.assertEqual(history(conn, "ledger", end=date(2026, 10, 4))["total"], 0)
        self.assertIn("'=SUM", csv_text([{"note": "=SUM(A1:A2)"}]))
        app = create_authenticated_app(Settings.for_data_dir(self.root))
        app.state.auth.bootstrap("owner", PASSWORD)
        with TestClient(app) as client:
            client.post(
                "/api/v1/auth/login",
                headers={"Origin": "http://testserver"},
                json={"username": "owner", "password": PASSWORD},
            )
            response = client.get("/api/v1/history/ledger/export?start=2026-10-05&end=2026-10-05")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 501)
            self.assertEqual(len({item["id"] for item in response.json()["items"]}), 501)
        from quant_assistant.services.api_queries.local_system import _SystemMixin

        class Broken:
            def data_status(self):
                raise RuntimeError("database offline")

        system = _SystemMixin()
        system._queries = Broken()
        system._database_unavailable_error = RuntimeError
        self.assertEqual(
            (system.health().status, system.health().database), ("degraded", "unavailable")
        )

    def test_admin_controls_usage_permissions_and_export(self):
        app = create_authenticated_app(Settings.for_data_dir(self.root))
        auth = app.state.auth
        owner = auth.bootstrap("owner", PASSWORD)
        alice = auth.register("alice", PASSWORD)
        bob = auth.register("bobby", PASSWORD)
        with TestClient(app) as client:
            origin = {"Origin": "http://testserver"}
            logged = client.post(
                "/api/v1/auth/login",
                headers=origin,
                json={"username": "alice", "password": PASSWORD},
            ).json()
            alice_cookie = client.cookies.get("quant_session")
            self.assertEqual(client.get("/api/v1/preferences").status_code, 200)
            self.assertEqual(client.get("/api/v1/admin/users").status_code, 403)
            context = app.state.user_contexts.get(alice)
            with context.services.database.transaction() as conn:
                conn.execute(
                    "INSERT INTO positions VALUES('000001','fund',10,1,12,'2026-10-04',?)", [NOW]
                )
            self.assertEqual(client.get("/api/v1/onboarding").status_code, 200)
            self.assertEqual(client.get("/api/v1/history/ledger").status_code, 200)
            logged = client.post(
                "/api/v1/auth/login",
                headers=origin,
                json={"username": "owner", "password": PASSWORD},
            ).json()
            admin_headers = {**origin, "X-CSRF-Token": logged["csrf_token"]}
            self.assertEqual(
                client.post(
                    "/api/v1/admin/registration", headers=origin, json={"enabled": False}
                ).status_code,
                403,
            )
            self.assertEqual(
                client.post(
                    "/api/v1/admin/registration", headers=admin_headers, json={"enabled": False}
                ).status_code,
                200,
            )
            with self.assertRaises(AuthError) as failure:
                auth.register("paused", PASSWORD)
            self.assertEqual(failure.exception.code, "registration_paused")
            self.assertEqual(
                client.post(
                    "/api/v1/admin/registration", headers=admin_headers, json={"enabled": True}
                ).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    f"/api/v1/admin/users/{owner['id']}/status",
                    headers=admin_headers,
                    json={"status": "disabled"},
                ).status_code,
                409,
            )
            details = client.get(f"/api/v1/admin/users/{alice['id']}/details").json()
            self.assertEqual(details["items"][0]["floating_profit"], "2.00000000000000")
            self.assertEqual(
                client.get(f"/api/v1/admin/users/{bob['id']}/details").json()["total"], 0
            )
            self.assertEqual(
                client.get(f"/api/v1/admin/users/{alice['id']}/export").json()["total"], 1
            )
            usage = client.get(f"/api/v1/admin/users/{alice['id']}/usage").json()
            self.assertGreaterEqual(usage["requests_7d"], 3)
            self.assertIsNotNone(usage["last_activity_at"])
            self.assertEqual(
                client.post(
                    f"/api/v1/admin/users/{alice['id']}/status",
                    headers=admin_headers,
                    json={"status": "disabled"},
                ).status_code,
                200,
            )
            self.assertFalse(context.services.api_queries.agent_research.account_enabled())
            advisor = context.services.api_queries.market_advisor
            with context.services.database.transaction() as conn:
                conn.execute(
                    "INSERT INTO market_advisor_reports(report_id,report_date,kind,status,"
                    "created_at,updated_at) "
                    "VALUES('disabled-queue','2026-10-05','morning','queued',?,?)",
                    [NOW, NOW],
                )
            with patch.object(
                advisor.models, "chat", side_effect=AssertionError("must not invoke a model")
            ):
                interrupted = advisor.execute("disabled-queue")
                self.assertEqual(
                    (interrupted["status"], interrupted["error_code"]), ("failed", "interrupted")
                )
            self.assertEqual(
                client.get(
                    "/api/v1/preferences", headers={"Cookie": "quant_session=" + alice_cookie}
                ).status_code,
                401,
            )
            self.assertEqual(
                client.post(
                    f"/api/v1/admin/users/{alice['id']}/status",
                    headers=admin_headers,
                    json={"status": "active"},
                ).status_code,
                200,
            )
            self.assertEqual(
                client.get(
                    "/api/v1/preferences", headers={"Cookie": "quant_session=" + alice_cookie}
                ).status_code,
                401,
            )
            second = auth.login("alice", PASSWORD)
            self.assertEqual(
                client.post(
                    f"/api/v1/admin/users/{alice['id']}/revoke-sessions", headers=admin_headers
                ).status_code,
                200,
            )
            with self.assertRaises(AuthError):
                auth.session(second["token"])
            self.assertGreaterEqual(client.get("/api/v1/admin/audit").json()["total"], 5)

    def test_30_simultaneous_accounts_cache_and_model_cap(self):
        auth = AuthService(self.root)
        owner = auth.bootstrap("owner", PASSWORD)
        accounts = [owner] + [auth.register(f"user{i:02}", PASSWORD) for i in range(29)]
        with self.assertRaises(AuthError):
            auth.register("overflow", PASSWORD)
        app = create_authenticated_app(Settings.for_data_dir(self.root))
        app.state.user_contexts.start()
        self.addCleanup(app.state.user_contexts.close)
        clients = []
        for account in accounts:
            client = TestClient(app)
            client.cookies.set("quant_session", auth.login(account["username"], PASSWORD)["token"])
            clients.append(client)
            self.addCleanup(client.close)

        def read(pair):
            index, client = pair
            response = client.get("/api/v1/preferences")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["X-Quant-User-ID"], accounts[index]["id"])
            return response.json()

        with ThreadPoolExecutor(max_workers=30) as workers:
            self.assertEqual(len(list(workers.map(read, enumerate(clients)))), 30)
        lock = Lock()
        counts = {"active": 0, "peak": 0, "loads": 0}

        class Provider:
            def estimate(self, symbol):
                with lock:
                    counts["loads"] += 1
                time.sleep(0.02)
                return {"symbol": symbol, "value": []}

        cache = PublicReadCache(Provider())
        gate = ModelJobGate(3)

        def job(_index):
            item = cache.estimate("000001")
            item["value"].append("private")
            with gate.acquire(lambda: True, wait=True) as acquired:
                self.assertTrue(acquired)
                with lock:
                    counts["active"] += 1
                    counts["peak"] = max(counts["peak"], counts["active"])
                time.sleep(0.02)
                with lock:
                    counts["active"] -= 1

        with ThreadPoolExecutor(max_workers=30) as workers:
            list(workers.map(job, range(30)))
        self.assertEqual(counts["loads"], 1)
        self.assertEqual(counts["peak"], 3)
        self.assertEqual(cache.estimate("000001")["value"], [])

    def test_full_backup_restore_and_corruption_rejection(self):
        auth = AuthService(self.root)
        auth.bootstrap("owner", PASSWORD)
        user = auth.register("alice", PASSWORD)
        old = auth.login("alice", PASSWORD)
        db = self.database()
        directory = self.root / "users" / user["id"]
        directory.mkdir(parents=True)
        (directory / "preferences.json").write_text('{"watchlist":[]}', encoding="utf-8")
        secret = directory / "server-secrets"
        secret.mkdir()
        (secret / "provider.key").write_text("synthetic-secret", encoding="utf-8")
        db.close()
        output = self.root.parent / "backup" / "site.zip"
        with SiteLock(self.root), self.assertRaises(RuntimeError):
            create_site_backup(self.root, output)
        create_site_backup(self.root, output)
        restored = self.root.parent / "restored"
        restore_site_backup(output, restored)
        self.assertTrue((restored / "users" / user["id"] / "preferences.json").is_file())
        self.assertFalse((restored / "users" / user["id"] / "server-secrets/provider.key").exists())
        restored_auth = AuthService(restored)
        self.assertEqual(restored_auth.login("alice", PASSWORD)["user"]["id"], user["id"])
        with self.assertRaises(AuthError):
            restored_auth.session(old["token"])
        with self.assertRaises(ValueError):
            restore_site_backup(output, restored)
        bad = self.root.parent / "bad.zip"
        with zipfile.ZipFile(output) as source, zipfile.ZipFile(bad, "w") as target:
            for name in source.namelist():
                target.writestr(
                    name,
                    b"corrupted"
                    if name == "users/" + user["id"] + "/preferences.json"
                    else source.read(name),
                )
        failed = self.root.parent / "failed"
        with self.assertRaises(ValueError):
            restore_site_backup(bad, failed)
        self.assertFalse(failed.exists())

    def test_year_calendar_validation_and_visible_missing_year(self):
        calendar = TradingCalendar(self.root / "calendars")
        self.assertFalse(calendar.trading_day(date(2026, 10, 5)))
        self.assertTrue(calendar.trading_day(date(2026, 10, 8)))
        self.assertIsNone(calendar.trading_day(date(2027, 1, 4)))
        with self.assertRaises(ValueError):
            calendar.save(
                {"year": 2027, "source": "https://example.com/fake", "holidays": ["2027-01-01"]}
            )
        # Synthetic future calendar is a test fixture, never a published holiday prediction.
        calendar.save(
            {
                "year": 2027,
                "source": "https://www.sse.com.cn/test-fixture",
                "holidays": ["2027-01-01"],
            }
        )
        self.assertTrue(calendar.trading_day(date(2027, 1, 4)))
        self.assertFalse(calendar.trading_day(date(2027, 1, 1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
