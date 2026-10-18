"""Persistence boundary for reproducible backtest run summaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quant_assistant.common.serialization import canonical_json
from quant_assistant.models.repositories.database import Database

if TYPE_CHECKING:
    from quant_assistant.backtest.engine import BacktestRequest, BacktestResult


def _result_fingerprint(result: BacktestResult) -> str:
    state = result.final_state
    state_payload = None
    if state is not None:
        state_payload = {
            "cash": state.cash,
            "settled_stock": state.settled_stock,
            "unsettled_stock": state.unsettled_stock,
            "stock_average_costs": state.stock_average_costs,
            "fund_units": state.fund_units,
            "unavailable_fund_units": state.unavailable_fund_units,
            "unavailable_cash": state.unavailable_cash,
            "pending_fund_cash": state.pending_fund_cash,
            "fund_fee_schedules": state.fund_fee_schedules,
            "fund_statuses": state.fund_statuses,
            "fund_purchase_open": state.fund_purchase_open,
            "fund_redemption_open": state.fund_redemption_open,
            "fund_acquired_on": state.fund_acquired_on,
            "fund_holding_since": state.fund_holding_since,
        }
    payload = {
        "run_id": result.run_id,
        "trading_dates": result.trading_dates,
        "nav": result.nav.to_dict(orient="records"),
        "orders": result.orders.to_dict(orient="records"),
        "fills": result.fills.to_dict(orient="records"),
        "positions": result.positions.to_dict(orient="records"),
        "research_only": result.research_only,
        "limitations": result.limitations,
        "snapshot_ids": result.snapshot_ids,
        "final_state": state_payload,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class BacktestRepository:
    """Write one immutable summary row per deterministic backtest run id."""

    def __init__(self, database: Database | Path):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()

    def save_result(
        self,
        request: BacktestRequest,
        result: BacktestResult,
        *,
        started_at: datetime,
        completed_at: datetime,
        metrics: Mapping[str, Any] | None = None,
    ) -> None:
        readiness = "research_only" if result.research_only else "paper"
        summary = dict(metrics or {})
        summary.setdefault("trading_sessions", len(result.trading_dates))
        if not result.nav.empty and "total_value" in result.nav:
            summary.setdefault("final_value", result.nav.iloc[-1]["total_value"])
        summary["_result_fingerprint"] = _result_fingerprint(result)

        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT metrics_json FROM backtest_runs WHERE run_id = ?",
                [result.run_id],
            ).fetchone()
            if existing is not None:
                raw_metrics = existing[0]
                stored_metrics = (
                    json.loads(raw_metrics) if isinstance(raw_metrics, str) else raw_metrics
                )
                if (
                    not isinstance(stored_metrics, Mapping)
                    or stored_metrics.get("_result_fingerprint") != summary["_result_fingerprint"]
                ):
                    raise RuntimeError("backtest_run_id_conflict")
                return
            connection.execute(
                """
                INSERT INTO backtest_runs (
                    run_id, started_at, completed_at, start_date, end_date,
                    strategy_version, parameters_json, metrics_json, readiness,
                    limitations_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result.run_id,
                    started_at,
                    completed_at,
                    request.start,
                    request.end,
                    request.strategy_version,
                    canonical_json(request.parameters),
                    canonical_json(summary),
                    readiness,
                    canonical_json(result.limitations),
                    "completed",
                ],
            )
            if not result.nav.empty:
                rows = []
                for item in result.nav.to_dict(orient="records"):
                    rows.append(
                        [
                            result.run_id,
                            item.get("trade_date", item.get("as_of_date")),
                            item.get("total_value"),
                            item.get("unit_nav"),
                            item.get("cash"),
                        ]
                    )
                connection.executemany(
                    """
                    INSERT INTO backtest_nav
                    (run_id, as_of_date, total_value, unit_nav, cash)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (run_id, as_of_date) DO NOTHING
                    """,
                    rows,
                )


__all__ = ["BacktestRepository", "canonical_json"]
