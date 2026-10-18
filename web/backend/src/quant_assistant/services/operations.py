"""Lease-based run exclusion and persistent local operational alerts."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from quant_assistant.models.repositories.database import Database


class OperationCoordinator:
    def __init__(self, database: Database | Path, clock: object):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        now = getattr(clock, "now", clock)
        if not callable(now):
            raise TypeError("clock must be callable or provide now()")
        self._now = now

    @contextmanager
    def hold(self, lock_name: str, *, lease: timedelta = timedelta(minutes=30)) -> Iterator[str]:
        name = _required_text(lock_name, "lock_name")
        if lease <= timedelta(0):
            raise ValueError("lease must be positive")
        owner = uuid.uuid4().hex
        acquired_at = _aware(self._now(), "clock")
        expires_at = acquired_at + lease
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM operation_locks WHERE lock_name = ? AND expires_at <= ?",
                [name, acquired_at],
            )
            try:
                connection.execute(
                    """
                    INSERT INTO operation_locks (
                        lock_name, owner_token, acquired_at, expires_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [name, owner, acquired_at, expires_at],
                )
            except duckdb.ConstraintException as exc:
                raise RuntimeError(f"operation_already_running:{name}") from exc
        try:
            yield owner
        finally:
            with self.database.connection() as connection:
                connection.execute(
                    "DELETE FROM operation_locks WHERE lock_name = ? AND owner_token = ?",
                    [name, owner],
                )


class OperationalAlertService:
    def __init__(self, database: Database | Path, clock: object):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        now = getattr(clock, "now", clock)
        if not callable(now):
            raise TypeError("clock must be callable or provide now()")
        self._now = now

    def raise_alert(
        self,
        alert_key: str,
        severity: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> int:
        key = _required_text(alert_key, "alert_key")
        level = _required_text(severity, "severity").lower()
        if level not in {"info", "warning", "critical"}:
            raise ValueError("severity must be info, warning, or critical")
        text = _required_text(message, "message")
        now = _aware(self._now(), "clock")
        payload = json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True)
        with self.database.connection() as connection:
            existing = connection.execute(
                "SELECT id FROM operational_alerts WHERE alert_key = ? AND status = 'open'",
                [key],
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE operational_alerts
                    SET severity = ?, message = ?, last_seen_at = ?, details_json = ?
                    WHERE id = ?
                    """,
                    [level, text, now, payload, existing[0]],
                )
                return int(existing[0])
            row = connection.execute(
                """
                INSERT INTO operational_alerts (
                    alert_key, severity, message, status,
                    first_seen_at, last_seen_at, details_json
                ) VALUES (?, ?, ?, 'open', ?, ?, ?) RETURNING id
                """,
                [key, level, text, now, now, payload],
            ).fetchone()
            if row is None:
                raise RuntimeError("alert insert did not return an id")
            return int(row[0])

    def resolve(self, alert_key: str) -> None:
        key = _required_text(alert_key, "alert_key")
        now = _aware(self._now(), "clock")
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM operational_alerts WHERE alert_key = ? AND status = 'resolved'",
                [key],
            )
            connection.execute(
                """
                UPDATE operational_alerts
                SET status = 'resolved', resolved_at = ?, last_seen_at = ?
                WHERE alert_key = ? AND status = 'open'
                """,
                [now, now, key],
            )

    def notification_due(
        self,
        alert_key: str,
        fingerprint: str,
        *,
        silence_window: timedelta,
    ) -> bool:
        """Return whether an open alert is eligible for another notification.

        A changed fingerprint (severity/direction/source event) always breaks
        the silence window.  Missing legacy notification state is treated as
        due, keeping upgrades fail-open for operator visibility.
        """

        key = _required_text(alert_key, "alert_key")
        marker = _required_text(fingerprint, "fingerprint")
        if silence_window < timedelta(0):
            raise ValueError("silence_window must not be negative")
        now = _aware(self._now(), "clock")
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT notification_fingerprint, last_notified_at, silenced_until
                FROM operational_alerts
                WHERE alert_key = ? AND status = 'open'
                """,
                [key],
            ).fetchone()
        if row is None:
            return True
        previous_fingerprint, last_notified_at, silenced_until = row
        if previous_fingerprint != marker or last_notified_at is None:
            return True
        if silenced_until is not None:
            return now >= silenced_until
        return now - last_notified_at >= silence_window

    def mark_notified(
        self,
        alert_key: str,
        fingerprint: str,
        *,
        silence_window: timedelta,
    ) -> None:
        """Persist successful notification delivery and its quiet-until time."""

        key = _required_text(alert_key, "alert_key")
        marker = _required_text(fingerprint, "fingerprint")
        if silence_window < timedelta(0):
            raise ValueError("silence_window must not be negative")
        now = _aware(self._now(), "clock")
        silenced_until = now + silence_window
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE operational_alerts
                SET last_notified_at = ?, notification_fingerprint = ?, silenced_until = ?
                WHERE alert_key = ? AND status = 'open'
                """,
                [now, marker, silenced_until, key],
            )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


__all__ = ["OperationCoordinator", "OperationalAlertService"]
