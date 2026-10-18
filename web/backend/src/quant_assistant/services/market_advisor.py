"""Daily research reports: three bounded models, Jev verdict, local vetoes."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from time import monotonic
from uuid import uuid4

from quant_assistant.analysis.advisor_config import AdvisorSettings
from quant_assistant.analysis.advisor_contract import role_instructions, validate_role_report
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.advisor_models import AdvisorModelError, AdvisorModels
from quant_assistant.services.advisor_jev import JevConnection, JevInput
from quant_assistant.services.advisor_model_settings import (
    AdvisorSettingsConflict,
    SaveAdvisorModels,
    load_model_selection,
)
from quant_assistant.services.advisor_provider import (
    AdvisorProvider,
    ProviderDiscoveryError,
    ProviderInput,
)
from quant_assistant.services.advisor_scheduler import AdvisorScheduler
from quant_assistant.services.advisor_tracking import freeze_report
from quant_assistant.services.model_costs import CostLimitError


class AdvisorBusyError(ValueError):
    """A report is already consuming the shared model budget."""


class MarketAdvisorService:
    def __init__(self, database, evidence, *, settings=None, models=None, now=None):
        self.database, self.evidence = database, evidence
        self.settings = settings or AdvisorSettings.from_file()
        self.models = models or AdvisorModels(self.settings)
        self.now = now or (lambda: datetime.now(SHANGHAI_TZ))
        from quant_assistant.services.model_costs import ModelCostService

        self.costs = ModelCostService(database, self.now)
        self.costs.recover_pending()
        if isinstance(self.models, AdvisorModels):
            self.models.costs = self.costs
            self.models.cancelled = lambda: not getattr(self, "account_enabled", lambda: True)()
        self.provider = AdvisorProvider(database.path.parent / "server-secrets")
        self.jev = JevConnection(database.path.parent / "server-secrets")
        self.research = None
        self._apply_provider()
        self.scheduler = AdvisorScheduler(self, now=self.now)

    def _apply_provider(self):
        saved = self.provider.load()
        if saved["endpoint"]:
            self.settings = replace(
                self.settings,
                enabled=True,
                endpoint=saved["endpoint"],
                key_file=self.provider.key_file,
            )
        if self.jev.load()["revision"]:
            self.settings = replace(self.settings, jev_key_file=self.jev.key_file)
        if isinstance(self.models, AdvisorModels):
            self.models.settings = self.settings

    def save_jev_connection(self, body):
        body = JevInput.model_validate(body)
        with self.database.transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM market_advisor_reports "
                    "WHERE status IN ('queued','running') LIMIT 1"
                ).fetchone()
                or connection.execute(
                    "SELECT 1 FROM agent_research_jobs "
                    "WHERE status IN ('queued','running','cancel_requested') LIMIT 1"
                ).fetchone()
            ):
                raise AdvisorBusyError("已有研究或报告正在执行，请等待完成后修改连接")
            self.jev.save(body)
            self._apply_provider()
        return self.model_settings()

    def discover_models(self, body):
        body = ProviderInput.model_validate(body)
        with self.database.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM market_advisor_reports WHERE status IN ('queued','running') LIMIT 1"
            ).fetchone():
                raise AdvisorBusyError("已有AI报告正在执行，请等待完成后再修改连接")
            if connection.execute(
                "SELECT 1 FROM agent_research_jobs "
                "WHERE status IN ('queued','running','cancel_requested') LIMIT 1"
            ).fetchone():
                raise AdvisorBusyError("已有TradingAgents研究正在执行，请等待完成")
            if not body.api_key.get_secret_value().strip() and body.endpoint.rstrip(
                "/"
            ) != self.settings.endpoint.rstrip("/"):
                raise ProviderDiscoveryError("更换API地址时必须提供对应API Key")
            self.provider.discover(body, fallback_key=self.settings.key_file)
            self._apply_provider()
            models, revision = load_model_selection(self.database, self.settings.public()["models"])
            connection.execute(
                "INSERT INTO advisor_model_settings VALUES (1,?,?,?) "
                "ON CONFLICT (id) DO UPDATE SET revision=excluded.revision, "
                "updated_at=excluded.updated_at",
                [json.dumps(models), revision + 1, self.now()],
            )
            if self.research is not None:
                self.research.settings = replace(self.research.settings, enabled=False)
        return self.model_settings()

    def configure_research(self):
        saved = self.provider.load()
        selected = self.model_settings()["models"]["strategy"]
        if (
            self.research is not None
            and self._provider_selected()
            and saved["endpoint"]
            and selected in saved["models"]
        ):
            self.research.configure_provider(
                self.settings.endpoint, selected, self.settings.key_file
            )

    def _provider_selected(self):
        revision = self.provider.load()["revision"]
        if not revision:
            return True
        rows = self.database.query_df(
            "SELECT provider_revision FROM advisor_provider_selection WHERE id=1"
        )
        return not rows.empty and int(rows.iloc[0, 0]) == revision

    def public_config(self):
        return {
            **self.settings.public(),
            "configured": self.settings.public()["configured"] and self._provider_selected(),
            "models": self.model_settings()["models"],
            "instance_id": hashlib.sha256(str(self.database.path.resolve()).encode()).hexdigest()[
                :16
            ],
        }

    def model_settings(self, *, recover=False):
        if recover:
            self._recover_stalled()
        defaults = self.settings.public()["models"]
        models, revision = load_model_selection(self.database, defaults)
        active = self.database.query_df(
            "SELECT (SELECT count(*) FROM market_advisor_reports "
            "WHERE status IN ('queued','running')) + "
            "(SELECT count(*) FROM agent_research_jobs "
            "WHERE status IN ('queued','running','cancel_requested')) AS n"
        ).iloc[0, 0]
        return {
            "models": models,
            "defaults": defaults,
            "revision": revision,
            "active": bool(active),
            "configured": self.settings.public()["configured"] and self._provider_selected(),
            "provider": self.provider.public(
                endpoint=self.settings.endpoint, key_file=self.settings.key_file
            ),
            "jev": self.jev.public(key_file=self.settings.jev_key_file),
        }

    def save_model_settings(self, body):
        with self.database.connection():
            return self._save_model_settings(body)

    def _save_model_settings(self, body):
        body = SaveAdvisorModels.model_validate(body)
        self._recover_stalled()
        with self.database.transaction() as connection:
            available = self.provider.load()["models"]
            if available and any(
                getattr(body.models, role) not in available
                for role in ("news", "strategy", "reviewer")
            ):
                raise ValueError("请选择该API返回的可用模型")
            if connection.execute(
                "SELECT 1 FROM market_advisor_reports WHERE status IN ('queued','running') LIMIT 1"
            ).fetchone():
                raise AdvisorBusyError("已有AI报告正在执行，请等待完成后再修改模型")
            if connection.execute(
                "SELECT 1 FROM agent_research_jobs "
                "WHERE status IN ('queued','running','cancel_requested') LIMIT 1"
            ).fetchone():
                raise AdvisorBusyError("已有TradingAgents研究正在执行，请等待完成")
            current = connection.execute(
                "SELECT revision FROM advisor_model_settings WHERE id=1"
            ).fetchone()
            if body.revision != (current[0] if current else 0):
                raise AdvisorSettingsConflict("模型配置已变化，请重新读取后修改")
            connection.execute(
                """INSERT INTO advisor_model_settings VALUES (1,?,?,?)
                   ON CONFLICT (id) DO UPDATE SET models_json=excluded.models_json,
                   revision=excluded.revision, updated_at=excluded.updated_at""",
                [body.models.model_dump_json(), body.revision + 1, self.now()],
            )
            if available:
                connection.execute(
                    "INSERT INTO advisor_provider_selection VALUES (1,?) "
                    "ON CONFLICT (id) DO UPDATE SET provider_revision=excluded.provider_revision",
                    [self.provider.load()["revision"]],
                )
        self.configure_research()
        return self.model_settings()

    def list(self, limit=20, *, kind=None, report_date=None, page=1):
        self._recover_stalled()
        if kind not in (None, "morning", "afternoon") or not 1 <= limit <= 100 or page < 1:
            raise ValueError("报告查询条件无效")
        clauses, values = [], []
        if kind:
            clauses.append("kind=?")
            values.append(kind)
        if report_date:
            clauses.append("report_date=?")
            values.append(report_date)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(
            self.database.query_df(
                "SELECT count(*) AS n FROM market_advisor_reports" + where, values
            ).iloc[0, 0]
        )
        rows = self.database.query_df(
            "SELECT * FROM market_advisor_reports"
            + where
            + " ORDER BY created_at DESC, report_id DESC LIMIT ? OFFSET ?",
            [*values, limit, (page - 1) * limit],
        )
        active = self.database.query_df(
            "SELECT report_id,kind,status FROM market_advisor_reports "
            "WHERE status IN ('queued','running') ORDER BY created_at DESC"
        )
        today = self.now().astimezone(SHANGHAI_TZ).date()
        used = int(
            self.database.query_df(
                "SELECT count(*) AS n FROM market_advisor_reports WHERE report_date=?",
                [today],
            ).iloc[0, 0]
        )
        return {
            "items": [self._view(row) for row in rows.to_dict("records")],
            "config": self.public_config(),
            "total": total,
            "page": page,
            "limit": limit,
            "active_reports": active.to_dict("records"),
            "usage": {
                "date": today.isoformat(),
                "used": used,
                "limit": self.settings.daily_limit,
                "remaining": max(0, self.settings.daily_limit - used),
            },
        }

    def get(self, report_id):
        self._recover_stalled()
        rows = self.database.query_df(
            "SELECT * FROM market_advisor_reports WHERE report_id=?", [report_id]
        )
        if rows.empty:
            raise ValueError("未找到AI报告")
        return self._view(rows.iloc[0].to_dict())

    def _view(self, row):
        def decode(value):
            return json.loads(value) if isinstance(value, str) and value else None

        report = decode(row.get("report_json"))
        expired = bool(
            report
            and report.get("expires_at")
            and datetime.fromisoformat(report["expires_at"]) <= self.now()
        )
        if expired:
            for decision in report.get("decisions", []):
                decision["action"] = "watch"
                decision["blockers"].append("报告已过期，当前只能查阅历史观点")
        return {
            "report_id": row["report_id"],
            "report_date": row["report_date"].date().isoformat()
            if isinstance(row["report_date"], datetime)
            else str(row["report_date"]),
            "kind": row["kind"],
            "status": row["status"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
            "report": report,
            "progress": decode(row.get("progress_json")),
            "models": decode(row.get("model_config_json")),
            "expired": expired,
            "error_code": row.get("error_code"),
            "research_only": True,
            "automatic_trading": False,
        }

    def _recover_stalled(self):
        with self.database.transaction() as connection:
            now = self.now()
            stalled = connection.execute(
                """SELECT report_id, progress_json FROM market_advisor_reports
                   WHERE status IN ('queued','running') AND updated_at<?""",
                [now - timedelta(minutes=10)],
            ).fetchall()
            for report_id, raw in stalled:
                progress = json.loads(raw) if raw else None
                for stage in progress.get("stages", []) if progress else []:
                    if stage["status"] == "running":
                        stage.update(
                            status="failed", error_code="interrupted", finished_at=now.isoformat()
                        )
                        try:
                            elapsed = now - datetime.fromisoformat(stage.get("started_at", ""))
                            stage.update(
                                elapsed_ms=max(0, round(elapsed.total_seconds() * 1000)),
                                elapsed_estimated=True,
                            )
                        except (TypeError, ValueError):
                            pass  # Legacy stages may lack a usable start time.
                connection.execute(
                    """UPDATE market_advisor_reports SET status='failed',
                       error_code='interrupted', progress_json=?, updated_at=? WHERE report_id=?""",
                    [json.dumps(progress) if progress else None, now, report_id],
                )
                freeze_report(connection, report_id, now)

    def _save_progress(self, report_id, progress):
        with self.database.transaction() as connection:
            return connection.execute(
                """UPDATE market_advisor_reports SET progress_json=?, updated_at=?
                   WHERE report_id=? AND status='running' RETURNING report_id""",
                [json.dumps(progress, ensure_ascii=False), self.now(), report_id],
            ).fetchone()

    @contextmanager
    def _stage(self, report_id, progress, name):
        if not getattr(self, "account_enabled", lambda: True)():
            raise AdvisorModelError("interrupted")
        stage = next(item for item in progress["stages"] if item["name"] == name)
        progress["current_stage"] = name
        stage.update(status="running", started_at=self.now().isoformat())
        if not self._save_progress(report_id, progress):
            raise AdvisorModelError("interrupted")
        started = monotonic()
        try:
            yield
        except Exception as error:
            stage.update(
                status="failed",
                error_code=str(error) if isinstance(error, AdvisorModelError) else "invalid_output",
                timeout_phase=error.timeout_phase if isinstance(error, AdvisorModelError) else None,
                validation_reason=error.validation_reason
                if isinstance(error, AdvisorModelError)
                else None,
            )
            raise
        else:
            stage["status"] = "completed"
        finally:
            stage.update(
                finished_at=self.now().isoformat(),
                elapsed_ms=max(0, round((monotonic() - started) * 1000)),
            )
            # A recovered task owns its terminal record; a late response must not revive it.
            if not self._save_progress(report_id, progress):
                raise AdvisorModelError("interrupted")

    def create(self, kind, *, force=False):
        if kind not in ("morning", "afternoon"):
            raise ValueError("报告类型无效")
        self._recover_stalled()
        now = self.now().astimezone(SHANGHAI_TZ)
        with self.database.transaction() as connection:
            if not self.public_config()["configured"]:
                raise ValueError("模型或Jev服务尚未配置")
            old = connection.execute(
                """SELECT report_id, status FROM market_advisor_reports
                   WHERE report_date=? AND kind=? ORDER BY created_at DESC LIMIT 1""",
                [now.date(), kind],
            ).fetchone()
            if old and (not force or old[1] in ("queued", "running")):
                report_id = old[0]
            else:
                active = connection.execute(
                    "SELECT 1 FROM market_advisor_reports "
                    "WHERE status IN ('queued','running') LIMIT 1"
                ).fetchone()
                if active:
                    raise AdvisorBusyError("已有AI报告正在执行，请等待完成后再创建新任务")
                count = connection.execute(
                    "SELECT count(*) FROM market_advisor_reports WHERE report_date=?", [now.date()]
                ).fetchone()[0]
                if count >= self.settings.daily_limit:
                    raise ValueError("今日AI报告额度已用完，失败和重新生成也计入额度")
                report_id = uuid4().hex
                connection.execute(
                    """INSERT INTO market_advisor_reports
                       (report_id,report_date,kind,status,created_at,updated_at,model_config_json)
                       VALUES (?,?,?,'queued',?,?,?)""",
                    [
                        report_id,
                        now.date(),
                        kind,
                        now,
                        now,
                        json.dumps(self.model_settings()["models"]),
                    ],
                )
        return self.get(report_id)

    def execute(self, report_id):
        gate = getattr(self, "job_gate", None)
        allowed = getattr(self, "account_enabled", lambda: True)
        if gate is None:
            return self._execute(report_id) if allowed() else self._interrupt_queued(report_id)
        with gate.acquire(allowed, wait=True) as acquired:
            return self._execute(report_id) if acquired else self._interrupt_queued(report_id)

    def _interrupt_queued(self, report_id):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE market_advisor_reports SET status='failed',"
                "error_code='interrupted',updated_at=? WHERE report_id=? AND status='queued'",
                [self.now(), report_id],
            )
        return self.get(report_id)

    def _execute(self, report_id):
        progress = {
            "current_stage": None,
            "stages": [
                {"name": name, "status": "pending"}
                for name in ("evidence", "news", "strategy", "reviewer", "decision", "finalize")
            ],
        }
        with self.database.transaction() as connection:
            claimed = connection.execute(
                """UPDATE market_advisor_reports SET status='running', updated_at=?, progress_json=?
                   WHERE report_id=? AND status='queued' RETURNING kind, model_config_json""",
                [self.now(), json.dumps(progress), report_id],
            ).fetchone()
        if not claimed:
            return self.get(report_id)
        selected_models = json.loads(claimed[1]) if claimed[1] else self.model_settings()["models"]
        try:
            with self._stage(report_id, progress, "evidence"):
                state = json.loads(
                    json.dumps(
                        self.evidence.build(claimed[0], self.now()),
                        default=str,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                )
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE market_advisor_reports SET input_json=?, updated_at=?
                       WHERE report_id=?""",
                    [json.dumps(state, ensure_ascii=False), self.now(), report_id],
                )
            if len(state["sources"]) == 1 and state["sources"][0]["id"] == "availability":
                self._finish(
                    report_id,
                    "blocked",
                    {
                        "blockers": state["blockers"],
                        "sources": state["sources"],
                        "decisions": [],
                        "research_only": True,
                    },
                )
                return self.get(report_id)
            previous = {}
            for role, model in (
                ("news", selected_models["news"]),
                ("strategy", selected_models["strategy"]),
                ("reviewer", selected_models["reviewer"]),
            ):
                with self._stage(report_id, progress, role):
                    if isinstance(self.models, AdvisorModels):
                        self.models.call_role = role
                    raw = self.models.chat(
                        model,
                        role_instructions(role, claimed[0]),
                        {"input": state, "previous": previous},
                    )
                    previous[role] = validate_role_report(raw, state)
            questions = {
                "evidence": {
                    "type": "noul",
                    "instructions": (
                        "Is the evidence for all recommendations sufficient, fresh and consistent? "
                        "Missing data or hard blockers means no. "
                        "Confidence is not a profit probability."
                    ),
                }
            }
            choices = (
                {
                    "recommend": "Research shortlist only, evidence and both analysts agree",
                    "watch": "Missing evidence, disagreement or uncertainty",
                    "exclude": "Evidence supports excluding this stock",
                }
                if claimed[0] == "morning"
                else {
                    "buy": "Evidence supports researching a purchase and all local gates pass",
                    "sell": "Confirmed owned batches support a redemption and all local gates pass",
                    "hold": "Evidence supports keeping the confirmed position",
                    "watch": "Missing evidence, unclear costs, holiday/stale data or disagreement",
                }
            )
            for index, symbol in enumerate(state["symbols"]):
                questions[f"candidate_{index}"] = {
                    "type": "choice",
                    "instructions": (
                        f"Evaluate {symbol} using its sources, symbol_gates and all three reports. "
                        "Never override hard gates."
                    ),
                    "criteria": choices,
                }
            with self._stage(report_id, progress, "decision"):
                verdict = self.models.decide(
                    {"input": state, "reports": previous},
                    questions,
                    model=selected_models["decision"],
                )
            with self._stage(report_id, progress, "finalize"):
                decisions = []
                sol = {r["symbol"]: r["action"] for r in previous["strategy"]["recommendations"]}
                astra = {r["symbol"]: r["action"] for r in previous["reviewer"]["recommendations"]}
                for index, symbol in enumerate(state["symbols"]):
                    answer = verdict["answers"][f"candidate_{index}"]
                    gates = list(
                        dict.fromkeys([*state["blockers"], *state["symbol_gates"].get(symbol, [])])
                    )
                    if (
                        claimed[0] == "afternoon"
                        and answer["choice"] == "sell"
                        and symbol not in state.get("held_symbols", [])
                    ):
                        gates.append("本地持仓快照未持有该基金，不能给出赎回建议")
                    confidence = min(
                        answer["confidence"], answer["probabilities"][answer["choice"]]
                    )
                    if confidence < self.settings.confidence_threshold:
                        gates.append("Jev判断置信度不足，需要人工复核")
                    if verdict["answers"]["evidence"]["noul"] < self.settings.confidence_threshold:
                        gates.append("Jev判定整体证据不足，需要补齐资料")
                    if answer["choice"] != sol.get(symbol) or answer["choice"] != astra.get(symbol):
                        gates.append("策略模型、复核模型与Jev观点未一致")
                    decisions.append(
                        {
                            "symbol": symbol,
                            "action": "watch" if gates else answer["choice"],
                            "jev_choice": answer["choice"],
                            "confidence": confidence,
                            "probabilities": answer["probabilities"],
                            "blockers": gates,
                            "research_only": True,
                        }
                    )
                report = {
                    "models": selected_models,
                    "roles": previous,
                    "jev": verdict,
                    "decisions": decisions,
                    "sources": state["sources"],
                    "blockers": state["blockers"],
                    "observed_at": state["observed_at"],
                    "expires_at": (
                        datetime.fromisoformat(state["observed_at"]) + timedelta(minutes=15)
                    ).isoformat(),
                    "research_only": True,
                    "strategy_validated": False,
                }
            self._finish(
                report_id,
                "blocked"
                if state["blockers"] or any(d["blockers"] for d in decisions)
                else "completed",
                report,
            )
        except AdvisorModelError as error:
            self._finish(report_id, "failed", error_code=str(error))
        except CostLimitError:
            self._finish(report_id, "failed", error_code="model_budget_exceeded")
        except Exception:
            self._finish(report_id, "failed", error_code="invalid_output")
        return self.get(report_id)

    def _finish(self, report_id, status, report=None, error_code=None):
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE market_advisor_reports SET status=?, report_json=?,
                   error_code=?, updated_at=? WHERE report_id=? AND status='running'""",
                [
                    status,
                    json.dumps(report, ensure_ascii=False) if report else None,
                    error_code,
                    self.now(),
                    report_id,
                ],
            )
            freeze_report(connection, report_id, self.now())
