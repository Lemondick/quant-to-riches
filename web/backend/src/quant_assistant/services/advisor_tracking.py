"""Frozen original decisions and gross follow-up observations, never trading returns."""

import json
import re
from datetime import datetime
from decimal import Decimal

from quant_assistant.services.fund_research_store import (
    PUBLIC_SOURCES,
    day,
    encode,
    fingerprint,
    persist_public,
    positive,
)

VERSION = "advisor-observation-v1"
HORIZONS = (5, 20, 60)


def failure_stage(raw):
    progress = json.loads(raw) if raw else {}
    if not isinstance(progress, dict):
        return None
    stages = progress.get("stages")
    if not isinstance(stages, list):
        return None
    failed = [s for s in stages if isinstance(s, dict) and s.get("status") == "failed"]
    if len(failed) != 1 or failed[0].get("name") not in {
        "evidence",
        "news",
        "strategy",
        "reviewer",
        "decision",
        "finalize",
    }:
        return None
    return {
        "name": failed[0]["name"],
        "timeout_phase": failed[0].get("timeout_phase")
        if failed[0].get("timeout_phase") in ("connect", "read")
        else None,
    }


def freeze_report(connection, report_id, captured_at):
    row = connection.execute(
        "SELECT kind,status,created_at,input_json,report_json,model_config_json,error_code "
        "FROM market_advisor_reports WHERE report_id=?",
        [report_id],
    ).fetchone()
    if row is None or row[1] not in ("completed", "blocked", "failed"):
        return
    snapshot = {
        "version": VERSION,
        "report_id": report_id,
        "kind": row[0],
        "status": row[1],
        "created_at": str(row[2]),
        "input": json.loads(row[3]) if row[3] else None,
        "report": json.loads(row[4]) if row[4] else None,
        "models": json.loads(row[5]) if row[5] else None,
        "error_code": row[6],
    }
    raw = encode(snapshot)
    connection.execute(
        "INSERT INTO advisor_decision_snapshots VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
        [report_id, captured_at, raw, fingerprint(raw)],
    )
    for source in (snapshot["input"] or {}).get("sources", []):
        if not source.get("id", "").startswith("fund:"):
            continue
        try:
            public = source["data"]
            if source["id"] != "fund:" + public["symbol"]:
                raise ValueError("fund source symbol mismatch")
            persist_public(connection, public, captured_at)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            pass  # Missing public evidence remains unavailable; never fabricate it.


class AdvisorTrackingService:
    def __init__(self, database, store):
        self.database, self.store = database, store

    def read(self, limit=30, page=1):
        if not 1 <= limit <= 50 or not 1 <= page <= 10000:
            raise ValueError("invalid tracking pagination")
        with self.database.connection() as connection:
            total = connection.execute(
                "SELECT count(*) FROM market_advisor_reports WHERE kind='afternoon'"
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT r.report_id,r.kind,r.status,r.created_at,r.input_json,r.report_json,"
                "r.model_config_json,r.error_code,s.captured_at,s.snapshot_json,s.fingerprint,"
                "r.progress_json "
                "FROM market_advisor_reports r "
                "LEFT JOIN advisor_decision_snapshots s USING(report_id) "
                "WHERE r.kind='afternoon' "
                "ORDER BY r.created_at DESC,r.report_id DESC LIMIT ? OFFSET ?",
                [limit, (page - 1) * limit],
            ).fetchall()
        reports = []
        for row in rows:
            snapshot = {
                "version": VERSION,
                "report_id": row[0],
                "kind": row[1],
                "status": row[2],
                "created_at": str(row[3]),
                "input": json.loads(row[4]) if row[4] else None,
                "report": json.loads(row[5]) if row[5] else None,
                "models": json.loads(row[6]) if row[6] else None,
                "error_code": row[7],
            }
            sealed = row[9] is not None
            valid = not sealed or (fingerprint(row[9]) == row[10] and encode(snapshot) == row[9])
            view = {
                "report_id": row[0],
                "kind": row[1],
                "status": row[2],
                "created_at": str(row[3]),
                "sealed": sealed and valid,
                "integrity": "valid" if valid else "failed",
                "fingerprint": row[10] if valid and sealed else None,
                "captured_at": str(row[8]) if sealed else None,
                "error_code": row[7],
                "items": [],
                "failure": failure_stage(row[11]) if valid and row[2] == "failed" else None,
                "blockers": (snapshot["report"] or {}).get("blockers", []) if valid else [],
                "message": "" if sealed else "旧报告未在生成时冻结，只保留历史查阅",
            }
            if not valid:
                view["message"] = "冻结证据校验失败，隐藏后续结果"
            elif row[1] == "afternoon":
                for decision in (snapshot["report"] or {}).get("decisions", []):
                    if re.fullmatch(r"\d{6}", decision.get("symbol", "")):
                        view["items"].append(self._decision(snapshot, decision, sealed))
            reports.append(view)
        return {
            "items": reports,
            "total": total,
            "page": page,
            "limit": limit,
            "checked_at": self.store.now().isoformat(),
            "research_only": True,
            "qualifies_paper_days": False,
            "ready_for_trading": False,
            "method": "原始判断保持不变；5/20/60条后续正式净值变化，不含个人交易费用。",
        }

    def _decision(self, snapshot, decision, sealed):
        symbol = decision["symbol"]
        sources = (snapshot["input"] or {}).get("sources", [])
        public = next((s.get("data") for s in sources if s["id"] == "fund:" + symbol), None)
        result = {
            "symbol": symbol,
            "name": public.get("name", symbol) if public else symbol,
            "action": decision["action"],
            "jev_choice": decision.get("jev_choice"),
            "blocked": bool(decision.get("blockers")) or decision["action"] == "watch",
            "blockers": decision.get("blockers", []),
            "reference": None,
            "horizons": [],
            "available_records": None,
            "latest_nav_date": None,
            "net_return": None,
            "net_blockers": ["完整执行回放尚未验收，不能计算扣费后策略收益"],
        }
        if not sealed:
            result["observation_message"] = "旧报告未冻结，不参与效果统计"
            return result
        try:
            observed = datetime.fromisoformat(snapshot["input"]["observed_at"])
            if not public or public["stale"] or public["source"] not in PUBLIC_SOURCES:
                raise ValueError("missing NAV reference")
            public_at = datetime.fromisoformat(public["observed_at"])
            if public_at > observed or (observed - public_at).total_seconds() > 86400:
                raise ValueError("reference not known at decision")
            points = public["nav_history"]
            if len({p["nav_date"] for p in points}) != len(points):
                raise ValueError("duplicate reference date")
            eligible = [p for p in points if day(p["nav_date"]) <= observed.date()]
            base = max(eligible, key=lambda p: p["nav_date"])
            if (
                base["source"] not in PUBLIC_SOURCES
                or not 0 <= (observed.date() - day(base["nav_date"])).days <= 7
            ):
                raise ValueError("stale reference")
            price = positive(base["unit_nav"])
            accumulated = (
                positive(base["accumulated_nav"])
                if base.get("accumulated_nav") is not None
                else None
            )
            result["reference"] = {"nav_date": base["nav_date"], "unit_nav": str(price)}
            history = self.store.navs(symbol)
            for nav_day, values in history:
                if day(nav_day) == day(base["nav_date"]) and (
                    values[0] != price or values[1] != accumulated
                ):
                    raise ValueError("frozen NAV reference differs from public revision")
            future = [(d, values) for d, values in history if day(d) > day(base["nav_date"])]
            result["available_records"] = len(future)
            result["latest_nav_date"] = history[-1][0] if history else base["nav_date"]
            announcements = [
                a
                for observation in self.store.snapshots(symbol)
                for a in observation.get("announcements", [])
            ] + public.get("announcements", [])
            for horizon in HORIZONS:
                follow = {
                    "records": horizon,
                    "available_records": min(horizon, len(future)),
                    "remaining_records": max(0, horizon - len(future)),
                    "status": "pending",
                    "gross_nav_change": None,
                    "max_drawdown": None,
                }
                if len(future) >= horizon:
                    segment = future[:horizon]
                    action_notice = any(
                        any(word in a["title"] for word in ("分红", "拆分", "折算"))
                        and (day(segment[-1][0]) - day(a["published_at"])).days >= 0
                        and (day(base["nav_date"]) - day(a["published_at"])).days <= 30
                        for a in announcements
                    )
                    if self._corporate_action_uncertain(base, segment) or action_notice:
                        follow.update(
                            status="blocked", message="分红、折算或累计净值缺失，需总收益核对"
                        )
                    else:
                        prices = [price, *(v[0] for _, v in segment)]
                        peak, drawdown = price, Decimal(0)
                        for value in prices:
                            peak = max(peak, value)
                            drawdown = max(drawdown, 1 - value / peak)
                        follow.update(
                            status="observed",
                            nav_date=segment[-1][0],
                            gross_nav_change=float(prices[-1] / price - 1),
                            max_drawdown=float(drawdown),
                        )
                result["horizons"].append(follow)
            result["observation_message"] = (
                "净值变化仅供观察；被阻断的建议没有执行"
                if result["blocked"]
                else "净值变化不代表成交或扣费收益"
            )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            result.update(
                reference=None,
                horizons=[],
                available_records=None,
                latest_nav_date=None,
                observation_message="参考净值缺失、过期或证据冲突，暂不能评估",
            )
        return result

    @staticmethod
    def _corporate_action_uncertain(base, segment):
        if base.get("accumulated_nav") is None or any(v[1] is None for _, v in segment):
            return True
        gap = Decimal(str(base["accumulated_nav"])) - positive(base["unit_nav"])
        return any(
            abs(Decimal(str(values[1])) - values[0] - gap) > Decimal("0.000001")
            for _, values in segment
        )
