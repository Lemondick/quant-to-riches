"""Closed contracts for remaining fund lots and read-only planning."""

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter
from pydantic import Field, StrictBool

from quant_assistant.controllers.schemas import ApiModel, DecimalString


class LotBody(ApiModel):
    lot_id: str = Field(min_length=1, max_length=128)
    confirmed_on: date
    remaining_units: Decimal = Field(gt=0, max_digits=28, decimal_places=8)


class LotsBody(ApiModel):
    items: tuple[LotBody, ...] = Field(min_length=1, max_length=100)
    verified_on: date
    evidence_reference: str = Field(min_length=1, max_length=1000)
    position_fingerprint: str = Field(min_length=64, max_length=64)
    confirmed: StrictBool = False


class LotResponse(ApiModel):
    lot_id: str
    confirmed_on: date
    remaining_units: DecimalString


class LotsResponse(ApiModel):
    symbol: str
    status: Literal["missing", "unavailable", "stale", "reconciled"]
    position_quantity: DecimalString | None
    position_as_of_date: date | None
    position_fingerprint: str | None
    snapshot_id: int | None
    verified_on: date | None
    evidence_reference: str | None
    items: tuple[LotResponse, ...]
    blockers: tuple[str, ...]


class Selection(ApiModel):
    lot_id: str = Field(min_length=1, max_length=128)
    quantity: Decimal = Field(gt=0, max_digits=28, decimal_places=8)


class QuoteBody(ApiModel):
    snapshot_id: int = Field(gt=0)
    selections: tuple[Selection, ...] = Field(min_length=1, max_length=100)
    confirmed_on: date
    assumed_nav: Decimal = Field(gt=0, max_digits=18, decimal_places=8)


class QuoteItemResponse(ApiModel):
    lot_id: str
    quantity: DecimalString
    confirmed_on: date
    holding_days: int
    gross_amount: DecimalString
    rate: DecimalString
    fee: DecimalString


class QuoteResponse(ApiModel):
    symbol: str
    snapshot_id: int
    confirmed_on: date
    assumed_nav: DecimalString
    items: tuple[QuoteItemResponse, ...]
    total_gross: DecimalString
    total_fee: DecimalString
    estimated_net: DecimalString
    cash_credit: DecimalString
    fee_evidence_reference: str
    basis: Literal["selected_reconciled_lots_assumed_nav"]
    research_only: Literal[True]


def build_fund_lot_router(service):
    router = APIRouter(prefix="/api/v1/funds")

    @router.get("/{symbol}/holding-lots", response_model=LotsResponse)
    def lots(symbol: str):
        return service.list(symbol)

    @router.post("/{symbol}/holding-lots", response_model=LotsResponse)
    def save_lots(symbol: str, body: LotsBody):
        return service.save(
            symbol, **{**body.model_dump(), "items": [item.model_dump() for item in body.items]}
        )

    @router.post("/{symbol}/holding-redemption-quote", response_model=QuoteResponse)
    def quote(symbol: str, body: QuoteBody):
        return service.quote(
            symbol,
            **{**body.model_dump(), "selections": [item.model_dump() for item in body.selections]},
        )

    return router
