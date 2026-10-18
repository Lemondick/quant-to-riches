"""User-defined portfolio benchmark persistence and comparison."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from quant_assistant.common.time import now_from_clock
from quant_assistant.common.validation import date_value, json_payload, text
from quant_assistant.models.repositories.database import Database


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    benchmark_id: str
    name: str
    components: tuple[tuple[str, float], ...]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class BenchmarkService:
    def __init__(self, database: Database | Path, clock: object | None = None):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        self._clock = clock

    def create(
        self,
        name: str,
        components: Mapping[str, float],
        *,
        benchmark_id: str | None = None,
        is_default: bool = False,
    ) -> BenchmarkConfig:
        clean_name = text(name, "name")
        if not isinstance(components, Mapping) or not components:
            raise ValueError("benchmark components cannot be empty")
        normalized: list[tuple[str, float]] = []
        for symbol, weight in components.items():
            code = text(symbol, "component symbol", maximum=32)
            if (
                not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or not math.isfinite(float(weight))
                or float(weight) < 0
            ):
                raise ValueError("benchmark weights must be finite and non-negative")
            normalized.append((code, float(weight)))
        total = sum(weight for _, weight in normalized)
        if abs(total - 1.0) > 1e-6:
            raise ValueError("benchmark weights must sum to 1")
        now = now_from_clock(self._clock)
        item = BenchmarkConfig(
            benchmark_id or uuid.uuid4().hex,
            clean_name,
            tuple(sorted(normalized)),
            bool(is_default),
            now,
            now,
        )
        with self.database.transaction() as connection:
            if item.is_default:
                connection.execute("UPDATE benchmark_configs SET is_default = FALSE")
            connection.execute(
                """
                INSERT INTO benchmark_configs
                (benchmark_id, name, components_json, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    item.benchmark_id,
                    item.name,
                    json_payload(dict(item.components)),
                    item.is_default,
                    item.created_at,
                    item.updated_at,
                ],
            )
        return item

    def list(self, *, limit: int = 100) -> tuple[BenchmarkConfig, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("benchmark limit must be between 1 and 500")
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM benchmark_configs ORDER BY is_default DESC, name LIMIT ?", [limit]
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def compare(
        self, benchmark_id: str, *, start_date: date | None = None, end_date: date | None = None
    ) -> dict[str, object]:
        identifier = text(benchmark_id, "benchmark_id", maximum=128)
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_configs WHERE benchmark_id = ?", [identifier]
            ).fetchone()
        if row is None:
            raise ValueError("benchmark_not_found")
        config = self._row(row)
        start = None if start_date is None else date_value(start_date, "start_date")
        end = None if end_date is None else date_value(end_date, "end_date")
        if start is not None and end is not None and end < start:
            raise ValueError("end_date must not precede start_date")
        symbols = [symbol for symbol, _ in config.components]
        placeholders = ",".join("?" for _ in symbols)
        sql = f"SELECT symbol, trade_date, close FROM daily_prices WHERE symbol IN ({placeholders})"
        values: list[object] = list(symbols)
        if start is not None:
            sql += " AND trade_date >= ?"
            values.append(start)
        if end is not None:
            sql += " AND trade_date <= ?"
            values.append(end)
        sql += " ORDER BY trade_date, symbol"
        with self.database.connection() as connection:
            frame = connection.execute(sql, values).fetchdf()
        if frame.empty:
            return {
                "benchmark_id": config.benchmark_id,
                "name": config.name,
                "components": dict(config.components),
                "observations": 0,
                "return": None,
                "research_only": True,
            }
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        active = {symbol: weight for symbol, weight in config.components if weight > 0}
        pivot = frame.pivot(index="trade_date", columns="symbol", values="close").sort_index()
        required = [symbol for symbol in active if symbol != "CASH"]
        if any(symbol not in pivot for symbol in required):
            raise ValueError("benchmark_component_prices_missing")
        pivot = pivot.reindex(columns=required)
        if pivot.isna().any().any() or (pivot <= 0).any().any():
            raise ValueError("benchmark_component_prices_incomplete")
        if not all(math.isfinite(float(value)) for value in pivot.to_numpy().flat):
            raise ValueError("benchmark_component_prices_invalid")
        returns = pivot.pct_change(fill_method=None).iloc[1:]
        weighted = pd.Series(0.0, index=returns.index)
        for symbol, weight in config.components:
            if symbol in returns:
                weighted = weighted.add(returns[symbol].fillna(0) * weight, fill_value=0)
        total = float((1 + weighted).prod() - 1) if not weighted.empty else None
        with self.database.connection() as connection:
            nav_rows = connection.execute(
                "SELECT as_of_date, unit_nav FROM portfolio_valuations "
                "WHERE as_of_date IN (?, ?) "
                "QUALIFY row_number() OVER (PARTITION BY as_of_date "
                "ORDER BY generated_at DESC) = 1 ORDER BY as_of_date",
                [pivot.index[0].date(), pivot.index[-1].date()],
            ).fetchall()
        portfolio_return = None
        if len(nav_rows) >= 2:
            first = float(nav_rows[0][1])
            last = float(nav_rows[-1][1])
            if math.isfinite(first) and math.isfinite(last) and first > 0 and last > 0:
                portfolio_return = last / first - 1
        return {
            "benchmark_id": config.benchmark_id,
            "name": config.name,
            "components": dict(config.components),
            "observations": int(len(weighted)),
            "return": total,
            "portfolio_return": portfolio_return,
            "excess_return": (
                None if total is None or portfolio_return is None else portfolio_return - total
            ),
            "research_only": True,
        }

    def _row(self, row: Sequence[object]) -> BenchmarkConfig:
        components = json.loads(row[2]) if isinstance(row[2], str) else row[2]
        return BenchmarkConfig(
            str(row[0]),
            str(row[1]),
            tuple((str(key), float(value)) for key, value in dict(components or {}).items()),
            bool(row[3]),
            row[4],
            row[5],
        )


__all__ = ["BenchmarkConfig", "BenchmarkService"]
