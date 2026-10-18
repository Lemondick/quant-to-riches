"""Point-in-time-safe benchmark features used by public-fund strategies."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

_REQUIRED_COLUMNS = {"symbol", "trade_date", "close"}
_COMPLETE_FEATURES = [
    "ret_20",
    "ret_60",
    "ret_120",
    "ma_120",
    "ma_200",
    "trend_120",
    "trend_200",
    "volatility_20",
    "drawdown_120",
    "max_drawdown_120",
]


def _rolling_max_drawdown(values: np.ndarray) -> float:
    peaks = np.maximum.accumulate(values)
    return float(np.min(values / peaks - 1.0))


def calculate_fund_features(benchmark_prices: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    """Return the latest complete feature row for each fund benchmark.

    The transformation removes future observations before calculating any
    return, moving average, volatility, or drawdown. At least 200 observations
    are required because the strategy's trend gate uses a 200-session average.
    """

    missing = _REQUIRED_COLUMNS.difference(benchmark_prices.columns)
    if missing:
        formatted = ", ".join(sorted(missing))
        raise ValueError(f"benchmark_prices missing required columns: {formatted}")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")

    history = benchmark_prices.copy(deep=True)
    history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce")
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    history = history.loc[
        history["trade_date"].notna()
        & history["trade_date"].le(pd.Timestamp(as_of_date))
        & history["symbol"].notna()
        & history["close"].gt(0)
    ].copy()
    history = history.sort_values(["symbol", "trade_date"], kind="mergesort")
    history = history.drop_duplicates(["symbol", "trade_date"], keep="last")
    grouped_close = history.groupby("symbol", sort=False)["close"]

    history["ret_20"] = grouped_close.pct_change(20, fill_method=None)
    history["ret_60"] = grouped_close.pct_change(60, fill_method=None)
    history["ret_120"] = grouped_close.pct_change(120, fill_method=None)
    history["ma_120"] = grouped_close.transform(
        lambda values: values.rolling(120, min_periods=120).mean()
    )
    history["ma_200"] = grouped_close.transform(
        lambda values: values.rolling(200, min_periods=200).mean()
    )
    history["volatility_20"] = grouped_close.transform(
        lambda values: (
            values.pct_change(fill_method=None).rolling(20, min_periods=20).std() * np.sqrt(252)
        )
    )
    history["drawdown_120"] = (
        history["close"]
        / grouped_close.transform(lambda values: values.rolling(120, min_periods=120).max())
        - 1
    )
    history["max_drawdown_120"] = grouped_close.transform(
        lambda values: values.rolling(120, min_periods=120).apply(_rolling_max_drawdown, raw=True)
    )
    history["trend_120"] = history["close"] / history["ma_120"] - 1
    history["trend_200"] = history["close"] / history["ma_200"] - 1

    history[_COMPLETE_FEATURES] = history[_COMPLETE_FEATURES].replace([np.inf, -np.inf], np.nan)
    latest = history.groupby("symbol", sort=False, as_index=False).tail(1)
    latest = latest.dropna(subset=_COMPLETE_FEATURES).copy()
    latest["trade_date"] = latest["trade_date"].dt.date
    return latest.reset_index(drop=True)


def calculate_benchmark_features(benchmark_prices: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    """Explicitly named alias for :func:`calculate_fund_features`."""

    return calculate_fund_features(benchmark_prices, as_of_date)
