"""Immutable public observations shared by providers and response adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quant_assistant.providers.data_quality import ObservationMetadata
from quant_assistant.providers.index_quotes import IndexQuoteStatus
from quant_assistant.providers.index_registry import IndexDefinition
from quant_assistant.providers.market_breadth import MarketBreadth
from quant_assistant.providers.market_sectors import SectorCatalog, SectorRanking


@dataclass(frozen=True, slots=True)
class MarketQuote(ObservationMetadata):
    symbol: str
    name: str
    asset_type: str
    price: Decimal
    change: Decimal
    change_rate: Decimal
    observed_at: datetime | None
    source: str
    trade_time_verified: bool = False
    volume: Decimal | None = None
    turnover: Decimal | None = None
    volume_unit: str | None = None
    turnover_unit: str | None = None
    id: str | None = None
    currency: str | None = None
    currency_basis: str | None = None
    calendar_id: str | None = None
    source_symbol: str | None = None
    raw_source_time: str | None = None
    change_basis: str | None = None
    cache_fallback: bool = False
    cache_read_at: datetime | None = None


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
    registry_version: str | None = None
    index_registry: tuple[IndexDefinition, ...] = ()
    index_statuses: tuple[IndexQuoteStatus, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketQuoteList:
    items: tuple[MarketQuote, ...]
    unavailable: tuple[str, ...]
    observed_at: datetime
    source: str
    stale: bool
    index_statuses: tuple[IndexQuoteStatus, ...] = ()


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
