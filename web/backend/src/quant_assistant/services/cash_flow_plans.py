"""Scheduled deposits and withdrawals used by planning/research views."""

from __future__ import annotations

import calendar
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from quant_assistant.common.time import now_from_clock
from quant_assistant.common.validation import date_value, decimal_value, text
from quant_assistant.models.repositories.database import Database


@dataclass(frozen=True, slots=True)
class CashFlowPlan:
    plan_id: str
    name: str
    direction: str
    amount: Decimal
    frequency: str
    start_date: date
    end_date: date | None
    status: str
    note: str | None
    created_at: datetime
    updated_at: datetime

    def schedule(self, *, until: date | None = None) -> tuple[date, ...]:
        end = self.end_date or until or self.start_date
        if until is not None and end > until:
            end = until
        if end < self.start_date or self.status != "active":
            return ()
        values: list[date] = []
        index = 0
        while True:
            current = (
                self.start_date
                if index == 0
                else _add_months(
                    self.start_date,
                    index * {"monthly": 1, "quarterly": 3, "annual": 12}.get(self.frequency, 0),
                )
            )
            if self.frequency == "weekly":
                current = self.start_date + timedelta(days=7 * index)
            if current > end:
                break
            values.append(current)
            if self.frequency == "once":
                break
            if self.frequency not in {"weekly", "monthly", "quarterly", "annual"}:
                break
            if current >= end:
                break
            index += 1
        return tuple(values)


class CashFlowPlanService:
    FREQUENCIES = frozenset({"once", "weekly", "monthly", "quarterly", "annual"})

    def __init__(self, database: Database | Path, clock: object | None = None):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        self._clock = clock

    def create(
        self,
        name: str,
        direction: str,
        amount: Decimal,
        frequency: str,
        start_date: date,
        *,
        end_date: date | None = None,
        note: str | None = None,
        plan_id: str | None = None,
    ) -> CashFlowPlan:
        plan = self._build(
            plan_id or uuid.uuid4().hex,
            name,
            direction,
            amount,
            frequency,
            start_date,
            end_date,
            "active",
            note,
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO cash_flow_plans
                (plan_id, name, direction, amount, frequency, start_date, end_date,
                 status, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    plan.plan_id,
                    plan.name,
                    plan.direction,
                    plan.amount,
                    plan.frequency,
                    plan.start_date,
                    plan.end_date,
                    plan.status,
                    plan.note,
                    plan.created_at,
                    plan.updated_at,
                ],
            )
        return plan

    def list(self, *, status: str | None = None, limit: int = 100) -> tuple[CashFlowPlan, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("cash flow plan limit must be between 1 and 500")
        sql = "SELECT * FROM cash_flow_plans"
        params: list[object] = []
        if status is not None:
            status = text(status, "status", maximum=20).lower()
            if status not in {"active", "paused", "completed"}:
                raise ValueError("invalid cash flow plan status")
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY start_date, plan_id LIMIT ?"
        params.append(limit)
        with self.database.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(self._row(row) for row in rows)

    def due_events(self, as_of: date, *, limit: int = 500) -> tuple[dict[str, object], ...]:
        target = date_value(as_of, "as_of")
        events: list[dict[str, object]] = []
        for plan in self.list(status="active", limit=limit):
            if target in plan.schedule(until=target):
                events.append(
                    {
                        "plan_id": plan.plan_id,
                        "name": plan.name,
                        "direction": plan.direction,
                        "amount": plan.amount,
                        "scheduled_on": target,
                        "note": plan.note,
                    }
                )
        return tuple(events)

    def set_status(self, plan_id: str, status: str) -> CashFlowPlan:
        identifier = text(plan_id, "plan_id", maximum=128)
        next_status = text(status, "status", maximum=20).lower()
        if next_status not in {"active", "paused", "completed"}:
            raise ValueError("invalid cash flow plan status")
        now = now_from_clock(self._clock)
        with self.database.transaction() as connection:
            row = connection.execute(
                "UPDATE cash_flow_plans SET status = ?, updated_at = ? "
                "WHERE plan_id = ? RETURNING *",
                [next_status, now, identifier],
            ).fetchone()
            if row is None:
                raise ValueError("cash_flow_plan_not_found")
        return self._row(row)

    def _build(
        self,
        plan_id: str,
        name: str,
        direction: str,
        amount: Decimal,
        frequency: str,
        start_date: date,
        end_date: date | None,
        status: str,
        note: str | None,
    ) -> CashFlowPlan:
        identifier = text(plan_id, "plan_id", maximum=128)
        clean_direction = text(direction, "direction", maximum=20).lower()
        if clean_direction not in {"deposit", "withdrawal"}:
            raise ValueError("direction must be deposit or withdrawal")
        clean_frequency = text(frequency, "frequency", maximum=20).lower()
        if clean_frequency not in self.FREQUENCIES:
            raise ValueError("unsupported cash flow frequency")
        start = date_value(start_date, "start_date")
        end = None if end_date is None else date_value(end_date, "end_date")
        if end is not None and end < start:
            raise ValueError("end_date must not precede start_date")
        now = now_from_clock(self._clock)
        return CashFlowPlan(
            identifier,
            text(name, "name"),
            clean_direction,
            decimal_value(amount, "amount", positive=True),
            clean_frequency,
            start,
            end,
            status,
            (None if note is None else text(note, "note")),
            now,
            now,
        )

    def _row(self, row: Sequence[object]) -> CashFlowPlan:
        return CashFlowPlan(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            Decimal(str(row[3])),
            str(row[4]),
            row[5],
            row[6],
            str(row[7]),
            row[8],
            row[9],
            row[10],
        )


def _add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    # Clamp to the last day of the destination month (e.g. Jan 31 -> Feb 28).
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


__all__ = ["CashFlowPlan", "CashFlowPlanService"]
