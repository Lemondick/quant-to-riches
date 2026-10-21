"""Bounded daily OHLC for verified cash indices, without invented volume or dates."""

from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from attribution_shared import _finite_decimal
from worker_cache import cache_get, cache_key, cache_set

from quant_assistant.providers.data_quality import metadata
from quant_assistant.providers.index_registry import definitions, get_index, resolve_legacy_index
from quant_assistant.providers.market_calendar import (
    MarketCalendars,
    latest_session,
    session_boundary,
)
from quant_assistant.providers.public_http import PublicHttp


def _transport_key(http_get):
    module = getattr(http_get, "__module__", None)
    qualname = getattr(http_get, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return cache_key("public_http_transport", "1", module + "." + qualname)
    return None


def _transport(http_get):
    # The old @lru_cache keyed on the http_get *function object*: every fresh
    # closure or mock minted a new entry and evicted a useful one, so the cache
    # never hit in practice. Key on a stable string identity instead; callables
    # without one (mocks, partials) bypass the cache: no leak, no wrong sharing.
    key = _transport_key(http_get)
    if key is None:
        return PublicHttp(http_get)
    transport = cache_get(key)
    if transport is None:
        transport = PublicHttp(http_get)
        cache_set(key, transport)
    return transport


INDICES = {
    row.legacy_history_symbol: (row.history.symbol, row.name)
    for row in definitions()
    if row.enabled
    and row.history is not None
    and row.history.verification_status == "verified"
    and row.legacy_history_symbol is not None
}


def _number(value):
    try:
        if value is None or isinstance(value, bool):
            raise ValueError("invalid history number")
        return _finite_decimal(value)
    except ValueError as failure:
        raise ValueError("invalid history number") from failure


def _tencent(payload, row):
    mapping = row.history
    part = payload["data"][mapping.symbol]
    qt = part["qt"][mapping.symbol]
    if (
        payload.get("code") != 0
        or len(qt) <= 30
        or qt[0] != mapping.response_type
        or qt[1] not in mapping.response_names
        or qt[2] != mapping.response_symbol
        or not mapping.symbol.startswith(mapping.response_market)
    ):
        raise ValueError("history identity mismatch")
    raw = qt[30]
    observed = None
    if raw:
        pattern = (
            "%Y-%m-%d %H:%M:%S"
            if row.market == "US"
            else "%Y/%m/%d %H:%M:%S"
            if row.market == "HK"
            else "%Y%m%d%H%M%S"
        )
        naive = datetime.strptime(raw, pattern)
        observed = naive.replace(tzinfo=ZoneInfo(mapping.source_timezone))
        if (
            naive.strftime(pattern) != raw
            or observed.utcoffset() != naive.replace(tzinfo=observed.tzinfo, fold=1).utcoffset()
        ):
            raise ValueError("invalid history source clock")
    return part["day"], observed, 641, 0


def _eastmoney(payload, row):
    data, mapping = payload["data"], row.history
    if (
        payload.get("rc") != 0
        or data["code"] != mapping.response_symbol
        or data["name"] not in mapping.response_names
        or type(data["market"]) is not int
        or str(data["market"]) != mapping.response_market
        or mapping.symbol != str(data["market"]) + "." + data["code"]
    ):
        raise ValueError("history identity mismatch")
    if not isinstance(data["klines"], list) or any(
        not isinstance(item, str) for item in data["klines"]
    ):
        raise ValueError("invalid daily observations")
    return [item.split(",") for item in data["klines"]], None, 640, 0


def _yahoo(payload, row):
    chart, mapping = payload["chart"], row.history
    if chart.get("error") is not None or len(chart["result"]) != 1:
        raise ValueError("invalid history payload")
    part = chart["result"][0]
    meta = part["meta"]
    if (
        meta.get("symbol") != mapping.response_symbol
        or meta.get("shortName") not in mapping.response_names
        or meta.get("instrumentType") != mapping.response_type
        or meta.get("currency") != mapping.response_currency
        or meta.get("exchangeName") != mapping.response_market
        or meta.get("exchangeTimezoneName") != mapping.source_timezone
    ):
        raise ValueError("history identity mismatch")
    times = part["timestamp"]
    quotes = part["indicators"]["quote"]
    if (
        not isinstance(times, list)
        or not 1 <= len(times) <= 640
        or any(type(t) is not int for t in times)
        or times != sorted(set(times))
        or len(quotes) != 1
    ):
        raise ValueError("invalid daily observations")
    fields = quotes[0]
    if any(
        not isinstance(fields.get(key), list) or len(fields[key]) != len(times)
        for key in ("open", "close", "high", "low")
    ):
        raise ValueError("invalid daily observations")
    zone = ZoneInfo(mapping.source_timezone)
    rows = [
        [
            datetime.fromtimestamp(stamp, zone).date().isoformat(),
            *(fields[key][index] for key in ("open", "close", "high", "low")),
        ]
        for index, stamp in enumerate(times)
    ]
    stamp = meta.get("regularMarketTime")
    if type(stamp) is not int:
        raise ValueError("invalid history source clock")
    observed = datetime.fromtimestamp(stamp, zone)
    omitted = 0
    if (
        rows
        and rows[-1][0] == observed.date().isoformat()
        and any(value is None for value in rows[-1][1:])
    ):
        # A latest incomplete daily row is not an OHLC bar. Validate supplied
        # values before omitting it, and expose the omission and older end date.
        values = [None if value is None else _number(value) for value in rows[-1][1:]]
        opening, close, high, low = values
        if any(value is not None and value <= 0 for value in values) or (
            high is not None and low is not None and high < low
        ):
            raise ValueError("inconsistent incomplete OHLC")
        for value in (opening, close):
            if value is not None and (
                high is not None and value > high or low is not None and value < low
            ):
                raise ValueError("inconsistent incomplete OHLC")
        rows.pop()
        omitted = 1
    return rows, observed, 640, omitted


def _load(http, row):
    mapping = row.history
    if mapping.provider == "tencent":
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {"param": mapping.symbol + ",day,,,640,qfq"}
        parser = _tencent
    elif mapping.provider == "eastmoney":
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": mapping.symbol,
            "klt": 101,
            "fqt": 0,
            "lmt": 640,
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        }
        parser = _eastmoney
    elif mapping.provider == "yahoo":
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(mapping.symbol, safe="")
        params = {"range": "2y", "interval": "1d"}
        parser = _yahoo
    else:
        raise ValueError("unverified history provider")
    response = http.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=(3, 10))
    response.raise_for_status()
    try:
        return (*parser(response.json(), row), response.public_fetched_at)
    except (KeyError, IndexError, TypeError, OverflowError, OSError) as error:
        raise ValueError("invalid history payload") from error


def index_history(symbol=None, *, index_id=None, http_get=requests.get, calendar=None, now=None):
    row = get_index(index_id) if index_id is not None else resolve_legacy_index(symbol)
    if symbol is not None and symbol != row.exchange_code:
        raise ValueError("conflicting index identifiers")
    if not row.enabled or row.history is None or row.history.verification_status != "verified":
        raise ValueError("unsupported index history")
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("history check requires a timezone")
    local = instant.astimezone(ZoneInfo(row.timezone))
    calendars = calendar if calendar is not None else MarketCalendars.builtin()
    http = _transport(http_get) if now is None else PublicHttp(http_get, now=lambda: instant)
    bars, observed, bound, omitted, fetched = _load(http, row)
    if not isinstance(bars, list) or not 1 <= len(bars) <= bound:
        raise ValueError("missing or excessive index history")
    if observed is not None and observed > instant + timedelta(minutes=1):
        raise ValueError("future history source clock")
    items, previous = [], None
    for bar in bars:
        if (
            not isinstance(bar, list)
            or len(bar) < 5
            or not isinstance(bar[0], str)
            or len(bar[0]) != 10
        ):
            raise ValueError("incomplete index OHLC")
        day = date.fromisoformat(bar[0])
        if (
            bar[0] != day.isoformat()
            or day > local.date()
            or previous is not None
            and day <= previous
            or day.weekday() >= 5
            or calendars.trading_day(day, row.market) is False
        ):
            raise ValueError("invalid index observation date")
        opening, close, high, low = map(_number, bar[1:5])
        if (
            min(opening, close, high, low) <= 0
            or high < max(opening, close)
            or low > min(opening, close)
        ):
            raise ValueError("inconsistent index OHLC")
        if observed is not None and day > observed.date():
            raise ValueError("history beyond source clock")
        partial = day == local.date()
        if partial and observed is not None:
            try:
                closing = session_boundary(local.replace(hour=23, minute=59), day, True, row.market)
                partial = local < closing or observed < closing
            except ValueError:
                pass  # Unknown schedule cannot certify that today's bar is complete.
        items.append(
            {
                "date": day.isoformat(),
                "open": str(opening),
                "high": str(high),
                "low": str(low),
                "close": str(close),
                "is_partial": partial,
            }
        )
        previous = day
    items = items[-640:]
    info = metadata(
        "historical_quote",
        row.history.provider,
        items[-1]["date"],
        fetched,
        as_of=instant,
        instrument_type="index",
        market=row.market,
        calendar=calendars,
    )
    latest = latest_session(instant, calendars, row.market)
    if latest is None:
        info.update(quality_status="unknown_time", quality_reason="calendar_or_session_unavailable")
    elif previous < latest:
        info.update(
            quality_status="stale", is_stale=True, quality_reason="history_last_session_old"
        )
    return {
        "id": row.id,
        "market": row.market,
        "currency": row.currency,
        "calendar_id": row.calendar_id,
        "currency_basis": row.history.currency_basis,
        "source_symbol": row.history.symbol,
        "symbol": row.exchange_code,
        "name": row.name,
        "items": items,
        "source": row.history.provider,
        "observed_at": None,
        "stale": info["is_stale"],
        "unavailable": [],
        "source_quote_time": observed.isoformat() if observed else None,
        "last_bar_partial": items[-1]["is_partial"],
        "omitted_incomplete_bars": omitted,
        "volume_status": "omitted_unverified_unit",
        "history_count": len(items),
        "history_start": items[0]["date"],
        "history_end": items[-1]["date"],
        **{
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in info.items()
        },
    }
