"""Baseline-first validation and safe degradation for optional providers."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace

from quant_assistant.analysis.deterministic import (
    DeterministicAnalyzer,
    fingerprint_orders,
    fingerprint_request,
)
from quant_assistant.analysis.models import (
    AnalysisItem,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisRisk,
    ProviderStatus,
)
from quant_assistant.analysis.providers import (
    AnalysisProvider,
    DisabledAnalysisProvider,
    ProviderNetworkError,
    ProviderRateLimited,
    ProviderTimeout,
)

logger = logging.getLogger(__name__)


def _provider_name(provider: object) -> str:
    name = getattr(provider, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise TypeError("provider.name must be a non-empty string")
    return name.strip()


def _degrade(
    baseline: AnalysisResponse,
    provider_name: str,
    status: ProviderStatus,
) -> AnalysisResponse:
    return replace(
        baseline,
        provider_name=provider_name,
        provider_status=status,
        rejected_claims=(),
    )


def _merge_claims(
    baseline: Sequence[AnalysisItem] | Sequence[AnalysisRisk],
    supplied: Sequence[AnalysisItem] | Sequence[AnalysisRisk],
    known_evidence: frozenset[str],
) -> tuple[tuple[AnalysisItem | AnalysisRisk, ...], tuple[str, ...]]:
    merged: list[AnalysisItem | AnalysisRisk] = list(baseline)
    seen = {(type(item), item.text, item.evidence_ids) for item in merged}
    rejected: list[str] = []
    for item in supplied:
        if not set(item.evidence_ids).issubset(known_evidence):
            if item.text not in rejected:
                rejected.append(item.text)
            continue
        identity = (type(item), item.text, item.evidence_ids)
        if identity not in seen:
            merged.append(item)
            seen.add(identity)
    return tuple(merged), tuple(rejected)


def validate_and_merge(
    baseline: AnalysisResponse,
    supplied: AnalysisResponse,
    request: AnalysisRequest,
) -> AnalysisResponse:
    """Append only evidence-bound claims while preserving deterministic authority."""

    if not isinstance(baseline, AnalysisResponse):
        raise TypeError("baseline must be AnalysisResponse")
    if not isinstance(supplied, AnalysisResponse):
        raise TypeError("supplied must be AnalysisResponse")
    if not isinstance(request, AnalysisRequest):
        raise TypeError("request must be AnalysisRequest")
    if (
        supplied.decision_run_id != request.decision_run_id
        or supplied.input_fingerprint != baseline.input_fingerprint
    ):
        return _degrade(
            baseline,
            supplied.provider_name,
            ProviderStatus.REJECTED_CONFLICT,
        )
    if supplied.provider_status is not ProviderStatus.OK:
        return _degrade(
            baseline,
            supplied.provider_name,
            ProviderStatus.INVALID_OUTPUT,
        )

    known = frozenset(item.evidence_id for item in request.evidence)
    opportunities, rejected_opportunities = _merge_claims(
        baseline.opportunities,
        supplied.opportunities,
        known,
    )
    risks, rejected_risks = _merge_claims(
        baseline.risks,
        supplied.risks,
        known,
    )
    rejected = tuple(dict.fromkeys((*rejected_opportunities, *rejected_risks)))
    return AnalysisResponse(
        provider_name=supplied.provider_name,
        provider_status=(ProviderStatus.INVALID_OUTPUT if rejected else ProviderStatus.OK),
        generated_at=supplied.generated_at,
        decision_run_id=baseline.decision_run_id,
        summary=baseline.summary,
        opportunities=tuple(item for item in opportunities if isinstance(item, AnalysisItem)),
        risks=tuple(item for item in risks if isinstance(item, AnalysisRisk)),
        review_actions=baseline.review_actions,
        rejected_claims=rejected,
        input_fingerprint=baseline.input_fingerprint,
    )


class AnalysisService:
    """Keep deterministic analysis authoritative around an optional provider."""

    def __init__(
        self,
        deterministic: DeterministicAnalyzer,
        provider: AnalysisProvider | None = None,
    ) -> None:
        if not isinstance(deterministic, DeterministicAnalyzer):
            raise TypeError("deterministic must be DeterministicAnalyzer")
        selected = provider or DisabledAnalysisProvider()
        if not callable(getattr(selected, "analyze", None)):
            raise TypeError("provider must implement analyze()")
        self._deterministic = deterministic
        self._provider = selected
        self._provider_name = _provider_name(selected)

    def explain(self, request: AnalysisRequest) -> AnalysisResponse:
        if not isinstance(request, AnalysisRequest):
            raise TypeError("request must be AnalysisRequest")
        baseline = self._deterministic.analyze(request)
        if isinstance(self._provider, DisabledAnalysisProvider):
            return baseline

        order_hash = fingerprint_orders(request.orders)
        request_hash = fingerprint_request(request)
        provider_request = deepcopy(request)
        try:
            supplied = self._provider.analyze(provider_request)
        except ProviderTimeout as exc:
            logger.warning("analysis provider unavailable: timeout (%s)", type(exc).__name__)
            return _degrade(baseline, self._provider_name, ProviderStatus.TIMEOUT)
        except ProviderRateLimited as exc:
            logger.warning("analysis provider unavailable: rate limited (%s)", type(exc).__name__)
            return _degrade(
                baseline,
                self._provider_name,
                ProviderStatus.RATE_LIMITED,
            )
        except ProviderNetworkError as exc:
            logger.warning("analysis provider unavailable: network error (%s)", type(exc).__name__)
            return _degrade(
                baseline,
                self._provider_name,
                ProviderStatus.NETWORK_ERROR,
            )
        except Exception as exc:
            logger.exception("analysis provider code/contract failure (%s)", type(exc).__name__)
            return _degrade(
                baseline,
                self._provider_name,
                ProviderStatus.INVALID_OUTPUT,
            )

        try:
            input_changed = (
                fingerprint_orders(provider_request.orders) != order_hash
                or fingerprint_request(provider_request) != request_hash
            )
        except Exception as exc:
            logger.exception("analysis provider mutated request (%s)", type(exc).__name__)
            input_changed = True
        if input_changed:
            return _degrade(
                baseline,
                self._provider_name,
                ProviderStatus.REJECTED_CONFLICT,
            )
        try:
            if not isinstance(supplied, AnalysisResponse):
                return _degrade(
                    baseline,
                    self._provider_name,
                    ProviderStatus.INVALID_OUTPUT,
                )
            if (
                supplied.decision_run_id != baseline.decision_run_id
                or supplied.input_fingerprint != baseline.input_fingerprint
            ):
                return _degrade(
                    baseline,
                    self._provider_name,
                    ProviderStatus.REJECTED_CONFLICT,
                )
            if supplied.provider_name != self._provider_name:
                return _degrade(
                    baseline,
                    self._provider_name,
                    ProviderStatus.INVALID_OUTPUT,
                )
            return validate_and_merge(baseline, supplied, request)
        except Exception as exc:
            logger.exception(
                "analysis provider returned an invalid response (%s)", type(exc).__name__
            )
            return _degrade(
                baseline,
                self._provider_name,
                ProviderStatus.INVALID_OUTPUT,
            )


__all__ = ["AnalysisService", "validate_and_merge"]
