"""Authenticated research endpoints; bodies contain identifiers only."""

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field


class CreateResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_snapshot_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")


class RetryResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_snapshot_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def build_research_router(service):
    router = APIRouter(prefix="/api/v1/research/agent-runs")

    @router.post("")
    def create(body: CreateResearch, response: Response):
        result, reused = service.create(body.preview_snapshot_id, body.symbol)
        response.status_code = 200 if reused else 202
        return result

    @router.get("/{job_id}")
    def get(job_id: str):
        return service.get(job_id)

    @router.post("/{job_id}/cancel")
    def cancel(job_id: str):
        return service.cancel(job_id)

    @router.post("/{job_id}/retry")
    def retry(job_id: str, body: RetryResearch, response: Response):
        result, reused = service.retry(job_id, body.preview_snapshot_id)
        response.status_code = 200 if reused else 202
        return result

    return router
