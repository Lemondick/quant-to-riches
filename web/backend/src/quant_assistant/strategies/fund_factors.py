"""Transparent multi-factor public-fund selection.

The functions here operate on point-in-time frames and return an auditable
table.  Missing manager, size, liquidity, or benchmark evidence is represented
as ``NaN``/a blocker; it is never replaced with a population average.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite, sqrt
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class FundFactorConfig:
    """Thresholds used by :func:`score_fund_universe`."""

    minimum_observations: int = 60
    minimum_size: float = 50_000_000.0
    large_size_threshold: float = 100_000_000_000.0
    large_size_penalty: float = 10.0
    annualization_days: int = 252

    def __post_init__(self) -> None:
        if type(self.minimum_observations) is not int or self.minimum_observations < 2:
            raise ValueError("minimum_observations must be at least 2")
        if (
            not isfinite(self.minimum_size)
            or not isfinite(self.large_size_threshold)
            or self.minimum_size < 0
            or self.large_size_threshold <= 0
            or self.large_size_threshold < self.minimum_size
        ):
            raise ValueError("size thresholds are invalid")
        if not 0 <= self.large_size_penalty <= 100:
            raise ValueError("large_size_penalty must be between 0 and 100")
        if type(self.annualization_days) is not int or self.annualization_days <= 0:
            raise ValueError("annualization_days must be positive")


def _series(values: Sequence[float] | pd.Series | np.ndarray) -> pd.Series:
    result = pd.to_numeric(pd.Series(values), errors="coerce")
    return result.replace([np.inf, -np.inf], np.nan).astype(float)


def annualized_return(returns: Sequence[float] | pd.Series, periods_per_year: int = 252) -> float:
    """Annualise a periodic return series, preserving sign and compounding."""

    values = _series(returns)
    if values.empty or values.isna().any() or periods_per_year <= 0:
        return float("nan")
    wealth = (1.0 + values).to_numpy(dtype=float)
    if (wealth <= 0).any():
        return float("nan")
    return float(np.prod(wealth) ** (periods_per_year / len(values)) - 1.0)


def max_drawdown(returns: Sequence[float] | pd.Series) -> float:
    values = _series(returns)
    if values.empty or values.isna().any() or values.lt(-1).any():
        return float("nan")
    wealth = pd.Series((1.0 + values).cumprod())
    # Initial capital is a peak too: a loss in the first period must count.
    drawdown = wealth / wealth.cummax().clip(lower=1.0) - 1.0
    return float(drawdown.min())


def calmar_ratio(
    returns: Sequence[float] | pd.Series,
    periods_per_year: int = 252,
) -> float:
    """Annualised return divided by absolute maximum drawdown."""

    annual = annualized_return(returns, periods_per_year)
    drawdown = max_drawdown(returns)
    if not isfinite(annual) or not isfinite(drawdown):
        return float("nan")
    if drawdown >= 0:
        return float("inf") if annual > 0 else 0.0
    return float(annual / abs(drawdown))


def sortino_ratio(
    returns: Sequence[float] | pd.Series,
    periods_per_year: int = 252,
    target: float = 0.0,
) -> float:
    """Annualised return divided by annualised downside deviation."""

    values = _series(returns)
    annual = annualized_return(values, periods_per_year)
    downside = np.minimum(values.to_numpy(dtype=float) - target, 0.0)
    if len(values) == 0 or not isfinite(annual):
        return float("nan")
    deviation = float(np.sqrt(np.mean(np.square(downside))) * sqrt(periods_per_year))
    if deviation == 0:
        return float("inf") if annual > 0 else 0.0
    return float((annual - target) / deviation)


def information_ratio(
    fund_returns: Sequence[float] | pd.Series,
    benchmark_returns: Sequence[float] | pd.Series,
    periods_per_year: int = 252,
) -> float:
    """Compare matched observations; indexed series align by their period labels."""

    fund = _series(fund_returns)
    benchmark = _series(benchmark_returns)
    if not (isinstance(fund_returns, pd.Series) and isinstance(benchmark_returns, pd.Series)):
        if len(fund) != len(benchmark):
            return float("nan")
        fund = fund.reset_index(drop=True)
        benchmark = benchmark.reset_index(drop=True)
    if not fund.index.is_unique or not benchmark.index.is_unique or periods_per_year <= 0:
        return float("nan")
    paired = pd.concat([fund.rename("fund"), benchmark.rename("benchmark")], axis=1).dropna()
    if len(paired) < 2:
        return float("nan")
    active = (paired["fund"] - paired["benchmark"]).to_numpy()
    tracking_error = float(np.std(active, ddof=1))
    if tracking_error == 0:
        mean = float(np.mean(active))
        return float(np.copysign(np.inf, mean)) if mean != 0 else 0.0
    return float(np.mean(active) / tracking_error * sqrt(periods_per_year))


def max_drawdown_recovery_days(returns: Sequence[float] | pd.Series) -> float:
    """Return the longest number of observation periods spent below a prior peak.

    An ongoing drawdown counts to the last observation; this is not a calendar
    day count when observations are sparse.
    """

    values = _series(returns).reset_index(drop=True)
    if values.empty or values.isna().any() or values.lt(-1).any():
        return float("nan")
    wealth = (1.0 + values).cumprod().to_numpy()
    peaks = np.maximum(np.maximum.accumulate(wealth), 1.0)
    longest = 0
    trough_start: int | None = None
    for index, (current, peak) in enumerate(zip(wealth, peaks, strict=True)):
        if current < peak and trough_start is None:
            trough_start = index
        elif current >= peak and trough_start is not None:
            longest = max(longest, index - trough_start)
            trough_start = None
    if trough_start is not None:
        longest = max(longest, len(values) - trough_start)
    return float(longest)


def _first_value(row: Mapping[str, Any] | pd.Series, aliases: Sequence[str]) -> Any:
    for name in aliases:
        if name in row:
            value = row[name]
            if value is not None:
                try:
                    missing = bool(pd.isna(value))
                except (TypeError, ValueError):
                    missing = False
                if not missing:
                    return value
    return None


def manager_factor_scores(metadata: pd.DataFrame) -> pd.DataFrame:
    """Normalize explicit manager fields to 0–100 scores.

    Expected fields are ``manager_tenure_years``, ``manager_win_rate``,
    ``alpha_stability`` and ``drawdown_recovery_days``.  Common Chinese/CSV
    aliases are accepted, but absent fields stay unavailable.
    """

    if not isinstance(metadata, pd.DataFrame):
        raise TypeError("metadata must be a pandas DataFrame")
    if metadata.empty:
        return pd.DataFrame(columns=["symbol", "manager_score", "manager_blockers"])
    rows: list[dict[str, Any]] = []
    aliases = {
        "tenure": ("manager_tenure_years", "manager_years", "manager_tenure", "经理任职年限"),
        "win_rate": ("manager_win_rate", "manager_win", "manager_win_probability", "经理胜率"),
        "alpha_stability": (
            "alpha_stability",
            "manager_alpha_stability",
            "selection_alpha_stability",
            "选股alpha稳定性",
        ),
        "recovery_days": (
            "max_drawdown_recovery_days",
            "manager_recovery_days",
            "drawdown_recovery_days",
            "最大回撤修复天数",
        ),
    }
    for row in metadata.to_dict("records"):
        values: dict[str, float] = {}
        blockers: list[str] = []
        for key, names in aliases.items():
            raw = _first_value(row, names)
            try:
                number = float(raw)
            except (TypeError, ValueError):
                number = float("nan")
            invalid_range = (
                isinstance(raw, (bool, np.bool_))
                or (key in {"tenure", "recovery_days"} and number < 0)
                or (key == "win_rate" and not 0 <= number <= 100)
            )
            if not isfinite(number) or invalid_range:
                blockers.append(f"经理{key}证据缺失")
                number = float("nan")
            elif key == "win_rate" and number > 1:
                number /= 100
            values[key] = number
        # Percentile ranking is independent of unit (years, ratio, days).
        rows.append(
            {
                "symbol": str(_first_value(row, ("symbol", "fund_symbol")) or "").strip(),
                **values,
                "manager_blockers": blockers,
            }
        )
    result = pd.DataFrame(rows)
    for key in ("tenure", "win_rate", "alpha_stability", "recovery_days"):
        values = pd.to_numeric(result[key], errors="coerce")
        if key == "recovery_days":
            score = values.rank(pct=True, ascending=False) * 100
        else:
            score = values.rank(pct=True, ascending=True) * 100
        result[f"manager_{key}_score"] = score.round(4)
    score_columns = [
        f"manager_{key}_score" for key in ("tenure", "win_rate", "alpha_stability", "recovery_days")
    ]
    result["manager_score"] = result[score_columns].mean(axis=1, skipna=False).round(4)
    result["manager_blockers"] = result["manager_blockers"].map(tuple)
    return result


def _normalise_metric(series: pd.Series, *, higher_is_better: bool) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return (numeric.rank(pct=True, ascending=higher_is_better) * 100).round(4)


def _prepare_nav(frame: pd.DataFrame, as_of_date: date | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("nav_history must be a pandas DataFrame")
    aliases = {
        "symbol": ("symbol", "fund_symbol"),
        "date": ("nav_date", "trade_date", "date"),
        "nav": ("unit_nav", "nav", "close", "price"),
    }
    columns: dict[str, str] = {}
    for canonical, names in aliases.items():
        found = next((name for name in names if name in frame.columns), None)
        if found is None:
            raise ValueError(f"nav_history missing {canonical}")
        columns[canonical] = found
    result = frame.rename(columns={value: key for key, value in columns.items()}).copy(deep=True)
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date
    result["nav"] = pd.to_numeric(result["nav"], errors="coerce")
    if result["date"].isna().any():
        raise ValueError("nav_history contains invalid dates")
    if as_of_date is not None:
        result = result.loc[result["date"].le(as_of_date)]
    if result["symbol"].isna().any():
        raise ValueError("nav_history contains missing symbols")
    result["symbol"] = result["symbol"].astype(str).str.strip()
    if (
        result["symbol"].eq("").any()
        or not np.isfinite(result["nav"]).all()
        or not result["nav"].gt(0).all()
    ):
        raise ValueError("nav_history requires symbols and finite positive NAV values")
    if result.duplicated(["symbol", "date"]).any():
        raise ValueError("nav_history contains duplicate symbol/date observations")
    return result.sort_values(["symbol", "date"], kind="mergesort")


def score_fund_universe(
    nav_history: pd.DataFrame,
    benchmark_history: pd.DataFrame | None = None,
    fund_metadata: pd.DataFrame | None = None,
    *,
    as_of_date: date | None = None,
    config: FundFactorConfig | None = None,
) -> pd.DataFrame:
    """Score a point-in-time fund universe with explicit evidence gates."""

    config = config or FundFactorConfig()
    nav = _prepare_nav(nav_history, as_of_date)
    if nav.empty:
        return pd.DataFrame(columns=["symbol", "eligible", "blockers", "factor_score"])
    benchmark = None if benchmark_history is None else _prepare_nav(benchmark_history, as_of_date)
    metadata = (
        fund_metadata.copy(deep=True) if isinstance(fund_metadata, pd.DataFrame) else pd.DataFrame()
    )
    metadata_date_column: str | None = None
    if as_of_date is not None and not metadata.empty:
        metadata_date_column = next(
            (
                name
                for name in ("as_of_date", "effective_from", "manager_as_of")
                if name in metadata
            ),
            None,
        )
        if metadata_date_column is not None:
            metadata_dates = pd.to_datetime(metadata[metadata_date_column], errors="coerce").dt.date
            metadata = metadata.loc[metadata_dates.notna() & metadata_dates.le(as_of_date)].copy()
    if (
        not metadata.empty
        and "symbol" not in metadata.columns
        and "fund_symbol" in metadata.columns
    ):
        metadata = metadata.rename(columns={"fund_symbol": "symbol"})
    metadata_by_symbol: dict[str, Mapping[str, object]] = {}
    if not metadata.empty and "symbol" in metadata.columns:
        metadata = metadata.copy()
        metadata["_symbol_key"] = metadata["symbol"].astype(str).str.strip()
        if metadata_date_column is not None:
            metadata["_pit_date"] = pd.to_datetime(metadata[metadata_date_column], errors="coerce")
            metadata = metadata.sort_values(["_symbol_key", "_pit_date"], kind="mergesort")
        metadata = metadata.drop_duplicates("_symbol_key", keep="last")
        metadata_by_symbol = {
            str(row["_symbol_key"]): row for row in metadata.to_dict(orient="records")
        }
    manager = manager_factor_scores(metadata) if not metadata.empty else pd.DataFrame()
    manager_by_symbol: dict[str, Mapping[str, object]] = {}
    if not manager.empty and "symbol" in manager.columns:
        manager = manager.sort_values("symbol", kind="mergesort").drop_duplicates(
            "symbol", keep="last"
        )
        manager_by_symbol = {str(row["symbol"]): row for row in manager.to_dict(orient="records")}

    benchmark_frame: pd.DataFrame | None = None
    if benchmark is not None and not benchmark.empty:
        first_symbol = str(benchmark["symbol"].iloc[0])
        benchmark_rows = benchmark.loc[benchmark["symbol"].astype(str).eq(first_symbol)]
        if benchmark["symbol"].nunique() != 1:
            raise ValueError("benchmark_history must contain exactly one benchmark")
        benchmark_frame = benchmark_rows[["date"]].copy()
        benchmark_frame["start_date"] = benchmark_rows["date"].shift()
        benchmark_frame["benchmark_return"] = benchmark_rows["nav"].pct_change(fill_method=None)

    rows: list[dict[str, Any]] = []
    for symbol, group in nav.groupby(nav["symbol"].astype(str), sort=True):
        group = group.sort_values("date", kind="mergesort")
        returns = group["nav"].pct_change(fill_method=None).dropna().reset_index(drop=True)
        blockers: list[str] = []
        if len(returns) < config.minimum_observations:
            blockers.append(f"净值历史不足{config.minimum_observations}个观测")
        annual = annualized_return(returns, config.annualization_days)
        drawdown = max_drawdown(returns)
        calmar = calmar_ratio(returns, config.annualization_days)
        sortino = sortino_ratio(returns, config.annualization_days)
        ir = float("nan")
        if benchmark_frame is None:
            blockers.append("基准收益历史缺失，无法计算IR")
        else:
            joined = group[["date", "nav"]].copy()
            joined["start_date"] = joined["date"].shift()
            joined["fund_return"] = joined["nav"].pct_change(fill_method=None)
            aligned = joined.merge(benchmark_frame, on=["start_date", "date"], how="inner").dropna()
            ir = information_ratio(
                aligned["fund_return"], aligned["benchmark_return"], config.annualization_days
            )
            if len(aligned) < config.minimum_observations:
                blockers.append("与基准可对齐的历史不足，IR不可用")

        metadata_row = metadata_by_symbol.get(str(symbol))
        size = float("nan")
        liquidity = float("nan")
        if metadata_row is None:
            blockers.extend(("基金规模证据缺失", "流动性证据缺失"))
        else:
            record = metadata_row
            if as_of_date is not None and metadata_date_column is None:
                blockers.append("基金元数据时点缺失，无法验证当时可知")
            raw_size = _first_value(
                record, ("fund_size", "aum", "scale", "asset_size", "规模", "基金规模")
            )
            raw_liquidity = _first_value(
                record, ("avg_daily_volume", "liquidity", "turnover", "流动性", "日均成交额")
            )
            try:
                size = float(raw_size)
            except (TypeError, ValueError):
                size = float("nan")
            try:
                liquidity = float(raw_liquidity)
            except (TypeError, ValueError):
                liquidity = float("nan")
            if not isfinite(size):
                blockers.append("基金规模证据缺失")
            elif size < config.minimum_size:
                blockers.append(f"基金规模低于{config.minimum_size / 10_000:.0f}万元清盘风险阈值")
            if not isfinite(liquidity) or liquidity < 0 or isinstance(raw_liquidity, bool):
                blockers.append("流动性证据缺失")
                liquidity = float("nan")

        manager_row = manager_by_symbol.get(str(symbol))
        manager_score = float("nan") if manager_row is None else float(manager_row["manager_score"])
        if manager_row is None:
            blockers.append("基金经理能力证据缺失")
        else:
            blockers.extend(str(item) for item in manager_row["manager_blockers"])
        size_score = (
            float("nan")
            if not isfinite(size)
            else (50.0 if size == 0 else min(size / config.large_size_threshold * 100, 100))
        )
        liquidity_score = float("nan") if not isfinite(liquidity) else liquidity
        metrics = {
            "annualized_return": annual,
            "calmar": calmar,
            "sortino": sortino,
            "information_ratio": ir,
            "max_drawdown": drawdown,
            "manager_score": manager_score,
            "size_score": size_score,
            "liquidity_score": liquidity_score,
        }
        # Rank-based scores are only assigned after the universe is known.
        rows.append(
            {
                "symbol": str(symbol),
                "size": size,
                "liquidity": liquidity,
                **metrics,
                "blockers": tuple(dict.fromkeys(blockers)),
            }
        )
    result = pd.DataFrame(rows)
    result["return_score"] = _normalise_metric(result["annualized_return"], higher_is_better=True)
    result["calmar_score"] = _normalise_metric(result["calmar"], higher_is_better=True)
    result["sortino_score"] = _normalise_metric(result["sortino"], higher_is_better=True)
    result["ir_score"] = _normalise_metric(result["information_ratio"], higher_is_better=True)
    result["drawdown_score"] = _normalise_metric(result["max_drawdown"], higher_is_better=True)
    result["size_factor"] = _normalise_metric(result["size"], higher_is_better=True)
    result["liquidity_factor"] = _normalise_metric(result["liquidity"], higher_is_better=True)
    result["manager_factor"] = result["manager_score"]
    factor_columns = [
        "return_score",
        "calmar_score",
        "sortino_score",
        "ir_score",
        "drawdown_score",
        "manager_factor",
        "size_factor",
        "liquidity_factor",
    ]
    result["factor_score"] = result[factor_columns].mean(axis=1, skipna=False)
    result["large_fund_penalty"] = np.where(
        pd.to_numeric(result["size"], errors="coerce").ge(config.large_size_threshold),
        config.large_size_penalty,
        0.0,
    )
    result["factor_score"] = (result["factor_score"] - result["large_fund_penalty"]).clip(
        lower=0, upper=100
    )
    result["eligible"] = result["blockers"].map(len).eq(0) & result["factor_score"].notna()
    result["status"] = np.where(result["eligible"], "eligible", "blocked")
    return result.sort_values(
        ["eligible", "factor_score", "symbol"], ascending=[False, False, True], kind="mergesort"
    ).reset_index(drop=True)


# Short aliases are useful to notebooks and preserve readable names in UI code.
calculate_fund_factors = score_fund_universe
calculate_calmar_ratio = calmar_ratio
calculate_sortino_ratio = sortino_ratio
calculate_information_ratio = information_ratio


__all__ = [
    "FundFactorConfig",
    "annualized_return",
    "calmar_ratio",
    "calculate_calmar_ratio",
    "calculate_fund_factors",
    "calculate_information_ratio",
    "calculate_sortino_ratio",
    "information_ratio",
    "manager_factor_scores",
    "max_drawdown",
    "max_drawdown_recovery_days",
    "score_fund_universe",
    "sortino_ratio",
]
