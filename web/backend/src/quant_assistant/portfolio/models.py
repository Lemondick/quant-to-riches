"""Portfolio state and immutable risk-policy models."""

from dataclasses import dataclass, fields
from datetime import date
from decimal import Decimal, InvalidOperation

from quant_assistant.models.entities import Position, ProposedOrder


@dataclass(frozen=True, slots=True)
class PortfolioValuationDraft:
    """Auditable NAV inputs owned by the portfolio domain."""

    net_external_flow: Decimal
    previous_run_id: str | None
    positions_fingerprint: str
    events_fingerprint: str
    is_baseline: bool
    valid: bool


def _as_decimal(value: object, field_name: str) -> Decimal:
    """Return a finite decimal without silently accepting floats' binary form."""

    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是有效数值") from exc
    if not converted.is_finite():
        raise ValueError(f"{field_name} 必须是有限数值")
    return converted


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Locked first-version portfolio limits.

    The values are proportions of total portfolio value.  They are upper/lower
    bounds, not allocation targets.
    """

    max_stock_bucket: Decimal = Decimal("0.30")
    max_core_fund_bucket: Decimal = Decimal("0.30")
    max_rotation_fund_bucket: Decimal = Decimal("0.10")
    min_cash_bucket: Decimal = Decimal("0.30")
    max_single_stock: Decimal = Decimal("0.10")
    max_single_fund: Decimal = Decimal("0.15")
    max_industry: Decimal = Decimal("0.20")
    max_daily_turnover: Decimal = Decimal("0.20")
    warning_drawdown: Decimal = Decimal("0.05")
    stop_drawdown: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        for field in fields(self):
            value = _as_decimal(getattr(self, field.name), field.name)
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError(f"{field.name} 必须位于0和1之间")
            object.__setattr__(self, field.name, value)
        if self.warning_drawdown > self.stop_drawdown:
            raise ValueError("warning_drawdown 不能高于 stop_drawdown")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Point-in-time portfolio values consumed by the order proposer."""

    total_value: Decimal
    cash: Decimal
    unit_nav: Decimal
    high_water_mark: Decimal
    positions: tuple[Position, ...]
    as_of_date: date

    def __post_init__(self) -> None:
        for field_name in ("total_value", "cash", "unit_nav", "high_water_mark"):
            value = _as_decimal(getattr(self, field_name), field_name)
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "positions", tuple(self.positions))


__all__ = ["PortfolioState", "PortfolioValuationDraft", "ProposedOrder", "RiskPolicy"]
