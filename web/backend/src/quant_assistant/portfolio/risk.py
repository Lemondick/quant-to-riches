"""Pure helpers shared by portfolio risk and order sizing."""

from collections.abc import Mapping
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from quant_assistant.models.entities import Action, Signal
from quant_assistant.portfolio.models import PortfolioState

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")


class AssetBucket(StrEnum):
    """Allocation buckets governed by :class:`RiskPolicy`."""

    STOCK = "stock"
    CORE_FUND = "core_fund"
    ROTATION_FUND = "rotation_fund"


def decimal_or_none(value: object) -> Decimal | None:
    """Convert an input to a finite Decimal, returning ``None`` on bad data."""

    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return converted if converted.is_finite() else None


def floor_money(value: Decimal) -> Decimal:
    """Floor a non-negative monetary value to cents so a cap is never exceeded."""

    return max(ZERO, value).quantize(CENT, rounding=ROUND_DOWN)


def portfolio_drawdown(state: PortfolioState) -> Decimal | None:
    """Return unit-NAV drawdown, or ``None`` when the inputs are unusable."""

    nav = decimal_or_none(state.unit_nav)
    high_water_mark = decimal_or_none(state.high_water_mark)
    if nav is None or high_water_mark is None or nav < ZERO or high_water_mark <= ZERO:
        return None
    return max(ZERO, (high_water_mark - nav) / high_water_mark)


def is_risk_increasing(action: Action) -> bool:
    return action in {Action.BUY, Action.ADD}


def is_risk_reducing(action: Action) -> bool:
    return action in {Action.REDUCE, Action.SELL}


def signal_is_expired(signal: Signal, state: PortfolioState) -> bool:
    """Use the state date as the deterministic portfolio evaluation clock."""

    valid_until = getattr(signal, "valid_until", None)
    try:
        return valid_until.date() < state.as_of_date
    except (AttributeError, TypeError, ValueError):
        return True


def signal_is_blocked(signal: Signal, state: PortfolioState) -> bool:
    """Recognise the locked no-trade action and optional upstream status fields."""

    if signal.action in {Action.NO_TRADE, Action.HOLD}:
        return True
    if getattr(signal, "blocked", False):
        return True
    status = getattr(signal, "data_status", getattr(signal, "status", None))
    if status is not None and str(getattr(status, "value", status)).lower() in {
        "blocked",
        "stale",
        "incomplete",
    }:
        return True
    return getattr(signal, "as_of_date", state.as_of_date) > state.as_of_date


def _normalise_bucket(value: object) -> AssetBucket | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if not text or text in {"nan", "none", "<na>"}:
        return None
    if text in {
        "core",
        "core_fund",
        "fund_core",
        "核心",
        "核心基金",
        "核心配置",
    }:
        return AssetBucket.CORE_FUND
    if text in {
        "rotation",
        "rotation_fund",
        "fund_rotation",
        "c_rotation",
        "c_share_rotation",
        "轮动",
        "轮动基金",
        "c类轮动",
        "c类轮动基金",
    }:
        return AssetBucket.ROTATION_FUND
    return None


def classify_fund_bucket(
    instrument: Mapping[str, Any] | None,
    signal: Signal | None = None,
) -> AssetBucket | None:
    """Resolve a fund bucket without assuming that every C share is rotation.

    Instrument metadata is authoritative.  The reason/version fallbacks keep the
    locked ``Signal`` interface capital-independent while still allowing the fund
    strategy to communicate its explicit strategy bucket.
    """

    if instrument:
        for column in (
            "fund_bucket",
            "bucket",
            "strategy_bucket",
            "strategy_type",
            "allocation_bucket",
            "portfolio_bucket",
            "mode",
        ):
            bucket = _normalise_bucket(instrument.get(column))
            if bucket is not None:
                return bucket
    if signal is None:
        return None
    for reason in signal.reasons:
        compact = str(reason).lower().replace(" ", "")
        if "基金策略桶：核心" in compact or "基金策略桶:核心" in compact:
            return AssetBucket.CORE_FUND
        if "基金策略桶：轮动" in compact or "基金策略桶:轮动" in compact:
            return AssetBucket.ROTATION_FUND
    version = signal.strategy_version.lower()
    if "rotation" in version or "轮动" in version:
        return AssetBucket.ROTATION_FUND
    if "core" in version or "核心" in version:
        return AssetBucket.CORE_FUND
    return None


__all__ = [
    "AssetBucket",
    "classify_fund_bucket",
    "decimal_or_none",
    "floor_money",
    "is_risk_increasing",
    "is_risk_reducing",
    "portfolio_drawdown",
    "signal_is_blocked",
    "signal_is_expired",
]
