"""Deterministic and optionally provider-augmented investment analysis."""

from quant_assistant.analysis.attribution import (
    AttributionResult,
    brinson_attribution,
    brinson_fachler_attribution,
    calculate_attribution,
    holding_contributions,
)
from quant_assistant.analysis.deterministic import (
    DeterministicAnalyzer,
    fingerprint_orders,
    fingerprint_request,
)
from quant_assistant.analysis.factor_validation import (
    FactorIcReport,
    LayerBacktestReport,
    cross_sectional_ic,
    factor_ic_report,
    layer_backtest,
    stratified_layer_backtest,
)
from quant_assistant.analysis.fund_holdings import (
    FundHoldingsResult,
    analyze_fund_holdings,
    fund_holdings_penetration,
)
from quant_assistant.analysis.models import (
    AnalysisCandidate,
    AnalysisItem,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisRisk,
    Evidence,
    OrderSnapshot,
    PortfolioBucket,
    ProviderStatus,
)
from quant_assistant.analysis.peer_comparison import (
    PeerComparisonConfig,
    PeerComparisonResult,
    build_fund_radar,
    compare_peers,
    peer_comparison,
)
from quant_assistant.analysis.providers import (
    AnalysisProvider,
    DisabledAnalysisProvider,
    ProviderNetworkError,
    ProviderRateLimited,
    ProviderTimeout,
)
from quant_assistant.analysis.research_statistics import (
    ConfidenceInterval,
    ParameterSensitivityResult,
    annualized_sharpe,
    bootstrap_confidence_intervals,
    max_drawdown,
    monte_carlo_confidence_intervals,
    scan_parameter_sensitivity,
    total_return,
)
from quant_assistant.analysis.service import AnalysisService
from quant_assistant.analysis.smart_dca_validation import (
    SmartDcaValidationFold,
    SmartDcaValidationResult,
    SmartDcaValidationWindow,
    run_smart_dca_walk_forward,
    scan_smart_dca_parameters,
)

__all__ = [
    "AnalysisCandidate",
    "AnalysisItem",
    "AnalysisProvider",
    "AnalysisRequest",
    "AnalysisResponse",
    "AnalysisRisk",
    "AnalysisService",
    "DeterministicAnalyzer",
    "DisabledAnalysisProvider",
    "Evidence",
    "OrderSnapshot",
    "PortfolioBucket",
    "ProviderNetworkError",
    "ProviderRateLimited",
    "ProviderStatus",
    "ProviderTimeout",
    "fingerprint_orders",
    "fingerprint_request",
    "FactorIcReport",
    "LayerBacktestReport",
    "cross_sectional_ic",
    "factor_ic_report",
    "layer_backtest",
    "stratified_layer_backtest",
    "AttributionResult",
    "brinson_attribution",
    "brinson_fachler_attribution",
    "calculate_attribution",
    "holding_contributions",
    "PeerComparisonConfig",
    "PeerComparisonResult",
    "build_fund_radar",
    "compare_peers",
    "peer_comparison",
    "ConfidenceInterval",
    "ParameterSensitivityResult",
    "annualized_sharpe",
    "bootstrap_confidence_intervals",
    "max_drawdown",
    "monte_carlo_confidence_intervals",
    "scan_parameter_sensitivity",
    "total_return",
    "FundHoldingsResult",
    "analyze_fund_holdings",
    "fund_holdings_penetration",
    "SmartDcaValidationFold",
    "SmartDcaValidationResult",
    "SmartDcaValidationWindow",
    "run_smart_dca_walk_forward",
    "scan_smart_dca_parameters",
]
