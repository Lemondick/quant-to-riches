"""Authenticated reports, private model discovery, and system schedule endpoints."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from quant_assistant.services.advisor_jev import JevInput
from quant_assistant.services.advisor_model_settings import SaveAdvisorModels
from quant_assistant.services.advisor_provider import ProviderDiscoveryError, ProviderInput


class CreateAdvisorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["morning", "afternoon"]
    force: bool = False


def build_advisor_router(service):
    router = APIRouter(prefix="/api/v1/advisor")

    @router.get("/model-settings")
    def model_settings():
        return service.model_settings(recover=True)

    @router.put("/model-settings")
    def save_models(body: SaveAdvisorModels):
        return service.save_model_settings(body)

    @router.post("/models/discover")
    def discover_models(body: ProviderInput):
        try:
            return service.discover_models(body)
        except ProviderDiscoveryError as error:
            return JSONResponse(
                status_code=422,
                content={"error_code": "model_discovery_failed", "message": str(error)},
            )

    @router.put("/jev-connection")
    def jev_connection(body: JevInput):
        try:
            return service.save_jev_connection(body)
        except ProviderDiscoveryError as error:
            return JSONResponse(
                status_code=422,
                content={"error_code": "jev_configuration_failed", "message": str(error)},
            )

    @router.get("/schedule")
    def schedule():
        return service.scheduler.status()

    @router.get("/notifications")
    def notifications():
        return service.scheduler.notifications()

    @router.post("/notifications/{notice_id}/acknowledge")
    def acknowledge(notice_id: str):
        return service.scheduler.acknowledge(notice_id)

    @router.get("/reports")
    def reports(
        kind: Literal["morning", "afternoon"] | None = None,
        report_date: date | None = None,
        page: int = Query(default=1, ge=1, le=100000),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        return service.list(limit, kind=kind, report_date=report_date, page=page)

    @router.get("/reports/{report_id}")
    def report(report_id: str):
        return service.get(report_id)

    @router.post("/reports", status_code=202)
    def create(body: CreateAdvisorReport, background: BackgroundTasks):
        result = service.create(body.kind, force=body.force)
        if result["status"] == "queued":
            background.add_task(service.execute, result["report_id"])
        return result

    return router
