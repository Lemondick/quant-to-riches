"""Provider protocol and deliberately disabled offline default."""

from __future__ import annotations

from typing import Protocol

from quant_assistant.analysis.models import AnalysisRequest, AnalysisResponse


class ProviderTimeout(Exception):
    """The optional provider did not respond within its local deadline."""


class ProviderRateLimited(Exception):
    """The optional provider refused work because of a rate limit."""


class ProviderNetworkError(Exception):
    """The optional provider could not reach its configured service."""


class AnalysisProvider(Protocol):
    name: str

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse: ...


class DisabledAnalysisProvider:
    """Offline provider that must be detected before invocation."""

    name = "disabled"

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        raise RuntimeError("disabled provider must not be invoked")


__all__ = [
    "AnalysisProvider",
    "DisabledAnalysisProvider",
    "ProviderNetworkError",
    "ProviderRateLimited",
    "ProviderTimeout",
]
