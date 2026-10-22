"""Report evidence checks without model credentials, billing or report publication."""

import os
from pathlib import Path
from types import SimpleNamespace

from analysis_context import analytical_context, shanghai_now

from quant_assistant.analysis.research_evidence import ResearchEvidence
from quant_assistant.services.advisor_evidence import AdvisorEvidence
from quant_assistant.services.preferences import PreferenceSnapshot
from quant_assistant.services.strategy_settings import StrategySettingsService
from quant_assistant.services.watchlist import WatchlistEntry


def evidence_context(database, queries, directory, payload, now, *, persist=True):
    manifest = (
        Path(os.environ["QUANT_AGENT_EVIDENCE_MANIFEST"])
        if os.environ.get("QUANT_AGENT_EVIDENCE_MANIFEST")
        else directory / "research-evidence.json"
    )
    research = ResearchEvidence(database, now, manifest)
    if not persist:
        original = research
        research = SimpleNamespace(
            freeze=lambda *args, **kwargs: original.freeze(*args, **kwargs, persist=False)
        )
    strategy = StrategySettingsService(
        Path("unused-native-settings"), database, clock=now, research_evidence=research
    )
    strategy.load = lambda: payload["strategy"]
    preferences = SimpleNamespace(
        load=lambda: PreferenceSnapshot(
            tuple(WatchlistEntry(**entry) for entry in payload["preferences"].get("watchlist", [])),
            (),
        )
    )
    return AdvisorEvidence(database, queries, strategy, preferences)


def issue(message, symbol=None):
    code, label, url = "evidence", "查看数据状态", "#/pages/settings/index"
    if "资讯" in message:
        code, label, url = "news", "查看资讯", "#/pages/news/index"
    elif "指数行情" in message:
        code, label, url = "market", "查看行情", "#/pages/market/index"
    elif "基线" in message or "截图" in message or "持仓" in message:
        code, label, url = "portfolio", "核对账户持仓", "#/pages/positions/index"
    elif "费率" in message or "批次" in message or "净值" in message or "申购" in message:
        code, label, url = "fund_evidence", "核对基金证据", "#/pages/positions/index"
        if symbol:
            url = "#/pages/fund-detail/index?symbol=" + symbol
    elif "完整策略回放" in message:
        code, label, url = "fund_replay", "查看执行回放", "#/pages/fund-review/index"
    elif "筛选" in message or "候选" in message or "历史" in message:
        code, label, url = "strategy", "检查研究资料", "#/pages/ai-stock/index"
    return {"code": code, "message": message, "symbol": symbol, "label": label, "url": url}


def execute_preflight(payload):
    kind = payload["report_kind"]
    if kind not in ("morning", "afternoon"):
        raise ValueError("invalid report kind")
    with analytical_context(payload["snapshot"]) as (database, queries, directory):
        facts = evidence_context(
            database, queries, directory, payload, shanghai_now, persist=False
        ).build(kind, shanghai_now())
        issues = [issue(message) for message in dict.fromkeys(facts["blockers"])]
        global_messages = {i["message"] for i in issues}
        for symbol, gates in facts["symbol_gates"].items():
            issues.extend(
                issue(message, symbol)
                for message in dict.fromkeys(gates)
                if message not in global_messages
            )
        return {
            "decision_ready": bool(facts["symbols"]) and not issues,
            "issues": issues,
            "symbols": facts["symbols"],
            "source_count": len(facts["sources"]),
            "observed_at": facts["observed_at"],
            "research_only": True,
            "model_calls": 0,
        }
