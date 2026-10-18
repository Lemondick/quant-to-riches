"""Private SQLite account registry with atomic registration and revocable sessions."""

from __future__ import annotations

import getpass
import json
import os
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

ACCOUNT_LIMIT = 30


class AccountConflict(ValueError):
    """An expected account-state conflict, safe to map to a fixed public error."""


class AuthRepository:
    def __init__(self, root: Path):
        directory = root.resolve() / "auth"
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            subprocess.run(
                [
                    "icacls",
                    str(directory),
                    "/inheritance:r",
                    "/grant:r",
                    getpass.getuser() + ":(OI)(CI)F",
                    "SYSTEM:(OI)(CI)F",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.chmod(directory, 0o700)
        self.path = directory / "accounts.sqlite3"
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
                    created_at REAL NOT NULL, last_login_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS single_admin
                    ON users(role) WHERE role='admin';
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    csrf TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS user_sessions ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS site_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO site_settings VALUES('registration_enabled','1');
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id INTEGER PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL,
                    target_id TEXT, detail TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_daily (
                    user_id TEXT NOT NULL, day TEXT NOT NULL, category TEXT NOT NULL,
                    requests INTEGER NOT NULL, errors INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL, last_activity_at REAL NOT NULL,
                    PRIMARY KEY(user_id,day,category)
                );
                CREATE TABLE IF NOT EXISTS recovery_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
                    issued_by TEXT NOT NULL REFERENCES users(id),
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, consumed_at REAL
                );
                CREATE TABLE IF NOT EXISTS account_activity (
                    id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    action TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS account_activity_user ON account_activity(user_id);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "session_id" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN session_id TEXT")
            if "device" not in columns:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN device TEXT NOT NULL DEFAULT '未知设备'"
                )
            for row in conn.execute("SELECT token_hash FROM sessions WHERE session_id IS NULL"):
                conn.execute(
                    "UPDATE sessions SET session_id=? WHERE token_hash=?", [uuid4().hex, row[0]]
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS session_identity ON sessions(session_id)"
            )
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self, *, write=False):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def status(self):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS n, count(*) FILTER(WHERE role='admin') AS admins, "
                "count(*) FILTER(WHERE status='active') AS active FROM users"
            ).fetchone()
            enabled = (
                conn.execute(
                    "SELECT value FROM site_settings WHERE key='registration_enabled'"
                ).fetchone()[0]
                == "1"
            )
        return {
            "registered": row["n"],
            "initialized": bool(row["admins"]),
            "active": row["active"],
            "registration_enabled": enabled,
        }

    def create(self, user_id, username, password_hash, now, *, admin=False):
        with self.connection(write=True) as conn:
            count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
            if admin:
                if count:
                    raise AccountConflict("already_initialized")
            elif not conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                raise AccountConflict("registration_closed")
            elif (
                conn.execute(
                    "SELECT value FROM site_settings WHERE key='registration_enabled'"
                ).fetchone()[0]
                != "1"
            ):
                raise AccountConflict("registration_paused")
            if conn.execute("SELECT 1 FROM users WHERE username=?", [username]).fetchone():
                raise AccountConflict("username_taken")
            if count >= ACCOUNT_LIMIT:
                raise AccountConflict("registration_full")
            conn.execute(
                "INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                [user_id, username, password_hash, "admin" if admin else "user", now],
            )
            return dict(conn.execute("SELECT * FROM users WHERE id=?", [user_id]).fetchone())

    def user(self, username):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", [username]).fetchone()
        return dict(row) if row else None

    def users(self):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY created_at,id")]

    def by_id(self, user_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", [user_id]).fetchone()
        return dict(row) if row else None

    def create_session(
        self, username, expected_hash, token_hash, csrf, now, expires, device="未知设备"
    ):
        with self.connection(write=True) as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", [username]).fetchone()
            if not row or row["status"] != "active" or row["password_hash"] != expected_hash:
                raise AccountConflict("invalid_credentials")
            conn.execute("DELETE FROM sessions WHERE expires_at<=?", [now])
            conn.execute(
                "INSERT INTO sessions(token_hash,user_id,csrf,created_at,expires_at,"
                "session_id,device) "
                "VALUES(?,?,?,?,?,?,?)",
                [token_hash, row["id"], csrf, now, expires, uuid4().hex, device],
            )
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?", [now, row["id"]])
            conn.execute(
                "INSERT INTO account_activity(user_id,action,created_at) VALUES(?,?,?)",
                [row["id"], "login", now],
            )
            return {**dict(row), "last_login_at": now}

    def session(self, token_hash, now):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT u.*, s.csrf, s.expires_at FROM sessions s "
                "JOIN users u ON u.id=s.user_id WHERE s.token_hash=? "
                "AND s.expires_at>? AND u.status='active'",
                [token_hash, now],
            ).fetchone()
        return dict(row) if row else None

    def revoke(self, token_hash, now=None):
        with self.connection(write=True) as conn:
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE token_hash=?", [token_hash]
            ).fetchone()
            conn.execute("DELETE FROM sessions WHERE token_hash=?", [token_hash])
            if row and now is not None:
                conn.execute(
                    "INSERT INTO account_activity(user_id,action,created_at) VALUES(?,?,?)",
                    [row["user_id"], "logout", now],
                )

    def sessions(self, user_id, token_hash, now):
        with self.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT session_id AS id,device,created_at,expires_at,token_hash=? AS current "
                    "FROM sessions WHERE user_id=? AND expires_at>? "
                    "ORDER BY created_at DESC,session_id",
                    [token_hash, user_id, now],
                )
            ]

    def revoke_owned_sessions(self, user_id, token_hash, now, *, session_id=None):
        with self.connection(write=True) as conn:
            if not conn.execute(
                "SELECT 1 FROM sessions WHERE user_id=? AND token_hash=? AND expires_at>?",
                [user_id, token_hash, now],
            ).fetchone():
                raise AccountConflict("authentication_required")
            if session_id is not None:
                row = conn.execute(
                    "SELECT token_hash FROM sessions "
                    "WHERE user_id=? AND session_id=? AND expires_at>?",
                    [user_id, session_id, now],
                ).fetchone()
                if not row:
                    raise AccountConflict("session_not_found")
                current = row["token_hash"] == token_hash
                result = conn.execute(
                    "DELETE FROM sessions WHERE user_id=? AND session_id=?", [user_id, session_id]
                )
            else:
                current = False
                result = conn.execute(
                    "DELETE FROM sessions WHERE user_id=? AND token_hash!=?", [user_id, token_hash]
                )
            conn.execute(
                "INSERT INTO account_activity(user_id,action,created_at) VALUES(?,?,?)",
                [user_id, "session_revoked" if session_id else "other_sessions_revoked", now],
            )
            return {"revoked": result.rowcount, "current_revoked": current}

    @staticmethod
    def _administrator(conn, actor_id):
        actor = conn.execute("SELECT role,status FROM users WHERE id=?", [actor_id]).fetchone()
        if not actor or actor["role"] != "admin" or actor["status"] != "active":
            raise AccountConflict("admin_required")

    def audit_access(self, actor_id, action, target_id, category, access_range, now):
        with self.connection(write=True) as conn:
            self._administrator(conn, actor_id)
            if not conn.execute("SELECT 1 FROM users WHERE id=?", [target_id]).fetchone():
                raise AccountConflict("account_not_found")
            conn.execute(
                "INSERT INTO admin_audit(actor_id,action,target_id,detail,created_at) "
                "VALUES(?,?,?,?,?)",
                [
                    actor_id,
                    action,
                    target_id,
                    json.dumps({"category": category, "range": access_range}),
                    now,
                ],
            )

    def issue_recovery(self, actor_id, user_id, token_hash, now, expires):
        with self.connection(write=True) as conn:
            self._administrator(conn, actor_id)
            if not conn.execute(
                "SELECT 1 FROM users WHERE id=? AND status='active'", [user_id]
            ).fetchone():
                raise AccountConflict("account_not_found")
            conn.execute("DELETE FROM recovery_tokens WHERE user_id=?", [user_id])
            conn.execute(
                "INSERT INTO recovery_tokens VALUES(?,?,?,?,?,NULL)",
                [token_hash, user_id, actor_id, now, expires],
            )
            conn.execute(
                "INSERT INTO admin_audit(actor_id,action,target_id,detail,created_at) "
                "VALUES(?,?,?,?,?)",
                [actor_id, "recovery_issued", user_id, json.dumps({"expires_at": expires}), now],
            )

    def redeem_recovery(self, username, token_hash, password_hash, now):
        """Consume the token, replace the password and revoke sessions in one write lock."""
        with self.connection(write=True) as conn:
            row = conn.execute(
                "SELECT r.user_id,r.issued_by FROM recovery_tokens r "
                "JOIN users u ON u.id=r.user_id "
                "WHERE r.token_hash=? AND u.username=? AND u.status='active' "
                "AND r.expires_at>? AND r.consumed_at IS NULL",
                [token_hash, username, now],
            ).fetchone()
            if not row:
                raise AccountConflict("invalid_recovery_token")
            conn.execute(
                "UPDATE recovery_tokens SET consumed_at=? WHERE token_hash=?", [now, token_hash]
            )
            conn.execute(
                "UPDATE users SET password_hash=? WHERE id=?", [password_hash, row["user_id"]]
            )
            conn.execute("DELETE FROM sessions WHERE user_id=?", [row["user_id"]])
            conn.execute(
                "INSERT INTO account_activity(user_id,action,created_at) VALUES(?,?,?)",
                [row["user_id"], "password_recovered", now],
            )
            conn.execute(
                "INSERT INTO admin_audit(actor_id,action,target_id,detail,created_at) "
                "VALUES(?,?,?,?,?)",
                [row["issued_by"], "recovery_redeemed", row["user_id"], "{}", now],
            )

    def administer(self, actor_id, action, target_id, value, now):
        """Authorize, update, revoke sessions and audit in the same transaction."""
        with self.connection(write=True) as conn:
            actor = conn.execute("SELECT role,status FROM users WHERE id=?", [actor_id]).fetchone()
            if not actor or actor["role"] != "admin" or actor["status"] != "active":
                raise AccountConflict("admin_required")
            if action == "registration":
                conn.execute(
                    "UPDATE site_settings SET value=? WHERE key='registration_enabled'",
                    ["1" if value else "0"],
                )
            elif action == "calendar":
                pass  # The validated calendar file has been saved by the administrator route.
            elif action in {"status", "revoke_sessions"}:
                target = conn.execute("SELECT role FROM users WHERE id=?", [target_id]).fetchone()
                if not target:
                    raise AccountConflict("account_not_found")
                if target["role"] == "admin":
                    raise AccountConflict("admin_protected")
                if action == "status":
                    if value not in {"active", "disabled"}:
                        raise ValueError("invalid account status")
                    conn.execute("UPDATE users SET status=? WHERE id=?", [value, target_id])
                conn.execute("DELETE FROM sessions WHERE user_id=?", [target_id])
            else:
                raise ValueError("unsupported administrator operation")
            conn.execute(
                "INSERT INTO admin_audit(actor_id,action,target_id,detail,created_at) "
                "VALUES(?,?,?,?,?)",
                [actor_id, action, target_id, json.dumps({"value": value}), now],
            )

    def audit(self, page=1, limit=50):
        with self.connection() as conn:
            total = conn.execute("SELECT count(*) FROM admin_audit").fetchone()[0]
            items = [
                dict(row)
                for row in conn.execute(
                    "SELECT a.id,a.action,a.detail,a.created_at,u.username AS actor,"
                    "t.username AS target FROM admin_audit a JOIN users u ON u.id=a.actor_id "
                    "LEFT JOIN users t ON t.id=a.target_id "
                    "ORDER BY a.id DESC LIMIT ? OFFSET ?",
                    [limit, (page - 1) * limit],
                )
            ]
        return {"items": items, "total": total, "page": page, "limit": limit}

    def record_usage(self, user_id, category, status, duration_ms, now):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        day = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()
        with self.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO usage_daily VALUES(?,?,?,1,?,?,?) ON CONFLICT(user_id,day,category) "
                "DO UPDATE SET requests=requests+1,errors=errors+excluded.errors,"
                "duration_ms=duration_ms+excluded.duration_ms,last_activity_at=excluded.last_activity_at",
                [user_id, day, category, int(status >= 400), max(0, duration_ms), now],
            )
            # Retention pruning is a full-table scan; it must not run per request.
            # Once per process-day is enough, and the DELETE itself is idempotent,
            # so a racing thread may repeat it at worst once on a day rollover.
            if day != getattr(self, "_usage_pruned_day", None):
                conn.execute("DELETE FROM usage_daily WHERE day < date(?, '-90 day')", [day])
                self._usage_pruned_day = day

    def usage(self, user_id, now):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        today = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date()
        with self.connection() as conn:
            recent = [
                dict(row)
                for row in conn.execute(
                    "SELECT day,category,requests,errors,duration_ms FROM usage_daily "
                    "WHERE user_id=? AND day>=? ORDER BY day DESC,category",
                    [user_id, (today - timedelta(days=6)).isoformat()],
                )
            ]
            last = conn.execute(
                "SELECT max(last_activity_at) FROM usage_daily WHERE user_id=?", [user_id]
            ).fetchone()[0]
        return {
            "last_activity_at": last,
            "requests_7d": sum(row["requests"] for row in recent),
            "errors_7d": sum(row["errors"] for row in recent),
            "items": recent,
        }

    def set_password(self, user_id, new_hash, *, expected_hash=None, token_hash=None, now=None):
        with self.connection(write=True) as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", [user_id]).fetchone()
            if not row or (expected_hash is not None and row["password_hash"] != expected_hash):
                raise AccountConflict("invalid_credentials")
            if (
                token_hash is not None
                and not conn.execute(
                    "SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?",
                    [token_hash, user_id, now],
                ).fetchone()
            ):
                raise AccountConflict("authentication_required")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", [new_hash, user_id])
            conn.execute("DELETE FROM sessions WHERE user_id=?", [user_id])
            conn.execute("DELETE FROM recovery_tokens WHERE user_id=?", [user_id])
            if now is not None:
                conn.execute(
                    "INSERT INTO account_activity(user_id,action,created_at) VALUES(?,?,?)",
                    [user_id, "password_changed", now],
                )
