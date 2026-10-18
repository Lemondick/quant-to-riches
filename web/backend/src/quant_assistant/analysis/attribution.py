"""Portfolio performance attribution primitives.

The implementation is intentionally explicit about the inputs used by the
Brinson-Fachler decomposition.  It produces rows suitable for a waterfall
chart while retaining the underlying allocation, selection and interaction
components for audit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class AttributionResult:
    """Brinson and holding-contribution outputs."""

    by_group: pd.DataFrame
    holding_contributions: pd.DataFrame
    total_return: float
    benchmark_return: float
    active_return: float
    blockers: tuple[str, ...] = ()


def _required(frame: pd.DataFrame, columns: Sequence[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} missing columns: {', '.join(missing)}")


def _clean(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{name} must be a pandas DataFrame")
    result = frame.copy(deep=True)
    _required(result, ("group", "weight", "return"), name)
    result["group"] = result["group"].astype(str).str.strip()
    result["weight"] = pd.to_numeric(result["weight"], errors="coerce")
    result["return"] = pd.to_numeric(result["return"], errors="coerce")
    result = result.loc[
        result["group"].ne("") & result["weight"].notna() & result["return"].notna()
    ].copy()
    if result.empty:
        raise ValueError(f"{name} has no valid rows")
    if (result["weight"] < 0).any():
        raise ValueError(f"{name} weight must be non-negative")
    result["weighted_return"] = result["weight"] * result["return"]
    grouped = result.groupby("group", as_index=False, sort=True).agg(
        weight=("weight", "sum"), weighted_return=("weighted_return", "sum")
    )
    if grouped["weight"].le(0).any():
        raise ValueError(f"{name} contains a zero-weight group")
    grouped["return"] = grouped["weighted_return"] / grouped["weight"]
    return grouped.drop(columns="weighted_return")


def brinson_attribution(portfolio: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    """Compute allocation, selection, and interaction by sector/asset group."""

    p = _clean(portfolio, "portfolio")
    b = _clean(benchmark, "benchmark")
    merged = p.merge(b, on="group", how="outer", suffixes=("_portfolio", "_benchmark")).fillna(0.0)
    benchmark_total = float((merged["weight_benchmark"] * merged["return_benchmark"]).sum())
    portfolio_total = float((merged["weight_portfolio"] * merged["return_portfolio"]).sum())
    merged["benchmark_total_return"] = benchmark_total
    merged["allocation"] = (merged["weight_portfolio"] - merged["weight_benchmark"]) * (
        merged["return_benchmark"] - benchmark_total
    )
    merged["selection"] = merged["weight_benchmark"] * (
        merged["return_portfolio"] - merged["return_benchmark"]
    )
    merged["interaction"] = (merged["weight_portfolio"] - merged["weight_benchmark"]) * (
        merged["return_portfolio"] - merged["return_benchmark"]
    )
    merged["total_effect"] = merged[["allocation", "selection", "interaction"]].sum(axis=1)
    result = (
        merged[
            [
                "group",
                "weight_portfolio",
                "weight_benchmark",
                "return_portfolio",
                "return_benchmark",
                "allocation",
                "selection",
                "interaction",
                "total_effect",
            ]
        ]
        .sort_values("total_effect", ascending=False, kind="mergesort")
        .reset_index(drop=True)
    )
    # The three components reconcile to active return when weights sum to one.
    result.attrs["portfolio_return"] = portfolio_total
    result.attrs["benchmark_return"] = benchmark_total
    result.attrs["active_return"] = portfolio_total - benchmark_total
    return result


def holding_contributions(
    holdings: pd.DataFrame,
    *,
    total_value: float | None = None,
) -> pd.DataFrame:
    """Return contribution and waterfall ordering for individual holdings.

    ``holdings`` must provide ``symbol`` and either ``weight``+``return`` or
    ``market_value``+``return``.  Values are point-in-time observations.
    """

    if not isinstance(holdings, pd.DataFrame):
        raise TypeError("holdings must be a pandas DataFrame")
    frame = holdings.copy(deep=True)
    _required(frame, ("symbol", "return"), "holdings")
    frame["symbol"] = frame["symbol"].astype(str).str.strip()
    frame["return"] = pd.to_numeric(frame["return"], errors="coerce")
    if "weight" in frame.columns:
        frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
    elif "market_value" in frame.columns:
        values = pd.to_numeric(frame["market_value"], errors="coerce")
        denominator = float(total_value) if total_value is not None else float(values.sum())
        if denominator <= 0:
            raise ValueError("total_value must be positive")
        frame["weight"] = values / denominator
    else:
        raise ValueError("holdings requires weight or market_value")
    frame = frame.loc[
        frame["symbol"].ne("") & frame["weight"].notna() & frame["return"].notna()
    ].copy()
    if (frame["weight"] < 0).any():
        raise ValueError("holding weight must be non-negative")
    frame["contribution"] = frame["weight"] * frame["return"]
    frame["contribution_pct"] = frame["contribution"] * 100
    frame["role"] = np.where(frame["contribution"] >= 0, "贡献", "拖累")
    return frame.sort_values("contribution", ascending=False, kind="mergesort").reset_index(
        drop=True
    )


def calculate_attribution(
    portfolio: pd.DataFrame,
    benchmark: pd.DataFrame,
    holdings: pd.DataFrame | None = None,
) -> AttributionResult:
    """Convenience wrapper returning both group and holding views."""

    group = brinson_attribution(portfolio, benchmark)
    holding = holding_contributions(holdings) if holdings is not None else pd.DataFrame()
    portfolio_return = float(group.attrs.get("portfolio_return", 0.0))
    benchmark_return = float(group.attrs.get("benchmark_return", 0.0))
    return AttributionResult(
        by_group=group,
        holding_contributions=holding,
        total_return=portfolio_return,
        benchmark_return=benchmark_return,
        active_return=portfolio_return - benchmark_return,
    )


brinson_fachler_attribution = brinson_attribution


__all__ = [
    "AttributionResult",
    "brinson_attribution",
    "calculate_attribution",
    "holding_contributions",
    "brinson_fachler_attribution",
]
