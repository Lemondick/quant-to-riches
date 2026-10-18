"""Read-only live market and news feeds for the mobile client.

External observations stay outside the portfolio ledger.  They are cached in
memory for a short period and may be returned as explicitly stale when an
upstream source becomes temporarily unavailable.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from threading import RLock
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

import pandas as pd
import requests

from quant_assistant.common.performance import measure
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers._lazy_akshare import ak
from quant_assistant.providers.base import ProviderDataError, ProviderUnavailable
from quant_assistant.providers.data_quality import MemoryCache, ObservationMetadata, metadata
from quant_assistant.providers.market_breadth import MarketBreadth, read_market_breadth
from quant_assistant.providers.market_calendar import MarketCalendars
from quant_assistant.providers.market_feed_parsing import (
    _MAIN_INDEX_IDS as _MAIN_INDEX_IDS,
)
from quant_assistant.providers.market_feed_parsing import (
    _datetime as _datetime,
)
from quant_assistant.providers.market_feed_parsing import (
    _decimal as _decimal,
)
from quant_assistant.providers.market_feed_parsing import (
    _important_news as _important_news,
)
from quant_assistant.providers.market_feed_parsing import (
    _instrument_type as _instrument_type,
)
from quant_assistant.providers.market_feed_parsing import (
    _news_category as _news_category,
)
from quant_assistant.providers.market_feed_parsing import (
    _news_plain_text as _news_plain_text,
)
from quant_assistant.providers.market_feed_parsing import (
    _normalize_code as _normalize_code,
)
from quant_assistant.providers.market_feed_parsing import (
    _optional_date_text as _optional_date_text,
)
from quant_assistant.providers.market_feed_parsing import (
    _optional_decimal as _optional_decimal,
)
from quant_assistant.providers.market_feed_parsing import (
    _optional_text as _optional_text,
)
from quant_assistant.providers.market_feed_parsing import (
    _quote_datetime as _quote_datetime,
)
from quant_assistant.providers.market_feed_parsing import (
    _quote_time_verified as _quote_time_verified,
)
from quant_assistant.providers.market_feed_parsing import (
    _require_columns as _require_columns,
)
from quant_assistant.providers.market_feed_parsing import (
    _source_for_cache_key as _source_for_cache_key,
)
from quant_assistant.providers.market_feed_parsing import (
    _source_from_label as _source_from_label,
)
from quant_assistant.providers.market_feed_parsing import (
    _tencent_id as _tencent_id,
)
from quant_assistant.providers.market_feed_parsing import (
    _text as _text,
)
from quant_assistant.providers.market_feed_parsing import (
    quote_conflict as quote_conflict,
)
from quant_assistant.providers.market_sectors import (
    SectorCatalog,
    SectorRanking,
    read_sector_catalogs,
)
from quant_assistant.providers.public_http import PublicHttp, log_validation
from quant_assistant.services.news_sentiment import classify_news_sentiment

_QUOTE_ROW_RE = re.compile(r'v_([a-z]{2}\d+)="(.*)";')


@dataclass(frozen=True, slots=True)
class MarketQuote(ObservationMetadata):
    symbol: str
    name: str
    asset_type: str
    price: Decimal
    change: Decimal
    change_rate: Decimal
    observed_at: datetime
    source: str
    trade_time_verified: bool = False
    volume: Decimal | None = None
    turnover: Decimal | None = None
    volume_unit: str | None = None
    turnover_unit: str | None = None


@dataclass(frozen=True, slots=True)
class FundRanking(ObservationMetadata):
    symbol: str
    name: str
    unit_nav: Decimal
    change_rate: Decimal
    one_month_return: Decimal | None
    nav_date: str | None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    indices: tuple[MarketQuote, ...]
    fund_rankings: tuple[FundRanking, ...]
    sector_rankings: tuple[SectorRanking, ...]
    observed_at: datetime
    source: str
    stale: bool
    unavailable: tuple[str, ...]
    breadth: MarketBreadth | None = None
    sector_catalogs: tuple[SectorCatalog, ...] = ()
    stale_sections: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketQuoteList:
    items: tuple[MarketQuote, ...]
    unavailable: tuple[str, ...]
    observed_at: datetime
    source: str
    stale: bool


@dataclass(frozen=True, slots=True)
class MarketNewsItem:
    id: str
    title: str
    summary: str
    category: str
    published_at: datetime
    source: str
    url: str
    important: bool
    sentiment: str = "neutral"
    sentiment_score: float = 0.0
    sentiment_keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketNewsFeed:
    items: tuple[MarketNewsItem, ...]
    observed_at: datetime
    source: str
    stale: bool
    unavailable: bool


@runtime_checkable
class MarketFeedProvider(Protocol):
    def market_snapshot(self, section: str | None = None) -> MarketSnapshot: ...

    def market_quotes(
        self,
        stock_symbols: Sequence[str],
        index_symbols: Sequence[str],
    ) -> MarketQuoteList: ...

    def market_news(self, limit: int) -> MarketNewsFeed: ...


@dataclass(frozen=True, slots=True)
class _Section:
    value: object
    source: str
    sector_catalogs: tuple[SectorCatalog, ...] = ()


class AkshareMarketFeedProvider:
    """Normalize public CLS news and Tencent, Sina, Eastmoney market data."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 5.0,
        now: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        akshare_module: object = ak,
        http_get: Callable[..., object] = requests.get,
        calendar: MarketCalendars | None = None,
    ) -> None:
        if ttl_seconds <= 0 or ttl_seconds > 3600:
            raise ValueError("market cache ttl must be between 0 and 3600 seconds")
        self._ttl_seconds = float(ttl_seconds)
        self._calendar = calendar if calendar is not None else MarketCalendars.builtin()
        self._now = now or (lambda: datetime.now(SHANGHAI_TZ))
        self._monotonic = monotonic_clock
        self._ak = akshare_module
        self._http_get = http_get
        self._quote_http = PublicHttp(http_get, now=self._now)
        self._memory = MemoryCache(capacity=128, clock=monotonic_clock)
        self._taxonomy_cache = MemoryCache(capacity=1, clock=monotonic_clock)
        self._cache: dict[str, tuple[float, _Section]] = {}
        self._lock = RLock()
        self._source_state: dict[str, dict[str, object]] = {
            source: {"failures": 0, "last_error": None, "last_success_at": None}
            for source in ("cls", "eastmoney", "sina", "tencent")
        }

    def source_status(self) -> pd.DataFrame:
        """Return redacted upstream health for the system-status page."""

        return pd.DataFrame(
            [
                {
                    "source": source,
                    "available": int(state["failures"]) == 0
                    and state["last_success_at"] is not None,
                    "status": (
                        "unavailable"
                        if int(state["failures"]) > 0
                        else "available"
                        if state["last_success_at"] is not None
                        else "unchecked"
                    ),
                    "consecutive_failures": int(state["failures"]),
                    "last_error": state["last_error"],
                    "last_success_at": state["last_success_at"],
                }
                for source, state in self._source_state.items()
            ]
        )

    def market_snapshot(self, section: str | None = None) -> MarketSnapshot:
        loaders = {
            "indices": self._load_main_indices,
            "fund_rankings": self._load_fund_rankings,
            "sector_rankings": self._load_sector_rankings,
            "breadth": self._load_breadth,
        }
        if section is not None:
            key = {
                "indices": "indices",
                "funds": "fund_rankings",
                "sectors": "sector_rankings",
            }.get(section)
            if key is None:
                raise ValueError("invalid market snapshot section")
            loaders = {key: loaders[key]}
        sections: dict[str, _Section] = {}
        stale_sections: list[str] = []
        unavailable: list[str] = []
        breadth_stale = False
        with ThreadPoolExecutor(max_workers=len(loaders)) as executor:
            futures = {
                executor.submit(copy_context().run, self._cached, key, loader): key
                for key, loader in loaders.items()
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    section, section_stale = future.result()
                except ProviderUnavailable:
                    if key != "breadth":
                        unavailable.append(key)
                    continue
                sections[key] = section
                if key == "breadth":
                    breadth_stale = section_stale
                else:
                    if section_stale:
                        stale_sections.append(key)

        sources = sorted({section.source for section in sections.values()})
        return MarketSnapshot(
            indices=tuple(sections.get("indices", _Section((), "")).value),
            fund_rankings=tuple(sections.get("fund_rankings", _Section((), "")).value),
            sector_rankings=tuple(sections.get("sector_rankings", _Section((), "")).value),
            observed_at=self._observed_at(),
            source="+".join(sources) or "unavailable",
            stale=bool(stale_sections)
            and len(stale_sections) == len([key for key in sections if key != "breadth"]),
            stale_sections=tuple(sorted(stale_sections)),
            sector_catalogs=sections.get("sector_rankings", _Section((), "")).sector_catalogs,
            unavailable=tuple(sorted(unavailable)),
            breadth=replace(sections["breadth"].value, stale=breadth_stale)
            if "breadth" in sections
            else None,
        )

    def _load_breadth(self) -> _Section:
        def get(url, **kwargs):
            return self._quote_http.get(
                url,
                expected="html" if "legulegu.com" in url else "data",
                module="market_breadth",
                **kwargs,
            )

        value = read_market_breadth(http_get=get, now=self._observed_at())
        return _Section(value, value.source)

    def market_quotes(
        self,
        stock_symbols: Sequence[str] = (),
        index_symbols: Sequence[str] = (),
    ) -> MarketQuoteList:
        stocks = tuple(dict.fromkeys(_normalize_code(item) for item in stock_symbols))
        indices = tuple(dict.fromkeys(_normalize_code(item) for item in index_symbols))
        if len(stocks) + len(indices) > 20:
            raise ValueError("at most 20 market quotes can be requested")
        if not stocks and not indices:
            return MarketQuoteList((), (), self._observed_at(), "tencent", False)
        requested = tuple((item, "stock") for item in stocks) + tuple(
            (item, "index") for item in indices
        )
        cache_key = "quotes:" + ",".join(f"{kind}:{symbol}" for symbol, kind in requested)
        try:
            section, stale = self._cached(
                cache_key,
                lambda: self._quote_section(requested),
            )
        except ProviderUnavailable:
            return MarketQuoteList(
                (),
                tuple(symbol for symbol, _kind in requested),
                self._observed_at(),
                "unavailable",
                False,
            )
        items = tuple(section.value)
        returned = {item.symbol for item in items}
        return MarketQuoteList(
            items,
            tuple(symbol for symbol, _kind in requested if symbol not in returned),
            self._observed_at(),
            section.source,
            stale or any(not item.trade_time_verified for item in items),
        )

    def market_news(self, limit: int = 30) -> MarketNewsFeed:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("news limit must be between 1 and 50")
        try:
            section, stale = self._cached("market_news", self._load_news)
        except ProviderUnavailable:
            return MarketNewsFeed((), self._observed_at(), "unavailable", False, True)
        return MarketNewsFeed(
            tuple(section.value)[:limit],
            self._observed_at(),
            section.source,
            stale,
            False,
        )

    def _cached(
        self,
        key: str,
        loader: Callable[[], _Section],
    ) -> tuple[_Section, bool]:
        def load():
            try:
                with measure("provider"):
                    loaded = loader()
            except Exception as exc:
                self._mark_source_failure(_source_for_cache_key(key), type(exc).__name__)
                raise ProviderUnavailable(f"market feed section unavailable: {key}") from exc
            if key != "sector_rankings":
                for label in loaded.source.split("+"):
                    self._mark_source_success(_source_from_label(label))
            return loaded

        ttl = {"sector_rankings": 15, "fund_rankings": 3600, "breadth": 15, "market_news": 60}.get(
            key, self._ttl_seconds
        )
        value, stale = self._memory.read(key, load, ttl=ttl)
        if key == "indices" or key.startswith("quotes:"):
            stale = stale or any(
                row.is_stale or row.quality_status in {"invalid", "unknown_time"}
                for row in value.value
            )
        return value, stale

    def _mark_source_success(self, source: str) -> None:
        state = self._source_state.get(source)
        if state is None:
            return
        state["failures"] = 0
        state["last_error"] = None
        state["last_success_at"] = self._observed_at()

    def _mark_source_failure(self, source: str, error: str) -> None:
        state = self._source_state.get(source)
        if state is None:
            return
        state["failures"] = int(state["failures"]) + 1
        state["last_error"] = error

    def _load_main_indices(self) -> _Section:
        requests_with_types = tuple((symbol, "index") for symbol in _MAIN_INDEX_IDS)
        return self._quote_section(requests_with_types)

    def _quote_section(self, requested):
        rows = self._fetch_tencent_quotes(requested)
        return _Section(rows, "+".join(sorted({row.source for row in rows})))

    def _load_fund_rankings(self) -> _Section:
        if self._ak is ak:
            response = self._quote_http.get(
                "https://fund.eastmoney.com/data/rankhandler.aspx",
                params={
                    "op": "ph",
                    "dt": "kf",
                    "ft": "zs",
                    "rs": "",
                    "gs": "0",
                    "sc": "rzdf",
                    "st": "desc",
                    "sd": (self._observed_at().date() - timedelta(days=365)).isoformat(),
                    "ed": self._observed_at().date().isoformat(),
                    "qdii": "",
                    "tabSubtype": ",,,,,",
                    "pi": "1",
                    "pn": "10",
                    "dx": "1",
                },
                headers={
                    "Referer": "https://fund.eastmoney.com/fundguzhi.html",
                    "User-Agent": "Mozilla/5.0",
                },
                expected="text",
                module="fund_rankings",
                timeout=8,
            )
            text = response.content.decode("utf-8-sig")
            match = re.match(r"\s*var\s+rankData\s*=\s*\{\s*datas\s*:\s*", text)
            if match is None:
                raise ProviderDataError("invalid ranking wrapper")
            data, _ = json.JSONDecoder().raw_decode(text[match.end() :])
            if not isinstance(data, list) or not 1 <= len(data) <= 10:
                raise ProviderDataError("invalid ranking count")
            records = []
            for item in data:
                fields = item.split(",")
                if len(fields) < 9 or not re.fullmatch(r"\d{6}", fields[0]):
                    raise ProviderDataError("invalid ranking identity")
                records.append(
                    dict(
                        zip(
                            ("基金代码", "基金简称", "日期", "单位净值", "日增长率", "近1月"),
                            (fields[0], fields[1], fields[3], fields[4], fields[6], fields[8]),
                            strict=True,
                        )
                    )
                )
            if len({row["基金代码"] for row in records}) != len(records):
                raise ProviderDataError("duplicate ranking code")
            frame = pd.DataFrame(records)
        else:
            frame = self._ak.fund_open_fund_rank_em(symbol="指数型")
        required = {"基金代码", "基金简称", "日期", "单位净值", "日增长率", "近1月"}
        _require_columns(frame, required, "fund_rankings")
        selected = frame[list(required)].copy()
        selected["日增长率"] = pd.to_numeric(selected["日增长率"], errors="coerce")
        selected["单位净值"] = pd.to_numeric(selected["单位净值"], errors="coerce")
        selected["近1月"] = pd.to_numeric(selected["近1月"], errors="coerce")
        selected = selected.dropna(subset=["基金代码", "基金简称", "单位净值", "日增长率"])
        selected = selected.sort_values("日增长率", ascending=False).head(10)
        items = tuple(
            FundRanking(
                symbol=_normalize_code(row["基金代码"]),
                name=_text(row["基金简称"], "fund name"),
                unit_nav=_decimal(row["单位净值"], "fund unit nav"),
                change_rate=_decimal(row["日增长率"], "fund change rate"),
                one_month_return=_optional_decimal(row["近1月"]),
                nav_date=_optional_date_text(row["日期"]),
                **metadata(
                    "official_nav",
                    "eastmoney",
                    _optional_date_text(row["日期"]),
                    self._observed_at(),
                    calendar=self._calendar,
                ),
            )
            for row in selected.to_dict("records")
        )
        if not items:
            raise ProviderDataError("fund_rankings: provider returned no valid rows")
        if any(
            item.unit_nav <= 0 or item.quality_status in {"invalid", "unknown_time"}
            for item in items
        ):
            raise ProviderDataError("invalid ranked NAV")
        log_validation(
            "fund_rankings",
            "eastmoney",
            count=len(items),
            source_time=min(item.nav_date for item in items if item.nav_date),
        )
        return _Section(items, "eastmoney")

    def _load_sector_rankings(self) -> _Section:
        rows, catalogs = read_sector_catalogs(
            http_get=self._http_get,
            now=self._observed_at(),
            taxonomy_cache=self._taxonomy_cache,
            transport=self._quote_http,
        )
        for catalog in catalogs:
            if catalog.received > catalog.unquoted and not catalog.stale:
                self._mark_source_success(catalog.source)
            elif catalog.error:
                self._mark_source_failure(catalog.source, catalog.error)
        # Each source/category carries independent completeness and failure state.
        # An unavailable catalog must not hold up a newer index observation.
        return _Section(rows, "+".join(sorted({r.source for r in rows})) or "unavailable", catalogs)

    def expire_cache(self) -> None:
        """Force a fresh attempt while retaining dated observations for outages."""
        with self._lock:
            self._cache = {key: (float("-inf"), value) for key, (_, value) in self._cache.items()}
        self._memory.expire()

    def _load_news(self) -> _Section:
        try:
            return self._load_cls_news()
        except Exception as error:
            self._mark_source_failure("cls", type(error).__name__)
        return self._load_eastmoney_news()

    def _load_cls_news(self) -> _Section:
        # Same public web endpoint documented by AKShare's CLS interface.
        # Fetch directly to retain article IDs and bound each request to 10s.
        now = self._observed_at()
        params = {
            "app": "CailianpressWeb",
            "category": "",
            "last_time": int(now.timestamp()),
            "os": "web",
            "refresh_type": "1",
            "rn": "20",
            "sv": "8.4.6",
        }
        query = urlencode(sorted(params.items()))
        params["sign"] = hashlib.md5(
            hashlib.sha1(query.encode()).hexdigest().encode(), usedforsecurity=False
        ).hexdigest()
        response = self._http_get(
            "https://www.cls.cn/v1/roll/get_roll_list",
            params=params,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cls.cn/telegraph"},
            timeout=10,
        )
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ProviderDataError("cls: response too large")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("errno") != 0:
            raise ProviderDataError("cls: upstream rejected request")
        data = payload.get("data")
        rows = data.get("roll_data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProviderDataError("cls: missing news rows")
        items = {}
        for row in rows[:100]:
            try:
                if not isinstance(row, dict) or row.get("is_ad") not in (None, 0, False):
                    continue
                article_id, timestamp = row.get("id"), row.get("ctime")
                if (
                    isinstance(article_id, bool)
                    or not isinstance(article_id, int)
                    or article_id <= 0
                    or isinstance(timestamp, bool)
                    or not isinstance(timestamp, int)
                ):
                    continue
                published = datetime.fromtimestamp(timestamp, SHANGHAI_TZ)
                if not 0 <= (now - published).total_seconds() <= 3 * 86400:
                    continue
                summary = _news_plain_text(row.get("content") or row.get("brief"))
                title = _news_plain_text(row.get("title")) or summary[:80]
                if not title or not summary:
                    continue
                title, summary = title[:400], summary[:4000]
                sentiment, score, keywords = classify_news_sentiment(title, summary)
                identity = f"cls:{article_id}"
                items[identity] = MarketNewsItem(
                    id=identity,
                    title=title,
                    summary=summary,
                    category=_news_category(title, summary),
                    published_at=published,
                    source="财联社",
                    url=f"https://www.cls.cn/detail/{article_id}",
                    important=row.get("level") in ("A", "B") or _important_news(title, summary),
                    sentiment=sentiment,
                    sentiment_score=score,
                    sentiment_keywords=keywords,
                )
            except (OverflowError, OSError, ValueError):
                continue
        if not items:
            raise ProviderDataError("cls: no recent valid news")
        return _Section(
            tuple(sorted(items.values(), key=lambda item: item.published_at, reverse=True)),
            "cailianpress:cls",
        )

    def _load_eastmoney_news(self) -> _Section:
        frame = self._ak.stock_info_global_em()
        required = {"标题", "摘要", "发布时间", "链接"}
        _require_columns(frame, required, "market_news")
        items: list[MarketNewsItem] = []
        for row in frame[list(required)].head(100).to_dict("records"):
            title = _text(row["标题"], "news title")
            summary = _optional_text(row["摘要"]) or title
            published_at = _datetime(row["发布时间"])
            url = _optional_text(row["链接"]) or ""
            category = _news_category(title, summary)
            sentiment, sentiment_score, sentiment_keywords = classify_news_sentiment(title, summary)
            identity = f"{url}|{title}|{published_at.isoformat()}"
            digest = hashlib.sha256(identity.encode()).hexdigest()
            items.append(
                # Keyword tagging is deterministic and explicitly display-only;
                # it never enters strategy signals or order sizing.
                MarketNewsItem(
                    id=digest[:20],
                    title=title,
                    summary=summary,
                    category=category,
                    published_at=published_at,
                    source="东方财富",
                    url=url,
                    important=_important_news(title, summary),
                    sentiment=sentiment,
                    sentiment_score=sentiment_score,
                    sentiment_keywords=sentiment_keywords,
                )
            )
        items.sort(key=lambda item: item.published_at, reverse=True)
        if not items:
            raise ProviderDataError("market_news: provider returned no valid rows")
        return _Section(tuple(items), "akshare:eastmoney")

    def _fetch_tencent_quotes(
        self,
        requested: Sequence[tuple[str, str]],
    ) -> tuple[MarketQuote, ...]:
        lookup = {
            _tencent_id(symbol, asset_type): (symbol, asset_type)
            for symbol, asset_type in requested
        }
        primary = {}
        try:
            response = self._quote_http.get(
                f"https://qt.gtimg.cn/q={','.join(lookup)}",
                timeout=8,
                expected="text",
                module="market_quotes",
            )
            primary = {
                (row.symbol, row.asset_type): row
                for row in self._parse_tencent(response.content, lookup)
            }
        except (ProviderUnavailable, ProviderDataError, ValueError, UnicodeError):
            self._mark_source_failure("tencent", "invalid_or_unavailable")
        missing = {
            ticker: item
            for ticker, item in lookup.items()
            if item not in primary or primary[item].quality_status != "valid"
        }
        backup = {}
        if missing:
            try:
                backup = {
                    (row.symbol, row.asset_type): row for row in self._fetch_sina_quotes(missing)
                }
            except (ProviderUnavailable, ProviderDataError, ValueError, UnicodeError):
                self._mark_source_failure("sina", "invalid_or_unavailable")
        for symbol, row in backup.items():
            previous = primary.get(symbol)
            if previous and quote_conflict(previous, row):
                primary.pop(symbol, None)
                log_validation(
                    "market_quotes",
                    "tencent+sina",
                    quality="invalid",
                    reason="same_clock_price_conflict",
                )
                continue
            if row.quality_status == "valid" or previous is None:
                primary[symbol] = replace(
                    row,
                    fallback_used=True,
                    fallback_reason=previous.quality_status if previous else "primary_unavailable",
                )
        rows = list(primary.values())
        if not rows:
            raise ProviderDataError("market_quotes: provider returned no valid rows")
        order = {(symbol, kind): index for index, (symbol, kind) in enumerate(requested)}
        rows.sort(key=lambda item: order[(item.symbol, item.asset_type)])
        return tuple(rows)

    def _parse_tencent(self, content, lookup):
        rows = []
        for line in content.decode("gb18030", errors="strict").splitlines():
            match = _QUOTE_ROW_RE.fullmatch(line.strip())
            if match is None or match.group(1) not in lookup:
                continue
            fields = match.group(2).split("~")
            if len(fields) <= 32:
                continue
            symbol, asset_type = lookup[match.group(1)]
            if fields[2] != symbol:
                log_validation(
                    "market_quotes", "tencent", quality="invalid", reason="identity_mismatch"
                )
                continue
            try:
                price = _decimal(fields[3], "quote price")
                previous_close = _decimal(fields[4], "quote previous close")
                if price <= 0 or previous_close <= 0:
                    continue
                change = _optional_decimal(fields[31])
                change_rate = _optional_decimal(fields[32])
                if change is None or change_rate is None:
                    raise ProviderDataError("missing quote change or percentage")
                if abs(change - (price - previous_close)) > Decimal("0.03") or abs(
                    change_rate - (price / previous_close - 1) * 100
                ) > Decimal("0.08"):
                    continue
                observed = _quote_datetime(fields[30], self._observed_at())
                meta = metadata(
                    "realtime_quote",
                    "tencent",
                    observed.isoformat(),
                    self._observed_at(),
                    instrument_type=_instrument_type(symbol, asset_type),
                    calendar=self._calendar,
                )
                if meta["quality_status"] == "invalid":
                    continue
                rows.append(
                    MarketQuote(
                        symbol=symbol,
                        name=_text(fields[1], "quote name"),
                        asset_type=asset_type,
                        price=price,
                        change=change,
                        change_rate=change_rate,
                        observed_at=observed,
                        source="tencent",
                        trade_time_verified=_quote_time_verified(fields[30]),
                        volume=_optional_decimal(fields[6]) * 100
                        if asset_type != "index" and _optional_decimal(fields[6]) is not None
                        else None,
                        turnover=_optional_decimal(fields[57]) * 10000
                        if len(fields) > 57 and _optional_decimal(fields[57]) is not None
                        else None,
                        volume_unit="share" if asset_type != "index" else None,
                        turnover_unit="CNY" if len(fields) > 57 else None,
                        **meta,
                    )
                )
            except (ProviderDataError, ValueError, TypeError):
                log_validation(
                    "market_quotes", "tencent", quality="invalid", reason="row_validation"
                )
                continue
        log_validation(
            "market_quotes",
            "tencent",
            count=len(rows),
            source_time=max((r.source_time for r in rows), default=None),
        )
        return tuple(rows)

    def _fetch_sina_quotes(self, lookup):
        response = self._quote_http.get(
            "https://hq.sinajs.cn/list=" + ",".join(lookup),
            headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"},
            timeout=8,
            expected="text",
            module="market_quotes",
        )
        rows = []
        for ticker, raw in re.findall(
            r'var hq_str_([a-z]{2}\d{6})="([^"]*)";', response.content.decode("gb18030")
        ):
            if ticker not in lookup:
                continue
            fields = raw.split(",")
            if len(fields) < 32:
                continue
            symbol, kind = lookup[ticker]
            try:
                price, previous = (
                    _decimal(fields[3], "price"),
                    _decimal(fields[2], "previous_close"),
                )
                if price <= 0 or previous <= 0:
                    continue
                observed = datetime.fromisoformat(fields[30] + "T" + fields[31]).replace(
                    tzinfo=SHANGHAI_TZ
                )
                meta = metadata(
                    "realtime_quote",
                    "sina",
                    observed.isoformat(),
                    self._observed_at(),
                    instrument_type=_instrument_type(symbol, kind),
                    calendar=self._calendar,
                )
                if meta["quality_status"] == "invalid":
                    continue
                # Sina index volume conventions differ between exchanges; leave volume
                # unknown there. Listed ETF/equity quantities are shares, amount is yuan.
                volume = _optional_decimal(fields[8]) if kind != "index" else None
                turnover = _optional_decimal(fields[9])
                if volume is not None and volume < 0 or turnover is not None and turnover < 0:
                    continue
                rows.append(
                    MarketQuote(
                        symbol,
                        fields[0],
                        kind,
                        price,
                        price - previous,
                        (price / previous - 1) * 100,
                        observed,
                        "sina",
                        True,
                        volume,
                        turnover,
                        "share" if volume is not None else None,
                        "CNY",
                        **meta,
                    )
                )
            except (ProviderDataError, ValueError, TypeError):
                log_validation("market_quotes", "sina", quality="invalid", reason="row_validation")
                continue
        log_validation(
            "market_quotes",
            "sina",
            count=len(rows),
            fallback=True,
            source_time=max((r.source_time for r in rows), default=None),
        )
        return tuple(rows)

    def _observed_at(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=SHANGHAI_TZ)
        return value.astimezone(SHANGHAI_TZ)


__all__ = [
    "AkshareMarketFeedProvider",
    "FundRanking",
    "MarketFeedProvider",
    "MarketNewsFeed",
    "MarketNewsItem",
    "MarketQuote",
    "MarketQuoteList",
    "MarketSnapshot",
    "SectorRanking",
]
