"""Bounded research calculations. C# authorizes every call and publishes durable results."""

import json
from datetime import datetime

import requests
from advisor_preflight import evidence_context
from analysis_context import analytical_context, shanghai_now
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
    if kind == "agent.research" and payload.get("research_engine") == "tradingagents_full":
        from tradingagents_operations import execute_full_tradingagents

        expires = datetime.fromisoformat(payload["preview_expires_at"])
        if expires <= datetime.now(SHANGHAI_TZ):
            raise AdvisorModelError("preview_expired")
        current = execute_strategy(
            "strategy.preview",
            {
                "snapshot": payload["snapshot"],
                "parameters": payload["preview_parameters"],
            },
        )
        if current.get("_evidence", {}).get("content_hash") != payload["preview_content_hash"]:
            raise AdvisorModelError("preview_changed")
        upstream = execute_full_tradingagents(payload)
        facts = payload["request"].get("facts", [])
        frozen_sources = [
            {
                "id": fact["fact_id"],
                "content": fact,
                "source_time_status": fact.get("source_time_status", "unknown"),
            }
            for fact in facts
            if isinstance(fact, dict) and isinstance(fact.get("fact_id"), str)
        ]
        upstream_sources = [
            {
                "id": item["source_id"],
                "content": item.get("content", ""),
                "tool_name": item.get("tool_name", "unknown"),
                "source_time_status": item.get("source_time_status", "unknown"),
                "observed_at": item.get("observed_at"),
            }
            for item in upstream.get("tool_evidence", [])
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        ]
        compact_state = {
            "input": {
                "kind": "morning",
                "symbols": [payload["request"]["symbol"]],
                "sources": [*frozen_sources, *upstream_sources],
                "blockers": list(payload["request"].get("warnings", [])),
                "symbol_gates": {},
                "observed_at": payload["request"].get("data_date"),
                "tradingagents": {
                    "data_quality_summary": str(upstream.get("data_quality_summary", ""))[:5000],
                    "missing_data_tasks": upstream.get("missing_data_tasks", []),
                    "analysts": {
                        key: str(value)[:1800]
                        for key, value in upstream.get("analysts", {}).items()
                    },
                    "research_plan": str(upstream.get("research_plan", ""))[:5000],
                    "trader_proposal": str(upstream.get("trader_proposal", ""))[:3000],
                    "risk_debate": str(upstream.get("risk_debate", ""))[:5000],
                    "portfolio_decision": str(upstream.get("portfolio_decision", ""))[:5000],
                },
            }
        }
        questions = {
            "evidence": {
                "type": "noul",
                "instructions": (
                    "Determine whether the frozen facts and full TradingAgents report "
                    "are fresh, consistent and sufficient. Missing facts or data-quality "
                    "warnings mean insufficient evidence. This is not a profit probability."
                ),
            },
            "candidate_0": {
                "type": "choice",
                "instructions": (
                    "Decide whether this stock should enter further research. Never issue "
                    "an order and never override local hard blockers."
                ),
                "criteria": {
                    "research_candidate": "Enter further research only.",
                    "watch": (
                        "Evidence is incomplete, stale, conflicting or requires human review."
                    ),
                    "exclude": (
                        "Current evidence supports excluding this candidate from further research."
                    ),
                },
            },
        }
        models.call_role = "jev"
        verdict = models.decide(compact_state, questions, model=models_config["decision"])
        answer = verdict["answers"]["candidate_0"]
        evidence_ok = verdict["answers"]["evidence"]["noul"] >= 0.8
        confidence = min(answer["confidence"], answer["probabilities"][answer["choice"]])
        hard_blockers = list(payload["request"].get("warnings", []))
        if upstream.get("missing_data_requires_reanalysis"):
            hard_blockers.append("TradingAgents 标记缺失数据需要重新分析")
        if not upstream.get("missing_data_complete", False):
            hard_blockers.append("TradingAgents 的必需数据任务尚未全部完成")
        if not evidence_ok:
            hard_blockers.append("Jev 判定研究证据不足")
        if confidence < 0.8:
            hard_blockers.append("Jev 模型自报置信度不足，需要人工复核")
        action = answer["choice"] if not hard_blockers else "watch"
        report = {
            "engine": "tradingagents-astock-full",
            "tradingagents": upstream,
            "jev_review": verdict,
            "final_status": action,
            "confidence_is_model_reported": True,
            "blockers": hard_blockers,
            "research_only": True,
            "strategy_validated": False,
            "automatic_trading": False,
        }
        return {"report": report, "usage": upstream.get("usage")}

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
    with analytical_context(payload["snapshot"]) as (database, queries, directory):
        service = MarketAdvisorService.__new__(MarketAdvisorService)
        (
            service.database,
            service.evidence,
            service.models,
            service.settings,
            service.now,
        ) = (
            database,
            evidence_context(database, queries, directory, payload, shanghai_now),
            models,
            settings,
            shanghai_now,
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
                    shanghai_now().date(),
                    payload["report_kind"],
                    shanghai_now(),
                    shanghai_now(),
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
