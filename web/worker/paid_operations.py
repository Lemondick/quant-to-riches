"""Bounded research calculations. C# authorizes every call and publishes durable results."""

import json
from datetime import datetime

import requests
from advisor_preflight import evidence_context
from analysis_context import build_context
from public_model_http import post as public_post
from strategy_operations import execute_strategy

from quant_assistant.analysis.advisor_config import AdvisorSettings
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.integrations.tradingagents_worker.contract import (
    ROLES,
    validate_report,
    validate_role,
)
from quant_assistant.integrations.tradingagents_worker.prompts import ROLE_PROMPTS
from quant_assistant.providers.advisor_models import AdvisorModelError, AdvisorModels
from quant_assistant.services.market_advisor import MarketAdvisorService
from quant_assistant.services.model_costs import CostLimitError, tokens

PAID_CAPABILITIES = ("advisor.report", "agent.research")


class MemoryKey:
    def __init__(self, value):
        self.value = value

    def is_file(self):
        return bool(self.value)

    def read_text(self, **_kwargs):
        return self.value


class CostClient:
    def __init__(self, context):
        self.api, self.lease = context["api"], context["lease"]
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["X-Quant-Worker-Secret"] = context["secret"]
        self.role, self.provider, self.stage = "unknown", "unknown", "response"

    def set_context(self, role, provider):
        self.role, self.provider, self.stage = role, provider, "response"

    def set_outcome(self, stage):
        self.stage = stage

    def reserve(self, model, *, max_input_tokens=None, max_output_tokens=None):
        response = self.session.post(
            self.api + "/cost-reserve",
            json={
                **self.lease,
                "model": model,
                "role": self.role,
                "provider": self.provider,
                "max_input_tokens": max_input_tokens,
                "max_output_tokens": max_output_tokens,
            },
            timeout=(3, 10),
        )
        if response.status_code != 200:
            raise CostLimitError("model_budget_exceeded")
        return response.json()["call_id"]

    def finish(self, call_id, usage):
        counts = tokens(usage) if isinstance(usage, dict) else None
        self.session.post(
            self.api + "/cost-finish",
            json={
                **self.lease,
                "call_id": call_id,
                "input_tokens": counts[0] if counts else None,
                "output_tokens": counts[1] if counts else None,
                "stage": self.stage,
                "unknown_reason": None
                if counts
                else "no_usage"
                if usage is None
                else "invalid_usage",
            },
            timeout=(3, 10),
        ).raise_for_status()


def execute_paid(kind, payload):
    credentials = payload["credentials"]
    models_config = payload["models"]
    settings = AdvisorSettings(
        enabled=True,
        endpoint=credentials["endpoint"],
        key_file=MemoryKey(credentials["api_key"]),
        jev_key_file=MemoryKey(credentials["jev_api_key"]),
    )
    models = AdvisorModels(settings, post=public_post)
    models.costs = CostClient(payload["_execution"])
    if kind == "agent.research":
        # Recheck analytical sources at execution time before reserving a paid call.
        # The preview can expire or its source files can change while queued.
        expires = datetime.fromisoformat(payload["preview_expires_at"])
        if expires <= datetime.now(SHANGHAI_TZ):
            raise AdvisorModelError("preview_expired")
        current = execute_strategy(
            "strategy.preview",
            {"snapshot": payload["snapshot"], "parameters": payload["preview_parameters"]},
        )
        if current.get("_evidence", {}).get("content_hash") != payload["preview_content_hash"]:
            raise AdvisorModelError("preview_changed")
        request, previous = payload["request"], {}
        for role in ROLES:
            models.call_role = role
            raw = models.chat(
                models_config["strategy"],
                ROLE_PROMPTS[role],
                {"input": request, "previous": previous},
            )
            previous[role] = validate_role(raw, role, request)
        return {"report": validate_report(previous, request), "usage": None}
    database, queries, directory = build_context(payload["snapshot"])
    try:

        def now():
            return datetime.now(SHANGHAI_TZ)

        service = MarketAdvisorService.__new__(MarketAdvisorService)
        (
            service.database,
            service.evidence,
            service.models,
            service.settings,
            service.now,
        ) = (
            database,
            evidence_context(database, queries, directory, payload, now),
            models,
            settings,
            now,
        )
        service.public_config = lambda: {"configured": True}
        service.model_settings = lambda: {"models": models_config}
        with database.connection() as connection:
            connection.execute(
                (
                    "INSERT INTO "
                    "market_advisor_reports(report_id,report_date,kind,status,created_at,"
                    "updated_at,model_config_json) VALUES(?,?,?,'queued',?,?,?) "
                    "ON CONFLICT DO NOTHING"
                ),
                [
                    payload["public_id"],
                    now().date(),
                    payload["report_kind"],
                    now(),
                    now(),
                    json.dumps(models_config),
                ],
            )
            connection.execute(
                "UPDATE market_advisor_reports SET status='queued' WHERE report_id=?",
                [payload["public_id"]],
            )
        result = service._execute(payload["public_id"])
        with database.connection() as connection:
            row = connection.execute(
                "SELECT input_json FROM market_advisor_reports WHERE report_id=?",
                [payload["public_id"]],
            ).fetchone()
            result["input"] = json.loads(row[0]) if row[0] else None
            sealed = connection.execute(
                (
                    "SELECT snapshot_json,fingerprint FROM "
                    "advisor_decision_snapshots WHERE report_id=?"
                ),
                [payload["public_id"]],
            ).fetchone()
            if sealed:
                result["decision_snapshot"] = {
                    "snapshot_json": sealed[0],
                    "fingerprint": sealed[1],
                }
        return result
    finally:
        database.close()
