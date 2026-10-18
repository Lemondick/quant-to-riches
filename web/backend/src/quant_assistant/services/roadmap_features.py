"""Backward-compatible facade for the remaining product roadmap services.

The implementations moved to their own modules (one feature per file).  This
module is kept only to preserve the original import path; all public names
re-export their authoritative home.
"""

from quant_assistant.services.benchmarks import BenchmarkConfig, BenchmarkService
from quant_assistant.services.cash_flow_plans import CashFlowPlan, CashFlowPlanService
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
from quant_assistant.services.macro_data import MacroDataService, MacroObservation
from quant_assistant.services.news_sentiment import classify_news_sentiment
from quant_assistant.services.watchdog import SchedulerWatchdogService

__all__ = [
    "BenchmarkConfig",
    "BenchmarkService",
    "CashFlowPlan",
    "CashFlowPlanService",
    "DrawdownControlService",
    "DrawdownValidation",
    "FactorMonitorReport",
    "FactorMonitoringService",
    "FactorObservation",
    "MacroDataService",
    "MacroObservation",
    "SchedulerWatchdogService",
    "classify_news_sentiment",
    "validate_drawdown",
]
