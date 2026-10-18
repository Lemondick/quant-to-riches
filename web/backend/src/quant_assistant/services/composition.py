"""One composition root shared by the web controllers and maintenance CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from quant_assistant import __version__
from quant_assistant.analysis.deterministic import DeterministicAnalyzer
from quant_assistant.analysis.providers import DisabledAnalysisProvider
from quant_assistant.analysis.research_evidence import ResearchEvidence
from quant_assistant.analysis.service import AnalysisService
from quant_assistant.config import Settings
from quant_assistant.models.entities import Position
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.akshare import AkshareProvider
from quant_assistant.providers.failover import FailoverMarketProvider, build_failover_provider
from quant_assistant.reporting.queries import DecisionQueries
from quant_assistant.services.account_registry import AccountRegistryService
from quant_assistant.services.agent_research import AgentResearchService
from quant_assistant.services.api_queries import LocalApiQueries
from quant_assistant.services.daily_decision import (
    DailyDecisionService,
    ShanghaiClock,
    build_daily_decision_service,
)
from quant_assistant.services.fund_pool_commands import (
    FundFamilyInput,
    FundPoolCommandService,
)
from quant_assistant.services.operations import OperationalAlertService, OperationCoordinator
from quant_assistant.services.paper_orders import PaperOrderService
from quant_assistant.services.photo_import import PhotoAccountImportService
from quant_assistant.services.photo_upload import PhotoUploadService
from quant_assistant.services.portfolio_commands import (
    CorporateActionInput,
    DividendInput,
    ExternalCashFlowInput,
    InstrumentInput,
    LedgerCorrectionInput,
    NavBaselineInput,
    PortfolioCalibrationInput,
    PortfolioCommandService,
    PositionInput,
    TransactionInput,
)
from quant_assistant.services.preferences import LocalPreferenceStore
from quant_assistant.services.roadmap_features import (
    BenchmarkService,
    CashFlowPlanService,
    DrawdownControlService,
    FactorMonitoringService,
    MacroDataService,
    SchedulerWatchdogService,
)
from quant_assistant.services.strategy_settings import StrategySettingsService


@dataclass(frozen=True, slots=True)
class DashboardCommands:
    """Page-facing bundle that keeps both write services explicit and local."""

    portfolio: PortfolioCommandService
    fund_pool: FundPoolCommandService
    paper_orders: PaperOrderService
    accounts: AccountRegistryService | None = None

    def record_position(self, item: PositionInput | Position) -> str:
        return self.portfolio.record_position(item)

    def import_initial_account(
        self,
        event_id: str,
        as_of_date: date,
        positions: tuple[PositionInput, ...],
        *,
        reason: str,
        instrument_metadata: tuple[InstrumentInput, ...] = (),
    ) -> str:
        return self.portfolio.import_initial_account(
            event_id,
            as_of_date,
            positions,
            reason=reason,
            instrument_metadata=instrument_metadata,
        )

    def record_transaction(self, item: TransactionInput) -> int:
        return self.portfolio.record_transaction(item)

    def record_dividend(self, item: DividendInput) -> int:
        return self.portfolio.record_dividend(item)

    def record_corporate_action(self, item: CorporateActionInput) -> int:
        return self.portfolio.record_corporate_action(item)

    def correct_ledger_entry(self, item: LedgerCorrectionInput) -> int:
        return self.portfolio.correct_ledger_entry(item)

    def record_transaction_batch(
        self,
        items: tuple[TransactionInput, ...],
        *,
        instrument_metadata: tuple[InstrumentInput, ...] = (),
    ) -> tuple[int, ...]:
        return self.portfolio.record_transaction_batch(
            items, instrument_metadata=instrument_metadata
        )

    def record_transactions(
        self,
        items: tuple[TransactionInput, ...],
        *,
        instrument_metadata: tuple[InstrumentInput, ...] = (),
    ) -> tuple[int, ...]:
        return self.portfolio.record_transactions(items, instrument_metadata=instrument_metadata)

    def confirm_nav_baseline(self, item: NavBaselineInput) -> str:
        return self.portfolio.confirm_nav_baseline(item)

    def record_external_cash_flow(self, item: ExternalCashFlowInput) -> str:
        return self.portfolio.record_external_cash_flow(item)

    def record_portfolio_calibration(self, item: PortfolioCalibrationInput) -> str:
        return self.portfolio.record_portfolio_calibration(item)

    def upsert_family(self, item: FundFamilyInput) -> str:
        return self.fund_pool.upsert_family(item)

    def stage_paper_run(self, run_id: str) -> tuple[str, ...]:
        return self.paper_orders.stage_run(run_id)

    def register_account(self, item):
        if self.accounts is None:
            raise RuntimeError("账户分组服务未启用")
        return self.accounts.register(item)

    def import_account_snapshot(self, account_id, as_of_date, positions, *, event_id, reason):
        if self.accounts is None:
            raise RuntimeError("账户分组服务未启用")
        return self.accounts.import_snapshot(
            account_id,
            as_of_date,
            positions,
            event_id=event_id,
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class LocalServices:
    database: Database
    daily_decision: DailyDecisionService
    decision_queries: DecisionQueries
    api_queries: LocalApiQueries
    preferences: LocalPreferenceStore
    analysis: AnalysisService
    portfolio_commands: PortfolioCommandService
    fund_pool_commands: FundPoolCommandService
    paper_order_commands: PaperOrderService
    account_registry: AccountRegistryService
    operation_coordinator: OperationCoordinator
    operational_alerts: OperationalAlertService
    dashboard_commands: DashboardCommands
    roadmap: RoadmapServices


@dataclass(frozen=True, slots=True)
class RoadmapServices:
    """Shared feature services used by web and maintenance CLI adapters."""

    drawdown: DrawdownControlService
    cash_flows: CashFlowPlanService
    factor_monitor: FactorMonitoringService
    benchmarks: BenchmarkService
    macro: MacroDataService
    watchdog: SchedulerWatchdogService


def build_local_services(
    settings: Settings,
    clock: object | Callable[[], datetime] | None = None,
    *,
    advisor_settings=None,
    public_providers=None,
    job_gate=None,
    account_enabled=None,
) -> LocalServices:
    """Build the complete offline-capable service graph exactly once per caller."""

    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    selected_clock = clock or ShanghaiClock()
    now = getattr(selected_clock, "now", selected_clock)
    if not callable(now):
        raise TypeError("clock must be callable or provide now()")

    database = Database(settings.database_path)
    database.initialize()
    decision_queries = DecisionQueries(database)
    daily_decision = build_daily_decision_service(database, selected_clock)
    portfolio_commands = PortfolioCommandService(database, selected_clock)
    preferences = LocalPreferenceStore(
        settings.data_dir / "preferences.json",
        legacy_watchlist_path=settings.data_dir / "watchlist.json",
        clock=now,
    )
    roadmap = RoadmapServices(
        drawdown=DrawdownControlService(database, selected_clock),
        cash_flows=CashFlowPlanService(database, selected_clock),
        factor_monitor=FactorMonitoringService(database, selected_clock),
        benchmarks=BenchmarkService(database, selected_clock),
        macro=MacroDataService(database, selected_clock),
        watchdog=SchedulerWatchdogService(database, selected_clock),
    )
    # Seed a conservative 60/40 reference only when no user benchmark exists.
    # It remains research-only and can never enable order generation.
    if not roadmap.benchmarks.list(limit=1):
        roadmap.benchmarks.create(
            "沪深300 / 现金 60/40",
            {"000300.SH": 0.6, "CASH": 0.4},
            benchmark_id="default-60-40",
            is_default=True,
        )
    evidence = ResearchEvidence(database, now, settings.agent_research.evidence_manifest)
    previews = StrategySettingsService(
        settings.data_dir / "strategy-settings.json",
        database,
        now,
        research_evidence=evidence if settings.agent_research.enabled else None,
    )
    agent_research = AgentResearchService(
        database, settings.agent_research, evidence, previews, now
    )
    agent_research.job_gate = job_gate
    agent_research.account_enabled = account_enabled or (lambda: True)
    api_queries = LocalApiQueries(
        decision_queries,
        version=__version__,
        **(public_providers or {}),
        preference_store=preferences,
        portfolio_commands=portfolio_commands,
        feature_services=roadmap,
        strategy_settings=previews,
        agent_research=agent_research,
        photo_upload=PhotoUploadService(
            PhotoAccountImportService.from_env(database), portfolio_commands, database=database
        ),
    )
    from quant_assistant.services.advisor_evidence import AdvisorEvidence
    from quant_assistant.services.market_advisor import MarketAdvisorService

    api_queries.market_advisor = MarketAdvisorService(
        database,
        AdvisorEvidence(database, api_queries, previews, preferences),
        settings=advisor_settings,
    )
    api_queries.market_advisor.research = agent_research
    api_queries.market_advisor.job_gate = job_gate
    api_queries.market_advisor.account_enabled = account_enabled or (lambda: True)
    api_queries.market_advisor.configure_research()
    from quant_assistant.services.fund_portfolio_nav import FundPortfolioNavService

    api_queries.fund_portfolio_nav = FundPortfolioNavService(
        database, settings, api_queries._fund_detail_provider, now
    )
    from quant_assistant.services.fund_lots import FundLotService

    api_queries.fund_lots = FundLotService(database, now)
    from quant_assistant.services.fund_scenario_study import FundScenarioStudy

    api_queries.fund_scenario_study = FundScenarioStudy(
        settings.data_dir / "fund-scenario-study", now=now
    )
    from quant_assistant.services.advisor_tracking import AdvisorTrackingService
    from quant_assistant.services.fund_portfolio_risk import FundPortfolioRisk
    from quant_assistant.services.fund_research_store import FundResearchStore

    api_queries.fund_research_store = FundResearchStore(
        database, api_queries._fund_detail_provider, now
    )
    api_queries.advisor_tracking = AdvisorTrackingService(database, api_queries.fund_research_store)
    api_queries.fund_portfolio_risk = FundPortfolioRisk(
        database, api_queries.fund_research_store, now
    )
    analysis = AnalysisService(
        DeterministicAnalyzer(now),
        DisabledAnalysisProvider(),
    )
    fund_pool_commands = FundPoolCommandService(database, selected_clock)
    paper_order_commands = PaperOrderService(database, selected_clock)
    account_registry = AccountRegistryService(database, selected_clock)
    operation_coordinator = OperationCoordinator(database, selected_clock)
    operational_alerts = OperationalAlertService(database, selected_clock)
    dashboard_commands = DashboardCommands(
        portfolio_commands, fund_pool_commands, paper_order_commands, account_registry
    )
    return LocalServices(
        database=database,
        daily_decision=daily_decision,
        decision_queries=decision_queries,
        api_queries=api_queries,
        preferences=preferences,
        analysis=analysis,
        portfolio_commands=portfolio_commands,
        fund_pool_commands=fund_pool_commands,
        paper_order_commands=paper_order_commands,
        account_registry=account_registry,
        operation_coordinator=operation_coordinator,
        operational_alerts=operational_alerts,
        dashboard_commands=dashboard_commands,
        roadmap=roadmap,
    )


def build_market_data_provider() -> FailoverMarketProvider:
    """Build the shared public-data failover boundary for CLI/data jobs.

    ``AkshareProvider`` already normalizes Eastmoney/Sina/Tencent and official
    exchange fallbacks.  Keeping the wrapper here means every caller gets the
    same health/cooldown policy and a future independent adapter can be added
    in one composition-root change.
    """

    return build_failover_provider(AkshareProvider())


__all__ = [
    "DashboardCommands",
    "RoadmapServices",
    "LocalServices",
    "build_market_data_provider",
    "build_local_services",
]
