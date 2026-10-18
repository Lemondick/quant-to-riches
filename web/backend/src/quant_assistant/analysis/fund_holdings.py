"""Point-in-time fund underlying holdings penetration and overlap analysis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class FundHoldingsResult:
    """Canonical holdings plus stock/industry overlap matrices."""

    as_of_date: date
    holdings: pd.DataFrame
    fund_industry: pd.DataFrame
    industry_overlap: pd.DataFrame
    stock_overlap: pd.DataFrame
    portfolio_industry_exposure: pd.DataFrame
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return bool(not self.holdings.empty and not self.blockers)


_REQUIRED = ("fund_symbol", "stock_symbol", "industry", "weight", "as_of_date")


def analyze_fund_holdings(
    holdings: pd.DataFrame,
    as_of_date: date,
    *,
    portfolio_weights: Mapping[str, float] | pd.DataFrame | None = None,
) -> FundHoldingsResult:
    """Select each fund's latest evidence not later than ``as_of_date``.

    Weights must be decimal fractions in ``[0, 1]``.  Percentage-form inputs
    (for example ``5.2`` meaning 5.2%) are rejected instead of guessed.
    """

    if not isinstance(holdings, pd.DataFrame):
        raise TypeError("holdings must be a pandas DataFrame")
    if isinstance(as_of_date, datetime) or not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")
    missing = [name for name in _REQUIRED if name not in holdings.columns]
    empty_frames = _empty_result_frames()
    if missing:
        return FundHoldingsResult(
            as_of_date,
            empty_frames[0],
            empty_frames[1],
            empty_frames[2],
            empty_frames[3],
            empty_frames[4],
            ("基金持仓缺少字段：" + ", ".join(missing),),
        )

    frame = holdings[list(_REQUIRED)].copy(deep=True)
    frame["fund_symbol"] = frame["fund_symbol"].astype("string").str.strip().str.upper()
    frame["stock_symbol"] = frame["stock_symbol"].astype("string").str.strip().str.upper()
    frame["industry"] = frame["industry"].astype("string").str.strip()
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"], errors="coerce").dt.date
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
    blockers: list[str] = []
    invalid_text = (
        frame["fund_symbol"].eq("")
        | frame["stock_symbol"].eq("")
        | frame["industry"].eq("")
        | frame["as_of_date"].isna()
    )
    if invalid_text.any():
        blockers.append(f"基金持仓有{int(invalid_text.sum())}行缺少代码、行业或目标日期")
    invalid_weight = (
        frame["weight"].isna()
        | ~np.isfinite(frame["weight"])
        | frame["weight"].lt(0)
        | frame["weight"].gt(1)
    )
    if invalid_weight.any():
        blockers.append("基金持仓权重必须是0到1之间的十进制比例")
    frame = frame.loc[~invalid_text & ~invalid_weight & frame["as_of_date"].le(as_of_date)].copy()
    if frame.empty:
        blockers.append("没有不晚于目标日的有效基金持仓证据")
        empty_frames = _empty_result_frames()
        return FundHoldingsResult(
            as_of_date,
            empty_frames[0],
            empty_frames[1],
            empty_frames[2],
            empty_frames[3],
            empty_frames[4],
            tuple(dict.fromkeys(blockers)),
        )

    latest_dates = frame.groupby("fund_symbol", sort=False)["as_of_date"].transform("max")
    frame = frame.loc[frame["as_of_date"].eq(latest_dates)].copy()
    frame = (
        frame.groupby(["fund_symbol", "stock_symbol", "industry", "as_of_date"], as_index=False)[
            "weight"
        ]
        .sum()
        .sort_values(["fund_symbol", "weight", "stock_symbol"], ascending=[True, False, True])
        .reset_index(drop=True)
    )
    fund_industry = (
        frame.groupby(["fund_symbol", "industry"], as_index=False)["weight"]
        .sum()
        .sort_values(["fund_symbol", "weight", "industry"], ascending=[True, False, True])
        .reset_index(drop=True)
    )
    industry_overlap = _overlap_matrix(fund_industry, key="industry")
    stock_overlap = _overlap_matrix(frame, key="stock_symbol")
    exposure = _portfolio_exposure(fund_industry, portfolio_weights, blockers)
    return FundHoldingsResult(
        as_of_date,
        frame,
        fund_industry,
        industry_overlap,
        stock_overlap,
        exposure,
        tuple(dict.fromkeys(blockers)),
    )


def _overlap_matrix(frame: pd.DataFrame, *, key: str) -> pd.DataFrame:
    funds = tuple(sorted(str(item) for item in frame["fund_symbol"].dropna().unique()))
    if not funds:
        return pd.DataFrame()
    lookup: dict[str, dict[str, float]] = {fund: {} for fund in funds}
    for row in frame.itertuples(index=False):
        lookup[str(row.fund_symbol)][str(getattr(row, key))] = float(row.weight)
    matrix = pd.DataFrame(index=funds, columns=funds, dtype=float)
    for left in funds:
        for right in funds:
            left_values = lookup[left]
            right_values = lookup[right]
            labels = set(left_values) | set(right_values)
            denominator = sum(
                max(left_values.get(label, 0), right_values.get(label, 0)) for label in labels
            )
            numerator = sum(
                min(left_values.get(label, 0), right_values.get(label, 0)) for label in labels
            )
            matrix.loc[left, right] = (
                1.0
                if denominator == 0 and left == right
                else (numerator / denominator if denominator > 0 else 0.0)
            )
    matrix.index.name = "fund_symbol"
    return matrix.round(6)


def _portfolio_exposure(
    fund_industry: pd.DataFrame,
    portfolio_weights: Mapping[str, float] | pd.DataFrame | None,
    blockers: list[str],
) -> pd.DataFrame:
    if portfolio_weights is None or fund_industry.empty:
        return pd.DataFrame(columns=["industry", "portfolio_exposure"])
    if isinstance(portfolio_weights, pd.DataFrame):
        if not {"fund_symbol", "weight"}.issubset(portfolio_weights.columns):
            blockers.append("组合基金权重缺少fund_symbol或weight")
            return pd.DataFrame(columns=["industry", "portfolio_exposure"])
        weights = dict(
            zip(
                portfolio_weights["fund_symbol"].astype(str).str.strip().str.upper(),
                pd.to_numeric(portfolio_weights["weight"], errors="coerce"),
                strict=True,
            )
        )
    elif isinstance(portfolio_weights, Mapping):
        weights = {str(key).strip().upper(): value for key, value in portfolio_weights.items()}
    else:
        raise TypeError("portfolio_weights must be a mapping, DataFrame, or None")
    normalized: dict[str, float] = {}
    for fund, value in weights.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = float("nan")
        if not isfinite(numeric) or numeric < 0 or numeric > 1:
            blockers.append(f"组合基金{fund}权重不是0到1之间的有限数")
            continue
        normalized[fund] = numeric
    if not normalized:
        blockers.append("没有有效的组合基金权重，无法计算行业穿透")
        return pd.DataFrame(columns=["industry", "portfolio_exposure"])
    joined = fund_industry.copy()
    joined["portfolio_weight"] = joined["fund_symbol"].map(normalized)
    joined = joined.loc[joined["portfolio_weight"].notna()].copy()
    if joined.empty:
        blockers.append("组合权重与持仓基金代码没有交集")
        return pd.DataFrame(columns=["industry", "portfolio_exposure"])
    joined["portfolio_exposure"] = joined["weight"] * joined["portfolio_weight"]
    return (
        joined.groupby("industry", as_index=False)["portfolio_exposure"]
        .sum()
        .sort_values("portfolio_exposure", ascending=False, kind="mergesort")
        .reset_index(drop=True)
    )


fund_holdings_penetration = analyze_fund_holdings


def _empty_result_frames() -> tuple[pd.DataFrame, ...]:
    return (
        pd.DataFrame(columns=list(_REQUIRED)),
        pd.DataFrame(columns=["fund_symbol", "industry", "weight"]),
        pd.DataFrame(),
        pd.DataFrame(columns=list(_REQUIRED)),
        pd.DataFrame(columns=["industry", "portfolio_exposure"]),
    )


__all__ = ["FundHoldingsResult", "analyze_fund_holdings", "fund_holdings_penetration"]
