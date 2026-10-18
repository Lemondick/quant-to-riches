"""Validate archived comparison paths before exposing them to web charts."""

import csv
import math
from datetime import date
from decimal import Decimal
from io import StringIO

from quant_assistant.reporting.fund_scenarios import STRATEGIES


def read_curves(payload, fund, period, purchase_rate):
    rows = {
        row["strategy"]: row
        for row in fund["results"]
        if row["period"] == period and row["purchase_rate"] == purchase_rate
    }
    if set(rows) != set(STRATEGIES):
        raise ValueError("missing strategy summaries")
    samples = {strategy: [] for strategy in STRATEGIES}
    reader = csv.DictReader(StringIO(payload.decode("utf-8-sig")))
    for record in reader:
        if (
            record["symbol"] != fund["symbol"]
            or record["period"] != period
            or record["purchase_rate"] != purchase_rate
        ):
            continue
        strategy = record["strategy"]
        if strategy not in samples:
            raise ValueError("unknown curve strategy")
        day = date.fromisoformat(record["day"]).isoformat()
        equity = Decimal(record["equity"])
        drawdown = float(record["drawdown"])
        if not equity.is_finite() or not 0 < equity <= Decimal("1e12"):
            raise ValueError("invalid curve equity")
        if not math.isfinite(drawdown) or not 0 <= drawdown <= 1:
            raise ValueError("invalid curve drawdown")
        points = samples[strategy]
        if len(points) >= 20000 or (points and points[-1]["day"] >= day):
            raise ValueError("invalid curve dates")
        points.append({"day": day, "equity": str(equity), "drawdown": drawdown})
    days, capital = None, None
    result = []
    for strategy, points in samples.items():
        row = rows[strategy]
        dates = [point["day"] for point in points]
        if (
            not points
            or len(points) != row["observations"]
            or dates[0] != row["first_date"]
            or dates[-1] != row["last_date"]
            or Decimal(points[-1]["equity"]) != Decimal(row["estimated_net_assets"])
            or not math.isclose(
                max(point["drawdown"] for point in points), row["max_drawdown"], abs_tol=1e-9
            )
            or (days is not None and dates != days)
            or (capital is not None and capital != row["capital"])
        ):
            raise ValueError("curve and summary mismatch")
        days, capital = dates, row["capital"]
        result.append(
            {"strategy": strategy, "label": row["label"], "capital": capital, "points": points}
        )
    return result
