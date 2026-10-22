"""Multi-fund portfolio backtest (``portfolio.backtest``).

Contract (batch2 §2): payload
``{funds: [{code, weight}], start, end,
   rebalance?: {mode: period|threshold, period?: "1M"|"3M", threshold_pct?},
   dca?: {amount, freq}}``
→ ``{metrics, curve, rebalance_events: [...]}``.

Simulation (documented assumptions):
- Backtest dates = the intersection of all funds' NAV dates inside
  [start, end] (a fund that did not exist yet cannot be invested in).
- ``initial_capital`` (optional, default 100000) is invested on the first
  date at target weights; weights are normalized if they do not sum to 1.
- ``dca`` adds an inflow on the first sampled trading day of each month
  or ISO week, allocated by target weight (``freq`` monthly/weekly).
- An omitted ``rebalance`` keeps holdings without scheduled rebalancing.
- Rebalance ``mode=period``: on each 1M/3M anniversary (first trading day
  on/after it) rebalance unconditionally to target weights.
  ``mode=threshold``: each day, rebalance when any fund's weight deviates
  from target by more than ``threshold_pct`` (percentage points).
- Rebalance trades: overweight legs are redeemed, underweight legs bought
  to target value. Buys are charged the documented 0.15% front-end via
  ``FundFeeSchedule``/``subscription_fee`` (cents, ROUND_HALF_UP, F8
  caliber); redemption fees are NOT charged (documented limitation —
  holding-period-dependent tiers are out of scope for v1).
- Metrics reuse ``calculate_performance`` (cash-flow-adjusted unit NAV,
  same caliber as the walk-forward engine); XIRR via bisection on dated
  cash flows. ``rebalance_events`` = [{date, action, reason}].
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from backtest_shared import (
    charge_purchase_fee,
    default_purchase_schedule,
    parse_iso_date,
    performance_summary,
    to_decimal,
    xirr,
)

PORTFOLIO_CAPABILITIES = ("portfolio.backtest",)

_DEFAULT_INITIAL_CAPITAL = Decimal("100000")


def _production_nav_series(codes: Sequence[str], start: date, end: date):
    from public_operations import _detail_provider

    provider = _detail_provider()
    series = {}
    for code in codes:
        points = [
            (point.nav_date, point.unit_nav)
            for point in provider.nav_history(code)
            if start <= point.nav_date <= end
        ]
        points.sort(key=lambda item: item[0])
        series[code] = points
    return series


def _targets(funds: Sequence[Mapping]) -> dict[str, Decimal]:
    if not isinstance(funds, Sequence) or isinstance(funds, (str, bytes)) or not funds:
        raise ValueError("funds must be a non-empty list")
    weights = {}
    for position, fund in enumerate(funds):
        if not isinstance(fund, Mapping):
            raise ValueError(f"fund #{position} must be a mapping")
        code = fund.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"fund #{position}: code must be a non-empty string")
        weight = to_decimal(fund.get("weight"), f"fund #{position} weight")
        if weight < 0:
            raise ValueError(f"fund #{position}: weight must be non-negative")
        weights[code.strip()] = weight
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("fund weights must sum to a positive value")
    return {code: weight / total for code, weight in weights.items()}


def _rebalance_config(raw: Mapping | None) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("rebalance must be a mapping")
    mode = raw.get("mode")
    if mode == "period":
        period = raw.get("period", "1M")
        if period not in ("1M", "3M"):
            raise ValueError('rebalance.period must be "1M" or "3M"')
        return {"mode": "period", "period": period}
    if mode == "threshold":
        threshold = raw.get("threshold_pct")
        if threshold is None:
            raise ValueError("rebalance.threshold_pct is required for threshold mode")
        threshold = float(threshold)
        if not 0 < threshold < 100:
            raise ValueError("rebalance.threshold_pct must be within (0, 100)")
        return {"mode": "threshold", "threshold_pct": threshold}
    raise ValueError('rebalance.mode must be "period" or "threshold"')


def _dca_config(raw: Mapping | None) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("dca must be a mapping")
    amount = to_decimal(raw.get("amount"), "dca.amount")
    if amount <= 0:
        raise ValueError("dca.amount must be positive")
    freq = raw.get("freq", "monthly")
    if freq not in ("monthly", "weekly"):
        raise ValueError('dca.freq must be "monthly" or "weekly"')
    return {"amount": amount, "freq": freq}


def _period_dates(start: date, end: date, period: str) -> set[date]:
    months = 1 if period == "1M" else 3
    dates = set()
    year, month = start.year, start.month
    while True:
        month += months
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
        anchor = date(year, month, 1)
        if anchor > end:
            break
        dates.add(anchor)
    return dates


def portfolio_backtest(
    funds: Sequence[Mapping],
    start: date,
    end: date,
    rebalance: Mapping | None = None,
    dca: Mapping | None = None,
    *,
    nav_series: dict[str, Sequence[tuple[date, Decimal]]] | None = None,
    initial_capital: Decimal = _DEFAULT_INITIAL_CAPITAL,
) -> dict:
    """Core computation; ``nav_series`` injectable for deterministic tests."""
    if start >= end:
        raise ValueError("start must precede end")
    if initial_capital < 0:
        raise ValueError("initial_capital must be non-negative")
    targets = _targets(funds)
    rebalance_cfg = _rebalance_config(rebalance)
    dca_cfg = _dca_config(dca)
    codes = list(targets)

    series = nav_series if nav_series is not None else _production_nav_series(codes, start, end)
    cleaned: dict[str, list[tuple[date, Decimal]]] = {}
    for code in codes:
        points = sorted(
            ((day, Decimal(str(nav))) for day, nav in (series.get(code) or ())),
            key=lambda item: item[0],
        )
        points = [(day, nav) for day, nav in points if start <= day <= end and nav > 0]
        if not points:
            raise ValueError(f"基金 {code} 在区间内无有效净值数据")
        cleaned[code] = points
    common = sorted(set.intersection(*(set(day for day, _ in pts) for pts in cleaned.values())))
    if len(common) < 2:
        raise ValueError("各基金净值日期无足够交集")
    nav_of = {code: dict(pts) for code, pts in cleaned.items()}

    schedule = default_purchase_schedule("PORTFOLIO")
    shares = {code: Decimal("0") for code in codes}
    invested = Decimal("0")
    cashflows: list[tuple[date, Decimal]] = []
    events: list[dict] = []
    warnings: list[str] = []
    values_total: list[Decimal] = []

    def buy(code: str, amount: Decimal, day: date) -> None:
        fee = charge_purchase_fee(schedule, amount)
        shares[code] += (amount - fee) / nav_of[code][day]

    def portfolio_value(day: date) -> dict[str, Decimal]:
        return {code: shares[code] * nav_of[code][day] for code in codes}

    def do_rebalance(day: date, reason: str) -> None:
        values = portfolio_value(day)
        total = sum(values.values())
        if total <= 0:
            return
        for code in codes:
            target_value = total * targets[code]
            delta = target_value - values[code]
            if delta > 0:
                buy(code, delta, day)
            elif delta < 0:
                shares[code] += delta / nav_of[code][day]  # redeem, no fee (documented)
        events.append({"date": day.isoformat(), "action": "rebalance_to_target", "reason": reason})

    period_anchors = (
        _period_dates(common[0], end, rebalance_cfg["period"])
        if rebalance_cfg is not None and rebalance_cfg["mode"] == "period"
        else set()
    )
    fired_anchors: set[date] = set()

    if initial_capital > 0:
        for code in codes:
            buy(code, initial_capital * targets[code], common[0])
        invested += initial_capital
        cashflows.append((common[0], -initial_capital))

    current_period = None
    for day in common:
        period_key = (
            day.isocalendar()[:2]
            if dca_cfg and dca_cfg["freq"] == "weekly"
            else (day.year, day.month)
        )
        if dca_cfg is not None and period_key != current_period:
            current_period = period_key
            for code in codes:
                buy(code, dca_cfg["amount"] * targets[code], day)
            invested += dca_cfg["amount"]
            cashflows.append((day, -dca_cfg["amount"]))
        if rebalance_cfg is not None and rebalance_cfg["mode"] == "period":
            anchor = next(
                (anchor for anchor in sorted(period_anchors - fired_anchors) if anchor <= day),
                None,
            )
            if anchor is not None:
                fired_anchors.add(anchor)
                do_rebalance(day, f"period:{rebalance_cfg['period']} 到期再平衡")
        elif rebalance_cfg is not None:
            values = portfolio_value(day)
            total = sum(values.values())
            if total > 0:
                worst = max(
                    codes,
                    key=lambda code: abs(values[code] / total - targets[code]),
                )
                deviation = abs(values[worst] / total - targets[worst]) * 100
                if deviation > rebalance_cfg["threshold_pct"]:
                    do_rebalance(
                        day,
                        f"threshold:{worst} 权重偏离 {deviation:.2f}% 超过阈值 "
                        f"{rebalance_cfg['threshold_pct']}%",
                    )
        # Shares are mutable; preserve each close before the next day's trades.
        values_total.append(sum(portfolio_value(day).values()))

    days = common
    flows = [Decimal("0")] * len(days)
    flow_map: dict[date, Decimal] = {}
    for flow_day, amount in cashflows:
        flow_map[flow_day] = flow_map.get(flow_day, Decimal("0")) + (-amount)
    for index, day in enumerate(days):
        flows[index] = flow_map.get(day, Decimal("0"))
    closes = [sum(nav_of[code][day] * targets[code] for code in codes) for day in days]
    summary = performance_summary(days, values_total, flows, [Decimal("0")] * len(days), closes)
    final_value = values_total[-1]
    all_flows = [*cashflows, (days[-1], final_value)]
    curve = []
    invested_to_date = Decimal("0")
    unit_nav = 1.0
    seen_months: set[str] = set()
    for index, day in enumerate(days):
        invested_to_date += flows[index]
        if index:
            unit_nav *= float((values_total[index] - flows[index]) / values_total[index - 1])
        key = day.strftime("%Y-%m")
        if key not in seen_months or day == days[-1]:
            seen_months.add(key)
            curve.append(
                {
                    "date": day.isoformat(),
                    "invested": str(invested_to_date),
                    "value": str(values_total[index].quantize(Decimal("0.01"))),
                    "unit_nav": unit_nav,
                }
            )
    return {
        "funds": [{"code": code, "weight": str(targets[code])} for code in codes],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rebalance": rebalance_cfg,
        "metrics": {
            "total_invested": str(invested),
            "final_value": str(final_value.quantize(Decimal("0.01"))),
            "xirr": None if (rate := xirr(all_flows)) is None else round(rate, 4),
            "max_drawdown": summary["max_drawdown"],
            "total_return": summary["total_return"],
            "sharpe_zero_rate": summary["sharpe_zero_rate"],
        },
        "curve": curve,
        "rebalance_events": events,
        "warnings": warnings + ["组合再平衡的赎回端未计赎回费（持有期相关，v1 未建模）"],
    }


def execute_portfolio_backtest(kind: str, payload: dict) -> dict:
    if kind != "portfolio.backtest":
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    initial = payload.get("initial_capital")
    return portfolio_backtest(
        payload.get("funds", ()),
        parse_iso_date(payload.get("start"), "start"),
        parse_iso_date(payload.get("end"), "end"),
        payload.get("rebalance"),
        payload.get("dca"),
        initial_capital=_DEFAULT_INITIAL_CAPITAL
        if initial is None
        else to_decimal(initial, "initial_capital"),
    )
