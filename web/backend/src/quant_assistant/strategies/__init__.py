"""Deterministic, capital-independent stock and fund strategies."""

from quant_assistant.strategies.fund_factors import (
    FundFactorConfig,
    calculate_calmar_ratio,
    calculate_information_ratio,
    calculate_sortino_ratio,
    calmar_ratio,
    information_ratio,
    score_fund_universe,
    sortino_ratio,
)
from quant_assistant.strategies.funds import (
    FundStrategyConfig,
    benchmark_score,
    generate_fund_signals,
)
from quant_assistant.strategies.macro_allocation import (
    DEFAULT_MACRO_ASSETS,
    MacroAllocationConfig,
    MacroAllocationResult,
    calculate_macro_allocation,
    macro_resilient_allocation,
    normalize_macro_asset,
)
from quant_assistant.strategies.market_regime import classify_market_regime
from quant_assistant.strategies.sector_rotation import (
    DEFAULT_SECTORS,
    SectorRotationConfig,
    SectorRotationResult,
    calculate_sector_rotation,
    normalize_sector,
    sector_momentum_rotation,
)
from quant_assistant.strategies.smart_dca import (
    SmartDcaAction,
    SmartDCAConfig,
    SmartDCADecision,
    SmartDCAInput,
    calculate_smart_dca,
    calculate_smart_dca_decision,
)
from quant_assistant.strategies.stocks import (
    StockStrategyConfig,
    generate_stock_signals,
    rank_stocks,
)

__all__ = [
    "FundStrategyConfig",
    "StockStrategyConfig",
    "benchmark_score",
    "classify_market_regime",
    "generate_fund_signals",
    "generate_stock_signals",
    "rank_stocks",
    "FundFactorConfig",
    "calmar_ratio",
    "calculate_calmar_ratio",
    "calculate_information_ratio",
    "calculate_sortino_ratio",
    "information_ratio",
    "score_fund_universe",
    "sortino_ratio",
    "SmartDcaAction",
    "SmartDCAConfig",
    "SmartDCADecision",
    "SmartDCAInput",
    "calculate_smart_dca",
    "calculate_smart_dca_decision",
    "DEFAULT_MACRO_ASSETS",
    "MacroAllocationConfig",
    "MacroAllocationResult",
    "calculate_macro_allocation",
    "normalize_macro_asset",
    "macro_resilient_allocation",
    "DEFAULT_SECTORS",
    "SectorRotationConfig",
    "SectorRotationResult",
    "calculate_sector_rotation",
    "normalize_sector",
    "sector_momentum_rotation",
]
