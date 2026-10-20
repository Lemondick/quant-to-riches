"""Stable Pydantic request and response contracts for the local API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from quant_assistant.models.entities import (
    Action,
    AssetType,
    DataStatus,
    MarketRegime,
    ReadinessStatus,
)

from .candidate_schemas import AssetTypeValue as AssetTypeValue
from .candidate_schemas import CandidateListResponse as CandidateListResponse
from .candidate_schemas import CandidateResponse as CandidateResponse
from .fund_schemas import FundAnnouncementResponse as FundAnnouncementResponse
from .fund_schemas import FundDetailResponse as FundDetailResponse
from .fund_schemas import FundFeeEvidenceBody as FundFeeEvidenceBody
from .fund_schemas import FundFeeEvidenceResponse as FundFeeEvidenceResponse
from .fund_schemas import FundFeeQuoteBody as FundFeeQuoteBody
from .fund_schemas import FundFeeQuoteResponse as FundFeeQuoteResponse
from .fund_schemas import FundFeeResponse as FundFeeResponse
from .fund_schemas import FundHoldingResponse as FundHoldingResponse
from .fund_schemas import FundLifecycleEventResponse as FundLifecycleEventResponse
from .fund_schemas import FundLifecycleEventsResponse as FundLifecycleEventsResponse
from .fund_schemas import FundManagerResponse as FundManagerResponse
from .fund_schemas import FundNavPointResponse as FundNavPointResponse
from .fund_schemas import FundPeriodReturnResponse as FundPeriodReturnResponse
from .fund_schemas import FundPortfolioNavBody as FundPortfolioNavBody
from .fund_schemas import FundPortfolioNavResponse as FundPortfolioNavResponse
from .fund_schemas import FundPortfolioNavSourceResponse as FundPortfolioNavSourceResponse
from .fund_schemas import FundPurchaseTierBody as FundPurchaseTierBody
from .fund_schemas import FundRedemptionLotBody as FundRedemptionLotBody
from .fund_schemas import FundRedemptionQuoteBody as FundRedemptionQuoteBody
from .fund_schemas import FundRedemptionQuoteResponse as FundRedemptionQuoteResponse
from .fund_schemas import FundRedemptionTierBody as FundRedemptionTierBody
from .fund_schemas import FundRuleFieldResponse as FundRuleFieldResponse
from .fund_schemas import FundServiceRefundBody as FundServiceRefundBody
from .fund_schemas import FundSourceHealthResponse as FundSourceHealthResponse
from .fund_schemas import FundStyleDriftEventResponse as FundStyleDriftEventResponse
from .fund_schemas import FundStyleDriftEventsResponse as FundStyleDriftEventsResponse
from .fund_schemas import FundTradingRulesResponse as FundTradingRulesResponse
from .fund_schemas import FundValuationAlertResponse as FundValuationAlertResponse
from .fund_schemas import FundValuationAlertsResponse as FundValuationAlertsResponse
from .fund_schemas import FundValuationResponse as FundValuationResponse
from .fund_schemas import FundValuationsResponse as FundValuationsResponse
from .fund_schemas import OperationalTrendResponse as OperationalTrendResponse
from .fund_schemas import OperationalTrendsResponse as OperationalTrendsResponse
from .fund_schemas import PortfolioHistoryPointResponse as PortfolioHistoryPointResponse
from .fund_schemas import SourceHealthTrendResponse as SourceHealthTrendResponse
from .schema_common import ApiModel as ApiModel
from .schema_common import DecimalString as DecimalString
from .schema_common import NavObservationModel, ObservationModel

ProviderStatusValue = Literal[
    "disabled",
    "ok",
    "timeout",
    "rate_limited",
    "network_error",
    "invalid_output",
    "rejected_conflict",
]
PreferenceAssetTypeValue = Literal["stock", "fund", "index"]


class ErrorResponse(ApiModel):
    error_code: str
    message: str
    request_id: str


class AnalysisExplainBody(ApiModel):
    decision_run_id: str = Field(min_length=1, max_length=128)
    user_text: str | None = Field(default=None, max_length=2000)


class HealthResponse(ApiModel):
    status: str
    version: str
    database: str


class DataStatusResponse(ApiModel):
    as_of_date: date | None
    status: DataStatus
    snapshot_id: int | None
    blockers: tuple[str, ...]


class ProposedOrderResponse(ApiModel):
    symbol: str
    asset_type: AssetType
    action: Action
    amount: DecimalString
    quantity: DecimalString | None
    target_weight: DecimalString
    reasons: tuple[str, ...]
    as_of_date: date
    valid_until: datetime
    data_snapshot_id: int
    strategy_version: str


class DecisionResponse(ApiModel):
    run_id: str
    as_of_date: date
    generated_at: datetime
    valid_until: datetime
    status: DataStatus
    readiness: ReadinessStatus
    market_regime: MarketRegime
    orders: tuple[ProposedOrderResponse, ...]
    blockers: tuple[str, ...]
    manual_confirmation_required: bool


class PositionResponse(ApiModel):
    symbol: str
    name: str | None = None
    asset_type: AssetType
    quantity: DecimalString
    average_cost: DecimalString
    market_value: DecimalString
    as_of_date: date


class PortfolioBucketResponse(ApiModel):
    name: str
    weight: DecimalString


class PortfolioResponse(ApiModel):
    as_of_date: date | None
    positions: tuple[PositionResponse, ...]
    bucket_weights: tuple[PortfolioBucketResponse, ...]


class PortfolioHistoryResponse(ApiModel):
    points: tuple[PortfolioHistoryPointResponse, ...]
    currency: Literal["CNY"]


class LedgerTradeBody(ApiModel):
    entry_type: Literal["buy", "sell", "dca"]
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=80)
    asset_type: Literal["stock", "fund"]
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fees: Decimal = Field(ge=0)
    occurred_at: datetime
    external_id: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class LedgerDividendBody(ApiModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    asset_type: Literal["stock", "fund"]
    gross_amount: Decimal = Field(gt=0)
    fees: Decimal = Field(ge=0)
    occurred_at: datetime
    external_id: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


CorporateActionType = Literal[
    "dividend_reinvestment",
    "fund_conversion",
    "split",
    "custody_transfer",
]


class CorporateActionBody(ApiModel):
    action_type: CorporateActionType
    symbol: str = Field(min_length=1, max_length=32)
    asset_type: Literal["stock", "fund"]
    effective_at: datetime
    external_id: str = Field(min_length=1, max_length=128)
    target_symbol: str | None = Field(default=None, min_length=1, max_length=32)
    quantity: Decimal | None = Field(default=None, gt=0)
    target_quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, gt=0)
    target_price: Decimal | None = Field(default=None, gt=0)
    gross_amount: Decimal = Field(default=Decimal("0"), ge=0)
    fees: Decimal = Field(default=Decimal("0"), ge=0)
    ratio: Decimal | None = Field(default=None, gt=0)
    cash_symbol: str | None = Field(default=None, min_length=1, max_length=32)
    from_account: str | None = Field(default=None, min_length=1, max_length=128)
    to_account: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class LedgerCorrectionBody(ApiModel):
    target_type: Literal["transaction", "dividend"]
    target_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)
    effective_at: datetime
    external_id: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class LedgerEntryResponse(ApiModel):
    id: int
    entry_type: Literal["buy", "sell", "dca", "dividend"]
    symbol: str
    name: str
    asset_type: Literal["stock", "fund"]
    quantity: DecimalString | None
    price: DecimalString | None
    gross_amount: DecimalString
    fees: DecimalString
    net_amount: DecimalString
    occurred_at: datetime
    external_id: str
    note: str | None
    reversed: bool = False


class LedgerResponse(ApiModel):
    entries: tuple[LedgerEntryResponse, ...]
    total_fees: DecimalString
    total_dividends: DecimalString


class CorporateActionResponse(ApiModel):
    id: int
    action_type: CorporateActionType
    symbol: str
    target_symbol: str | None
    asset_type: Literal["stock", "fund"]
    quantity: DecimalString | None
    target_quantity: DecimalString | None
    price: DecimalString | None
    target_price: DecimalString | None
    gross_amount: DecimalString
    fees: DecimalString
    cash_delta: DecimalString
    ratio: DecimalString | None
    effective_at: datetime
    external_id: str
    source: str
    from_account: str | None
    to_account: str | None
    note: str | None


class CorporateActionListResponse(ApiModel):
    items: tuple[CorporateActionResponse, ...]


class LedgerCorrectionResponse(ApiModel):
    id: int
    target_type: Literal["transaction", "dividend"]
    target_id: int
    reason: str
    effective_at: datetime
    external_id: str
    source: str
    note: str | None


class LedgerCorrectionListResponse(ApiModel):
    items: tuple[LedgerCorrectionResponse, ...]


class ProfitAttributionItemResponse(ApiModel):
    symbol: str
    name: str
    asset_type: Literal["stock", "fund"]
    realized_trading_profit: DecimalString | None
    dividend_income: DecimalString
    unrealized_profit: DecimalString | None
    fees: DecimalString
    total_profit: DecimalString | None


class ProfitAttributionResponse(ApiModel):
    items: tuple[ProfitAttributionItemResponse, ...]
    realized_trading_profit: DecimalString | None
    dividend_income: DecimalString
    unrealized_profit: DecimalString
    fees: DecimalString
    total_profit: DecimalString | None


class PreferenceWatchlistItem(ApiModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=80)
    asset_type: PreferenceAssetTypeValue


class UserPreferencesResponse(ApiModel):
    watchlist: tuple[PreferenceWatchlistItem, ...]
    read_news_ids: tuple[str, ...]
    revision: int = Field(ge=0)
    updated_at: datetime | None


class WatchlistReplaceBody(ApiModel):
    items: tuple[PreferenceWatchlistItem, ...] = Field(max_length=100)


class NewsReadBody(ApiModel):
    ids: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(
        min_length=1,
        max_length=200,
    )


class MarketQuoteResponse(ObservationModel):
    symbol: str
    name: str
    asset_type: Literal["stock", "index"]
    price: DecimalString
    change: DecimalString
    change_rate: DecimalString
    observed_at: datetime
    source: str
    trade_time_verified: bool = False
    volume: DecimalString | None = None
    turnover: DecimalString | None = None
    volume_unit: str | None = None
    turnover_unit: str | None = None


class MarketFundRankingResponse(NavObservationModel):
    symbol: str
    name: str
    unit_nav: DecimalString
    change_rate: DecimalString
    one_month_return: DecimalString | None
    nav_date: str | None


class MarketSectorRankingResponse(ObservationModel):
    standard_id: str | None = None
    name: str
    code: str
    change_rate: DecimalString | None
    leader: str | None
    category: str = "industry"
    source: str = "unknown"
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    turnover: DecimalString | None = None
    advancing: int | None = None
    declining: int | None = None
    members: int | None = None


class MarketSectorCatalogResponse(ObservationModel):
    listed_total: int | None = None
    unquoted: int = 0
    category: str
    source: str
    total: int | None
    received: int
    complete: bool | None = None
    fetched_at: datetime | None
    observed_at: datetime | None = None
    stale: bool = False
    error: str | None = None
    coverage_version: int = 0
    catalog_fetched_at: datetime | None = None
    catalog_http_ok: bool | None = None
    catalog_parse_ok: bool | None = None
    catalog_cache_used: bool = False
    catalog_stale: bool = False
    catalog_error: str | None = None
    catalog_status: str = "unknown"
    quote_total: int | None = None
    quote_payload_complete: bool | None = None
    quotes_complete: bool | None = None
    http_ok: bool | None = None
    parse_ok: bool | None = None
    source_time_known: bool = False
    cache_used: bool | None = None
    listed_received: int | None = None


class MarketBreadthResponse(ObservationModel):
    advancing: int
    declining: int
    unchanged: int
    limit_up: int
    limit_down: int
    observed_at: datetime
    source: str
    turnover: DecimalString | None = None
    previous_turnover: DecimalString | None = None
    turnover_ratio: DecimalString | None = None
    previous_trade_date: str | None = None
    stale: bool = False
    unavailable: tuple[str, ...] = ()


class MarketSnapshotResponse(ApiModel):
    indices: tuple[MarketQuoteResponse, ...]
    fund_rankings: tuple[MarketFundRankingResponse, ...]
    sector_rankings: tuple[MarketSectorRankingResponse, ...]
    observed_at: datetime
    source: str
    stale: bool
    unavailable: tuple[str, ...]
    breadth: MarketBreadthResponse | None = None
    sector_catalogs: tuple[MarketSectorCatalogResponse, ...] = ()
    stale_sections: tuple[str, ...] = ()


class MarketQuoteListResponse(ApiModel):
    items: tuple[MarketQuoteResponse, ...]
    unavailable: tuple[str, ...]
    observed_at: datetime
    source: str
    stale: bool


class MarketNewsItemResponse(ApiModel):
    id: str
    title: str
    summary: str
    category: str
    published_at: datetime
    source: str
    url: str
    important: bool
    sentiment: Literal["positive", "negative", "neutral"] = "neutral"
    sentiment_score: float = 0.0
    sentiment_keywords: tuple[str, ...] = ()


class MarketNewsFeedResponse(ApiModel):
    items: tuple[MarketNewsItemResponse, ...]
    observed_at: datetime
    source: str
    stale: bool
    unavailable: bool


class BacktestResponse(ApiModel):
    run_id: str
    completed_at: datetime
    status: str
    total_return: float
    max_drawdown: float
    research_only_reasons: tuple[str, ...]


class DrawdownNavPoint(ApiModel):
    as_of_date: date | None = None
    unit_nav: float | None = None
    total_value: float | None = None


class DrawdownValidationResponse(ApiModel):
    run_id: str
    threshold: float
    max_drawdown: float
    observed_sessions: int
    breach_count: int
    passed: bool
    reasons: tuple[str, ...]
    research_only: bool


class DrawdownValidationBody(ApiModel):
    run_id: str = Field(min_length=1, max_length=128)
    nav: tuple[DrawdownNavPoint, ...] = Field(min_length=2, max_length=10000)
    threshold: float = Field(default=0.2, gt=0, le=1)


class CashFlowPlanResponse(ApiModel):
    plan_id: str
    name: str
    direction: Literal["deposit", "withdrawal"]
    amount: DecimalString
    frequency: Literal["once", "weekly", "monthly", "quarterly", "annual"]
    start_date: date
    end_date: date | None
    status: Literal["active", "paused", "completed"]
    note: str | None
    next_dates: tuple[date, ...]
    created_at: datetime
    updated_at: datetime


class CashFlowPlanBody(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    direction: Literal["deposit", "withdrawal"]
    amount: Decimal = Field(gt=0)
    frequency: Literal["once", "weekly", "monthly", "quarterly", "annual"]
    start_date: date
    end_date: date | None = None
    note: str | None = Field(default=None, max_length=500)
    plan_id: str | None = Field(default=None, max_length=128)


class CashFlowPlanListResponse(ApiModel):
    items: tuple[CashFlowPlanResponse, ...]


class CashFlowDueResponse(ApiModel):
    items: tuple[dict[str, object], ...]


class FactorObservationResponse(ApiModel):
    factor: str
    as_of_date: date
    ic: float | None
    ir: float | None
    sample_count: int
    source: str
    created_at: datetime


class FactorObservationBody(ApiModel):
    factor: str = Field(min_length=1, max_length=80)
    as_of_date: date
    ic: float | None = None
    ir: float | None = None
    sample_count: int = Field(default=0, ge=0)
    source: str = Field(default="manual", min_length=1, max_length=200)


class FactorMonitorResponse(ApiModel):
    factor: str
    observations: tuple[FactorObservationResponse, ...]
    mean_ic: float | None
    mean_ir: float | None
    ic_slope: float | None
    ir_slope: float | None
    decay: bool
    alerts: tuple[dict[str, object], ...]
    research_only: bool


class FactorAlertsResponse(ApiModel):
    items: tuple[dict[str, object], ...]


class BenchmarkConfigResponse(ApiModel):
    benchmark_id: str
    name: str
    components: dict[str, float]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class BenchmarkConfigBody(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    components: dict[str, float] = Field(min_length=1, max_length=20)
    benchmark_id: str | None = Field(default=None, max_length=128)
    is_default: bool = False


class BenchmarkConfigListResponse(ApiModel):
    items: tuple[BenchmarkConfigResponse, ...]


class BenchmarkCompareResponse(ApiModel):
    benchmark_id: str
    name: str
    components: dict[str, float]
    observations: int
    return_: float | None = Field(alias="return", default=None)
    portfolio_return: float | None = None
    excess_return: float | None = None
    research_only: bool

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
        allow_inf_nan=False,
        populate_by_name=True,
    )


class MacroObservationResponse(ApiModel):
    indicator: str
    observed_on: date
    value: float
    unit: str
    source: str
    created_at: datetime


class MacroObservationBody(ApiModel):
    indicator: Literal["interest_rate", "pmi", "social_financing"]
    observed_on: date
    value: float
    unit: str = Field(min_length=1, max_length=30)
    source: str = Field(default="manual", min_length=1, max_length=200)


class MacroContextResponse(ApiModel):
    as_of_date: date
    status: Literal["valid", "stale", "blocked"]
    missing: tuple[str, ...]
    max_age_days: int | None
    observations: tuple[MacroObservationResponse, ...]
    research_only: bool


class MacroObservationsResponse(ApiModel):
    items: tuple[MacroObservationResponse, ...]


class WatchdogHeartbeatBody(ApiModel):
    job_name: str = Field(min_length=1, max_length=100)
    expected_interval_minutes: int = Field(default=1440, gt=0, le=525600)
    status: Literal["running", "completed", "failed", "blocked"] = "completed"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = Field(default=None, max_length=500)


class WatchdogJobResponse(ApiModel):
    job_name: str
    expected_interval_minutes: int
    last_started_at: datetime | None
    last_completed_at: datetime | None
    status: str
    last_error: str | None
    overdue: bool
    failed: bool
    healthy: bool


class WatchdogResponse(ApiModel):
    items: tuple[WatchdogJobResponse, ...]


class WatchdogHeartbeatResponse(ApiModel):
    job_name: str
    status: str
    recorded: bool


class AnalysisItemResponse(ApiModel):
    text: str
    evidence_ids: tuple[str, ...]


class AnalysisRiskResponse(ApiModel):
    severity: str
    text: str
    evidence_ids: tuple[str, ...]


class AnalysisResponseModel(ApiModel):
    provider_name: str
    provider_status: ProviderStatusValue
    generated_at: datetime
    decision_run_id: str
    summary: str
    opportunities: tuple[AnalysisItemResponse, ...]
    risks: tuple[AnalysisRiskResponse, ...]
    review_actions: tuple[str, ...]
    rejected_claims: tuple[str, ...]
    input_fingerprint: str
