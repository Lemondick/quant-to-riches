"""Pure risk-budget helpers for volatility-targeted allocation.

Volatility targeting is intentionally an opt-in calculation boundary.  The
existing :class:`RiskPolicy` remains unchanged until a walk-forward validation
proves the policy improves outcomes; callers can use the returned audit object
to display exactly why a target was or was not scaled.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")
ONE = Decimal("1")


def _decimal(value: object, field_name: str) -> Decimal:
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是有效数值") from exc
    if not converted.is_finite():
        raise ValueError(f"{field_name} 必须是有限数值")
    return converted


@dataclass(frozen=True, slots=True)
class VolatilityBudget:
    """Validated opt-in parameters for a volatility target calculation."""

    target_volatility: Decimal
    min_scale: Decimal = ZERO
    max_scale: Decimal = ONE
    enabled: bool = False

    def __post_init__(self) -> None:
        target = _decimal(self.target_volatility, "target_volatility")
        minimum = _decimal(self.min_scale, "min_scale")
        maximum = _decimal(self.max_scale, "max_scale")
        if target <= ZERO:
            raise ValueError("target_volatility 必须大于0")
        if minimum < ZERO or maximum < ZERO or minimum > maximum:
            raise ValueError("min_scale/max_scale 必须为非负数且 min_scale 不得高于 max_scale")
        object.__setattr__(self, "target_volatility", target)
        object.__setattr__(self, "min_scale", minimum)
        object.__setattr__(self, "max_scale", maximum)


@dataclass(frozen=True, slots=True)
class VolatilityAllocation:
    """Auditable result of scaling one requested target weight."""

    requested_weight: Decimal
    scaled_weight: Decimal
    scale: Decimal
    current_volatility: Decimal | None
    target_volatility: Decimal
    applied: bool
    reason: str


def scale_target_weight(
    target_weight: object,
    current_volatility: object,
    budget: VolatilityBudget,
    *,
    max_weight: object = ONE,
) -> VolatilityAllocation:
    """Scale a target weight using a validated volatility budget.

    ``current_volatility`` is required and strictly positive when targeting is
    enabled.  Invalid evidence raises instead of silently recommending a
    position.  Disabled budgets return the original target unchanged, making
    rollout explicit and behaviour-preserving.
    """

    requested = _decimal(target_weight, "target_weight")
    ceiling = _decimal(max_weight, "max_weight")
    if requested < ZERO or requested > ONE:
        raise ValueError("target_weight 必须位于0和1之间")
    if ceiling < ZERO or ceiling > ONE:
        raise ValueError("max_weight 必须位于0和1之间")
    requested = min(ceiling, requested)
    if not budget.enabled:
        return VolatilityAllocation(
            requested,
            requested,
            ONE,
            None,
            budget.target_volatility,
            False,
            "volatility_targeting_disabled",
        )

    current = _decimal(current_volatility, "current_volatility")
    if current <= ZERO:
        raise ValueError("current_volatility 必须大于0")
    raw_scale = budget.target_volatility / current
    scale = min(budget.max_scale, max(budget.min_scale, raw_scale))
    scaled = min(ceiling, max(ZERO, requested * scale))
    return VolatilityAllocation(
        requested,
        scaled,
        scale,
        current,
        budget.target_volatility,
        True,
        "volatility_target_applied",
    )


__all__ = ["VolatilityAllocation", "VolatilityBudget", "scale_target_weight"]
