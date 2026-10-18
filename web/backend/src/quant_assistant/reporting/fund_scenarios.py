"""Fixed-rule, fee-sensitive NAV experiments; never account or admission evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

import pandas as pd

D = Decimal
CENT = D("0.01")
UNIT = D("0.00000001")
STRATEGIES = ("buy_hold", "monthly_staging", "monthly_trend")
LABELS = {
    "buy_hold": "买入持有",
    "monthly_staging": "分12个月投入",
    "monthly_trend": "月度趋势观察",
}


@dataclass(frozen=True)
class ScenarioRules:
    capital: str = "100000"
    invested_fraction: str = "0.8"
    signal_lag: int = 2
    trend_window: int = 120
    momentum_window: int = 60
    minimum_hold_days: int = 30
    confirm_lag: int = 1
    redemption_cash_lag: int = 3
    redemption_under_7: str = "0.015"
    redemption_under_30: str = "0.005"
    redemption_other: str = "0"
    drawdown_line: str = "0.20"

    def __post_init__(self):
        for key in ("capital", "invested_fraction", "drawdown_line"):
            value = D(getattr(self, key))
            if not value.is_finite() or value <= 0:
                raise ValueError(f"invalid {key}")
        if D(self.capital) > D("100000000"):
            raise ValueError("experiment capital exceeds supported range")
        if D(self.invested_fraction) > 1 or D(self.drawdown_line) > 1:
            raise ValueError("invalid fraction")
        for key in ("redemption_under_7", "redemption_under_30", "redemption_other"):
            value = D(getattr(self, key))
            if not value.is_finite() or not 0 <= value < 1:
                raise ValueError(f"invalid {key}")
        for key in (
            "signal_lag",
            "trend_window",
            "momentum_window",
            "confirm_lag",
            "redemption_cash_lag",
        ):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 500:
                raise ValueError(f"invalid {key}")
        if type(self.minimum_hold_days) is not int or self.minimum_hold_days < 0:
            raise ValueError("invalid minimum holding period")


def _money(value):
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _series(frame):
    if not {"as_of_date", "source_unit_nav"}.issubset(frame.columns):
        raise ValueError("missing scenario NAV columns")
    days = [pd.Timestamp(value).date() for value in frame["as_of_date"]]
    prices = [D(str(value)) for value in frame["source_unit_nav"]]
    if len(days) > 20000 or not days or any(pd.isna(value) for value in days):
        raise ValueError("invalid NAV dates")
    if any(right <= left for left, right in zip(days, days[1:], strict=False)):
        raise ValueError("NAV dates must be unique and increasing")
    if any(not value.is_finite() or value <= 0 for value in prices):
        raise ValueError("invalid NAV values")
    return days, prices


def simulate(frame, *, strategy, purchase_rate, start_index, end_index=None, rules=None):
    """Orders use only lagged NAV signals; closing NAV values simulated fills afterwards.

    All alternatives start with the same cash. Staging uses that cash, without
    external deposits. Open units and unavailable redemption proceeds remain
    assets; terminal redemption charges are estimates, not settled cash.
    """
    rules = rules or ScenarioRules()
    if strategy not in STRATEGIES:
        raise ValueError("unknown scenario strategy")
    rate = D(str(purchase_rate))
    if not rate.is_finite() or not 0 <= rate < 1:
        raise ValueError("invalid purchase fee")
    days, prices = _series(frame)
    end = len(days) - 1 if end_index is None else end_index
    warmup = max(rules.trend_window, rules.momentum_window + 1) + rules.signal_lag - 1
    if (
        type(start_index) is not int
        or type(end) is not int
        or not warmup <= start_index <= end < len(days)
    ):
        raise ValueError("invalid scenario period or insufficient warmup")
    cash = capital = D(rules.capital)
    target = capital * D(rules.invested_fraction)
    lots, pending, curve, trades = [], [], [], []
    fees = D(0)
    peak = capital
    halted = False
    last_month = None
    staged_months = 0
    exposure = []

    def redemption_rate(confirmed, index):
        held = max(0, (days[index] - confirmed).days)
        return D(
            rules.redemption_under_7
            if held < 7
            else rules.redemption_under_30
            if held < 30
            else rules.redemption_other
        )

    for index in range(start_index, end + 1):
        day, nav = days[index], prices[index]
        cash += sum((amount for available, amount in pending if available <= index), D(0))
        pending = [(available, amount) for available, amount in pending if available > index]
        value = sum((lot[0] * nav for lot in lots), D(0))
        equity = cash + value + sum((amount for _, amount in pending), D(0))
        # Risk checks also use past recorded equity, not today's unknown NAV.
        if (
            strategy == "monthly_trend"
            and len(curve) >= rules.signal_lag
            and D(str(curve[-rules.signal_lag]["drawdown"])) >= D(rules.drawdown_line)
        ):
            halted = True
        new_month = (day.year, day.month) != last_month
        last_month = (day.year, day.month)
        known = index - rules.signal_lag
        signal_day = days[known]
        average = sum(prices[known - rules.trend_window + 1 : known + 1], D(0)) / rules.trend_window
        positive_trend = (
            prices[known] > average and prices[known] > prices[known - rules.momentum_window]
        )
        eligible = [lot for lot in lots if lot[1] <= index]
        can_rotate = eligible and all(
            (day - lot[2]).days >= rules.minimum_hold_days for lot in eligible
        )
        sell = strategy == "monthly_trend" and (
            halted or (new_month and not positive_trend and can_rotate)
        )
        if sell and eligible:
            gross = sum((lot[0] * nav for lot in eligible), D(0)).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            charge = sum(
                (
                    (lot[0] * nav * redemption_rate(lot[2], index)).quantize(
                        CENT, rounding=ROUND_HALF_UP
                    )
                    for lot in eligible
                ),
                D(0),
            )
            pending.append((index + rules.redemption_cash_lag, gross - charge))
            fees += charge
            lots = [lot for lot in lots if lot not in eligible]
            trades.append(
                {
                    "day": day.isoformat(),
                    "signal_nav_date": signal_day.isoformat(),
                    "side": "sell",
                    "gross": _money(gross),
                    "fee": _money(charge),
                }
            )
        amount = D(0)
        if strategy == "buy_hold" and index == start_index:
            amount = target
        elif strategy == "monthly_staging" and new_month and staged_months < 12:
            staged_months += 1
            amount = target / 12
        elif (
            strategy == "monthly_trend" and new_month and positive_trend and not lots and not halted
        ):
            # Leave the same initial cash reserve; unsettled proceeds cannot be spent.
            amount = max(D(0), cash - (capital - target))
        amount = min(cash, amount).quantize(CENT, rounding=ROUND_DOWN)
        if amount > 0:
            net = (amount / (1 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
            charge = amount - net
            units = (net / nav).quantize(UNIT, rounding=ROUND_DOWN)
            confirm_index = index + rules.confirm_lag
            confirmed = days[confirm_index] if confirm_index < len(days) else date.max
            lots.append((units, confirm_index, confirmed))
            cash -= amount
            fees += charge
            trades.append(
                {
                    "day": day.isoformat(),
                    "signal_nav_date": signal_day.isoformat(),
                    "side": "buy",
                    "gross": _money(amount),
                    "fee": _money(charge),
                }
            )
        value = sum((lot[0] * nav for lot in lots), D(0))
        equity = cash + value + sum((amount for _, amount in pending), D(0))
        peak = max(peak, equity)
        drawdown = 1 - equity / peak
        exposure.append(float(value / equity))
        curve.append(
            {"day": day.isoformat(), "equity": _money(equity), "drawdown": float(drawdown)}
        )
    terminal_fee = sum(
        (
            (lot[0] * prices[end] * redemption_rate(lot[2], end)).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            for lot in lots
        ),
        D(0),
    )
    final = equity - terminal_fee
    curve[-1].update(equity=_money(final), drawdown=float(1 - final / peak))
    return {
        "strategy": strategy,
        "label": LABELS[strategy],
        "capital": _money(capital),
        "first_date": days[start_index].isoformat(),
        "last_date": days[end].isoformat(),
        "observations": len(curve),
        "net_return": float(final / capital - 1),
        "max_drawdown": max(row["drawdown"] for row in curve),
        "estimated_net_assets": _money(final),
        "fees_including_terminal_estimate": _money(fees + terminal_fee),
        "terminal_fee_estimate": _money(terminal_fee),
        "transaction_count": len(trades),
        "average_invested_fraction": sum(exposure) / len(exposure),
        "risk_halted": halted,
        "cash": _money(cash),
        "unavailable_redemption_cash": _money(sum((v for _, v in pending), D(0))),
        "curve": curve,
        "trades": trades,
    }


def compare_scenarios(frame, rules=None):
    rules = rules or ScenarioRules()
    days, _ = _series(frame)
    start = max(rules.trend_window, rules.momentum_window + 1) + rules.signal_lag - 1
    split = start + (len(days) - start) * 2 // 3
    if split - start < 120 or len(days) - split < 120:
        raise ValueError("each comparison segment needs at least 120 NAV observations")
    results = []
    for fee in ("0", "0.0012", "0.012"):
        for period, left, right in (
            ("full", start, len(days) - 1),
            ("early", start, split - 1),
            ("late", split, len(days) - 1),
        ):
            group = [
                simulate(
                    frame,
                    strategy=s,
                    purchase_rate=fee,
                    start_index=left,
                    end_index=right,
                    rules=rules,
                )
                for s in STRATEGIES
            ]
            for row in group:
                row.update(
                    purchase_rate=fee,
                    period=period,
                    excess_vs_buy_hold=row["net_return"] - group[0]["net_return"],
                )
                if any(
                    not math.isfinite(row[key])
                    for key in ("net_return", "max_drawdown", "average_invested_fraction")
                ):
                    raise ValueError("nonfinite scenario result")
            results.extend(group)
    params = asdict(rules)
    return {
        "rules": params,
        "rules_sha256": hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest(),
        "results": results,
    }
