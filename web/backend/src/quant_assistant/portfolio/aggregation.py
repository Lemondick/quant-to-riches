"""Deterministic multi-strategy signal aggregation.

The daily decision path currently keeps its legacy de-duplication behaviour.
This module provides the explicit portfolio-level aggregation boundary needed
for a later, backtest-validated weighting policy.  With no weights supplied it
is deliberately equivalent to the existing risk-first selector; supplying
weights opts into auditable fusion for same-direction increase signals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from math import isfinite

from quant_assistant.models.entities import Action, Signal

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class SignalConflict:
    """Audit record for a symbol where more than one strategy spoke."""

    symbol: str
    actions: tuple[str, ...]
    strategy_versions: tuple[str, ...]
    selected_strategy_version: str | None
    resolution: str


@dataclass(frozen=True, slots=True)
class SignalAggregation:
    """Aggregated signals plus conflict records for persistence or display."""

    signals: tuple[Signal, ...]
    conflicts: tuple[SignalConflict, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(self, "conflicts", tuple(self.conflicts))


def _action(signal: Signal) -> Action | None:
    try:
        return signal.action if isinstance(signal.action, Action) else Action(signal.action)
    except (TypeError, ValueError):
        return None


def _score(signal: Signal) -> float | None:
    try:
        score = float(signal.score)
    except (TypeError, ValueError):
        return None
    return score if isfinite(score) else None


def _decimal(value: object) -> Decimal | None:
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return converted if converted.is_finite() else None


def _risk_increasing(action: Action | None) -> bool:
    return action in {Action.BUY, Action.ADD}


def _conflict(
    symbol: str,
    candidates: Sequence[Signal],
    selected: Signal | None,
    resolution: str,
) -> SignalConflict:
    return SignalConflict(
        symbol=symbol,
        actions=tuple(sorted({str(_action(item).value) for item in candidates if _action(item)})),
        strategy_versions=tuple(sorted({str(item.strategy_version) for item in candidates})),
        selected_strategy_version=(selected.strategy_version if selected is not None else None),
        resolution=resolution,
    )


def _fuse_increases(
    candidates: Sequence[Signal],
    weights: Mapping[str, object],
) -> tuple[Signal | None, str]:
    """Fuse same-symbol increase signals using explicit positive weights."""

    weighted: list[tuple[Signal, Decimal, float, Decimal]] = []
    for signal in candidates:
        score = _score(signal)
        weight = _decimal(weights.get(signal.strategy_version))
        target = _decimal(signal.target_weight)
        if score is None or weight is None or weight <= ZERO or target is None or target < ZERO:
            continue
        weighted.append((signal, weight, score, min(ONE, target)))
    if not weighted:
        return None, "risk_increasing_weight_missing"
    asset_types = {item.asset_type for item, *_ in weighted}
    if len(asset_types) != 1:
        return None, "risk_increasing_asset_type_conflict"
    total_weight = sum((weight for _, weight, _, _ in weighted), start=ZERO)
    if total_weight <= ZERO:
        return None, "risk_increasing_weight_missing"
    score = sum((Decimal(str(score)) * weight for _, weight, score, _ in weighted), start=ZERO)
    target = sum((target * weight for _, weight, _, target in weighted), start=ZERO)
    target = min(ONE, max(ZERO, target / total_weight))
    representative = max(
        weighted,
        key=lambda item: (item[1], item[2], str(item[0].strategy_version)),
    )[0]
    versions = "+".join(sorted({str(item.strategy_version) for item, *_ in weighted}))
    reasons: list[str] = []
    for item, *_ in weighted:
        for reason in item.reasons:
            if reason not in reasons:
                reasons.append(reason)
    reasons.append(f"组合层信号融合：{versions}")
    return (
        replace(
            representative,
            score=float(score / total_weight),
            target_weight=target,
            valid_until=min(item.valid_until for item, *_ in weighted),
            strategy_version=f"aggregate[{versions}]",
            reasons=tuple(reasons),
        ),
        "risk_increasing_weighted_fusion",
    )


def aggregate_signals(
    signals: Sequence[Signal],
    *,
    strategy_weights: Mapping[str, object] | None = None,
) -> SignalAggregation:
    """Resolve one final signal per symbol and retain conflict audit data.

    Resolution order is intentionally conservative: ``SELL`` → ``REDUCE`` →
    ``NO_TRADE/HOLD`` → increases.  Increases select the highest finite score
    by default; an explicit ``strategy_weights`` mapping enables weighted
    fusion of same-symbol increases.  Missing/invalid weights produce no
    increase signal, preserving the fail-closed boundary.
    """

    grouped: dict[str, list[Signal]] = {}
    for signal in signals:
        symbol = str(getattr(signal, "symbol", "")).strip()
        if symbol and _action(signal) is not None:
            grouped.setdefault(symbol, []).append(signal)

    selected: list[Signal] = []
    conflicts: list[SignalConflict] = []
    for symbol in sorted(grouped):
        candidates = grouped[symbol]
        sells = [signal for signal in candidates if _action(signal) is Action.SELL]
        reduces = [signal for signal in candidates if _action(signal) is Action.REDUCE]
        blockers = [
            signal for signal in candidates if _action(signal) in {Action.NO_TRADE, Action.HOLD}
        ]
        increases = [
            signal
            for signal in candidates
            if _risk_increasing(_action(signal)) and _score(signal) is not None
        ]
        chosen: Signal | None = None
        resolution = "single_signal"
        if sells:
            chosen = sorted(
                sells,
                key=lambda item: (str(item.valid_until), item.strategy_version),
            )[0]
            resolution = "risk_reducing_sell_priority"
        elif reduces:
            chosen = sorted(
                reduces,
                key=lambda item: (_decimal(item.target_weight) or ZERO, item.strategy_version),
            )[0]
            resolution = "risk_reducing_reduce_priority"
        elif blockers:
            chosen = sorted(
                blockers,
                key=lambda item: (
                    0 if _action(item) is Action.NO_TRADE else 1,
                    str(item.valid_until),
                    item.strategy_version,
                ),
            )[0]
            resolution = "blocker_priority"
        elif increases:
            if strategy_weights is None:
                chosen = sorted(
                    increases,
                    key=lambda item: (-(_score(item) or 0.0), item.strategy_version),
                )[0]
                resolution = "risk_increasing_highest_score"
            else:
                chosen, resolution = _fuse_increases(increases, strategy_weights)

        if chosen is not None:
            selected.append(chosen)
        if len(candidates) > 1 or (strategy_weights is not None and increases and chosen is None):
            conflicts.append(_conflict(symbol, candidates, chosen, resolution))

    return SignalAggregation(tuple(selected), tuple(conflicts))


__all__ = ["SignalAggregation", "SignalConflict", "aggregate_signals"]
