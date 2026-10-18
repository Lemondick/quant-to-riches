"""Operational health, source-status and trend read projections."""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant_assistant.models.entities import DataStatus

from ._base import _DecisionQueriesBase
from ._helpers import (
    _required_date,
    _validate_limit,
)

__all__ = ["_OperationsMixin"]


class _OperationsMixin(_DecisionQueriesBase):
    """Point-in-time, source-status and historical trend health checks."""

    def point_in_time_status(self) -> dict[str, object]:
        """Summarize explicit suspension and price-limit evidence in the local DB."""

        summary = self._query(
            """
            SELECT count(*) AS row_count,
                   count(*) FILTER (WHERE suspended IS NULL) AS missing_suspension_rows,
                   count(*) FILTER (
                       WHERE upper_limit IS NULL OR lower_limit IS NULL
                          OR upper_limit < lower_limit
                   ) AS invalid_limit_rows,
                   max(trade_date) AS as_of_date
            FROM daily_prices
            """
        ).iloc[0]
        row_count = int(summary["row_count"] or 0)
        if row_count == 0:
            return {
                "status": DataStatus.BLOCKED,
                "row_count": 0,
                "missing_suspension_rows": 0,
                "invalid_limit_rows": 0,
                "as_of_date": None,
                "blockers": ("尚无点时停牌/涨跌停证据",),
            }
        missing_suspension = int(summary["missing_suspension_rows"] or 0)
        invalid_limits = int(summary["invalid_limit_rows"] or 0)
        blockers: list[str] = []
        if missing_suspension:
            blockers.append(f"{missing_suspension}行缺少停牌标记")
        if invalid_limits:
            blockers.append(f"{invalid_limits}行涨跌停价格缺失或倒挂")
        return {
            "status": DataStatus.VALID if not blockers else DataStatus.BLOCKED,
            "row_count": row_count,
            "missing_suspension_rows": missing_suspension,
            "invalid_limit_rows": invalid_limits,
            "as_of_date": _required_date(summary["as_of_date"]),
            "blockers": tuple(blockers),
        }

    def market_source_status(self) -> pd.DataFrame:
        """Summarize persisted snapshot sources for operational source auditing."""

        return self._query(
            """
            SELECT source,
                   count(*) AS snapshot_count,
                   count(*) FILTER (WHERE status = 'valid') AS valid_snapshots,
                   max(as_of_date) AS last_data_date,
                   max(fetched_at) AS last_fetched_at
            FROM data_snapshots
            GROUP BY source
            ORDER BY last_fetched_at DESC, source
            """
        )

    def operational_trends(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 180,
    ) -> pd.DataFrame:
        """Aggregate daily decision pass rate and snapshot freshness evidence.

        The query reads immutable strategy runs and data snapshots in DuckDB;
        no UI-side full-table scan or inferred health value is required.  The
        returned ``fetch_lag_days`` is the explicit calendar-day difference
        between a snapshot's data date and its recorded fetch date.
        """

        _validate_limit(limit, "operational trend limit", maximum=730)
        parameters: list[object] = []
        predicates: list[str] = []
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            predicates.append("as_of_date >= ?")
            parameters.append(start_date)
        if end_date is not None:
            if not isinstance(end_date, date):
                raise TypeError("end_date must be a date or None")
            predicates.append("as_of_date <= ?")
            parameters.append(end_date)
        run_filter = " WHERE " + " AND ".join(predicates) if predicates else ""
        snapshot_filter = run_filter
        run_parameters = list(parameters)
        snapshot_parameters = list(parameters)
        rows = self._query(
            f"""
            WITH run_daily AS (
                SELECT as_of_date,
                       count(*) AS decision_runs,
                       count(*) FILTER (WHERE data_status = 'valid') AS valid_decisions,
                       count(*) FILTER (WHERE data_status = 'stale') AS stale_decisions,
                       count(*) FILTER (WHERE data_status = 'blocked') AS blocked_decisions
                FROM strategy_runs{run_filter}
                GROUP BY as_of_date
            ), snapshot_daily AS (
                SELECT as_of_date,
                       count(*) AS snapshot_count,
                       count(*) FILTER (WHERE status = 'valid') AS valid_snapshots,
                       count(DISTINCT source) AS source_count,
                       max(fetched_at) AS latest_fetched_at,
                       greatest(
                           0,
                           max(date_diff('day', as_of_date, CAST(fetched_at AS DATE)))
                       )
                           AS fetch_lag_days
                FROM data_snapshots{snapshot_filter}
                GROUP BY as_of_date
            )
            SELECT coalesce(r.as_of_date, s.as_of_date) AS as_of_date,
                   coalesce(r.decision_runs, 0) AS decision_runs,
                   coalesce(r.valid_decisions, 0) AS valid_decisions,
                   coalesce(r.stale_decisions, 0) AS stale_decisions,
                   coalesce(r.blocked_decisions, 0) AS blocked_decisions,
                   CASE WHEN r.decision_runs > 0
                        THEN CAST(r.valid_decisions AS DOUBLE) / r.decision_runs
                        ELSE NULL END AS decision_pass_rate,
                   coalesce(s.snapshot_count, 0) AS snapshot_count,
                   coalesce(s.valid_snapshots, 0) AS valid_snapshots,
                   coalesce(s.source_count, 0) AS source_count,
                   CASE WHEN s.snapshot_count > 0
                        THEN CAST(s.valid_snapshots AS DOUBLE) / s.snapshot_count
                        ELSE NULL END AS snapshot_valid_rate,
                   s.latest_fetched_at,
                   s.fetch_lag_days
            FROM run_daily r
            FULL OUTER JOIN snapshot_daily s USING (as_of_date)
            ORDER BY as_of_date DESC
            LIMIT ?
            """,
            [*run_parameters, *snapshot_parameters, limit],
        )
        if rows.empty:
            return rows
        rows["as_of_date"] = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
        return rows.sort_values("as_of_date", kind="mergesort").reset_index(drop=True)

    def source_health_trends(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 730,
    ) -> pd.DataFrame:
        """Return per-source daily health ratios from persisted snapshots."""

        _validate_limit(limit, "source health trend limit", maximum=2000)
        parameters: list[object] = []
        predicates: list[str] = []
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            predicates.append("as_of_date >= ?")
            parameters.append(start_date)
        if end_date is not None:
            if not isinstance(end_date, date):
                raise TypeError("end_date must be a date or None")
            predicates.append("as_of_date <= ?")
            parameters.append(end_date)
        where = " WHERE " + " AND ".join(predicates) if predicates else ""
        rows = self._query(
            f"""
            SELECT as_of_date, source,
                   count(*) AS snapshot_count,
                   count(*) FILTER (WHERE status = 'valid') AS valid_snapshots,
                   CAST(count(*) FILTER (WHERE status = 'valid') AS DOUBLE)
                       / NULLIF(count(*), 0) AS valid_snapshot_rate,
                   max(fetched_at) AS latest_fetched_at
            FROM data_snapshots{where}
            GROUP BY as_of_date, source
            ORDER BY as_of_date DESC, source
            LIMIT ?
            """,
            [*parameters, limit],
        )
        if rows.empty:
            return rows
        rows["as_of_date"] = pd.to_datetime(rows["as_of_date"], errors="coerce").dt.date
        return rows.sort_values(["as_of_date", "source"], kind="mergesort").reset_index(drop=True)
