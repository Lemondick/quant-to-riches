"""Thin API endpoint handlers with explicit preference and ledger writes."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Query

from quant_assistant.controllers.photo_routes import photo_router
from quant_assistant.controllers.schemas import (
    AnalysisExplainBody,
    AnalysisResponseModel,
    BacktestResponse,
    BenchmarkCompareResponse,
    BenchmarkConfigBody,
    BenchmarkConfigListResponse,
    BenchmarkConfigResponse,
    CandidateListResponse,
    CashFlowDueResponse,
    CashFlowPlanBody,
    CashFlowPlanListResponse,
    CashFlowPlanResponse,
    CorporateActionBody,
    CorporateActionListResponse,
    CorporateActionResponse,
    DataStatusResponse,
    DecisionResponse,
    DrawdownValidationBody,
    DrawdownValidationResponse,
    FactorAlertsResponse,
    FactorMonitorResponse,
    FactorObservationBody,
    FactorObservationResponse,
    FundDetailResponse,
    FundFeeEvidenceBody,
    FundFeeEvidenceResponse,
    FundFeeQuoteBody,
    FundFeeQuoteResponse,
    FundLifecycleEventsResponse,
    FundPortfolioNavBody,
    FundPortfolioNavResponse,
    FundRedemptionQuoteBody,
    FundRedemptionQuoteResponse,
    FundStyleDriftEventsResponse,
    FundValuationAlertsResponse,
    FundValuationsResponse,
    HealthResponse,
    LedgerCorrectionBody,
    LedgerCorrectionListResponse,
    LedgerCorrectionResponse,
    LedgerDividendBody,
    LedgerEntryResponse,
    LedgerResponse,
    LedgerTradeBody,
    MacroContextResponse,
    MacroObservationBody,
    MacroObservationResponse,
    MacroObservationsResponse,
    MarketNewsFeedResponse,
    MarketQuoteListResponse,
    MarketSnapshotResponse,
    NewsReadBody,
    OperationalTrendsResponse,
    PortfolioHistoryResponse,
    PortfolioResponse,
    ProfitAttributionResponse,
    UserPreferencesResponse,
    WatchdogHeartbeatBody,
    WatchdogHeartbeatResponse,
    WatchdogResponse,
    WatchlistReplaceBody,
)
from quant_assistant.funds.fees import (
    FundFeeSchedule,
    FundRedemptionLot,
    PurchaseTier,
    RedemptionTier,
    ServiceFeeRefundRule,
)
from quant_assistant.services.api_queries import (
    CorporateActionInputView,
    DividendLedgerInputView,
    LedgerCorrectionInputView,
    PreferenceWatchlistItemView,
    TradeLedgerInputView,
)
from quant_assistant.services.strategy_settings import StrategyParameters

if TYPE_CHECKING:
    from quant_assistant.analysis.service import AnalysisService
    from quant_assistant.services.api_queries import ApiQueries


RunId = Annotated[str, Query(min_length=1, max_length=128)]
HistoryLimit = Annotated[int, Query(ge=1, le=730)]
OperationalTrendLimit = Annotated[int, Query(ge=1, le=730)]
FundLifecycleEventLimit = Annotated[int, Query(ge=1, le=500)]
FundStyleDriftEventLimit = Annotated[int, Query(ge=1, le=500)]
FundValuationAlertLimit = Annotated[int, Query(ge=1, le=500)]
NewsLimit = Annotated[int, Query(ge=1, le=50)]
LedgerLimit = Annotated[int, Query(ge=1, le=500)]
ActionLimit = Annotated[int, Query(ge=1, le=500)]
FeatureLimit = Annotated[int, Query(ge=1, le=500)]
FactorLimit = Annotated[int, Query(ge=2, le=730)]


def build_router(queries: ApiQueries, analysis_service: AnalysisService) -> APIRouter:
    """Build only the approved version-one business routes."""

    router = APIRouter(prefix="/api/v1")
    router.include_router(photo_router(queries))

    @router.get("/health", response_model=HealthResponse)
    def health() -> object:
        return queries.health()

    @router.get("/data/status", response_model=DataStatusResponse)
    def data_status() -> object:
        return queries.data_status()

    @router.get("/decisions/latest", response_model=DecisionResponse | None)
    def latest_decision() -> object | None:
        return queries.latest_decision()

    @router.get("/portfolio", response_model=PortfolioResponse)
    def portfolio() -> object:
        return queries.portfolio()

    @router.get("/portfolio/history", response_model=PortfolioHistoryResponse)
    def portfolio_history(limit: HistoryLimit = 180) -> object:
        return queries.portfolio_history(limit)

    @router.post("/portfolio/fund-nav", response_model=FundPortfolioNavResponse)
    def fund_portfolio_nav(body: FundPortfolioNavBody) -> object:
        return queries.fund_portfolio_nav.run(
            body.as_of_date, confirmed=body.confirmed, preview_id=body.preview_id
        )

    @router.get("/operations/trends", response_model=OperationalTrendsResponse)
    def operational_trends(limit: OperationalTrendLimit = 180) -> object:
        return queries.operational_trends(limit)

    @router.get("/stocks/candidates", response_model=CandidateListResponse)
    def stock_candidates(run_id: RunId) -> object:
        return queries.stock_candidates(run_id)

    @router.get("/funds/candidates", response_model=CandidateListResponse)
    def fund_candidates(run_id: RunId) -> object:
        return queries.fund_candidates(run_id)

    @router.get("/funds/valuations", response_model=FundValuationsResponse)
    def fund_valuations(symbols: Annotated[list[str] | None, Query()] = None) -> object:
        return queries.fund_valuations(tuple(symbols or ()))

    @router.get("/funds/valuations/alerts", response_model=FundValuationAlertsResponse)
    def fund_valuation_alerts(
        symbol: Annotated[str | None, Query(min_length=6, max_length=6)] = None,
        limit: FundValuationAlertLimit = 100,
    ) -> object:
        return {"items": queries.valuation_alerts(symbol, limit)}

    @router.get("/funds/lifecycle/events", response_model=FundLifecycleEventsResponse)
    def fund_lifecycle_events(
        symbol: Annotated[str | None, Query(min_length=6, max_length=6)] = None,
        limit: FundLifecycleEventLimit = 100,
    ) -> object:
        return {"items": queries.fund_lifecycle_events(symbol, limit)}

    @router.get("/funds/style-drift/events", response_model=FundStyleDriftEventsResponse)
    def fund_style_drift_events(
        symbol: Annotated[str | None, Query(min_length=6, max_length=6)] = None,
        limit: FundStyleDriftEventLimit = 100,
    ) -> object:
        return {"items": queries.fund_style_drift_events(symbol, limit)}

    @router.get("/funds/{symbol}/detail", response_model=FundDetailResponse)
    def fund_detail(symbol: str) -> object:
        return queries.fund_detail(symbol)

    @router.get("/funds/{symbol}/fee-evidence", response_model=FundFeeEvidenceResponse)
    def fund_fee_evidence(symbol: str) -> object:
        return queries.fund_fee_evidence(symbol)

    @router.post("/funds/{symbol}/fee-evidence", response_model=FundFeeEvidenceResponse)
    def save_fund_fee_evidence(symbol: str, body: FundFeeEvidenceBody) -> object:
        schedule = FundFeeSchedule(
            symbol=symbol,
            share_class=body.share_class,
            purchase_rate=body.purchase_rate,
            annual_service_rate=body.annual_service_rate,
            redemption_tiers=tuple(
                RedemptionTier(**tier.model_dump()) for tier in body.redemption_tiers
            ),
            purchase_tiers=tuple(PurchaseTier(**tier.model_dump()) for tier in body.purchase_tiers),
            verified_on=body.verified_on,
            effective_from=body.effective_from,
            effective_to=body.effective_to,
            channel=body.channel,
            evidence_reference=body.evidence_reference.strip(),
            service_refund_rule=ServiceFeeRefundRule(**body.service_refund_rule.model_dump())
            if body.service_refund_rule
            else None,
        )
        return queries.save_fund_fee_evidence(schedule)

    @router.post("/funds/{symbol}/fee-quote", response_model=FundFeeQuoteResponse)
    def fund_fee_quote(symbol: str, body: FundFeeQuoteBody) -> object:
        return queries.fund_fee_quote(symbol, **body.model_dump())

    @router.post("/funds/{symbol}/redemption-quote", response_model=FundRedemptionQuoteResponse)
    def fund_redemption_quote(symbol: str, body: FundRedemptionQuoteBody) -> object:
        return queries.fund_redemption_quote(
            symbol,
            tuple(FundRedemptionLot(**lot.model_dump()) for lot in body.lots),
            body.confirmed_on,
        )

    @router.get("/ledger", response_model=LedgerResponse)
    def ledger(limit: LedgerLimit = 200) -> object:
        return queries.ledger(limit)

    @router.get("/ledger/attribution", response_model=ProfitAttributionResponse)
    def profit_attribution() -> object:
        return queries.profit_attribution()

    @router.post("/ledger/transactions", response_model=LedgerEntryResponse)
    def record_ledger_trade(body: LedgerTradeBody) -> object:
        return queries.record_ledger_trade(
            TradeLedgerInputView(
                body.entry_type,
                body.symbol,
                body.name,
                body.asset_type,
                body.quantity,
                body.price,
                body.fees,
                body.occurred_at,
                body.external_id,
                body.note,
            )
        )

    @router.post("/ledger/dividends", response_model=LedgerEntryResponse)
    def record_ledger_dividend(body: LedgerDividendBody) -> object:
        return queries.record_ledger_dividend(
            DividendLedgerInputView(
                body.symbol,
                body.asset_type,
                body.gross_amount,
                body.fees,
                body.occurred_at,
                body.external_id,
                body.note,
            )
        )

    @router.get("/ledger/actions", response_model=CorporateActionListResponse)
    def corporate_actions(limit: ActionLimit = 100) -> object:
        return {"items": queries.corporate_actions(limit)}

    @router.post("/ledger/actions", response_model=CorporateActionResponse)
    def record_corporate_action(body: CorporateActionBody) -> object:
        return queries.record_corporate_action(
            CorporateActionInputView(
                body.action_type,
                body.symbol,
                body.asset_type,
                body.effective_at,
                body.external_id,
                body.target_symbol,
                body.quantity,
                body.target_quantity,
                body.price,
                body.target_price,
                body.gross_amount,
                body.fees,
                body.ratio,
                body.cash_symbol,
                body.from_account,
                body.to_account,
                body.note,
            )
        )

    @router.get("/ledger/corrections", response_model=LedgerCorrectionListResponse)
    def ledger_corrections(limit: ActionLimit = 100) -> object:
        return {"items": queries.ledger_corrections(limit)}

    @router.post("/ledger/corrections", response_model=LedgerCorrectionResponse)
    def correct_ledger_entry(body: LedgerCorrectionBody) -> object:
        return queries.correct_ledger_entry(
            LedgerCorrectionInputView(
                body.target_type,
                body.target_id,
                body.reason,
                body.effective_at,
                body.external_id,
                body.note,
            )
        )

    @router.get("/market/snapshot", response_model=MarketSnapshotResponse)
    def market_snapshot(section: Literal["indices", "funds", "sectors"] | None = None) -> object:
        if section is None:
            return queries.market_snapshot()
        return queries.market_snapshot(section=section)

    @router.get("/market/quotes", response_model=MarketQuoteListResponse)
    def market_quotes(
        stocks: Annotated[list[str] | None, Query()] = None,
        indices: Annotated[list[str] | None, Query()] = None,
    ) -> object:
        return queries.market_quotes(tuple(stocks or ()), tuple(indices or ()))

    @router.get("/market/news", response_model=MarketNewsFeedResponse)
    def market_news(limit: NewsLimit = 30) -> object:
        return queries.market_news(limit)

    @router.get("/preferences", response_model=UserPreferencesResponse)
    def user_preferences() -> object:
        return queries.user_preferences()

    @router.get("/research/strategy-settings")
    def strategy_settings() -> object:
        return queries.strategy_settings()

    @router.put("/research/strategy-settings")
    def save_strategy_settings(body: StrategyParameters) -> object:
        return queries.save_strategy_settings(body)

    @router.post("/research/strategy-settings/preview")
    def preview_strategy_settings(body: StrategyParameters) -> object:
        return queries.preview_strategy_settings(body)

    @router.put("/preferences/watchlist", response_model=UserPreferencesResponse)
    def replace_watchlist(body: WatchlistReplaceBody) -> object:
        items = tuple(
            PreferenceWatchlistItemView(item.symbol, item.name, item.asset_type)
            for item in body.items
        )
        return queries.replace_watchlist(items)

    @router.post("/preferences/news/read", response_model=UserPreferencesResponse)
    def mark_news_read(body: NewsReadBody) -> object:
        return queries.mark_news_read(body.ids)

    @router.get("/backtests/latest", response_model=BacktestResponse | None)
    def latest_backtest() -> object | None:
        return queries.latest_backtest()

    @router.get("/portfolio/drawdown", response_model=DrawdownValidationResponse | None)
    def drawdown_validation(
        run_id: Annotated[str | None, Query(max_length=128)] = None,
    ) -> object | None:
        return queries.drawdown_validation(run_id)

    @router.post("/portfolio/drawdown/validate", response_model=DrawdownValidationResponse)
    def validate_drawdown(body: DrawdownValidationBody) -> object:
        return queries.validate_drawdown(
            body.run_id, tuple(item.model_dump() for item in body.nav), body.threshold
        )

    @router.get("/planning/cash-flows", response_model=CashFlowPlanListResponse)
    def cash_flow_plans(
        status: Annotated[str | None, Query(max_length=20)] = None,
        limit: FeatureLimit = 100,
    ) -> object:
        return {"items": queries.cash_flow_plans(status, limit)}

    @router.post("/planning/cash-flows", response_model=CashFlowPlanResponse)
    def create_cash_flow_plan(body: CashFlowPlanBody) -> object:
        return queries.create_cash_flow_plan(body.model_dump())

    @router.get("/planning/cash-flows/due", response_model=CashFlowDueResponse)
    def cash_flow_due(as_of: Annotated[date | None, Query()] = None) -> object:
        return {"items": queries.cash_flow_due(as_of or date.today())}

    @router.post("/research/factors/observations", response_model=FactorObservationResponse)
    def record_factor_observation(body: FactorObservationBody) -> object:
        return queries.record_factor_observation(body.model_dump())

    @router.get("/research/factors/alerts", response_model=FactorAlertsResponse)
    def factor_alerts(
        factor: Annotated[str | None, Query(max_length=80)] = None,
        limit: FeatureLimit = 100,
    ) -> object:
        return {"items": queries.factor_alerts(factor, limit)}

    @router.get("/research/factors/{factor}", response_model=FactorMonitorResponse)
    def factor_monitor(factor: str, limit: FactorLimit = 60) -> object:
        return queries.factor_monitor(factor, limit)

    @router.get("/research/benchmarks", response_model=BenchmarkConfigListResponse)
    def benchmarks(limit: FeatureLimit = 100) -> object:
        return {"items": queries.benchmarks(limit)}

    @router.post("/research/benchmarks", response_model=BenchmarkConfigResponse)
    def create_benchmark(body: BenchmarkConfigBody) -> object:
        return queries.create_benchmark(body.model_dump())

    @router.get(
        "/research/benchmarks/{benchmark_id}/compare", response_model=BenchmarkCompareResponse
    )
    def compare_benchmark(
        benchmark_id: str,
        start_date: Annotated[date | None, Query()] = None,
        end_date: Annotated[date | None, Query()] = None,
    ) -> object:
        return queries.compare_benchmark(benchmark_id, start_date, end_date)

    @router.get("/research/macro/context", response_model=MacroContextResponse)
    def macro_context(as_of: Annotated[date | None, Query()] = None) -> object:
        return queries.macro_context(as_of)

    @router.get("/research/macro/observations", response_model=MacroObservationsResponse)
    def macro_observations(
        indicator: Annotated[str | None, Query(max_length=60)] = None,
        limit: FeatureLimit = 100,
    ) -> object:
        return {"items": queries.macro_observations(indicator, limit)}

    @router.post("/research/macro/observations", response_model=MacroObservationResponse)
    def record_macro_observation(body: MacroObservationBody) -> object:
        return queries.record_macro_observation(body.model_dump())

    @router.get("/operations/watchdog", response_model=WatchdogResponse)
    def watchdog() -> object:
        return {"items": queries.watchdog_scan()}

    @router.post("/operations/watchdog/heartbeat", response_model=WatchdogHeartbeatResponse)
    def watchdog_heartbeat(body: WatchdogHeartbeatBody) -> object:
        return queries.record_watchdog_heartbeat(body.model_dump())

    @router.post("/analysis/explain", response_model=AnalysisResponseModel)
    def explain(body: AnalysisExplainBody) -> object:
        request = queries.analysis_request(body.decision_run_id, body.user_text)
        return analysis_service.explain(request)

    return router
