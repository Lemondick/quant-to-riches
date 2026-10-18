"""Research-only parameter scans and return-distribution uncertainty estimates.

The helpers in this module deliberately stop at descriptive research output.
They do not select production parameters, change a strategy configuration, or
submit an order.  A failed candidate is kept in the result table and prevents
the result from exposing a ``best_parameters`` value; this makes incomplete
research visible instead of turning it into an implicit recommendation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite, sqrt
from numbers import Integral, Real
from typing import Any

import numpy as np
import pandas as pd

from quant_assistant.common.serialization import canonical_json

MetricFunction = Callable[[np.ndarray], float]
MetricSpec = str | MetricFunction


@dataclass(frozen=True, slots=True)
class ParameterSensitivityResult:
    """Auditable scores for a finite set of parameter candidates."""

    results: pd.DataFrame
    best_parameters: Mapping[str, object] | None
    observations: int
    score_column: str = "score"
    research_only: bool = True
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    """A percentile interval for one return-derived metric."""

    metric: str
    estimate: float
    lower: float
    upper: float
    samples: int
    method: str
    blockers: tuple[str, ...] = ()


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{name} must be a finite number")
    return converted


def _canonical_parameters(parameters: Mapping[str, object]) -> str:
    try:
        return canonical_json(parameters)
    except (TypeError, ValueError) as exc:
        raise ValueError("parameter candidates must contain JSON-compatible values") from exc


def _score_value(value: object) -> float:
    if isinstance(value, Mapping):
        if "score" not in value:
            raise ValueError("evaluator result must contain a score field")
        value = value["score"]
    return _finite_number(value, "score")


def scan_parameter_sensitivity(
    parameter_candidates: Sequence[Mapping[str, object]],
    evaluator: Callable[[Mapping[str, object]], Mapping[str, object] | Real],
    *,
    maximize: bool = True,
) -> ParameterSensitivityResult:
    """Evaluate candidates deterministically and retain every failure.

    ``evaluator`` may return a number (the score) or a mapping containing a
    numeric ``score`` plus extra scalar diagnostics.  No best candidate is
    exposed when any candidate is invalid or failed, even if other rows scored
    successfully.  This is intentional: a partial scan is not evidence for a
    production parameter choice.
    """

    if not isinstance(parameter_candidates, Sequence) or isinstance(
        parameter_candidates, (str, bytes, bytearray)
    ):
        raise TypeError("parameter_candidates must be a sequence of mappings")
    if not parameter_candidates:
        raise ValueError("at least one parameter candidate is required")
    if not callable(evaluator):
        raise TypeError("evaluator must be callable")
    if not isinstance(maximize, bool):
        raise TypeError("maximize must be a bool")

    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    valid_scores: list[tuple[float, dict[str, object], int]] = []
    metric_columns: set[str] = set()
    for index, raw_parameters in enumerate(parameter_candidates):
        row: dict[str, Any] = {
            "candidate": index + 1,
            "parameters": "",
            "score": float("nan"),
            "status": "blocked",
            "error": "",
        }
        try:
            if not isinstance(raw_parameters, Mapping):
                raise TypeError("candidate must be a mapping")
            parameters = dict(raw_parameters)
            row["parameters"] = _canonical_parameters(parameters)
            evaluation = evaluator(parameters)
            if isinstance(evaluation, Mapping):
                diagnostics = dict(evaluation)
                row["score"] = _score_value(diagnostics)
                for name, value in diagnostics.items():
                    if name == "score":
                        continue
                    if not isinstance(name, str) or not name or name in row:
                        raise ValueError("evaluator diagnostic names must be non-empty and unique")
                    row[name] = _finite_number(value, f"diagnostic {name}")
                    metric_columns.add(name)
            else:
                row["score"] = _score_value(evaluation)
            row["status"] = "ok"
            valid_scores.append((float(row["score"]), parameters, index))
        except (TypeError, ValueError, KeyError) as exc:
            error_name = type(exc).__name__
            row["error"] = error_name
            blockers.append(f"候选{index + 1}评估失败:{error_name}")
        except Exception as exc:  # evaluator code is external to this module
            error_name = type(exc).__name__
            row["error"] = error_name
            blockers.append(f"候选{index + 1}评估失败:{error_name}")
        rows.append(row)

    columns = ["candidate", "parameters", "score", "status", "error", *sorted(metric_columns)]
    result_frame = pd.DataFrame(rows)
    for column in columns:
        if column not in result_frame:
            result_frame[column] = np.nan if column not in {"error", "status"} else ""
    result_frame = result_frame.loc[:, columns]
    if valid_scores and not blockers:
        selected = (
            max(valid_scores, key=lambda item: (item[0], -item[2]))
            if maximize
            else min(valid_scores, key=lambda item: (item[0], item[2]))
        )
        best_parameters: Mapping[str, object] | None = dict(selected[1])
        result_frame["rank"] = result_frame["score"].rank(
            ascending=not maximize, method="min", na_option="bottom"
        )
        result_frame["rank"] = result_frame["rank"].astype("Int64")
    else:
        best_parameters = None
        if valid_scores and blockers:
            blockers.append("参数扫描不完整，不暴露最佳参数")
        result_frame["rank"] = pd.Series(pd.array([pd.NA] * len(result_frame), dtype="Int64"))
    return ParameterSensitivityResult(
        results=result_frame,
        best_parameters=best_parameters,
        observations=len(result_frame),
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _prepare_returns(returns: Sequence[float] | np.ndarray | pd.Series) -> np.ndarray:
    if isinstance(returns, pd.Series):
        values = returns.to_numpy(dtype=float, copy=True)
    elif isinstance(returns, np.ndarray):
        values = np.asarray(returns, dtype=float)
    else:
        if isinstance(returns, (str, bytes, bytearray)):
            raise TypeError("returns must be a one-dimensional numeric sequence")
        try:
            values = np.asarray(list(returns), dtype=float)
        except (TypeError, ValueError) as exc:
            raise TypeError("returns must be a one-dimensional numeric sequence") from exc
    if values.ndim != 1:
        raise ValueError("returns must be one-dimensional")
    if values.size == 0:
        raise ValueError("returns must not be empty")
    if not np.isfinite(values).all():
        raise ValueError("returns must contain only finite values")
    if (values < -1).any():
        raise ValueError("returns cannot be below -100%")
    return values.astype(float, copy=False)


def _validate_simulation(iterations: int, confidence: float, seed: int) -> tuple[int, float, int]:
    if isinstance(iterations, bool) or not isinstance(iterations, Integral) or iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    if not isinstance(confidence, Real) or isinstance(confidence, bool):
        raise ValueError("confidence must be between 0 and 1")
    confidence_value = float(confidence)
    if not isfinite(confidence_value) or not 0 < confidence_value < 1:
        raise ValueError("confidence must be between 0 and 1")
    if isinstance(seed, bool) or not isinstance(seed, Integral):
        raise ValueError("seed must be an integer")
    return int(iterations), confidence_value, int(seed)


def total_return(returns: Sequence[float] | np.ndarray) -> float:
    """Compound total return for a path of simple returns."""

    values = _prepare_returns(returns)
    return float(np.prod(1.0 + values) - 1.0)


def annualized_sharpe(
    returns: Sequence[float] | np.ndarray, *, periods_per_year: int = 252
) -> float:
    """Annualized zero-risk-rate Sharpe ratio."""

    values = _prepare_returns(returns)
    if (
        isinstance(periods_per_year, bool)
        or not isinstance(periods_per_year, Integral)
        or periods_per_year <= 0
    ):
        raise ValueError("periods_per_year must be positive")
    if values.size < 2:
        return float("nan")
    deviation = float(values.std(ddof=1))
    if deviation == 0:
        return float("nan")
    return float(values.mean() / deviation * sqrt(periods_per_year))


def max_drawdown(returns: Sequence[float] | np.ndarray) -> float:
    """Return the worst peak-to-trough drawdown as a negative fraction."""

    values = _prepare_returns(returns)
    wealth = np.cumprod(1.0 + values)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], wealth)))
    drawdowns = wealth / peaks[1:] - 1.0
    return float(drawdowns.min())


def _metric_functions(
    metrics: Sequence[MetricSpec] | Mapping[str, MetricFunction],
) -> list[tuple[str, MetricFunction]]:
    if isinstance(metrics, Mapping):
        items = list(metrics.items())
    else:
        if isinstance(metrics, (str, bytes, bytearray)):
            raise TypeError("metrics must be a sequence of names or callables")
        items = []
        for metric in metrics:
            if isinstance(metric, str):
                items.append((metric, _named_metric(metric)))
            elif callable(metric):
                items.append((getattr(metric, "__name__", "custom_metric"), metric))
            else:
                raise TypeError("metrics must contain names or callables")
    if not items:
        raise ValueError("at least one metric is required")
    functions: list[tuple[str, MetricFunction]] = []
    for name, function in items:
        if not isinstance(name, str) or not name.strip() or not callable(function):
            raise TypeError("metric names must be non-empty and functions must be callable")
        functions.append((name.strip(), function))
    if len({name for name, _ in functions}) != len(functions):
        raise ValueError("metric names must be unique")
    return functions


def _named_metric(name: str) -> MetricFunction:
    normalized = name.strip().lower()
    if normalized in {"total_return", "total", "cumulative_return"}:
        return total_return
    if normalized in {"sharpe", "sharpe_zero_rate", "annualized_sharpe"}:
        return annualized_sharpe
    if normalized in {"max_drawdown", "drawdown"}:
        return max_drawdown
    raise ValueError(f"unsupported metric: {name}")


def _intervals(
    values: Sequence[np.ndarray],
    original: np.ndarray,
    functions: Sequence[tuple[str, MetricFunction]],
    *,
    confidence: float,
    method: str,
) -> tuple[ConfidenceInterval, ...]:
    alpha = (1.0 - confidence) / 2.0
    result: list[ConfidenceInterval] = []
    for (name, function), distribution in zip(functions, values, strict=True):
        blockers: list[str] = []
        try:
            estimate = _finite_number(function(original), f"{name} estimate")
        except Exception as exc:
            estimate = float("nan")
            blockers.append(f"点估计不可用:{type(exc).__name__}")
        finite_distribution = distribution[np.isfinite(distribution)]
        if len(finite_distribution) != len(distribution):
            blockers.append("模拟样本包含非有限指标")
        if not len(finite_distribution):
            lower = upper = float("nan")
            blockers.append("没有可用模拟样本")
        else:
            lower, upper = np.quantile(finite_distribution, [alpha, 1.0 - alpha])
            lower = float(lower)
            upper = float(upper)
        result.append(
            ConfidenceInterval(
                metric=name,
                estimate=estimate,
                lower=lower,
                upper=upper,
                samples=len(distribution),
                method=method,
                blockers=tuple(dict.fromkeys(blockers)),
            )
        )
    return tuple(result)


def bootstrap_confidence_intervals(
    returns: Sequence[float] | np.ndarray | pd.Series,
    *,
    metrics: Sequence[MetricSpec] | Mapping[str, MetricFunction] = (
        "total_return",
        "sharpe",
        "max_drawdown",
    ),
    iterations: int = 1000,
    confidence: float = 0.95,
    seed: int = 20260830,
) -> tuple[ConfidenceInterval, ...]:
    """Estimate metric uncertainty by iid bootstrap resampling of observations."""

    original = _prepare_returns(returns)
    iterations, confidence, seed = _validate_simulation(iterations, confidence, seed)
    functions = _metric_functions(metrics)
    rng = np.random.default_rng(seed)
    distributions = [np.empty(iterations, dtype=float) for _ in functions]
    for index in range(iterations):
        sample = rng.choice(original, size=original.size, replace=True)
        for metric_index, (_, function) in enumerate(functions):
            try:
                distributions[metric_index][index] = float(function(sample))
            except Exception:
                distributions[metric_index][index] = float("nan")
    return _intervals(
        distributions,
        original,
        functions,
        confidence=confidence,
        method="bootstrap",
    )


def monte_carlo_confidence_intervals(
    returns: Sequence[float] | np.ndarray | pd.Series,
    *,
    periods: int | None = None,
    metrics: Sequence[MetricSpec] | Mapping[str, MetricFunction] = (
        "total_return",
        "sharpe",
        "max_drawdown",
    ),
    iterations: int = 1000,
    confidence: float = 0.95,
    seed: int = 20260830,
) -> tuple[ConfidenceInterval, ...]:
    """Estimate iid return-path uncertainty via Monte Carlo resampling.

    This is a descriptive iid model: it preserves the empirical one-period
    return distribution but not serial correlation or regime changes.
    """

    original = _prepare_returns(returns)
    if periods is None:
        periods_value = int(original.size)
    elif isinstance(periods, bool) or not isinstance(periods, Integral) or periods <= 0:
        raise ValueError("periods must be a positive integer")
    else:
        periods_value = int(periods)
    iterations, confidence, seed = _validate_simulation(iterations, confidence, seed)
    functions = _metric_functions(metrics)
    rng = np.random.default_rng(seed)
    distributions = [np.empty(iterations, dtype=float) for _ in functions]
    for index in range(iterations):
        sample = rng.choice(original, size=periods_value, replace=True)
        for metric_index, (_, function) in enumerate(functions):
            try:
                distributions[metric_index][index] = float(function(sample))
            except Exception:
                distributions[metric_index][index] = float("nan")
    return _intervals(
        distributions,
        original,
        functions,
        confidence=confidence,
        method="monte_carlo_iid",
    )


__all__ = [
    "ConfidenceInterval",
    "ParameterSensitivityResult",
    "annualized_sharpe",
    "bootstrap_confidence_intervals",
    "max_drawdown",
    "monte_carlo_confidence_intervals",
    "scan_parameter_sensitivity",
    "total_return",
]
