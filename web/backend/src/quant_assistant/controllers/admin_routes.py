"""Administrator account operations and read-only financial and usage details."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict

from quant_assistant.controllers.auth_routes import require_session
from quant_assistant.models.repositories.auth import AccountConflict
from quant_assistant.services.admin_user_details import AdminUserDetails
from quant_assistant.services.admin_user_timeline import AdminUserTimeline
from quant_assistant.services.auth import AuthError, timestamp


class UserStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"]


class RegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class CalendarBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    source: str
    holidays: list[str]


def build_admin_router(auth, contexts):
    def administrator(request: Request):
        session = require_session(
            request, auth, write=request.method not in {"GET", "HEAD", "OPTIONS"}
        )
        if session["user"]["role"] != "admin":
            raise AuthError("admin_required")
        return session["user"]

    router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(administrator)])
    service = AdminUserDetails(contexts)
    timeline = AdminUserTimeline(contexts, auth)

    def account(user_id):
        user = auth.repository.by_id(user_id)
        if not user:
            raise AuthError("account_not_found")
        return user

    def operate(request, action, target_id, value):
        actor = administrator(request)
        try:
            auth.repository.administer(actor["id"], action, target_id, value, auth.clock())
        except AccountConflict as error:
            raise AuthError(str(error)) from None
        return {"status": "updated"}

    def access(request, action, user_id, category, access_range):
        actor = administrator(request)
        try:
            auth.repository.audit_access(
                actor["id"], action, user_id, category, access_range, auth.clock()
            )
        except AccountConflict as error:
            raise AuthError(str(error)) from None

    def read_range(symbol, start, end, **pagination):
        return {
            "symbol": symbol,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            **pagination,
        }

    @router.post("/users/{user_id}/recovery-token", status_code=201)
    def recovery_token(user_id: str, request: Request):
        return auth.issue_recovery(administrator(request)["id"], user_id)

    @router.get("/users/{user_id}/timeline")
    def events(
        user_id: str,
        request: Request,
        category: Literal[
            "all",
            "business",
            "account",
            "imports",
            "trades",
            "nav",
            "reports",
            "tasks",
            "portfolio",
            "data",
        ] = "all",
        page: int = Query(1, ge=1, le=100000),
        limit: int = Query(50, ge=1, le=100),
        start: date | None = None,
        end: date | None = None,
    ):
        if start and end and start > end:
            from fastapi import HTTPException

            raise HTTPException(422, "开始日期不能晚于结束日期")
        result = timeline.events(
            account(user_id), category=category, page=page, limit=limit, start=start, end=end
        )
        access(
            request,
            "timeline_read",
            user_id,
            category,
            read_range("", start, end, page=page, limit=limit),
        )
        return result

    @router.post("/users/{user_id}/status")
    def status(user_id: str, body: UserStatusBody, request: Request):
        result = operate(request, "status", user_id, body.status)
        return result

    @router.post("/users/{user_id}/revoke-sessions")
    def revoke(user_id: str, request: Request):
        return operate(request, "revoke_sessions", user_id, None)

    @router.post("/registration")
    def registration(body: RegistrationBody, request: Request):
        operate(request, "registration", None, body.enabled)
        return auth.status()

    @router.put("/calendar")
    def calendar(body: CalendarBody, request: Request):
        from fastapi import HTTPException

        from quant_assistant.services.trading_calendar import TradingCalendar

        try:
            result = TradingCalendar(contexts.root / "calendars").save(body.model_dump())
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        operate(
            request,
            "calendar",
            None,
            {"year": body.year, "source": body.source, "holiday_count": len(result["holidays"])},
        )
        return result

    @router.get("/audit")
    def audit(page: int = Query(1, ge=1, le=100000), limit: int = Query(50, ge=1, le=100)):
        result = auth.repository.audit(page, limit)
        for item in result["items"]:
            item["created_at"] = timestamp(item["created_at"])
        return result

    @router.get("/users/{user_id}/usage")
    def usage(user_id: str):
        user = account(user_id)
        result = auth.repository.usage(user["id"], auth.clock())
        result["last_activity_at"] = timestamp(result["last_activity_at"])
        return {**result, **service.task_statistics(user)}

    @router.get("/users/{user_id}/export")
    def export_details(
        user_id: str,
        request: Request,
        section: Literal["holdings", "ledger", "watchlist", "reports", "tasks"] = "holdings",
        symbol: str = Query("", max_length=32),
        start: date | None = None,
        end: date | None = None,
    ):
        from fastapi import HTTPException

        user = account(user_id)
        if start and end and start > end:
            raise HTTPException(422, "开始日期不能晚于结束日期")
        with contexts.read_database(user):
            result = service.details(
                user, section=section, page=1, limit=100, symbol=symbol, start=start, end=end
            )
            if result["total"] > 100000:
                raise HTTPException(413, "单次导出最多10万条，请缩小日期范围")
            items = list(result["items"])
            for page in range(2, (result["total"] + 99) // 100 + 1):
                items.extend(
                    service.details(
                        user,
                        section=section,
                        page=page,
                        limit=100,
                        symbol=symbol,
                        start=start,
                        end=end,
                    )["items"]
                )
        access(request, "details_export", user_id, section, read_range(symbol, start, end))
        return {"user_id": user_id, "items": items, "total": len(items)}

    @router.get("/users/{user_id}/details")
    def details(
        user_id: str,
        request: Request,
        section: Literal["holdings", "ledger", "watchlist", "reports", "tasks"] = "holdings",
        page: int = Query(1, ge=1, le=100000),
        limit: int = Query(50, ge=1, le=100),
        symbol: str = Query("", max_length=32),
        start: date | None = None,
        end: date | None = None,
    ):
        if start and end and start > end:
            from fastapi import HTTPException

            raise HTTPException(422, "开始日期不能晚于结束日期")
        result = service.details(
            account(user_id),
            section=section,
            page=page,
            limit=limit,
            symbol=symbol,
            start=start,
            end=end,
        )
        access(
            request,
            "details_read",
            user_id,
            section,
            read_range(symbol, start, end, page=page, limit=limit),
        )
        return result

    @router.get("/users/{user_id}/reports/{source}/{report_id}")
    def report(
        user_id: str,
        source: Literal["advisor", "research"],
        request: Request,
        report_id: str = Path(min_length=1, max_length=128),
        attempt_no: int = Query(1, ge=1, le=100000),
    ):
        result = service.report(
            account(user_id), source=source, report_id=report_id, attempt_no=attempt_no
        )
        access(
            request,
            "report_read",
            user_id,
            "reports",
            {"source": source, "report_id": report_id, "attempt_no": attempt_no},
        )
        return result

    return router
