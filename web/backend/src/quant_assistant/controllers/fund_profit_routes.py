"""Read-only research views and an explicit, bounded public-data refresh."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field


class RefreshFundResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(min_length=1, max_length=6)


def build_fund_profit_router(tracking, risk, store):
    router = APIRouter(prefix="/api/v1/research/funds")

    @router.get("/advisor-tracking")
    def advisor_tracking(limit: int = Query(30, ge=1, le=50), page: int = Query(1, ge=1, le=10000)):
        return tracking.read(limit, page)

    @router.get("/portfolio-risk")
    def portfolio_risk():
        return risk.read()

    @router.post("/public-observations")
    def refresh(body: RefreshFundResearch):
        return store.refresh(body.symbols)

    return router
