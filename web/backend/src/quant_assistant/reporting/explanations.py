"""Short Chinese explanations derived from the rule inputs that actually fired."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from quant_assistant.models.entities import MarketRegime, ProposedOrder, Signal


@dataclass(frozen=True, slots=True)
class ConstraintEvent:
    """A named portfolio constraint and the values observed when it fired."""

    code: str
    actual: Decimal | None = None
    limit: Decimal | None = None


def explain_stock_momentum(ret_60: float, ret_120: float) -> str:
    """Explain only the supplied 60/120-session momentum signs."""

    if ret_60 > 0 and ret_120 > 0:
        return "60/120日动量为正"
    if ret_60 < 0 and ret_120 < 0:
        return "60/120日动量为负"
    return "60/120日动量方向不一致"


def explain_market_regime(regime: MarketRegime) -> str:
    """Map the exact market-regime state to its configured risk effect."""

    try:
        normalized = regime if isinstance(regime, MarketRegime) else MarketRegime(regime)
    except (TypeError, ValueError) as exc:
        raise ValueError("未知市场风险状态") from exc
    return {
        MarketRegime.GREEN: "市场风险为绿色，可使用完整股票风险预算",
        MarketRegime.YELLOW: "市场风险为黄色，目标仓位减半",
        MarketRegime.RED: "市场风险为红色，禁止新增股票风险",
    }[normalized]


def explain_fee_verification(
    verified_on: date, as_of_date: date, *, maximum_age_days: int = 30
) -> str:
    """Explain fee verification freshness without assuming a recommendation action."""

    if not isinstance(verified_on, date) or not isinstance(as_of_date, date):
        raise TypeError("verified_on and as_of_date must be dates")
    if isinstance(maximum_age_days, bool) or not isinstance(maximum_age_days, int):
        raise TypeError("maximum_age_days must be an integer")
    if maximum_age_days < 0:
        raise ValueError("maximum_age_days must be non-negative")
    if verified_on > as_of_date:
        return "费率核验日期晚于策略日期，禁止申购"
    if (as_of_date - verified_on).days > maximum_age_days:
        return f"费率超过{maximum_age_days}日未核验，禁止申购"
    return f"费率已在{maximum_age_days}日内核验"


def explain_constraint(event: ConstraintEvent | str) -> str:
    """Render a known constraint event; unknown codes fail instead of inventing a reason."""

    normalized = event if isinstance(event, ConstraintEvent) else ConstraintEvent(str(event))
    fixed = {
        "cash_floor": "现金比例下限触发，建议金额已缩减",
        "stock_bucket_cap": "股票资产桶上限触发，建议金额已缩减",
        "core_fund_bucket_cap": "核心基金资产桶上限触发，建议金额已缩减",
        "rotation_fund_bucket_cap": "轮动基金资产桶上限触发，建议金额已缩减",
        "single_name_cap": "单一标的仓位上限触发，建议金额已缩减",
        "industry_cap": "行业集中度上限触发，建议金额已缩减",
        "turnover_cap": "单日换手上限触发，建议金额已缩减",
        "drawdown_warning": "组合回撤达到警戒线，新增风险预算减半",
        "drawdown_stop": "组合回撤达到停止线，仅允许降低风险",
        "stale_data": "数据已过期，禁止新增风险",
        "blocked_data": "数据校验被阻断，禁止新增风险",
    }
    if normalized.code not in fixed:
        raise ValueError(f"未知约束事件: {normalized.code}")
    if normalized.actual is None and normalized.limit is None:
        return fixed[normalized.code]
    if normalized.actual is None or normalized.limit is None:
        raise ValueError("带数值的约束解释必须同时提供 actual 和 limit")
    return (
        f"{fixed[normalized.code]}"
        f"（实际{_format_decimal(normalized.actual)}，限制{_format_decimal(normalized.limit)}）"
    )


def render_reasons(reasons: Sequence[str]) -> str:
    """Join recorded rule reasons verbatim after removing empty duplicates."""

    rendered: list[str] = []
    for reason in reasons:
        text = str(reason).strip()
        if text and text not in rendered:
            rendered.append(text)
    return "；".join(rendered)


def explain_signal(signal: Signal) -> str:
    """Render a signal's recorded reasons without inferring from its action."""

    if not isinstance(signal, Signal):
        raise TypeError("signal must be Signal")
    return render_reasons(signal.reasons)


def explain_order(order: ProposedOrder) -> str:
    """Render a proposed order's recorded reasons without inferring from its action."""

    if not isinstance(order, ProposedOrder):
        raise TypeError("order must be ProposedOrder")
    return render_reasons(order.reasons)


def _format_decimal(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("约束数值必须是有限Decimal")
    return format(value.normalize(), "f")


# Readable aliases used by callers that prefer noun-first names.
stock_momentum_explanation = explain_stock_momentum
market_regime_explanation = explain_market_regime
fee_verification_explanation = explain_fee_verification
constraint_explanation = explain_constraint


__all__ = [
    "ConstraintEvent",
    "constraint_explanation",
    "explain_constraint",
    "explain_fee_verification",
    "explain_market_regime",
    "explain_order",
    "explain_signal",
    "explain_stock_momentum",
    "fee_verification_explanation",
    "market_regime_explanation",
    "render_reasons",
    "stock_momentum_explanation",
]
