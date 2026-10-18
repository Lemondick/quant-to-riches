"""Immutable execution records shared by backtest fill models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import TypeVar

from quant_assistant.common.serialization import decimal_coerce
from quant_assistant.models.entities import Action, AssetType, ProposedOrder

_T = TypeVar("_T")


def _require_decimal(value: object, field_name: str, *, non_negative: bool = False) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if non_negative and value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _require_date(value: object, field_name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a date")
    return value


def _decimal_mapping(value: Mapping[str, Decimal], field_name: str) -> Mapping[str, Decimal]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    normalized: dict[str, Decimal] = {}
    for raw_symbol, raw_amount in value.items():
        symbol = str(raw_symbol).strip()
        if not symbol:
            raise ValueError(f"{field_name} contains an empty symbol")
        if symbol in normalized:
            raise ValueError(f"{field_name} contains duplicate symbol {symbol}")
        normalized[symbol] = decimal_coerce(
            raw_amount, f"{field_name}[{symbol}]", non_negative=True
        )
    return MappingProxyType(dict(sorted(normalized.items())))


def _object_mapping(value: Mapping[str, _T], field_name: str) -> Mapping[str, _T]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    normalized: dict[str, _T] = {}
    for raw_symbol, item in value.items():
        symbol = str(raw_symbol).strip()
        if not symbol:
            raise ValueError(f"{field_name} contains an empty symbol")
        normalized[symbol] = item
    return MappingProxyType(dict(sorted(normalized.items())))


def _dated_cash_mapping(value: Mapping[date, Decimal], field_name: str) -> Mapping[date, Decimal]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    normalized: dict[date, Decimal] = {}
    for available_on, amount in value.items():
        settlement_date = _require_date(available_on, f"{field_name} key")
        normalized[settlement_date] = normalized.get(settlement_date, Decimal("0")) + (
            _require_decimal(amount, f"{field_name}[{settlement_date}]", non_negative=True)
        )
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One completed daily bar used for next-open stock execution."""

    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    previous_close: Decimal
    suspended: bool
    upper_limit: Decimal
    lower_limit: Decimal

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip()
        if not symbol:
            raise ValueError("symbol must not be empty")
        object.__setattr__(self, "symbol", symbol)
        _require_date(self.trade_date, "trade_date")
        for field_name in (
            "open",
            "high",
            "low",
            "close",
            "previous_close",
            "upper_limit",
            "lower_limit",
        ):
            value = _require_decimal(getattr(self, field_name), field_name)
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")
        if not isinstance(self.suspended, bool):
            raise TypeError("suspended must be bool")
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        if not self.low <= self.open <= self.high:
            raise ValueError("open must be within low and high")
        if not self.low <= self.close <= self.high:
            raise ValueError("close must be within low and high")
        if self.lower_limit > self.upper_limit:
            raise ValueError("lower_limit must not exceed upper_limit")


@dataclass(frozen=True, slots=True)
class StockFeeSchedule:
    """Account-specific stock rates verified on a historical date."""

    commission_rate: Decimal
    minimum_commission: Decimal
    sell_stamp_tax_rate: Decimal
    transfer_fee_rate: Decimal
    slippage_bps: Decimal
    verified_on: date

    def __post_init__(self) -> None:
        for field_name in (
            "commission_rate",
            "sell_stamp_tax_rate",
            "transfer_fee_rate",
        ):
            value = _require_decimal(getattr(self, field_name), field_name, non_negative=True)
            if value > 1:
                raise ValueError(f"{field_name} must not exceed 1")
        _require_decimal(self.minimum_commission, "minimum_commission", non_negative=True)
        slippage = _require_decimal(self.slippage_bps, "slippage_bps", non_negative=True)
        if slippage > Decimal("10000"):
            raise ValueError("slippage_bps must not exceed 10000")
        _require_date(self.verified_on, "verified_on")

    def is_verified_as_of(self, as_of_date: date) -> bool:
        """Return whether the rates were known by ``as_of_date``."""

        _require_date(as_of_date, "as_of_date")
        return self.verified_on <= as_of_date


@dataclass(frozen=True, slots=True)
class ExecutionState:
    """Available cash and settled/unavailable security balances."""

    cash: Decimal
    settled_stock: Mapping[str, Decimal] = field(default_factory=dict)
    unsettled_stock: Mapping[str, Decimal] = field(default_factory=dict)
    stock_average_costs: Mapping[str, Decimal] = field(default_factory=dict)
    fund_units: Mapping[str, Decimal] = field(default_factory=dict)
    unavailable_fund_units: Mapping[str, Decimal] = field(default_factory=dict)
    unavailable_cash: Decimal = Decimal("0")
    pending_fund_cash: Mapping[date, Decimal] = field(default_factory=dict)
    fund_fee_schedules: Mapping[str, object] = field(default_factory=dict)
    fund_statuses: Mapping[str, object] = field(default_factory=dict)
    fund_purchase_open: Mapping[str, bool] = field(default_factory=dict)
    fund_redemption_open: Mapping[str, bool] = field(default_factory=dict)
    fund_acquired_on: Mapping[str, date] = field(default_factory=dict)
    fund_holding_since: Mapping[str, date] = field(default_factory=dict)
    active_fund_reservations: frozenset[str] = field(
        default_factory=frozenset, repr=False, compare=False, metadata={"secret": True}
    )
    retired_fund_reservations: frozenset[str] = field(
        default_factory=frozenset, repr=False, compare=False, metadata={"secret": True}
    )

    def __post_init__(self) -> None:
        _require_decimal(self.cash, "cash", non_negative=True)
        _require_decimal(self.unavailable_cash, "unavailable_cash", non_negative=True)
        for field_name in ("active_fund_reservations", "retired_fund_reservations"):
            reservations = frozenset(getattr(self, field_name))
            if not all(isinstance(value, str) and value for value in reservations):
                raise TypeError(f"{field_name} must contain non-empty strings")
            object.__setattr__(self, field_name, reservations)
        object.__setattr__(
            self,
            "pending_fund_cash",
            _dated_cash_mapping(self.pending_fund_cash, "pending_fund_cash"),
        )
        for field_name in (
            "settled_stock",
            "unsettled_stock",
            "stock_average_costs",
            "fund_units",
            "unavailable_fund_units",
        ):
            object.__setattr__(
                self, field_name, _decimal_mapping(getattr(self, field_name), field_name)
            )
        for field_name in (
            "fund_fee_schedules",
            "fund_statuses",
            "fund_purchase_open",
            "fund_redemption_open",
            "fund_acquired_on",
            "fund_holding_since",
        ):
            object.__setattr__(
                self, field_name, _object_mapping(getattr(self, field_name), field_name)
            )
        for field_name in ("fund_purchase_open", "fund_redemption_open"):
            if not all(isinstance(value, bool) for value in getattr(self, field_name).values()):
                raise TypeError(f"{field_name} values must be bool")
        for field_name in ("fund_acquired_on", "fund_holding_since"):
            if not all(
                isinstance(value, date) and not isinstance(value, datetime)
                for value in getattr(self, field_name).values()
            ):
                raise TypeError(f"{field_name} values must be dates")

    @property
    def stock_quantities(self) -> Mapping[str, Decimal]:
        """Return total stock units, including today's unsettled purchases."""

        symbols = set(self.settled_stock) | set(self.unsettled_stock)
        return MappingProxyType(
            {
                symbol: self.settled_stock.get(symbol, Decimal("0"))
                + self.unsettled_stock.get(symbol, Decimal("0"))
                for symbol in sorted(symbols)
            }
        )

    @property
    def settled_quantities(self) -> Mapping[str, Decimal]:
        """Compatibility alias for stock quantities available to sell."""

        return self.settled_stock

    @property
    def unsettled_quantities(self) -> Mapping[str, Decimal]:
        """Compatibility alias for stock quantities still under T+1."""

        return self.unsettled_stock

    @property
    def available_cash(self) -> Decimal:
        """Compatibility alias emphasizing that reserved cash is excluded."""

        return self.cash


@dataclass(frozen=True, slots=True)
class Fill:
    """A deterministic stock or public-fund execution fill."""

    order: ProposedOrder
    quantity: Decimal
    price: Decimal
    fees: Decimal
    gross_amount: Decimal
    trade_date: date
    nav: Decimal | None = None
    cash_available_on: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.order, ProposedOrder):
            raise TypeError("order must be ProposedOrder")
        _require_decimal(self.quantity, "quantity", non_negative=True)
        price = _require_decimal(self.price, "price", non_negative=True)
        _require_decimal(self.fees, "fees", non_negative=True)
        _require_decimal(self.gross_amount, "gross_amount", non_negative=True)
        _require_date(self.trade_date, "trade_date")
        if self.quantity > 0 and price <= 0:
            raise ValueError("price must be positive for a non-zero fill")
        if self.nav is not None:
            nav = _require_decimal(self.nav, "nav")
            if nav <= 0:
                raise ValueError("nav must be positive")
        if self.cash_available_on is not None:
            _require_date(self.cash_available_on, "cash_available_on")

    @property
    def symbol(self) -> str:
        return self.order.symbol

    @property
    def asset_type(self) -> AssetType:
        return self.order.asset_type

    @property
    def action(self) -> Action:
        return self.order.action

    @property
    def net_amount(self) -> Decimal:
        """Cash paid by a buy or received from a sale."""

        if self.action in {Action.BUY, Action.ADD}:
            return self.gross_amount + self.fees
        return self.gross_amount - self.fees


@dataclass(frozen=True, slots=True)
class RejectedOrder:
    """A fail-closed order outcome with a stable machine-readable code."""

    order: ProposedOrder
    code: str
    reason: str
    rejected_on: date

    def __post_init__(self) -> None:
        if not isinstance(self.order, ProposedOrder):
            raise TypeError("order must be ProposedOrder")
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must not be empty")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must not be empty")
        _require_date(self.rejected_on, "rejected_on")

    @property
    def symbol(self) -> str:
        return self.order.symbol


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Exactly one fill or rejection plus the resulting immutable state."""

    fill: Fill | None
    rejection: RejectedOrder | None
    state: ExecutionState

    def __post_init__(self) -> None:
        if (self.fill is None) == (self.rejection is None):
            raise ValueError("execution result must contain exactly one fill or rejection")
        if not isinstance(self.state, ExecutionState):
            raise TypeError("state must be ExecutionState")

    @property
    def succeeded(self) -> bool:
        return self.fill is not None


__all__ = [
    "DailyBar",
    "ExecutionResult",
    "ExecutionState",
    "Fill",
    "RejectedOrder",
    "StockFeeSchedule",
]
