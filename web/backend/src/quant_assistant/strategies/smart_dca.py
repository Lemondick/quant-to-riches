"""Evidence-gated smart dollar-cost averaging (Smart DCA).

The strategy in this module is deliberately small and deterministic.  It is
intended to turn an already collected, point-in-time valuation/price snapshot
into an explainable *proposal*; it never submits an order and it never fills
missing valuation history with a fabricated value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from enum import StrEnum
from math import isfinite

import numpy as np
import pandas as pd


class SmartDcaAction(StrEnum):
    """Actions returned by :func:`calculate_smart_dca`."""

    INVEST = "invest"
    HOLD = "hold"
    PAUSE = "pause"
    TAKE_PROFIT = "take_profit"
    BLOCKED = "blocked"


def _decimal(value: object, name: str, *, non_negative: bool = False) -> Decimal:
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not converted.is_finite() or (non_negative and converted < 0):
        raise ValueError(f"{name} must be a finite non-negative decimal")
    return converted


def _finite(value: object, name: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be a finite number")
    return number


@dataclass(frozen=True, slots=True)
class SmartDcaConfig:
    """Conservative defaults for valuation- and trend-aware contributions."""

    min_valuation_observations: int = 60
    low_valuation_percentile: float = 0.20
    high_valuation_percentile: float = 0.80
    low_valuation_multiplier: Decimal = Decimal("1.50")
    high_valuation_multiplier: Decimal = Decimal("0.50")
    ma_sensitivity: float = 1.0
    min_trend_multiplier: Decimal = Decimal("0.50")
    max_trend_multiplier: Decimal = Decimal("1.50")
    target_annualized_return: float | None = 0.12
    trailing_stop_drawdown: float | None = 0.10
    minimum_investment: Decimal = Decimal("0.01")
    amount_rounding: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if isinstance(self.min_valuation_observations, bool) or self.min_valuation_observations < 2:
            raise ValueError("min_valuation_observations must be at least 2")
        for name in ("low_valuation_percentile", "high_valuation_percentile"):
            value = _finite(getattr(self, name), name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.low_valuation_percentile >= self.high_valuation_percentile:
            raise ValueError("low valuation percentile must be below high percentile")
        for name in (
            "low_valuation_multiplier",
            "high_valuation_multiplier",
            "min_trend_multiplier",
            "max_trend_multiplier",
            "minimum_investment",
            "amount_rounding",
        ):
            value = _decimal(getattr(self, name), name, non_negative=True)
            if name in {"amount_rounding", "minimum_investment"} and value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.min_trend_multiplier > self.max_trend_multiplier:
            raise ValueError("min_trend_multiplier must not exceed max_trend_multiplier")
        if self.ma_sensitivity < 0 or not isfinite(float(self.ma_sensitivity)):
            raise ValueError("ma_sensitivity must be finite and non-negative")
        for name in ("target_annualized_return", "trailing_stop_drawdown"):
            value = getattr(self, name)
            if value is not None:
                numeric = _finite(value, name)
                if not 0 <= numeric < 1:
                    raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class SmartDcaInput:
    """Point-in-time inputs for one Smart DCA decision.

    ``valuation_history`` accepts a sequence of numeric observations or a
    DataFrame containing a PE/PB-like column and, optionally, a date column.
    When both PE and PB are supplied, their percentiles are averaged.
    """

    symbol: str
    as_of_date: date
    base_amount: Decimal
    current_price: float
    ma120: float
    ma200: float
    valuation_history: pd.DataFrame | Sequence[float] | None
    pe: float | None = None
    pb: float | None = None
    current_cost: float | None = None
    high_water_mark: float | None = None
    holding_days: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if not isinstance(self.as_of_date, date) or isinstance(self.as_of_date, datetime):
            # datetime subclasses date: a timestamp would pass this check and
            # then break the point-in-time valuation-history filter, which
            # compares against plain ``date`` objects.
            raise TypeError("as_of_date must be a date")
        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(
            self, "base_amount", _decimal(self.base_amount, "base_amount", non_negative=True)
        )
        for name in ("current_price", "ma120", "ma200"):
            _finite(getattr(self, name), name, positive=True)
        for name in ("pe", "pb", "current_cost", "high_water_mark"):
            value = getattr(self, name)
            if value is not None:
                _finite(
                    value, name, positive=name in {"pe", "pb", "current_cost", "high_water_mark"}
                )
        if self.holding_days is not None and (
            isinstance(self.holding_days, bool) or self.holding_days < 0
        ):
            raise ValueError("holding_days must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class SmartDcaDecision:
    """Auditable Smart DCA output."""

    symbol: str
    as_of_date: date
    action: SmartDcaAction
    amount: Decimal
    valuation_percentile: float | None
    trend_deviation: float | None
    valuation_multiplier: Decimal
    trend_multiplier: Decimal
    annualized_return: float | None
    trailing_drawdown: float | None
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def executable(self) -> bool:
        return self.action is SmartDcaAction.INVEST and not self.blockers and self.amount > 0


def _history_frame(
    history: pd.DataFrame | Sequence[float] | None,
    as_of_date: date,
    *,
    value_column: str | None = None,
) -> pd.Series:
    if history is None:
        return pd.Series(dtype=float)
    if isinstance(history, pd.DataFrame):
        frame = history.copy(deep=True)
        date_column = next(
            (
                name
                for name in ("as_of_date", "valuation_date", "trade_date", "date")
                if name in frame
            ),
            None,
        )
        if date_column is not None:
            parsed = pd.to_datetime(frame[date_column], errors="coerce")
            frame = frame.loc[parsed.notna() & parsed.dt.date.le(as_of_date)].copy()
        selected_column = value_column or next(
            (name for name in ("value", "pe", "pb", "valuation", "metric") if name in frame),
            None,
        )
        if selected_column is None or selected_column not in frame:
            return pd.Series(dtype=float)
        values = pd.to_numeric(frame[selected_column], errors="coerce")
    elif isinstance(history, Sequence) and not isinstance(history, (str, bytes, bytearray)):
        values = pd.to_numeric(pd.Series(list(history)), errors="coerce")
    else:
        raise TypeError("valuation_history must be a DataFrame, sequence, or None")
    values = values.replace([np.inf, -np.inf], np.nan).dropna()
    return values.loc[values > 0].astype(float).reset_index(drop=True)


def valuation_percentile(
    value: float | None,
    history: pd.DataFrame | Sequence[float] | None,
    as_of_date: date,
    *,
    minimum_observations: int = 60,
    value_column: str | None = None,
) -> float | None:
    """Return an empirical percentile without using observations after ``as_of_date``."""

    if value is None:
        return None
    current = _finite(value, "valuation", positive=True)
    values = _history_frame(history, as_of_date, value_column=value_column)
    if len(values) < minimum_observations:
        return None
    # ``<=`` makes a tied current observation deterministic and conservative.
    return float((values <= current).mean())


def _combined_percentile(item: SmartDcaInput, config: SmartDcaConfig) -> float | None:
    if isinstance(item.valuation_history, Mapping):
        return None
    values: list[float] = []
    if item.pe is not None:
        value = valuation_percentile(
            item.pe,
            item.valuation_history,
            item.as_of_date,
            minimum_observations=config.min_valuation_observations,
            value_column="pe" if isinstance(item.valuation_history, pd.DataFrame) else None,
        )
        if value is not None:
            values.append(value)
    if item.pb is not None:
        value = valuation_percentile(
            item.pb,
            item.valuation_history,
            item.as_of_date,
            minimum_observations=config.min_valuation_observations,
            value_column="pb" if isinstance(item.valuation_history, pd.DataFrame) else None,
        )
        if value is not None:
            values.append(value)
    return float(np.mean(values)) if values else None


def _round_amount(amount: Decimal, config: SmartDcaConfig) -> Decimal:
    rounded = (amount / config.amount_rounding).to_integral_value(rounding=ROUND_DOWN)
    return (rounded * config.amount_rounding).quantize(config.amount_rounding)


def calculate_smart_dca(
    item: SmartDcaInput, config: SmartDcaConfig | None = None
) -> SmartDcaDecision:
    """Calculate one fail-closed contribution proposal.

    The output is ``blocked`` whenever the valuation percentile cannot be
    established.  This keeps an interactive dashboard from silently treating
    missing PE/PB evidence as a neutral valuation.
    """

    if not isinstance(item, SmartDcaInput):
        raise TypeError("item must be SmartDcaInput")
    config = config or SmartDcaConfig()
    percentile = _combined_percentile(item, config)
    current_price = _finite(item.current_price, "current_price", positive=True)
    ma120 = _finite(item.ma120, "ma120", positive=True)
    ma200 = _finite(item.ma200, "ma200", positive=True)
    deviation = (current_price / ma120) - 1.0
    trend_multiplier = float(
        np.clip(
            1.0 - config.ma_sensitivity * deviation,
            float(config.min_trend_multiplier),
            float(config.max_trend_multiplier),
        )
    )
    trend_multiplier_decimal = Decimal(str(trend_multiplier)).quantize(Decimal("0.0001"))

    blockers: list[str] = []
    reasons: list[str] = []
    if percentile is None:
        blockers.append("PE/PB历史分位证据不足（需要按目标日截断的历史序列）")
        valuation_multiplier = Decimal("0")
    elif percentile <= config.low_valuation_percentile:
        valuation_multiplier = config.low_valuation_multiplier
        reasons.append(f"估值分位{percentile:.1%}≤低估阈值，放大定投")
    elif percentile >= config.high_valuation_percentile:
        valuation_multiplier = config.high_valuation_multiplier
        reasons.append(f"估值分位{percentile:.1%}≥高估阈值，降低定投")
    else:
        valuation_multiplier = Decimal("1")
        reasons.append(f"估值分位{percentile:.1%}处于中性区间")

    if current_price > ma120:
        reasons.append(f"价格较MA120偏离{deviation:.1%}，趋势乘数{trend_multiplier:.2f}")
    else:
        reasons.append(f"价格低于MA120，趋势乘数{trend_multiplier:.2f}")
    if ma120 <= ma200:
        reasons.append("MA120未站上MA200，趋势门槛偏谨慎")
        trend_multiplier_decimal = max(
            config.min_trend_multiplier,
            min(trend_multiplier_decimal, Decimal("0.75")),
        )

    annualized_return: float | None = None
    trailing_drawdown: float | None = None
    if item.current_cost is not None and item.holding_days and item.holding_days > 0:
        annualized_return = (current_price / float(item.current_cost)) ** (
            365 / item.holding_days
        ) - 1
        reasons.append(f"持有期年化收益{annualized_return:.1%}")
    if item.high_water_mark is not None:
        trailing_drawdown = 1 - current_price / float(item.high_water_mark)
        reasons.append(f"距高水位回撤{trailing_drawdown:.1%}")

    amount = Decimal("0")
    action = SmartDcaAction.BLOCKED
    if not blockers:
        target_hit = (
            config.target_annualized_return is not None
            and annualized_return is not None
            and annualized_return >= config.target_annualized_return
        )
        trailing_hit = (
            config.trailing_stop_drawdown is not None
            and trailing_drawdown is not None
            and trailing_drawdown >= config.trailing_stop_drawdown
        )
        if target_hit or trailing_hit:
            action = SmartDcaAction.TAKE_PROFIT
            if target_hit:
                reasons.append("达到目标年化收益，触发目标止盈")
            if trailing_hit:
                reasons.append("达到高位回撤阈值，触发移动止盈")
        else:
            raw_amount = item.base_amount * valuation_multiplier * trend_multiplier_decimal
            amount = _round_amount(raw_amount, config)
            if amount < config.minimum_investment:
                action = SmartDcaAction.PAUSE
                reasons.append("动态金额低于最小定投金额，暂停本次投入")
            elif valuation_multiplier == config.high_valuation_multiplier:
                action = SmartDcaAction.HOLD
                reasons.append("高估区仅保留观察，不增加风险敞口")
            else:
                action = SmartDcaAction.INVEST
                reasons.append(f"建议投入{amount:.2f}元")
    else:
        reasons.append("证据门禁未通过，不生成投入金额")

    return SmartDcaDecision(
        symbol=item.symbol,
        as_of_date=item.as_of_date,
        action=action,
        amount=amount,
        valuation_percentile=percentile,
        trend_deviation=deviation,
        valuation_multiplier=valuation_multiplier,
        trend_multiplier=trend_multiplier_decimal,
        annualized_return=annualized_return,
        trailing_drawdown=trailing_drawdown,
        reasons=tuple(dict.fromkeys(reasons)),
        blockers=tuple(dict.fromkeys(blockers)),
    )


# Readable acronym aliases keep notebook/UI imports unsurprising.
SmartDCAConfig = SmartDcaConfig
SmartDCAInput = SmartDcaInput
SmartDCADecision = SmartDcaDecision
calculate_smart_dca_decision = calculate_smart_dca


__all__ = [
    "SmartDCAConfig",
    "SmartDCAInput",
    "SmartDCADecision",
    "SmartDcaAction",
    "SmartDcaConfig",
    "SmartDcaDecision",
    "SmartDcaInput",
    "calculate_smart_dca",
    "calculate_smart_dca_decision",
    "valuation_percentile",
]
