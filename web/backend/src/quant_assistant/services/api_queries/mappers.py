"""Private mapping helpers that convert raw query records into View dataclasses.

Every mapper here preserves the exact coercion rules the original monolithic
``api_queries.py`` used; splitting them simply removes the structural noise
from the LocalApiQueries body so the ``LocalApiQueries`` mixins can stay
focused on control flow.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from quant_assistant.analysis.models import AnalysisCandidate, Evidence
from quant_assistant.models.entities import (
    Action,
    AssetType,
    DataStatus,
    MarketRegime,
    ProposedOrder,
    ReadinessStatus,
)
from quant_assistant.providers.fund_valuation import normalize_fund_symbol
from quant_assistant.services.roadmap_features import (
    BenchmarkConfig,
    CashFlowPlan,
    DrawdownValidation,
    FactorMonitorReport,
    FactorObservation,
    MacroObservation,
)

from ._common import (
    _MISSING,
    _boolean,
    _date,
    _datetime,
    _decimal,
    _enum_value,
    _evidence_id,
    _field,
    _finite_float,
    _json_list,
    _json_mapping,
    _nonempty_text,
    _optional_datetime,
    _optional_decimal_value,
    _optional_int,
    _optional_text_value,
    _records,
    _text_tuple,
)
from .views import (
    BenchmarkConfigView,
    CandidateView,
    CashFlowPlanView,
    CorporateActionView,
    DecisionView,
    DrawdownValidationView,
    FactorMonitorView,
    FactorObservationView,
    FundLifecycleEventView,
    FundStyleDriftEventView,
    FundValuationAlertView,
    LedgerCorrectionView,
    LedgerEntryView,
    MacroContextView,
    MacroObservationView,
    PortfolioPositionView,
    PreferenceWatchlistItemView,
    UserPreferencesView,
    WatchdogJobView,
)


def _preference_view(snapshot) -> UserPreferencesView:
    from quant_assistant.services.preferences import PreferenceSnapshot

    if not isinstance(snapshot, PreferenceSnapshot):
        raise TypeError("preference store must return PreferenceSnapshot")
    return UserPreferencesView(
        watchlist=tuple(
            PreferenceWatchlistItemView(item.symbol, item.name, item.asset_type)
            for item in snapshot.watchlist
        ),
        read_news_ids=snapshot.read_news_ids,
        revision=snapshot.revision,
        updated_at=snapshot.updated_at,
    )


def _drawdown_view(value: DrawdownValidation) -> DrawdownValidationView:
    return DrawdownValidationView(
        value.run_id,
        value.threshold,
        value.max_drawdown,
        value.observed_sessions,
        value.breach_count,
        value.passed,
        value.reasons,
        value.research_only,
    )


def _cash_flow_view(value: CashFlowPlan) -> CashFlowPlanView:
    return CashFlowPlanView(
        value.plan_id,
        value.name,
        value.direction,
        value.amount,
        value.frequency,
        value.start_date,
        value.end_date,
        value.status,
        value.note,
        value.schedule(until=value.start_date + timedelta(days=365))[:12],
        value.created_at,
        value.updated_at,
    )


def _factor_observation_view(value: FactorObservation) -> FactorObservationView:
    return FactorObservationView(
        value.factor,
        value.as_of_date,
        value.ic,
        value.ir,
        value.sample_count,
        value.source,
        value.created_at,
    )


def _factor_report_view(value: FactorMonitorReport) -> FactorMonitorView:
    return FactorMonitorView(
        value.factor,
        tuple(_factor_observation_view(item) for item in value.observations),
        value.mean_ic,
        value.mean_ir,
        value.ic_slope,
        value.ir_slope,
        value.decay,
        value.alerts,
        value.research_only,
    )


def _benchmark_view(value: BenchmarkConfig) -> BenchmarkConfigView:
    return BenchmarkConfigView(
        value.benchmark_id,
        value.name,
        dict(value.components),
        value.is_default,
        value.created_at,
        value.updated_at,
    )


def _macro_observation_view(value: MacroObservation) -> MacroObservationView:
    return MacroObservationView(
        value.indicator, value.observed_on, value.value, value.unit, value.source, value.created_at
    )


def _macro_context_view(value: Mapping[str, object]) -> MacroContextView:
    rows = value.get("observations", ())
    observations = tuple(
        _macro_observation_view(item) for item in rows if isinstance(item, MacroObservation)
    )
    return MacroContextView(
        _date(value.get("as_of_date"), "as_of_date"),
        _nonempty_text(value.get("status"), "macro status"),
        _text_tuple(value.get("missing", ())),
        _optional_int(value.get("max_age_days"), "max_age_days"),
        observations,
        bool(value.get("research_only", True)),
    )


def _watchdog_view(value: Mapping[str, object]) -> WatchdogJobView:
    return WatchdogJobView(
        _nonempty_text(value.get("job_name"), "job_name"),
        int(value.get("expected_interval_minutes", 0)),
        _optional_datetime(value.get("last_started_at"), "last_started_at"),
        _optional_datetime(value.get("last_completed_at"), "last_completed_at"),
        _nonempty_text(value.get("status"), "status"),
        _optional_text_value(value.get("last_error")),
        bool(value.get("overdue", False)),
        bool(value.get("failed", False)),
        bool(value.get("healthy", False)),
    )


def _ledger_entry_view(row: object) -> LedgerEntryView:
    entry_type = _nonempty_text(_field(row, "entry_type"), "ledger entry_type")
    quantity = _optional_decimal_value(_field(row, "quantity", None), "ledger quantity")
    price = _optional_decimal_value(_field(row, "price", None), "ledger price")
    reversed_value = _field(row, "reversed_by_correction_id", None)
    return LedgerEntryView(
        id=int(_field(row, "id")),
        entry_type=entry_type,
        symbol=_nonempty_text(_field(row, "symbol"), "ledger symbol"),
        name=_nonempty_text(_field(row, "name", _field(row, "symbol")), "ledger name"),
        asset_type=_nonempty_text(_field(row, "asset_type"), "ledger asset_type"),
        quantity=quantity,
        price=price,
        gross_amount=_decimal(_field(row, "gross_amount"), "ledger gross_amount"),
        fees=_decimal(_field(row, "fees"), "ledger fees"),
        net_amount=_decimal(_field(row, "net_amount"), "ledger net_amount"),
        occurred_at=_datetime(_field(row, "occurred_at"), "ledger occurred_at"),
        external_id=_nonempty_text(_field(row, "external_id"), "ledger external_id"),
        note=_optional_text_value(_field(row, "note", None)),
        reversed=reversed_value is not None,
    )


def _fund_lifecycle_event_view(row: object) -> FundLifecycleEventView:
    details_raw = _field(row, "details", _field(row, "details_json", {}))
    details = _json_mapping(details_raw)
    return FundLifecycleEventView(
        id=int(_field(row, "id")),
        symbol=normalize_fund_symbol(_field(row, "symbol")),
        observed_on=_date(_field(row, "observed_on"), "observed_on"),
        event_type=_nonempty_text(_field(row, "event_type"), "event_type"),
        severity=_nonempty_text(_field(row, "severity"), "severity"),
        message=_nonempty_text(_field(row, "message"), "message"),
        previous_value=_optional_text_value(_field(row, "previous_value", None)),
        current_value=_optional_text_value(_field(row, "current_value", None)),
        source=_nonempty_text(_field(row, "source"), "source"),
        details=details,
        created_at=_datetime(_field(row, "created_at"), "created_at"),
    )


def _valuation_alert_view(row: object) -> FundValuationAlertView:
    details = _json_mapping(_field(row, "details", _field(row, "details_json", {})))
    alert_key = _nonempty_text(_field(row, "alert_key"), "alert_key")
    fallback_symbol = alert_key.rsplit(":", 1)[-1]
    symbol = normalize_fund_symbol(details.get("symbol", fallback_symbol))
    return FundValuationAlertView(
        id=int(_field(row, "id")),
        symbol=symbol,
        severity=_nonempty_text(_field(row, "severity"), "severity"),
        message=_nonempty_text(_field(row, "message"), "message"),
        change_rate=_optional_decimal_value(
            details.get("change_rate_percent"), "change_rate_percent"
        ),
        estimate_nav=_optional_decimal_value(details.get("estimate_nav"), "estimate_nav"),
        previous_nav=_optional_decimal_value(details.get("previous_nav"), "previous_nav"),
        observed_at=_optional_datetime(details.get("observed_at"), "observed_at"),
        source=_optional_text_value(details.get("source")),
        first_seen_at=_datetime(_field(row, "first_seen_at"), "first_seen_at"),
        last_seen_at=_datetime(_field(row, "last_seen_at"), "last_seen_at"),
        last_notified_at=_optional_datetime(
            _field(row, "last_notified_at", None), "last_notified_at"
        ),
        silenced_until=_optional_datetime(_field(row, "silenced_until", None), "silenced_until"),
        details=details,
    )


def _fund_style_drift_event_view(row: object) -> FundStyleDriftEventView:
    details_raw = _field(row, "details", _MISSING)
    if details_raw is _MISSING:
        details_raw = _field(row, "details_json", {})
    details = _json_mapping(details_raw)
    added_raw = _field(row, "added_holdings", _MISSING)
    if added_raw is _MISSING:
        added_raw = _field(row, "added_json", [])
    removed_raw = _field(row, "removed_holdings", _MISSING)
    if removed_raw is _MISSING:
        removed_raw = _field(row, "removed_json", [])
    added = _text_tuple(_json_list(added_raw))
    removed = _text_tuple(_json_list(removed_raw))
    drift_value = details.get("drift_detected", _field(row, "drift_detected", False))
    return FundStyleDriftEventView(
        id=int(_field(row, "id")),
        symbol=normalize_fund_symbol(_field(row, "symbol")),
        report_date=_date(_field(row, "report_date"), "report_date"),
        previous_report_date=_date(_field(row, "previous_report_date"), "previous_report_date"),
        severity=_nonempty_text(_field(row, "severity"), "severity"),
        overlap_ratio=_decimal(_field(row, "overlap_ratio"), "overlap_ratio"),
        message=_nonempty_text(_field(row, "message"), "message"),
        added_holdings=added,
        removed_holdings=removed,
        drift_detected=_boolean(drift_value, "drift_detected"),
        source=_nonempty_text(_field(row, "source"), "source"),
        details=details,
        created_at=_datetime(_field(row, "created_at"), "created_at"),
    )


def _corporate_action_view(row: object) -> CorporateActionView:
    return CorporateActionView(
        id=int(_field(row, "id")),
        action_type=_nonempty_text(_field(row, "action_type"), "action_type"),
        symbol=_nonempty_text(_field(row, "symbol"), "symbol"),
        target_symbol=_optional_text_value(_field(row, "target_symbol", None)),
        asset_type=_nonempty_text(_field(row, "asset_type"), "asset_type"),
        quantity=_optional_decimal_value(_field(row, "quantity", None), "quantity"),
        target_quantity=_optional_decimal_value(
            _field(row, "target_quantity", None), "target_quantity"
        ),
        price=_optional_decimal_value(_field(row, "price", None), "price"),
        target_price=_optional_decimal_value(_field(row, "target_price", None), "target_price"),
        gross_amount=_decimal(_field(row, "gross_amount", 0), "gross_amount"),
        fees=_decimal(_field(row, "fees", 0), "fees"),
        cash_delta=_decimal(_field(row, "cash_delta", 0), "cash_delta"),
        ratio=_optional_decimal_value(_field(row, "ratio", None), "ratio"),
        effective_at=_datetime(_field(row, "effective_at"), "effective_at"),
        external_id=_nonempty_text(_field(row, "external_id"), "external_id"),
        source=_nonempty_text(_field(row, "source"), "source"),
        from_account=_optional_text_value(_field(row, "from_account", None)),
        to_account=_optional_text_value(_field(row, "to_account", None)),
        note=_optional_text_value(_field(row, "note", None)),
    )


def _ledger_correction_view(row: object) -> LedgerCorrectionView:
    return LedgerCorrectionView(
        id=int(_field(row, "id")),
        target_type=_nonempty_text(_field(row, "target_type"), "target_type"),
        target_id=int(_field(row, "target_id")),
        reason=_nonempty_text(_field(row, "reason"), "reason"),
        effective_at=_datetime(_field(row, "effective_at"), "effective_at"),
        external_id=_nonempty_text(_field(row, "external_id"), "external_id"),
        source=_nonempty_text(_field(row, "source"), "source"),
        note=_optional_text_value(_field(row, "note", None)),
    )


def _decision_view(decision: object) -> DecisionView:
    orders = tuple(_field(decision, "orders", ()))
    if not all(isinstance(item, ProposedOrder) for item in orders):
        raise TypeError("decision orders contain an invalid value")
    signals = tuple(_field(decision, "signals", ()))
    expiry_values = [item.valid_until for item in orders]
    expiry_values.extend(
        _field(item, "valid_until")
        for item in signals
        if _field(item, "valid_until", None) is not None
    )
    generated_at = _datetime(_field(decision, "generated_at"), "generated_at")
    valid_until = (
        min(_datetime(value, "valid_until") for value in expiry_values)
        if expiry_values
        else generated_at
    )
    return DecisionView(
        run_id=_nonempty_text(_field(decision, "run_id"), "run_id"),
        as_of_date=_date(_field(decision, "as_of_date"), "as_of_date"),
        generated_at=generated_at,
        valid_until=valid_until,
        status=_enum_value(DataStatus, _field(decision, "status"), "data status"),
        readiness=_enum_value(
            ReadinessStatus,
            _field(decision, "readiness"),
            "readiness",
        ),
        market_regime=_enum_value(
            MarketRegime,
            _field(decision, "market_regime"),
            "market regime",
        ),
        orders=orders,
        blockers=_text_tuple(_field(decision, "blockers", ())),
        manual_confirmation_required=True,
    )


def _candidate_views(
    run_id: str,
    frame: object,
    *,
    source_kind: str,
    expected_asset_type: AssetType,
) -> tuple[
    tuple[CandidateView, ...],
    tuple[AnalysisCandidate, ...],
    tuple[Evidence, ...],
]:
    views: list[CandidateView] = []
    analysis: list[AnalysisCandidate] = []
    evidence: list[Evidence] = []
    for rank, row in enumerate(_records(frame), start=1):
        symbol = _nonempty_text(_field(row, "symbol"), "candidate symbol")
        asset_type = _enum_value(
            AssetType,
            _field(row, "asset_type"),
            "candidate asset_type",
        )
        if asset_type is not expected_asset_type:
            raise ValueError("candidate_asset_type_mismatch")
        action = _enum_value(Action, _field(row, "action"), "candidate action")
        score = _finite_float(_field(row, "score"), "candidate score")
        reasons = _candidate_reasons(row)
        identifiers = tuple(
            _evidence_id(run_id, source_kind, symbol, index) for index, _ in enumerate(reasons)
        )
        if not identifiers:
            raise ValueError("candidate_missing_evidence")
        views.append(
            CandidateView(
                symbol,
                asset_type.value,
                action,
                score,
                rank,
                reasons,
                identifiers,
                str(row["name"])
                if isinstance(row.get("name"), str) and row["name"].strip()
                else None,
            )
        )
        analysis.append(
            AnalysisCandidate(
                symbol=symbol,
                asset_type=asset_type.value,
                action=action,
                score=score,
                rank=rank,
                evidence_ids=identifiers,
            )
        )
        evidence.extend(
            Evidence(identifier, reason)
            for identifier, reason in zip(identifiers, reasons, strict=True)
        )
    from .helpers import deduplicate_evidence

    return tuple(views), tuple(analysis), deduplicate_evidence(evidence)


def _candidate_reasons(row: object) -> tuple[str, ...]:
    value = _field(row, "reasons", _MISSING)
    if value is _MISSING:
        value = _field(row, "reason", ())
    return _text_tuple(value)


def _positions(value: object) -> tuple[PortfolioPositionView, ...]:
    positions: list[PortfolioPositionView] = []
    for row in _records(value):
        symbol = _nonempty_text(_field(row, "symbol"), "position symbol")
        positions.append(
            PortfolioPositionView(
                symbol=symbol,
                name=_nonempty_text(_field(row, "name", symbol), "position name"),
                asset_type=_enum_value(
                    AssetType,
                    _field(row, "asset_type"),
                    "position asset_type",
                ),
                quantity=_decimal(_field(row, "quantity"), "position quantity"),
                average_cost=_decimal(
                    _field(row, "average_cost"),
                    "position average_cost",
                ),
                market_value=_decimal(
                    _field(row, "market_value"),
                    "position market_value",
                ),
                as_of_date=_date(
                    _field(row, "as_of_date"),
                    "position as_of_date",
                ),
            )
        )
    return tuple(sorted(positions, key=lambda item: item.symbol))
