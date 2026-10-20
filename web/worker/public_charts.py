"""Bounded true daily OHLC history for registry-verified index capabilities."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests

from quant_assistant.providers.data_quality import metadata
from quant_assistant.providers.index_registry import definitions, resolve_legacy_index
from quant_assistant.providers.public_http import PublicHttp

_HTTP = PublicHttp()

INDICES = {
    row.legacy_history_symbol: (row.history.symbol, row.name)
    for row in definitions()
    if row.enabled
    and row.history is not None
    and row.history.verification_status == "verified"
    and row.legacy_history_symbol is not None
}


def index_history(symbol, *, http_get=requests.get, calendar=None):
    if symbol not in INDICES:
        raise ValueError("unsupported index symbol")
    definition = resolve_legacy_index(symbol)
    ticker, name = INDICES[symbol]
    response = (_HTTP if http_get is requests.get else PublicHttp(http_get)).get(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        params={"param": ticker + ",day,,,640,qfq"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(3, 10),
    )
    response.raise_for_status()
    payload = response.json()
    quote = payload.get("data", {}).get(ticker, {})
    rows = quote.get("day")  # Index OHLC is never silently substituted with adjusted equity prices.
    if payload.get("code") != 0 or not isinstance(rows, list) or not 1 <= len(rows) <= 640:
        raise ValueError("missing index history")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    seen, items = set(), []
    for row in rows:
        day = date.fromisoformat(row[0])
        if day > now.date() or day in seen or day.isoformat() != row[0]:
            raise ValueError("invalid index observation date")
        seen.add(day)
        values = [Decimal(str(value)) for value in row[1:6]]
        if len(values) != 5 or not all(value.is_finite() for value in values):
            raise ValueError("invalid index observation")
        opening, close, high, low, volume = values
        if (
            min(opening, close, high, low) <= 0
            or high < max(opening, close)
            or low > min(opening, close)
            or volume < 0
        ):
            raise ValueError("inconsistent index OHLC")
        items.append(
            {
                "date": day.isoformat(),
                "open": str(opening),
                "high": str(high),
                "low": str(low),
                "close": str(close),
                "volume": str(volume),
            }
        )
    return {
        "id": definition.id,
        "market": definition.market,
        "currency": definition.currency,
        "calendar_id": definition.calendar_id,
        "source_symbol": ticker,
        "symbol": symbol,
        "name": name,
        "items": sorted(items, key=lambda item: item["date"]),
        "source": "tencent",
        "observed_at": now.isoformat(),
        "stale": False,
        "unavailable": [],
        **{
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in metadata(
                "historical_quote",
                "tencent",
                max(items, key=lambda item: item["date"])["date"],
                now,
                instrument_type="index",
                market=definition.market,
                calendar=calendar,
            ).items()
        },
    }
