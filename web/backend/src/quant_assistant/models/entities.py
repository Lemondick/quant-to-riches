"""Immutable domain records and closed vocabularies."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class AssetType(StrEnum):
    STOCK = "stock"
    FUND = "fund"
    INDEX = "index"
    CASH = "cash"


class Action(StrEnum):
    BUY = "buy"
    ADD = "add"
    HOLD = "hold"
    REDUCE = "reduce"
    SELL = "sell"
    NO_TRADE = "no_trade"


class MarketRegime(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class DataStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"
    BLOCKED = "blocked"


class ReadinessStatus(StrEnum):
    RESEARCH_ONLY = "research_only"
    PAPER = "paper"
    REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    asset_type: AssetType
    quantity: Decimal
    average_cost: Decimal
    market_value: Decimal
    as_of_date: date


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    asset_type: AssetType
    action: Action
    score: float
    target_weight: Decimal
    reasons: tuple[str, ...]
    as_of_date: date
    valid_until: datetime
    strategy_version: str


@dataclass(frozen=True, slots=True)
class ProposedOrder:
    symbol: str
    asset_type: AssetType
    action: Action
    amount: Decimal
    quantity: Decimal | None
    target_weight: Decimal
    reasons: tuple[str, ...]
    as_of_date: date
    valid_until: datetime
    data_snapshot_id: int
    strategy_version: str
