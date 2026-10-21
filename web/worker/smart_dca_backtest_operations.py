"""Smart-DCA backtest (``dca.smart_plan_backtest``): valuation-aware contributions.

Contract (batch2 §2): payload
``{index_symbol, start, end, base_amount, rules: {low_pct_boost, high_pct_cut, halt_pct}}``
→ ``{metrics: {total_invested, final_value, xirr, max_drawdown}, curve: [...]}``.

Rules (documented; ``rules`` keys):
- ``low_pct`` (default 30), ``boost`` (default 1.5): valuation percentile ≤
  low_pct → contribute ``base_amount × boost``;
- ``high_pct`` (default 70), ``cut`` (default 0.5): percentile ≥ high_pct →
  contribute ``base_amount × cut``;
- ``halt_pct`` (default 90): percentile ≥ halt_pct → pause (no contribution).

Valuation signal (documented assumption): the percentile is the trailing
``valuation_years`` (default 10) percentile of the index PE history when a
valuation source provides it — the same ``trailing_percentile`` math as
``valuation.percentiles``. The existing public providers publish no PE
history, so the production fallback is the trailing *price* percentile,
labeled ``valuation_basis: "price_proxy"`` in the output; an injected
``valuation_series`` yields ``"pe_percentile"``.

Simulation caliber (F8): contributions buy at the trading-day close on the
first trading day of each month; purchase fees go through
``FundFeeSchedule``/``subscription_fee`` (cents, ROUND_HALF_UP; default
0.15% front-end, documented assumption); metrics via
``calculate_performance`` on the cash-flow-adjusted unit NAV. The index
level proxies the fund NAV (no tracking error modeled).
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
from valuation_operations import trailing_percentile

DCA_CAPABILITIES = ("dca.smart_plan_backtest",)

_DEFAULT_RULES = {
    "low_pct": 30.0,
    "boost": Decimal("1.5"),
    "high_pct": 70.0,
    "cut": Decimal("0.5"),
    "halt_pct": 90.0,
}


def _rules(raw: Mapping | None) -> dict:
    merged = dict(_DEFAULT_RULES)
    if raw:
        if not isinstance(raw, Mapping):
            raise ValueError("rules must be a mapping")
        for key in ("low_pct", "high_pct", "halt_pct"):
            if key in raw:
                value = float(raw[key])
                if not 0 < value < 100:
                    raise ValueError(f"rules.{key} must be within (0, 100)")
                merged[key] = value
        for key in ("boost", "cut"):
            if key in raw:
                value = to_decimal(raw[key], f"rules.{key}")
                if value < 0:
                    raise ValueError(f"rules.{key} must be non-negative")
                merged[key] = value
    if not merged["low_pct"] < merged["high_pct"] < merged["halt_pct"]:
        raise ValueError("rules require low_pct < high_pct < halt_pct")
    return merged


def _production_price_series(symbol: str, start: date, end: date):
    """Index closes from the existing ``market.history`` capability."""
    from public_charts import index_history

    from quant_assistant.common.performance import traced_get

    payload = index_history(symbol, http_get=traced_get)
    points = []
    for item in payload.get("items", ()):
        day = date.fromisoformat(item["date"])
        if start <= day <= end and not item.get("is_partial"):
            points.append((day, Decimal(item["close"])))
    points.sort(key=lambda item: item[0])
    return points


def _monthly_first_sessions(days: list[date], start: date, end: date) -> list[date]:
    """First trading day of each month inside [start, end]."""
    sessions = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        candidates = [day for day in days if day.year == cursor.year and day.month == cursor.month]
        if candidates:
            sessions.append(min(candidates))
        cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
    return sessions


def smart_dca_backtest(
    index_symbol: str,
    start: date,
    end: date,
    base_amount: Decimal,
    rules: Mapping | None = None,
    *,
    price_series: Sequence[tuple[date, Decimal]] | None = None,
    valuation_series: Sequence[tuple[date, float]] | None = None,
    valuation_years: int = 10,
) -> dict:
    """Core computation; series injectable for deterministic tests."""
    if not isinstance(index_symbol, str) or not index_symbol.strip():
        raise ValueError("index_symbol must be a non-empty string")
    if start >= end:
        raise ValueError("start must precede end")
    if base_amount <= 0:
        raise ValueError("base_amount must be positive")
    parsed_rules = _rules(rules)
    if valuation_years < 1:
        raise ValueError("valuation_years must be positive")

    prices = sorted(
        price_series if price_series is not None
        else _production_price_series(index_symbol.strip(), start, end)
    )
    prices = [(day, close) for day, close in prices if start <= day <= end]
    if len(prices) < 2:
        raise ValueError("insufficient price history for the requested period")
    price_of = dict(prices)
    sessions = _monthly_first_sessions([day for day, _ in prices], start, end)
    if not sessions:
        raise ValueError("no monthly contribution session in the requested period")

    if valuation_series is not None:
        signal = [(day, float(pe)) for day, pe in valuation_series]
        basis = "pe_percentile"
    else:
        signal = [(day, float(close)) for day, close in prices]
        basis = "price_proxy"

    schedule = default_purchase_schedule(index_symbol.strip())
    shares = Decimal("0")
    invested = Decimal("0")
    fee_total = Decimal("0")
    contributions = []
    cashflows: list[tuple[date, Decimal]] = []
    for session in sessions:
        pct = trailing_percentile(signal, session, valuation_years)
        close = price_of[session]
        if pct is None:
            multiplier, paused = Decimal("1"), False
        elif pct >= parsed_rules["halt_pct"]:
            multiplier, paused = Decimal("0"), True
        elif pct <= parsed_rules["low_pct"]:
            multiplier, paused = parsed_rules["boost"], False
        elif pct >= parsed_rules["high_pct"]:
            multiplier, paused = parsed_rules["cut"], False
        else:
            multiplier, paused = Decimal("1"), False
        record = {
            "date": session.isoformat(),
            "percentile": None if pct is None else round(pct, 1),
            "multiplier": str(multiplier),
            "paused": paused,
        }
        if not paused:
            amount = (base_amount * multiplier).quantize(Decimal("0.01"))
            fee = charge_purchase_fee(schedule, amount)
            net = amount - fee
            bought = net / close
            shares += bought
            invested += amount
            fee_total += fee
            cashflows.append((session, -amount))
            record["amount"] = str(amount)
            record["fee"] = str(fee)
            record["shares_bought"] = str(bought)
        contributions.append(record)

    days = [day for day, _ in prices]
    closes = [close for _, close in prices]
    bought_by_date: dict[str, Decimal] = {}
    for record in contributions:
        if "shares_bought" in record:
            bought_by_date[record["date"]] = bought_by_date.get(
                record["date"], Decimal("0")
            ) + Decimal(record["shares_bought"])
    cumulative = Decimal("0")
    values, flows, fee_days = [], [], []
    for day, close in prices:
        iso = day.isoformat()
        cumulative += bought_by_date.get(iso, Decimal("0"))
        values.append((cumulative * close).quantize(Decimal("0.01")))
        flows.append(flow_at(day, contributions))
        fee_days.append(fee_at(day, contributions))
    summary = performance_summary(days, values, flows, fee_days, closes)
    final_value = values[-1]
    if cashflows:
        cashflows.append((days[-1], final_value))
    curve = []
    cumulative = Decimal("0")
    invested_to_date = Decimal("0")
    for record in contributions:
        if "shares_bought" in record:
            cumulative += Decimal(record["shares_bought"])
            invested_to_date += Decimal(record["amount"])
        close = price_of[date.fromisoformat(record["date"])]
        curve.append(
            {
                "date": record["date"],
                "invested": str(invested_to_date),
                "value": str((cumulative * close).quantize(Decimal("0.01"))),
                "paused": record["paused"],
            }
        )
    curve.append(
        {"date": days[-1].isoformat(), "invested": str(invested), "value": str(final_value)}
    )
    return {
        "index_symbol": index_symbol.strip(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "base_amount": str(base_amount),
        "rules": {key: str(value) for key, value in parsed_rules.items()},
        "valuation_basis": basis,
        "metrics": {
            "total_invested": str(invested),
            "final_value": str(final_value.quantize(Decimal("0.01"))),
            "xirr": None if (rate := xirr(cashflows)) is None else round(rate, 4),
            "max_drawdown": summary["max_drawdown"],
        },
        "curve": curve,
        "contributions": contributions,
        "warnings": [
            "估值信号为价格分位代理（无 PE 历史源），非真实估值分位"
        ] if basis == "price_proxy" else [],
    }


def flow_at(day, contributions):
    iso = day.isoformat()
    return sum(
        (Decimal(record["amount"]) for record in contributions
         if record["date"] == iso and "amount" in record),
        start=Decimal("0"),
    )


def fee_at(day, contributions):
    iso = day.isoformat()
    return sum(
        (Decimal(record["fee"]) for record in contributions
         if record["date"] == iso and "fee" in record),
        start=Decimal("0"),
    )


def execute_smart_dca(kind: str, payload: dict) -> dict:
    if kind != "dca.smart_plan_backtest":
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    return smart_dca_backtest(
        payload.get("index_symbol", ""),
        parse_iso_date(payload.get("start"), "start"),
        parse_iso_date(payload.get("end"), "end"),
        to_decimal(payload.get("base_amount"), "base_amount"),
        payload.get("rules"),
        valuation_years=payload.get("valuation_years", 10),
    )
