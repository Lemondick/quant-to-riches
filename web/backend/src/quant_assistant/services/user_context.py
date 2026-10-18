"""Resolve trusted account identities to isolated business applications and storage."""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import RLock

import duckdb
from fastapi import Query

from quant_assistant.analysis.advisor_config import AdvisorSettings
from quant_assistant.analysis.research_config import AgentResearchSettings
from quant_assistant.config import NotificationSettings
from quant_assistant.services.auth import AuthError
from quant_assistant.services.composition import build_local_services


@dataclass
class UserContext:
    services: object
    app: object


class UserContexts:
    def __init__(self, settings, auth):
        self.settings, self.auth = settings, auth
        self.root = settings.data_dir.resolve()
        self.users_root = (self.root / "users").resolve()
        if not self.users_root.is_relative_to(self.root):
            raise ValueError("user directory outside storage")
        self.users_root.mkdir(parents=True, exist_ok=True)
        self._contexts = {}
        self._lock = RLock()
        self._lifecycle_lock = RLock()
        self._started = False
        self._user_locks = {}
        self.site_lock = None
        from quant_assistant.providers.fund_detail import PublicFundDetailProvider
        from quant_assistant.providers.fund_valuation import FallbackFundValuationProvider
        from quant_assistant.providers.market_feed import AkshareMarketFeedProvider
        from quant_assistant.services.runtime_limits import ModelJobGate, PublicReadCache

        self.job_gate = ModelJobGate()
        self.public_providers = {
            "fund_detail_provider": PublicReadCache(PublicFundDetailProvider(), 300),
            "fund_valuation_provider": PublicReadCache(FallbackFundValuationProvider(), 20),
            "market_feed_provider": PublicReadCache(AkshareMarketFeedProvider(), 30),
        }

    def _user_lock(self, user_id):
        with self._lock:
            return self._user_locks.setdefault(user_id, RLock())

    def _enabled(self, user_id):
        account = self.auth.repository.by_id(user_id)
        return bool(account and account["status"] == "active" and not self.job_gate.stopping)

    def directory(self, user):
        user_id = user.get("id", "")
        if not isinstance(user_id, str) or not re.fullmatch(r"[a-f0-9]{32}", user_id):
            raise ValueError("invalid user identity")
        account = self.auth.repository.by_id(user_id)
        if not account:
            raise ValueError("unknown user identity")
        if account["role"] == "admin":
            return self.root
        directory = (self.users_root / user_id).resolve()
        if not directory.is_relative_to(self.users_root):
            raise ValueError("user directory outside storage")
        return directory

    def get(self, user):
        with self._lifecycle_lock:
            return self._get(user)

    def _get(self, user):
        from quant_assistant.controllers.app import create_app
        from quant_assistant.controllers.security import ApiSecuritySettings

        directory = self.directory(user)
        with self._user_lock(user["id"]):
            if self.job_gate.stopping:
                raise RuntimeError("business services are draining")
            if user["id"] in self._contexts:
                return self._contexts[user["id"]]
            admin = directory == self.root
            settings = replace(
                self.settings,
                data_dir=directory,
                raw_snapshot_dir=directory / "raw",
                database_path=directory / "quant_assistant.duckdb",
                notifications=self.settings.notifications if admin else NotificationSettings(),
                agent_research=self.settings.agent_research if admin else AgentResearchSettings(),
            )
            # Inject explicitly: ordinary users must never read the process-wide private config.
            advisor = AdvisorSettings.from_file() if admin else AdvisorSettings()
            services = build_local_services(
                settings,
                advisor_settings=advisor,
                public_providers=self.public_providers,
                job_gate=self.job_gate,
                account_enabled=lambda: self._enabled(user["id"]),
            )
            from quant_assistant.services.trading_calendar import TradingCalendar

            services.api_queries.market_advisor.scheduler.calendar = TradingCalendar(
                self.root / "calendars"
            )
            context = UserContext(
                services,
                create_app(
                    services.api_queries,
                    services.analysis,
                    security=ApiSecuritySettings(
                        rate_limit_per_minute=settings.deployment.rate_limit_per_minute
                    ),
                ),
            )
            self._add_readiness(context)
            from quant_assistant.controllers.runtime_routes import build_user_runtime_router

            context.app.include_router(build_user_runtime_router(services))
            with self._lock:
                self._contexts[user["id"]] = context
            if self._started:
                self._start_context(context)
            return context

    def _add_readiness(self, context):
        from quant_assistant.services.production_readiness import build_report
        from quant_assistant.services.readiness_client import path_identity

        @context.app.get("/api/v1/operations/readiness")
        def readiness(
            minimum_paper_sessions: int = Query(20, ge=1, le=730),
            minimum_backups: int = Query(1, ge=1, le=100),
        ):
            database = context.services.database
            backup_dir = self.root / "backups"
            with database.connection():
                report = build_report(
                    database,
                    minimum_paper_sessions=minimum_paper_sessions,
                    backup_dir=backup_dir,
                    minimum_backups=minimum_backups,
                )
            return {
                **report,
                "database_identity": path_identity(database.path),
                "backup_identity": path_identity(backup_dir),
            }

    @staticmethod
    def _start_context(context):
        context.services.api_queries.agent_research.start()
        context.services.api_queries.market_advisor.scheduler.start()

    def start(self):
        with self._lock:
            if self._started:
                return
            from quant_assistant.site_backup import SiteLock

            self.site_lock = SiteLock(self.root).acquire()
            self._started = True
            self.job_gate.stopping = False
        for user in self.auth.repository.users():
            try:
                if (
                    user["status"] == "active"
                    and (self.directory(user) / "quant_assistant.duckdb").is_file()
                ):
                    self.get(user)
            except (duckdb.Error, OSError, ValueError, RuntimeError):
                continue
        with self._lock:
            contexts = list(self._contexts.values())
        for context in contexts:
            self._start_context(context)

    def close(self):
        with self._lifecycle_lock:
            self._close()

    def _close(self):
        self.job_gate.stopping = True
        with self._lock:
            contexts = list(self._contexts.values())
        drained = True
        for context in contexts:
            queries = context.services.api_queries
            drained = queries.market_advisor.scheduler.stop() and drained
            drained = queries.agent_research.stop() and drained
        # Manual ASGI background reports also own storage while in the global gate.
        drained = self.job_gate.drain() and drained
        if not drained:
            raise RuntimeError("workers are still draining; storage and site lock retained")
        for context in contexts:
            context.services.database.close()
        with self._lock:
            self._started = False
            self._contexts = {}
        if self.site_lock:
            self.site_lock.close()
            self.site_lock = None

    def business_readiness(self):
        if self.job_gate.stopping:
            return False
        return all(
            self.summary(user)["data_status"] != "unavailable"
            for user in self.auth.repository.users()
            if user["status"] == "active"
        )

    @contextmanager
    def _read_connection(self, user, path):
        # Prevent a second connection with conflicting flags during first-user initialization.
        with self._user_lock(user["id"]):
            context = self._contexts.get(user["id"])
            if context is not None:
                with context.services.database.connection() as conn:
                    yield conn
            else:
                conn = duckdb.connect(str(path), read_only=True)
                try:
                    conn.execute("SET TimeZone='Asia/Shanghai'")
                    yield conn
                finally:
                    conn.close()

    def summary(self, user):
        path = self.directory(user) / "quant_assistant.duckdb"
        if not path.is_file():
            return {
                "data_status": "unused",
                "position_count": 0,
                "ledger_count": 0,
                "report_count": 0,
            }
        try:
            with self._read_connection(user, path) as conn:
                positions = conn.execute("""
                    SELECT count(DISTINCT symbol) FROM (
                        SELECT symbol FROM positions WHERE asset_type!='cash' AND quantity>0
                        UNION ALL
                        SELECT p.symbol FROM account_positions p
                        JOIN portfolio_accounts a ON a.account_id=p.account_id
                        WHERE a.status='active' AND p.asset_type!='cash' AND p.quantity>0
                    )
                """).fetchone()[0]
                ledger = conn.execute(
                    "SELECT (SELECT count(*) FROM transactions) + "
                    "(SELECT count(*) FROM dividend_events)"
                ).fetchone()[0]
                reports = conn.execute(
                    "SELECT (SELECT count(*) FROM market_advisor_reports) + "
                    "(SELECT count(*) FROM agent_research_attempts WHERE report_json IS NOT NULL)"
                ).fetchone()[0]
            return {
                "data_status": "ready",
                "position_count": positions,
                "ledger_count": ledger,
                "report_count": reports,
            }
        except (duckdb.Error, OSError, RuntimeError):
            return {
                "data_status": "unavailable",
                "position_count": None,
                "ledger_count": None,
                "report_count": None,
            }

    @contextmanager
    def read_database(self, user):
        try:
            path = self.directory(user) / "quant_assistant.duckdb"
        except (ValueError, OSError):
            raise AuthError("user_data_unavailable") from None
        if not path.is_file():
            yield None
            return
        try:
            with self._read_connection(user, path) as connection:
                yield connection
        except (duckdb.Error, OSError, RuntimeError):
            raise AuthError("user_data_unavailable") from None
