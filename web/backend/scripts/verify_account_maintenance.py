"""Offline account-maintenance acceptance checks against real temporary databases."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.controllers.auth_app import create_authenticated_app
from quant_assistant.models.repositories.database import Database
from quant_assistant.services.auth import COOKIE_NAME, AuthError
from quant_assistant.site_backup import create_site_backup, restore_site_backup

PASSWORD = "acceptance password 123456"
NEW_PASSWORD = "replacement password 987654"
NOW = datetime(2026, 10, 5, 10, tzinfo=SHANGHAI_TZ)
ORIGIN = {"Origin": "http://testserver"}


class AccountMaintenance(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "data"
        self.app = create_authenticated_app(Settings.for_data_dir(self.root))
        self.auth = self.app.state.auth
        self.now = NOW.timestamp()
        self.auth.clock = lambda: self.now
        self.owner = self.auth.bootstrap("owner", PASSWORD)
        self.alice = self.auth.register("alice", PASSWORD)
        self.bob = self.auth.register("bob", PASSWORD)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.user_contexts.close)
        self.owner_login = self.sign_in("owner")

    def sign_in(self, username, password=PASSWORD):
        response = self.client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {**response.json(), "cookie": self.client.cookies.get(COOKIE_NAME)}

    def headers(self, login):
        return {
            **ORIGIN,
            "X-CSRF-Token": login["csrf_token"],
            "Cookie": COOKIE_NAME + "=" + login["cookie"],
        }

    def database(self, user):
        directory = self.app.state.user_contexts.directory(user)
        directory.mkdir(parents=True, exist_ok=True)
        database = Database(directory / "quant_assistant.duckdb")
        database.initialize()
        self.addCleanup(database.close)
        return database

    def issue(self, user=None):
        selected = user or self.alice
        return self.client.post(
            f"/api/v1/admin/users/{selected['id']}/recovery-token",
            headers=self.headers(self.owner_login),
        )

    def redeem(self, token, username="alice", password=NEW_PASSWORD):
        return self.client.post(
            "/api/v1/auth/recovery/redeem",
            headers=ORIGIN,
            json={"username": username, "token": token, "new_password": password},
        )

    def test_details_export_and_report_reads_are_audited_without_payloads(self):
        database = self.database(self.alice)
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO positions VALUES('CASH','cash',4321,1,4321,'2026-10-04',?)", [NOW]
            )
            connection.execute(
                "INSERT INTO market_advisor_reports(report_id,report_date,kind,status,"
                "created_at,updated_at,report_json) VALUES('report-1','2026-10-04','morning',"
                "'completed',?,?,?)",
                [NOW, NOW, json.dumps({"private": "never-audit-this"})],
            )
        database.close()
        base = f"/api/v1/admin/users/{self.alice['id']}"
        for path in (
            "/details?section=ledger&start=2026-10-01&end=2026-10-05&page=2&limit=20",
            "/export?section=holdings",
            "/reports/advisor/report-1",
        ):
            response = self.client.get(base + path)
            self.assertEqual(response.status_code, 200, response.text)
        with self.auth.repository.connection() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM admin_audit")]
        self.assertEqual(len(rows), 3, "Each successful sensitive read must add one audit row")
        self.assertEqual(
            {row["action"] for row in rows}, {"details_read", "details_export", "report_read"}
        )
        self.assertTrue(all(row["actor_id"] == self.owner["id"] for row in rows))
        self.assertTrue(all(row["target_id"] == self.alice["id"] for row in rows))
        detail = json.loads(rows[0]["detail"])
        self.assertEqual(detail["category"], "ledger")
        self.assertEqual(detail["range"]["start"], "2026-10-01")
        self.assertEqual(detail["range"]["end"], "2026-10-05")
        self.assertEqual(detail["range"]["page"], 2)
        audit_text = json.dumps(rows)
        for secret in ("never-audit-this", "4321", PASSWORD, self.owner_login["cookie"]):
            self.assertNotIn(secret, audit_text)

    def test_ordinary_users_cannot_read_admin_data_or_issue_recovery(self):
        login = self.sign_in("alice")
        base = f"/api/v1/admin/users/{self.bob['id']}"
        for suffix in ("/details", "/export", "/timeline", "/usage", "/reports/advisor/x"):
            self.assertEqual(self.client.get(base + suffix).status_code, 403, suffix)
        self.assertEqual(
            self.client.post(base + "/recovery-token", headers=self.headers(login)).status_code, 403
        )
        self.assertEqual(self.auth.repository.audit()["total"], 0)

    def test_session_list_revoke_and_cross_account_denial(self):
        alice_one = self.sign_in("alice")
        alice_two = self.sign_in("alice")
        bob = self.sign_in("bob")
        bob_sessions = self.client.get("/api/v1/auth/sessions", headers=self.headers(bob))
        self.assertEqual(bob_sessions.status_code, 200, bob_sessions.text)
        bob_id = bob_sessions.json()["items"][0]["id"]
        response = self.client.get("/api/v1/auth/sessions", headers=self.headers(alice_one))
        self.assertEqual(response.status_code, 200, response.text)
        sessions = response.json()["items"]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sum(item["current"] for item in sessions), 1)
        self.assertNotIn("token_hash", response.text)
        self.assertNotIn("csrf", response.text)
        for account_session in (alice_one, alice_two):
            self.assertNotIn(account_session["cookie"], response.text)
        denied = self.client.post(
            f"/api/v1/auth/sessions/{bob_id}/revoke", headers=self.headers(alice_one)
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(bob)).status_code, 200
        )
        other_id = next(item["id"] for item in sessions if not item["current"])
        self.assertEqual(
            self.client.post(
                f"/api/v1/auth/sessions/{other_id}/revoke", headers=self.headers(alice_one)
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(alice_two)).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(alice_one)).status_code,
            200,
        )
        current_id = next(item["id"] for item in sessions if item["current"])
        self.assertEqual(
            self.client.post(
                f"/api/v1/auth/sessions/{current_id}/revoke", headers=self.headers(alice_one)
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(alice_one)).status_code,
            401,
        )

    def test_revoke_other_devices_keeps_current_and_other_account(self):
        one = self.sign_in("alice")
        two = self.sign_in("alice")
        bob = self.sign_in("bob")
        response = self.client.post(
            "/api/v1/auth/sessions/revoke-others", headers=self.headers(one)
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revoked"], 1)
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(one)).status_code, 200
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(two)).status_code, 401
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(bob)).status_code, 200
        )

    def test_recovery_expiry_username_binding_rotation_and_weak_password(self):
        response = self.issue()
        self.assertEqual(response.status_code, 201, response.text)
        token = response.json()["token"]
        self.assertEqual(response.json()["expires_at"], "2026-10-05T02:15:00+00:00")
        self.assertEqual(self.redeem(token, username="bob").status_code, 400)
        self.assertEqual(self.redeem(token, password="short").status_code, 422)
        rotated = self.issue().json()["token"]
        self.assertEqual(self.redeem(token).status_code, 400)
        self.now += 900
        self.assertEqual(self.redeem(rotated).status_code, 400, "Expiry boundary is exclusive")
        with self.auth.repository.connection() as connection:
            raw = [dict(row) for row in connection.execute("SELECT * FROM recovery_tokens")]
        self.assertNotIn(rotated, json.dumps(raw))
        self.assertNotIn(token, json.dumps(raw))
        self.assertNotIn(token, json.dumps(self.auth.repository.audit()))
        self.assertEqual(self.auth.login("alice", PASSWORD)["user"]["id"], self.alice["id"])

    def test_recovery_is_atomic_single_use_and_revokes_all_sessions(self):
        one = self.sign_in("alice")
        two = self.sign_in("alice")
        bob = self.sign_in("bob")
        response = self.issue()
        self.assertEqual(response.status_code, 201, response.text)
        token = response.json()["token"]
        self.client.cookies.clear()  # Forgotten-password redemption must work without a session.
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self.redeem(token).status_code, range(2)))
        self.assertEqual(sorted(results), [200, 400])
        self.assertEqual(self.redeem(token).status_code, 400)
        for login in (one, two):
            self.assertEqual(
                self.client.get("/api/v1/auth/session", headers=self.headers(login)).status_code,
                401,
            )
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(bob)).status_code, 200
        )
        with self.assertRaises(AuthError):
            self.auth.login("alice", PASSWORD)
        self.assertEqual(self.auth.login("alice", NEW_PASSWORD)["user"]["id"], self.alice["id"])
        self.assertNotIn(token, json.dumps(self.auth.repository.audit()))
        self.assertNotIn(NEW_PASSWORD, json.dumps(self.auth.repository.audit()))

    def test_timeline_pages_real_actions_and_trends_exclude_request_polling(self):
        database = self.database(self.alice)
        with database.transaction() as connection:
            for entity, entity_id, day in (
                ("portfolio_account_import", "import-1", 3),
                ("transaction", "7", 4),
            ):
                connection.execute(
                    "INSERT INTO audit_log(entity_type,entity_id,changed_at,after_json) "
                    "VALUES(?,?,?,?)",
                    [
                        entity,
                        entity_id,
                        NOW.replace(day=day),
                        json.dumps({"private": "secret-financial-payload"}),
                    ],
                )
            connection.execute(
                "INSERT INTO data_snapshots(dataset,source,as_of_date,fetched_at,path,sha256,"
                "row_count,status,issues_json) "
                "VALUES('fund_nav','fixture','2026-10-04',?,'private-path','fixture-hash',1,'valid','[]')",
                [NOW.replace(day=4)],
            )
            connection.execute(
                "INSERT INTO market_advisor_reports(report_id,report_date,kind,status,created_at,"
                "updated_at,report_json) "
                "VALUES('failed-report','2026-10-05','morning','failed',?,?,NULL)",
                [NOW, NOW],
            )
        database.close()
        for _ in range(12):
            self.auth.repository.record_usage(self.alice["id"], "research", 200, 1, self.now)
        base = f"/api/v1/admin/users/{self.alice['id']}/timeline?category=business&limit=2"
        response = self.client.get(base)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["total"], 4)
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(len(data["trends"]), 30)
        self.assertEqual(sum(item["actions"] for item in data["trends"]), 4)
        self.assertEqual(sum(item["failures"] for item in data["trends"]), 1)
        second = self.client.get(base + "&page=2").json()
        events = data["items"] + second["items"]
        self.assertEqual(
            {item["category"] for item in events}, {"imports", "trades", "nav", "reports"}
        )
        self.assertEqual(len({item["id"] for item in events}), 4)
        self.assertNotIn("secret-financial-payload", json.dumps(data))
        self.assertNotIn("private-path", json.dumps(data))
        self.assertEqual(
            self.client.get(
                f"/api/v1/admin/users/{self.bob['id']}/timeline?category=business"
            ).json()["total"],
            0,
        )
        accounts = self.client.get(
            f"/api/v1/admin/users/{self.alice['id']}/timeline?category=account"
        ).json()
        self.assertEqual(
            accounts["total"], 1, "Only real registry creation is present for unused account"
        )
        self.assertEqual(accounts["items"][0]["action"], "account_created")

    def test_account_timeline_reuses_real_administrator_mutations_without_read_polling(self):
        self.sign_in("alice")
        base = f"/api/v1/admin/users/{self.alice['id']}"
        for status in ("disabled", "active"):
            response = self.client.post(
                base + "/status", headers=self.headers(self.owner_login), json={"status": status}
            )
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.issue().status_code, 201)
        self.assertEqual(
            self.client.post(
                base + "/revoke-sessions", headers=self.headers(self.owner_login)
            ).status_code,
            200,
        )
        for _ in range(2):
            self.assertEqual(
                self.client.get(
                    base + "/details", headers=self.headers(self.owner_login)
                ).status_code,
                200,
            )
        response = self.client.get(
            base + "/timeline?category=account", headers=self.headers(self.owner_login)
        )
        self.assertEqual(response.status_code, 200, response.text)
        events = response.json()
        self.assertEqual(events["total"], 6)
        self.assertEqual([item["action"] for item in events["items"]].count("status"), 2)
        self.assertEqual(
            {item["action"] for item in events["items"]},
            {"account_created", "login", "status", "recovery_issued", "revoke_sessions"},
        )

    def test_restore_cannot_resurrect_a_used_recovery_token(self):
        token = self.issue().json()["token"]
        archive = self.root.parent / "site.zip"
        create_site_backup(self.root, archive)
        self.assertEqual(self.redeem(token).status_code, 200)
        restored = self.root.parent / "restored"
        restore_site_backup(archive, restored)
        from quant_assistant.services.auth import AuthService

        auth = AuthService(restored, clock=lambda: self.now)
        with self.assertRaises(AuthError):
            auth.redeem_recovery("alice", token, NEW_PASSWORD)
        self.assertEqual(auth.login("alice", PASSWORD)["user"]["id"], self.alice["id"])

    def test_session_mutations_require_csrf_and_recovery_requires_same_origin(self):
        login = self.sign_in("alice")
        self.assertEqual(
            self.client.post("/api/v1/auth/sessions/revoke-others", headers=ORIGIN).status_code, 403
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/session", headers=self.headers(login)).status_code, 200
        )
        token = self.issue().json()["token"]
        self.client.cookies.clear()
        response = self.client.post(
            "/api/v1/auth/recovery/redeem",
            json={
                "username": "alice",
                "token": token,
                "new_password": NEW_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.redeem(token).status_code, 200)

    def test_ordinary_password_change_invalidates_existing_recovery_token(self):
        login = self.sign_in("alice")
        token = self.issue().json()["token"]
        response = self.client.post(
            "/api/v1/auth/password",
            headers=self.headers(login),
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.redeem(token).status_code, 400)
        self.assertEqual(self.auth.login("alice", NEW_PASSWORD)["user"]["id"], self.alice["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
