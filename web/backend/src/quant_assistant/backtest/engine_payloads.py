"""State, identifier and row-shape helpers for the backtest engine.

Extracted from ``engine.py`` so the deterministic event loop itself stays
readable.  The mixin carries no ``__init__``: instances always resolve these
through ``BacktestEngine``'s own constructor, and the methods only rely on
attributes that ``BacktestEngine`` already defines.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import CodeType, MappingProxyType

import pandas as pd

from quant_assistant.backtest.models import ExecutionState, Fill, RejectedOrder
from quant_assistant.backtest.repository import canonical_json
from quant_assistant.models.entities import AssetType, Position, ProposedOrder
from quant_assistant.portfolio.models import PortfolioState

from .engine_components import (
    CENT,
    ZERO,
    BacktestRequest,
    _enum_text,
    _jsonable,
    _PendingFundEntry,
)

__all__ = ["EnginePayloadMixin"]


class EnginePayloadMixin:
    """Snapshot, identifier and row-shape helpers of ``BacktestEngine``."""

    @staticmethod
    def _portfolio_state(
        state: ExecutionState,
        as_of_date: date,
        marks: Mapping[str, Decimal],
        initial_cash: Decimal,
        high_water_mark: Decimal,
        limitations: list[str],
    ) -> PortfolioState:
        positions: list[Position] = []
        for symbol, quantity in state.stock_quantities.items():
            if quantity <= ZERO:
                continue
            mark = marks.get(symbol)
            if mark is None:
                limitations.append(f"missing_valuation:{symbol}:{as_of_date.isoformat()}")
                mark = ZERO
            positions.append(
                Position(
                    symbol=symbol,
                    asset_type=AssetType.STOCK,
                    quantity=quantity,
                    average_cost=state.stock_average_costs.get(symbol, mark),
                    market_value=quantity * mark,
                    as_of_date=as_of_date,
                )
            )
        for symbol, quantity in state.fund_units.items():
            if quantity <= ZERO:
                continue
            mark = marks.get(symbol)
            if mark is None:
                limitations.append(f"missing_valuation:{symbol}:{as_of_date.isoformat()}")
                mark = ZERO
            positions.append(
                Position(
                    symbol=symbol,
                    asset_type=AssetType.FUND,
                    quantity=quantity,
                    average_cost=mark,
                    market_value=quantity * mark,
                    as_of_date=as_of_date,
                )
            )
        total_value = (
            state.cash
            + state.unavailable_cash
            + sum((item.market_value for item in positions), ZERO)
        )
        unit_nav = total_value / initial_cash
        return PortfolioState(
            total_value=total_value,
            cash=state.cash,
            unit_nav=unit_nav,
            high_water_mark=high_water_mark,
            positions=tuple(sorted(positions, key=lambda item: item.symbol)),
            as_of_date=as_of_date,
        )

    @staticmethod
    def _closing_positions(
        state: ExecutionState,
        trade_date: date,
        marks: Mapping[str, Decimal],
        limitations: list[str],
    ) -> tuple[Decimal, list[dict[str, object]]]:
        total = state.cash + state.unavailable_cash
        rows: list[dict[str, object]] = []
        for asset_type, quantities in (
            (AssetType.STOCK, state.stock_quantities),
            (AssetType.FUND, state.fund_units),
        ):
            for symbol, quantity in sorted(quantities.items()):
                if quantity <= ZERO:
                    continue
                mark = marks.get(symbol)
                if mark is None:
                    limitations.append(f"missing_valuation:{symbol}:{trade_date.isoformat()}")
                    market_value = ZERO
                else:
                    market_value = quantity * mark
                    total += market_value
                rows.append(
                    {
                        "trade_date": trade_date,
                        "symbol": symbol,
                        "asset_type": asset_type.value,
                        "quantity": quantity,
                        "settled_quantity": (
                            state.settled_stock.get(symbol, ZERO)
                            if asset_type is AssetType.STOCK
                            else quantity - state.unavailable_fund_units.get(symbol, ZERO)
                        ),
                        "unsettled_quantity": (
                            state.unsettled_stock.get(symbol, ZERO)
                            if asset_type is AssetType.STOCK
                            else state.unavailable_fund_units.get(symbol, ZERO)
                        ),
                        "market_price": mark,
                        "market_value": market_value,
                    }
                )
        return total.quantize(CENT), rows

    @staticmethod
    def _order_id(order: ProposedOrder, execution_date: date, sequence: int) -> str:
        payload = {
            "sequence": sequence,
            "symbol": order.symbol,
            "asset_type": _enum_text(order.asset_type),
            "action": _enum_text(order.action),
            "amount": str(order.amount),
            "quantity": None if order.quantity is None else str(order.quantity),
            "signal_date": str(order.as_of_date),
            "execution_date": str(execution_date),
            "valid_until": str(order.valid_until),
            "reasons": order.reasons,
            "snapshot_id": order.data_snapshot_id,
            "strategy_version": order.strategy_version,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]

    def _run_id(
        self,
        request: BacktestRequest,
        trading_dates: Sequence[date],
        snapshot_ids: set[int],
        nav: pd.DataFrame,
        orders: pd.DataFrame,
        fills: pd.DataFrame,
        positions: pd.DataFrame,
        limitations: Sequence[str],
        final_state: ExecutionState,
        pending_funds: Sequence[_PendingFundEntry],
    ) -> str:
        payload = {
            "start": request.start,
            "end": request.end,
            "initial_cash": request.initial_cash,
            "strategy_version": request.strategy_version,
            "parameters": _jsonable(request.parameters),
            "research_only_reasons": request.research_only_reasons,
            "trading_dates": tuple(trading_dates),
            "snapshot_ids": tuple(sorted(snapshot_ids)),
            "configuration": self._configuration_payload(),
            "limitations": tuple(limitations),
            "nav": nav.to_dict(orient="records"),
            "orders": orders.to_dict(orient="records"),
            "fills": fills.to_dict(orient="records"),
            "positions": positions.to_dict(orient="records"),
            "final_state": self._state_payload(final_state),
            "pending_funds": tuple(
                {
                    "order_id": item.order_id,
                    "submitted_on": item.pending.submitted_on,
                    "nav_date": item.pending.nav_date,
                    "confirm_on": item.pending.confirm_on,
                    "cash_available_on": item.pending.cash_available_on,
                    "reserved_cash": item.pending.reserved_cash,
                    "reserved_units": item.pending.reserved_units,
                    "holding_days": item.pending.holding_days,
                }
                for item in pending_funds
            ),
        }
        encoded = canonical_json(_jsonable(payload)).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:32]

    @staticmethod
    def _state_payload(state: ExecutionState) -> Mapping[str, object]:
        return {
            "cash": state.cash,
            "settled_stock": state.settled_stock,
            "unsettled_stock": state.unsettled_stock,
            "stock_average_costs": state.stock_average_costs,
            "fund_units": state.fund_units,
            "unavailable_fund_units": state.unavailable_fund_units,
            "unavailable_cash": state.unavailable_cash,
            "pending_fund_cash": state.pending_fund_cash,
            "fund_fee_schedules": state.fund_fee_schedules,
            "fund_statuses": state.fund_statuses,
            "fund_purchase_open": state.fund_purchase_open,
            "fund_redemption_open": state.fund_redemption_open,
            "fund_acquired_on": state.fund_acquired_on,
            "fund_holding_since": state.fund_holding_since,
        }

    def _configuration_payload(self) -> Mapping[str, object]:
        def immutable_config(value: object) -> object:
            if isinstance(value, CodeType):
                return {
                    "name": value.co_name,
                    "code": value.co_code.hex(),
                    "names": value.co_names,
                    "constants": tuple(immutable_config(item) for item in value.co_consts),
                }
            if isinstance(value, Enum):
                return _jsonable(value.value)
            if value is None or isinstance(value, (str, bool, int, float, Decimal, date, datetime)):
                return _jsonable(value)
            if isinstance(value, tuple):
                return tuple(immutable_config(item) for item in value)
            if isinstance(value, frozenset):
                items = [immutable_config(item) for item in value]
                return tuple(sorted(items, key=canonical_json))
            if isinstance(value, MappingProxyType):
                return {
                    str(key): immutable_config(item)
                    for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                }
            dataclass_parameters = getattr(type(value), "__dataclass_params__", None)
            if is_dataclass(value) and getattr(dataclass_parameters, "frozen", False):
                return _jsonable(value)
            return {"mutable_or_opaque_type": type(value).__qualname__}

        def callable_identity(callback: object) -> Mapping[str, object]:
            code = getattr(callback, "__code__", None)
            closure_values: list[object] = []
            for cell in getattr(callback, "__closure__", ()) or ():
                try:
                    closure_values.append(immutable_config(cell.cell_contents))
                except ValueError:
                    closure_values.append({"empty_cell": True})
            return {
                "module": getattr(callback, "__module__", type(callback).__module__),
                "qualname": getattr(
                    callback,
                    "__qualname__",
                    getattr(callback, "__name__", type(callback).__qualname__),
                ),
                "code": None if code is None else immutable_config(code),
                "defaults": immutable_config(getattr(callback, "__defaults__", None)),
                "keyword_defaults": immutable_config(getattr(callback, "__kwdefaults__", None)),
                "closure": tuple(closure_values),
            }

        return {
            "stock_fees": getattr(self.stock_execution, "_fees", None),
            "fund_rule": (None if self.fund_execution is None else self.fund_execution.rule),
            "fund_fee_schedules": getattr(self.data, "fund_fee_schedules", {}),
            "risk_policy": self.policy,
            "strategies": tuple(callable_identity(item) for item in self.strategies),
            "order_proposer": callable_identity(self.order_proposer),
        }

    @staticmethod
    def _order_row(
        order: ProposedOrder,
        order_id: str,
        execution_date: date,
        *,
        status: str,
        rejection_code: str | None = None,
        rejection_reason: str | None = None,
    ) -> dict[str, object]:
        return {
            "order_id": order_id,
            "signal_date": order.as_of_date,
            "execution_date": execution_date,
            "settlement_date": None,
            "symbol": order.symbol,
            "asset_type": _enum_text(order.asset_type),
            "action": _enum_text(order.action),
            "amount": order.amount,
            "quantity": order.quantity,
            "target_weight": order.target_weight,
            "reasons": order.reasons,
            "valid_until": order.valid_until,
            "status": status,
            "rejection_code": rejection_code,
            "rejection_reason": rejection_reason,
            "data_snapshot_id": order.data_snapshot_id,
            "strategy_version": order.strategy_version,
        }

    def _execution_order_row(
        self,
        order: ProposedOrder,
        order_id: str,
        execution_date: date,
        rejection: RejectedOrder | None,
    ) -> dict[str, object]:
        return self._order_row(
            order,
            order_id,
            execution_date,
            status="filled" if rejection is None else "rejected",
            rejection_code=None if rejection is None else rejection.code,
            rejection_reason=None if rejection is None else rejection.reason,
        )

    @staticmethod
    def _fill_row(fill: Fill, settlement_date: date, order_id: str) -> dict[str, object]:
        return {
            "order_id": order_id,
            "trade_date": fill.trade_date,
            "settlement_date": settlement_date,
            "symbol": fill.symbol,
            "asset_type": _enum_text(fill.asset_type),
            "action": _enum_text(fill.action),
            "quantity": fill.quantity,
            "price": fill.price,
            "nav": fill.nav,
            "gross_amount": fill.gross_amount,
            "fees": fill.fees,
            "net_amount": fill.net_amount,
            "cash_available_on": fill.cash_available_on,
            "data_snapshot_id": fill.order.data_snapshot_id,
            "strategy_version": fill.order.strategy_version,
        }

    @staticmethod
    def _frame(rows: list[dict[str, object]], columns: Sequence[str]) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=list(columns))

    _ORDER_COLUMNS = (
        "order_id",
        "signal_date",
        "execution_date",
        "settlement_date",
        "symbol",
        "asset_type",
        "action",
        "amount",
        "quantity",
        "target_weight",
        "reasons",
        "valid_until",
        "status",
        "rejection_code",
        "rejection_reason",
        "data_snapshot_id",
        "strategy_version",
    )
    _FILL_COLUMNS = (
        "order_id",
        "trade_date",
        "settlement_date",
        "symbol",
        "asset_type",
        "action",
        "quantity",
        "price",
        "nav",
        "gross_amount",
        "fees",
        "net_amount",
        "cash_available_on",
        "data_snapshot_id",
        "strategy_version",
    )
    _POSITION_COLUMNS = (
        "trade_date",
        "symbol",
        "asset_type",
        "quantity",
        "settled_quantity",
        "unsettled_quantity",
        "market_price",
        "market_value",
        "data_snapshot_id",
    )
