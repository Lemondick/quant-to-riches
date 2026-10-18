"""Convert capital-independent signals into deterministic proposed orders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from math import isfinite
from typing import Any

import pandas as pd

from quant_assistant.common.serialization import flag
from quant_assistant.models.entities import Action, AssetType, Position, ProposedOrder, Signal
from quant_assistant.portfolio.aggregation import aggregate_signals
from quant_assistant.portfolio.models import PortfolioState, RiskPolicy
from quant_assistant.portfolio.risk import (
    AssetBucket,
    classify_fund_bucket,
    decimal_or_none,
    floor_money,
    is_risk_increasing,
    is_risk_reducing,
    portfolio_drawdown,
    signal_is_blocked,
    signal_is_expired,
)

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


@dataclass(slots=True)
class _PositionView:
    asset_type: AssetType
    quantity: Decimal
    market_value: Decimal


@dataclass(slots=True)
class _Ledger:
    values: dict[str, Decimal]
    quantities: dict[str, Decimal]
    buckets: dict[AssetBucket, Decimal]
    industries: dict[str, Decimal]
    available_cash: Decimal
    turnover: Decimal
    unknown_fund_buckets: set[str]
    unknown_stock_industries: set[str]


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _action(signal: Signal) -> Action | None:
    try:
        return signal.action if isinstance(signal.action, Action) else Action(signal.action)
    except (TypeError, ValueError):
        return None


def _asset_type(value: object) -> AssetType | None:
    try:
        return value if isinstance(value, AssetType) else AssetType(str(value))
    except (TypeError, ValueError):
        return None


def _instrument_records(instruments: pd.DataFrame) -> tuple[dict[str, dict[str, Any]], set[str]]:
    if not isinstance(instruments, pd.DataFrame) or "symbol" not in instruments.columns:
        return {}, set()
    records: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for raw in instruments.to_dict(orient="records"):
        symbol = str(raw.get("symbol", "")).strip()
        if not symbol:
            continue
        if symbol in records:
            duplicates.add(symbol)
            continue
        records[symbol] = raw
    return records, duplicates


def _industry(record: Mapping[str, Any] | None) -> str | None:
    if not record:
        return None
    value = record.get("industry")
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() in {"nan", "none", "<na>"} else text


def _price(prices: Mapping[str, Decimal], symbol: str) -> Decimal | None:
    value = decimal_or_none(prices.get(symbol))
    return value if value is not None and value > ZERO else None


def _score(signal: Signal) -> float | None:
    try:
        value = float(signal.score)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _flag(value: object) -> bool | None:
    return flag(value, default=None)


def _record_blocks_increase(record: Mapping[str, Any] | None, asset_type: AssetType) -> bool:
    if not record:
        return False
    status = record.get("data_status", record.get("status"))
    if status is not None and str(getattr(status, "value", status)).strip().lower() in {
        "blocked",
        "stale",
        "incomplete",
    }:
        return True
    if asset_type is AssetType.STOCK:
        return _flag(record.get("is_st")) is True or _flag(record.get("is_suspended")) is True
    if asset_type is AssetType.FUND and "is_purchasable" in record:
        return _flag(record.get("is_purchasable")) is False
    return False


def _position_views(positions: Sequence[Position]) -> tuple[dict[str, _PositionView], bool]:
    views: dict[str, _PositionView] = {}
    complete = True
    for position in positions:
        asset_type = _asset_type(position.asset_type)
        quantity = decimal_or_none(position.quantity)
        market_value = decimal_or_none(position.market_value)
        symbol = str(position.symbol).strip()
        if (
            not symbol
            or asset_type is None
            or quantity is None
            or market_value is None
            or quantity < ZERO
            or market_value < ZERO
            or (
                asset_type in {AssetType.STOCK, AssetType.FUND}
                and ((market_value > ZERO) != (quantity > ZERO))
            )
        ):
            complete = False
            continue
        existing = views.get(symbol)
        if existing is None:
            views[symbol] = _PositionView(asset_type, quantity, market_value)
        elif existing.asset_type is asset_type:
            existing.quantity += quantity
            existing.market_value += market_value
        else:
            complete = False
    return views, complete


def _choose_signals(signals: Sequence[Signal]) -> list[Signal]:
    """Select one deterministic, risk-first final signal per symbol."""
    return list(aggregate_signals(signals).signals)


def _signal_metadata(signals: Sequence[Signal]) -> dict[str, Signal]:
    """Retain non-order signals because their reasons can carry fund-bucket metadata."""

    metadata: dict[str, Signal] = {}
    ordered = sorted(
        signals,
        key=lambda signal: (
            str(getattr(signal, "symbol", "")),
            str(getattr(signal, "strategy_version", "")),
            str(getattr(signal, "valid_until", "")),
        ),
    )
    for signal in ordered:
        symbol = str(getattr(signal, "symbol", "")).strip()
        if not symbol:
            continue
        current = metadata.get(symbol)
        if current is None or (
            classify_fund_bucket(None, current) is None
            and classify_fund_bucket(None, signal) is not None
        ):
            metadata[symbol] = signal
    return metadata


def _bucket_for(
    asset_type: AssetType,
    record: Mapping[str, Any] | None,
    signal: Signal | None,
) -> AssetBucket | None:
    if asset_type is AssetType.STOCK:
        return AssetBucket.STOCK
    if asset_type is AssetType.FUND:
        return classify_fund_bucket(record, signal)
    return None


def _bucket_limit(bucket: AssetBucket, policy: RiskPolicy) -> Decimal:
    if bucket is AssetBucket.STOCK:
        return policy.max_stock_bucket
    if bucket is AssetBucket.CORE_FUND:
        return policy.max_core_fund_bucket
    return policy.max_rotation_fund_bucket


def _bucket_reason(bucket: AssetBucket, policy: RiskPolicy) -> str:
    percent = _percent(_bucket_limit(bucket, policy))
    label = {
        AssetBucket.STOCK: "股票资产桶",
        AssetBucket.CORE_FUND: "核心基金资产桶",
        AssetBucket.ROTATION_FUND: "轮动基金资产桶",
    }[bucket]
    return f"{label}上限为{percent}%，建议金额已缩减"


def _percent(value: Decimal) -> str:
    percent = (value * Decimal("100")).normalize()
    return format(percent, "f")


def _build_ledger(
    state: PortfolioState,
    positions: Mapping[str, _PositionView],
    records: Mapping[str, Mapping[str, Any]],
    signals_by_symbol: Mapping[str, Signal],
) -> _Ledger:
    values = {symbol: position.market_value for symbol, position in positions.items()}
    quantities = {symbol: position.quantity for symbol, position in positions.items()}
    buckets = {bucket: ZERO for bucket in AssetBucket}
    industries: dict[str, Decimal] = {}
    unknown_fund_buckets: set[str] = set()
    unknown_stock_industries: set[str] = set()

    for symbol, position in positions.items():
        record = records.get(symbol)
        bucket = _bucket_for(position.asset_type, record, signals_by_symbol.get(symbol))
        if (
            position.asset_type is AssetType.FUND
            and bucket is None
            and position.market_value > ZERO
        ):
            unknown_fund_buckets.add(symbol)
        elif bucket is not None:
            buckets[bucket] += position.market_value

        if position.asset_type is AssetType.STOCK and position.market_value > ZERO:
            industry = _industry(record)
            if industry is None:
                unknown_stock_industries.add(symbol)
            else:
                industries[industry] = industries.get(industry, ZERO) + position.market_value

    return _Ledger(
        values=values,
        quantities=quantities,
        buckets=buckets,
        industries=industries,
        available_cash=state.cash,
        turnover=ZERO,
        unknown_fund_buckets=unknown_fund_buckets,
        unknown_stock_industries=unknown_stock_industries,
    )


def _state_allows_increase(
    state: PortfolioState,
    positions_complete: bool,
    data_snapshot_id: int,
) -> bool:
    accounted_positions = [decimal_or_none(position.market_value) for position in state.positions]
    if any(value is None for value in accounted_positions):
        return False
    accounted_value = state.cash + sum(
        (value for value in accounted_positions if value is not None), start=ZERO
    )
    return (
        positions_complete
        and state.total_value > ZERO
        and state.cash >= ZERO
        and abs(accounted_value - state.total_value) <= Decimal("0.01")
        and portfolio_drawdown(state) is not None
        and isinstance(data_snapshot_id, int)
        and not isinstance(data_snapshot_id, bool)
        and data_snapshot_id > 0
        and all(position.as_of_date <= state.as_of_date for position in state.positions)
    )


def _reduce_to_limit(amount: Decimal, limit: Decimal, reasons: list[str], reason: str) -> Decimal:
    allowed = max(ZERO, limit)
    if amount > allowed:
        _append_reason(reasons, reason)
        return allowed
    return amount


def _reduction_order(
    signal: Signal,
    state: PortfolioState,
    prices: Mapping[str, Decimal],
    records: Mapping[str, Mapping[str, Any]],
    ledger: _Ledger,
    policy: RiskPolicy,
    data_snapshot_id: int,
    drawdown: Decimal | None,
) -> ProposedOrder | None:
    action = _action(signal)
    if action is None or not is_risk_reducing(action):
        return None
    position_value = ledger.values.get(signal.symbol, ZERO)
    position_quantity = ledger.quantities.get(signal.symbol, ZERO)
    if position_value <= ZERO or position_quantity < ZERO:
        return None

    reasons = list(signal.reasons)
    expired = signal_is_expired(signal, state)
    blocked = signal_is_blocked(signal, state)
    if expired:
        _append_reason(reasons, "信号已过期，仅保留降低风险的建议")
    if blocked:
        _append_reason(reasons, "输入已阻断，仅允许降低风险")
    if drawdown is None:
        _append_reason(reasons, "组合净值或历史高水位无效，仅允许降低风险")
    elif drawdown >= policy.stop_drawdown:
        _append_reason(reasons, "组合回撤达到止损阈值，仅允许降低风险")

    target_weight = ZERO
    requested_amount = position_value
    if action is Action.REDUCE:
        parsed_target = decimal_or_none(signal.target_weight)
        if parsed_target is None:
            return None
        target_weight = min(Decimal("1"), max(ZERO, parsed_target))
        target_value = target_weight * state.total_value
        if target_value >= position_value:
            return None
        requested_amount = position_value - target_value

    position = next((item for item in state.positions if item.symbol == signal.symbol), None)
    asset_type = _asset_type(position.asset_type if position is not None else signal.asset_type)
    if asset_type not in {AssetType.STOCK, AssetType.FUND}:
        return None

    price = _price(prices, signal.symbol)
    if price is None and position_quantity > ZERO:
        price = position_value / position_quantity

    quantity: Decimal | None
    amount: Decimal
    if asset_type is AssetType.STOCK:
        if action is Action.SELL:
            quantity = position_quantity
            amount = floor_money(position_value)
            if quantity % ONE_HUNDRED:
                _append_reason(reasons, "清仓允许卖出持仓中的零股")
        else:
            if price is None:
                return None
            quantity = (requested_amount / price / ONE_HUNDRED).to_integral_value(
                rounding=ROUND_DOWN
            ) * ONE_HUNDRED
            sellable_lots = (position_quantity / ONE_HUNDRED).to_integral_value(
                rounding=ROUND_DOWN
            ) * ONE_HUNDRED
            quantity = min(quantity, sellable_lots)
            if quantity <= ZERO:
                return None
            amount = floor_money(quantity * price)
            if amount < floor_money(requested_amount):
                _append_reason(reasons, "A股按100股一手向下取整")
    else:
        amount = floor_money(requested_amount)
        if amount <= ZERO:
            return None
        if action is Action.SELL:
            quantity = position_quantity
        elif position_value > ZERO:
            quantity = (position_quantity * amount / position_value).quantize(
                Decimal("0.00000001"), rounding=ROUND_DOWN
            )
        else:
            quantity = None

    if amount <= ZERO:
        return None

    bucket = _bucket_for(asset_type, records.get(signal.symbol), signal)
    industry = _industry(records.get(signal.symbol)) if asset_type is AssetType.STOCK else None
    ledger.values[signal.symbol] = max(ZERO, position_value - amount)
    if quantity is not None:
        ledger.quantities[signal.symbol] = max(ZERO, position_quantity - quantity)
    if bucket is not None:
        ledger.buckets[bucket] = max(ZERO, ledger.buckets[bucket] - amount)
    if industry is not None:
        ledger.industries[industry] = max(ZERO, ledger.industries.get(industry, ZERO) - amount)
    if ledger.values[signal.symbol] == ZERO:
        ledger.unknown_fund_buckets.discard(signal.symbol)
        ledger.unknown_stock_industries.discard(signal.symbol)

    turnover_limit = policy.max_daily_turnover * state.total_value
    if ledger.turnover + amount > turnover_limit:
        _append_reason(reasons, "降低风险的订单优先，豁免单日换手上限")
    ledger.turnover += amount
    # A proposal is not a fill. Do not finance later BUY proposals with stock
    # sale proceeds or fund redemption cash that has not actually settled.
    _append_reason(reasons, "卖出或减仓用于降低组合风险，不受持仓上限阻止")

    return ProposedOrder(
        symbol=signal.symbol,
        asset_type=asset_type,
        action=action,
        amount=amount,
        quantity=quantity,
        target_weight=target_weight,
        reasons=tuple(reasons),
        as_of_date=signal.as_of_date,
        valid_until=signal.valid_until,
        data_snapshot_id=data_snapshot_id,
        strategy_version=signal.strategy_version,
    )


def _increase_order(
    signal: Signal,
    state: PortfolioState,
    prices: Mapping[str, Decimal],
    records: Mapping[str, Mapping[str, Any]],
    duplicate_records: set[str],
    ledger: _Ledger,
    policy: RiskPolicy,
    data_snapshot_id: int,
    drawdown: Decimal | None,
    state_complete: bool,
) -> ProposedOrder | None:
    action = _action(signal)
    if action is None or not is_risk_increasing(action):
        return None

    # 1. Expired, blocked, future, and incomplete inputs never add risk.
    if signal_is_expired(signal, state) or signal_is_blocked(signal, state) or not state_complete:
        return None
    if signal.symbol in duplicate_records:
        return None

    asset_type = _asset_type(signal.asset_type)
    if asset_type not in {AssetType.STOCK, AssetType.FUND}:
        return None
    record = records.get(signal.symbol)
    record_asset_type = _asset_type(record.get("asset_type")) if record else None
    if record_asset_type is not None and record_asset_type is not asset_type:
        return None
    if _record_blocks_increase(record, asset_type):
        return None

    # 2. At the stop threshold, only risk-reducing orders survive.
    if drawdown is None or drawdown >= policy.stop_drawdown:
        return None

    target_weight = decimal_or_none(signal.target_weight)
    if target_weight is None or target_weight <= ZERO:
        return None
    target_weight = min(Decimal("1"), target_weight)
    reasons = list(signal.reasons)

    # 3. Warning drawdown scales the target risk budget before exposure caps.
    if drawdown >= policy.warning_drawdown:
        target_weight *= Decimal("0.5")
        warning_percent = _percent(policy.warning_drawdown)
        _append_reason(reasons, f"组合回撤达到{warning_percent}%警戒线，新增目标仓位减半")

    current_value = ledger.values.get(signal.symbol, ZERO)
    requested_amount = target_weight * state.total_value - current_value
    if requested_amount <= ZERO:
        return None

    bucket = _bucket_for(asset_type, record, signal)
    if bucket is None:
        return None
    if asset_type is AssetType.FUND and ledger.unknown_fund_buckets:
        return None
    if asset_type is AssetType.STOCK and ledger.unknown_stock_industries:
        return None

    amount = requested_amount

    # 4. Asset-bucket cap.
    bucket_headroom = _bucket_limit(bucket, policy) * state.total_value - ledger.buckets[bucket]
    amount = _reduce_to_limit(amount, bucket_headroom, reasons, _bucket_reason(bucket, policy))

    # 5. Single-name cap.
    single_limit = (
        policy.max_single_stock if asset_type is AssetType.STOCK else policy.max_single_fund
    )
    single_label = "股票" if asset_type is AssetType.STOCK else "基金"
    single_headroom = single_limit * state.total_value - current_value
    amount = _reduce_to_limit(
        amount,
        single_headroom,
        reasons,
        f"单只{single_label}上限为{_percent(single_limit)}%，建议金额已缩减",
    )

    # 6. Industry cap. Missing mappings fail closed above.
    industry: str | None = None
    if asset_type is AssetType.STOCK:
        industry = _industry(record)
        if industry is None:
            return None
        industry_headroom = policy.max_industry * state.total_value - ledger.industries.get(
            industry, ZERO
        )
        amount = _reduce_to_limit(
            amount,
            industry_headroom,
            reasons,
            f"{industry}行业上限为{_percent(policy.max_industry)}%，建议金额已缩减",
        )

    # 7. Preserve the minimum cash/low-risk bucket.
    cash_headroom = ledger.available_cash - policy.min_cash_bucket * state.total_value
    amount = _reduce_to_limit(
        amount,
        cash_headroom,
        reasons,
        f"至少保留{_percent(policy.min_cash_bucket)}%现金，建议金额已缩减",
    )

    # 8. Daily turnover includes prior sells, although de-risking sells bypass it.
    turnover_headroom = policy.max_daily_turnover * state.total_value - ledger.turnover
    amount = _reduce_to_limit(
        amount,
        turnover_headroom,
        reasons,
        f"单日换手上限为{_percent(policy.max_daily_turnover)}%，建议金额已缩减",
    )
    if amount <= ZERO:
        return None

    # 9. A-share buys are whole 100-share lots.
    quantity: Decimal | None = None
    constrained_target_weight = (current_value + amount) / state.total_value
    if asset_type is AssetType.STOCK:
        price = _price(prices, signal.symbol)
        if price is None:
            return None
        quantity = (amount / price / ONE_HUNDRED).to_integral_value(
            rounding=ROUND_DOWN
        ) * ONE_HUNDRED
        if quantity <= ZERO:
            return None
        rounded_amount = floor_money(quantity * price)
        if rounded_amount < floor_money(amount):
            _append_reason(reasons, "A股按100股一手向下取整")
        amount = rounded_amount
    else:
        amount = floor_money(amount)

    # 10. Do not rely on more cash than is currently/projectedly available.
    amount_before_cash = amount
    amount = min(amount, floor_money(ledger.available_cash))
    if amount < amount_before_cash:
        _append_reason(reasons, "可用现金不足，建议金额已缩减")
        constrained_target_weight = (current_value + amount) / state.total_value
        if asset_type is AssetType.STOCK:
            price = _price(prices, signal.symbol)
            if price is None:
                return None
            quantity = (amount / price / ONE_HUNDRED).to_integral_value(
                rounding=ROUND_DOWN
            ) * ONE_HUNDRED
            if quantity <= ZERO:
                return None
            amount = floor_money(quantity * price)

    if amount <= ZERO:
        return None

    ledger.values[signal.symbol] = current_value + amount
    if quantity is not None:
        ledger.quantities[signal.symbol] = ledger.quantities.get(signal.symbol, ZERO) + quantity
    ledger.buckets[bucket] += amount
    if industry is not None:
        ledger.industries[industry] = ledger.industries.get(industry, ZERO) + amount
    ledger.available_cash -= amount
    ledger.turnover += amount

    return ProposedOrder(
        symbol=signal.symbol,
        asset_type=asset_type,
        action=action,
        amount=amount,
        quantity=quantity,
        target_weight=constrained_target_weight,
        reasons=tuple(reasons),
        as_of_date=signal.as_of_date,
        valid_until=signal.valid_until,
        data_snapshot_id=data_snapshot_id,
        strategy_version=signal.strategy_version,
    )


def propose_orders(
    signals: Sequence[Signal],
    state: PortfolioState,
    prices: Mapping[str, Decimal],
    instruments: pd.DataFrame,
    policy: RiskPolicy,
    data_snapshot_id: int,
    *,
    strategy_weights: Mapping[str, object] | None = None,
) -> list[ProposedOrder]:
    """Apply the locked constraints and return deterministic proposed orders.

    Risk-reducing signals are evaluated first so they cannot be prevented by
    exposure, cash, drawdown, or turnover ceilings.  Risk-increasing signals are
    then prioritised by score and symbol, independent of caller input order.
    """

    selected = (
        _choose_signals(signals)
        if strategy_weights is None
        else list(aggregate_signals(signals, strategy_weights=strategy_weights).signals)
    )
    records, duplicate_records = _instrument_records(instruments)
    positions, positions_complete = _position_views(state.positions)
    metadata_by_symbol = _signal_metadata(signals)
    ledger = _build_ledger(state, positions, records, metadata_by_symbol)
    drawdown = portfolio_drawdown(state)
    state_complete = _state_allows_increase(state, positions_complete, data_snapshot_id)

    reductions = sorted(
        (signal for signal in selected if is_risk_reducing(_action(signal))),
        key=lambda signal: (0 if _action(signal) is Action.SELL else 1, signal.symbol),
    )
    increases = sorted(
        (signal for signal in selected if is_risk_increasing(_action(signal))),
        key=lambda signal: (-(_score(signal) or 0.0), signal.symbol),
    )

    orders: list[ProposedOrder] = []
    for signal in reductions:
        order = _reduction_order(
            signal,
            state,
            prices,
            records,
            ledger,
            policy,
            data_snapshot_id,
            drawdown,
        )
        if order is not None:
            orders.append(order)
    for signal in increases:
        order = _increase_order(
            signal,
            state,
            prices,
            records,
            duplicate_records,
            ledger,
            policy,
            data_snapshot_id,
            drawdown,
            state_complete,
        )
        if order is not None:
            orders.append(order)
    return orders


__all__ = ["propose_orders"]
