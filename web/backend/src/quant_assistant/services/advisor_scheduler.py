"""System-owned daily research schedule and local notifications, never a trading executor."""

from contextlib import suppress
from datetime import date, datetime
from threading import Event, Thread
from uuid import uuid4

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.services.trading_calendar import TradingCalendar


def trading_day(value: date):
    return TradingCalendar().trading_day(value)


class AdvisorScheduler:
    def __init__(self, service, *, now=None):
        self.service, self.database = service, service.database
        self.now = now or (lambda: datetime.now(SHANGHAI_TZ))
        self.stopping, self.thread = Event(), None
        self.calendar = TradingCalendar()

    def start(self):
        if self.thread is not None:
            return
        self._recover_claims()
        self.stopping.clear()
        self.thread = Thread(target=self._loop, name="system-advisor-scheduler", daemon=True)
        self.thread.start()

    def _recover_claims(self):
        # A process restart must resolve an uncertain claim without resubmitting models.
        rows = self.database.query_df("SELECT * FROM advisor_schedule_runs WHERE status='claimed'")
        for row in rows.to_dict("records"):
            reports = self.database.query_df(
                "SELECT report_id FROM market_advisor_reports WHERE report_date=? AND kind=? "
                "ORDER BY created_at DESC LIMIT 1",
                [row["run_date"], row["kind"]],
            )
            report_id = reports.iloc[0, 0] if not reports.empty else None
            result = (
                self.service.get(report_id)
                if report_id
                else {"status": "failed", "error_code": "interrupted"}
            )
            self._finish(
                row["run_date"].date(),
                row["kind"],
                report_id,
                result["status"],
                result.get("error_code"),
            )

    def stop(self, timeout=None):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=timeout)
        if self.thread and not self.thread.is_alive():
            self.thread = None
        return self.thread is None

    def _loop(self):
        while not self.stopping.is_set():
            # Persistent claims prevent resubmitting uncertain paid work after errors.
            with suppress(Exception):
                self.tick()
            self.stopping.wait(20)

    def status(self):
        now = self.now().astimezone(SHANGHAI_TZ)
        rows = self.database.query_df(
            "SELECT * FROM advisor_schedule_runs ORDER BY started_at DESC LIMIT 8"
        )
        return {
            "owner": "system",
            "running": bool(self.thread and self.thread.is_alive()),
            "timezone": "Asia/Shanghai",
            "schedules": {"morning": "09:30", "afternoon": "14:30"},
            "trading_day": self.calendar.trading_day(now.date()),
            "calendar_year": now.year,
            "calendar_status": "ready" if self.calendar.load(now.year) else "missing",
            "calendar_warning": None
            if self.calendar.load(now.year)
            else f"{now.year}年交易日历缺失，请管理者导入交易所休市公告，自动报告暂暂停。",
            "items": rows.to_dict("records"),
            "automatic_trading": False,
        }

    def tick(self):
        if not getattr(self.service, "account_enabled", lambda: True)():
            return
        now = self.now().astimezone(SHANGHAI_TZ)
        if self.calendar.trading_day(now.date()) is not True:
            return
        self._reconcile(now)
        kind = (
            "morning"
            if now.hour == 9 and 30 <= now.minute < 45
            else ("afternoon" if now.hour == 14 and 30 <= now.minute < 45 else None)
        )
        if kind is None:
            return
        with self.database.transaction() as connection:
            inserted = connection.execute(
                "INSERT INTO advisor_schedule_runs VALUES (?,?,'claimed',?,NULL,NULL) "
                "ON CONFLICT DO NOTHING RETURNING kind",
                [now.date(), kind, now],
            ).fetchone()
        if not inserted:
            return
        report_id = None
        status, code = "failed", "model_configuration"
        try:
            if self.service.public_config()["configured"]:
                job = self.service.create(kind, force=False)
                report_id = job["report_id"]
                # Existing queued/running manual jobs are not executed a second time.
                if job["status"] == "queued":
                    job = self.service.execute(report_id)
                status, code = job["status"], job.get("error_code")
        except Exception:
            code = "schedule_failed"
        self._finish(now.date(), kind, report_id, status, code)

    def _finish(self, run_date, kind, report_id, status, code):
        now = self.now().astimezone(SHANGHAI_TZ)
        with self.database.transaction() as connection:
            updated = connection.execute(
                "UPDATE advisor_schedule_runs SET status=?,report_id=?,error_code=? "
                "WHERE run_date=? AND kind=? AND status IN ('claimed','queued','running') "
                "RETURNING kind",
                [status, report_id, code, run_date, kind],
            ).fetchone()
            if (
                updated
                and status not in {"queued", "running"}
                and run_date == now.date()
                and self.calendar.trading_day(now.date()) is True
            ):
                connection.execute(
                    "INSERT INTO advisor_notifications VALUES (?,?,?,?,?,?,false)",
                    [uuid4().hex, kind, report_id, status, code, now],
                )

    def _reconcile(self, now):
        rows = self.database.query_df(
            "SELECT * FROM advisor_schedule_runs WHERE status IN ('queued','running') "
            "AND report_id IS NOT NULL"
        )
        for row in rows.to_dict("records"):
            result = self.service.get(row["report_id"])
            if result["status"] not in {"queued", "running"}:
                self._finish(
                    row["run_date"].date(),
                    row["kind"],
                    row["report_id"],
                    result["status"],
                    result.get("error_code"),
                )

    def notifications(self):
        now = self.now().astimezone(SHANGHAI_TZ)
        if self.calendar.trading_day(now.date()) is not True:
            return {"items": []}
        rows = self.database.query_df(
            "SELECT * FROM advisor_notifications WHERE NOT acknowledged "
            "AND CAST(created_at AS DATE)=? ORDER BY created_at DESC LIMIT 10",
            [now.date()],
        )
        return {"items": rows.to_dict("records")}

    def acknowledge(self, notice_id):
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE advisor_notifications SET acknowledged=true WHERE id=?", [notice_id]
            )
        return {"status": "acknowledged"}
