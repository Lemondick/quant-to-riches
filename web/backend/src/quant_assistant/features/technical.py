"""Point-in-time-safe technical features for daily market data."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

_PRICE_COLUMNS = {
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}
_FEATURE_COLUMNS = [
    "ret_20",
    "ret_60",
    "ret_120",
    "ma_120",
    "ma_200",
    "trend_120",
    "trend_200",
    "volatility_20",
    "amount_20",
]


def calculate_stock_features(prices: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    """Return the latest complete technical-feature row for every symbol.

    Rows after ``as_of_date`` are removed before any rolling operation. A symbol
    needs at least 200 valid observations, because the base strategy relies on
    its 200-session moving average.
    """

    history = calculate_stock_feature_history(prices)
    latest = _latest_complete_features(history, as_of_date)
    return latest.reset_index(drop=True)


def calculate_stock_feature_history(prices: pd.DataFrame) -> pd.DataFrame:
    """Calculate rolling stock features once for every available historical row.

    Rolling indicators are causal: a row only depends on earlier rows for the same
    symbol.  A caller can therefore cache this full frame and select a point-in-time
    slice without recomputing the entire history for every signal date.
    """

    missing = _PRICE_COLUMNS.difference(prices.columns)
    if missing:
        formatted = ", ".join(sorted(missing))
        raise ValueError(f"prices missing required columns: {formatted}")

    history = prices.copy(deep=True)
    history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce")
    history = history.loc[history["trade_date"].notna() & history["symbol"].notna()].copy()

    for column in ("open", "high", "low", "close", "volume", "amount"):
        history[column] = pd.to_numeric(history[column], errors="coerce")

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
    history["amount_20"] = history.groupby("symbol", sort=False)["amount"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    history["trend_120"] = history["close"] / history["ma_120"] - 1
    history["trend_200"] = history["close"] / history["ma_200"] - 1
    history[_FEATURE_COLUMNS] = history[_FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    return history.reset_index(drop=True)


def _latest_complete_features(history: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    cutoff = pd.Timestamp(as_of_date)
    available = history.loc[history["trade_date"].le(cutoff)]
    latest = available.groupby("symbol", sort=False, as_index=False).tail(1)
    latest = latest.dropna(subset=["ret_120", "ma_200", "volatility_20", "amount_20"]).copy()
    latest["trade_date"] = latest["trade_date"].dt.date
    return latest


def robust_zscore(series: pd.Series, higher_is_better: bool) -> pd.Series:
    """Winsorize a cross-section at 5/95% and return a signed z-score.

    Missing observations stay missing so they cannot become eligible by
    accident. A constant or one-item valid cross-section receives zero scores.
    """

    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    valid = numeric.dropna()
    result = pd.Series(np.nan, index=series.index, dtype=float, name=series.name)
    if valid.empty:
        return result

    lower, upper = valid.quantile([0.05, 0.95])
    clipped = numeric.clip(lower=lower, upper=upper)
    standard_deviation = clipped.std()
    if not np.isfinite(standard_deviation) or np.isclose(standard_deviation, 0.0):
        result.loc[numeric.notna()] = 0.0
        return result

    result = (clipped - clipped.mean()) / standard_deviation
    return result if higher_is_better else -result
