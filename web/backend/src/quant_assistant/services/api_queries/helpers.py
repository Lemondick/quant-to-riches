"""Public module-level helpers exposed by ``quant_assistant.services.api_queries``.

These three helpers (``normalize_user_text``, ``candidate_context``,
``order_context``, ``deduplicate_evidence``) build the immutable analysis
payload that downstream analysts consume.  They were originally defined at
the bottom of ``api_queries.py`` and are public API surface, so they live in
their own module to make ``LocalApiQueries.analysis_request`` more readable.
"""

from __future__ import annotations

from collections.abc import Sequence

from quant_assistant.analysis.models import (
    AnalysisCandidate,
    Evidence,
    OrderSnapshot,
)
from quant_assistant.models.entities import (
    AssetType,
    ProposedOrder,
)

from ._common import (
    MAX_USER_TEXT_CODEPOINTS,
    _evidence_id,
    _run_id,
)


def normalize_user_text(value: str | None) -> str | None:
    """Normalize untrusted display text without interpreting it as an instruction."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("user_text must be a string or None")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_USER_TEXT_CODEPOINTS:
        raise ValueError("user_text_too_long")
    return normalized


def candidate_context(
    run_id: str,
    stocks: object,
    funds: object,
) -> tuple[tuple[AnalysisCandidate, ...], tuple[Evidence, ...]]:
    """Copy candidate facts and attach deterministic evidence identifiers."""

    normalized_id = _run_id(run_id)
    _, stock_candidates, stock_evidence = _candidate_views(
        normalized_id,
        stocks,
        source_kind="stock_candidate",
        expected_asset_type=AssetType.STOCK,
    )
    _, fund_candidates, fund_evidence = _candidate_views(
        normalized_id,
        funds,
        source_kind="fund_candidate",
        expected_asset_type=AssetType.FUND,
    )
    return (
        (*stock_candidates, *fund_candidates),
        deduplicate_evidence((*stock_evidence, *fund_evidence)),
    )


def order_context(
    run_id: str,
    orders: Sequence[ProposedOrder],
) -> tuple[tuple[OrderSnapshot, ...], tuple[Evidence, ...]]:
    """Copy authoritative order values without deriving or replacing financial fields."""

    normalized_id = _run_id(run_id)
    if not isinstance(orders, Sequence) or isinstance(orders, (str, bytes)):
        raise TypeError("orders must be a sequence")
    snapshots: list[OrderSnapshot] = []
    evidence: list[Evidence] = []
    for order in orders:
        if not isinstance(order, ProposedOrder):
            raise TypeError("orders contains a non-ProposedOrder value")
        identifiers = tuple(
            _evidence_id(normalized_id, "order", order.symbol, index)
            for index, _ in enumerate(order.reasons)
        )
        if not identifiers:
            raise ValueError("order_missing_evidence")
        snapshots.append(
            OrderSnapshot(
                symbol=order.symbol,
                action=order.action,
                amount=order.amount,
                quantity=order.quantity,
                target_weight=order.target_weight,
                evidence_ids=identifiers,
            )
        )
        evidence.extend(
            Evidence(identifier, reason)
            for identifier, reason in zip(identifiers, order.reasons, strict=True)
        )
    return tuple(snapshots), deduplicate_evidence(evidence)


def deduplicate_evidence(values: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """Deduplicate identical evidence while rejecting an ID/text conflict."""

    selected: dict[str, Evidence] = {}
    for value in values:
        if not isinstance(value, Evidence):
            raise TypeError("evidence contains an invalid value")
        existing = selected.get(value.evidence_id)
        if existing is not None and existing.text != value.text:
            raise ValueError("evidence_id_conflict")
        selected.setdefault(value.evidence_id, value)
    return tuple(selected.values())


# Re-export to keep the public surface stable.
__all__ = [
    "candidate_context",
    "deduplicate_evidence",
    "normalize_user_text",
    "order_context",
]


# ``candidate_context`` delegates to the shared mapper so the helper and
# ``LocalApiQueries.stock_candidates``/``fund_candidates`` produce identical
# evidence identifiers regardless of which entry point is used.
def _candidate_views(
    run_id: str,
    frame: object,
    *,
    source_kind: str,
    expected_asset_type: AssetType,
):
    from .mappers import _candidate_views as _mappers_candidate_views

    return _mappers_candidate_views(
        run_id,
        frame,
        source_kind=source_kind,
        expected_asset_type=expected_asset_type,
    )
