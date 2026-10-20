"""Auditable close-to-close OOS observations; never invent fold-gap returns."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from quant_assistant.backtest.metrics import (
    PerformanceMetrics,
    _normalise_frame,
    _numeric,
    calculate_performance,
    cash_flow_adjusted_unit_nav,
)
from quant_assistant.common.tabular import first_column


@dataclass(frozen=True, slots=True)
class OosReturn:
    date: date
    strategy_return: float
    benchmark_return: float
    adjusted_unit_nav: float
    benchmark_close: float


@dataclass(frozen=True, slots=True)
class OosSeries:
    baseline_date: date
    adjusted_baseline_nav: float
    benchmark_baseline_close: float
    returns: tuple[OosReturn, ...]


def collect_oos(
    nav: pd.DataFrame | None,
    benchmark: pd.DataFrame | None,
    start: date,
    end: date,
    trading_dates=None,
) -> tuple[OosSeries, PerformanceMetrics]:
    if not isinstance(nav, pd.DataFrame):
        raise ValueError("oos_nav_required: test metrics cannot replace dated observations")
    if nav.empty:
        raise ValueError("insufficient_oos_observations")
    unit = cash_flow_adjusted_unit_nav(nav)
    if not isinstance(unit.index, pd.DatetimeIndex):
        raise ValueError("oos_nav_dates_required")
    if len(unit) < 2:
        raise ValueError("insufficient_oos_observations")
    if any(not start <= day.date() <= end for day in unit.index):
        raise ValueError("oos_nav_dates_outside_window")
    if trading_dates is None:
        raise ValueError("oos_trading_dates_required")
    declared = pd.DatetimeIndex(pd.to_datetime(tuple(trading_dates), errors="raise"))
    if declared.tz is not None:
        declared = declared.tz_localize(None)
    declared = declared.normalize().sort_values()
    if declared.has_duplicates or not declared.equals(unit.index):
        raise ValueError("oos_trading_dates_mismatch")
    if not isinstance(benchmark, pd.DataFrame) or benchmark.empty:
        raise ValueError("oos_benchmark_dates_required")
    frame = _normalise_frame(benchmark, "benchmark")
    index = pd.Index(frame["_metric_key"])
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("oos_benchmark_dates_required")
    column = first_column(frame, ("close", "benchmark_close", "unit_nav", "value"))
    if column is None:
        raise ValueError("oos_benchmark_close_required")
    closes = _numeric(frame, column, "benchmark close")
    if (closes <= 0).any():
        raise ValueError("oos_benchmark_close_invalid")
    closes.index = index
    closes = closes.loc[(closes.index >= pd.Timestamp(start)) & (closes.index <= pd.Timestamp(end))]
    if not closes.index.equals(unit.index):
        raise ValueError("oos_benchmark_dates_mismatch")
    strategy_returns = unit.pct_change(fill_method=None)
    benchmark_returns = closes.pct_change(fill_method=None)
    observations = tuple(
        OosReturn(
            day.date(),
            float(strategy_returns.loc[day]),
            float(benchmark_returns.loc[day]),
            float(unit.loc[day]),
            float(closes.loc[day]),
        )
        for day in unit.index[1:]
    )
    series = OosSeries(
        unit.index[0].date(), float(unit.iloc[0]), float(closes.iloc[0]), observations
    )
    # Test metrics and costs must reflect the same actual input as the OOS evidence.
    return series, calculate_performance(nav, benchmark)


def concatenate_oos(series: Sequence[OosSeries]) -> PerformanceMetrics:
    if not series or any(not item.returns for item in series):
        raise ValueError("insufficient_oos_observations")
    value, benchmark = 1.0, 1.0
    previous = series[0].baseline_date
    nav_rows = [{"trade_date": previous, "unit_nav": value}]
    benchmark_rows = [{"trade_date": previous, "close": benchmark}]
    for number, item in enumerate(series):
        if number and item.baseline_date <= previous:
            raise ValueError("oos_dates_overlap")
        for observation in item.returns:
            if observation.date <= previous:
                raise ValueError("oos_dates_overlap")
            value *= 1 + observation.strategy_return
            benchmark *= 1 + observation.benchmark_return
            nav_rows.append({"trade_date": observation.date, "unit_nav": value})
            benchmark_rows.append({"trade_date": observation.date, "close": benchmark})
            previous = observation.date
    return calculate_performance(pd.DataFrame(nav_rows), pd.DataFrame(benchmark_rows))
