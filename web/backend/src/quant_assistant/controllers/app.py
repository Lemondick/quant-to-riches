"""FastAPI application factory with fixed, non-sensitive error responses."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from quant_assistant.analysis.research_models import ResearchError
from quant_assistant.controllers.advisor_routes import build_advisor_router
from quant_assistant.controllers.fund_lot_routes import build_fund_lot_router
from quant_assistant.controllers.fund_profit_routes import build_fund_profit_router
from quant_assistant.controllers.fund_study_routes import build_fund_study_router
from quant_assistant.controllers.research_routes import build_research_router
from quant_assistant.controllers.routes import build_router
from quant_assistant.controllers.schemas import ErrorResponse
from quant_assistant.controllers.security import (
    ApiSecuritySettings,
    MetricsCollector,
    SlidingWindowRateLimiter,
    request_client_key,
    valid_bearer,
)
from quant_assistant.funds.fees import UnverifiedFeeSchedule
from quant_assistant.reporting.queries import DatabaseUnavailableError
from quant_assistant.services.advisor_model_settings import AdvisorSettingsConflict
from quant_assistant.services.api_queries import DecisionRunNotFoundError
from quant_assistant.services.commands_common import LedgerStateError
from quant_assistant.services.market_advisor import AdvisorBusyError
from quant_assistant.services.portfolio_commands import (
    DividendNotFoundError,
    TransactionNotFoundError,
)

if TYPE_CHECKING:
    from quant_assistant.analysis.service import AnalysisService
    from quant_assistant.services.api_queries import ApiQueries

ERROR_MESSAGES = {
    "decision_run_not_found": "未找到指定的决策运行。",
    "transaction_not_found": "未找到要冲正的成交记录。",
    "dividend_not_found": "未找到要冲正的分红记录。",
    "database_unavailable": "本地数据库暂不可用。",
    "invalid_request": "请求参数无效。",
    "missing_cash_position": "尚未确认账户现金，请先导入完整账户并核对可用现金。账户未改动。",
    "insufficient_cash": "可用现金不足以支付成交金额和费用，请核对现金余额或成交记录。账户未改动。",
    "insufficient_position": "卖出份额超过已确认持仓，请核对持仓或成交记录。账户未改动。",
    "nav_baseline_pending": (
        "账户快照已保存，但尚未完成该日期的净值入账。请先核对净值基线，再补录后续成交。账户未改动。"
    ),
    "route_not_found": "未找到请求的接口。",
    "method_not_allowed": "该接口不允许此请求方法。",
    "authentication_required": "需要有效的访问凭据。",
    "rate_limited": "请求过于频繁，请稍后重试。",
    "internal_error": "服务器内部错误。",
    "fund_lots_unconfirmed": "请先按确认单核对所有剩余批次，并明确确认。持仓未改动。",
    "fund_lots_stale": "持仓或批次已变化，请刷新并重新核对剩余批次。持仓未改动。",
    "fund_lots_mismatch": "批次剩余份额合计与已确认持仓不一致，请核对完整确认单。持仓未改动。",
    "fund_lots_selection_invalid": "选定的份额超过该批次余额，或重复选择同一批次。没有提交赎回。",
    "fee_evidence_unavailable": "缺少在估算日期有效且30日内核对的实际费率，请先核对费用证据。",
    "advisor_report_active": "已有AI报告正在执行，请等待完成后再创建新任务。没有发起新的模型调用。",
    "advisor_settings_changed": "模型配置已由其他页面修改，请重新读取后再保存。",
    "ledger_correction_cost_missing": (
        "旧卖出记录缺少成交前成本，无法可靠冲正。请使用完整账户对账校准；账户未改动。"
    ),
    "ledger_correction_date_invalid": "冲正日期不能早于原记录或当前账本日期。账户未改动。",
    "ledger_correction_dependent_events": (
        "原成交之后已有卖出、份额变化或账户校准，无法可靠直接冲正。"
        "请先核对关联记录或使用完整账户对账校准；账户未改动。"
    ),
}


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid4().hex


def _error_response(request: Request, status_code: int, error_code: str) -> JSONResponse:
    payload = ErrorResponse(
        error_code=error_code,
        message=ERROR_MESSAGES[error_code],
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _database_unavailable(error: BaseException) -> bool:
    if isinstance(error, (ConnectionError, DatabaseUnavailableError)):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_database_unavailable(item) for item in error.exceptions)
    return False


def create_app(
    queries: ApiQueries,
    analysis_service: AnalysisService,
    *,
    security: ApiSecuritySettings | None = None,
    public_web_assets: bool = False,
) -> FastAPI:
    """Build the internal business API behind the authenticated user gateway.

    The gateway owns account sessions. This app retains per-context request
    limits and metrics; network entry points must use create_authenticated_app.
    """

    research = getattr(queries, "agent_research", None)
    advisor = getattr(queries, "market_advisor", None)
    scheduler = getattr(advisor, "scheduler", None)

    @asynccontextmanager
    async def lifespan(_app):
        if research is not None:
            research.start()
        if scheduler is not None:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()
            if research is not None:
                research.stop()

    app = FastAPI(
        title="Quant Assistant Local API",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    effective_security = security or ApiSecuritySettings(require_auth=False)
    metrics = MetricsCollector()
    limiter = SlidingWindowRateLimiter(effective_security.rate_limit_per_minute)
    allowed_origins = effective_security.allowed_origins
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    @app.middleware("http")
    async def add_request_id_and_security(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = uuid4().hex
        started = perf_counter()
        public = request.url.path in effective_security.public_paths
        web_asset = (
            public_web_assets
            and request.method in {"GET", "HEAD"}
            and (
                request.url.path in {"/", "/index.html"}
                or request.url.path.startswith(("/assets/", "/static/"))
            )
        )
        public = public or web_asset
        cors_preflight = (
            request.method == "OPTIONS"
            and request.headers.get("origin", "").rstrip("/") in allowed_origins
            and bool(request.headers.get("access-control-request-method"))
        )
        if not public and not cors_preflight and not limiter.allow(request_client_key(request)):
            response = _error_response(request, 429, "rate_limited")
            response.headers["Retry-After"] = "60"
        elif (
            effective_security.require_auth
            and not public
            and not cors_preflight
            and not valid_bearer(request, effective_security.auth_token)
        ):
            response = _error_response(request, 401, "authentication_required")
            response.headers["WWW-Authenticate"] = "Bearer"
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if web_asset and request.url.path.startswith("/assets/") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        metrics.observe(request.url.path, response.status_code, perf_counter() - started)
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        try:
            health = queries.health()
            available = (
                getattr(health, "status", None) == "ok"
                and getattr(health, "database", None) == "connected"
            )
            payload = {
                "status": "ok" if available else "degraded",
                "version": str(getattr(health, "version", "unknown")),
            }
        except Exception:
            payload = {"status": "degraded", "version": "unknown"}
            available = False
        return JSONResponse(status_code=200 if available else 503, content=payload)

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        try:
            health = queries.health()
            available = (
                getattr(health, "status", None) == "ok"
                and getattr(health, "database", None) == "connected"
            )
        except Exception:
            available = False
        return JSONResponse(
            status_code=200 if available else 503,
            content={"status": "ready" if available else "not_ready"},
        )

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(metrics.render(), media_type="text/plain; version=0.0.4")

    @app.exception_handler(RequestValidationError)
    async def invalid_request_handler(
        request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _error_response(request, 422, "invalid_request")

    @app.exception_handler(DecisionRunNotFoundError)
    async def decision_not_found_handler(
        request: Request, _error: DecisionRunNotFoundError
    ) -> JSONResponse:
        return _error_response(request, 404, "decision_run_not_found")

    @app.exception_handler(TransactionNotFoundError)
    async def transaction_not_found_handler(
        request: Request, _error: TransactionNotFoundError
    ) -> JSONResponse:
        return _error_response(request, 404, "transaction_not_found")

    @app.exception_handler(DividendNotFoundError)
    async def dividend_not_found_handler(
        request: Request, _error: DividendNotFoundError
    ) -> JSONResponse:
        return _error_response(request, 404, "dividend_not_found")

    @app.exception_handler(LedgerStateError)
    async def ledger_state_handler(request: Request, error: LedgerStateError) -> JSONResponse:
        code = error.code if error.code in ERROR_MESSAGES else "invalid_request"
        return _error_response(request, 422, code)

    @app.exception_handler(UnverifiedFeeSchedule)
    async def fee_evidence_handler(request: Request, _error: UnverifiedFeeSchedule):
        return _error_response(request, 422, "fee_evidence_unavailable")

    @app.exception_handler(AdvisorBusyError)
    async def advisor_busy_handler(request: Request, _error: AdvisorBusyError):
        return _error_response(request, 409, "advisor_report_active")

    @app.exception_handler(AdvisorSettingsConflict)
    async def advisor_settings_handler(request: Request, _error: AdvisorSettingsConflict):
        return _error_response(request, 409, "advisor_settings_changed")

    @app.exception_handler(ValueError)
    @app.exception_handler(TypeError)
    async def normalized_input_handler(
        request: Request, _error: ValueError | TypeError
    ) -> JSONResponse:
        return _error_response(request, 422, "invalid_request")

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, error: StarletteHTTPException) -> JSONResponse:
        if error.status_code == 404:
            return _error_response(request, 404, "route_not_found")
        if error.status_code == 405:
            return _error_response(request, 405, "method_not_allowed")
        if error.status_code == 422:
            return _error_response(request, 422, "invalid_request")
        return _error_response(request, 500, "internal_error")

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
        if _database_unavailable(error):
            return _error_response(request, 503, "database_unavailable")
        return _error_response(request, 500, "internal_error")

    @app.exception_handler(ResearchError)
    async def research_error_handler(request: Request, error: ResearchError):
        return JSONResponse(
            status_code=error.status,
            content={
                "error_code": error.code,
                "message": str(error),
                "request_id": _request_id(request),
            },
        )

    app.include_router(build_router(queries, analysis_service))
    if getattr(getattr(queries, "_queries", None), "database", None) is not None:
        from quant_assistant.controllers.history_routes import build_history_router

        app.include_router(build_history_router(queries))
        from quant_assistant.controllers.ledger_review_routes import build_ledger_review_router

        app.include_router(build_ledger_review_router(queries._queries.database))
    study = getattr(queries, "fund_scenario_study", None)
    if study is not None:
        app.include_router(build_fund_study_router(study))
    tracking = getattr(queries, "advisor_tracking", None)
    if tracking is not None:
        app.include_router(
            build_fund_profit_router(
                tracking, queries.fund_portfolio_risk, queries.fund_research_store
            )
        )
    fund_lots = getattr(queries, "fund_lots", None)
    if fund_lots is not None:
        app.include_router(build_fund_lot_router(fund_lots))
    advisor = getattr(queries, "market_advisor", None)
    if advisor is not None:
        app.include_router(build_advisor_router(advisor))
    if research is not None:
        app.include_router(build_research_router(research))
    return app
