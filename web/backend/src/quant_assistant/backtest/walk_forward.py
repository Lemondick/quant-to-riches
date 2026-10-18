"""Chronological walk-forward validation with a conservative research-only gate."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date
from decimal import Decimal
from math import isfinite, isnan, sqrt
from numbers import Integral
from typing import Any, Protocol

import pandas as pd

from quant_assistant.backtest.metrics import PerformanceMetrics, calculate_performance


class BacktestRunner(Protocol):
    """Minimal protocol implemented by the production and test backtest engines."""

    def run(self, request: object) -> object: ...


@dataclass(frozen=True, slots=True)
class ValidationWindow:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date


@dataclass(frozen=True, slots=True)
class WalkForwardRequest:
    base_request: object
    windows: tuple[ValidationWindow, ...]
    parameter_candidates: tuple[Mapping[str, object], ...]
    selection_metric: str = "sharpe_zero_rate"
    engine: BacktestRunner | Callable[[object], object] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    benchmark: pd.DataFrame | None = field(default=None, repr=False, compare=False)
    limitations: tuple[str, ...] = ()
    benchmark_name: str = "benchmark"

    def __post_init__(self) -> None:
        object.__setattr__(self, "windows", tuple(self.windows))
        object.__setattr__(
            self,
            "parameter_candidates",
            tuple(dict(candidate) for candidate in self.parameter_candidates),
        )
        object.__setattr__(self, "limitations", tuple(str(item) for item in self.limitations))


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date
    selected_parameters: Mapping[str, object]
    test_metrics: PerformanceMetrics
    train_metrics: PerformanceMetrics | None = None
    validation_metrics: PerformanceMetrics | None = None
    snapshot_ids: tuple[int, ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    folds: tuple[WalkForwardFold, ...]
    aggregate_metrics: PerformanceMetrics
    research_only: bool
    limitations: tuple[str, ...]
    snapshot_ids: tuple[int, ...] = ()
    selection_metric: str = "sharpe_zero_rate"
    strategy_version: str = ""
    benchmark_name: str = "benchmark"


@dataclass(frozen=True, slots=True)
class _CandidateEvaluation:
    parameters: Mapping[str, object]
    train_metrics: PerformanceMetrics
    validation_metrics: PerformanceMetrics
    outputs: tuple[object, object]


_MINIMIZED_METRICS = {"annualized_volatility", "turnover", "total_fees"}


def _value(source: object, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _validate_request(request: WalkForwardRequest) -> None:
    if not request.windows:
        raise ValueError("at least one validation window is required")
    if not request.parameter_candidates:
        raise ValueError("at least one parameter candidate is required")
    metric_names = {item.name for item in fields(PerformanceMetrics)}
    if request.selection_metric not in metric_names:
        raise ValueError(f"unsupported selection metric: {request.selection_metric}")

    previous_test_end: date | None = None
    for window in request.windows:
        if not (
            window.train_start
            <= window.train_end
            < window.validation_start
            <= window.validation_end
            < window.test_start
            <= window.test_end
        ):
            raise ValueError(
                "train, validation, and test periods must be chronological and disjoint"
            )
        if previous_test_end is not None and window.test_start <= previous_test_end:
            raise ValueError("walk-forward test windows must not overlap")
        previous_test_end = window.test_end
    if any(not isinstance(candidate, Mapping) for candidate in request.parameter_candidates):
        raise TypeError("parameter candidates must be mappings")


def _merged_parameters(base_request: object, candidate: Mapping[str, object]) -> dict[str, object]:
    base_parameters = _value(base_request, "parameters", {})
    if base_parameters is None:
        base_parameters = {}
    if not isinstance(base_parameters, Mapping):
        raise TypeError("base request parameters must be a mapping")
    return {**dict(base_parameters), **dict(candidate)}


def _request_for_period(
    base_request: object,
    start: date,
    end: date,
    parameters: Mapping[str, object],
) -> object:
    """Clone the production request while changing only its bounded inputs."""

    if is_dataclass(base_request) and not isinstance(base_request, type):
        field_names = {item.name for item in fields(base_request)}
        updates: dict[str, object] = {}
        if "start" in field_names:
            updates["start"] = start
        elif "start_date" in field_names:
            updates["start_date"] = start
        else:
            raise TypeError("base request does not expose a start date")
        if "end" in field_names:
            updates["end"] = end
        elif "end_date" in field_names:
            updates["end_date"] = end
        else:
            raise TypeError("base request does not expose an end date")
        if "parameters" not in field_names:
            raise TypeError("base request does not expose parameters")
        updates["parameters"] = dict(parameters)
        return replace(base_request, **updates)

    if isinstance(base_request, Mapping):
        result = dict(base_request)
        start_key = "start" if "start" in result else "start_date"
        end_key = "end" if "end" in result else "end_date"
        result[start_key] = start
        result[end_key] = end
        result["parameters"] = dict(parameters)
        return result
    if hasattr(base_request, "model_copy"):
        return base_request.model_copy(
            update={"start": start, "end": end, "parameters": dict(parameters)}
        )
    raise TypeError("base request must be a dataclass, mapping, or model-copy object")


def _run(engine: BacktestRunner | Callable[[object], object], invocation: object) -> object:
    runner = getattr(engine, "run", None)
    return runner(invocation) if callable(runner) else engine(invocation)


def _result_benchmark(
    output: object,
    configured_benchmark: pd.DataFrame | None,
    base_request: object,
) -> pd.DataFrame | None:
    result_benchmark = _value(output, "benchmark")
    if isinstance(result_benchmark, pd.DataFrame):
        return result_benchmark
    if isinstance(configured_benchmark, pd.DataFrame):
        return configured_benchmark
    base_benchmark = _value(base_request, "benchmark")
    return base_benchmark if isinstance(base_benchmark, pd.DataFrame) else None


def _metrics_from_output(
    output: object,
    configured_benchmark: pd.DataFrame | None,
    base_request: object,
) -> PerformanceMetrics:
    if isinstance(output, PerformanceMetrics):
        return output
    metrics = _value(output, "metrics")
    if isinstance(metrics, PerformanceMetrics):
        return metrics
    nav = _value(output, "nav")
    if not isinstance(nav, pd.DataFrame):
        raise TypeError("backtest output must provide PerformanceMetrics or a nav DataFrame")
    benchmark = _result_benchmark(output, configured_benchmark, base_request)
    if benchmark is None:
        raise ValueError("a benchmark is required to calculate walk-forward metrics")
    return calculate_performance(nav, benchmark)


def _metric_value(metrics: PerformanceMetrics, name: str) -> float:
    value = getattr(metrics, name)
    return float(value)


def _is_better(
    candidate: _CandidateEvaluation,
    incumbent: _CandidateEvaluation | None,
    metric_name: str,
) -> bool:
    if incumbent is None:
        return True
    candidate_validation = _metric_value(candidate.validation_metrics, metric_name)
    incumbent_validation = _metric_value(incumbent.validation_metrics, metric_name)
    candidate_valid = isfinite(candidate_validation)
    incumbent_valid = isfinite(incumbent_validation)
    if candidate_valid != incumbent_valid:
        return candidate_valid
    if candidate_valid and candidate_validation != incumbent_validation:
        if metric_name in _MINIMIZED_METRICS:
            return candidate_validation < incumbent_validation
        return candidate_validation > incumbent_validation

    candidate_train = _metric_value(candidate.train_metrics, metric_name)
    incumbent_train = _metric_value(incumbent.train_metrics, metric_name)
    candidate_train_valid = isfinite(candidate_train)
    incumbent_train_valid = isfinite(incumbent_train)
    if candidate_train_valid != incumbent_train_valid:
        return candidate_train_valid
    if candidate_train_valid and candidate_train != incumbent_train:
        if metric_name in _MINIMIZED_METRICS:
            return candidate_train < incumbent_train
        return candidate_train > incumbent_train
    return False


def _limitations(output: object) -> tuple[str, ...]:
    raw = _value(output, "limitations", ()) or ()
    if isinstance(raw, str):
        raw = (raw,)
    return tuple(str(item) for item in raw if str(item))


def _snapshot_ids(output: object) -> tuple[int, ...]:
    identifiers: list[int] = []
    direct = _value(output, "data_snapshot_id")
    if isinstance(direct, Integral) and not isinstance(direct, bool):
        identifiers.append(int(direct))
    multiple = _value(output, "snapshot_ids", ()) or ()
    if isinstance(multiple, Integral) and not isinstance(multiple, bool):
        multiple = (multiple,)
    for item in multiple:
        if isinstance(item, Integral) and not isinstance(item, bool):
            identifiers.append(int(item))
    nav = _value(output, "nav")
    if isinstance(nav, pd.DataFrame) and "data_snapshot_id" in nav.columns:
        identifiers.extend(
            int(item)
            for item in nav["data_snapshot_id"].dropna().unique().tolist()
            if isinstance(item, Integral) and not isinstance(item, bool)
        )
    return tuple(sorted(set(identifiers)))


def _deduplicate(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))


def _finite_average(values: Sequence[float]) -> float:
    valid = [value for value in values if isfinite(value)]
    return sum(valid) / len(valid) if valid else float("nan")


def _compound(values: Sequence[float]) -> float:
    valid = [value for value in values if isfinite(value)]
    if not valid:
        return float("nan")
    result = 1.0
    for value in valid:
        result *= 1.0 + value
    return result - 1.0


def _aggregate_metrics(folds: Sequence[WalkForwardFold]) -> PerformanceMetrics:
    metrics = [fold.test_metrics for fold in folds]
    total_return = _compound([item.total_return for item in metrics])
    benchmark_return = _compound([item.benchmark_return for item in metrics])
    total_fees = sum((item.total_fees for item in metrics), start=Decimal("0"))
    drawdowns = [item.max_drawdown for item in metrics if isfinite(item.max_drawdown)]
    volatilities = [
        item.annualized_volatility for item in metrics if isfinite(item.annualized_volatility)
    ]
    aggregate_volatility = (
        sqrt(sum(value * value for value in volatilities) / len(volatilities))
        if volatilities
        else float("nan")
    )
    excess_return = (
        total_return - benchmark_return
        if isfinite(total_return) and isfinite(benchmark_return)
        else float("nan")
    )
    return PerformanceMetrics(
        total_return=total_return,
        annualized_return=_finite_average([item.annualized_return for item in metrics]),
        annualized_volatility=aggregate_volatility,
        max_drawdown=min(drawdowns) if drawdowns else float("nan"),
        sharpe_zero_rate=_finite_average([item.sharpe_zero_rate for item in metrics]),
        turnover=sum(item.turnover for item in metrics if not isnan(item.turnover)),
        total_fees=total_fees,
        benchmark_return=benchmark_return,
        excess_return=excess_return,
    )


def run_walk_forward(
    request: WalkForwardRequest,
    engine: BacktestRunner | Callable[[object], object] | None = None,
    benchmark: pd.DataFrame | None = None,
) -> WalkForwardResult:
    """Run chronological folds without using a test result for parameter selection."""

    if not isinstance(request, WalkForwardRequest):
        raise TypeError("request must be WalkForwardRequest")
    _validate_request(request)
    selected_engine = engine or request.engine or _value(request.base_request, "engine")
    if selected_engine is None or not (
        callable(selected_engine) or callable(getattr(selected_engine, "run", None))
    ):
        raise ValueError("a callable backtest engine is required")
    selected_benchmark = benchmark if benchmark is not None else request.benchmark

    all_limitations: list[str] = list(request.limitations)
    base_reasons = _value(request.base_request, "research_only_reasons", ()) or ()
    all_limitations.extend(str(item) for item in base_reasons)
    all_snapshot_ids: set[int] = set()
    any_engine_research_only = False
    folds: list[WalkForwardFold] = []

    for window in request.windows:
        best: _CandidateEvaluation | None = None
        for candidate in request.parameter_candidates:
            parameters = _merged_parameters(request.base_request, candidate)
            train_request = _request_for_period(
                request.base_request,
                window.train_start,
                window.train_end,
                parameters,
            )
            train_output = _run(selected_engine, train_request)
            train_metrics = _metrics_from_output(
                train_output,
                selected_benchmark,
                request.base_request,
            )

            validation_request = _request_for_period(
                request.base_request,
                window.validation_start,
                window.validation_end,
                parameters,
            )
            validation_output = _run(selected_engine, validation_request)
            validation_metrics = _metrics_from_output(
                validation_output,
                selected_benchmark,
                request.base_request,
            )
            evaluation = _CandidateEvaluation(
                parameters=dict(parameters),
                train_metrics=train_metrics,
                validation_metrics=validation_metrics,
                outputs=(train_output, validation_output),
            )
            if _is_better(evaluation, best, request.selection_metric):
                best = evaluation

            for output in (train_output, validation_output):
                all_limitations.extend(_limitations(output))
                all_snapshot_ids.update(_snapshot_ids(output))
                any_engine_research_only |= bool(_value(output, "research_only", False))

        if best is None:
            raise RuntimeError("no parameter candidate could be evaluated")

        # The test request is created only after the train/validation winner is fixed.
        test_request = _request_for_period(
            request.base_request,
            window.test_start,
            window.test_end,
            best.parameters,
        )
        test_output = _run(selected_engine, test_request)
        test_metrics = _metrics_from_output(
            test_output,
            selected_benchmark,
            request.base_request,
        )
        fold_limitations = _deduplicate(
            [
                *(_limitations(best.outputs[0])),
                *(_limitations(best.outputs[1])),
                *(_limitations(test_output)),
            ]
        )
        fold_snapshot_ids = tuple(
            sorted(
                {
                    *(_snapshot_ids(best.outputs[0])),
                    *(_snapshot_ids(best.outputs[1])),
                    *(_snapshot_ids(test_output)),
                }
            )
        )
        all_limitations.extend(_limitations(test_output))
        all_snapshot_ids.update(fold_snapshot_ids)
        any_engine_research_only |= bool(_value(test_output, "research_only", False))
        folds.append(
            WalkForwardFold(
                train_start=window.train_start,
                train_end=window.train_end,
                validation_start=window.validation_start,
                validation_end=window.validation_end,
                test_start=window.test_start,
                test_end=window.test_end,
                selected_parameters=dict(best.parameters),
                test_metrics=test_metrics,
                train_metrics=best.train_metrics,
                validation_metrics=best.validation_metrics,
                snapshot_ids=fold_snapshot_ids,
                limitations=fold_limitations,
            )
        )

    limitations = _deduplicate(all_limitations)
    strategy_version = str(_value(request.base_request, "strategy_version", ""))
    return WalkForwardResult(
        folds=tuple(folds),
        aggregate_metrics=_aggregate_metrics(folds),
        research_only=bool(limitations) or any_engine_research_only,
        limitations=limitations,
        snapshot_ids=tuple(sorted(all_snapshot_ids)),
        selection_metric=request.selection_metric,
        strategy_version=strategy_version,
        benchmark_name=request.benchmark_name,
    )


__all__ = [
    "BacktestRunner",
    "ValidationWindow",
    "WalkForwardFold",
    "WalkForwardRequest",
    "WalkForwardResult",
    "run_walk_forward",
]
