"""Mixin providing the system-level read views: health, data status,
latest decision, operational trends, and the analysis-request builder.

``LocalApiQueries`` combines this mixin with the portfolio, preferences,
research, and ledger mixins defined in sibling modules so the 1,100-line
class body becomes navigable without changing its public surface.
"""

from __future__ import annotations

from quant_assistant.models.entities import DataStatus, MarketRegime

from ._common import (
    _count,
    _date,
    _enum_value,
    _field,
    _nonempty_text,
    _optional_datetime,
    _optional_finite_float,
    _optional_int,
    _records,
    _run_id,
    _text_tuple,
)
from .helpers import deduplicate_evidence
from .mappers import _decision_view
from .views import (
    DataStatusView,
    DecisionRunNotFoundError,
    DecisionView,
    HealthView,
    OperationalTrendsView,
    OperationalTrendView,
    SourceHealthTrendView,
)


class _SystemMixin:
    """System-level (database/decision/operations) views and feature access."""

    def health(self) -> HealthView:
        status = "ok"
        version = getattr(self, "_version", "0")
        database = "unavailable"
        try:
            loader = getattr(self._queries, "data_status", None)
            if callable(loader):
                loader()
                database = "connected"
        except (AttributeError, TypeError, ValueError):
            status = "degraded"
        except Exception:
            status = "degraded"
        if database != "connected":
            status = "degraded"
        return HealthView(status=status, version=version, database=database)

    def data_status(self) -> DataStatusView:
        loader = self._queries.data_status
        try:
            snapshot = loader()
        except Exception:
            from quant_assistant.models.entities import DataStatus as _DS

            return DataStatusView(None, _DS.BLOCKED, None, ("data_status_unavailable",))
        from quant_assistant.models.entities import DataStatus as _DS

        blockers = _text_tuple(_field(snapshot, "blockers", ()))
        snapshot_id_value = _field(snapshot, "snapshot_id", _SENTINEL)
        snapshot_id = None if snapshot_id_value is _SENTINEL else int(snapshot_id_value)
        return DataStatusView(
            as_of_date=_field(snapshot, "as_of_date", None),  # type: ignore[arg-type]
            status=_enum_value(_DS, _field(snapshot, "status"), "data status"),
            snapshot_id=snapshot_id,
            blockers=blockers,
        )

    def latest_decision(self) -> DecisionView | None:
        loader = getattr(self._queries, "latest_decision", None)
        if not callable(loader):
            return None
        decision = loader()
        if decision is None:
            return None
        return _decision_view(decision)

    def operational_trends(self, limit: int = 180) -> OperationalTrendsView:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 730:
            raise ValueError("operational trend limit must be between 1 and 730")
        trend_loader = getattr(self._queries, "operational_trends", None)
        source_loader = getattr(self._queries, "source_health_trends", None)
        if not callable(trend_loader) or not callable(source_loader):
            return OperationalTrendsView((), ())
        daily_rows = _records(trend_loader(limit=limit))
        source_rows = _records(source_loader(limit=min(2000, limit * 10)))
        daily = tuple(
            OperationalTrendView(
                as_of_date=_date(_field(row, "as_of_date"), "as_of_date"),
                decision_runs=_count(row, "decision_runs"),
                valid_decisions=_count(row, "valid_decisions"),
                stale_decisions=_count(row, "stale_decisions"),
                blocked_decisions=_count(row, "blocked_decisions"),
                decision_pass_rate=_optional_finite_float(
                    _field(row, "decision_pass_rate", None), "decision_pass_rate"
                ),
                snapshot_count=_count(row, "snapshot_count"),
                valid_snapshots=_count(row, "valid_snapshots"),
                source_count=_count(row, "source_count"),
                snapshot_valid_rate=_optional_finite_float(
                    _field(row, "snapshot_valid_rate", None), "snapshot_valid_rate"
                ),
                latest_fetched_at=_optional_datetime(
                    _field(row, "latest_fetched_at", None), "latest_fetched_at"
                ),
                fetch_lag_days=_optional_int(_field(row, "fetch_lag_days", None), "fetch_lag_days"),
            )
            for row in daily_rows
        )
        sources = tuple(
            SourceHealthTrendView(
                as_of_date=_date(_field(row, "as_of_date"), "as_of_date"),
                source=_nonempty_text(_field(row, "source"), "source"),
                snapshot_count=_count(row, "snapshot_count"),
                valid_snapshots=_count(row, "valid_snapshots"),
                valid_snapshot_rate=_optional_finite_float(
                    _field(row, "valid_snapshot_rate", None), "valid_snapshot_rate"
                ),
                latest_fetched_at=_optional_datetime(
                    _field(row, "latest_fetched_at", None), "latest_fetched_at"
                ),
            )
            for row in source_rows
        )
        return OperationalTrendsView(daily[-limit:], sources)

    def _feature(self, name: str) -> object | None:
        return (
            getattr(self._feature_services, name, None)
            if self._feature_services is not None
            else None
        )

    def _require_feature(self, name: str) -> object:
        service = self._feature(name)
        if service is None:
            raise RuntimeError(f"feature service unavailable: {name}")
        return service

    def analysis_request(self, run_id, user_text):
        from quant_assistant.analysis.models import AnalysisRequest

        from .helpers import candidate_context, normalize_user_text, order_context

        normalized_id = _run_id(run_id)
        decision = self._queries.decision_by_id(normalized_id)
        if decision is None:
            raise DecisionRunNotFoundError("decision run was not found")
        stocks = self._queries.stock_candidates(normalized_id)
        funds = self._queries.fund_candidates(normalized_id)
        candidates, candidate_evidence = candidate_context(normalized_id, stocks, funds)
        orders, order_evidence = order_context(normalized_id, tuple(_field(decision, "orders", ())))
        signals = tuple(_field(decision, "signals", ()))
        strategy_versions = tuple(
            sorted(
                {
                    _nonempty_text(_field(item, "strategy_version"), "strategy_version")
                    for item in (*signals, *tuple(_field(decision, "orders", ())))
                }
            )
        )
        return AnalysisRequest(
            as_of_date=_date(_field(decision, "as_of_date"), "as_of_date"),
            decision_run_id=_nonempty_text(_field(decision, "run_id"), "decision_run_id"),
            strategy_versions=strategy_versions,
            data_status=_enum_value(DataStatus, _field(decision, "status"), "data status"),
            blockers=_text_tuple(_field(decision, "blockers", ())),
            market_regime=_enum_value(
                MarketRegime, _field(decision, "market_regime"), "market regime"
            ),
            portfolio_buckets=self.portfolio().bucket_weights,
            candidates=candidates,
            orders=orders,
            evidence=deduplicate_evidence((*candidate_evidence, *order_evidence)),
            user_text=normalize_user_text(user_text),
        )


# Module-local sentinel keeps the snapshot_id "missing vs zero" distinction.
_SENTINEL: object = object()
