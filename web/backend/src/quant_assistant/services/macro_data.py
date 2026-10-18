"""Evidence-bound macro observations for research context."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from quant_assistant.common.time import now_from_clock
from quant_assistant.common.validation import date_value, text
from quant_assistant.models.repositories.database import Database


@dataclass(frozen=True, slots=True)
class MacroObservation:
    indicator: str
    observed_on: date
    value: float
    unit: str
    source: str
    created_at: datetime


class MacroDataService:
    INDICATORS = frozenset({"interest_rate", "pmi", "social_financing"})

    def __init__(self, database: Database | Path, clock: object | None = None):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        self._clock = clock

    def record(
        self, indicator: str, observed_on: date, value: float, unit: str, source: str = "manual"
    ) -> MacroObservation:
        name = text(indicator, "indicator", maximum=60).lower()
        if name not in self.INDICATORS:
            raise ValueError(f"indicator must be one of {sorted(self.INDICATORS)}")
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise ValueError("macro value must be finite")
        item = MacroObservation(
            name,
            date_value(observed_on, "observed_on"),
            float(value),
            text(unit, "unit", maximum=30),
            text(source, "source"),
            now_from_clock(self._clock),
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO macro_observations
                  (indicator, observed_on, value, unit, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (indicator, observed_on, source) DO UPDATE
                  SET value = excluded.value, unit = excluded.unit,
                      created_at = excluded.created_at
                """,
                [
                    item.indicator,
                    item.observed_on,
                    item.value,
                    item.unit,
                    item.source,
                    item.created_at,
                ],
            )
        return item

    def list(
        self, *, indicator: str | None = None, limit: int = 100
    ) -> tuple[MacroObservation, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("macro observation limit must be between 1 and 500")
        sql = (
            "SELECT indicator, observed_on, value, unit, source, created_at FROM macro_observations"
        )
        params: list[object] = []
        if indicator is not None:
            name = text(indicator, "indicator", maximum=60).lower()
            sql += " WHERE indicator = ?"
            params.append(name)
        sql += " ORDER BY observed_on DESC, id DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(
            MacroObservation(str(row[0]), row[1], float(row[2]), str(row[3]), str(row[4]), row[5])
            for row in rows
        )

    def context(self, *, as_of: date | None = None) -> dict[str, object]:
        target = as_of or date.today()
        observations = self.list(limit=500)
        latest: dict[str, MacroObservation] = {}
        for item in observations:
            if item.observed_on <= target and item.indicator not in latest:
                latest[item.indicator] = item
        missing = sorted(self.INDICATORS.difference(latest))
        max_age = max(((target - item.observed_on).days for item in latest.values()), default=None)
        status = (
            "valid"
            if not missing and (max_age is None or max_age <= 45)
            else "stale"
            if latest
            else "blocked"
        )
        return {
            "as_of_date": target,
            "status": status,
            "missing": tuple(missing),
            "max_age_days": max_age,
            "observations": tuple(latest.values()),
            "research_only": True,
        }


__all__ = ["MacroDataService", "MacroObservation"]
