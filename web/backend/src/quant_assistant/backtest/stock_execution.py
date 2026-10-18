"""Conservative next-open execution for mainland ordinary A-shares."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from quant_assistant.backtest.models import (
    DailyBar,
    ExecutionResult,
    ExecutionState,
    Fill,
    RejectedOrder,
    StockFeeSchedule,
)
from quant_assistant.models.entities import Action, AssetType, ProposedOrder

_CENT = Decimal("0.01")
_PRICE_TICK = Decimal("0.001")
_BOARD_LOT = Decimal("100")
_BASIS_POINTS = Decimal("10000")
_ZERO = Decimal("0")


class StockExecutionModel:
    """Turn a stock proposal into a fill or an explicit rejection."""

    def __init__(self, fee_schedule: StockFeeSchedule):
        if not isinstance(fee_schedule, StockFeeSchedule):
            raise TypeError("fee_schedule must be StockFeeSchedule")
        self._fees = fee_schedule

    def execute(
        self,
        order: ProposedOrder,
        bar: DailyBar,
        state: ExecutionState,
    ) -> ExecutionResult:
        """Execute at the next session's open, applying all constraints first."""

        if not isinstance(order, ProposedOrder):
            raise TypeError("order must be ProposedOrder")
        if not isinstance(bar, DailyBar):
            raise TypeError("bar must be DailyBar")
        if not isinstance(state, ExecutionState):
            raise TypeError("state must be ExecutionState")

        rejection = self._preflight(order, bar, state)
        if rejection is not None:
            return self._rejected(order, bar, state, *rejection)

        action = Action(order.action)
        quantity = order.quantity
        assert isinstance(quantity, Decimal)  # guaranteed by preflight
        is_buy = action in {Action.BUY, Action.ADD}
        execution_price = self._execution_price(bar, is_buy=is_buy)
        gross_amount = (execution_price * quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
        fees = self._calculate_fees(gross_amount, is_sell=not is_buy)

        if is_buy:
            cash_required = gross_amount + fees
            if cash_required > state.cash:
                return self._rejected(
                    order,
                    bar,
                    state,
                    "insufficient_cash",
                    "available cash does not cover consideration and fees",
                )
            unsettled = dict(state.unsettled_stock)
            unsettled[order.symbol] = unsettled.get(order.symbol, _ZERO) + quantity
            old_quantity = state.stock_quantities.get(order.symbol, _ZERO)
            new_quantity = old_quantity + quantity
            average_costs = dict(state.stock_average_costs)
            old_average_cost = average_costs.get(order.symbol, _ZERO)
            average_costs[order.symbol] = (
                old_quantity * old_average_cost + gross_amount + fees
            ) / new_quantity
            next_state = replace(
                state,
                cash=(state.cash - cash_required).quantize(_CENT, rounding=ROUND_HALF_UP),
                unsettled_stock=unsettled,
                stock_average_costs=average_costs,
            )
        else:
            settled = dict(state.settled_stock)
            settled[order.symbol] = settled.get(order.symbol, _ZERO) - quantity
            average_costs = dict(state.stock_average_costs)
            if state.stock_quantities.get(order.symbol, _ZERO) - quantity <= _ZERO:
                average_costs.pop(order.symbol, None)
            proceeds = gross_amount - fees
            next_state = replace(
                state,
                cash=(state.cash + proceeds).quantize(_CENT, rounding=ROUND_HALF_UP),
                settled_stock=settled,
                stock_average_costs=average_costs,
            )

        fill = Fill(
            order=order,
            quantity=quantity,
            price=execution_price,
            fees=fees,
            gross_amount=gross_amount,
            trade_date=bar.trade_date,
        )
        return ExecutionResult(fill=fill, rejection=None, state=next_state)

    def _preflight(
        self,
        order: ProposedOrder,
        bar: DailyBar,
        state: ExecutionState,
    ) -> tuple[str, str] | None:
        if order.symbol != bar.symbol:
            return "symbol_mismatch", "order symbol does not match execution bar"
        try:
            asset_type = AssetType(order.asset_type)
        except (TypeError, ValueError):
            return "invalid_asset_type", "order asset type is invalid"
        if asset_type is not AssetType.STOCK:
            return "invalid_asset_type", "stock execution only accepts stock orders"
        try:
            action = Action(order.action)
        except (TypeError, ValueError):
            return "unsupported_action", "order action is invalid"
        if action not in {Action.BUY, Action.ADD, Action.REDUCE, Action.SELL}:
            return "unsupported_action", "order action cannot create a stock fill"
        if (
            not isinstance(order.amount, Decimal)
            or not order.amount.is_finite()
            or order.amount <= 0
        ):
            return "invalid_amount", "order amount must be a positive finite Decimal"
        if (
            not isinstance(order.as_of_date, date)
            or isinstance(order.as_of_date, datetime)
            or order.as_of_date >= bar.trade_date
        ):
            return "invalid_execution_date", "stock orders execute only after their signal date"
        if not isinstance(order.valid_until, datetime):
            return "invalid_validity", "valid_until must be a datetime"
        if order.valid_until.tzinfo is None or order.valid_until.utcoffset() is None:
            return "invalid_validity", "valid_until must be timezone-aware"
        if order.valid_until.date() < bar.trade_date:
            return "expired_order", "order expired before the execution session"
        if not self._fees.is_verified_as_of(order.as_of_date):
            return "unverified_fee_schedule", "stock fee rates were not verified by the order date"
        if not self._fees.is_verified_as_of(bar.trade_date):
            return "unverified_fee_schedule", "stock fee rates were not verified by the trade date"
        quantity = order.quantity
        if (
            not isinstance(quantity, Decimal)
            or not quantity.is_finite()
            or quantity <= 0
            or quantity != quantity.to_integral_value()
        ):
            return "invalid_quantity", "stock quantity must be a positive whole Decimal"
        if bar.suspended:
            return "suspended", "security is suspended on the execution date"
        is_buy = action in {Action.BUY, Action.ADD}
        if is_buy and bar.open >= bar.upper_limit:
            return "upper_limit_locked", "buy cannot fill at a locked upper limit"
        if not is_buy and bar.open <= bar.lower_limit:
            return "lower_limit_locked", "sell cannot fill at a locked lower limit"
        if is_buy and quantity % _BOARD_LOT != 0:
            return "invalid_board_lot", "stock purchases must use 100-share board lots"
        if not is_buy:
            settled = state.settled_stock.get(order.symbol, _ZERO)
            total = state.stock_quantities.get(order.symbol, _ZERO)
            if quantity > total:
                return "insufficient_position", "sell quantity exceeds the total position"
            if quantity > settled:
                return "t_plus_one", "sell quantity exceeds shares settled under T+1"
        return None

    def _execution_price(self, bar: DailyBar, *, is_buy: bool) -> Decimal:
        slippage = self._fees.slippage_bps / _BASIS_POINTS
        if is_buy:
            raw_price = min(bar.open * (Decimal("1") + slippage), bar.high)
            rounded = raw_price.quantize(_PRICE_TICK, rounding=ROUND_CEILING)
            return min(rounded, bar.high)
        raw_price = max(bar.open * (Decimal("1") - slippage), bar.low)
        rounded = raw_price.quantize(_PRICE_TICK, rounding=ROUND_FLOOR)
        return max(rounded, bar.low)

    def _calculate_fees(self, gross_amount: Decimal, *, is_sell: bool) -> Decimal:
        commission = max(
            gross_amount * self._fees.commission_rate,
            self._fees.minimum_commission,
        )
        transfer_fee = gross_amount * self._fees.transfer_fee_rate
        stamp_tax = gross_amount * self._fees.sell_stamp_tax_rate if is_sell else _ZERO
        return (commission + transfer_fee + stamp_tax).quantize(_CENT, rounding=ROUND_HALF_UP)

    @staticmethod
    def _rejected(
        order: ProposedOrder,
        bar: DailyBar,
        state: ExecutionState,
        code: str,
        reason: str,
    ) -> ExecutionResult:
        rejection = RejectedOrder(
            order=order,
            code=code,
            reason=reason,
            rejected_on=bar.trade_date,
        )
        return ExecutionResult(fill=None, rejection=rejection, state=state)


__all__ = ["StockExecutionModel"]
