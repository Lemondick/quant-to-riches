"""AKShare adapter that normalizes provider-specific response columns."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import TypeVar

import numpy as np
import pandas as pd
import requests
from requests import exceptions as requests_exceptions

from quant_assistant.providers._lazy_akshare import demjson
from quant_assistant.providers.akshare_symbols import (
    coerce_suspended_flag as _coerce_suspended_flag,
)
from quant_assistant.providers.akshare_symbols import (
    first_matching_column as _first_column,
)
from quant_assistant.providers.akshare_symbols import (
    normalize_a_share_symbol as _normalize_a_share_symbol,
)
from quant_assistant.providers.akshare_symbols import (
    tx_symbol as _tx_symbol,
)
from quant_assistant.providers.base import (
    ProviderDataError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Tencent's public endpoints expose positional fields and hard response caps;
# keep those protocol details named and tested instead of scattering literals.
TENCENT_QUOTE_BATCH_SIZE = 500
TENCENT_KLINE_LIMIT = 640
TENCENT_QUOTE_DATE_FIELD = 30
TENCENT_QUOTE_HIGH_FIELD = 33
TENCENT_QUOTE_LOW_FIELD = 34
TENCENT_QUOTE_SUMMARY_FIELD = 35
TENCENT_KLINE_CACHE_BUSTER = "0.8205512681390605"
TENCENT_VOLUME_SCALE = 100
TENCENT_AMOUNT_SCALE = 10_000
PERCENT_VALUE_THRESHOLD = 100
INDUSTRY_MAP_CACHE_SECONDS = 6 * 60 * 60
INDUSTRY_MAP_MAX_WORKERS = 8

_STOCK_PRICE_COLUMNS = {
    "日期": "trade_date",
    "date": "trade_date",
    "开盘": "open",
    "open": "open",
    "最高": "high",
    "high": "high",
    "最低": "low",
    "low": "low",
    "收盘": "close",
    "close": "close",
    "成交量": "volume",
    "volume": "volume",
    "成交额": "amount",
    "amount": "amount",
}
_PRICE_FIELDS = ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"]

_INDEX_VALUATION_LABELS = {
    "000016.SH": "上证50",
    "000300.SH": "沪深300",
    "000688.SH": "科创50",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "399006.SZ": "创业板指",
    "000510.SH": "中证500",
}


def _coerce_prices(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    missing = set(_PRICE_FIELDS).difference(frame.columns)
    if missing:
        formatted = ", ".join(sorted(missing))
        raise ProviderDataError(
            f"{dataset}: missing provider columns after normalization: {formatted}"
        )
    optional_fields = [
        column for column in ("suspended", "upper_limit", "lower_limit") if column in frame
    ]
    result = frame.loc[:, [*_PRICE_FIELDS, *optional_fields]].copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.date
    for column in ("open", "high", "low", "close", "volume", "amount"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in ("upper_limit", "lower_limit"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    if "suspended" in result:
        result["suspended"] = result["suspended"].map(_coerce_suspended_flag)
    return result.reset_index(drop=True)


def _normalize_stock_spot(raw: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    mapping = {
        "代码": "symbol",
        "今开": "open",
        "最高": "high",
        "最低": "low",
        "最新价": "close",
        "成交量": "volume",
        "成交额": "amount",
    }
    missing = set(mapping).difference(raw.columns)
    if missing:
        formatted = ", ".join(sorted(missing))
        raise ProviderDataError(f"daily_prices: missing provider columns: {formatted}")
    normalized = raw.rename(columns=mapping).loc[:, list(mapping.values())].copy()
    optional_mapping = {
        "涨停价": "upper_limit",
        "跌停价": "lower_limit",
        "是否停牌": "suspended",
        "停牌": "suspended",
        "is_suspended": "suspended",
        "upper_limit": "upper_limit",
        "lower_limit": "lower_limit",
    }
    for source, target in optional_mapping.items():
        if source in raw and target not in normalized:
            normalized[target] = raw[source]
    normalized["symbol"] = normalized["symbol"].map(_normalize_a_share_symbol)
    normalized.insert(1, "trade_date", trade_date)
    return normalized


def _fetch_tencent_stock_daily(symbols: Sequence[str], trade_date: date) -> pd.DataFrame:
    """Fetch dated Tencent quotes in batches without relabeling stale observations."""

    rows: list[dict[str, object]] = []
    unique_symbols = list(dict.fromkeys(_tx_symbol(symbol) for symbol in symbols))
    for offset in range(0, len(unique_symbols), TENCENT_QUOTE_BATCH_SIZE):
        batch = unique_symbols[offset : offset + TENCENT_QUOTE_BATCH_SIZE]
        response = requests.get(
            f"https://qt.gtimg.cn/q={','.join(batch)}",
            timeout=30,
        )
        response.raise_for_status()
        for line in response.content.decode("gbk", errors="replace").splitlines():
            match = re.fullmatch(r'v_([a-z]{2}\d+)="(.*)";', line.strip())
            if match is None:
                raise ProviderDataError("daily_prices: Tencent returned a malformed quote row")
            fields = match.group(2).split("~")
            if len(fields) <= TENCENT_QUOTE_SUMMARY_FIELD:
                raise ProviderDataError("daily_prices: Tencent returned an incomplete quote row")
            timestamp = fields[TENCENT_QUOTE_DATE_FIELD]
            try:
                quote_date = date(int(timestamp[0:4]), int(timestamp[4:6]), int(timestamp[6:8]))
            except (TypeError, ValueError) as error:
                raise ProviderDataError(
                    "daily_prices: Tencent returned an invalid quote timestamp"
                ) from error
            if quote_date != trade_date:
                continue
            trade_summary = fields[TENCENT_QUOTE_SUMMARY_FIELD].split("/")
            if len(trade_summary) < 3:
                raise ProviderDataError("daily_prices: Tencent returned an invalid trade summary")
            rows.append(
                {
                    "symbol": _normalize_a_share_symbol(match.group(1)),
                    "trade_date": quote_date,
                    "open": fields[5],
                    "high": fields[TENCENT_QUOTE_HIGH_FIELD],
                    "low": fields[TENCENT_QUOTE_LOW_FIELD],
                    "close": fields[3],
                    "volume": fields[6],
                    "amount": trade_summary[2],
                }
            )
    if not rows:
        raise ProviderDataError(
            f"daily_prices: Tencent returned no quotes dated {trade_date.isoformat()}"
        )
    result = pd.DataFrame(rows, columns=_PRICE_FIELDS)
    result["volume"] = pd.to_numeric(result["volume"], errors="coerce") * TENCENT_VOLUME_SCALE
    return result


def _fetch_tencent_price_history(symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch Tencent daily bars without AKShare's redundant yearly overlap.

    Tencent caps a response at 640 bars.  A full-window request is normally enough
    for the strategy horizon; older ranges are recursively split only when the first
    returned date proves that the cap truncated the requested window.
    """

    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    normalized_symbol = _tx_symbol(symbol)

    def fetch_window(window_start: date, window_end: date) -> pd.DataFrame:
        url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        params = {
            "_var": f"kline_day{window_start.year}",
            "param": (
                f"{normalized_symbol},day,{window_start.isoformat()},{window_end.isoformat()},"
                f"{TENCENT_KLINE_LIMIT},"
            ),
            "r": TENCENT_KLINE_CACHE_BUSTER,
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        marker = response.text.find("={")
        if marker < 0:
            raise requests_exceptions.ConnectionError("Tencent returned malformed JSONP")
        try:
            payload = demjson.decode(response.text[marker + 1 :])
            quote = payload.get("data", {}).get(normalized_symbol, {})
            # An adjusted fallback would silently change the price basis.
            rows = quote.get("day") or []
            if not rows and (quote.get("hfqday") or quote.get("qfqday")):
                raise requests_exceptions.ConnectionError(
                    "Tencent returned only adjusted price history"
                )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise requests_exceptions.ConnectionError(
                "Tencent returned an invalid price payload"
            ) from error
        if not rows:
            return pd.DataFrame(
                columns=["date", "open", "close", "high", "low", "volume", "amount"]
            )
        if any(len(row) < 9 for row in rows):
            raise ProviderDataError("stock_history: Tencent returned incomplete price rows")
        frame = pd.DataFrame(
            [
                {
                    "date": row[0],
                    "open": row[1],
                    "close": row[2],
                    "high": row[3],
                    "low": row[4],
                    "volume": row[5],
                    "amount": row[8],
                }
                for row in rows
            ]
        )
        returned_dates = pd.to_datetime(frame["date"], errors="coerce")
        first_date = returned_dates.min()
        if (
            len(frame) >= TENCENT_KLINE_LIMIT
            and pd.notna(first_date)
            and first_date.date() > window_start + timedelta(days=14)
            and window_start < window_end
        ):
            midpoint = window_start + (window_end - window_start) // 2
            left = fetch_window(window_start, midpoint)
            right = fetch_window(midpoint + timedelta(days=1), window_end)
            return pd.concat([left, right], ignore_index=True)
        return frame

    result = fetch_window(start_date, end_date)
    if result.empty:
        return result
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date
    for column in ("open", "close", "high", "low", "volume", "amount"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if not normalized_symbol.startswith(("sh688", "sz399", "sh000", "sz000")):
        result["volume"] *= TENCENT_VOLUME_SCALE
    result["amount"] *= TENCENT_AMOUNT_SCALE
    return (
        result.loc[
            result["date"].notna() & result["date"].ge(start_date) & result["date"].le(end_date)
        ]
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _normalize_index_valuation(
    pe_frame: pd.DataFrame,
    pb_frame: pd.DataFrame,
    *,
    symbol: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Join PE/PB responses while tolerating provider column-name changes."""

    def prepare(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ProviderDataError(f"valuation_history: empty {metric} response")
        date_column = _first_column(frame, ("日期", "date", "trade_date", "valuation_date"))
        if date_column is None:
            raise ProviderDataError(f"valuation_history: {metric} response has no date column")
        preferred = (
            ("滚动市盈率", "动态市盈率", "等权滚动市盈率", "静态市盈率", "市盈率")
            if metric == "pe"
            else ("市净率", "等权市净率", "滚动市净率", "pb")
        )
        metric_column = _first_column(frame, preferred)
        if metric_column is None:
            metric_column = next(
                (
                    column
                    for column in frame.columns
                    if metric in str(column).lower() or (metric == "pe" and "市盈率" in str(column))
                ),
                None,
            )
        if metric_column is None:
            raise ProviderDataError(f"valuation_history: {metric} response has no metric column")
        values = pd.to_numeric(frame[metric_column], errors="coerce")
        dates = pd.to_datetime(frame[date_column], errors="coerce").dt.date
        prepared = pd.DataFrame({"as_of_date": dates, metric: values})
        return prepared.loc[
            prepared["as_of_date"].notna()
            & prepared["as_of_date"].ge(start)
            & prepared["as_of_date"].le(end)
            & prepared[metric].notna()
            & np.isfinite(prepared[metric])
            & prepared[metric].gt(0)
        ]

    pe = prepare(pe_frame, "pe")
    pb = prepare(pb_frame, "pb")
    result = pe.merge(pb, on="as_of_date", how="outer")
    result.insert(0, "symbol", symbol)
    return result.loc[:, ["symbol", "as_of_date", "pe", "pb"]].sort_values(
        "as_of_date", kind="mergesort"
    )


def _source_for_function(function: Callable[..., object]) -> str:
    """Map an AKShare/raw callable to its upstream family for health reporting."""

    name = str(getattr(function, "__name__", "")).lower()
    if "tencent" in name or name.endswith("_tx") or "_tx" in name or "qq" in name:
        return "tencent"
    if name in {"stock_zh_a_spot", "stock_zh_index_daily", "stock_sector_spot"} or "sina" in name:
        return "sina"
    if name.startswith("stock_info_") or "official" in name:
        return "exchange_official"
    return "eastmoney"


def _extract_win_rate(frame: pd.DataFrame) -> float:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return float("nan")
    column = _first_column(frame, ("盈利概率", "win_rate", "manager_win_rate"))
    if column is None:
        column = next((item for item in frame.columns if "概率" in str(item)), None)
    if column is None:
        return float("nan")
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return float("nan")
    value = float(values.iloc[-1])
    if 1 < value <= PERCENT_VALUE_THRESHOLD:
        value /= 100
    return value if 0 <= value <= 1 else float("nan")


def _extract_alpha_stability(frame: pd.DataFrame) -> float:
    """Use cross-period peer-rank consistency as an explicit alpha proxy."""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return float("nan")
    column = _first_column(frame, ("周期收益同类排名", "peer_rank", "rank"))
    if column is None:
        column = next((item for item in frame.columns if "排名" in str(item)), None)
    if column is None:
        return float("nan")
    percentiles: list[float] = []
    for raw in frame[column].astype(str):
        match = re.search(r"(\d+)\s*/\s*(\d+)", raw)
        if match is None:
            continue
        rank, total = (int(value) for value in match.groups())
        if total > 0 and 0 < rank <= total:
            percentiles.append(1 - (rank - 1) / total)
    if len(percentiles) < 2:
        return float("nan")
    return float(np.clip(1 - np.std(percentiles, ddof=0), 0, 1))
