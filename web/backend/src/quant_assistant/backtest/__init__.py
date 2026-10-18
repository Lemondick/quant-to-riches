"""Deterministic backtest execution contracts."""

from quant_assistant.backtest.engine import (
    BacktestContext,
    BacktestEngine,
    BacktestRequest,
    BacktestResult,
    FrameBacktestData,
)
from quant_assistant.backtest.fund_execution import (
    FundExecutionModel,
    FundOrderRejected,
    FundSettlementRule,
    FundTradingStatus,
    PendingFundOrder,
)
from quant_assistant.backtest.metrics import PerformanceMetrics, calculate_performance
from quant_assistant.backtest.models import (
    DailyBar,
    ExecutionResult,
    ExecutionState,
    Fill,
    RejectedOrder,
    StockFeeSchedule,
)
from quant_assistant.backtest.stock_execution import StockExecutionModel
from quant_assistant.backtest.walk_forward import (
    ValidationWindow,
    WalkForwardFold,
    WalkForwardRequest,
    WalkForwardResult,
    run_walk_forward,
)

__all__ = [
    "BacktestContext",
    "BacktestEngine",
    "BacktestRequest",
    "BacktestResult",
    "DailyBar",
    "ExecutionResult",
    "ExecutionState",
    "Fill",
    "FrameBacktestData",
    "FundExecutionModel",
    "FundOrderRejected",
    "FundSettlementRule",
    "FundTradingStatus",
    "PendingFundOrder",
    "PerformanceMetrics",
    "RejectedOrder",
    "StockExecutionModel",
    "StockFeeSchedule",
    "ValidationWindow",
    "WalkForwardFold",
    "WalkForwardRequest",
    "WalkForwardResult",
    "calculate_performance",
    "run_walk_forward",
]
