"""External market-data provider boundaries."""

from quant_assistant.providers.akshare import AkshareProvider
from quant_assistant.providers.base import (
    DailyMarketProvider,
    ProviderDataError,
    ProviderError,
    ProviderUnavailable,
    ResearchEvidenceProvider,
)
from quant_assistant.providers.failover import (
    FailoverMarketProvider,
    SourceHealth,
    build_failover_provider,
)
from quant_assistant.providers.fund_detail import FundSourceHealth
from quant_assistant.providers.fund_valuation import (
    DEFAULT_EASTMONEY_ESTIMATE_URL,
    AkshareFundNavProvider,
    EastmoneyFundValuationProvider,
    FallbackFundValuationProvider,
    FundIntradayEstimate,
    FundValuationError,
    FundValuationProvider,
    normalize_fund_symbol,
    parse_eastmoney_estimate,
    parse_fund_valuation_response,
)
from quant_assistant.providers.ocr import (
    DEFAULT_PADDLE_OCR_ENDPOINT,
    BaiduOcrProvider,
    DisabledOcrProvider,
    OcrError,
    OcrNotConfigured,
    OcrResult,
    OcrUnavailable,
    PaddleOcrProvider,
    create_ocr_provider_from_env,
    extract_detected_dates,
)

__all__ = [
    "AkshareProvider",
    "DailyMarketProvider",
    "ResearchEvidenceProvider",
    "ProviderDataError",
    "ProviderError",
    "ProviderUnavailable",
    "DEFAULT_EASTMONEY_ESTIMATE_URL",
    "AkshareFundNavProvider",
    "EastmoneyFundValuationProvider",
    "FallbackFundValuationProvider",
    "FundIntradayEstimate",
    "FundValuationError",
    "FundValuationProvider",
    "normalize_fund_symbol",
    "parse_eastmoney_estimate",
    "parse_fund_valuation_response",
    "FundSourceHealth",
    "BaiduOcrProvider",
    "DEFAULT_PADDLE_OCR_ENDPOINT",
    "DisabledOcrProvider",
    "OcrError",
    "OcrNotConfigured",
    "OcrResult",
    "OcrUnavailable",
    "PaddleOcrProvider",
    "create_ocr_provider_from_env",
    "extract_detected_dates",
    "FailoverMarketProvider",
    "SourceHealth",
    "build_failover_provider",
]
