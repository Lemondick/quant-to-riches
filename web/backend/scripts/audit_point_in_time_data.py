"""Audit point-in-time coverage and unresolved data limitations in the local database."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from quant_assistant.config import Settings
from quant_assistant.models.repositories.database import Database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    database = Database(Settings.from_env().database_path)
    database.initialize()
    payload = audit(database, args.as_of)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "as_of": payload["as_of"],
                    "point_in_time_status": payload["point_in_time_status"],
                    "blocking_findings": payload["blocking_findings"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(text)
    return 0 if not payload["blocking_findings"] else 2


def audit(database: Database, as_of: date | None = None) -> dict[str, object]:
    cutoff = as_of or _latest_snapshot_date(database)
    snapshots = database.query_df(
        """
        SELECT dataset, max(as_of_date) AS latest_date,
               count(*) AS snapshot_count,
               sum(CASE WHEN status = 'valid' THEN 1 ELSE 0 END) AS valid_count
        FROM data_snapshots
        WHERE as_of_date <= ?
        GROUP BY dataset ORDER BY dataset
        """,
        [cutoff],
    )
    history = database.query_df(
        """
        SELECT count(DISTINCT as_of_date) AS snapshot_dates,
               count(DISTINCT symbol) AS symbols,
               min(as_of_date) AS first_date,
               max(as_of_date) AS latest_date
        FROM instrument_history WHERE as_of_date <= ?
        """,
        [cutoff],
    ).iloc[0]
    universe_coverage = database.query_df(
        """
        SELECT count(*) AS missing_universe_sessions
        FROM (
            SELECT DISTINCT p.trade_date
            FROM daily_prices AS p
            LEFT JOIN (
                SELECT DISTINCT as_of_date
                FROM instrument_history
                WHERE as_of_date <= ?
            ) AS h ON h.as_of_date = p.trade_date
            WHERE p.trade_date <= ? AND h.as_of_date IS NULL
        )
        """,
        [cutoff, cutoff],
    ).iloc[0]
    prices = database.query_df(
        """
        SELECT count(*) AS rows,
               count(DISTINCT trade_date) AS sessions,
               count(DISTINCT symbol) AS symbols,
               min(trade_date) AS first_date,
               max(trade_date) AS latest_date,
               count(suspended) AS suspension_evidence_rows,
               count(upper_limit) AS upper_limit_evidence_rows,
               count(lower_limit) AS lower_limit_evidence_rows
        FROM daily_prices WHERE trade_date <= ?
        """,
        [cutoff],
    ).iloc[0]
    blocking: list[str] = []
    required = {"instruments", "daily_prices", "index_history"}
    available = set(snapshots["dataset"].astype(str))
    missing = sorted(required - available)
    if missing:
        blocking.append("missing_required_snapshots:" + ",".join(missing))
    invalid_required = sorted(
        set(
            snapshots.loc[
                snapshots["dataset"].astype(str).isin(required)
                & snapshots["valid_count"].fillna(0).astype(int).eq(0),
                "dataset",
            ].astype(str)
        )
    )
    if invalid_required:
        blocking.append("required_snapshots_not_valid:" + ",".join(invalid_required))
    if int(history["snapshot_dates"] or 0) < 2:
        blocking.append("instrument_history_has_fewer_than_two_dates")
    limitations: list[str] = []
    if int(universe_coverage["missing_universe_sessions"] or 0) > 0:
        limitations.append("historical_universe_reconstructed_from_current_snapshot")
    total_rows = int(prices["rows"] or 0)
    if int(prices["suspension_evidence_rows"] or 0) < total_rows:
        limitations.append("missing_historical_suspension_flags")
    if any(
        int(prices[column] or 0) < total_rows
        for column in ("upper_limit_evidence_rows", "lower_limit_evidence_rows")
    ):
        limitations.append("estimated_historical_price_limits")
    blocking.extend(limitations)
    return {
        "as_of": cutoff.isoformat(),
        "snapshots": snapshots.to_dict(orient="records"),
        "instrument_history": history.to_dict(),
        "universe_coverage": universe_coverage.to_dict(),
        "daily_prices": prices.to_dict(),
        "point_in_time_status": "ready" if not blocking else "blocked",
        "limitations": limitations,
        "blocking_findings": blocking,
    }


def _latest_snapshot_date(database: Database) -> date:
    value = database.query_df("SELECT max(as_of_date) AS latest_date FROM data_snapshots").iloc[0][
        "latest_date"
    ]
    if value is None:
        raise ValueError("database contains no data snapshots")
    return value.date() if hasattr(value, "date") else value


if __name__ == "__main__":
    raise SystemExit(main())
