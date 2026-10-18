"""Transactional commands for locally maintained positions and transactions."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Protocol

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import Action, AssetType
from quant_assistant.services.commands_common import require_decimal

SHANGHAI = SHANGHAI_TZ
BUY_ACTIONS = frozenset({Action.BUY, Action.ADD})
SELL_ACTIONS = frozenset({Action.REDUCE, Action.SELL})
TRANSACTION_ACTIONS = BUY_ACTIONS | SELL_ACTIONS


class Clock(Protocol):
    """The small clock surface used by local command services."""

    def now(self) -> datetime: ...


class LedgerEntryNotFoundError(LookupError):
    """Raised when a correction targets a missing immutable ledger row."""

    def __init__(self, target_type: str) -> None:
        self.target_type = target_type
        self.error_code = f"{target_type}_not_found"
        super().__init__(self.error_code)


class TransactionNotFoundError(LedgerEntryNotFoundError):
    """Raised when a correction targets a missing transaction."""

    def __init__(self) -> None:
        super().__init__("transaction")


class DividendNotFoundError(LedgerEntryNotFoundError):
    """Raised when a correction targets a missing dividend."""

    def __init__(self) -> None:
        super().__init__("dividend")


@dataclass(frozen=True, slots=True)
class TransactionInput:
    """Exact user-entered transaction values."""

    symbol: str
    asset_type: AssetType
    action: Action
    quantity: Decimal
    price: Decimal
    fees: Decimal
    traded_at: datetime
    external_id: str
    source: str = "manual"
    cash_symbol: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DividendInput:
    """A confirmed cash dividend; it never submits an external order."""

    symbol: str
    asset_type: AssetType
    gross_amount: Decimal
    fees: Decimal
    paid_at: datetime
    external_id: str
    source: str = "manual"
    cash_symbol: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class CorporateActionInput:
    """Confirmed fund/stock corporate action applied to the local ledger."""

    action_type: str
    symbol: str
    asset_type: AssetType
    effective_at: datetime
    external_id: str
    source: str = "manual"
    target_symbol: str | None = None
    quantity: Decimal | None = None
    target_quantity: Decimal | None = None
    price: Decimal | None = None
    target_price: Decimal | None = None
    gross_amount: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    ratio: Decimal | None = None
    cash_symbol: str | None = None
    from_account: str | None = None
    to_account: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerCorrectionInput:
    """Immutable reversal request for a previously recorded ledger entry."""

    target_type: str
    target_id: int
    reason: str
    effective_at: datetime
    external_id: str
    source: str = "manual"
    note: str | None = None


@dataclass(frozen=True, slots=True)
class PositionInput:
    """Exact current-position values entered by the local user."""

    symbol: str
    asset_type: AssetType
    quantity: Decimal
    average_cost: Decimal
    market_value: Decimal
    as_of_date: date


@dataclass(frozen=True, slots=True)
class InstrumentInput:
    """Minimal externally verified instrument metadata for an account import."""

    symbol: str
    asset_type: AssetType
    name: str
    exchange: str | None
    as_of_date: date


@dataclass(frozen=True, slots=True)
class NavBaselineInput:
    """Explicit confirmation that the complete portfolio starts a new NAV series."""

    event_id: str
    as_of_date: date
    reason: str


class CashFlowType(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


@dataclass(frozen=True, slots=True)
class ExternalCashFlowInput:
    """A confirmed deposit or withdrawal with a caller-owned idempotency key."""

    event_id: str
    flow_type: CashFlowType
    amount: Decimal
    effective_date: date
    reason: str
    cash_symbol: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioCalibrationInput:
    """A complete reconciled account snapshot with an explicit neutral flow amount."""

    event_id: str
    as_of_date: date
    positions: tuple[PositionInput, ...]
    net_external_flow: Decimal
    reason: str


ClockLike = Clock | Callable[[], datetime]


def _require_decimal(value: object, label: str) -> Decimal:
    """Backward-compatible alias for the shared command validator."""

    return require_decimal(value, label)


def _require_symbol(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("标的代码不能为空")
    return value.strip().upper()


def _require_plain_date(value: object, label: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{label}必须是date")
    return value


def _require_shanghai_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label}必须是datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(hours=8):
        raise ValueError(f"{label}必须为上海时区时间")
    return value.astimezone(SHANGHAI)


def _same_datetime(actual: object, expected: object) -> bool:
    """Compare aware timestamps by instant, not by the database's display zone."""

    if (
        isinstance(actual, datetime)
        and isinstance(expected, datetime)
        and actual.tzinfo is not None
        and expected.tzinfo is not None
    ):
        return actual.astimezone(UTC) == expected.astimezone(UTC)
    return actual == expected or str(actual) == str(expected)


def _clock_now(clock: ClockLike) -> datetime:
    now_method = getattr(clock, "now", None)
    value = now_method() if callable(now_method) else clock()
    return _require_shanghai_datetime(value, "当前时间")


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported audit value: {type(value).__name__}")


def _to_json(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


from .base_commands import _PortfolioCommandBase  # noqa: E402
from .corporate_actions_commands import _PortfolioCommandCorporateActions  # noqa: E402
from .flows_commands import _PortfolioCommandFlows  # noqa: E402
from .storage_commands import _PortfolioCommandStorage  # noqa: E402
from .transactions_commands import _PortfolioCommandTransactions  # noqa: E402


class PortfolioCommandService(
    _PortfolioCommandBase,
    _PortfolioCommandFlows,
    _PortfolioCommandTransactions,
    _PortfolioCommandCorporateActions,
    _PortfolioCommandStorage,
):
    """Record local holdings and trades without leaving partial state."""

    pass
