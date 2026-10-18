"""Mixin providing the user-preference, market, and watchlist views.

These methods are stateless projections over injected providers
(``self._preference_store``, ``self._market_feed_provider``).  Splitting
them out makes the write paths in ``local_ledger.py`` easier to scan.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from quant_assistant.services.watchlist import WatchlistEntry

from .mappers import _preference_view
from .views import (
    MarketNewsFeed,
    MarketQuoteList,
    MarketSnapshot,
    PreferenceWatchlistItemView,
    UserPreferencesView,
)


class _PreferencesMixin:
    """User preferences (watchlist, news read state) and market data."""

    def user_preferences(self) -> UserPreferencesView:
        if self._preference_store is None:
            return UserPreferencesView((), (), 0, None)
        return _preference_view(self._preference_store.load())

    def replace_watchlist(
        self,
        items: Sequence[PreferenceWatchlistItemView],
    ) -> UserPreferencesView:
        if self._preference_store is None:
            raise RuntimeError("preference store is unavailable")
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            raise TypeError("watchlist items must be a sequence")
        if any(not isinstance(item, PreferenceWatchlistItemView) for item in items):
            raise TypeError("watchlist items must use PreferenceWatchlistItemView")
        entries = tuple(WatchlistEntry(item.symbol, item.name, item.asset_type) for item in items)
        return _preference_view(self._preference_store.replace_watchlist(entries))

    def mark_news_read(self, ids: Sequence[str]) -> UserPreferencesView:
        if self._preference_store is None:
            raise RuntimeError("preference store is unavailable")
        if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
            raise TypeError("news ids must be a sequence")
        return _preference_view(self._preference_store.mark_news_read(ids))

    def market_snapshot(self, section: str | None = None) -> MarketSnapshot:
        if section is None:
            return self._market_feed_provider.market_snapshot()
        return self._market_feed_provider.market_snapshot(section=section)

    def market_quotes(
        self,
        stock_symbols: Sequence[str] = (),
        index_symbols: Sequence[str] = (),
    ) -> MarketQuoteList:
        return self._market_feed_provider.market_quotes(stock_symbols, index_symbols)

    def market_news(self, limit: int = 30) -> MarketNewsFeed:
        return self._market_feed_provider.market_news(limit)

    def market_source_status(self) -> pd.DataFrame:
        """Expose provider health without leaking credentials or raw errors."""

        status = getattr(self._market_feed_provider, "source_status", None)
        if not callable(status):
            return pd.DataFrame()
        result = status()
        return result.copy(deep=True) if isinstance(result, pd.DataFrame) else pd.DataFrame()
