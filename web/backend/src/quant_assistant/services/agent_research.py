"""Opt-in single-consumer research, independent of signals and proposed orders."""

from dataclasses import replace
from datetime import datetime, timedelta
from threading import Event, Thread

from quant_assistant.analysis.research_models import (
    ADAPTER_VERSION,
    ERRORS,
    PROMPT_VERSION,
    UPSTREAM_COMMIT,
    ResearchError,
    fingerprint,
)
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.integrations.tradingagents_worker.contract import validate_report
from quant_assistant.integrations.tradingagents_worker.process import (
    ProcessFailure,
    SubprocessWorker,
)
from quant_assistant.models.repositories.agent_research import ResearchRepository
from quant_assistant.services.model_costs import CostLimitError, ModelCostService
from quant_assistant.services.strategy_settings import StrategyParameters


class AgentResearchService:
    def __init__(self, database, settings, evidence, previews, clock=None, runner=None):
        self.settings, self.evidence, self.previews = settings, evidence, previews
        source_clock = clock or (lambda: datetime.now(SHANGHAI_TZ))
        self.clock = lambda: source_clock().astimezone(SHANGHAI_TZ)
        self.repository = ResearchRepository(database, self.clock)
        self.costs = ModelCostService(database, self.clock)
        self.runner = runner or SubprocessWorker(settings.python_executable, settings.key_file)
        if runner is None and settings.transport == "exchange":
            from quant_assistant.integrations.tradingagents_worker.exchange import ExchangeWorker

            self.runner = ExchangeWorker(settings.exchange_dir)
        self.stopping, self.wakeup = Event(), Event()
        self.thread = None
        self.job_gate = None
        self.account_enabled = lambda: True

    def start(self):
        if not self.settings.enabled or self.thread is not None:
            return
        self.repository.recover()
        self.stopping.clear()
        self.thread = Thread(target=self._loop, name="agent-research", daemon=True)
        self.thread.start()

    def configure_provider(self, endpoint, model, key_file):
        self.settings = replace(
            self.settings,
            enabled=True,
            provider="compatible",
            endpoint=endpoint,
            model=model,
            key_file=key_file,
        )
        if isinstance(self.runner, SubprocessWorker):
            self.runner.key_file = key_file
        self.previews.research_evidence = self.evidence
        self.start()

    def stop(self):
        if self.thread is None:
            return True
        self.stopping.set()
        self.wakeup.set()
        self.thread.join(timeout=self.settings.timeout_seconds + 10)
        if self.thread.is_alive():
            return False
        self.repository.recover()
        self.thread = None
        return True

    def _enabled(self):
        if not self.settings.enabled:
            raise ResearchError("research_disabled", 503)

    def _available(self):
        self._enabled()
        if self.thread is None or not self.thread.is_alive() or self.stopping.is_set():
            raise ResearchError("worker_unavailable", 503)
        if self.settings.provider == "compatible" and (
            not self.settings.endpoint
            or not self.settings.model
            or (
                self.settings.transport == "subprocess"
                and (self.settings.key_file is None or not self.settings.key_file.is_file())
            )
        ):
            raise ResearchError("model_not_configured", 503)
        if hasattr(self.runner, "check_available"):
            try:
                self.runner.check_available(self.settings.model_config())
            except ProcessFailure as error:
                raise ResearchError("worker_unavailable", 503) from error

    def _snapshot(self, snapshot_id, symbol):
        snapshot = self.evidence.get(snapshot_id)
        payload = snapshot["payload"]
        if snapshot["expires_at"] <= self.clock():
            raise ResearchError("preview_expired")
        if symbol not in payload["candidates"]:
            raise ResearchError("not_candidate", 422)
        if payload["blockers"]:
            raise ResearchError("ineligible_evidence", 422)
        current = self.previews.research_fingerprint(StrategyParameters(**payload["parameters"]))
        if current != snapshot["content_hash"]:
            raise ResearchError("preview_changed")
        return snapshot

    def create(self, snapshot_id, symbol, *, retry_id=None):
        # Serialize availability, configuration freeze and enqueue with BYOK updates.
        with self.repository.database.connection():
            self._available()
            snapshot = self._snapshot(snapshot_id, symbol)
            cache_key = fingerprint(
                {
                    "input": snapshot["content_hash"],
                    "symbol": symbol,
                    "config": self.settings.model_config(),
                    "prompt": PROMPT_VERSION,
                    "adapter": ADAPTER_VERSION,
                    "upstream": UPSTREAM_COMMIT,
                }
            )
            job_id, reused = self.repository.enqueue(
                cache_key, symbol, snapshot_id, self.settings, retry_id
            )
        self.wakeup.set()
        return self.get(job_id, cache_hit=reused), reused

    def retry(self, job_id, snapshot_id):
        self._enabled()
        old = self.repository.get(job_id)
        return self.create(snapshot_id, old["symbol"], retry_id=job_id)

    def get(self, job_id, *, cache_hit=False):
        # Archived reports remain queryable even when model execution is disabled.
        job = self.repository.decoded(job_id)
        snapshot = self.evidence.get(job["snapshot_id"])
        payload = snapshot["payload"]
        job.pop("cache_key", None)
        return {
            **job,
            "cache_hit": cache_hit,
            "data_date": payload["data_date"],
            "snapshot_expires_at": snapshot["expires_at"],
            "expired": snapshot["expires_at"] <= self.clock(),
            "facts": payload["facts"][job["symbol"]],
            "sources": payload["sources"],
            "source_declarations": payload["source_declarations"],
            "warnings": payload["warnings"],
            "input_fingerprint": snapshot["content_hash"],
            "simulated": job["provider"] == "mock",
            "estimated_cost": None,
            "error_message": ERRORS.get(job["error_code"]),
            "research_only": True,
        }

    def cancel(self, job_id):
        self.repository.cancel(job_id)
        self.wakeup.set()
        return self.get(job_id)

    def _loop(self):
        while not self.stopping.is_set():
            self.wakeup.clear()
            try:
                found = self.execute_next()
            except Exception:
                # No provider response, secret or account state is logged on failure.
                found = False
            if not found:
                self.wakeup.wait(0.5)

    def execute_next(self):
        if self.job_gate is None:
            return self._execute_next() if self.account_enabled() else False
        with self.job_gate.acquire(self.account_enabled) as acquired:
            return self._execute_next() if acquired else False

    def _execute_next(self):
        job = self.repository.next_job(
            self.clock() + timedelta(seconds=self.settings.timeout_seconds),
            self.settings.daily_limit,
        )
        if job is None:
            return False
        job_id, attempt = job["job_id"], job["attempt_no"]
        reservation, usage = None, None
        try:
            snapshot = self._snapshot(job["snapshot_id"], job["symbol"])
            payload = snapshot["payload"]
            request = {
                "symbol": job["symbol"],
                "data_date": payload["data_date"],
                "facts": payload["facts"][job["symbol"]],
                "warnings": payload["warnings"],
                "screening_rules": {
                    "min_amount_20": payload["parameters"]["min_amount_20"],
                    "top_candidate_count": payload["parameters"]["top_candidate_count"],
                    "min_listing_days": 250,
                },
            }

            def cancelled():
                return (
                    self.stopping.is_set()
                    or not self.account_enabled()
                    or self.repository.get(job_id)["status"] == "cancel_requested"
                )

            if cancelled():
                raise ProcessFailure("cancelled")
            config = self.settings.model_config()
            if config["provider"] != "mock":
                # Opaque multi-call workers cannot promise a token ceiling. A monetary
                # budget therefore blocks them before any provider invocation.
                reservation = self.costs.reserve(config["model"])
            output = self.runner.run(
                {
                    "request": request,
                    "config": config,
                    "timeout_seconds": self.settings.timeout_seconds,
                },
                cancelled,
            )
            usage = output.get("usage")
            if output.get("error_code"):
                raise ProcessFailure(output["error_code"])
            if output.get("config") != config:
                raise ValueError("invalid_output")
            report = validate_report(output.get("report"), request)
            self.repository.finish(
                job_id, attempt, "succeeded", report=report, usage=output.get("usage")
            )
        except CostLimitError:
            self.repository.finish(job_id, attempt, "failed", error="model_budget_exceeded")
        except (ResearchError, ProcessFailure, ValueError) as error:
            code = error.code if isinstance(error, ResearchError) else str(error)
            code = code if code in ERRORS else "invalid_output"
            status = code if code in {"cancelled", "timed_out", "interrupted"} else "failed"
            if self.stopping.is_set():
                code, status = "interrupted", "interrupted"
            self.repository.finish(job_id, attempt, status, error=code)
        except Exception:
            self.repository.finish(job_id, attempt, "failed", error="model_error")
        finally:
            if reservation is not None:
                self.costs.finish(reservation, usage)
        return True
