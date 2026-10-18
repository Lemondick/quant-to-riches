"""Conservative market-risk regime derived from the CSI 300 trend."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from quant_assistant.models.entities import MarketRegime


def classify_market_regime(index_prices: pd.DataFrame, as_of_date: date) -> MarketRegime:
    """Classify the benchmark as green, yellow, or red without look-ahead.

    Green requires both a close above the 200-session moving average and a
    positive 20-session return. Yellow has exactly one condition. Missing or
    invalid history fails closed to red.
    """

    missing = {"trade_date", "close"}.difference(index_prices.columns)
    if missing:
        formatted = ", ".join(sorted(missing))
        raise ValueError(f"index_prices missing required columns: {formatted}")

    history = index_prices.copy(deep=True)
    if "symbol" in history.columns:
        normalized_symbols = history["symbol"].astype("string").str.upper()
        csi_300 = normalized_symbols.eq("000300.SH")
        if csi_300.any():
            history = history.loc[csi_300].copy()
        elif normalized_symbols.dropna().nunique() > 1:
            return MarketRegime.RED

    history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce")
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    history = history.loc[
        history["trade_date"].notna() & history["trade_date"].le(pd.Timestamp(as_of_date))
    ].copy()
    history = history.sort_values("trade_date", kind="mergesort").drop_duplicates(
        "trade_date", keep="last"
    )

    if len(history) < 200:
        return MarketRegime.RED

    closes = history["close"].tail(200)
    if closes.isna().any() or not np.isfinite(closes).all() or closes.le(0).any():
        return MarketRegime.RED
    last_close = float(closes.iloc[-1])
    moving_average_200 = float(closes.mean())
    close_20_sessions_ago = float(closes.iloc[-21])
    if not all(np.isfinite(value) for value in (last_close, moving_average_200)):
        return MarketRegime.RED

    above_200_day_average = last_close > moving_average_200
    positive_20_day_trend = last_close > close_20_sessions_ago
    conditions_met = int(above_200_day_average) + int(positive_20_day_trend)
    if conditions_met == 2:
        return MarketRegime.GREEN
    if conditions_met == 1:
        return MarketRegime.YELLOW
    return MarketRegime.RED
