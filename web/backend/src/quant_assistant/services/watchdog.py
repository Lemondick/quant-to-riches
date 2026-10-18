"""Scheduler heartbeat and missed/failed-run watchdog."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from quant_assistant.common.time import now_from_clock
from quant_assistant.common.validation import json_payload, text
from quant_assistant.models.repositories.database import Database


class SchedulerWatchdogService:
    def __init__(self, database: Database | Path, clock: object | None = None):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        self._clock = clock

    def heartbeat(
        self,
        job_name: str,
        *,
        expected_interval_minutes: int = 1440,
        status: str = "completed",
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        name = text(job_name, "job_name", maximum=100)
        if (
            isinstance(expected_interval_minutes, bool)
            or not isinstance(expected_interval_minutes, int)
            or expected_interval_minutes <= 0
        ):
            raise ValueError("expected_interval_minutes must be positive")
        clean_status = text(status, "status", maximum=20).lower()
        if clean_status not in {"running", "completed", "failed", "blocked"}:
            raise ValueError("invalid job heartbeat status")
        now = now_from_clock(self._clock)
        for stamp in (started_at, completed_at):
            if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None):
                raise ValueError("heartbeat timestamps must include a timezone")
            if stamp is not None and stamp > now:
                raise ValueError("heartbeat timestamps cannot be in the future")
        if clean_status == "running" and started_at is None:
            started_at = now
        if clean_status == "completed" and completed_at is None:
            completed_at = now
        if started_at is not None and completed_at is not None and completed_at < started_at:
            raise ValueError("completed_at must not precede started_at")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO job_heartbeats
                (job_name, expected_interval_minutes, last_started_at, last_completed_at,
                 last_status, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_name) DO UPDATE SET
                  expected_interval_minutes = excluded.expected_interval_minutes,
                  last_started_at = COALESCE(
                    excluded.last_started_at, job_heartbeats.last_started_at),
                  last_completed_at = COALESCE(
                    excluded.last_completed_at, job_heartbeats.last_completed_at),
                  last_status = excluded.last_status,
                  last_error = excluded.last_error,
                  updated_at = excluded.updated_at
                """,
                [
                    name,
                    expected_interval_minutes,
                    started_at,
                    completed_at,
                    clean_status,
                    None if error is None else text(error, "error", maximum=500),
                    now,
                ],
            )

    def scan(self) -> tuple[dict[str, object], ...]:
        now = now_from_clock(self._clock)
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM job_heartbeats ORDER BY job_name").fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            last_completed = row[3]
            overdue = last_completed is None or now - last_completed > timedelta(
                minutes=int(row[1])
            )
            failed = str(row[4]) in {"failed", "blocked"}
            result = {
                "job_name": str(row[0]),
                "expected_interval_minutes": int(row[1]),
                "last_started_at": row[2],
                "last_completed_at": last_completed,
                "status": str(row[4]),
                "last_error": row[5],
                "overdue": overdue,
                "failed": failed,
                "healthy": not overdue and not failed,
            }
            results.append(result)
            if overdue or failed:
                key = f"scheduler:{row[0]}"
                severity = "critical" if failed else "warning"
                message = f"任务 {row[0]} {'失败' if failed else '超时未运行'}"
                with self.database.connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO operational_alerts
                          (alert_key, severity, message, status, first_seen_at,
                           last_seen_at, details_json)
                        VALUES (?, ?, ?, 'open', ?, ?, ?)
                        ON CONFLICT (alert_key, status) DO UPDATE SET
                          severity = excluded.severity, message = excluded.message,
                          last_seen_at = excluded.last_seen_at,
                          details_json = excluded.details_json
                        """,
                        [key, severity, message, now, now, json_payload(result)],
                    )
        return tuple(results)


__all__ = ["SchedulerWatchdogService"]
