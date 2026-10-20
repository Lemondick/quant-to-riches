"""Deterministic daily event engine shared by research and paper workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from quant_assistant.backtest.fund_execution import (
    FundExecutionModel,
    FundOrderRejected,
    PendingFundOrder,
)
from quant_assistant.backtest.models import (
    ExecutionState,
    Fill,
)
from quant_assistant.backtest.repository import BacktestRepository
from quant_assistant.backtest.stock_execution import StockExecutionModel
from quant_assistant.backtest.stock_features import PreparedStockFeatures
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import Action, AssetType, ProposedOrder, Signal
from quant_assistant.portfolio.models import PortfolioState, RiskPolicy
from quant_assistant.portfolio.orders import propose_orders

from .engine_components import (
    ZERO,
    BacktestContext,
    BacktestDataLoader,
    BacktestRequest,
    BacktestResult,
    FrameBacktestData,
    OrderProposer,
    StrategyCallable,
    _PendingFundEntry,
)
from .engine_payloads import EnginePayloadMixin


class BacktestEngine(EnginePayloadMixin):
    """Run strategies, portfolio limits, and execution models in session order."""

    def __init__(
        self,
        data_loader: BacktestDataLoader,
        *,
        stock_execution: StockExecutionModel,
        strategy: StrategyCallable | None = None,
        strategies: Sequence[StrategyCallable] = (),
        stock_strategy: StrategyCallable | None = None,
        fund_strategy: StrategyCallable | None = None,
        fund_execution: FundExecutionModel | None = None,
        order_proposer: OrderProposer = propose_orders,
        policy: RiskPolicy | None = None,
        repository: BacktestRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(order_proposer):
            raise TypeError("order_proposer must be callable")
        callbacks = [*strategies]
        callbacks.extend(
            item for item in (strategy, stock_strategy, fund_strategy) if item is not None
        )
        if not callbacks or not all(callable(item) for item in callbacks):
            raise ValueError("at least one callable strategy is required")
        if not isinstance(stock_execution, StockExecutionModel):
            raise TypeError("stock_execution must be StockExecutionModel")
        if fund_execution is not None and not isinstance(fund_execution, FundExecutionModel):
            raise TypeError("fund_execution must be FundExecutionModel or None")
        self.data = data_loader
        self.strategies = tuple(callbacks)
        self.stock_execution = stock_execution
        self.fund_execution = fund_execution
        self.order_proposer = order_proposer
        self.policy = policy or RiskPolicy()
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(tz=SHANGHAI_TZ))

    def run(self, request: BacktestRequest) -> BacktestResult:
        if not isinstance(request, BacktestRequest):
            raise TypeError("request must be BacktestRequest")
        started_at = self._now()
        trading_dates = self.data.trading_dates(request.start, request.end)
        if not trading_dates:
            raise ValueError("no trading dates fall within the requested period")

        prepared_features = None
        if isinstance(self.data, FrameBacktestData) and any(
            getattr(callback, "requires_stock_features", False) for callback in self.strategies
        ):
            try:
                prepared_features = PreparedStockFeatures(self.data.prices)
            except ValueError:
                # Preserve the callback's explicit strategy_error limitation for
                # incomplete feature inputs, as with a generic prefix-only loader.
                prepared_features = None

        state = ExecutionState(
            cash=request.initial_cash,
            fund_fee_schedules=getattr(self.data, "fund_fee_schedules", {}),
        )
        pending_funds: list[_PendingFundEntry] = []
        order_rows: list[dict[str, object]] = []
        order_rows_by_id: dict[str, dict[str, object]] = {}
        fill_rows: list[dict[str, object]] = []
        position_rows: list[dict[str, object]] = []
        nav_rows: list[dict[str, object]] = []
        limitations = [*request.research_only_reasons, *getattr(self.data, "limitations", ())]
        used_snapshot_ids: set[int] = set()
        high_water_mark = Decimal("1")
        last_valid_marks: dict[str, Decimal] = {}

        for trade_date in trading_dates:
            state = self._release_stock_t_plus_one(state)
            if self.fund_execution is not None:
                state = self.fund_execution.release_cash(state, trade_date)

            session_buys = ZERO
            session_sells = ZERO
            session_fees = ZERO
            state, pending_funds, settled = self._settle_funds(
                pending_funds,
                state,
                trade_date,
                order_rows_by_id,
                limitations,
            )
            for fill, settled_on, order_id in settled:
                fill_rows.append(self._fill_row(fill, settled_on, order_id))
                session_fees += fill.fees
                if fill.action in {Action.BUY, Action.ADD}:
                    session_buys += fill.gross_amount
                else:
                    session_sells += fill.gross_amount

            signal_date = self.data.previous_trading_date(trade_date)
            if signal_date is not None:
                history = self.data.prices_through(signal_date)
                if not history.empty and max(history["trade_date"]) > signal_date:
                    raise RuntimeError("data loader exposed a future price row")
                instruments = self.data.instruments_as_of(signal_date)
                if "as_of_date" in instruments.columns:
                    instrument_dates = pd.to_datetime(instruments["as_of_date"], errors="coerce")
                    if instrument_dates.gt(pd.Timestamp(signal_date)).any():
                        raise RuntimeError("data loader exposed future instrument metadata")
                if "listed_on" in instruments.columns:
                    listed_dates = pd.to_datetime(instruments["listed_on"], errors="coerce")
                    if listed_dates.gt(pd.Timestamp(signal_date)).any():
                        raise RuntimeError("data loader exposed a future-listed instrument")
                if "delisted_on" in instruments.columns:
                    delisted_dates = pd.to_datetime(instruments["delisted_on"], errors="coerce")
                    if delisted_dates.le(pd.Timestamp(signal_date)).any():
                        raise RuntimeError("data loader exposed a delisted instrument")
                snapshot_id = self.data.snapshot_id(signal_date)
                if snapshot_id > 0:
                    used_snapshot_ids.add(snapshot_id)
                else:
                    limitations.append(f"missing_data_snapshot_id:{signal_date.isoformat()}")
                observed_signal_marks = self.data.marks_as_of(signal_date)
                held_symbols = {
                    symbol
                    for symbol, quantity in (
                        *state.stock_quantities.items(),
                        *state.fund_units.items(),
                    )
                    if quantity > ZERO
                }
                stale_held_symbols = held_symbols.difference(observed_signal_marks)
                marks = {**last_valid_marks, **observed_signal_marks}
                for symbol in sorted(stale_held_symbols):
                    limitations.append(
                        f"stale_decision_valuation:{symbol}:{signal_date.isoformat()}"
                    )
                last_valid_marks.update(observed_signal_marks)
                portfolio = self._portfolio_state(
                    state,
                    signal_date,
                    marks,
                    request.initial_cash,
                    high_water_mark,
                    limitations,
                )
                context = BacktestContext(
                    as_of_date=signal_date,
                    execution_date=trade_date,
                    prices=history.copy(deep=True),
                    instruments=instruments.copy(deep=True),
                    portfolio=portfolio,
                    execution_state=state,
                    parameters=request.parameters,
                    data_snapshot_id=snapshot_id,
                    stock_features=(
                        prepared_features.view(signal_date)
                        if prepared_features is not None
                        else None
                    ),
                )
                signals = self._signals(context, limitations)
                if stale_held_symbols:
                    signals = [
                        item for item in signals if item.action not in {Action.BUY, Action.ADD}
                    ]
                proposals = self._orders(
                    signals,
                    portfolio,
                    marks,
                    instruments,
                    snapshot_id,
                    limitations,
                )
                bars = self.data.bars_on(trade_date)
                current_statuses = self.data.fund_statuses_on(trade_date)
                state = replace(state, fund_statuses=current_statuses)

                (
                    state,
                    pending_funds,
                    proposal_buys,
                    proposal_sells,
                    proposal_fees,
                ) = self._execute_proposals(
                    proposals,
                    trade_date,
                    bars,
                    state,
                    pending_funds,
                    order_rows,
                    order_rows_by_id,
                    fill_rows,
                )
                session_buys += proposal_buys
                session_sells += proposal_sells
                session_fees += proposal_fees

            observed_marks = self.data.marks_as_of(trade_date)
            closing_marks = {**last_valid_marks, **observed_marks}
            held_symbols = {
                symbol
                for symbol, quantity in (
                    *state.stock_quantities.items(),
                    *state.fund_units.items(),
                )
                if quantity > ZERO
            }
            for symbol in sorted(held_symbols.difference(observed_marks)):
                limitations.append(f"stale_valuation:{symbol}:{trade_date.isoformat()}")
            last_valid_marks.update(observed_marks)
            total_value, day_positions = self._closing_positions(
                state, trade_date, closing_marks, limitations
            )
            snapshot_id = self.data.snapshot_id(trade_date)
            if snapshot_id > 0:
                used_snapshot_ids.add(snapshot_id)
            for row in day_positions:
                row["data_snapshot_id"] = snapshot_id or None
            position_rows.extend(day_positions)
            unit_nav = total_value / request.initial_cash
            high_water_mark = max(high_water_mark, unit_nav)
            nav_rows.append(
                {
                    "trade_date": trade_date,
                    "cash": state.cash,
                    "unavailable_cash": state.unavailable_cash,
                    "total_value": total_value,
                    "unit_nav": unit_nav,
                    "high_water_mark": high_water_mark,
                    "external_cash_flow": ZERO,
                    "buy_amount": session_buys,
                    "sell_amount": session_sells,
                    "fees": session_fees,
                    "data_snapshot_id": snapshot_id or None,
                }
            )

        if pending_funds:
            limitations.append("unsettled_fund_orders_at_backtest_end")
        limitations = list(dict.fromkeys(str(item) for item in limitations if str(item)))
        nav_frame = pd.DataFrame(nav_rows)
        orders_frame = self._frame(order_rows, self._ORDER_COLUMNS)
        fills_frame = self._frame(fill_rows, self._FILL_COLUMNS)
        positions_frame = self._frame(position_rows, self._POSITION_COLUMNS)
        result = BacktestResult(
            run_id=self._run_id(
                request,
                trading_dates,
                used_snapshot_ids,
                nav_frame,
                orders_frame,
                fills_frame,
                positions_frame,
                limitations,
                state,
                pending_funds,
            ),
            trading_dates=trading_dates,
            nav=nav_frame,
            orders=orders_frame,
            fills=fills_frame,
            positions=positions_frame,
            research_only=bool(limitations),
            limitations=tuple(limitations),
            snapshot_ids=tuple(sorted(used_snapshot_ids)),
            final_state=state,
        )
        completed_at = self._now()
        if self.repository is not None:
            self.repository.save_result(
                request,
                result,
                started_at=started_at,
                completed_at=completed_at,
            )
        return result

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value

    @staticmethod
    def _release_stock_t_plus_one(state: ExecutionState) -> ExecutionState:
        if not state.unsettled_stock:
            return state
        settled = dict(state.settled_stock)
        for symbol, quantity in state.unsettled_stock.items():
            settled[symbol] = settled.get(symbol, ZERO) + quantity
        return replace(state, settled_stock=settled, unsettled_stock={})

    def _settle_funds(
        self,
        pending_orders: Sequence[_PendingFundEntry],
        state: ExecutionState,
        settlement_date: date,
        order_rows: Mapping[str, dict[str, object]],
        limitations: list[str],
    ) -> tuple[
        ExecutionState,
        list[_PendingFundEntry],
        list[tuple[Fill, date, str]],
    ]:
        if self.fund_execution is None:
            return state, list(pending_orders), []
        remaining: list[_PendingFundEntry] = []
        fills: list[tuple[Fill, date, str]] = []
        ordered = sorted(
            pending_orders,
            key=lambda item: (
                item.pending.confirm_on,
                item.pending.submitted_on,
                item.pending.order.symbol,
                item.order_id,
            ),
        )
        for entry in ordered:
            pending = entry.pending
            if pending.confirm_on > settlement_date:
                remaining.append(entry)
                continue
            nav = self.data.fund_nav_for(pending.order.symbol, pending.nav_date, settlement_date)
            if nav is None:
                remaining.append(entry)
                continue
            try:
                outcome = self.fund_execution.settle(pending, nav, settlement_date, state)
            except (TypeError, ValueError) as exc:
                state = self._cancel_fund_reservation(pending, state)
                row = order_rows.get(entry.order_id)
                if row is not None:
                    row["status"] = "rejected"
                    row["settlement_date"] = settlement_date
                    row["rejection_code"] = "fund_settlement_error"
                    row["rejection_reason"] = str(exc)
                limitations.append(
                    f"fund_settlement_rejected:{pending.order.symbol}:fund_settlement_error"
                )
                continue
            state = outcome.state
            row = order_rows.get(entry.order_id)
            if outcome.fill is not None:
                fills.append((outcome.fill, settlement_date, entry.order_id))
                if row is not None:
                    row["status"] = "filled"
                    row["settlement_date"] = settlement_date
            else:
                rejection = outcome.rejection
                temporary = rejection is not None and rejection.code in {
                    "confirmation_pending",
                    "invalid_nav",
                    "non_trading_day",
                }
                if temporary:
                    remaining.append(entry)
                    continue
                state = self._cancel_fund_reservation(pending, state)
                if row is not None and rejection is not None:
                    row["status"] = "rejected"
                    row["settlement_date"] = settlement_date
                    row["rejection_code"] = rejection.code
                    row["rejection_reason"] = rejection.reason
                if rejection is not None:
                    limitations.append(
                        f"fund_settlement_rejected:{pending.order.symbol}:{rejection.code}"
                    )
        return state, remaining, fills

    @staticmethod
    def _cancel_fund_reservation(
        pending: PendingFundOrder, state: ExecutionState
    ) -> ExecutionState:
        return FundExecutionModel.cancel_reservation(pending, state)

    def _signals(self, context: BacktestContext, limitations: list[str]) -> list[Signal]:
        signals: list[Signal] = []
        # Price and instrument frames were detached once at the context boundary.
        # Callbacks share that past-only context by a read-only contract, avoiding
        # an O(strategy × rows) deep-copy fan-out without mutating the source.
        for callback in self.strategies:
            try:
                generated = callback(context)
            except Exception as exc:  # pragma: no cover - exact strategy failures vary
                limitations.append(f"strategy_error:{type(exc).__name__}")
                continue
            if generated is None:
                limitations.append("strategy_returned_none")
                continue
            for signal in generated:
                if not isinstance(signal, Signal):
                    limitations.append("strategy_returned_invalid_signal")
                    continue
                if signal.as_of_date != context.as_of_date:
                    limitations.append(f"invalid_signal_date:{signal.symbol}")
                    continue
                signals.append(signal)
        return signals

    def _orders(
        self,
        signals: Sequence[Signal],
        portfolio: PortfolioState,
        marks: Mapping[str, Decimal],
        instruments: pd.DataFrame,
        snapshot_id: int,
        limitations: list[str],
    ) -> list[ProposedOrder]:
        try:
            orders = self.order_proposer(
                signals,
                portfolio,
                marks,
                instruments,
                self.policy,
                snapshot_id,
            )
        except Exception as exc:  # pragma: no cover - custom proposers are user supplied
            limitations.append(f"order_proposer_error:{type(exc).__name__}")
            return []
        valid: list[ProposedOrder] = []
        for order in orders:
            if not isinstance(order, ProposedOrder):
                limitations.append("order_proposer_returned_invalid_order")
                continue
            valid.append(order)
        return valid

    def _execute_proposals(
        self,
        proposals: Sequence[ProposedOrder],
        trade_date: date,
        bars: Mapping[str, object],
        state: ExecutionState,
        pending_funds: list[_PendingFundEntry],
        order_rows: list[dict[str, object]],
        order_rows_by_id: dict[str, dict[str, object]],
        fill_rows: list[dict[str, object]],
    ) -> tuple[ExecutionState, list[_PendingFundEntry], Decimal, Decimal, Decimal]:
        """Execute one session's proposals and return state plus session totals."""

        buys = ZERO
        sells = ZERO
        fees = ZERO
        next_pending = list(pending_funds)
        for order_sequence, order in enumerate(proposals, start=1):
            order_id = self._order_id(order, trade_date, order_sequence)
            if order.asset_type is AssetType.STOCK:
                bar = bars.get(order.symbol)
                if bar is None:
                    row = self._order_row(
                        order,
                        order_id,
                        trade_date,
                        status="rejected",
                        rejection_code="missing_execution_bar",
                        rejection_reason="缺少完整执行日行情或交易状态",
                    )
                    order_rows.append(row)
                    order_rows_by_id[order_id] = row
                    continue
                outcome = self.stock_execution.execute(order, bar, state)
                state = outcome.state
                row = self._execution_order_row(order, order_id, trade_date, outcome.rejection)
                order_rows.append(row)
                order_rows_by_id[order_id] = row
                if outcome.fill is not None:
                    fill_rows.append(self._fill_row(outcome.fill, trade_date, order_id))
                    fees += outcome.fill.fees
                    if outcome.fill.action in {Action.BUY, Action.ADD}:
                        buys += outcome.fill.gross_amount
                    else:
                        sells += outcome.fill.gross_amount
                continue

            if order.asset_type is AssetType.FUND:
                row, pending, state = self._submit_fund(order, order_id, trade_date, state)
                order_rows.append(row)
                order_rows_by_id[order_id] = row
                if pending is not None:
                    next_pending.append(_PendingFundEntry(pending, order_id))
                continue

            row = self._order_row(
                order,
                order_id,
                trade_date,
                status="rejected",
                rejection_code="unsupported_asset_type",
                rejection_reason="回测执行器不支持该资产类型",
            )
            order_rows.append(row)
            order_rows_by_id[order_id] = row
        return state, next_pending, buys, sells, fees

    def _submit_fund(
        self,
        order: ProposedOrder,
        order_id: str,
        submitted_on: date,
        state: ExecutionState,
    ) -> tuple[dict[str, object], PendingFundOrder | None, ExecutionState]:
        if self.fund_execution is None:
            return (
                self._order_row(
                    order,
                    order_id,
                    submitted_on,
                    status="rejected",
                    rejection_code="fund_execution_unavailable",
                    rejection_reason="未配置基金执行模型",
                ),
                None,
                state,
            )
        try:
            pending = self.fund_execution.submit(order, submitted_on, state)
            reserved = self.fund_execution.reserve(pending, state)
        except FundOrderRejected as exc:
            return (
                self._order_row(
                    order,
                    order_id,
                    submitted_on,
                    status="rejected",
                    rejection_code=exc.code,
                    rejection_reason=exc.reason,
                ),
                None,
                state,
            )
        except (TypeError, ValueError) as exc:
            return (
                self._order_row(
                    order,
                    order_id,
                    submitted_on,
                    status="rejected",
                    rejection_code="fund_reservation_failed",
                    rejection_reason=str(exc),
                ),
                None,
                state,
            )
        row = self._order_row(order, order_id, submitted_on, status="pending")
        row["settlement_date"] = pending.confirm_on
        return row, pending, reserved


__all__ = [
    "BacktestContext",
    "BacktestDataLoader",
    "BacktestEngine",
    "BacktestRequest",
    "BacktestResult",
    "FrameBacktestData",
]
