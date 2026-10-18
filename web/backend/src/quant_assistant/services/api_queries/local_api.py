"""Concrete ``LocalApiQueries`` implementation composed of feature mixins.

Each mixin in ``local_*.py`` owns one slice of the api surface and depends
on the constructor-defined instance attributes (``self._queries``,
``self._version``, etc.).  Splitting the class this way keeps the
~1,100-line original body navigable without changing public behaviour:

* all the named public method signatures are preserved;
* method-resolution order falls back to the order the mixins are listed
  in ``LocalApiQueries.__mro__`` below.

The combination class itself owns no body code apart from ``__init__``,
which validates the injected dependencies.
"""

from __future__ import annotations

from quant_assistant.providers.fund_detail import PublicFundDetailProvider
from quant_assistant.providers.fund_valuation import FallbackFundValuationProvider
from quant_assistant.providers.market_feed import AkshareMarketFeedProvider
from quant_assistant.reporting.queries import DatabaseUnavailableError

from .local_ledger import _LedgerMixin
from .local_portfolio import _PortfolioMixin
from .local_preferences import _PreferencesMixin
from .local_research import _ResearchMixin
from .local_system import _SystemMixin


class LocalApiQueries(
    _LedgerMixin,
    _PortfolioMixin,
    _PreferencesMixin,
    _ResearchMixin,
    _SystemMixin,
):
    """Copy approved local facts without exposing a database to API or analysis code."""

    def __init__(
        self,
        decision_queries: object,
        *,
        version: str,
        fund_valuation_provider=None,
        market_feed_provider=None,
        fund_detail_provider=None,
        preference_store=None,
        portfolio_commands=None,
        feature_services=None,
        strategy_settings=None,
        agent_research=None,
        photo_upload=None,
    ):
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        required = (
            "latest_decision",
            "decision_by_id",
            "current_positions",
            "stock_candidates",
            "fund_candidates",
            "data_status",
            "latest_backtest",
            "portfolio_valuations",
        )
        if not all(callable(getattr(decision_queries, name, None)) for name in required):
            raise TypeError("decision_queries does not implement the required interface")
        error_type = getattr(
            decision_queries,
            "database_unavailable_error",
            DatabaseUnavailableError,
        )
        if not isinstance(error_type, type) or not issubclass(error_type, Exception):
            raise TypeError("database_unavailable_error must be an exception type")
        self._queries = decision_queries
        self._version = version.strip()
        self._database_unavailable_error = error_type
        provider = fund_valuation_provider or FallbackFundValuationProvider()
        if not callable(getattr(provider, "estimate", None)):
            raise TypeError("fund valuation provider must provide estimate()")
        self._fund_valuation_provider = provider
        market_provider = market_feed_provider or AkshareMarketFeedProvider()
        if not all(
            callable(getattr(market_provider, name, None))
            for name in ("market_snapshot", "market_quotes", "market_news")
        ):
            raise TypeError("market feed provider does not implement the required interface")
        self._market_feed_provider = market_provider
        detail_provider = fund_detail_provider or PublicFundDetailProvider()
        if not callable(getattr(detail_provider, "detail", None)):
            raise TypeError("fund detail provider must provide detail()")
        self._fund_detail_provider = detail_provider
        if preference_store is not None and not all(
            callable(getattr(preference_store, name, None))
            for name in ("load", "replace_watchlist", "mark_news_read")
        ):
            raise TypeError("preference store does not implement the required interface")
        self._preference_store = preference_store
        if portfolio_commands is not None and not all(
            callable(getattr(portfolio_commands, name, None))
            for name in ("record_transaction_batch", "record_dividend")
        ):
            raise TypeError("portfolio commands do not implement the required ledger interface")
        self._portfolio_commands = portfolio_commands
        self._feature_services = feature_services
        self._strategy_settings = strategy_settings
        self.agent_research = agent_research
        self._photo_upload = photo_upload

    def strategy_settings(self):
        return self._strategy_settings.load()

    def save_strategy_settings(self, parameters):
        return self._strategy_settings.save(parameters)

    def preview_strategy_settings(self, parameters):
        return self._strategy_settings.preview(parameters)
