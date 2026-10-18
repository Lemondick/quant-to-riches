"""Historical portfolio drawdown control-line validation and persistence.

The validation itself is pure risk-domain computation; the service persists
immutable validation reports so production gates and research views can cite
them later.  Results are research-only by design: a caller must explicitly
pass a report through the production acceptance workflow before using any
risk policy in production.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from quant_assistant.common.time import now_from_clock
from quant_assistant.common.validation import date_value, json_payload, records, text
from quant_assistant.models.repositories.database import Database


@dataclass(frozen=True, slots=True)
class DrawdownValidation:
    run_id: str
    threshold: float
    max_drawdown: float
    observed_sessions: int
    breach_count: int
    passed: bool
    reasons: tuple[str, ...]
    research_only: bool = True


def validate_drawdown(
    run_id: str,
    nav: object,
    *,
    threshold: float = 0.20,
) -> DrawdownValidation:
    """Validate a historical NAV series against an explicit control line.

    ``nav`` accepts a ``unit_nav``/``total_value`` dataframe or a sequence of
    mappings with either field.  The result is research-only by design.  A
    caller must explicitly pass this report through the production acceptance
    workflow before using any risk policy in production.
    """

    run = text(run_id, "run_id", maximum=128)
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise TypeError("threshold must be a number")
    if not math.isfinite(float(threshold)) or not 0 < float(threshold) <= 1:
        raise ValueError("threshold must be between 0 and 1")
    rows = records(nav)
    values: list[float] = []
    reasons: list[str] = []
    previous_date = None
    dated = any(
        row.get("as_of_date") is not None or row.get("trade_date") is not None for row in rows
    )
    for row in rows:
        if dated:
            try:
                stamp = row.get("as_of_date") or row.get("trade_date")
                if isinstance(stamp, datetime):
                    stamp = stamp.date()
                elif isinstance(stamp, str):
                    stamp = date.fromisoformat(stamp)
                current_date = date_value(stamp, "nav_date")
                if previous_date is not None and current_date <= previous_date:
                    reasons.append("nav_dates_not_strictly_increasing")
                previous_date = current_date
            except (ValueError, TypeError):
                reasons.append("invalid_nav_date")
        value = row.get("unit_nav")
        if value is None:
            value = row.get("total_value")
        try:
            converted = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            reasons.append("invalid_nav_value")
            continue
        if math.isfinite(converted) and converted > 0:
            values.append(converted)
        else:
            reasons.append("invalid_nav_value")
    if len(values) < 2:
        reasons.append("insufficient_nav_sessions")
    running_max = values[0] if values else 0.0
    drawdowns: list[float] = []
    for value in values:
        running_max = max(running_max, value)
        drawdowns.append(max(0.0, 1.0 - value / running_max) if running_max else 0.0)
    maximum = max(drawdowns, default=0.0)
    breaches = sum(1 for value in drawdowns if value > float(threshold))
    if breaches:
        reasons.append("drawdown_control_line_breached")
    return DrawdownValidation(
        run,
        float(threshold),
        maximum,
        len(values),
        breaches,
        not reasons,
        tuple(dict.fromkeys(reasons)),
    )


class DrawdownControlService:
    def __init__(self, database: Database | Path, clock: object | None = None):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        self._clock = clock

    def validate_and_store(
        self, run_id: str, nav: object, *, threshold: float = 0.20
    ) -> DrawdownValidation:
        report = validate_drawdown(run_id, nav, threshold=threshold)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT threshold, max_drawdown, observed_sessions, breach_count, passed, "
                "reasons_json FROM drawdown_validations WHERE run_id = ?",
                [report.run_id],
            ).fetchone()
            if existing is not None:
                expected = (
                    report.threshold,
                    report.max_drawdown,
                    report.observed_sessions,
                    report.breach_count,
                    report.passed,
                    list(report.reasons),
                )
                stored = (*existing[:5], json.loads(existing[5]))
                if stored != expected:
                    raise ValueError("drawdown_run_id_conflict")
                return report
            connection.execute(
                """
                INSERT INTO drawdown_validations
                (run_id, threshold, max_drawdown, observed_sessions, breach_count,
                 passed, research_only, reasons_json, validated_at)
                VALUES (?, ?, ?, ?, ?, ?, TRUE, ?, ?)
                """,
                [
                    report.run_id,
                    report.threshold,
                    report.max_drawdown,
                    report.observed_sessions,
                    report.breach_count,
                    report.passed,
                    json_payload(report.reasons),
                    now_from_clock(self._clock),
                ],
            )
        return report

    def latest(self, run_id: str | None = None) -> DrawdownValidation | None:
        sql = "SELECT * FROM drawdown_validations"
        parameters: list[object] = []
        if run_id is not None:
            sql += " WHERE run_id = ?"
            parameters.append(text(run_id, "run_id", maximum=128))
        sql += " ORDER BY validated_at DESC, run_id DESC LIMIT 1"
        with self.database.connection() as connection:
            row = connection.execute(sql, parameters).fetchone()
        if row is None:
            return None
        reasons = json.loads(row[7]) if isinstance(row[7], str) else row[7]
        return DrawdownValidation(
            str(row[0]),
            float(row[1]),
            float(row[2]),
            int(row[3]),
            int(row[4]),
            bool(row[5]),
            tuple(str(item) for item in (reasons or ())),
            bool(row[6]),
        )


__all__ = ["DrawdownControlService", "DrawdownValidation", "validate_drawdown"]
