"""Unknown-NAV submission and delayed settlement for public funds."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from types import MappingProxyType
from uuid import uuid4

from quant_assistant.backtest.models import (
    ExecutionResult,
    ExecutionState,
    Fill,
    RejectedOrder,
)
from quant_assistant.funds.fees import (
    FeeScheduleError,
    FundFeeSchedule,
    RedemptionTier,
    subscription_fee,
)
from quant_assistant.models.entities import Action, AssetType, ProposedOrder

_CENT = Decimal("0.01")
_FUND_UNIT = Decimal("0.00000001")
_MAX_FEE_VERIFICATION_AGE_DAYS = 30
_SUBSCRIPTION_ACTIONS = frozenset({Action.BUY, Action.ADD})
_REDEMPTION_ACTIONS = frozenset({Action.SELL, Action.REDUCE})


def _require_date(value: object, field_name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a date")
    return value


def _require_decimal(value: object, field_name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _require_non_negative_days(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


@dataclass(frozen=True, slots=True)
class FundSettlementRule:
    """Trading-session delays for fund confirmation and redemption cash."""

    subscription_confirm_days: int
    redemption_cash_days: int
    redemption_confirm_days: int = 1
    trading_dates: tuple[date, ...] = ()
    holidays: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        _require_non_negative_days(self.subscription_confirm_days, "subscription_confirm_days")
        _require_non_negative_days(self.redemption_cash_days, "redemption_cash_days")
        _require_non_negative_days(self.redemption_confirm_days, "redemption_confirm_days")
        if self.redemption_cash_days < self.redemption_confirm_days:
            raise ValueError("redemption_cash_days must not precede redemption confirmation")

        trading_dates = tuple(sorted(set(self.trading_dates)))
        holidays = tuple(sorted(set(self.holidays)))
        for value in (*trading_dates, *holidays):
            _require_date(value, "calendar date")
        if set(trading_dates).intersection(holidays):
            raise ValueError("a date cannot be both a trading date and a holiday")
        object.__setattr__(self, "trading_dates", trading_dates)
        object.__setattr__(self, "holidays", holidays)


@dataclass(frozen=True, slots=True)
class FundTradingStatus:
    """Point-in-time subscription, redemption, and latest-NAV status."""

    symbol: str
    status_date: date
    latest_nav_date: date
    purchase_open: bool
    redemption_open: bool

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip()
        if not symbol:
            raise ValueError("symbol must not be empty")
        object.__setattr__(self, "symbol", symbol)
        _require_date(self.status_date, "status_date")
        _require_date(self.latest_nav_date, "latest_nav_date")
        if not isinstance(self.purchase_open, bool):
            raise TypeError("purchase_open must be bool")
        if not isinstance(self.redemption_open, bool):
            raise TypeError("redemption_open must be bool")


@dataclass(frozen=True, slots=True)
class PendingFundOrder:
    """An application whose application-date NAV is deliberately unresolved."""

    order: ProposedOrder
    submitted_on: date
    nav_date: date
    confirm_on: date
    cash_available_on: date | None
    reserved_cash: Decimal
    fee_schedule: FundFeeSchedule
    reserved_units: Decimal = Decimal("0")
    holding_days: int | None = None
    reservation_id: str = field(
        default_factory=lambda: uuid4().hex, repr=False, compare=False, metadata={"secret": True}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.order, ProposedOrder):
            raise TypeError("order must be ProposedOrder")
        if not isinstance(self.reservation_id, str) or not self.reservation_id:
            raise ValueError("reservation_id must be a non-empty string")
        if self.order.asset_type is not AssetType.FUND:
            raise ValueError("pending order must be for a fund")
        for field_name in ("submitted_on", "nav_date", "confirm_on"):
            _require_date(getattr(self, field_name), field_name)
        if self.cash_available_on is not None:
            _require_date(self.cash_available_on, "cash_available_on")
        _require_decimal(self.reserved_cash, "reserved_cash")
        _require_decimal(self.reserved_units, "reserved_units")
        if self.reserved_cash < 0 or self.reserved_units < 0:
            raise ValueError("reserved values must be non-negative")
        if not isinstance(self.fee_schedule, FundFeeSchedule):
            raise TypeError("fee_schedule must be FundFeeSchedule")
        if self.fee_schedule.symbol != self.order.symbol:
            raise ValueError("fee schedule symbol must match order symbol")
        if self.nav_date != self.submitted_on:
            raise ValueError("fund NAV date must be the application date")
        if self.confirm_on < self.submitted_on:
            raise ValueError("confirmation cannot precede submission")
        if self.order.action in _SUBSCRIPTION_ACTIONS:
            if self.reserved_cash <= 0 or self.reserved_units != 0:
                raise ValueError("subscription must reserve cash only")
            if self.cash_available_on is not None or self.holding_days is not None:
                raise ValueError("subscription cannot have redemption settlement fields")
        elif self.order.action in _REDEMPTION_ACTIONS:
            if self.reserved_units <= 0 or self.reserved_cash != 0:
                raise ValueError("redemption must reserve units only")
            if self.cash_available_on is None or self.cash_available_on < self.confirm_on:
                raise ValueError("redemption cash date must follow confirmation")
            _require_non_negative_days(self.holding_days, "holding_days")
        else:
            raise ValueError("unsupported fund action")


class FundOrderRejected(ValueError):
    """Submission-time rejection carrying a stable machine-readable code."""

    def __init__(self, order: ProposedOrder, code: str, reason: str, rejected_on: date):
        self.order = order
        self.code = code
        self.reason = reason
        self.rejected_on = rejected_on
        self.rejection = RejectedOrder(order, code, reason, rejected_on)
        super().__init__(f"{code}: {reason}")


class _TradingCalendar:
    def __init__(
        self,
        trading_dates: Iterable[date] = (),
        holidays: Iterable[date] = (),
    ) -> None:
        explicit = tuple(sorted(set(trading_dates)))
        closed = frozenset(holidays)
        for value in (*explicit, *closed):
            _require_date(value, "calendar date")
        if set(explicit).intersection(closed):
            raise ValueError("a date cannot be both a trading date and a holiday")
        self._explicit = explicit
        self._explicit_set = frozenset(explicit)
        self._holidays = closed

    def is_trading_day(self, value: date) -> bool:
        if self._explicit:
            return value in self._explicit_set
        return value.weekday() < 5 and value not in self._holidays

    def add(self, start: date, sessions: int) -> date:
        if not self.is_trading_day(start):
            raise ValueError("application date is not in the trading calendar")
        if sessions == 0:
            return start
        if self._explicit:
            index = self._explicit.index(start) + sessions
            if index >= len(self._explicit):
                raise ValueError("trading calendar does not cover the settlement date")
            return self._explicit[index]

        current = start
        remaining = sessions
        while remaining:
            current += timedelta(days=1)
            if self.is_trading_day(current):
                remaining -= 1
        return current

    def previous(self, value: date) -> date:
        if self._explicit:
            if value not in self._explicit_set:
                raise ValueError("application date is not in the trading calendar")
            index = self._explicit.index(value)
            if index == 0:
                raise ValueError("trading calendar lacks a previous session")
            return self._explicit[index - 1]
        previous = value - timedelta(days=1)
        while not self.is_trading_day(previous):
            previous -= timedelta(days=1)
        return previous


class FundExecutionModel:
    """Submit at an unknown NAV and settle using only the application-date NAV."""

    def __init__(
        self,
        rule: FundSettlementRule,
        fee_schedules: Mapping[str, FundFeeSchedule] | None = None,
        fund_statuses: Mapping[str, object] | None = None,
        trading_dates: Iterable[date] | None = None,
        holidays: Iterable[date] | None = None,
    ) -> None:
        if not isinstance(rule, FundSettlementRule):
            raise TypeError("rule must be FundSettlementRule")
        self.rule = rule
        self._fee_schedules = MappingProxyType(dict(fee_schedules or {}))
        self._fund_statuses = MappingProxyType(dict(fund_statuses or {}))
        self._calendar = _TradingCalendar(
            rule.trading_dates if trading_dates is None else trading_dates,
            rule.holidays if holidays is None else holidays,
        )

    def submit(
        self, order: ProposedOrder, submitted_on: date, state: ExecutionState
    ) -> PendingFundOrder:
        """Validate an application and return its cash/unit reservation."""

        _require_date(submitted_on, "submitted_on")
        if not isinstance(order, ProposedOrder):
            raise TypeError("order must be ProposedOrder")
        if not isinstance(state, ExecutionState):
            raise TypeError("state must be ExecutionState")
        self._validate_order(order, submitted_on)
        try:
            if not self._calendar.is_trading_day(submitted_on):
                self._reject(order, "non_trading_day", "提交日不是基金交易日", submitted_on)
            schedule = self._fee_schedule(order, state, submitted_on)
            status = self._status(order, state, submitted_on)

            if order.action in _SUBSCRIPTION_ACTIONS:
                if not status.purchase_open:
                    self._reject(
                        order, "purchase_suspended", "基金份额在提交日暂停申购", submitted_on
                    )
                amount = _require_decimal(order.amount, "order.amount", positive=True)
                if state.cash < amount:
                    self._reject(order, "insufficient_cash", "可用现金不足", submitted_on)
                return PendingFundOrder(
                    order=order,
                    submitted_on=submitted_on,
                    nav_date=submitted_on,
                    confirm_on=self._calendar.add(
                        submitted_on, self.rule.subscription_confirm_days
                    ),
                    cash_available_on=None,
                    reserved_cash=amount,
                    fee_schedule=schedule,
                )

            if not status.redemption_open:
                self._reject(
                    order, "redemption_suspended", "基金份额在提交日暂停赎回", submitted_on
                )
            quantity = _require_decimal(order.quantity, "order.quantity", positive=True)
            available_units = state.fund_units.get(order.symbol, Decimal("0")) - (
                state.unavailable_fund_units.get(order.symbol, Decimal("0"))
            )
            if quantity > available_units:
                self._reject(order, "insufficient_position", "可赎回基金份额不足", submitted_on)
            confirm_on = self._calendar.add(submitted_on, self.rule.redemption_confirm_days)
            holding_days = self._holding_days(order, state, schedule, submitted_on, confirm_on)
            try:
                self._redemption_tier(schedule, holding_days)
            except ValueError:
                self._reject(
                    order,
                    "unverified_fee_schedule",
                    "没有唯一赎回费档覆盖实际持有期",
                    submitted_on,
                )
            return PendingFundOrder(
                order=order,
                submitted_on=submitted_on,
                nav_date=submitted_on,
                confirm_on=confirm_on,
                cash_available_on=self._calendar.add(submitted_on, self.rule.redemption_cash_days),
                reserved_cash=Decimal("0"),
                reserved_units=quantity,
                holding_days=holding_days,
                fee_schedule=schedule,
            )
        except FundOrderRejected:
            raise
        except (TypeError, ValueError) as exc:
            self._reject(order, "invalid_fund_order", str(exc), submitted_on)

    def reserve(self, pending: PendingFundOrder, state: ExecutionState) -> ExecutionState:
        """Apply a successful submission's immediate cash or unit lock."""

        if not isinstance(pending, PendingFundOrder):
            raise TypeError("pending must be PendingFundOrder")
        if not isinstance(state, ExecutionState):
            raise TypeError("state must be ExecutionState")
        if pending.reservation_id in state.active_fund_reservations:
            raise ValueError("fund application is already reserved")
        if pending.reservation_id in state.retired_fund_reservations:
            raise ValueError("fund application is already handled")
        reservations = state.active_fund_reservations | {pending.reservation_id}
        symbol = pending.order.symbol
        if pending.order.action in _SUBSCRIPTION_ACTIONS:
            if state.cash < pending.reserved_cash:
                raise ValueError("cash was no longer sufficient when reserving the order")
            return replace(
                state,
                cash=state.cash - pending.reserved_cash,
                unavailable_cash=state.unavailable_cash + pending.reserved_cash,
                active_fund_reservations=reservations,
            )

        available_units = state.fund_units.get(symbol, Decimal("0")) - (
            state.unavailable_fund_units.get(symbol, Decimal("0"))
        )
        if available_units < pending.reserved_units:
            raise ValueError("fund units were no longer available when reserving the order")
        unavailable = dict(state.unavailable_fund_units)
        unavailable[symbol] = unavailable.get(symbol, Decimal("0")) + pending.reserved_units
        return replace(
            state, unavailable_fund_units=unavailable, active_fund_reservations=reservations
        )

    @staticmethod
    def cancel_reservation(pending: PendingFundOrder, state: ExecutionState) -> ExecutionState:
        """Refund or unlock a live application once, including terminal rejection."""

        if pending.reservation_id not in state.active_fund_reservations:
            return state
        reservations = state.active_fund_reservations - {pending.reservation_id}
        retired = state.retired_fund_reservations | {pending.reservation_id}
        if pending.reserved_cash > 0:
            return replace(
                state,
                cash=state.cash + pending.reserved_cash,
                unavailable_cash=state.unavailable_cash - pending.reserved_cash,
                active_fund_reservations=reservations,
                retired_fund_reservations=retired,
            )
        unavailable = dict(state.unavailable_fund_units)
        symbol = pending.order.symbol
        remaining = unavailable.get(symbol, Decimal("0")) - pending.reserved_units
        if remaining > 0:
            unavailable[symbol] = remaining
        else:
            unavailable.pop(symbol, None)
        return replace(
            state,
            unavailable_fund_units=unavailable,
            active_fund_reservations=reservations,
            retired_fund_reservations=retired,
        )

    def settle(
        self,
        pending: PendingFundOrder,
        nav: Decimal,
        settlement_date: date,
        state: ExecutionState,
    ) -> ExecutionResult:
        """Resolve the application using its NAV once confirmation is due."""

        if not isinstance(pending, PendingFundOrder):
            raise TypeError("pending must be PendingFundOrder")
        _require_date(settlement_date, "settlement_date")
        if not isinstance(state, ExecutionState):
            raise TypeError("state must be ExecutionState")
        if pending.reservation_id not in state.active_fund_reservations:
            return self._rejection_result(
                pending.order,
                "reservation_not_active",
                "基金申请未锁定或已经处理",
                settlement_date,
                state,
            )
        if settlement_date < pending.confirm_on:
            return self._rejection_result(
                pending.order,
                "confirmation_pending",
                "基金份额尚未到确认日",
                settlement_date,
                state,
            )
        if not self._calendar.is_trading_day(settlement_date):
            return self._rejection_result(
                pending.order,
                "non_trading_day",
                "结算日不是基金交易日",
                settlement_date,
                state,
            )
        try:
            confirmed_nav = _require_decimal(nav, "nav", positive=True)
        except (TypeError, ValueError):
            return self._rejection_result(
                pending.order,
                "invalid_nav",
                "申请日净值缺失、非正数或不是Decimal",
                settlement_date,
                state,
            )

        if pending.order.action in _SUBSCRIPTION_ACTIONS:
            outcome = self._settle_subscription(pending, confirmed_nav, settlement_date, state)
        else:
            outcome = self._settle_redemption(pending, confirmed_nav, settlement_date, state)
        next_state = (
            replace(
                outcome.state,
                active_fund_reservations=state.active_fund_reservations - {pending.reservation_id},
                retired_fund_reservations=state.retired_fund_reservations
                | {pending.reservation_id},
            )
            if outcome.fill is not None
            else self.cancel_reservation(pending, outcome.state)
        )
        return replace(outcome, state=next_state)

    def release_cash(self, state: ExecutionState, on_date: date) -> ExecutionState:
        """Release all confirmed redemption cash due on or before ``on_date``."""

        if not isinstance(state, ExecutionState):
            raise TypeError("state must be ExecutionState")
        _require_date(on_date, "on_date")
        pending_cash = dict(state.pending_fund_cash)
        due_dates = [available_on for available_on in pending_cash if available_on <= on_date]
        released = sum((pending_cash.pop(value) for value in due_dates), Decimal("0"))
        if released == 0:
            return state
        return replace(
            state,
            cash=state.cash + released,
            unavailable_cash=max(state.unavailable_cash - released, Decimal("0")),
            pending_fund_cash=pending_cash,
        )

    def _validate_order(self, order: ProposedOrder, submitted_on: date) -> None:
        if order.asset_type is not AssetType.FUND:
            self._reject(order, "invalid_asset_type", "订单不是场外基金订单", submitted_on)
        if order.action not in _SUBSCRIPTION_ACTIONS | _REDEMPTION_ACTIONS:
            self._reject(order, "unsupported_action", "订单操作不支持基金申赎", submitted_on)
        if order.as_of_date >= submitted_on:
            self._reject(
                order,
                "invalid_submission_date",
                "基金订单必须基于提交日前已完成的数据",
                submitted_on,
            )
        if order.valid_until.tzinfo is None or order.valid_until.utcoffset() is None:
            self._reject(order, "invalid_validity", "订单失效时间必须包含时区", submitted_on)
        if order.valid_until.date() < submitted_on:
            self._reject(order, "expired_order", "基金订单已失效", submitted_on)

    def _fee_schedule(
        self, order: ProposedOrder, state: ExecutionState, submitted_on: date
    ) -> FundFeeSchedule:
        raw_schedules = self._fee_schedules.get(
            order.symbol, state.fund_fee_schedules.get(order.symbol)
        )
        if isinstance(raw_schedules, FundFeeSchedule):
            candidates = (raw_schedules,)
        elif isinstance(raw_schedules, Iterable) and not isinstance(
            raw_schedules, (str, bytes, Mapping)
        ):
            candidates = tuple(
                value for value in raw_schedules if isinstance(value, FundFeeSchedule)
            )
        else:
            candidates = ()
        effective_candidates = [
            schedule
            for schedule in candidates
            if schedule.symbol == order.symbol
            and (schedule.effective_from is None or schedule.effective_from <= submitted_on)
        ]
        if not effective_candidates:
            self._reject(
                order,
                "unverified_fee_schedule",
                "缺少订单份额在提交日有效且30日内核验的具体费率",
                submitted_on,
            )
        selected = max(
            effective_candidates,
            key=lambda schedule: (
                schedule.effective_from or date.min,
                schedule.verified_on,
            ),
        )
        if (
            selected.verified_on > submitted_on
            or (submitted_on - selected.verified_on).days > _MAX_FEE_VERIFICATION_AGE_DAYS
            or (selected.effective_to is not None and submitted_on > selected.effective_to)
        ):
            self._reject(
                order,
                "unverified_fee_schedule",
                "最新已生效费率未通过核验，不能回退旧费率",
                submitted_on,
            )
        return selected

    def _status(
        self, order: ProposedOrder, state: ExecutionState, submitted_on: date
    ) -> FundTradingStatus:
        raw_statuses = self._fund_statuses.get(order.symbol, state.fund_statuses.get(order.symbol))
        if isinstance(raw_statuses, Iterable) and not isinstance(
            raw_statuses, (str, bytes, Mapping, FundTradingStatus)
        ):
            statuses = tuple(
                status
                for value in raw_statuses
                if (status := self._coerce_status(value, order.symbol)) is not None
                and status.status_date <= submitted_on
            )
            status = max(statuses, key=lambda value: value.status_date, default=None)
        else:
            status = self._coerce_status(raw_statuses, order.symbol)
        if status is None:
            self._reject(order, "stale_fund_status", "缺少提交日基金申赎与净值状态", submitted_on)
        if status.status_date != submitted_on:
            self._reject(order, "stale_fund_status", "基金开放状态不是提交日状态", submitted_on)
        if status.latest_nav_date > submitted_on:
            self._reject(order, "future_nav_status", "基金状态包含提交日之后的净值", submitted_on)
        try:
            previous_session = self._calendar.previous(submitted_on)
        except ValueError as exc:
            self._reject(order, "invalid_calendar", str(exc), submitted_on)
        if status.latest_nav_date < previous_session:
            self._reject(order, "stale_nav_status", "最新已知净值早于前一交易日", submitted_on)
        return status

    @staticmethod
    def _coerce_status(raw_status: object, symbol: str) -> FundTradingStatus | None:
        if isinstance(raw_status, FundTradingStatus):
            return raw_status if raw_status.symbol == symbol else None
        if raw_status is None:
            return None
        if isinstance(raw_status, Mapping):
            get_value = raw_status.get
        else:

            def get_value(name: str, default: object = None) -> object:
                return getattr(raw_status, name, default)

        status_date = get_value("status_date", get_value("as_of_date"))
        latest_nav_date = get_value(
            "latest_nav_date", get_value("nav_date", get_value("valuation_date"))
        )
        purchase_open = get_value(
            "purchase_open", get_value("is_purchasable", get_value("subscription_open"))
        )
        redemption_open = get_value(
            "redemption_open", get_value("is_redeemable", get_value("redeem_open"))
        )
        try:
            return FundTradingStatus(
                symbol=symbol,
                status_date=status_date,
                latest_nav_date=latest_nav_date,
                purchase_open=purchase_open,
                redemption_open=redemption_open,
            )
        except (TypeError, ValueError):
            return None

    def _holding_days(
        self,
        order: ProposedOrder,
        state: ExecutionState,
        schedule: FundFeeSchedule,
        submitted_on: date,
        confirmed_on: date,
    ) -> int:
        acquired_on = state.fund_holding_since.get(
            order.symbol, state.fund_acquired_on.get(order.symbol)
        )
        if acquired_on is not None:
            if acquired_on > submitted_on:
                self._reject(
                    order, "future_holding_date", "基金持有起始日在提交日之后", submitted_on
                )
            # Match the web estimator: include subscription confirmation and
            # exclude redemption confirmation, counting natural calendar days.
            return (confirmed_on - acquired_on).days
        if (
            len(schedule.redemption_tiers) == 1
            and schedule.redemption_tiers[0].min_days == 0
            and schedule.redemption_tiers[0].max_days is None
        ):
            return 0
        self._reject(
            order,
            "unknown_holding_period",
            "缺少基金持有起始日，无法确定赎回费档",
            submitted_on,
        )

    def _settle_subscription(
        self,
        pending: PendingFundOrder,
        nav: Decimal,
        settlement_date: date,
        state: ExecutionState,
    ) -> ExecutionResult:
        try:
            fees = subscription_fee(pending.fee_schedule, pending.reserved_cash)
        except FeeScheduleError:
            return self._rejection_result(
                pending.order,
                "invalid_subscription_fee",
                "申购费档缺失或费用超过锁定金额",
                settlement_date,
                state,
            )
        invested = pending.reserved_cash - fees
        if invested <= 0:
            return self._rejection_result(
                pending.order,
                "fees_exceed_subscription",
                "申购费不低于锁定申购金额",
                settlement_date,
                state,
            )
        quantity = (invested / nav).quantize(_FUND_UNIT, rounding=ROUND_DOWN)
        if quantity <= 0:
            return self._rejection_result(
                pending.order,
                "zero_confirmed_units",
                "申购金额不足以确认最小基金份额",
                settlement_date,
                state,
            )
        units = dict(state.fund_units)
        existing_units = units.get(pending.order.symbol, Decimal("0"))
        units[pending.order.symbol] = existing_units + quantity
        holding_since = dict(state.fund_holding_since)
        acquired_on = dict(state.fund_acquired_on)
        if existing_units == 0:
            holding_since[pending.order.symbol] = pending.confirm_on
            acquired_on[pending.order.symbol] = pending.confirm_on
        elif (
            holding_since.get(pending.order.symbol, acquired_on.get(pending.order.symbol))
            != pending.confirm_on
        ):
            # A single date cannot describe mixed acquisition lots. Preserve
            # units, but block age-tiered redemption until lots are supported.
            holding_since.pop(pending.order.symbol, None)
            acquired_on.pop(pending.order.symbol, None)
        next_state = replace(
            state,
            fund_units=units,
            unavailable_cash=max(state.unavailable_cash - pending.reserved_cash, Decimal("0")),
            fund_holding_since=holding_since,
            fund_acquired_on=acquired_on,
        )
        fill = Fill(
            order=pending.order,
            quantity=quantity,
            price=nav,
            fees=fees,
            gross_amount=invested,
            trade_date=pending.nav_date,
            nav=nav,
        )
        return ExecutionResult(fill=fill, rejection=None, state=next_state)

    def _settle_redemption(
        self,
        pending: PendingFundOrder,
        nav: Decimal,
        settlement_date: date,
        state: ExecutionState,
    ) -> ExecutionResult:
        symbol = pending.order.symbol
        current_units = state.fund_units.get(symbol, Decimal("0"))
        if current_units < pending.reserved_units:
            return self._rejection_result(
                pending.order,
                "insufficient_position",
                "确认时基金份额不足",
                settlement_date,
                state,
            )
        try:
            tier = self._redemption_tier(pending.fee_schedule, pending.holding_days)
        except ValueError:
            return self._rejection_result(
                pending.order,
                "invalid_fee_tier",
                "没有唯一赎回费档覆盖实际持有期",
                settlement_date,
                state,
            )
        gross = (pending.reserved_units * nav).quantize(_CENT, rounding=ROUND_HALF_UP)
        fees = (gross * tier.rate).quantize(_CENT, rounding=ROUND_HALF_UP)
        net_proceeds = gross - fees

        units = dict(state.fund_units)
        units[symbol] = current_units - pending.reserved_units
        unavailable_units = dict(state.unavailable_fund_units)
        locked = unavailable_units.get(symbol, Decimal("0"))
        unavailable_units[symbol] = max(locked - pending.reserved_units, Decimal("0"))
        if unavailable_units[symbol] == 0:
            unavailable_units.pop(symbol)

        holding_since = dict(state.fund_holding_since)
        acquired_on = dict(state.fund_acquired_on)
        if units[symbol] == 0:
            holding_since.pop(symbol, None)
            acquired_on.pop(symbol, None)

        cash = state.cash
        unavailable_cash = state.unavailable_cash
        pending_cash = dict(state.pending_fund_cash)
        if settlement_date >= pending.cash_available_on:
            cash += net_proceeds
        else:
            pending_cash[pending.cash_available_on] = (
                pending_cash.get(pending.cash_available_on, Decimal("0")) + net_proceeds
            )
            unavailable_cash += net_proceeds
        next_state = replace(
            state,
            cash=cash,
            fund_units=units,
            unavailable_fund_units=unavailable_units,
            unavailable_cash=unavailable_cash,
            pending_fund_cash=pending_cash,
            fund_holding_since=holding_since,
            fund_acquired_on=acquired_on,
        )
        fill = Fill(
            order=pending.order,
            quantity=pending.reserved_units,
            price=nav,
            fees=fees,
            gross_amount=gross,
            trade_date=pending.nav_date,
            nav=nav,
            cash_available_on=pending.cash_available_on,
        )
        return ExecutionResult(fill=fill, rejection=None, state=next_state)

    @staticmethod
    def _redemption_tier(schedule: FundFeeSchedule, holding_days: int | None) -> RedemptionTier:
        if holding_days is None:
            raise ValueError("holding_days is required for redemption")
        matches = [tier for tier in schedule.redemption_tiers if tier.includes(holding_days)]
        if len(matches) != 1:
            raise ValueError("no unique redemption fee tier covers the holding period")
        return matches[0]

    @staticmethod
    def _reject(order: ProposedOrder, code: str, reason: str, rejected_on: date) -> None:
        raise FundOrderRejected(order, code, reason, rejected_on)

    @staticmethod
    def _rejection_result(
        order: ProposedOrder,
        code: str,
        reason: str,
        rejected_on: date,
        state: ExecutionState,
    ) -> ExecutionResult:
        return ExecutionResult(
            fill=None,
            rejection=RejectedOrder(order, code, reason, rejected_on),
            state=state,
        )


__all__ = [
    "FundExecutionModel",
    "FundOrderRejected",
    "FundSettlementRule",
    "FundTradingStatus",
    "PendingFundOrder",
]
