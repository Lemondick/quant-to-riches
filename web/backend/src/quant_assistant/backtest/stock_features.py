"""Engine-owned causal preparation, separate from the strategy decision view."""

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from quant_assistant.features import technical


@dataclass(frozen=True, slots=True)
class StockFeatureView:
    as_of_date: date
    trading_dates: tuple[date, ...]
    revision: object = field(repr=False, compare=False)
    _rows: dict[date, pd.DataFrame] = field(repr=False, compare=False)

    def on(self, day: date) -> pd.DataFrame:
        if day > self.as_of_date or day not in self._rows:
            raise ValueError("feature date is outside the prepared decision view")
        return self._rows[day].copy(deep=True)


class PreparedStockFeatures:
    """Full preparation stays in the engine; views contain no reference back here."""

    def __init__(self, prices: pd.DataFrame):
        history = technical.calculate_stock_feature_history(prices)
        self._dates = tuple(sorted(pd.to_datetime(prices.trade_date).dt.date.unique()))
        self._history = _build_point_in_time_features(history, self._dates)
        self._revision = object()

    def view(self, as_of_date: date) -> StockFeatureView:
        dates = tuple(day for day in self._dates if day <= as_of_date)
        previous_week = [
            day for day in dates if day.isocalendar()[:2] < as_of_date.isocalendar()[:2]
        ]
        selected = {as_of_date, *previous_week[-1:]}
        rows = {}
        for day in selected:
            timestamp = pd.Timestamp(day)
            if timestamp not in self._history.index:
                rows[day] = pd.DataFrame()
                continue
            frame = self._history.loc[[timestamp]].reset_index(drop=True)
            frame = frame.dropna(subset=["ret_120", "ma_200", "volatility_20", "amount_20"])
            frame = frame.copy(deep=True)
            frame["trade_date"] = frame["trade_date"].dt.date
            rows[day] = frame
        return StockFeatureView(as_of_date, dates, self._revision, rows)


def _build_point_in_time_features(
    history: pd.DataFrame, trading_dates: tuple[date, ...]
) -> pd.DataFrame:
    """Forward-fill each symbol's causal feature row onto the market calendar."""

    columns = [
        "symbol",
        "trade_date",
        "close",
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
    compact = history.loc[:, columns].copy()
    compact["symbol"] = compact["symbol"].astype(str)
    compact["trade_date"] = pd.to_datetime(compact["trade_date"], errors="coerce").astype(
        "datetime64[ns]"
    )
    compact = compact.dropna(subset=["symbol", "trade_date"])
    symbols = tuple(compact["symbol"].astype(str).drop_duplicates())
    calendar = pd.MultiIndex.from_product(
        [pd.to_datetime(trading_dates).astype("datetime64[ns]"), symbols],
        names=["trade_date", "symbol"],
    ).to_frame(index=False)
    calendar["symbol"] = calendar["symbol"].astype(str)
    calendar = calendar.sort_values(["trade_date", "symbol"], kind="mergesort")
    compact = compact.sort_values(["trade_date", "symbol"], kind="mergesort")
    merged = pd.merge_asof(
        calendar,
        compact,
        on="trade_date",
        by="symbol",
        direction="backward",
        allow_exact_matches=True,
        suffixes=("", "_history"),
    )
    return merged.set_index("trade_date", drop=False).sort_index()
