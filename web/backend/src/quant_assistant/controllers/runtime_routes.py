"""Authenticated runtime telemetry and isolated user cost/alert controls."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from quant_assistant.controllers.auth_routes import require_session
from quant_assistant.models.repositories.auth import AccountConflict
from quant_assistant.services.alert_center import AlertCenter, AlertSettings
from quant_assistant.services.auth import AuthError
from quant_assistant.services.model_costs import CostSettings


def build_user_runtime_router(services):
    router = APIRouter(prefix="/api/v1")
    costs = services.api_queries.market_advisor.costs
    alerts = AlertCenter(services.database)

    @router.get("/runtime/costs")
    def read_costs():
        return costs.read()

    @router.put("/runtime/costs")
    def save_costs(body: CostSettings):
        try:
            return costs.save(body)
        except ValueError as error:
            return JSONResponse(
                {"error_code": "cost_settings_conflict", "message": str(error)}, status_code=409
            )

    @router.get("/alerts")
    def read_alerts():
        return alerts.read()

    @router.get("/alerts/settings")
    def read_settings():
        return alerts.settings()

    @router.put("/alerts/settings")
    def save_settings(body: AlertSettings):
        try:
            return alerts.save(body)
        except ValueError as error:
            return JSONResponse(
                {"error_code": "alert_settings_conflict", "message": str(error)}, status_code=409
            )

    @router.post("/alerts/{alert_id}/acknowledge")
    def acknowledge(alert_id: str):
        try:
            return alerts.acknowledge(alert_id)
        except KeyError:
            raise HTTPException(404, "未找到当前账户告警") from None

    return router


def build_runtime_router(auth, contexts):
    def administrator(request: Request):
        session = require_session(request, auth)
        if session["user"]["role"] != "admin":
            raise AuthError("admin_required")
        return session["user"]

    router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(administrator)])

    @router.get("/runtime")
    def runtime(request: Request):
        from quant_assistant.services.site_backup_evidence import verify_site_archive

        actor = administrator(request)
        users = []
        for user in auth.repository.users():
            try:
                auth.repository.audit_access(
                    actor["id"],
                    "runtime_view",
                    user["id"],
                    "runtime_tasks",
                    {"limit": 10},
                    auth.clock(),
                )
            except AccountConflict as error:
                raise AuthError(str(error)) from None
            summary = contexts.summary(user)
            item = {
                "user_id": user["id"],
                "username": user["username"],
                "status": user["status"],
                "data_status": summary["data_status"],
                "tasks": [],
                "failure_count": None,
                "workers": None,
            }
            try:
                with contexts.read_database(user) as conn:
                    if conn is not None:
                        reports = conn.execute(
                            "SELECT report_id,status,error_code,updated_at FROM "
                            "market_advisor_reports ORDER BY updated_at DESC LIMIT 10"
                        ).fetchall()
                        research = conn.execute(
                            "SELECT job_id,status,error_code,created_at FROM agent_research_jobs "
                            "ORDER BY created_at DESC LIMIT 10"
                        ).fetchall()
                        item["tasks"] = [
                            {
                                "source": source,
                                "id": r[0],
                                "status": r[1],
                                "error_code": r[2],
                                "updated_at": r[3],
                            }
                            for source, rows in (("advisor", reports), ("research", research))
                            for r in rows
                        ]
                        item["failure_count"] = conn.execute(
                            "SELECT (SELECT count(*) FROM market_advisor_reports WHERE "
                            "status='failed') + (SELECT count(*) FROM agent_research_jobs WHERE "
                            "status IN ('failed','timed_out','interrupted'))"
                        ).fetchone()[0]
            except AuthError:
                item["data_status"] = "unavailable"
            with contexts._lock:
                context = contexts._contexts.get(user["id"])
            if context is not None:
                queries = context.services.api_queries
                item["workers"] = {
                    "advisor": bool(
                        queries.market_advisor.scheduler.thread
                        and queries.market_advisor.scheduler.thread.is_alive()
                    ),
                    "research": bool(
                        queries.agent_research.thread and queries.agent_research.thread.is_alive()
                    ),
                }
                if item["data_status"] == "ready":
                    item["costs"] = queries.market_advisor.costs.read()["today"]
            users.append(item)
        archives = sorted(
            (contexts.root / "backups").glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:10]
        backups = []
        for path in archives:
            result = verify_site_archive(path, site_root=contexts.root)
            backups.append(
                {**result, "modified_at": path.stat().st_mtime, "size_bytes": path.stat().st_size}
            )
        return {
            "accounts": users,
            "global_model_gate": contexts.job_gate.status(),
            "site_backups": backups,
            "site_lock_held": contexts.site_lock is not None,
            "business_ready": all(
                x["data_status"] != "unavailable" for x in users if x["status"] == "active"
            )
            and not contexts.job_gate.stopping,
            "automatic_trading": False,
        }

    return router
