"""Common closed model and serialisation helpers for the local API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer


class ApiModel(BaseModel):
    """Closed API model that can validate immutable application records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
        allow_inf_nan=False,
    )


class ObservationModel(ApiModel):
    auction_supported: bool = False
    market: str = "CN"
    market_timezone: str | None = None
    market_session: str = "unknown"
    calendar_revision: str | None = None
    calendar_source: str | None = None
    quality_reason: str | None = None
    instrument_type: str | None = None
    data_type: str | None = None
    source_family: str | None = None
    source_time: str | None = None
    updated_at: str | None = None
    fetched_at: datetime | None = None
    trade_date: str | None = None
    is_estimate: bool = False
    is_stale: bool = False
    fallback_used: bool = False
    fallback_reason: str | None = None
    quality_status: str = "unknown_time"


class NavObservationModel(ObservationModel):
    nav_publication_lag_sessions: int | None = None
    nav_freshness_policy: str | None = None
    nav_age_sessions: int | None = None
    nav_freshness_status: str | None = None
    nav_valuation_weekday: int | None = None
    nav_policy_basis: str | None = None


def _decimal_string(value: Decimal) -> str:
    return format(value, "f")


DecimalString = Annotated[
    Decimal,
    PlainSerializer(_decimal_string, return_type=str, when_used="json"),
]

__all__ = ["ApiModel", "DecimalString"]
