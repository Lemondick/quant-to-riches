"""Goal projection (``goal.project``).

Contract (batch5): payload
``{target_amount, target_date(yyyy-MM-dd), current_amount, monthly_amount?,
assumed_annual_return?, frequency: monthly}`` →
``{required_monthly, projected_amount, gap, on_track: bool, assumptions: {...},
disclaimer}``.

Pure computation op: monthly compounding of ``current_amount`` plus an
optional fixed monthly contribution. No DB, no network. Missing required
keys raise ``KeyError`` (mapped to ``invalid_request`` by the E3 worker
boundary); malformed values raise ``ValueError`` (same mapping).

Conventions:
- Contributions are assumed at each month end (ordinary annuity).
- ``assumed_annual_return`` defaults to ``DEFAULT_ANNUAL_RETURN`` (0.06),
  matching the ``QUANT_GOAL_DEFAULT_ANNUAL_RETURN`` backend default; the
  backend passes its configured value through the payload.
- ``frequency`` must be ``"monthly"`` when present; other frequencies are
  not supported by this op.
- Money is rounded to 2 decimals; ratios to 6.
"""

from __future__ import annotations

import math
from datetime import date, datetime

GOAL_CAPABILITIES = frozenset({"goal.project"})

#: Default assumed annual return, mirroring QUANT_GOAL_DEFAULT_ANNUAL_RETURN.
DEFAULT_ANNUAL_RETURN = 0.06

DISCLAIMER = "假设测算非承诺"


def _to_float(value: object, field: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _to_date(value: object, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date (yyyy-MM-dd)") from exc
    raise ValueError(f"{field} must be an ISO date string")


def months_until(target: date, today: date) -> int:
    """Full calendar months from ``today`` to ``target`` (may be <= 0)."""
    months = (target.year - today.year) * 12 + (target.month - today.month)
    if target.day < today.day:
        months -= 1
    return months


def monthly_rate(assumed_annual_return: float) -> float:
    """Equivalent monthly rate for the assumed annual return."""
    return (1.0 + assumed_annual_return) ** (1.0 / 12.0) - 1.0


def project_goal(
    target_amount: float,
    target_date: date | str,
    current_amount: float,
    monthly_amount: float | None = None,
    assumed_annual_return: float = DEFAULT_ANNUAL_RETURN,
    *,
    as_of: date | None = None,
) -> dict:
    """Core projection; pure apart from the default ``as_of`` date.

    With ``monthly_amount``: forward-projects ``projected_amount`` and
    reports ``gap = target - projected`` and ``on_track``. Without it:
    back-solves the constant monthly contribution (``required_monthly``)
    that closes the shortfall. ``required_monthly`` is still reported
    when ``monthly_amount`` is given (the plan needed), so callers can
    compare it with the actual ``monthly_amount``.
    """
    target = _to_float(target_amount, "target_amount")
    current = _to_float(current_amount, "current_amount")
    annual = _to_float(assumed_annual_return, "assumed_annual_return")
    goal_date = _to_date(target_date, "target_date")
    if target <= 0:
        raise ValueError("target_amount must be positive")
    if current < 0:
        raise ValueError("current_amount must be non-negative")
    if annual < -1.0:
        raise ValueError("assumed_annual_return must be >= -1")
    monthly = None
    if monthly_amount is not None:
        monthly = _to_float(monthly_amount, "monthly_amount")
        if monthly < 0:
            raise ValueError("monthly_amount must be non-negative")

    today = as_of or date.today()
    months = months_until(goal_date, today)
    rate = monthly_rate(annual)
    growth = (1.0 + rate) ** max(months, 0)

    warnings: list[str] = []
    if months <= 0:
        # Expired goal: nothing left to project; the current position is
        # the outcome.
        warnings.append("目标已到期（target_date <= 今天），测算退化为当前值对比")
        projected = round(current, 2)
        gap = round(target - current, 2)
        return {
            "required_monthly": 0.0,
            "projected_amount": projected,
            "gap": gap,
            "on_track": current >= target,
            "months_remaining": 0,
            "assumptions": {
                "assumed_annual_return": annual,
                "monthly_rate": round(rate, 6),
                "compounding": "monthly",
                "as_of": today.isoformat(),
            },
            "warnings": warnings,
            "disclaimer": DISCLAIMER,
        }

    future_current = current * growth
    annuity_factor = months if rate == 0.0 else (growth - 1.0) / rate
    shortfall = target - future_current
    required = 0.0 if shortfall <= 0 else shortfall / annuity_factor

    if monthly is None:
        projected = round(future_current, 2)
    else:
        projected = round(future_current + monthly * annuity_factor, 2)
    gap = round(target - projected, 2)

    return {
        "required_monthly": round(required, 2),
        "projected_amount": projected,
        "gap": gap,
        "on_track": gap <= 0,
        "months_remaining": months,
        "assumptions": {
            "assumed_annual_return": annual,
            "monthly_rate": round(rate, 6),
            "compounding": "monthly",
            "contribution_timing": "month_end",
            "as_of": today.isoformat(),
        },
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }


def execute_goal(kind: str, payload: dict) -> dict:
    if kind not in GOAL_CAPABILITIES:
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    frequency = payload.get("frequency", "monthly")
    if frequency != "monthly":
        raise ValueError("goal.project only supports frequency='monthly'")
    # Required keys use subscript access so a missing key raises KeyError
    # (E3 -> invalid_request at the worker boundary).
    return project_goal(
        payload["target_amount"],
        payload["target_date"],
        payload["current_amount"],
        payload.get("monthly_amount"),
        payload.get("assumed_annual_return", DEFAULT_ANNUAL_RETURN),
    )
