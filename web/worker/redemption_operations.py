"""Redemption-fee tier-drop reminders (``fund.redemption_windows``).

Contract (batch2 §2): payload ``{lots: [{fund_code, acquired_at, amount}],
days_ahead?: 30}`` → ``{windows: [{fund_code, lot_id, current_tier,
next_tier, next_tier_at, days_left, save_estimate}]}``.

Fee caliber: ``quant_assistant.funds.fees.FundFeeSchedule`` (locked
consistent with C# ``FundFeeRules``) is the *only* fee model used — no
parallel rate tables. The "next tier change point" is derived generically
from the schedule's ``redemption_tiers`` (sorted by ``min_days`` by the
dataclass itself): the next tier is the one with the smallest ``min_days``
strictly greater than the current holding days. No hardcoded 30d/1y/2y
boundaries anywhere.

Tier sourcing (documented): tiers come from the payload — either a
top-level ``redemption_tiers`` list applied to every lot, or a per-lot
``redemption_tiers`` override — because the worker holds no verified
per-fund fee schedule store. Each tier is ``{min_days, max_days?, rate}``
and is validated by ``RedemptionTier`` on construction. Lots without tiers
are skipped with a warning, never an error.

``save_estimate`` = amount × (current_rate − next_rate), quantized to
cents ROUND_HALF_UP. Only lots whose next tier is *cheaper* and reachable
within ``days_ahead`` days produce a window.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from quant_assistant.funds.fees import (
    FundFeeSchedule,
    FundRedemptionLot,
    RedemptionTier,
    estimate_redemption_lots,
)

REDEMPTION_CAPABILITIES = ("fund.redemption_windows",)

CENT = Decimal("0.01")


def _to_date(value: object, field: str) -> date:
    if isinstance(value, date) and type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date string") from exc
    raise ValueError(f"{field} must be a date or ISO date string")


def _to_decimal(value: object, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _to_tiers(raw: object) -> tuple[RedemptionTier, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ValueError("redemption_tiers must be a non-empty list")
    tiers = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("each redemption tier must be a mapping")
        min_days = entry.get("min_days")
        if isinstance(min_days, bool) or not isinstance(min_days, int):
            raise ValueError("tier min_days must be an integer")
        max_days = entry.get("max_days")
        if max_days is not None and (isinstance(max_days, bool) or not isinstance(max_days, int)):
            raise ValueError("tier max_days must be an integer or null")
        rate = _to_decimal(entry.get("rate"), "tier rate")
        tiers.append(RedemptionTier(min_days, max_days, rate))
    return tuple(tiers)


def _tier_view(tier: RedemptionTier) -> dict:
    return {
        "min_days": tier.min_days,
        "max_days": tier.max_days,
        "rate": str(tier.rate),
    }


def _schedule_for(fund_code: str, tiers: tuple[RedemptionTier, ...], today: date) -> FundFeeSchedule:
    # The schedule is a caliber carrier for the redemption tiers only;
    # purchase/service legs are zeroed because this op never prices them.
    return FundFeeSchedule(
        symbol=fund_code,
        share_class="A",
        purchase_rate=Decimal("0"),
        annual_service_rate=Decimal("0"),
        redemption_tiers=tiers,
        verified_on=today,
    )


def redemption_windows(
    lots: Sequence[Mapping],
    days_ahead: int = 30,
    *,
    as_of: date | None = None,
) -> dict:
    """Core computation; pure apart from ``date.today()`` default."""
    if not isinstance(lots, Sequence) or isinstance(lots, (str, bytes)) or not lots:
        raise ValueError("lots must be a non-empty list")
    if isinstance(days_ahead, bool) or not isinstance(days_ahead, int) or days_ahead < 0:
        raise ValueError("days_ahead must be a non-negative integer")
    today = as_of or date.today()
    windows: list[dict] = []
    warnings: list[str] = []
    for position, lot in enumerate(lots):
        if not isinstance(lot, Mapping):
            raise ValueError(f"lot #{position} must be a mapping")
        fund_code = lot.get("fund_code")
        if not isinstance(fund_code, str) or not fund_code.strip():
            raise ValueError(f"lot #{position}: fund_code must be a non-empty string")
        fund_code = fund_code.strip()
        acquired_at = _to_date(lot.get("acquired_at"), f"lot #{position} acquired_at")
        amount = _to_decimal(lot.get("amount"), f"lot #{position} amount")
        if amount <= 0:
            raise ValueError(f"lot #{position}: amount must be positive")
        lot_id = lot.get("lot_id") or f"{fund_code}#{position}"
        if not isinstance(lot_id, str):
            raise ValueError(f"lot #{position}: lot_id must be a string")
        holding_days = (today - acquired_at).days
        if holding_days < 0:
            raise ValueError(f"lot {lot_id}: acquired_at is in the future")

        tiers_raw = lot.get("redemption_tiers")
        if tiers_raw is None:
            warnings.append(f"{lot_id}: 无赎回费率档位数据，已跳过")
            continue
        tiers = _to_tiers(tiers_raw)
        schedule = _schedule_for(fund_code, tiers, today)
        evaluated = estimate_redemption_lots(
            schedule, (FundRedemptionLot(acquired_at, amount),), today
        )[0]
        current_rate = evaluated["rate"]
        coming = [tier for tier in schedule.redemption_tiers if tier.min_days > holding_days]
        if not coming:
            continue  # already at the cheapest tier
        nxt = min(coming, key=lambda tier: tier.min_days)
        if nxt.rate >= current_rate:
            continue  # no cheaper tier ahead
        next_tier_at = acquired_at + timedelta(days=nxt.min_days)
        days_left = (next_tier_at - today).days
        if days_left > days_ahead:
            continue
        save_estimate = (amount * (current_rate - nxt.rate)).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        current_tier = next(
            tier for tier in schedule.redemption_tiers if tier.includes(holding_days)
        )
        windows.append(
            {
                "fund_code": fund_code,
                "lot_id": lot_id,
                "current_tier": _tier_view(current_tier),
                "next_tier": _tier_view(nxt),
                "next_tier_at": next_tier_at.isoformat(),
                "days_left": days_left,
                "save_estimate": str(save_estimate),
            }
        )
    windows.sort(key=lambda item: item["days_left"])
    return {"as_of": today.isoformat(), "windows": windows, "warnings": warnings}


def execute_redemption(kind: str, payload: dict) -> dict:
    if kind != "fund.redemption_windows":
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    global_tiers = payload.get("redemption_tiers")
    lots = payload.get("lots", ())
    if global_tiers is not None:
        # Attach the shared tiers to lots that do not carry their own.
        enriched = []
        for lot in lots:
            if isinstance(lot, Mapping) and "redemption_tiers" not in lot:
                enriched.append({**lot, "redemption_tiers": global_tiers})
            else:
                enriched.append(lot)
        lots = enriched
    return redemption_windows(lots, payload.get("days_ahead", 30))
