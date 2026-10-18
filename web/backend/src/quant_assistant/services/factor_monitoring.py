"""Persisted factor IC/IR trend and decay monitoring."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from quant_assistant.common.time import now_from_clock
from quant_assistant.common.validation import date_value, json_payload, text
from quant_assistant.models.repositories.database import Database


@dataclass(frozen=True, slots=True)
class FactorObservation:
    factor: str
    as_of_date: date
    ic: float | None
    ir: float | None
    sample_count: int
    source: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FactorMonitorReport:
    factor: str
    observations: tuple[FactorObservation, ...]
    mean_ic: float | None
    mean_ir: float | None
    ic_slope: float | None
    ir_slope: float | None
    decay: bool
    alerts: tuple[dict[str, object], ...]
    research_only: bool = True


class FactorMonitoringService:
    def __init__(self, database: Database | Path, clock: object | None = None):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        self._clock = clock

    def record(
        self,
        factor: str,
        as_of_date: date,
        *,
        ic: float | None,
        ir: float | None,
        sample_count: int,
        source: str = "manual",
    ) -> FactorObservation:
        name = text(factor, "factor", maximum=80)
        observed = date_value(as_of_date, "as_of_date")
        clean_ic = _finite_optional(ic, "ic")
        clean_ir = _finite_optional(ir, "ir")
        if clean_ic is not None and not -1 <= clean_ic <= 1:
            raise ValueError("ic must be between -1 and 1")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
            raise ValueError("sample_count must be a non-negative integer")
        item = FactorObservation(
            name,
            observed,
            clean_ic,
            clean_ir,
            sample_count,
            text(source, "source"),
            now_from_clock(self._clock),
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO factor_observations
                (factor, as_of_date, ic, ir, sample_count, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    item.factor,
                    item.as_of_date,
                    item.ic,
                    item.ir,
                    item.sample_count,
                    item.source,
                    item.created_at,
                ],
            )
        report = self.report(name)
        self._store_alerts(name, report.alerts, observed=item.created_at)
        return item

    def report(
        self, factor: str, *, limit: int = 60, decay_threshold: float = -0.01
    ) -> FactorMonitorReport:
        name = text(factor, "factor", maximum=80)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 730:
            raise ValueError("factor observation limit must be between 2 and 730")
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT factor, as_of_date, ic, ir, sample_count, source, created_at "
                "FROM factor_observations WHERE factor = ? ORDER BY as_of_date DESC LIMIT ?",
                [name, limit],
            ).fetchall()
        observations = tuple(
            FactorObservation(
                str(row[0]),
                row[1],
                _finite_optional(row[2], "ic"),
                _finite_optional(row[3], "ir"),
                int(row[4]),
                str(row[5]),
                row[6],
            )
            for row in reversed(rows)
        )
        ic_values = [item.ic for item in observations if item.ic is not None]
        ir_values = [item.ir for item in observations if item.ir is not None]
        ic_slope = _slope(ic_values)
        ir_slope = _slope(ir_values)
        decay = (ic_slope is not None and ic_slope <= decay_threshold) or (
            ir_slope is not None and ir_slope <= decay_threshold
        )
        alerts: list[dict[str, object]] = []
        if not observations:
            alerts.append(
                {
                    "alert_type": "missing",
                    "severity": "warning",
                    "message": f"因子 {name} 尚无持续 IC/IR 观测",
                }
            )
        elif decay:
            alerts.append(
                {
                    "alert_type": "decay",
                    "severity": "warning",
                    "message": f"因子 {name} 的 IC/IR 出现衰减趋势",
                    "ic_slope": ic_slope,
                    "ir_slope": ir_slope,
                }
            )
        if observations and observations[-1].sample_count == 0:
            alerts.append(
                {
                    "alert_type": "unstable",
                    "severity": "critical",
                    "message": f"因子 {name} 最新观测没有有效样本",
                }
            )
        return FactorMonitorReport(
            name,
            observations,
            _mean(ic_values),
            _mean(ir_values),
            ic_slope,
            ir_slope,
            decay,
            tuple(alerts),
        )

    def alerts(
        self, *, factor: str | None = None, limit: int = 100
    ) -> tuple[dict[str, object], ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("factor alert limit must be between 1 and 500")
        sql = (
            "SELECT id, factor, alert_type, severity, message, observed_at, status, "
            "details_json FROM factor_alerts"
        )
        params: list[object] = []
        if factor is not None:
            sql += " WHERE factor = ?"
            params.append(text(factor, "factor", maximum=80))
        sql += " ORDER BY observed_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            details = json.loads(row[7]) if isinstance(row[7], str) else row[7]
            result.append(
                {
                    "id": int(row[0]),
                    "factor": str(row[1]),
                    "alert_type": str(row[2]),
                    "severity": str(row[3]),
                    "message": str(row[4]),
                    "observed_at": row[5],
                    "status": str(row[6]),
                    "details": details or {},
                }
            )
        return tuple(result)

    def _store_alerts(
        self, factor: str, alerts: Iterable[Mapping[str, object]], *, observed: datetime
    ) -> None:
        with self.database.transaction() as connection:
            for alert in alerts:
                connection.execute(
                    """
                    INSERT INTO factor_alerts
                    (factor, alert_type, severity, message, observed_at, status, details_json)
                    VALUES (?, ?, ?, ?, ?, 'open', ?)
                    ON CONFLICT (factor, alert_type, observed_at) DO NOTHING
                    """,
                    [
                        factor,
                        alert.get("alert_type", "missing"),
                        alert.get("severity", "warning"),
                        alert.get("message", "因子监控告警"),
                        observed,
                        json_payload(dict(alert)),
                    ],
                )


def _finite_optional(value: object, name: str) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _slope(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    x = list(range(len(values)))
    x_mean = sum(x) / len(x)
    y_mean = sum(values) / len(values)
    denominator = sum((item - x_mean) ** 2 for item in x)
    return (
        sum((item - x_mean) * (value - y_mean) for item, value in zip(x, values, strict=False))
        / denominator
    )


__all__ = ["FactorMonitorReport", "FactorMonitoringService", "FactorObservation"]
