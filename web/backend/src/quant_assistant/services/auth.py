"""Account passwords and opaque server-side sessions; no default credentials."""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from quant_assistant.models.repositories.auth import (
    ACCOUNT_LIMIT,
    AccountConflict,
    AuthRepository,
)

SESSION_SECONDS = 12 * 3600
RECOVERY_SECONDS = 15 * 60
COOKIE_NAME = "quant_session"
ERRORS = {
    "registration_closed": (409, "请先由管理者在本机初始化账号，随后开放注册。"),
    "registration_full": (409, "注册人数已达到30人上限，暂不接受新账号。"),
    "already_initialized": (409, "管理者账号已初始化，不能重复创建。"),
    "username_taken": (409, "该用户名已注册，请更换用户名或登录。"),
    "invalid_username": (422, "用户名须为3至32位字母、数字或下划线，以字母开头。"),
    "invalid_password": (422, "密码长度须为15至128个字符，建议使用较长的密码短语。"),
    "invalid_credentials": (401, "账号或密码错误。"),
    "authentication_required": (401, "请登录后继续使用。"),
    "admin_required": (403, "此功能仅限管理者使用。"),
    "csrf_invalid": (403, "请求校验失败，请刷新页面后重试。"),
    "origin_forbidden": (403, "请求来源不被允许。"),
    "rate_limited": (429, "请求过于频繁，请稍后重试。"),
    "account_not_found": (404, "未找到该账号。"),
    "user_data_unavailable": (503, "当前账号的数据暂不可用，请联系管理者检查。"),
    "report_not_found": (404, "未找到该用户的报告。"),
    "registration_paused": (409, "管理者已暂停注册，请稍后再试。"),
    "admin_protected": (409, "管理者账号不能在后台停用或强制退出，请在账号设置管理自己的会话。"),
    "session_not_found": (404, "未找到当前账号的有效会话。"),
    "invalid_recovery_token": (400, "恢复凭证无效、已过期或已使用，请联系管理者重新申请。"),
}


class AuthError(ValueError):
    def __init__(self, code):
        self.code = code
        self.status, message = ERRORS[code]
        super().__init__(message)


def public_user(row):
    return {key: row[key] for key in ("id", "username", "role", "status")} | {
        "created_at": timestamp(row["created_at"]),
        "last_login_at": timestamp(row["last_login_at"]),
    }


def timestamp(value):
    return datetime.fromtimestamp(value, UTC).isoformat() if value is not None else None


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AuthService:
    def __init__(self, root: Path, *, clock=time.time):
        self.repository = AuthRepository(Path(root))
        self.clock = clock
        self.hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    def status(self):
        result = self.repository.status()
        return {
            **result,
            "limit": ACCOUNT_LIMIT,
            "remaining": max(0, ACCOUNT_LIMIT - result["registered"]),
            "registration_open": result["initialized"]
            and result["registration_enabled"]
            and result["registered"] < ACCOUNT_LIMIT,
            "authentication": "session",
        }

    @staticmethod
    def username(value):
        normalized = str(value).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", normalized):
            raise AuthError("invalid_username")
        return normalized

    @staticmethod
    def password(value):
        if not isinstance(value, str) or not 15 <= len(value) <= 128:
            raise AuthError("invalid_password")
        return value

    def _create(self, username, password, *, admin=False):
        username = self.username(username)
        hashed = self.hasher.hash(self.password(password))
        try:
            row = self.repository.create(uuid4().hex, username, hashed, self.clock(), admin=admin)
        except AccountConflict as error:
            raise AuthError(str(error)) from None
        return public_user(row)

    def bootstrap(self, username, password):
        return self._create(username, password, admin=True)

    def register(self, username, password):
        return self._create(username, password)

    def _verify(self, hashed, password):
        try:
            return self.hasher.verify(hashed, password)
        except (InvalidHashError, VerificationError):
            return False

    def login(self, username, password, *, device="未知设备"):
        normalized = str(username).strip().lower()
        row = self.repository.user(normalized)
        valid = self._verify(row["password_hash"] if row else self._dummy_hash, password)
        if not valid or not row or row["status"] != "active":
            raise AuthError("invalid_credentials")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = self.clock()
        expires = now + SESSION_SECONDS
        try:
            user = self.repository.create_session(
                normalized, row["password_hash"], token_digest(token), csrf, now, expires, device
            )
        except AccountConflict as error:
            raise AuthError(str(error)) from None
        return {
            "token": token,
            "user": public_user(user),
            "csrf_token": csrf,
            "expires_at": timestamp(expires),
        }

    def _session(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise AuthError("authentication_required")
        row = self.repository.session(token_digest(token), self.clock())
        if not row:
            raise AuthError("authentication_required")
        return row

    def session(self, token):
        row = self._session(token)
        return {
            "user": public_user(row),
            "csrf_token": row["csrf"],
            "expires_at": timestamp(row["expires_at"]),
        }

    def logout(self, token):
        self._session(token)
        self.repository.revoke(token_digest(token), self.clock())

    def sessions(self, token):
        row = self._session(token)
        items = self.repository.sessions(row["id"], token_digest(token), self.clock())
        return {
            "items": [
                {
                    **item,
                    "current": bool(item["current"]),
                    "created_at": timestamp(item["created_at"]),
                    "expires_at": timestamp(item["expires_at"]),
                }
                for item in items
            ]
        }

    def revoke_sessions(self, token, *, session_id=None):
        row = self._session(token)
        try:
            return self.repository.revoke_owned_sessions(
                row["id"], token_digest(token), self.clock(), session_id=session_id
            )
        except AccountConflict as error:
            raise AuthError(str(error)) from None

    def issue_recovery(self, actor_id, user_id):
        token = secrets.token_urlsafe(32)
        now = self.clock()
        expires = now + RECOVERY_SECONDS
        try:
            self.repository.issue_recovery(actor_id, user_id, token_digest(token), now, expires)
        except AccountConflict as error:
            raise AuthError(str(error)) from None
        return {"user_id": user_id, "token": token, "expires_at": timestamp(expires)}

    def redeem_recovery(self, username, token, password):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise AuthError("invalid_recovery_token")
        hashed = self.hasher.hash(self.password(password))
        try:
            self.repository.redeem_recovery(
                str(username).strip().lower(), token_digest(token), hashed, self.clock()
            )
        except AccountConflict as error:
            raise AuthError(str(error)) from None

    def change_password(self, token, current, password):
        row = self._session(token)
        if not self._verify(row["password_hash"], current):
            raise AuthError("invalid_credentials")
        new_hash = self.hasher.hash(self.password(password))
        try:
            self.repository.set_password(
                row["id"],
                new_hash,
                expected_hash=row["password_hash"],
                token_hash=token_digest(token),
                now=self.clock(),
            )
        except AccountConflict as error:
            raise AuthError(str(error)) from None

    def reset_password(self, username, password):
        row = self.repository.user(self.username(username))
        if not row:
            raise AuthError("account_not_found")
        self.repository.set_password(row["id"], self.hasher.hash(self.password(password)))
