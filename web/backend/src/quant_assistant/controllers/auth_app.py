"""Authenticated HTTP entry point and dispatch to the session owner's business app."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import duckdb
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware

from quant_assistant.controllers.admin_routes import build_admin_router
from quant_assistant.controllers.auth_routes import build_auth_router
from quant_assistant.controllers.runtime_routes import build_runtime_router
from quant_assistant.controllers.security import (
    ApiSecuritySettings,
    SlidingWindowRateLimiter,
    request_client_key,
)
from quant_assistant.services.auth import COOKIE_NAME, AuthError, AuthService
from quant_assistant.services.user_context import UserContexts

PUBLIC_AUTH = frozenset(
    {
        "/api/v1/auth/status",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/recovery/redeem",
    }
)


def failure(code, message, status, request_id):
    return JSONResponse(
        {"error_code": code, "message": message, "request_id": request_id}, status_code=status
    )


class UserGateway:
    def __init__(self, app, *, auth, contexts, origins, public_web_assets, rate_limit):
        self.app, self.auth, self.contexts = app, auth, contexts
        self.origins, self.public_web_assets = origins, public_web_assets
        self.login_limiter = SlidingWindowRateLimiter(20)
        self.api_limiter = SlidingWindowRateLimiter(rate_limit)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        request_id = uuid4().hex
        started = time.monotonic()
        response_status = 500

        async def secured_send(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = headers.get("X-Request-ID", request_id)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cache-Control"] = "no-store"
                session = scope.get("state", {}).get("session")
                if session:
                    headers["X-Quant-User-ID"] = session["user"]["id"]
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body")
                and business
            ):
                session = scope.get("state", {}).get("session")
                if session:
                    try:
                        category = path.split("/")[3] if path.startswith("/api/v1/") else "system"
                        await run_in_threadpool(
                            self.auth.repository.record_usage,
                            session["user"]["id"],
                            category,
                            response_status,
                            int((time.monotonic() - started) * 1000),
                            self.auth.clock(),
                        )
                    except sqlite3.Error:
                        pass  # Telemetry failure cannot turn a committed trade into an HTTP error.
            await send(message)

        path = request.url.path
        business = (
            path.startswith("/api/")
            and not path.startswith(("/api/v1/auth/", "/api/v1/admin/"))
            or path in {"/openapi.json", "/metrics"}
        )
        protected = (
            business
            or path.startswith("/api/v1/admin/")
            or (path.startswith("/api/v1/auth/") and path not in PUBLIC_AUTH)
        )
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin", "")
                own_origin = f"{request.url.scheme}://{request.url.netloc}"
                if not origin or origin not in {*self.origins, own_origin}:
                    raise AuthError("origin_forbidden")
            if (
                path in PUBLIC_AUTH
                and request.method == "POST"
                and not self.login_limiter.allow(request_client_key(request) + path)
            ):
                raise AuthError("rate_limited")
            session = None
            if protected:
                session = await run_in_threadpool(
                    self.auth.session, request.cookies.get(COOKIE_NAME)
                )
                scope.setdefault("state", {})["session"] = session
                if not self.api_limiter.allow(session["user"]["id"]):
                    raise AuthError("rate_limited")
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(
                    request.headers.get("x-csrf-token", "").encode("utf-8"),
                    session["csrf_token"].encode("ascii"),
                ):
                    raise AuthError("csrf_invalid")
            if business:
                try:
                    context = await run_in_threadpool(self.contexts.get, session["user"])
                except (duckdb.Error, OSError, ValueError, RuntimeError):
                    raise AuthError("user_data_unavailable") from None
                return await context.app(scope, receive, secured_send)
        except AuthError as error:
            response = failure(error.code, str(error), error.status, request_id)
            if error.status == 429:
                response.headers["Retry-After"] = "60"
            return await response(scope, receive, secured_send)
        except sqlite3.Error:
            return await failure(
                "authentication_unavailable", "账号服务暂不可用，请稍后重试。", 503, request_id
            )(scope, receive, secured_send)
        await self.app(scope, receive, secured_send)


def create_authenticated_app(settings, *, public_web_assets=False):
    origins = ApiSecuritySettings(
        allowed_origins=settings.deployment.allowed_origins
    ).allowed_origins
    auth = AuthService(settings.data_dir)
    contexts = UserContexts(settings, auth)

    @asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(contexts.start)
        try:
            yield
        finally:
            await run_in_threadpool(contexts.close)

    app = FastAPI(
        title="量化宝 API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.auth, app.state.user_contexts = auth, contexts
    instance_id = hashlib.sha256(str(settings.database_path.resolve()).encode()).hexdigest()[:16]
    secure_cookie = settings.deployment.host.lower() not in {"127.0.0.1", "localhost", "::1"}
    app.include_router(
        build_auth_router(auth, contexts, secure_cookie=secure_cookie, instance_id=instance_id)
    )
    app.include_router(build_admin_router(auth, contexts))
    app.include_router(build_runtime_router(auth, contexts))

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def ready():
        try:
            auth.status()
            if not contexts.business_readiness():
                return JSONResponse({"status": "unavailable"}, status_code=503)
            return {"status": "ok", "authentication": "session"}
        except sqlite3.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, error: AuthError):
        return failure(error.code, str(error), error.status, uuid4().hex)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        return failure("invalid_request", "请求参数无效。", 422, uuid4().hex)

    @app.exception_handler(sqlite3.Error)
    async def unavailable(request: Request, error: sqlite3.Error):
        return failure(
            "authentication_unavailable", "账号服务暂不可用，请稍后重试。", 503, uuid4().hex
        )

    app.add_middleware(
        UserGateway,
        auth=auth,
        contexts=contexts,
        origins=origins,
        rate_limit=settings.deployment.rate_limit_per_minute,
        public_web_assets=public_web_assets,
    )
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
            expose_headers=["X-Request-ID", "X-Quant-User-ID"],
        )
    return app
