"""Verified index adapters with bounded batch requests and independent last-valid rows."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.data_quality import MemoryCache, metadata
from quant_assistant.providers.index_registry import definitions, get_index, registry_version
from quant_assistant.providers.market_calendar import MarketCalendars, in_refresh_window
from quant_assistant.providers.public_http import PublicHttp

if TYPE_CHECKING:
    from quant_assistant.providers.market_observations import MarketQuote


@dataclass(frozen=True, slots=True)
class IndexQuoteStatus:
    id: str
    quality_status: str
    quality_reason: str | None
    source: str
    source_time: str | None
    fetched_at: datetime | None
    cache_read_at: datetime
    is_stale: bool = False
    fallback_used: bool = False
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class IndexQuoteBatch:
    items: tuple[MarketQuote, ...]
    statuses: tuple[IndexQuoteStatus, ...]


class IndexQuoteError(ValueError):
    def __init__(self, reason, quality="invalid"):
        super().__init__(reason)
        self.reason, self.quality = reason, quality


class _FailedBatch(Exception):
    def __init__(self, rows):
        self.rows = rows


def _number(value):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (ValueError, InvalidOperation):
        raise IndexQuoteError("invalid_numeric_value") from None


def _quote(
    definition,
    price,
    previous,
    change,
    rate,
    observed,
    fetched,
    calendar,
    raw_time,
    *,
    change_basis="provider_fields",
):
    from quant_assistant.providers.market_observations import MarketQuote

    if (
        price <= 0
        or previous <= 0
        or abs(change - (price - previous)) > Decimal("0.03")
        or abs(rate - (price / previous - 1) * 100) > Decimal("0.08")
    ):
        raise IndexQuoteError("inconsistent_quote_values")
    info = metadata(
        "realtime_quote",
        definition.quote.provider,
        observed.isoformat() if observed else None,
        fetched,
        instrument_type="index",
        calendar=calendar,
        market=definition.market,
    )
    if info["quality_status"] == "invalid":
        raise IndexQuoteError("invalid_source_time")
    if info["quality_status"] == "unavailable":
        raise IndexQuoteError(info["quality_reason"] or "quote_unavailable", "unavailable")
    return MarketQuote(
        id=definition.id,
        symbol=definition.exchange_code,
        name=definition.name,
        asset_type="index",
        price=price,
        change=change,
        change_rate=rate,
        observed_at=observed,
        source=definition.quote.provider,
        trade_time_verified=observed is not None,
        currency=definition.currency,
        currency_basis=definition.quote.currency_basis,
        calendar_id=definition.calendar_id,
        source_symbol=definition.quote.symbol,
        raw_source_time=raw_time,
        change_basis=change_basis,
        cache_read_at=fetched,
        **info,
    )


def _tencent(content, selected, fetched, calendar):
    lookup = {row.quote.symbol: row for row in selected}
    matches = {}
    for ticker, value in re.findall(
        r'v_([A-Za-z0-9_.]+)="([^"\r\n]*)";', content.decode("gb18030")
    ):
        if ticker in lookup:
            matches.setdefault(ticker, []).append(value.split("~"))
    result = {}
    for ticker, row in lookup.items():
        values = matches.get(ticker, [])
        try:
            if len(values) > 1:
                raise IndexQuoteError("duplicate_provider_identity")
            if not values:
                raise IndexQuoteError("quote_not_returned", "unavailable")
            fields = values[0]
            if (
                len(fields) <= 32
                or fields[0] != row.quote.response_type
                or fields[1] not in row.quote.response_names
                or fields[2] != row.quote.response_symbol
                or not ticker.startswith(row.quote.response_market)
            ):
                raise IndexQuoteError("identity_mismatch")
            raw = fields[30]
            observed = None
            if raw:
                pattern = (
                    "%Y-%m-%d %H:%M:%S"
                    if row.market == "US"
                    else "%Y/%m/%d %H:%M:%S"
                    if row.market == "HK"
                    else "%Y%m%d%H%M%S"
                )
                try:
                    naive = datetime.strptime(raw, pattern)
                    if naive.strftime(pattern) != raw:
                        raise ValueError
                    zone = ZoneInfo(row.quote.source_timezone)
                    observed = naive.replace(tzinfo=zone)
                    if observed.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset():
                        raise ValueError
                except ValueError:
                    raise IndexQuoteError("invalid_source_time") from None
            result[row.id] = _quote(
                row,
                *(_number(fields[index]) for index in (3, 4, 31, 32)),
                observed,
                fetched,
                calendar,
                raw or None,
            )
        except IndexQuoteError as error:
            result[row.id] = error
    return result


def _yahoo(payload, row, fetched, calendar):
    try:
        chart = payload["chart"]
        if (
            chart.get("error") is not None
            or not isinstance(chart["result"], list)
            or len(chart["result"]) != 1
        ):
            raise IndexQuoteError("invalid_provider_payload")
        data = chart["result"][0]
        meta = data["meta"]
        mapping = row.quote
        if (
            meta.get("symbol") != mapping.response_symbol
            or meta.get("shortName") not in mapping.response_names
            or meta.get("instrumentType") != mapping.response_type
            or meta.get("currency") != mapping.response_currency
            or meta.get("exchangeName") != mapping.response_market
            or meta.get("exchangeTimezoneName") != mapping.source_timezone
        ):
            raise IndexQuoteError("identity_mismatch")
        timestamp = meta["regularMarketTime"]
        dates, closes = data["timestamp"], data["indicators"]["quote"][0]["close"]
        if (
            type(timestamp) is not int
            or not isinstance(dates, list)
            or not 2 <= len(dates) <= 64
            or len(dates) != len(closes)
            or any(type(value) is not int for value in dates)
            or dates != sorted(set(dates))
        ):
            raise IndexQuoteError("invalid_daily_observations")
        zone = ZoneInfo(mapping.source_timezone)
        observed = datetime.fromtimestamp(timestamp, zone)
        last_day = datetime.fromtimestamp(dates[-1], zone).date()
        prior_day = datetime.fromtimestamp(dates[-2], zone).date()
        if last_day != observed.date() or prior_day >= last_day:
            raise IndexQuoteError("invalid_daily_observations")
        price, previous = (
            _number(meta["regularMarketPrice"]),
            _number(closes[-2]),
        )
        # Yahoo can publish an actual metadata quote before its daily OHLC row.
        # A missing daily close cannot contradict that quote; a supplied one must match.
        if (
            previous <= 0
            or closes[-1] is not None
            and abs(price - _number(closes[-1])) > Decimal("0.03")
        ):
            raise IndexQuoteError("inconsistent_quote_values")
        # chartPreviousClose is the close BEFORE the full requested chart window.
        # It is never used as yesterday's close for a multi-session chart.
        return _quote(
            row,
            price,
            previous,
            price - previous,
            (price / previous - 1) * 100,
            observed,
            fetched,
            calendar,
            str(timestamp),
            change_basis="previous_daily_close",
        )
    except IndexQuoteError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, OSError):
        raise IndexQuoteError("invalid_provider_payload") from None


class IndexQuoteProvider:
    def __init__(
        self,
        *,
        http=None,
        now=None,
        calendar=None,
        ttl_seconds=5,
        closed_ttl_seconds=300,
        monotonic_clock=time.monotonic,
    ):
        if not 0 < ttl_seconds <= 300:
            raise ValueError("invalid index cache interval")
        if not ttl_seconds <= closed_ttl_seconds <= 3600:
            raise ValueError("invalid closed index cache interval")
        self.now = now or (lambda: datetime.now(SHANGHAI_TZ))
        self.http, self.calendar, self.ttl = http or PublicHttp(now=self.now), calendar, ttl_seconds
        self.closed_ttl = closed_ttl_seconds
        self._batches = MemoryCache(capacity=8, clock=monotonic_clock)
        self._rows = MemoryCache(capacity=32, clock=monotonic_clock)

    def expire(self):
        self._batches.expire()
        self._rows.expire()

    def _load(self, selected):
        provider = selected[0].quote.provider
        if provider == "tencent":
            response = self.http.get(
                "https://qt.gtimg.cn/q=" + ",".join(row.quote.symbol for row in selected),
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=(3, 8),
                expected="text",
                module="index_quotes",
            )
            rows = _tencent(response.content, selected, response.public_fetched_at, self.calendar)
        elif provider == "yahoo":
            rows = {}
            for row in selected:
                response = self.http.get(
                    "https://query1.finance.yahoo.com/v8/finance/chart/" + row.quote.symbol,
                    params={"range": "1mo", "interval": "1d"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=(3, 8),
                    module="index_quotes",
                )
                try:
                    rows[row.id] = _yahoo(
                        response.json(), row, response.public_fetched_at, self.calendar
                    )
                except IndexQuoteError as error:
                    rows[row.id] = error
        else:
            raise ValueError("unverified index provider")
        if not any(not isinstance(value, IndexQuoteError) for value in rows.values()):
            raise _FailedBatch(rows)
        return rows

    def _batch(self, selected):
        active = in_refresh_window(
            self.now(),
            self.calendar if self.calendar is not None else MarketCalendars.builtin(),
            selected[0].market,
        )
        try:
            rows, _ = self._batches.read(
                (
                    registry_version(),
                    tuple(row.id for row in selected),
                    "active" if active else "rest",
                ),
                lambda: self._load(selected),
                ttl=self.ttl if active else self.closed_ttl,
                stale_on_error=False,
            )
            return rows
        except _FailedBatch as error:
            return error.rows
        except (ValueError, TypeError, KeyError, IndexError):
            return {row.id: IndexQuoteError("invalid_provider_payload") for row in selected}
        except Exception:
            return {
                row.id: IndexQuoteError("source_unavailable", "unavailable") for row in selected
            }

    def fetch(self, index_ids=None):
        selected = (
            definitions()
            if index_ids is None
            else tuple(get_index(value) for value in dict.fromkeys(index_ids))
        )
        groups, found, errors = {}, {}, {}
        for row in selected:
            if row.enabled and row.quote.verification_status == "verified":
                groups.setdefault((row.quote.provider, row.market), []).append(row)
            else:
                errors[row.id] = IndexQuoteError("mapping_not_verified", "unavailable")
        if groups:
            with ThreadPoolExecutor(max_workers=min(2, len(groups))) as pool:
                for result in pool.map(self._batch, groups.values()):
                    found.update(result)
        now, items, statuses = self.now(), [], []
        for row in selected:
            result = errors.get(row.id) or found.get(
                row.id, IndexQuoteError("quote_not_returned", "unavailable")
            )

            def load(result=result):
                if isinstance(result, IndexQuoteError):
                    raise result
                return result

            try:
                quote, fallback = self._rows.read((registry_version(), row.id), load, ttl=self.ttl)
                info = metadata(
                    "realtime_quote",
                    quote.source,
                    quote.source_time,
                    quote.fetched_at,
                    calendar=self.calendar,
                    instrument_type="index",
                    market=row.market,
                    as_of=now,
                )
                if fallback:
                    info.update(
                        is_stale=True, fallback_used=True, fallback_reason="index_refresh_failed"
                    )
                    if info["quality_status"] == "valid":
                        info.update(quality_status="stale", quality_reason="index_refresh_failed")
                quote = replace(quote, **info, cache_fallback=fallback, cache_read_at=now)
                items.append(quote)
                statuses.append(
                    IndexQuoteStatus(
                        row.id,
                        quote.quality_status,
                        quote.quality_reason,
                        quote.source,
                        quote.source_time,
                        quote.fetched_at,
                        now,
                        quote.is_stale,
                        quote.fallback_used,
                        quote.fallback_reason,
                    )
                )
            except IndexQuoteError as error:
                statuses.append(
                    IndexQuoteStatus(
                        row.id, error.quality, error.reason, row.quote.provider, None, None, now
                    )
                )
        return IndexQuoteBatch(tuple(items), tuple(statuses))
