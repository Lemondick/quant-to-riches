"""Isolated research input: copied analytical tables plus C# approved facts.

The legacy business database is never writable. Algorithms execute against an
in-memory DuckDB populated from a bounded, authenticated account snapshot.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from quant_assistant.models.repositories.database import Database
from quant_assistant.reporting.queries import DecisionQueries
from quant_assistant.services.api_queries import LocalApiQueries

ANALYTICAL = {
    "instrument_history",
    "daily_prices",
    "fund_nav",
    "fund_valuation_history",
    "fund_manager_history",
    "fund_profile_history",
    "fund_lifecycle_events",
    "fund_holding_history",
    "fund_style_drift_events",
    "backtest_runs",
    "backtest_nav",
    "stock_rankings",
}
FACTS = {
    "instruments",
    "positions",
    "transactions",
    "dividend_events",
    "ledger_corrections",
    "corporate_action_events",
    "portfolio_events",
    "portfolio_valuations",
    "strategy_runs",
    "signals",
    "proposed_orders",
    "data_snapshots",
    "fund_share_families",
    "fund_fee_schedules",
    "factor_observations",
    "drawdown_validations",
    "benchmark_configs",
    "macro_observations",
    "portfolio_accounts",
    "account_positions",
    "account_events",
    "fund_research_snapshots",
    "advisor_decision_snapshots",
    "operational_alerts",
    "job_heartbeats",
    "paper_orders",
    "paper_fills",
    "paper_reconciliations",
    "audit_log",
    "market_advisor_reports",
    "fund_lot_snapshots",
    "fund_holding_lots",
}


def site_directory(snapshot):
    if (
        snapshot.get("version") != 1
        or not re.fullmatch(r"[a-f0-9]{32}", snapshot.get("user_id", ""))
        or snapshot.get("role") not in {"admin", "user"}
    ):
        raise ValueError("invalid_account_snapshot")
    configured = os.environ.get("QUANT_SITE_ROOT")
    if not configured:
        raise RuntimeError("site_root_unconfigured")
    root = Path(configured).resolve(strict=True)
    directory = root if snapshot["role"] == "admin" else root / "users" / snapshot["user_id"]
    resolved = directory.resolve()
    if resolved != directory or not resolved.is_relative_to(root):
        raise ValueError("account_directory_escape")
    return resolved


def build_context(snapshot):
    directory = site_directory(snapshot)
    tables = snapshot.get("tables")
    if not isinstance(tables, dict) or set(tables) != FACTS:
        raise ValueError("invalid_account_fact_tables")
    database = Database(Path(":memory:"))
    database.initialize()
    with database.connection() as connection:
        source = directory / "quant_assistant.duckdb"
        if source.is_file():
            if source.is_symlink() or not source.resolve().is_relative_to(directory):
                raise ValueError("analytical_database_escape")
            quoted = str(source).replace("'", "''")
            connection.execute(f"ATTACH '{quoted}' AS analytical_source (READ_ONLY)")
            available = {
                row[0]
                for row in connection.execute("SHOW TABLES FROM analytical_source").fetchall()
            }
            for table in sorted(ANALYTICAL & available):
                connection.execute(f'DELETE FROM "{table}"')
                connection.execute(
                    f'INSERT INTO "{table}" BY NAME SELECT * FROM analytical_source.main."{table}"'
                )
            connection.execute("DETACH analytical_source")
        for table, rows in tables.items():
            if not isinstance(rows, list) or len(rows) > 50000:
                raise ValueError("invalid_account_fact_rows")
            connection.execute(f'DELETE FROM "{table}"')
            target_columns = {
                row[0]: row[1] for row in connection.execute(f'DESCRIBE "{table}"').fetchall()
            }
            for row in rows:
                if not isinstance(row, dict) or not set(row) <= set(target_columns):
                    raise ValueError("invalid_account_fact_columns")
                columns = list(row)
                values = [
                    json.dumps(row[column], ensure_ascii=False, allow_nan=False)
                    if target_columns[column] == "JSON" and row[column] is not None
                    else row[column]
                    for column in columns
                ]
                names = ",".join('"' + column + '"' for column in columns)
                connection.execute(
                    f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in columns)})',
                    values,
                )
        # New formal NAV files are accepted into PostgreSQL by C# before an
        # analytical projection can consume them. Verify immutable source content.
        import pandas as pd

        from quant_assistant.data.snapshots import _frame_sha256

        for row in tables["data_snapshots"]:
            if row["dataset"] != "fund_nav" or row["status"] != "valid":
                continue
            source = Path(row["path"])
            if not source.is_file():
                continue  # Missing historical files remain an admission blocker.
            if source.is_symlink() or not source.resolve().is_relative_to(directory):
                raise ValueError("snapshot_file_escape")
            frame = pd.read_parquet(source)
            if _frame_sha256(frame) != row["sha256"] or len(frame) != row["row_count"]:
                raise ValueError("snapshot_integrity_failed")
            for point in frame.to_dict("records"):
                symbol, day = str(point["symbol"]), str(point["nav_date"])[:10]
                old = connection.execute(
                    "SELECT unit_nav FROM fund_nav WHERE symbol=? AND nav_date=?",
                    [symbol, day],
                ).fetchone()
                if old is not None and old[0] != float(point["unit_nav"]):
                    raise ValueError("formal_nav_conflict")
                connection.execute(
                    (
                        "INSERT INTO fund_nav VALUES(?,?,?,?,?) ON "
                        "CONFLICT(symbol,nav_date) DO UPDATE SET "
                        "source_snapshot_id=excluded.source_snapshot_id"
                    ),
                    [
                        symbol,
                        day,
                        point["unit_nav"],
                        point.get("accumulated_nav"),
                        row["id"],
                    ],
                )
    queries = LocalApiQueries(DecisionQueries(database), version="1.0")
    return database, queries, directory
