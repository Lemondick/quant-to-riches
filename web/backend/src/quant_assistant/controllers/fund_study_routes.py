"""Research-only fund experiments; public updates never call models or trade."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query


def build_fund_study_router(service):
    router = APIRouter(prefix="/api/v1/research/funds")

    @router.get("/cost-study")
    def study():
        return service.read()

    @router.get("/cost-study/curves")
    def curves(
        symbol: Annotated[str, Query(pattern=r"^[0-9]{6}$")],
        period: Literal["full", "early", "late"],
        purchase_rate: Literal["0", "0.0012", "0.012"],
        version: Annotated[str, Query(min_length=1, max_length=64)],
    ):
        return service.curves(symbol, period, purchase_rate, version)

    @router.get("/cost-study/update")
    def update_status():
        return service.update_status()

    @router.post("/cost-study/update", status_code=202)
    def update():
        return service.start_update()

    return router
