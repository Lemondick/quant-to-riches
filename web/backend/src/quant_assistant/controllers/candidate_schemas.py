"""Named stock and fund screening response contracts."""

from typing import Literal

from quant_assistant.models.entities import Action

from .schema_common import ApiModel

AssetTypeValue = Literal["stock", "fund", "index", "cash"]


class CandidateResponse(ApiModel):
    symbol: str
    asset_type: AssetTypeValue
    action: Action
    score: float
    rank: int
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    name: str | None = None


class CandidateListResponse(ApiModel):
    run_id: str
    items: tuple[CandidateResponse, ...]
