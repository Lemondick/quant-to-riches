"""Account and read-only administrator APIs; privileges never come from request bodies."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from quant_assistant.services.auth import (
    COOKIE_NAME,
    SESSION_SECONDS,
    AuthError,
    public_user,
    timestamp,
)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=32)
    password: SecretStr = Field(min_length=1, max_length=128)


class NewPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=15, max_length=128)


class RecoveryPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=32)
    token: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=15, max_length=128)


def device_label(user_agent):
    """Store a coarse device label instead of an arbitrary, potentially private header."""
    browser = next(
        (
            name
            for marker, name in (
                ("Edg/", "Edge"),
                ("Firefox/", "Firefox"),
                ("Chrome/", "Chrome"),
                ("Safari/", "Safari"),
            )
            if marker in user_agent
        ),
        "浏览器",
    )
    system = next(
        (
            name
            for marker, name in (
                ("Android", "Android"),
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Windows", "Windows"),
                ("Macintosh", "macOS"),
                ("Linux", "Linux"),
            )
            if marker in user_agent
        ),
        "未知系统",
    )
    return f"{browser} · {system}"


def require_session(request, auth, *, write=False):
    session = auth.session(request.cookies.get(COOKIE_NAME))
    if write and not hmac.compare_digest(
        request.headers.get("x-csrf-token", "").encode("utf-8"),
        session["csrf_token"].encode("ascii"),
    ):
        raise AuthError("csrf_invalid")
    return session


def build_auth_router(auth, contexts, *, secure_cookie=False, instance_id=""):
    router = APIRouter(prefix="/api/v1")

    @router.get("/auth/status")
    def status():
        return {**auth.status(), "instance_id": instance_id}

    @router.post("/auth/register", status_code=201)
    def register(body: Credentials):
        return {"user": auth.register(body.username, body.password.get_secret_value())}

    @router.post("/auth/login")
    def login(body: Credentials, response: Response, request: Request):
        result = auth.login(
            body.username,
            body.password.get_secret_value(),
            device=device_label(request.headers.get("user-agent", "")),
        )
        response.set_cookie(
            COOKIE_NAME,
            result.pop("token"),
            max_age=SESSION_SECONDS,
            secure=secure_cookie,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return result

    @router.get("/auth/session")
    def session(request: Request):
        return require_session(request, auth)

    @router.get("/auth/sessions")
    def sessions(request: Request):
        require_session(request, auth)
        return auth.sessions(request.cookies.get(COOKIE_NAME))

    @router.post("/auth/sessions/revoke-others")
    def revoke_others(request: Request):
        require_session(request, auth, write=True)
        return auth.revoke_sessions(request.cookies.get(COOKIE_NAME))

    @router.post("/auth/sessions/{session_id}/revoke")
    def revoke_session(session_id: str, request: Request, response: Response):
        require_session(request, auth, write=True)
        result = auth.revoke_sessions(request.cookies.get(COOKIE_NAME), session_id=session_id)
        if result["current_revoked"]:
            response.delete_cookie(
                COOKIE_NAME, path="/", secure=secure_cookie, httponly=True, samesite="lax"
            )
        return result

    @router.post("/auth/recovery/redeem")
    def redeem_recovery(body: RecoveryPassword, response: Response):
        auth.redeem_recovery(
            body.username, body.token.get_secret_value(), body.new_password.get_secret_value()
        )
        response.delete_cookie(
            COOKIE_NAME, path="/", secure=secure_cookie, httponly=True, samesite="lax"
        )
        return {"status": "password_recovered"}

    @router.post("/auth/logout")
    def logout(request: Request, response: Response):
        require_session(request, auth, write=True)
        auth.logout(request.cookies.get(COOKIE_NAME))
        response.delete_cookie(
            COOKIE_NAME, path="/", secure=secure_cookie, httponly=True, samesite="lax"
        )
        return {"status": "logged_out"}

    @router.post("/auth/password")
    def password(body: NewPassword, request: Request, response: Response):
        require_session(request, auth, write=True)
        auth.change_password(
            request.cookies.get(COOKIE_NAME),
            body.current_password.get_secret_value(),
            body.new_password.get_secret_value(),
        )
        response.delete_cookie(
            COOKIE_NAME, path="/", secure=secure_cookie, httponly=True, samesite="lax"
        )
        return {"status": "password_changed"}

    @router.get("/admin/users")
    def users(request: Request, search: str = Query(default="", max_length=32)):
        session = require_session(request, auth)
        if session["user"]["role"] != "admin":
            raise AuthError("admin_required")
        selected = search.strip().lower()
        items = []
        for row in auth.repository.users():
            if selected and selected not in row["username"]:
                continue
            usage = auth.repository.usage(row["id"], auth.clock())
            items.append(
                {
                    **public_user(row),
                    **contexts.summary(row),
                    "last_activity_at": timestamp(usage["last_activity_at"]),
                    "requests_7d": usage["requests_7d"],
                    "errors_7d": usage["errors_7d"],
                }
            )
        return {**auth.status(), "items": items}

    return router
