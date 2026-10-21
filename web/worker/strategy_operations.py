"""Quantitative strategy views and immutable evidence; durable storage is owned by C#."""

import json
import os
from pathlib import Path

from analysis_context import analytical_context, shanghai_now

from quant_assistant.analysis.research_evidence import ResearchEvidence
from quant_assistant.services.strategy_settings import (
    StrategyParameters,
    StrategySettingsService,
)

STRATEGY_CAPABILITIES = ("strategy.view", "strategy.preview")


def execute_strategy(kind, payload):
    parameters = StrategyParameters.model_validate(payload.get("parameters", {}))
    if kind == "strategy.view":
        return StrategySettingsService._view(parameters, payload.get("updated_at"))
    with analytical_context(payload["snapshot"]) as (database, _queries, directory):
        manifest = (
            Path(os.environ["QUANT_AGENT_EVIDENCE_MANIFEST"])
            if os.environ.get("QUANT_AGENT_EVIDENCE_MANIFEST")
            else directory / "research-evidence.json"
        )
        evidence = ResearchEvidence(database, shanghai_now, manifest)
        service = StrategySettingsService(
            Path("unused-native-settings"),
            database,
            clock=shanghai_now,
            research_evidence=evidence,
        )
        result = service.preview(parameters)
        if result.get("preview_snapshot_id"):
            snapshot = evidence.get(result["preview_snapshot_id"])
            result["_evidence"] = {
                "content_hash": snapshot["content_hash"],
                "payload": snapshot["payload"],
                "created_at": snapshot["created_at"].isoformat(),
                "expires_at": snapshot["expires_at"].isoformat(),
            }
        return json.loads(json.dumps(result, allow_nan=False))
