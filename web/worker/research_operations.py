"""Named non-trading research projections and immutable study generation."""

import hashlib
from datetime import date, datetime

from analysis_context import analytical_context, shanghai_now

from quant_assistant.analysis.deterministic import DeterministicAnalyzer
from quant_assistant.analysis.service import AnalysisService
from quant_assistant.controllers.schemas import AnalysisResponseModel
from quant_assistant.providers.fund_detail import PublicFundDetailProvider
from quant_assistant.services.advisor_tracking import AdvisorTrackingService
from quant_assistant.services.fund_portfolio_risk import FundPortfolioRisk
from quant_assistant.services.fund_research_store import FundResearchStore
from quant_assistant.services.fund_scenario_study import FundScenarioStudy
from quant_assistant.services.fund_study_refresh import run_refresh
from quant_assistant.services.production_readiness import (
    _account_check,
    _backtest_check,
    _drawdown_check,
    _fee_evidence_check,
    _paper_check,
    _point_in_time_check,
)

RESEARCH_CAPABILITIES = (
    "analysis.explain",
    "analysis.readiness",
    "fund.tracking",
    "fund.risk",
    "fund.public_observations",
    "fund.study",
    "fund.curves",
    "fund.study_update",
)


def execute_research(kind, payload):
    with analytical_context(payload["snapshot"]) as (database, queries, directory):
        if kind == "analysis.explain":
            request = queries.analysis_request(
                payload.get("decision_run_id"), payload.get("user_text", "")
            )
            return AnalysisResponseModel.model_validate(
                AnalysisService(DeterministicAnalyzer(shanghai_now)).explain(request)
            ).model_dump(mode="json")
        if kind == "analysis.readiness":
            account = _account_check(database)
            point_in_time = _point_in_time_check(database)
            if isinstance(account.get("as_of_date"), date):
                account["as_of_date"] = account["as_of_date"].isoformat()
            if isinstance(point_in_time.get("as_of"), date):
                point_in_time["as_of"] = point_in_time["as_of"].isoformat()
            return {
                "generated_at": shanghai_now().isoformat(),
                "checks": {
                    "account_baseline": account,
                    "broker_fee_evidence": _fee_evidence_check(database),
                    "point_in_time_data": point_in_time,
                    "strategy_acceptance": _backtest_check(database),
                    "drawdown_control": _drawdown_check(database),
                    "paper_sessions": _paper_check(database, 20),
                },
            }
        if kind in {"fund.tracking", "fund.risk", "fund.public_observations"}:
            store = FundResearchStore(database, PublicFundDetailProvider(), shanghai_now)
            if kind == "fund.tracking":
                return AdvisorTrackingService(database, store).read(
                    payload["limit"], payload["page"]
                )
            if kind == "fund.risk":
                return FundPortfolioRisk(database, store, shanghai_now).read()
            result = store.refresh(payload["symbols"])
            with database.connection() as connection:
                result["_snapshots"] = [
                    dict(
                        zip(
                            ("fingerprint", "symbol", "observed_at", "snapshot_json"),
                            (digest, symbol, observed.isoformat(), raw),
                            strict=True,
                        )
                    )
                    for digest, symbol, observed, raw in connection.execute(
                        "SELECT * FROM fund_research_snapshots ORDER BY fingerprint"
                    ).fetchall()
                ]
            return result
        root = directory / "fund-scenario-study"
        pointer = payload.get("pointer")
        source = root
        if pointer:
            source = root / "versions" / pointer["version"] / "study"
            if (
                not source.resolve().is_relative_to(root.resolve())
                or hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
                != pointer["manifest_sha256"]
            ):
                raise ValueError("study_integrity_failed")
        service = FundScenarioStudy(source, now=shanghai_now)
        if kind == "fund.study":
            return service.read()
        if kind == "fund.curves":
            return service.curves(
                payload["symbol"],
                payload["period"],
                payload["purchase_rate"],
                payload["version"],
            )
        previous = service.read()
        if not previous.get("available") or not previous.get("funds"):
            return {
                "status": "failed",
                "message": "缺少有效研究范围，未发起采集。",
                "model_calls": 0,
            }
        job = payload["study_id"]
        candidate_root = root / "versions" / job
        candidate_root.mkdir(parents=True, exist_ok=False)
        run_refresh(candidate_root, previous, shanghai_now(), lambda *_: None)
        candidate = FundScenarioStudy(candidate_root / "study", now=shanghai_now)
        updated = candidate.read()
        if not updated.get("available") or {f["symbol"] for f in updated["funds"]} != {
            f["symbol"] for f in previous["funds"]
        }:
            raise ValueError("study_scope_changed")
        # Rules are study-wide (run_refresh requires every fund to carry the
        # default ScenarioRules), but compare per fund by symbol rather than
        # against previous["funds"][0]: the old comparison was only correct
        # while all funds happened to share identical rules.
        previous_rules = {fund["symbol"]: fund["rules"] for fund in previous["funds"]}
        for fund in updated["funds"]:
            if fund["rules"] != previous_rules[fund["symbol"]]:
                raise ValueError("study_rules_changed")
            for period in ("full", "early", "late"):
                for rate in ("0", "0.0012", "0.012"):
                    if not candidate.curves(fund["symbol"], period, rate, updated["study_version"])[
                        "available"
                    ]:
                        raise ValueError("study_curves_invalid")
        return {
            "status": "completed",
            "model_calls": 0,
            "message": "研究已更新并另存新版本。",
            "pointer": {
                "version": job,
                "manifest_sha256": hashlib.sha256(
                    (candidate_root / "study" / "manifest.json").read_bytes()
                ).hexdigest(),
            },
        }
