"""Pydantic contracts for fund detail, monitoring and operational trends."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from .schema_common import ApiModel, DecimalString, ObservationModel


class FundPortfolioNavBody(ApiModel):
    as_of_date: date
    confirmed: bool = False
    preview_id: str | None = Field(default=None, min_length=64, max_length=64)


class FundPortfolioNavSourceResponse(ApiModel):
    symbol: str
    nav_date: date
    unit_nav: DecimalString
    source: Literal["eastmoney", "tencent"]
    source_url: str
    observed_at: datetime


class PortfolioHistoryPointResponse(ApiModel):
    run_id: str
    as_of_date: date
    generated_at: datetime
    total_value: DecimalString
    cash: DecimalString
    unit_nav: DecimalString
    high_water_mark: DecimalString
    net_external_flow: DecimalString
    is_baseline: bool


class FundPortfolioNavResponse(ApiModel):
    status: Literal["blocked", "ready", "recorded", "existing"]
    as_of_date: date
    preview_id: str | None
    point: PortfolioHistoryPointResponse | None
    blockers: tuple[str, ...]
    sources: tuple[FundPortfolioNavSourceResponse, ...]


class OperationalTrendResponse(ApiModel):
    as_of_date: date
    decision_runs: int = Field(ge=0)
    valid_decisions: int = Field(ge=0)
    stale_decisions: int = Field(ge=0)
    blocked_decisions: int = Field(ge=0)
    decision_pass_rate: float | None
    snapshot_count: int = Field(ge=0)
    valid_snapshots: int = Field(ge=0)
    source_count: int = Field(ge=0)
    snapshot_valid_rate: float | None
    latest_fetched_at: datetime | None
    fetch_lag_days: int | None = Field(default=None, ge=0)


class SourceHealthTrendResponse(ApiModel):
    as_of_date: date
    source: str
    snapshot_count: int = Field(ge=0)
    valid_snapshots: int = Field(ge=0)
    valid_snapshot_rate: float | None
    latest_fetched_at: datetime | None


class OperationalTrendsResponse(ApiModel):
    daily: tuple[OperationalTrendResponse, ...]
    sources: tuple[SourceHealthTrendResponse, ...]


FundLifecycleEventType = Literal[
    "manager_change",
    "scale_drop",
    "liquidation_risk",
    "purchase_suspension",
    "redemption_suspension",
    "fee_change",
]
LifecycleSeverity = Literal["info", "warning", "critical"]


class FundLifecycleEventResponse(ApiModel):
    id: int = Field(ge=1)
    symbol: str = Field(pattern=r"^\d{6}$")
    observed_on: date
    event_type: FundLifecycleEventType
    severity: LifecycleSeverity
    message: str
    previous_value: str | None
    current_value: str | None
    source: str
    details: dict[str, object]
    created_at: datetime


class FundLifecycleEventsResponse(ApiModel):
    items: tuple[FundLifecycleEventResponse, ...]


StyleDriftSeverity = Literal["info", "warning", "critical"]


class FundStyleDriftEventResponse(ApiModel):
    id: int = Field(ge=1)
    symbol: str = Field(pattern=r"^\d{6}$")
    report_date: date
    previous_report_date: date
    severity: StyleDriftSeverity
    overlap_ratio: DecimalString = Field(ge=0, le=1)
    message: str
    added_holdings: tuple[str, ...]
    removed_holdings: tuple[str, ...]
    drift_detected: bool
    source: str
    details: dict[str, object]
    created_at: datetime


class FundStyleDriftEventsResponse(ApiModel):
    items: tuple[FundStyleDriftEventResponse, ...]


class FundValuationResponse(ObservationModel):
    symbol: str
    estimate_nav: DecimalString | None
    previous_nav: DecimalString
    change_rate: DecimalString | None
    change_amount: DecimalString | None
    observed_at: datetime | None
    source: str
    nav_date: str | None
    valuation_type: Literal["intraday_estimate", "latest_nav"]
    official_nav: DecimalString | None = None
    official_change_rate: DecimalString | None = None


class FundValuationsResponse(ApiModel):
    items: tuple[FundValuationResponse, ...]
    unavailable: tuple[str, ...]
    estimate_only: bool
    disclaimer: str


ValuationAlertSeverity = Literal["info", "warning", "critical"]


class FundValuationAlertResponse(ApiModel):
    id: int = Field(ge=1)
    symbol: str = Field(pattern=r"^\d{6}$")
    severity: ValuationAlertSeverity
    message: str
    change_rate: DecimalString | None
    estimate_nav: DecimalString | None
    previous_nav: DecimalString | None
    observed_at: datetime | None
    source: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_notified_at: datetime | None
    silenced_until: datetime | None
    details: dict[str, object]


class FundValuationAlertsResponse(ApiModel):
    items: tuple[FundValuationAlertResponse, ...]


class FundNavPointResponse(ApiModel):
    nav_date: date
    unit_nav: DecimalString
    accumulated_nav: DecimalString | None
    daily_return: DecimalString | None
    source: str | None = None
    corporate_action: str | None = None
    return_verified: bool = False
    validation_errors: tuple[str, ...] = ()


class FundPeriodReturnResponse(ApiModel):
    period: str
    return_rate: DecimalString | None
    start_date: date | None = None
    end_date: date | None = None
    methodology: str = "unit_nav_ratio_without_corporate_actions"
    quality_status: str = "unavailable"
    reason: str | None = None


class FundHoldingResponse(ApiModel):
    symbol: str
    name: str
    asset_type: Literal["stock", "bond"]
    weight: DecimalString
    report_date: date | None


class FundManagerResponse(ApiModel):
    name: str
    work_years: DecimalString | None
    tenure_start: date | None
    tenure_return: DecimalString | None
    resume: str | None


class FundFeeResponse(ApiModel):
    fee_type: str
    condition: str
    rate: DecimalString | None
    fixed_amount: DecimalString | None = None
    discounted_rate: DecimalString | None = None
    discount_channel: str | None = None
    source: str = "danjuan"
    source_url: str | None = None


class FundRuleFieldResponse(ApiModel):
    label: str
    value: str


class FundTradingRulesResponse(ApiModel):
    fields: tuple[FundRuleFieldResponse, ...]
    source: str
    source_url: str
    arrival_description: str | None = None


class FundPurchaseTierBody(ApiModel):
    min_amount: Decimal = Field(ge=0)
    max_amount: Decimal | None = Field(default=None, gt=0)
    rate: Decimal | None = Field(default=None, ge=0, le=1)
    fixed_amount: Decimal | None = Field(default=None, ge=0)


class FundRedemptionTierBody(ApiModel):
    min_days: int = Field(ge=0)
    max_days: int | None = Field(default=None, ge=0)
    rate: Decimal = Field(ge=0, le=1)


class FundServiceRefundBody(ApiModel):
    effective_from: date
    after_holding_days: int = Field(ge=0, le=36600)
    evidence_reference: str = Field(min_length=1, max_length=1000)


class FundFeeEvidenceBody(ApiModel):
    share_class: Literal["A", "C"]
    purchase_rate: Decimal = Field(ge=0, le=1)
    annual_service_rate: Decimal = Field(ge=0, le=1)
    redemption_tiers: tuple[FundRedemptionTierBody, ...] = Field(min_length=1, max_length=30)
    purchase_tiers: tuple[FundPurchaseTierBody, ...] = Field(default=(), max_length=30)
    verified_on: date
    effective_from: date
    effective_to: date | None = None
    channel: Literal["alipay"] = "alipay"
    evidence_reference: str = Field(min_length=1, max_length=1000)
    service_refund_rule: FundServiceRefundBody | None = None
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("人工核对确认必须明确为true")
        return value


class FundFeeEvidenceResponse(ApiModel):
    symbol: str
    items: tuple[dict[str, object], ...]


class FundFeeQuoteBody(ApiModel):
    amount: Decimal = Field(gt=0)
    holding_days: int = Field(ge=0, le=36600)
    confirmed_on: date | None = None
    nav_includes_service_fee: bool = False


class FundFeeQuoteResponse(ApiModel):
    symbol: str
    purchase_fee: DecimalString
    redemption_fee: DecimalString
    service_fee_estimate: DecimalString
    expected_service_refund: DecimalString
    net_cost_estimate: DecimalString
    cash_credit: DecimalString
    estimated_on: date
    basis: str
    evidence_reference: str
    research_only: bool


class FundRedemptionLotBody(ApiModel):
    confirmed_on: date
    gross_amount: Decimal = Field(gt=0)


class FundRedemptionQuoteBody(ApiModel):
    confirmed_on: date
    lots: tuple[FundRedemptionLotBody, ...] = Field(min_length=1, max_length=100)


class FundRedemptionQuoteResponse(ApiModel):
    symbol: str
    items: tuple[dict[str, object], ...]
    total_fee: DecimalString
    cash_credit: DecimalString
    research_only: bool


class FundAnnouncementResponse(ApiModel):
    id: str
    title: str
    published_at: date
    url: str


class FundSourceHealthResponse(ApiModel):
    name: str
    available: bool
    consecutive_failures: int
    last_error: str | None
    last_success_at: datetime | None
    status: Literal["unchecked", "available", "unavailable"] = "unchecked"


class FundDetailResponse(ObservationModel):
    symbol: str
    name: str
    full_name: str | None
    fund_type: str | None
    company: str | None
    established_on: date | None
    latest_scale: str | None
    benchmark: str | None
    investment_target: str | None
    nav_history: tuple[FundNavPointResponse, ...]
    period_returns: tuple[FundPeriodReturnResponse, ...]
    holdings: tuple[FundHoldingResponse, ...]
    managers: tuple[FundManagerResponse, ...]
    fees: tuple[FundFeeResponse, ...]
    announcements: tuple[FundAnnouncementResponse, ...]
    holdings_report_date: date | None
    observed_at: datetime
    source: str
    stale: bool
    source_health: tuple[FundSourceHealthResponse, ...] = ()
    purchase_status: str | None = None
    redemption_status: str | None = None
    trading_rules: FundTradingRulesResponse | None = None
    status_source: str | None = None
    field_metadata: dict | None = None
    nav_metadata: dict | None = None
    conflicts: tuple[str, ...] = ()
