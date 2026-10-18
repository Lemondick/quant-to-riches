"""Point-in-time factor effectiveness diagnostics.

The functions in this module validate a factor *after* its signal date against
forward returns supplied by the caller.  They never infer a return from a
future row implicitly, making the look-ahead boundary explicit and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class FactorIcReport:
    """Cross-sectional rank-IC and IC-IR summary."""

    observations: int
    dates: int
    mean_ic: float
    ic_std: float
    ic_ir: float
    positive_ic_ratio: float
    by_date: pd.DataFrame
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LayerBacktestReport:
    """Forward-return results for deterministic factor quantile layers."""

    layers: int
    observations: int
    by_date: pd.DataFrame
    summary: pd.DataFrame
    long_short_mean: float
    long_short_ir: float
    blockers: tuple[str, ...] = ()


def _prepare(
    frame: pd.DataFrame,
    date_column: str,
    factor_column: str,
    return_column: str,
) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("factor observations must be a pandas DataFrame")
    required = {date_column, "symbol", factor_column, return_column}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"factor observations missing columns: {', '.join(missing)}")
    result = frame.loc[:, [date_column, "symbol", factor_column, return_column]].copy(deep=True)
    result[date_column] = pd.to_datetime(result[date_column], errors="coerce").dt.date
    result[factor_column] = pd.to_numeric(result[factor_column], errors="coerce")
    result[return_column] = pd.to_numeric(result[return_column], errors="coerce")
    result["symbol"] = result["symbol"].astype("string").str.strip()
    result = result.loc[
        result[date_column].notna()
        & result["symbol"].notna()
        & result["symbol"].ne("")
        & np.isfinite(result[factor_column])
        & np.isfinite(result[return_column])
    ]
    if result.duplicated([date_column, "symbol"]).any():
        raise ValueError("factor observations contain duplicate date/symbol rows")
    return result.sort_values([date_column, "symbol"], kind="mergesort").reset_index(drop=True)


def cross_sectional_ic(
    observations: pd.DataFrame,
    *,
    date_column: str = "trade_date",
    factor_column: str = "factor",
    return_column: str = "forward_return",
    minimum_observations: int = 5,
) -> FactorIcReport:
    """Calculate daily Spearman rank IC and its information ratio."""

    if isinstance(minimum_observations, bool) or minimum_observations < 2:
        raise ValueError("minimum_observations must be at least 2")
    frame = _prepare(observations, date_column, factor_column, return_column)
    rows: list[dict[str, object]] = []
    blockers: list[str] = []
    for observed_date, group in frame.groupby(date_column, sort=True):
        if len(group) < minimum_observations:
            continue
        ic = (
            group[factor_column]
            .rank(method="average")
            .corr(group[return_column].rank(method="average"))
        )
        if ic is not None and isfinite(float(ic)):
            rows.append(
                {
                    "trade_date": observed_date,
                    "ic": float(ic),
                    "observations": len(group),
                }
            )
    by_date = pd.DataFrame(rows, columns=["trade_date", "ic", "observations"])
    if by_date.empty:
        blockers.append(f"没有达到每期至少{minimum_observations}个标的的截面")
        return FactorIcReport(
            observations=0,
            dates=0,
            mean_ic=float("nan"),
            ic_std=float("nan"),
            ic_ir=float("nan"),
            positive_ic_ratio=float("nan"),
            by_date=by_date,
            blockers=tuple(blockers),
        )
    values = by_date["ic"].to_numpy(dtype=float)
    mean_ic = float(values.mean())
    ic_std = float(values.std(ddof=1)) if len(values) > 1 else float("nan")
    ic_ir = (
        float(mean_ic / ic_std * sqrt(len(values))) if ic_std and isfinite(ic_std) else float("nan")
    )
    return FactorIcReport(
        observations=int(by_date["observations"].sum()),
        dates=len(by_date),
        mean_ic=mean_ic,
        ic_std=ic_std,
        ic_ir=ic_ir,
        positive_ic_ratio=float((values > 0).mean()),
        by_date=by_date,
        blockers=tuple(blockers),
    )


def layer_backtest(
    observations: pd.DataFrame,
    *,
    layers: int = 5,
    date_column: str = "trade_date",
    factor_column: str = "factor",
    return_column: str = "forward_return",
    minimum_observations: int = 5,
) -> LayerBacktestReport:
    """Assign deterministic factor quantile layers and evaluate long-short spread."""

    if isinstance(layers, bool) or not 2 <= layers <= 20:
        raise ValueError("layers must be between 2 and 20")
    if isinstance(minimum_observations, bool) or minimum_observations < layers:
        raise ValueError("minimum_observations must be at least layers")
    frame = _prepare(observations, date_column, factor_column, return_column)
    rows: list[dict[str, object]] = []
    for observed_date, group in frame.groupby(date_column, sort=True):
        if len(group) < minimum_observations:
            continue
        ordered = group.sort_values([factor_column, "symbol"], kind="mergesort").reset_index(
            drop=True
        )
        # qcut can drop bins for ties; rank first gives every row a stable order.
        ordered["layer"] = (
            pd.qcut(ordered[factor_column].rank(method="first"), q=layers, labels=False).astype(int)
            + 1
        )
        for layer, layer_group in ordered.groupby("layer", sort=True):
            rows.append(
                {
                    "trade_date": observed_date,
                    "layer": int(layer),
                    "mean_forward_return": float(layer_group[return_column].mean()),
                    "observations": len(layer_group),
                }
            )
    by_date = pd.DataFrame(
        rows,
        columns=["trade_date", "layer", "mean_forward_return", "observations"],
    )
    blockers: list[str] = []
    if by_date.empty:
        blockers.append(f"没有达到每期至少{minimum_observations}个标的的分层截面")
        empty = pd.DataFrame(
            columns=["layer", "mean_forward_return", "observations", "cumulative_return"]
        )
        return LayerBacktestReport(
            layers=layers,
            observations=0,
            by_date=by_date,
            summary=empty,
            long_short_mean=float("nan"),
            long_short_ir=float("nan"),
            blockers=tuple(blockers),
        )
    summary = (
        by_date.groupby("layer", as_index=False)
        .agg(
            mean_forward_return=("mean_forward_return", "mean"),
            observations=("observations", "sum"),
        )
        .sort_values("layer", kind="mergesort")
        .reset_index(drop=True)
    )
    summary["cumulative_return"] = summary["mean_forward_return"].fillna(0).add(1).cumprod().sub(1)
    pivot = by_date.pivot(index="trade_date", columns="layer", values="mean_forward_return")
    spread = (pivot[layers] - pivot[1]).dropna()
    spread_values = spread.to_numpy(dtype=float)
    spread_mean = float(spread_values.mean()) if len(spread_values) else float("nan")
    spread_std = float(spread_values.std(ddof=1)) if len(spread_values) > 1 else float("nan")
    if spread_std == 0 and isfinite(spread_mean):
        spread_ir = float("inf") if spread_mean > 0 else 0.0
    elif spread_std and isfinite(spread_std):
        spread_ir = float(spread_mean / spread_std * sqrt(len(spread_values)))
    else:
        spread_ir = float("nan")
    return LayerBacktestReport(
        layers=layers,
        observations=int(by_date["observations"].sum()),
        by_date=by_date,
        summary=summary,
        long_short_mean=spread_mean,
        long_short_ir=spread_ir,
        blockers=tuple(blockers),
    )


# Descriptive aliases for callers that prefer the longer research terminology.
factor_ic_report = cross_sectional_ic
stratified_layer_backtest = layer_backtest


__all__ = [
    "FactorIcReport",
    "LayerBacktestReport",
    "cross_sectional_ic",
    "factor_ic_report",
    "layer_backtest",
    "stratified_layer_backtest",
]
