"""Immutable, evidence-bound contracts for optional investment analysis."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeVar

from quant_assistant.common.serialization import decimal_coerce
from quant_assistant.models.entities import Action, DataStatus, MarketRegime

_T = TypeVar("_T")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ProviderStatus(StrEnum):
    DISABLED = "disabled"
    OK = "ok"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    INVALID_OUTPUT = "invalid_output"
    REJECTED_CONFLICT = "rejected_conflict"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _date(value: object, field_name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a date")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")
    return decimal_coerce(value, field_name)


def _typed_tuple(value: object, item_type: type[_T], field_name: str) -> tuple[_T, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence")
    items = tuple(value)
    if not all(isinstance(item, item_type) for item in items):
        raise TypeError(f"{field_name} contains an invalid item")
    return items


def _text_tuple(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = True,
    reject_duplicates: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence of strings")
    items = tuple(_text(item, f"{field_name} item") for item in value)
    if not allow_empty and not items:
        raise ValueError(f"{field_name} must not be empty")
    if reject_duplicates and len(items) != len(set(items)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return items


def _evidence_ids(value: object) -> tuple[str, ...]:
    return _text_tuple(value, "evidence_ids", allow_empty=False)


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _text(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "text", _text(self.text, "text"))


@dataclass(frozen=True, slots=True)
class PortfolioBucket:
    name: str
    weight: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        weight = _decimal(self.weight, "weight")
        if not Decimal("0") <= weight <= Decimal("1"):
            raise ValueError("weight must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class AnalysisCandidate:
    symbol: str
    asset_type: str
    action: Action
    score: float
    rank: int
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol"))
        object.__setattr__(self, "asset_type", _text(self.asset_type, "asset_type"))
        if not isinstance(self.action, Action):
            raise TypeError("action must be Action")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise TypeError("score must be a finite number")
        score = float(self.score)
        if not math.isfinite(score):
            raise ValueError("score must be finite")
        object.__setattr__(self, "score", score)
        if isinstance(self.rank, bool) or not isinstance(self.rank, int):
            raise TypeError("rank must be an integer")
        if self.rank <= 0:
            raise ValueError("rank must be positive")
        object.__setattr__(self, "evidence_ids", _evidence_ids(self.evidence_ids))


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    symbol: str
    action: Action
    amount: Decimal
    quantity: Decimal | None
    target_weight: Decimal
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol"))
        if not isinstance(self.action, Action):
            raise TypeError("action must be Action")
        amount = _decimal(self.amount, "amount")
        if amount < 0:
            raise ValueError("amount must be non-negative")
        if self.quantity is not None:
            quantity = _decimal(self.quantity, "quantity")
            if quantity < 0:
                raise ValueError("quantity must be non-negative")
        target_weight = _decimal(self.target_weight, "target_weight")
        if not Decimal("0") <= target_weight <= Decimal("1"):
            raise ValueError("target_weight must be between 0 and 1")
        object.__setattr__(self, "evidence_ids", _evidence_ids(self.evidence_ids))


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    as_of_date: date
    decision_run_id: str
    strategy_versions: tuple[str, ...]
    data_status: DataStatus
    blockers: tuple[str, ...]
    market_regime: MarketRegime
    portfolio_buckets: tuple[PortfolioBucket, ...]
    candidates: tuple[AnalysisCandidate, ...]
    orders: tuple[OrderSnapshot, ...]
    evidence: tuple[Evidence, ...]
    user_text: str | None

    def __post_init__(self) -> None:
        _date(self.as_of_date, "as_of_date")
        object.__setattr__(self, "decision_run_id", _text(self.decision_run_id, "decision_run_id"))
        object.__setattr__(
            self,
            "strategy_versions",
            _text_tuple(self.strategy_versions, "strategy_versions"),
        )
        if not isinstance(self.data_status, DataStatus):
            raise TypeError("data_status must be DataStatus")
        object.__setattr__(self, "blockers", _text_tuple(self.blockers, "blockers"))
        if not isinstance(self.market_regime, MarketRegime):
            raise TypeError("market_regime must be MarketRegime")
        buckets = _typed_tuple(self.portfolio_buckets, PortfolioBucket, "portfolio_buckets")
        candidates = _typed_tuple(self.candidates, AnalysisCandidate, "candidates")
        orders = _typed_tuple(self.orders, OrderSnapshot, "orders")
        evidence = _typed_tuple(self.evidence, Evidence, "evidence")
        object.__setattr__(self, "portfolio_buckets", buckets)
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "orders", orders)
        object.__setattr__(self, "evidence", evidence)
        if self.user_text is not None and not isinstance(self.user_text, str):
            raise TypeError("user_text must be a string or None")

        bucket_names = [item.name for item in buckets]
        if len(bucket_names) != len(set(bucket_names)):
            raise ValueError("portfolio_buckets contains duplicate names")
        evidence_ids = [item.evidence_id for item in evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate evidence_id")
        known = set(evidence_ids)
        for source_name, items in (("candidate", candidates), ("order", orders)):
            unknown = sorted(
                {
                    evidence_id
                    for item in items
                    for evidence_id in item.evidence_ids
                    if evidence_id not in known
                }
            )
            if unknown:
                raise ValueError(
                    f"{source_name} references unknown evidence_id: {', '.join(unknown)}"
                )


@dataclass(frozen=True, slots=True)
class AnalysisItem:
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _text(self.text, "text"))
        object.__setattr__(self, "evidence_ids", _evidence_ids(self.evidence_ids))


@dataclass(frozen=True, slots=True)
class AnalysisRisk:
    severity: str
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "severity", _text(self.severity, "severity"))
        object.__setattr__(self, "text", _text(self.text, "text"))
        object.__setattr__(self, "evidence_ids", _evidence_ids(self.evidence_ids))


@dataclass(frozen=True, slots=True)
class AnalysisResponse:
    provider_name: str
    provider_status: ProviderStatus
    generated_at: datetime
    decision_run_id: str
    summary: str
    opportunities: tuple[AnalysisItem, ...]
    risks: tuple[AnalysisRisk, ...]
    review_actions: tuple[str, ...]
    rejected_claims: tuple[str, ...]
    input_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _text(self.provider_name, "provider_name"))
        if not isinstance(self.provider_status, ProviderStatus):
            raise TypeError("provider_status must be ProviderStatus")
        if not isinstance(self.generated_at, datetime):
            raise TypeError("generated_at must be datetime")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        object.__setattr__(self, "decision_run_id", _text(self.decision_run_id, "decision_run_id"))
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        object.__setattr__(
            self,
            "opportunities",
            _typed_tuple(self.opportunities, AnalysisItem, "opportunities"),
        )
        object.__setattr__(self, "risks", _typed_tuple(self.risks, AnalysisRisk, "risks"))
        object.__setattr__(
            self,
            "review_actions",
            _text_tuple(self.review_actions, "review_actions"),
        )
        object.__setattr__(
            self,
            "rejected_claims",
            _text_tuple(self.rejected_claims, "rejected_claims"),
        )
        fingerprint = _text(self.input_fingerprint, "input_fingerprint")
        if _SHA256_PATTERN.fullmatch(fingerprint) is None:
            raise ValueError("input_fingerprint must be a lowercase SHA-256 digest")
        object.__setattr__(self, "input_fingerprint", fingerprint)


__all__ = [
    "AnalysisCandidate",
    "AnalysisItem",
    "AnalysisRequest",
    "AnalysisResponse",
    "AnalysisRisk",
    "Evidence",
    "OrderSnapshot",
    "PortfolioBucket",
    "ProviderStatus",
]
