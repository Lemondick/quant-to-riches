"""Portfolio construction and NAV continuity calculations."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from quant_assistant.common.dates import optional_date, optional_timestamp
from quant_assistant.common.serialization import canonical_json, decimal_coerce
from quant_assistant.models.entities import AssetType, Position
from quant_assistant.portfolio.models import PortfolioState, PortfolioValuationDraft

_ZERO = Decimal("0")


def _build_portfolio(
    rows: pd.DataFrame,
    daily_prices: pd.DataFrame,
    fund_nav: pd.DataFrame,
    as_of_date: date,
    previous_valuation: pd.DataFrame,
    baseline_event: pd.DataFrame,
    confirmed_flow_events: pd.DataFrame,
    pending_events: pd.DataFrame,
    late_events: pd.DataFrame,
    latest_manual_activity: object,
) -> tuple[
    tuple[Position, ...],
    PortfolioState,
    PortfolioValuationDraft,
    dict[str, Decimal],
    list[str],
]:
    marks, mark_dates = _latest_marks(daily_prices, fund_nav)
    positions: list[Position] = []
    blockers: list[str] = []
    cash = _ZERO
    for row in rows.to_dict("records"):
        try:
            asset_type = AssetType(str(row["asset_type"]))
            quantity = _decimal(row["quantity"])
            average_cost = _decimal(row["average_cost"])
            stored_value = _decimal(row["market_value"])
        except (KeyError, ValueError, TypeError):
            blockers.append(f"{row.get('symbol', '未知标的')}持仓记录无效")
            continue
        symbol = str(row["symbol"])
        position_date = _to_date(row["as_of_date"])
        if position_date is None or position_date > as_of_date:
            blockers.append(f"{symbol}持仓日期晚于策略日期或无效")
            continue
        if asset_type is AssetType.CASH:
            cash += stored_value
            continue
        mark = marks.get(symbol)
        market_value = stored_value if mark is None else quantity * mark
        if mark is None and quantity > 0:
            blockers.append(f"{symbol}缺少策略日期可用估值")
        elif quantity > 0 and mark_dates.get(symbol) != as_of_date:
            blockers.append(f"{symbol}估值未更新到策略日期")
        positions.append(
            Position(
                symbol=symbol,
                asset_type=asset_type,
                quantity=quantity,
                average_cost=average_cost,
                market_value=market_value,
                as_of_date=position_date,
            )
        )
    total_value = cash + sum((item.market_value for item in positions), _ZERO)
    if total_value <= 0:
        blockers.append("组合总资产必须为正且需要本地现金持仓")
    ordered_positions = tuple(sorted(positions, key=lambda item: item.symbol))
    positions_fingerprint = _portfolio_positions_fingerprint(ordered_positions, cash)
    unit_nav, high_water_mark, valuation, nav_blockers = _portfolio_nav_state(
        total_value,
        previous_valuation,
        baseline_event,
        confirmed_flow_events,
        pending_events,
        late_events,
        latest_manual_activity,
        positions_fingerprint,
    )
    blockers.extend(nav_blockers)
    if blockers:
        valuation = replace(valuation, valid=False)
    portfolio = PortfolioState(
        total_value=total_value,
        cash=cash,
        unit_nav=unit_nav,
        high_water_mark=high_water_mark,
        positions=ordered_positions,
        as_of_date=as_of_date,
    )
    return portfolio.positions, portfolio, valuation, marks, blockers


def _portfolio_nav_state(
    total_value: Decimal,
    previous_valuation: pd.DataFrame,
    baseline_event: pd.DataFrame,
    confirmed_flow_events: pd.DataFrame,
    pending_events: pd.DataFrame,
    late_events: pd.DataFrame,
    latest_manual_activity: object,
    positions_fingerprint: str,
) -> tuple[Decimal, Decimal, PortfolioValuationDraft, list[str]]:
    """Continue cash-flow-adjusted NAV across a reconciled local double-entry ledger."""

    flow_fingerprint = _portfolio_events_fingerprint(confirmed_flow_events)
    invalid = PortfolioValuationDraft(
        net_external_flow=_ZERO,
        previous_run_id=None,
        positions_fingerprint=positions_fingerprint,
        events_fingerprint=flow_fingerprint,
        is_baseline=previous_valuation.empty,
        valid=False,
    )

    if total_value <= 0:
        return _ZERO, _ZERO, invalid, []
    if not pending_events.empty:
        event_ids = ",".join(pending_events["event_id"].astype(str).tolist())
        return _ZERO, _ZERO, invalid, [f"存在待确认组合事件：{event_ids}"]
    if previous_valuation.empty:
        if baseline_event.empty:
            return _ZERO, _ZERO, invalid, ["组合净值尚未显式确认初始基线，禁止新增风险"]
        baseline_recorded_at = _utc_timestamp(baseline_event.iloc[0].get("recorded_at"))
        latest_activity_at = _utc_timestamp(latest_manual_activity)
        if baseline_recorded_at is None or (
            latest_activity_at is not None and latest_activity_at > baseline_recorded_at
        ):
            return _ZERO, _ZERO, invalid, ["初始基线确认后持仓又发生未对账变更，请重新建档"]
        baseline_fingerprint = _portfolio_events_fingerprint(baseline_event)
        draft = replace(
            invalid,
            events_fingerprint=baseline_fingerprint,
            is_baseline=True,
            valid=True,
        )
        return Decimal("1"), Decimal("1"), draft, []

    row = previous_valuation.iloc[0]
    try:
        previous_run_id = str(row["run_id"])
        previous_total = _decimal(row["total_value"])
        previous_nav = _decimal(row["unit_nav"])
        previous_high = _decimal(row["high_water_mark"])
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return _ZERO, _ZERO, invalid, ["历史组合净值记录无效，禁止新增风险"]
    invalid = replace(invalid, previous_run_id=previous_run_id, is_baseline=False)
    if (
        previous_total <= 0
        or previous_nav <= 0
        or previous_high <= 0
        or previous_high < previous_nav
    ):
        return _ZERO, _ZERO, invalid, ["历史组合净值记录无效，禁止新增风险"]

    previous_generated_at = _utc_timestamp(row.get("generated_at"))
    latest_activity_at = _utc_timestamp(latest_manual_activity)
    if previous_generated_at is None or (
        latest_activity_at is not None and latest_activity_at > previous_generated_at
    ):
        return (
            previous_nav,
            previous_high,
            invalid,
            ["上次组合估值后存在未对账的持仓覆盖，净值序列已冻结"],
        )
    if not late_events.empty:
        event_ids = ",".join(late_events["event_id"].astype(str).tolist())
        return previous_nav, previous_high, invalid, [f"存在晚到组合事件：{event_ids}"]

    net_external_flow = _ZERO
    flow_blockers: list[str] = []
    for event in confirmed_flow_events.to_dict("records"):
        amount = _decimal_or_none(event.get("amount"))
        event_type = str(event.get("event_type"))
        if amount is None:
            flow_blockers.append(f"{event.get('event_id')}资金流金额无效")
            continue
        if (event_type == "deposit" and amount <= 0) or (
            event_type == "withdrawal" and amount >= 0
        ):
            flow_blockers.append(f"{event.get('event_id')}资金流方向无效")
            continue
        net_external_flow += amount
    if flow_blockers:
        return previous_nav, previous_high, invalid, flow_blockers

    adjusted_value = total_value - net_external_flow
    if adjusted_value <= 0:
        return previous_nav, previous_high, invalid, ["扣除外部资金流后的组合价值无效"]
    unit_nav = previous_nav * adjusted_value / previous_total
    if unit_nav <= 0:
        return previous_nav, previous_high, invalid, ["组合净值计算结果无效，禁止新增风险"]
    draft = replace(invalid, net_external_flow=net_external_flow, valid=True)
    return unit_nav, max(previous_high, unit_nav), draft, []


def _portfolio_positions_fingerprint(positions: Sequence[Position], cash: Decimal) -> str:
    payload = {
        "cash": cash,
        "positions": tuple(
            {
                "symbol": item.symbol,
                "asset_type": item.asset_type.value,
                "quantity": item.quantity,
                "average_cost": item.average_cost,
                "market_value": item.market_value,
                "as_of_date": item.as_of_date,
            }
            for item in positions
        ),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _portfolio_events_fingerprint(events: pd.DataFrame) -> str:
    payload = []
    if not events.empty:
        for row in events.sort_values(
            [
                column
                for column in ("effective_date", "recorded_at", "event_id")
                if column in events
            ],
            kind="mergesort",
        ).to_dict("records"):
            payload.append(
                {
                    "event_id": str(row.get("event_id", "")),
                    "event_type": str(row.get("event_type", "")),
                    "effective_date": _to_date(row.get("effective_date")),
                    "amount": _decimal_or_none(row.get("amount")),
                    "recorded_at": row.get("recorded_at"),
                }
            )
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _latest_marks(
    daily_prices: pd.DataFrame, fund_nav: pd.DataFrame
) -> tuple[dict[str, Decimal], dict[str, date]]:
    marks: dict[str, Decimal] = {}
    mark_dates: dict[str, date] = {}
    if not daily_prices.empty:
        latest = (
            daily_prices.sort_values(["symbol", "trade_date"])
            .groupby("symbol", as_index=False)
            .tail(1)
        )
        for row in latest.to_dict("records"):
            value = _decimal_or_none(row["close"])
            if value is not None and value > 0:
                symbol = str(row["symbol"])
                marks[symbol] = value
                parsed_date = _to_date(row["trade_date"])
                if parsed_date is not None:
                    mark_dates[symbol] = parsed_date
    if not fund_nav.empty:
        latest = (
            fund_nav.sort_values(["symbol", "nav_date"]).groupby("symbol", as_index=False).tail(1)
        )
        for row in latest.to_dict("records"):
            value = _decimal_or_none(row["unit_nav"])
            if value is not None and value > 0:
                symbol = str(row["symbol"])
                marks[symbol] = value
                parsed_date = _to_date(row["nav_date"])
                if parsed_date is not None:
                    mark_dates[symbol] = parsed_date
    return marks, mark_dates


def _has_complete_stock_history(
    prices: pd.DataFrame,
    instruments: pd.DataFrame,
    symbols: Sequence[str],
    as_of_date: date,
) -> bool:
    required_symbols = tuple(sorted({str(symbol) for symbol in symbols if str(symbol)}))
    if not required_symbols:
        return False
    dated_prices = prices.copy(deep=False)
    trade_dates = pd.to_datetime(dated_prices["trade_date"], errors="coerce")
    through_date = trade_dates.le(pd.Timestamp(as_of_date))
    market_dates = trade_dates.loc[
        through_date & dated_prices["symbol"].astype(str).eq("000300.SH")
    ].dropna()
    listed_dates = (
        instruments.assign(
            symbol=instruments["symbol"].astype(str),
            listed_on=pd.to_datetime(instruments["listed_on"], errors="coerce"),
        )
        .drop_duplicates("symbol", keep="last")
        .set_index("symbol")["listed_on"]
    )
    mature_symbols = tuple(
        symbol
        for symbol in required_symbols
        if symbol in listed_dates.index
        and listed_dates.loc[symbol] is not pd.NaT
        and market_dates.ge(listed_dates.loc[symbol]).sum() >= 200
    )
    if not mature_symbols:
        return False
    stock_rows = dated_prices.loc[
        dated_prices["symbol"].astype(str).isin(mature_symbols) & through_date
    ]
    counts = stock_rows.groupby("symbol")["trade_date"].nunique()
    complete = counts.reindex(mature_symbols, fill_value=0).ge(200)
    return bool(not stock_rows.empty and complete.all())


def _decimal(value: object) -> Decimal:
    return decimal_coerce(value, "数值")


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        return _decimal(value)
    except (TypeError, ValueError):
        return None


def _to_date(value: object) -> date | None:
    """Deprecated alias kept for callers that still reference it."""

    return optional_date(value)


def _utc_timestamp(value: object) -> pd.Timestamp | None:
    return optional_timestamp(value)


def _required_date(value: object) -> date:
    parsed = _to_date(value)
    if parsed is None:
        raise ValueError("日期无效")
    return parsed


def _canonical_json(value: object) -> str:
    return canonical_json(value)
