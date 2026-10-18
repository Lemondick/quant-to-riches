"""Fund-side historical evidence read projections."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ._base import _DecisionQueriesBase
from ._helpers import _validate_limit

__all__ = ["_FundsMixin"]


class _FundsMixin(_DecisionQueriesBase):
    """Valuation, manager history, lifecycle events and style drift."""

    def fund_valuation_history(
        self,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read persisted PE/PB evidence for Smart DCA and audit views."""

        parameters: list[object] = []
        filters: list[str] = []
        if symbol is not None:
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol must be a non-empty string or None")
            filters.append("symbol = ?")
            parameters.append(symbol.strip().upper())
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            filters.append("as_of_date >= ?")
            parameters.append(start_date)
        if end_date is not None:
            if not isinstance(end_date, date):
                raise TypeError("end_date must be a date or None")
            filters.append("as_of_date <= ?")
            parameters.append(end_date)
        sql = """
            SELECT symbol, as_of_date, pe, pb, source, source_snapshot_id
            FROM fund_valuation_history
        """
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY symbol, as_of_date"
        return self._query(sql, parameters)

    def fund_manager_history(
        self,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read versioned manager archive evidence without filling missing factors."""

        parameters: list[object] = []
        filters: list[str] = []
        if symbol is not None:
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol must be a non-empty string or None")
            filters.append("symbol = ?")
            parameters.append(symbol.strip().upper())
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            filters.append("as_of_date >= ?")
            parameters.append(start_date)
        if end_date is not None:
            if not isinstance(end_date, date):
                raise TypeError("end_date must be a date or None")
            filters.append("as_of_date <= ?")
            parameters.append(end_date)
        sql = """
            SELECT symbol, as_of_date, manager_name, manager_tenure_years,
                   manager_win_rate, alpha_stability, drawdown_recovery_days,
                   source, source_snapshot_id
            FROM fund_manager_history
        """
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY symbol, as_of_date"
        return self._query(sql, parameters)

    def fund_lifecycle_events(
        self,
        *,
        symbol: str | None = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Read persisted fund lifecycle changes in newest-first order."""

        _validate_limit(limit, "fund lifecycle event limit", maximum=500)
        parameters: list[object] = []
        filters: list[str] = []
        if symbol is not None:
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol must be a non-empty string or None")
            filters.append("symbol = ?")
            parameters.append(symbol.strip().upper())
        sql = """
            SELECT id, symbol, observed_on, event_type, severity, message,
                   previous_value, current_value, source, details_json, created_at
            FROM fund_lifecycle_events
        """
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY observed_on DESC, id DESC LIMIT ?"
        parameters.append(limit)
        rows = self._query(sql, parameters)
        if not rows.empty:
            rows["observed_on"] = pd.to_datetime(rows["observed_on"], errors="coerce").dt.date
        return rows

    def fund_style_drift_events(
        self,
        *,
        symbol: str | None = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Read report-period holding overlap and drift events newest first."""

        _validate_limit(limit, "fund style drift event limit", maximum=500)
        parameters: list[object] = []
        filters: list[str] = []
        if symbol is not None:
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol must be a non-empty string or None")
            filters.append("symbol = ?")
            parameters.append(symbol.strip().upper())
        sql = """
            SELECT id, symbol, report_date, previous_report_date, severity,
                   overlap_ratio, message, added_json, removed_json, source,
                   details_json, created_at
            FROM fund_style_drift_events
        """
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY report_date DESC, id DESC LIMIT ?"
        parameters.append(limit)
        rows = self._query(sql, parameters)
        if not rows.empty:
            for column in ("report_date", "previous_report_date"):
                rows[column] = pd.to_datetime(rows[column], errors="coerce").dt.date
        return rows
