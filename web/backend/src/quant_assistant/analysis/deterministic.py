"""Deterministic, offline Chinese explanations derived only from approved facts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime

from quant_assistant.analysis.models import (
    AnalysisItem,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisRisk,
    OrderSnapshot,
    ProviderStatus,
)
from quant_assistant.common.serialization import canonical_json
from quant_assistant.models.entities import Action, DataStatus, MarketRegime


def _fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def fingerprint_request(request: AnalysisRequest) -> str:
    if not isinstance(request, AnalysisRequest):
        raise TypeError("request must be AnalysisRequest")
    return _fingerprint(request)


def fingerprint_orders(orders: Sequence[OrderSnapshot]) -> str:
    if not isinstance(orders, Sequence) or isinstance(orders, (str, bytes)):
        raise TypeError("orders must be a sequence")
    normalized = tuple(orders)
    if not all(isinstance(order, OrderSnapshot) for order in normalized):
        raise TypeError("orders contains an invalid item")
    return _fingerprint(normalized)


class DeterministicAnalyzer:
    """Generate evidence-bound explanations without a provider or network."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        if not callable(now):
            raise TypeError("now must be callable")
        self._now = now

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        if not isinstance(request, AnalysisRequest):
            raise TypeError("request must be AnalysisRequest")
        status_text = "数据有效" if request.data_status is DataStatus.VALID else "数据已阻断"
        regime_text = {
            MarketRegime.GREEN: "市场风险为绿色",
            MarketRegime.YELLOW: "市场风险为黄色",
            MarketRegime.RED: "市场风险为红色",
        }[request.market_regime]
        summary = f"{status_text}；{regime_text}；建议订单{len(request.orders)}条，均需人工确认。"
        known = {item.evidence_id for item in request.evidence}
        opportunities = tuple(
            AnalysisItem(
                f"{candidate.symbol}量化排名第{candidate.rank}",
                candidate.evidence_ids,
            )
            for candidate in request.candidates
            if candidate.action in {Action.BUY, Action.ADD}
        )
        risks = tuple(
            AnalysisRisk("warning", item.text, (item.evidence_id,))
            for item in request.evidence
            if "风险" in item.text or "禁止" in item.text
        )
        claims = (*opportunities, *risks)
        if not all(set(item.evidence_ids) <= known for item in claims):
            raise RuntimeError("deterministic analysis emitted unknown evidence")
        return AnalysisResponse(
            provider_name="disabled",
            provider_status=ProviderStatus.DISABLED,
            generated_at=self._now(),
            decision_run_id=request.decision_run_id,
            summary=summary,
            opportunities=opportunities,
            risks=risks,
            review_actions=("核对支付宝或券商实际费率", "人工确认后再执行"),
            rejected_claims=(),
            input_fingerprint=fingerprint_request(request),
        )


__all__ = [
    "DeterministicAnalyzer",
    "canonical_json",
    "fingerprint_orders",
    "fingerprint_request",
]
