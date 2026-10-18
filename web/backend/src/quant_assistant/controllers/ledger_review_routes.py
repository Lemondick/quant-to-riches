"""Account-scoped reconciliation and explicitly confirmed historical replay."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import Action, AssetType
from quant_assistant.services.ledger_replay import LedgerReplayService
from quant_assistant.services.portfolio_commands import TransactionInput
from quant_assistant.services.reconciliation import ReconciliationService


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompareBody(ReviewBody):
    account_ids: list[str] = Field(min_length=1, max_length=30)
    complete_scope: bool = False
    expected_fees: Decimal | None = Field(default=None, ge=0)
    start: date | None = None
    end: date | None = None
    evidence: str = Field(default="", max_length=500)


class ResolutionBody(ReviewBody):
    resolution: Literal["explained", "follow_up", "matched"]
    note: str = Field(min_length=1, max_length=1000)


class HistoricalTradeBody(ReviewBody):
    symbol: str = Field(min_length=1, max_length=32)
    asset_type: Literal["stock", "fund"]
    action: Literal["buy", "sell", "add", "reduce"]
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fees: Decimal = Field(ge=0)
    occurred_at: datetime
    external_id: str = Field(min_length=1, max_length=128)
    cash_symbol: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)


class ConfirmBody(ReviewBody):
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    confirmed: Literal[True]


def build_ledger_review_router(database, now=None):
    now = now or (lambda: datetime.now(SHANGHAI_TZ))
    reconciliation = ReconciliationService(database, now)
    replay = LedgerReplayService(database, now)
    router = APIRouter(prefix="/api/v1/ledger")

    def guarded(callback):
        try:
            return callback()
        except ValueError as error:
            return JSONResponse(
                {"error_code": "ledger_review_conflict", "message": str(error)}, status_code=409
            )

    @router.get("/reconciliation/accounts")
    def accounts():
        return reconciliation.accounts()

    @router.post("/reconciliation/compare")
    def compare(body: CompareBody):
        return guarded(lambda: reconciliation.compare(**body.model_dump()))

    @router.get("/reconciliation/history")
    def history(page: int = Query(1, ge=1, le=10000), limit: int = Query(20, ge=1, le=100)):
        return reconciliation.history(page, limit)

    @router.post("/reconciliation/{identity}/resolve")
    def resolve(identity: str, body: ResolutionBody):
        return guarded(lambda: reconciliation.resolve(identity, body.resolution, body.note))

    @router.post("/replay/preview")
    def preview(body: HistoricalTradeBody):
        item = TransactionInput(
            body.symbol,
            AssetType(body.asset_type),
            Action(body.action),
            body.quantity,
            body.price,
            body.fees,
            body.occurred_at,
            body.external_id,
            "historical-manual",
            body.cash_symbol,
            body.note,
        )
        return guarded(lambda: replay.preview(item, reason=body.reason))

    @router.post("/replay/confirm")
    def confirm(body: ConfirmBody):
        return guarded(lambda: replay.confirm(body.preview_id))

    @router.get("/replay/history")
    def replay_history(page: int = Query(1, ge=1, le=10000), limit: int = Query(20, ge=1, le=100)):
        return replay.history(page, limit)

    return router
