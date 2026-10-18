"""Mixin providing candidate, backtest, and feature-service proxy views.

Every method here is a thin projection over either the injected
``self._queries`` adapter or one of the eight feature services exposed
through ``self._feature_services``.  Splitting them keeps the ledger
write methods isolated from the read-only proxy layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from quant_assistant.models.entities import AssetType

from ._common import (
    _datetime,
    _field,
    _metric,
    _nonempty_text,
    _run_id,
    _text_tuple,
)
from .mappers import (
    _benchmark_view,
    _candidate_views,
    _cash_flow_view,
    _drawdown_view,
    _factor_observation_view,
    _factor_report_view,
    _macro_context_view,
    _macro_observation_view,
    _watchdog_view,
)
from .views import (
    BacktestView,
    BenchmarkConfigView,
    CandidateListView,
    CashFlowPlanView,
    DrawdownValidationView,
    FactorMonitorView,
    FactorObservationView,
    MacroContextView,
    MacroObservationView,
    WatchdogJobView,
)


class _ResearchMixin:
    """Candidate, backtest, drawdown, factor, benchmark, macro, watchdog."""

    def stock_candidates(self, run_id: str) -> CandidateListView:
        normalized_id = _run_id(run_id)
        views, _, _ = _candidate_views(
            normalized_id,
            self._queries.stock_candidates(normalized_id),
            source_kind="stock_candidate",
            expected_asset_type=AssetType.STOCK,
        )
        return CandidateListView(normalized_id, views)

    def fund_candidates(self, run_id: str) -> CandidateListView:
        normalized_id = _run_id(run_id)
        views, _, _ = _candidate_views(
            normalized_id,
            self._queries.fund_candidates(normalized_id),
            source_kind="fund_candidate",
            expected_asset_type=AssetType.FUND,
        )
        return CandidateListView(normalized_id, views)

    def latest_backtest(self) -> BacktestView | None:
        raw = self._queries.latest_backtest()
        if raw is None:
            return None
        metrics = _field(raw, "metrics", {})
        total_return = _metric(raw, metrics, "total_return")
        max_drawdown = _metric(raw, metrics, "max_drawdown")
        reasons = _text_tuple(_field(raw, "research_only_reasons", _field(raw, "limitations", ())))
        return BacktestView(
            run_id=_nonempty_text(_field(raw, "run_id"), "backtest run_id"),
            completed_at=_datetime(_field(raw, "completed_at"), "completed_at"),
            status=_nonempty_text(_field(raw, "status"), "backtest status"),
            total_return=total_return,
            max_drawdown=max_drawdown,
            research_only_reasons=reasons,
        )

    def drawdown_validation(self, run_id: str | None = None) -> DrawdownValidationView | None:
        service = self._feature("drawdown")
        if service is None:
            return None
        value = service.latest(run_id)
        return None if value is None else _drawdown_view(value)

    def validate_drawdown(
        self, run_id: str, nav: object, threshold: float = 0.20
    ) -> DrawdownValidationView:
        service = self._feature("drawdown")
        if service is None:
            from quant_assistant.services.roadmap_features import validate_drawdown

            value = validate_drawdown(run_id, nav, threshold=threshold)
        else:
            value = service.validate_and_store(run_id, nav, threshold=threshold)
        return _drawdown_view(value)

    def cash_flow_plans(
        self, status: str | None = None, limit: int = 100
    ) -> tuple[CashFlowPlanView, ...]:
        service = self._feature("cash_flows")
        if service is None:
            return ()
        return tuple(_cash_flow_view(item) for item in service.list(status=status, limit=limit))

    def create_cash_flow_plan(self, payload: Mapping[str, object]) -> CashFlowPlanView:
        service = self._require_feature("cash_flows")
        value = service.create(
            payload.get("name"),
            payload.get("direction"),
            payload.get("amount"),
            payload.get("frequency"),
            payload.get("start_date"),
            end_date=payload.get("end_date"),
            note=payload.get("note"),
            plan_id=payload.get("plan_id"),
        )
        return _cash_flow_view(value)

    def cash_flow_due(self, as_of: date) -> tuple[Mapping[str, object], ...]:
        service = self._feature("cash_flows")
        return () if service is None else service.due_events(as_of)

    def factor_monitor(self, factor: str, limit: int = 60) -> FactorMonitorView:
        service = self._require_feature("factor_monitor")
        return _factor_report_view(service.report(factor, limit=limit))

    def record_factor_observation(self, payload: Mapping[str, object]) -> FactorObservationView:
        service = self._require_feature("factor_monitor")
        value = service.record(
            payload.get("factor"),
            payload.get("as_of_date"),
            ic=payload.get("ic"),
            ir=payload.get("ir"),
            sample_count=payload.get("sample_count", 0),
            source=payload.get("source", "manual"),
        )
        return _factor_observation_view(value)

    def factor_alerts(
        self, factor: str | None = None, limit: int = 100
    ) -> tuple[Mapping[str, object], ...]:
        service = self._feature("factor_monitor")
        return () if service is None else service.alerts(factor=factor, limit=limit)

    def benchmarks(self, limit: int = 100) -> tuple[BenchmarkConfigView, ...]:
        service = self._feature("benchmarks")
        return (
            ()
            if service is None
            else tuple(_benchmark_view(item) for item in service.list(limit=limit))
        )

    def create_benchmark(self, payload: Mapping[str, object]) -> BenchmarkConfigView:
        service = self._require_feature("benchmarks")
        components = payload.get("components")
        if not isinstance(components, Mapping):
            raise TypeError("benchmark components must be an object")
        value = service.create(
            payload.get("name"),
            components,
            benchmark_id=payload.get("benchmark_id"),
            is_default=bool(payload.get("is_default", False)),
        )
        return _benchmark_view(value)

    def compare_benchmark(
        self, benchmark_id: str, start_date: date | None = None, end_date: date | None = None
    ) -> Mapping[str, object]:
        service = self._require_feature("benchmarks")
        return service.compare(benchmark_id, start_date=start_date, end_date=end_date)

    def macro_context(self, as_of: date | None = None) -> MacroContextView:
        service = self._feature("macro")
        if service is None:
            target = as_of or date.today()
            return MacroContextView(
                target, "blocked", ("interest_rate", "pmi", "social_financing"), None, (), True
            )
        return _macro_context_view(service.context(as_of=as_of))

    def macro_observations(
        self, indicator: str | None = None, limit: int = 100
    ) -> tuple[MacroObservationView, ...]:
        service = self._feature("macro")
        return (
            ()
            if service is None
            else tuple(
                _macro_observation_view(item)
                for item in service.list(indicator=indicator, limit=limit)
            )
        )

    def record_macro_observation(self, payload: Mapping[str, object]) -> MacroObservationView:
        service = self._require_feature("macro")
        value = service.record(
            payload.get("indicator"),
            payload.get("observed_on"),
            payload.get("value"),
            payload.get("unit"),
            payload.get("source", "manual"),
        )
        return _macro_observation_view(value)

    def watchdog_scan(self) -> tuple[WatchdogJobView, ...]:
        service = self._feature("watchdog")
        if service is None:
            return ()
        return tuple(_watchdog_view(item) for item in service.scan())

    def record_watchdog_heartbeat(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        service = self._require_feature("watchdog")
        service.heartbeat(
            payload.get("job_name"),
            expected_interval_minutes=payload.get("expected_interval_minutes", 1440),
            status=payload.get("status", "completed"),
            started_at=payload.get("started_at"),
            completed_at=payload.get("completed_at"),
            error=payload.get("error"),
        )
        return {
            "job_name": payload.get("job_name"),
            "status": payload.get("status", "completed"),
            "recorded": True,
        }
