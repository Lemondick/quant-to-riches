"""List market-only source hashes for the optional AI research evidence manifest.

This opens DuckDB read-only and never reads account, portfolio or ledger tables.
Run while the API/database writers are stopped to avoid DuckDB file locks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/quant_assistant.duckdb"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("database file does not exist; provide --database")
    connection = duckdb.connect(str(args.database), read_only=True)
    try:
        rows = connection.execute("""
            WITH recent AS (
                SELECT source_snapshot_id FROM daily_prices
                QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) <= 200
            )
            SELECT DISTINCT s.id,s.dataset,s.source,s.as_of_date,s.sha256,s.status
            FROM recent p JOIN data_snapshots s ON s.id=p.source_snapshot_id
            WHERE s.dataset='daily_prices' OR s.dataset='point_in_time_prices'
               OR regexp_matches(s.dataset, '^stock_history_[0-9]{6}_(SH|SZ)$')
               OR s.dataset='stock_history'
            ORDER BY s.dataset,s.as_of_date,s.id
        """).fetchall()
    finally:
        connection.close()
    for snapshot_id, dataset, source, day, digest, status in rows:
        print(f"{snapshot_id}\t{dataset}\t{source}\t{day}\t{status}\t{digest}")
    if args.output:
        if args.output.exists():
            parser.error("output already exists; refusing to overwrite reviewed declarations")
        template = {
            digest: {"price_basis": "", "amount_unit": "", "evidence": ""}
            for _, _, _, _, digest, _ in rows
            if digest
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Template saved: {args.output} ({len(template)} source hashes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
