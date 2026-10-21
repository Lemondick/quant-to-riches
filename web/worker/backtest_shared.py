"""Shared deterministic helpers for the worker backtest ops.

Conventions reused from the existing engines (F8 口径一致性):
- money is quantized to cents with ROUND_HALF_UP whenever a fee is charged
  (via ``quant_assistant.funds.fees.subscription_fee``);
- performance metrics (max_drawdown etc.) go through
  ``quant_assistant.backtest.metrics.calculate_performance`` so the
  cash-flow-adjusted unit-NAV definition matches the walk-forward engine.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from quant_assistant.backtest.metrics import calculate_performance
from quant_assistant.funds.fees import FundFeeSchedule, RedemptionTier, subscription_fee

CENT = Decimal("0.01")

# Documented assumption: a typical third-party channel front-end purchase
# rate for an index fund. Both backtest ops price buys through
# ``FundFeeSchedule``/``subscription_fee`` so the F8 fee caliber holds.
DEFAULT_PURCHASE_RATE = Decimal("0.0015")


def parse_iso_date(value: object, field: str) -> date:
    if isinstance(value, date) and type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date") from exc
    raise ValueError(f"{field} must be a date or ISO date string")


def to_decimal(value: object, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def default_purchase_schedule(symbol: str = "BACKTEST") -> FundFeeSchedule:
    """Fee-caliber carrier for backtest buys (redemption leg unused)."""
    from datetime import date as _date

    return FundFeeSchedule(
        symbol=symbol,
        share_class="A",
        purchase_rate=DEFAULT_PURCHASE_RATE,
        annual_service_rate=Decimal("0"),
        redemption_tiers=(RedemptionTier(0, None, Decimal("0")),),
        verified_on=_date.today(),
    )


def charge_purchase_fee(schedule: FundFeeSchedule, amount: Decimal) -> Decimal:
    """Purchase fee quantized to cents ROUND_HALF_UP (F8 caliber)."""
    return subscription_fee(schedule, amount)


def xirr(cashflows: list[tuple[date, Decimal]]) -> float | None:
    """Annualized IRR of dated cash flows (inflows positive). Bisection solve."""
    if len(cashflows) < 2:
        return None
    base = cashflows[0][0]
    points = [((day - base).days / 365.0, float(amount)) for day, amount in cashflows]
    if not any(amount > 0 for _, amount in points) or not any(
        amount < 0 for _, amount in points
    ):
        return None

    def npv(rate: float) -> float:
        return sum(amount / (1.0 + rate) ** t for t, amount in points)

    low, high = -0.9999, 10.0
    low_npv, high_npv = npv(low), npv(high)
    if low_npv * high_npv > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2.0
        mid_npv = npv(mid)
        if abs(mid_npv) < 1e-12:
            return mid
        if low_npv * mid_npv <= 0:
            high, high_npv = mid, mid_npv
        else:
            low, low_npv = mid, mid_npv
    return (low + high) / 2.0


def performance_summary(
    days: list[date],
    total_values: list[Decimal],
    external_flows: list[Decimal],
    fees: list[Decimal],
    benchmark_closes: list[Decimal],
) -> dict:
    """Metrics via ``calculate_performance`` (walk-forward engine caliber)."""
    nav = pd.DataFrame(
        {
            "trade_date": days,
            "total_value": [float(value) for value in total_values],
            "external_cash_flow": [float(flow) for flow in external_flows],
            "purchase_fee": [float(fee) for fee in fees],
        }
    )
    benchmark = pd.DataFrame(
        {"trade_date": days, "close": [float(close) for close in benchmark_closes]}
    )
    metrics = calculate_performance(nav, benchmark)
    return {
        "total_return": metrics.total_return,
        "annualized_return": metrics.annualized_return,
        "max_drawdown": metrics.max_drawdown,
        "sharpe_zero_rate": metrics.sharpe_zero_rate,
        "total_fees": str(metrics.total_fees),
    }
