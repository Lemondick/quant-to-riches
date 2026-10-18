"""Application services for repeatable local daily operations."""

from quant_assistant.services.account_registry import (
    AccountInput,
    AccountRegistryService,
    AccountSnapshotResult,
    AccountSummary,
)
from quant_assistant.services.benchmarks import BenchmarkConfig, BenchmarkService
from quant_assistant.services.cash_flow_plans import CashFlowPlan, CashFlowPlanService
from quant_assistant.services.daily_decision import (
    DailyDecisionResult,
    DailyDecisionService,
    DecisionInputGate,
    DecisionInputs,
    DefaultStrategyRunner,
    DuckDBDecisionRepository,
    ReadinessGate,
    ShanghaiClock,
    build_daily_decision_service,
)
from quant_assistant.services.drawdown_control import (
    DrawdownControlService,
    DrawdownValidation,
    validate_drawdown,
)
from quant_assistant.services.factor_monitoring import (
    FactorMonitoringService,
    FactorMonitorReport,
    FactorObservation,
)
from quant_assistant.services.fund_lifecycle import (
    FundLifecycleEvent,
    FundLifecycleMonitor,
    FundLifecycleSnapshot,
    FundLifecycleSyncItem,
    FundLifecycleSyncResult,
    detect_lifecycle_events,
    parse_scale,
    sync_fund_lifecycle,
)
from quant_assistant.services.fund_style_drift import (
    FundHoldingsSnapshot,
    FundStyleDriftEvent,
    FundStyleDriftMonitor,
    FundStyleSyncItem,
    FundStyleSyncResult,
    StyleDriftComparison,
    compare_holdings,
    normalize_holdings_snapshot,
    sync_fund_style,
)
from quant_assistant.services.fund_style_drift import (
    snapshot_from_detail as holdings_snapshot_from_detail,
)
from quant_assistant.services.fund_valuation import (
    FundIntradayValuationService,
    FundPositionEstimate,
)
from quant_assistant.services.macro_data import MacroDataService, MacroObservation
from quant_assistant.services.news_sentiment import classify_news_sentiment
from quant_assistant.services.notifications import (
    FanoutNotifier,
    NotificationResult,
    NotificationStatus,
    PushPlusNotifier,
    ServerChanNotifier,
    WebhookNotifier,
    WeChatSubscribeNotifier,
    WeComWebhookNotifier,
    format_daily_order_digest,
    format_valuation_alert_digest,
    notifier_from_env,
    send_optional_notification,
)
from quant_assistant.services.valuation_alerts import (
    FundValuationAlert,
    FundValuationAlertService,
    ValuationAlertConfig,
    ValuationAlertSeverity,
    ValuationScanResult,
    detect_valuation_alerts,
)
from quant_assistant.services.watchdog import SchedulerWatchdogService

__all__ = [
    "DailyDecisionResult",
    "DailyDecisionService",
    "DecisionInputGate",
    "DecisionInputs",
    "DefaultStrategyRunner",
    "DuckDBDecisionRepository",
    "ReadinessGate",
    "ShanghaiClock",
    "build_daily_decision_service",
    "AccountInput",
    "AccountRegistryService",
    "AccountSnapshotResult",
    "AccountSummary",
    "FundIntradayValuationService",
    "FundPositionEstimate",
    "FundLifecycleEvent",
    "FundLifecycleMonitor",
    "FundLifecycleSnapshot",
    "FundLifecycleSyncItem",
    "FundLifecycleSyncResult",
    "detect_lifecycle_events",
    "parse_scale",
    "sync_fund_lifecycle",
    "FundHoldingsSnapshot",
    "FundStyleDriftEvent",
    "FundStyleDriftMonitor",
    "FundStyleSyncItem",
    "FundStyleSyncResult",
    "StyleDriftComparison",
    "compare_holdings",
    "normalize_holdings_snapshot",
    "holdings_snapshot_from_detail",
    "sync_fund_style",
    "NotificationResult",
    "NotificationStatus",
    "FanoutNotifier",
    "PushPlusNotifier",
    "ServerChanNotifier",
    "WeChatSubscribeNotifier",
    "WebhookNotifier",
    "WeComWebhookNotifier",
    "format_daily_order_digest",
    "format_valuation_alert_digest",
    "notifier_from_env",
    "send_optional_notification",
    "FundValuationAlert",
    "FundValuationAlertService",
    "ValuationAlertConfig",
    "ValuationAlertSeverity",
    "ValuationScanResult",
    "detect_valuation_alerts",
    "BenchmarkConfig",
    "BenchmarkService",
    "CashFlowPlan",
    "CashFlowPlanService",
    "DrawdownControlService",
    "DrawdownValidation",
    "validate_drawdown",
    "FactorMonitorReport",
    "FactorMonitoringService",
    "FactorObservation",
    "MacroDataService",
    "MacroObservation",
    "classify_news_sentiment",
    "SchedulerWatchdogService",
]
