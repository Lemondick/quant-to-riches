"""Immutable read views and evidence-bound context for the local API.

The 52 frozen dataclasses and the ``ApiQueries`` Protocol live here so they
can be referenced by both the local mixin implementations and the analysis
helpers without dragging the 1,200+ line ``LocalApiQueries`` body around.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from quant_assistant.analysis.models import (
    AnalysisRequest,
    PortfolioBucket,
)
from quant_assistant.funds.fees import FundFeeSchedule, FundRedemptionLot
from quant_assistant.models.entities import (
    Action,
    AssetType,
    DataStatus,
    MarketRegime,
    ProposedOrder,
    ReadinessStatus,
)
from quant_assistant.providers.data_quality import ObservationMetadata
from quant_assistant.providers.fund_detail import FundDetail
from quant_assistant.providers.market_feed import MarketNewsFeed, MarketQuoteList, MarketSnapshot


class DecisionRunNotFoundError(LookupError):
    """Raised when an analysis request references an unknown decision run."""

    error_code = "decision_run_not_found"


@dataclass(frozen=True, slots=True)
class HealthView:
    status: str
    version: str
    database: str


@dataclass(frozen=True, slots=True)
class DataStatusView:
    as_of_date: date | None
    status: DataStatus
    snapshot_id: int | None
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecisionView:
    run_id: str
    as_of_date: date
    generated_at: datetime
    valid_until: datetime
    status: DataStatus
    readiness: ReadinessStatus
    market_regime: MarketRegime
    orders: tuple[ProposedOrder, ...]
    blockers: tuple[str, ...]
    manual_confirmation_required: bool = True


@dataclass(frozen=True, slots=True)
class PortfolioView:
    as_of_date: date | None
    positions: tuple[PortfolioPositionView, ...]
    bucket_weights: tuple[PortfolioBucket, ...]


@dataclass(frozen=True, slots=True)
class PortfolioPositionView:
    symbol: str
    name: str
    asset_type: AssetType
    quantity: Decimal
    average_cost: Decimal
    market_value: Decimal
    as_of_date: date


@dataclass(frozen=True, slots=True)
class PortfolioHistoryPointView:
    run_id: str
    as_of_date: date
    generated_at: datetime
    total_value: Decimal
    cash: Decimal
    unit_nav: Decimal
    high_water_mark: Decimal
    net_external_flow: Decimal
    is_baseline: bool


@dataclass(frozen=True, slots=True)
class PortfolioHistoryView:
    points: tuple[PortfolioHistoryPointView, ...]
    currency: str = "CNY"


@dataclass(frozen=True, slots=True)
class FundValuationView(ObservationMetadata):
    symbol: str
    estimate_nav: Decimal | None
    previous_nav: Decimal
    change_rate: Decimal | None
    change_amount: Decimal | None
    observed_at: datetime | None
    source: str
    nav_date: str | None
    valuation_type: str
    official_nav: Decimal | None = None
    official_change_rate: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FundValuationsView:
    items: tuple[FundValuationView, ...]
    unavailable: tuple[str, ...]
    estimate_only: bool = True
    disclaimer: str = "盘中估值或最新官方净值仅供参考，最终以基金官方公告为准。"


@dataclass(frozen=True, slots=True)
class FundValuationAlertView:
    """An open fund valuation alert and its persisted notification state."""

    id: int
    symbol: str
    severity: str
    message: str
    change_rate: Decimal | None
    estimate_nav: Decimal | None
    previous_nav: Decimal | None
    observed_at: datetime | None
    source: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_notified_at: datetime | None
    silenced_until: datetime | None
    details: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class TradeLedgerInputView:
    entry_type: str
    symbol: str
    name: str
    asset_type: str
    quantity: Decimal
    price: Decimal
    fees: Decimal
    occurred_at: datetime
    external_id: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DividendLedgerInputView:
    symbol: str
    asset_type: str
    gross_amount: Decimal
    fees: Decimal
    occurred_at: datetime
    external_id: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class CorporateActionInputView:
    action_type: str
    symbol: str
    asset_type: str
    effective_at: datetime
    external_id: str
    target_symbol: str | None = None
    quantity: Decimal | None = None
    target_quantity: Decimal | None = None
    price: Decimal | None = None
    target_price: Decimal | None = None
    gross_amount: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    ratio: Decimal | None = None
    cash_symbol: str | None = None
    from_account: str | None = None
    to_account: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerCorrectionInputView:
    target_type: str
    target_id: int
    reason: str
    effective_at: datetime
    external_id: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerEntryView:
    id: int
    entry_type: str
    symbol: str
    name: str
    asset_type: str
    quantity: Decimal | None
    price: Decimal | None
    gross_amount: Decimal
    fees: Decimal
    net_amount: Decimal
    occurred_at: datetime
    external_id: str
    note: str | None
    reversed: bool = False


@dataclass(frozen=True, slots=True)
class CorporateActionView:
    id: int
    action_type: str
    symbol: str
    target_symbol: str | None
    asset_type: str
    quantity: Decimal | None
    target_quantity: Decimal | None
    price: Decimal | None
    target_price: Decimal | None
    gross_amount: Decimal
    fees: Decimal
    cash_delta: Decimal
    ratio: Decimal | None
    effective_at: datetime
    external_id: str
    source: str
    from_account: str | None
    to_account: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class LedgerCorrectionView:
    id: int
    target_type: str
    target_id: int
    reason: str
    effective_at: datetime
    external_id: str
    source: str
    note: str | None


@dataclass(frozen=True, slots=True)
class LedgerView:
    entries: tuple[LedgerEntryView, ...]
    total_fees: Decimal
    total_dividends: Decimal


@dataclass(frozen=True, slots=True)
class ProfitAttributionItemView:
    symbol: str
    name: str
    asset_type: str
    realized_trading_profit: Decimal | None
    dividend_income: Decimal
    unrealized_profit: Decimal | None
    fees: Decimal
    total_profit: Decimal | None


@dataclass(frozen=True, slots=True)
class ProfitAttributionView:
    items: tuple[ProfitAttributionItemView, ...]
    realized_trading_profit: Decimal | None
    dividend_income: Decimal
    unrealized_profit: Decimal
    fees: Decimal
    total_profit: Decimal | None


@dataclass(frozen=True, slots=True)
class PreferenceWatchlistItemView:
    symbol: str
    name: str
    asset_type: str


@dataclass(frozen=True, slots=True)
class UserPreferencesView:
    watchlist: tuple[PreferenceWatchlistItemView, ...]
    read_news_ids: tuple[str, ...]
    revision: int
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class CandidateView:
    symbol: str
    asset_type: str
    action: Action
    score: float
    rank: int
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateListView:
    run_id: str
    items: tuple[CandidateView, ...]


@dataclass(frozen=True, slots=True)
class BacktestView:
    run_id: str
    completed_at: datetime
    status: str
    total_return: float
    max_drawdown: float
    research_only_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DrawdownValidationView:
    run_id: str
    threshold: float
    max_drawdown: float
    observed_sessions: int
    breach_count: int
    passed: bool
    reasons: tuple[str, ...]
    research_only: bool


@dataclass(frozen=True, slots=True)
class CashFlowPlanView:
    plan_id: str
    name: str
    direction: str
    amount: Decimal
    frequency: str
    start_date: date
    end_date: date | None
    status: str
    note: str | None
    next_dates: tuple[date, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class FactorObservationView:
    factor: str
    as_of_date: date
    ic: float | None
    ir: float | None
    sample_count: int
    source: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FactorMonitorView:
    factor: str
    observations: tuple[FactorObservationView, ...]
    mean_ic: float | None
    mean_ir: float | None
    ic_slope: float | None
    ir_slope: float | None
    decay: bool
    alerts: tuple[Mapping[str, object], ...]
    research_only: bool


@dataclass(frozen=True, slots=True)
class BenchmarkConfigView:
    benchmark_id: str
    name: str
    components: Mapping[str, float]
    is_default: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MacroObservationView:
    indicator: str
    observed_on: date
    value: float
    unit: str
    source: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MacroContextView:
    as_of_date: date
    status: str
    missing: tuple[str, ...]
    max_age_days: int | None
    observations: tuple[MacroObservationView, ...]
    research_only: bool


@dataclass(frozen=True, slots=True)
class WatchdogJobView:
    job_name: str
    expected_interval_minutes: int
    last_started_at: datetime | None
    last_completed_at: datetime | None
    status: str
    last_error: str | None
    overdue: bool
    failed: bool
    healthy: bool


@dataclass(frozen=True, slots=True)
class OperationalTrendView:
    """One daily, persisted operations-health observation."""

    as_of_date: date
    decision_runs: int
    valid_decisions: int
    stale_decisions: int
    blocked_decisions: int
    decision_pass_rate: float | None
    snapshot_count: int
    valid_snapshots: int
    source_count: int
    snapshot_valid_rate: float | None
    latest_fetched_at: datetime | None
    fetch_lag_days: int | None


@dataclass(frozen=True, slots=True)
class SourceHealthTrendView:
    """One source's daily persisted snapshot-health observation."""

    as_of_date: date
    source: str
    snapshot_count: int
    valid_snapshots: int
    valid_snapshot_rate: float | None
    latest_fetched_at: datetime | None


@dataclass(frozen=True, slots=True)
class OperationalTrendsView:
    daily: tuple[OperationalTrendView, ...]
    sources: tuple[SourceHealthTrendView, ...]


@dataclass(frozen=True, slots=True)
class FundLifecycleEventView:
    """A persisted fund lifecycle alert suitable for API/mobile display."""

    id: int
    symbol: str
    observed_on: date
    event_type: str
    severity: str
    message: str
    previous_value: str | None
    current_value: str | None
    source: str
    details: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FundStyleDriftEventView:
    """A report-period holding overlap comparison for review only."""

    id: int
    symbol: str
    report_date: date
    previous_report_date: date
    severity: str
    overlap_ratio: Decimal
    message: str
    added_holdings: tuple[str, ...]
    removed_holdings: tuple[str, ...]
    drift_detected: bool
    source: str
    details: Mapping[str, object]
    created_at: datetime


@runtime_checkable
class ApiQueries(Protocol):
    def health(self) -> HealthView: ...

    def data_status(self) -> DataStatusView: ...

    def latest_decision(self) -> DecisionView | None: ...

    def portfolio(self) -> PortfolioView: ...

    def portfolio_history(self, limit: int) -> PortfolioHistoryView: ...

    def operational_trends(self, limit: int = 180) -> OperationalTrendsView: ...

    def fund_lifecycle_events(
        self, symbol: str | None = None, limit: int = 100
    ) -> tuple[FundLifecycleEventView, ...]: ...

    def valuation_alerts(
        self, symbol: str | None = None, limit: int = 100
    ) -> tuple[FundValuationAlertView, ...]: ...

    def fund_style_drift_events(
        self, symbol: str | None = None, limit: int = 100
    ) -> tuple[FundStyleDriftEventView, ...]: ...

    def fund_valuations(self, symbols: Sequence[str]) -> FundValuationsView: ...

    def fund_detail(self, symbol: str) -> FundDetail: ...

    def fund_fee_evidence(self, symbol: str) -> dict: ...

    def save_fund_fee_evidence(self, schedule: FundFeeSchedule) -> dict: ...

    def fund_fee_quote(
        self,
        symbol: str,
        *,
        amount: Decimal,
        holding_days: int,
        confirmed_on: date | None = None,
        nav_includes_service_fee: bool = False,
    ) -> dict: ...

    def fund_redemption_quote(
        self, symbol: str, lots: tuple[FundRedemptionLot, ...], confirmed_on: date
    ) -> dict: ...

    def ledger(self, limit: int) -> LedgerView: ...

    def profit_attribution(self) -> ProfitAttributionView: ...

    def record_ledger_trade(self, item: TradeLedgerInputView) -> LedgerEntryView: ...

    def record_ledger_dividend(self, item: DividendLedgerInputView) -> LedgerEntryView: ...

    def corporate_actions(self, limit: int = 100) -> tuple[CorporateActionView, ...]: ...

    def record_corporate_action(self, item: CorporateActionInputView) -> CorporateActionView: ...

    def ledger_corrections(self, limit: int = 100) -> tuple[LedgerCorrectionView, ...]: ...

    def correct_ledger_entry(self, item: LedgerCorrectionInputView) -> LedgerCorrectionView: ...

    def market_snapshot(self, section: str | None = None) -> MarketSnapshot: ...

    def market_quotes(
        self,
        stock_symbols: Sequence[str],
        index_symbols: Sequence[str],
    ) -> MarketQuoteList: ...

    def market_news(self, limit: int) -> MarketNewsFeed: ...

    def user_preferences(self) -> UserPreferencesView: ...

    def replace_watchlist(
        self, items: Sequence[PreferenceWatchlistItemView]
    ) -> UserPreferencesView: ...

    def mark_news_read(self, ids: Sequence[str]) -> UserPreferencesView: ...

    def stock_candidates(self, run_id: str) -> CandidateListView: ...

    def fund_candidates(self, run_id: str) -> CandidateListView: ...

    def latest_backtest(self) -> BacktestView | None: ...

    def drawdown_validation(self, run_id: str | None = None) -> DrawdownValidationView | None: ...

    def validate_drawdown(
        self, run_id: str, nav: object, threshold: float = 0.20
    ) -> DrawdownValidationView: ...

    def cash_flow_plans(
        self, status: str | None = None, limit: int = 100
    ) -> tuple[CashFlowPlanView, ...]: ...

    def create_cash_flow_plan(self, payload: Mapping[str, object]) -> CashFlowPlanView: ...

    def cash_flow_due(self, as_of: date) -> tuple[Mapping[str, object], ...]: ...

    def factor_monitor(self, factor: str, limit: int = 60) -> FactorMonitorView: ...

    def record_factor_observation(self, payload: Mapping[str, object]) -> FactorObservationView: ...

    def factor_alerts(
        self, factor: str | None = None, limit: int = 100
    ) -> tuple[Mapping[str, object], ...]: ...

    def benchmarks(self, limit: int = 100) -> tuple[BenchmarkConfigView, ...]: ...

    def create_benchmark(self, payload: Mapping[str, object]) -> BenchmarkConfigView: ...

    def compare_benchmark(
        self, benchmark_id: str, start_date: date | None = None, end_date: date | None = None
    ) -> Mapping[str, object]: ...

    def macro_context(self, as_of: date | None = None) -> MacroContextView: ...

    def macro_observations(
        self, indicator: str | None = None, limit: int = 100
    ) -> tuple[MacroObservationView, ...]: ...

    def record_macro_observation(self, payload: Mapping[str, object]) -> MacroObservationView: ...

    def watchdog_scan(self) -> tuple[WatchdogJobView, ...]: ...

    def record_watchdog_heartbeat(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def analysis_request(self, run_id: str, user_text: str | None) -> AnalysisRequest: ...
