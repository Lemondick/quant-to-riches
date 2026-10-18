"""Mixin providing portfolio, fund lifecycle, valuation, and style-drift views.

Holds methods that mostly project ``self._queries`` or
``self._fund_*_provider`` into frozen View objects.  Provider interactions
stay injected through ``__init__`` so the mixin can stay focused on
projection.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from decimal import Decimal

from quant_assistant.analysis.models import PortfolioBucket
from quant_assistant.models.entities import AssetType
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.data_quality import ObservationMetadata
from quant_assistant.providers.fund_detail import FundDetail
from quant_assistant.providers.fund_valuation import normalize_fund_symbol
from quant_assistant.services.fund_fee_evidence import FundFeeEvidenceService

from ._common import (
    _boolean,
    _date,
    _datetime,
    _decimal,
    _field,
    _nonempty_text,
    _records,
    _run_id,
)
from .mappers import (
    _fund_lifecycle_event_view,
    _fund_style_drift_event_view,
    _positions,
    _valuation_alert_view,
)
from .views import (
    FundLifecycleEventView,
    FundStyleDriftEventView,
    FundValuationAlertView,
    FundValuationsView,
    FundValuationView,
    PortfolioHistoryPointView,
    PortfolioHistoryView,
    PortfolioView,
)


class _PortfolioMixin:
    """Read-only portfolio and fund-event views."""

    def portfolio(self) -> PortfolioView:
        positions = _positions(self._queries.current_positions())
        positive_values: dict[str, Decimal] = {}
        for item in positions:
            if item.market_value < 0:
                raise ValueError("position_market_value_negative")
            if item.market_value == 0:
                continue
            key = item.asset_type.value
            positive_values[key] = positive_values.get(key, Decimal("0")) + item.market_value
        total = sum(positive_values.values(), Decimal("0"))
        buckets = (
            tuple(
                PortfolioBucket(name, value / total)
                for name, value in sorted(positive_values.items())
            )
            if total > 0
            else ()
        )
        as_of_date = max((item.as_of_date for item in positions), default=None)
        return PortfolioView(as_of_date, positions, buckets)

    def portfolio_history(self, limit: int = 180) -> PortfolioHistoryView:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 730:
            raise ValueError("portfolio history limit must be between 1 and 730")
        points: list[PortfolioHistoryPointView] = []
        valuation_loader = self._queries.portfolio_valuations
        for row in _records(valuation_loader(limit=limit)):
            points.append(
                PortfolioHistoryPointView(
                    run_id=_run_id(_field(row, "run_id")),
                    as_of_date=_date(_field(row, "as_of_date"), "as_of_date"),
                    generated_at=_datetime(_field(row, "generated_at"), "generated_at"),
                    total_value=_decimal(_field(row, "total_value"), "total_value"),
                    cash=_decimal(_field(row, "cash"), "cash"),
                    unit_nav=_decimal(_field(row, "unit_nav"), "unit_nav"),
                    high_water_mark=_decimal(_field(row, "high_water_mark"), "high_water_mark"),
                    net_external_flow=_decimal(
                        _field(row, "net_external_flow", 0), "net_external_flow"
                    ),
                    is_baseline=_boolean(_field(row, "is_baseline", False), "is_baseline"),
                )
            )
        points.sort(key=lambda item: (item.as_of_date, item.generated_at, item.run_id))
        return PortfolioHistoryView(tuple(points[-limit:]))

    def fund_lifecycle_events(
        self, symbol: str | None = None, limit: int = 100
    ) -> tuple[FundLifecycleEventView, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("fund lifecycle event limit must be between 1 and 500")
        normalized = None if symbol is None else normalize_fund_symbol(symbol)
        loader = getattr(self._queries, "fund_lifecycle_events", None)
        if not callable(loader):
            return ()
        try:
            rows = loader(symbol=normalized, limit=limit)
        except TypeError:
            rows = loader(normalized, limit)
        views = tuple(_fund_lifecycle_event_view(row) for row in _records(rows))
        return tuple(
            sorted(views, key=lambda item: (item.observed_on, item.id), reverse=True)[:limit]
        )

    def valuation_alerts(
        self, symbol: str | None = None, limit: int = 100
    ) -> tuple[FundValuationAlertView, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("valuation alert limit must be between 1 and 500")
        normalized = None if symbol is None else normalize_fund_symbol(symbol)
        loader = getattr(self._queries, "valuation_alerts", None)
        if not callable(loader):
            return ()
        try:
            rows = loader(symbol=normalized, limit=limit)
        except TypeError:
            rows = loader(normalized, limit)
        views = tuple(_valuation_alert_view(row) for row in _records(rows))
        return tuple(views[:limit])

    def fund_style_drift_events(
        self, symbol: str | None = None, limit: int = 100
    ) -> tuple[FundStyleDriftEventView, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("fund style drift event limit must be between 1 and 500")
        normalized = None if symbol is None else normalize_fund_symbol(symbol)
        loader = getattr(self._queries, "fund_style_drift_events", None)
        if not callable(loader):
            return ()
        try:
            rows = loader(symbol=normalized, limit=limit)
        except TypeError:
            rows = loader(normalized, limit)
        views = tuple(_fund_style_drift_event_view(row) for row in _records(rows))
        return tuple(
            sorted(views, key=lambda item: (item.report_date, item.id), reverse=True)[:limit]
        )

    def fund_valuations(self, symbols: Sequence[str] = ()) -> FundValuationsView:
        if not isinstance(symbols, Sequence) or isinstance(symbols, (str, bytes)):
            raise TypeError("fund symbols must be a sequence")
        requested = list(symbols)
        if not requested:
            requested = [
                item.symbol
                for item in _positions(self._queries.current_positions())
                if item.asset_type is AssetType.FUND and item.quantity > 0
            ]
        normalized = tuple(dict.fromkeys(normalize_fund_symbol(item) for item in requested))
        if len(normalized) > 20:
            raise ValueError("at most 20 fund valuations can be requested")

        def load(symbol: str) -> FundValuationView:
            estimate = self._fund_valuation_provider.estimate(symbol)
            estimate_nav = (
                _decimal(estimate.estimate_nav, "estimate_nav")
                if estimate.estimate_nav is not None
                else None
            )
            previous_nav = _decimal(estimate.previous_nav, "previous_nav")
            valuation_type = str(getattr(estimate, "valuation_type", "intraday_estimate")).strip()
            if valuation_type not in {"intraday_estimate", "latest_nav"}:
                raise ValueError("invalid fund valuation type")
            return FundValuationView(
                symbol=normalize_fund_symbol(estimate.symbol),
                estimate_nav=estimate_nav,
                previous_nav=previous_nav,
                change_rate=_decimal(estimate.change_rate, "change_rate")
                if estimate.change_rate is not None
                else None,
                change_amount=estimate_nav - previous_nav if estimate_nav is not None else None,
                observed_at=_datetime(estimate.observed_at, "observed_at")
                if estimate.observed_at
                else None,
                source=_nonempty_text(estimate.source, "valuation source"),
                nav_date=estimate.nav_date,
                valuation_type=valuation_type,
                official_nav=estimate.official_nav,
                official_change_rate=estimate.official_change_rate,
                **{
                    key: getattr(estimate, key)
                    for key in estimate.__dataclass_fields__
                    if key in FundValuationView.__dataclass_fields__
                    and key in ObservationMetadata.__dataclass_fields__
                },
            )

        items: list[FundValuationView] = []
        unavailable: list[str] = []
        if normalized:
            with ThreadPoolExecutor(max_workers=min(4, len(normalized))) as executor:
                futures = {
                    executor.submit(copy_context().run, load, symbol): symbol
                    for symbol in normalized
                }
                for future in as_completed(futures):
                    try:
                        items.append(future.result())
                    except Exception:
                        unavailable.append(futures[future])
        sorted_items = tuple(sorted(items, key=lambda item: item.symbol))
        return FundValuationsView(
            sorted_items,
            tuple(sorted(unavailable)),
            estimate_only=all(item.valuation_type == "intraday_estimate" for item in sorted_items),
        )

    def fund_detail(self, symbol: str) -> FundDetail:
        return self._fund_detail_provider.detail(normalize_fund_symbol(symbol))

    def _fund_fee_service(self) -> FundFeeEvidenceService:
        database = getattr(self._queries, "database", None)
        if not isinstance(database, Database):
            raise ValueError("基金费率证据存储未配置")
        return FundFeeEvidenceService(database)

    def fund_fee_evidence(self, symbol: str) -> dict:
        return self._fund_fee_service().list(symbol)

    def save_fund_fee_evidence(self, schedule) -> dict:
        return self._fund_fee_service().save(schedule)

    def fund_fee_quote(self, symbol: str, **kwargs) -> dict:
        return self._fund_fee_service().quote(symbol, **kwargs)

    def fund_redemption_quote(self, symbol: str, lots, confirmed_on) -> dict:
        return self._fund_fee_service().redemption_quote(symbol, lots, confirmed_on)
