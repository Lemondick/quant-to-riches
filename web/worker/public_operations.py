"""Public display queries without loading the financial and AI execution stack."""

import json
import logging
import os

from worker_cache import cache_get, cache_invalidate, cache_key, cache_set
from worker_result import child_envelope

_public_logger = logging.getLogger("quant_assistant.providers")
if not _public_logger.handlers:
    _public_logger.addHandler(logging.StreamHandler())
_public_logger.setLevel(logging.INFO)

PUBLIC_CAPABILITIES = (
    "market.snapshot",
    "market.quotes",
    "market.news",
    "market.history",
    "fund.detail",
    "fund.catalog",
)
IN_PROCESS_CAPABILITIES = (*PUBLIC_CAPABILITIES, "fund.valuations")


# Provider instances are cached in the unified worker cache (contract section 4).
# Keys are "{namespace}:{version}:{sha256}" strings; the old @lru_cache keyed on
# the unbounded snapshot-JSON key space with no TTL and mixed refresh semantics.
_PROVIDER_CACHE_VERSION = "1"


def _provider_cache_ttl():
    try:
        return max(60, int(os.environ.get("QUANT_WORKER_PROVIDER_CACHE_TTL_S", "21600")))
    except (TypeError, ValueError):
        return 21600


_PROVIDER_CACHE_TTL_S = _provider_cache_ttl()


def _provider_key(namespace, snapshot):
    return cache_key(namespace, _PROVIDER_CACHE_VERSION, snapshot or "default")


def _cached_provider(namespace, snapshot, build):
    key = _provider_key(namespace, snapshot)
    provider = cache_get(key)
    if provider is None:
        provider = build()
        cache_set(key, provider, ttl_s=_PROVIDER_CACHE_TTL_S)
    return provider


def _catalog_provider():
    from quant_assistant.common.performance import traced_get
    from quant_assistant.providers.fund_catalog import FundCatalogProvider

    return _cached_provider(
        "provider.fund_catalog", None, lambda: FundCatalogProvider(http_get=traced_get)
    )


def _detail_provider(snapshot=None):
    from quant_assistant.common.performance import traced_get
    from quant_assistant.providers.fund_detail import PublicFundDetailProvider

    return _cached_provider(
        "provider.fund_detail",
        snapshot,
        lambda: PublicFundDetailProvider(http_get=traced_get, calendar=_calendar(snapshot)),
    )


def _market_provider(snapshot=None):
    from quant_assistant.common.performance import traced_get
    from quant_assistant.providers.market_feed import AkshareMarketFeedProvider

    return _cached_provider(
        "provider.market_feed",
        snapshot,
        lambda: AkshareMarketFeedProvider(http_get=traced_get, calendar=_calendar(snapshot)),
    )


def _valuation_provider(snapshot=None):
    from quant_assistant.providers.fund_valuation import EastmoneyFundValuationProvider
    from quant_assistant.services.runtime_limits import PublicReadCache

    return _cached_provider(
        "provider.fund_valuation",
        snapshot,
        lambda: PublicReadCache(
            EastmoneyFundValuationProvider(calendar=_calendar(snapshot)), 60
        ),
    )


def _calendar(snapshot):
    from quant_assistant.providers.market_calendar import MarketCalendars

    return MarketCalendars.from_payload(json.loads(snapshot) if snapshot is not None else None)


def execute_public(kind: str, payload: dict) -> dict:
    if kind == "fund.catalog":
        if any(key not in {"_refresh", "_calendars"} for key in payload):
            raise ValueError("fund catalog does not accept account or instrument inputs")
        provider = _catalog_provider()
        if payload.get("_refresh"):
            provider.expire_cache()
        return provider.read()
    snapshot = (
        json.dumps(payload["_calendars"], sort_keys=True) if "_calendars" in payload else None
    )
    calendar = _calendar(snapshot)

    def get(factory):
        return factory() if snapshot is None else factory(snapshot)

    if kind == "market.history":
        from public_charts import index_history

        from quant_assistant.common.performance import traced_get

        return index_history(
            payload.get("symbol"),
            index_id=payload.get("index_id"),
            http_get=traced_get,
            calendar=calendar,
        )
    from quant_assistant.controllers.schemas import (
        FundDetailResponse,
        FundValuationsResponse,
        MarketNewsFeedResponse,
        MarketQuoteListResponse,
        MarketSnapshotResponse,
    )

    if kind == "fund.detail":
        if payload.get("_refresh"):
            get(_detail_provider).expire_cache()
        result = get(_detail_provider).detail(payload["symbol"])
        schema = FundDetailResponse
    elif kind == "fund.valuations":
        from quant_assistant.providers.fund_valuation import (
            FallbackFundValuationProvider,
            LocalFundNavProvider,
        )
        from quant_assistant.services.api_queries.local_portfolio import _PortfolioMixin
        from quant_assistant.services.api_queries.views import FundValuationsView

        if payload.get("_refresh"):
            # Drop the cached provider instance (old: _valuation_provider.cache_clear()).
            cache_invalidate("provider.fund_valuation")
        query = _PortfolioMixin()
        query._fund_valuation_provider = FallbackFundValuationProvider(
            primary=get(_valuation_provider),
            fallback=LocalFundNavProvider(payload.get("local_nav", {}), calendar=calendar),
        )
        symbols = payload["symbols"]
        result = query.fund_valuations(symbols) if symbols else FundValuationsView((), ())
        schema = FundValuationsResponse
    elif kind in PUBLIC_CAPABILITIES:
        provider = get(_market_provider)
        if payload.get("_refresh"):
            provider.expire_cache()
        if kind == "market.snapshot":
            result = provider.market_snapshot(payload.get("section"))
            schema = MarketSnapshotResponse
        elif kind == "market.quotes":
            result = provider.market_quotes(payload.get("stocks", ()), payload.get("indices", ()))
            schema = MarketQuoteListResponse
        else:
            result = provider.market_news(payload.get("limit", 30))
            schema = MarketNewsFeedResponse
    else:
        raise ValueError("unsupported_public_operation")
    from quant_assistant.providers.data_quality import enrich_payload

    data = enrich_payload(
        kind, schema.model_validate(result).model_dump(mode="json"), calendar=calendar
    )
    from quant_assistant.providers.public_http import log_validation

    records = data.get("items", data.get("nav_history", data.get("indices", [])))
    log_validation(
        kind,
        data.get("source", "public"),
        count=len(records),
        source_time=data.get("updated_at") or data.get("nav_updated_at"),
        quality="stale" if data.get("stale") else data.get("quality_status", "valid"),
    )
    return data


def child_public_operation(kind: str, payload: dict) -> dict:
    # Shared envelope (contract section 4); provider details stay redacted
    # at this boundary.
    return child_envelope(lambda: execute_public(kind, payload))
