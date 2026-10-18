"""Small, allow-listed DuckDB repository."""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import contextmanager, suppress
from datetime import date
from importlib import resources
from pathlib import Path
from queue import Empty, Full, LifoQueue
from threading import Condition, RLock, local
from typing import TYPE_CHECKING

import duckdb
import pandas as pd

if TYPE_CHECKING:
    from quant_assistant.data.snapshots import SnapshotRecord
    from quant_assistant.data.validation import ValidationResult


class Database:
    """Own normalized local state while keeping table names out of user input."""

    WRITABLE_TABLES = {
        "instruments",
        "instrument_history",
        "daily_prices",
        "fund_nav",
        "fund_valuation_history",
        "fund_manager_history",
        "fund_profile_history",
        "fund_holding_history",
        "fund_style_drift_events",
        "fund_fee_schedules",
        "fund_share_families",
        "positions",
        "transactions",
        "signals",
        "proposed_orders",
    }
    DATE_COLUMNS = {"daily_prices": "trade_date", "fund_nav": "nav_date"}

    def __init__(self, path: Path):
        self.path = Path(path)
        # DuckDB accepts ``:memory:`` as a process-local database.  It is not a
        # filesystem path, so ``Path.is_file()`` can never be used as the
        # initialized-state check for this useful test/CLI mode.
        self._is_memory = str(path) == ":memory:"
        self._initialized = False
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._lock = RLock()
        self._reader_state = Condition(RLock())
        self._read_root: duckdb.DuckDBPyConnection | None = None
        self._idle_readers: LifoQueue = LifoQueue(maxsize=8)
        self._active_readers = 0
        self._writer_thread = local()

    def initialize(self) -> None:
        # Service composition creates several repositories around the same
        # Database instance.  Schema DDL is immutable after the first successful
        # run, so avoid reopening DuckDB and replaying every CREATE statement.
        # Re-check the file so a caller can recover after explicitly removing it.
        if (
            self._initialized
            and self._connection is not None
            and (self._is_memory or self.path.is_file())
        ):
            return
        with self._lock:
            if (
                self._initialized
                and self._connection is not None
                and (self._is_memory or self.path.is_file())
            ):
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            schema = (
                resources.files("quant_assistant.models.repositories")
                .joinpath("schema.sql")
                .read_text(encoding="utf-8")
            )
            if self._connection is not None:
                self._close_readers()
                with suppress(Exception):
                    self._connection.close()
            self._connection = duckdb.connect(str(self.path))
            # Ledger dates and API timestamps are defined in Shanghai time;
            # pin DuckDB's session zone so DATE casts behave identically on
            # developer machines and UTC-based CI/production hosts.
            self._connection.execute("SET TimeZone='Asia/Shanghai'")
            self._connection.execute(schema)
            from .symbol_migration import migrate_stock_symbols

            migrate_stock_symbols(self._connection)
            if not self._is_memory:
                # Persist schema migrations before accepting writes. Otherwise
                # an abrupt exit can leave DDL that DuckDB cannot replay from WAL.
                self._connection.execute("CHECKPOINT")
            # Duplicate cursors share the database and its MVCC state without
            # sharing the writer's Python connection or transaction.
            self._read_root = self._connection.cursor()
            self._initialized = True

    @contextmanager
    def connection(self):
        """Yield the shared connection while serializing access.

        DuckDB connections are inexpensive to query but opening one for every
        repository operation is not.  A single connection also makes a sequence
        of append/upsert calls deterministic.  The lock keeps the object safe for
        the small amount of concurrent work performed by the local API.
        """

        self.initialize()
        with self._lock:
            if self._connection is None:  # pragma: no cover - initialize guarantees this
                raise RuntimeError("database connection is not initialized")
            self._writer_thread.depth = getattr(self._writer_thread, "depth", 0) + 1
            try:
                yield self._connection
            finally:
                self._writer_thread.depth -= 1

    @contextmanager
    def read_connection(self):
        """Lease an independent MVCC cursor in an enforced read-only transaction."""

        self.initialize()
        with self._reader_state:
            if self._read_root is None:
                raise RuntimeError("database read connections are closed")
            try:
                reader = self._idle_readers.get_nowait()
            except Empty:
                reader = self._read_root.cursor()
            self._active_readers += 1
        reusable = False
        try:
            reader.execute("SET TimeZone='Asia/Shanghai'")
            reader.execute("BEGIN TRANSACTION READ ONLY")
            yield reader
            reader.commit()
            reusable = True
        except BaseException:
            with suppress(Exception):
                reader.rollback()
            raise
        finally:
            with self._reader_state:
                if reusable:
                    try:
                        self._idle_readers.put_nowait(reader)
                    except Full:
                        reader.close()
                else:
                    reader.close()
                self._active_readers -= 1
                self._reader_state.notify_all()

    def _close_readers(self):
        with self._reader_state:
            # Holding this condition prevents new leases while the pool drains.
            self._reader_state.wait_for(lambda: self._active_readers == 0)
            while not self._idle_readers.empty():
                self._idle_readers.get_nowait().close()
            if self._read_root is not None:
                self._read_root.close()
                self._read_root = None

    @contextmanager
    def transaction(self):
        """Yield the shared connection inside one commit/rollback boundary."""

        with self.connection() as connection:
            connection.begin()
            try:
                yield connection
            except Exception:
                with suppress(Exception):
                    connection.rollback()
                raise
            else:
                connection.commit()

    def close(self) -> None:
        """Close the shared connection; a later operation re-opens it."""

        with self._lock:
            self._initialized = False
            self._close_readers()
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._initialized = False

    def append_frame(self, table: str, frame: pd.DataFrame) -> None:
        self._require_writable(table)
        if frame.empty:
            return
        with self.transaction() as connection:
            try:
                connection.register("incoming", frame)
                connection.execute(f"INSERT INTO {table} BY NAME SELECT * FROM incoming")
            finally:
                with suppress(Exception):
                    connection.unregister("incoming")

    def upsert_frame(self, table: str, frame: pd.DataFrame) -> None:
        """Insert current-state rows, replacing conflicts on declared keys."""

        self._require_writable(table)
        if table not in {
            "instruments",
            "instrument_history",
            "fund_share_families",
            "positions",
            "daily_prices",
            "fund_nav",
            "fund_valuation_history",
            "fund_manager_history",
        }:
            raise ValueError(f"table is not an upsert table: {table}")
        if frame.empty:
            return
        with self.transaction() as connection:
            try:
                connection.register("incoming", frame)
                connection.execute(f"INSERT OR REPLACE INTO {table} BY NAME SELECT * FROM incoming")
            finally:
                with suppress(Exception):
                    connection.unregister("incoming")

    def replace_for_date(
        self,
        table: str,
        as_of_date: date,
        frame: pd.DataFrame,
        *,
        preserve_symbols: Sequence[str] = (),
    ) -> None:
        self._require_writable(table)
        date_column = self.DATE_COLUMNS.get(table)
        if date_column is None:
            raise ValueError(f"table has no replace date mapping: {table}")
        if date_column not in frame.columns:
            raise ValueError(f"incoming frame is missing date column: {date_column}")

        incoming_dates = pd.to_datetime(frame[date_column], errors="coerce").dt.date
        if incoming_dates.isna().any() or not incoming_dates.eq(as_of_date).all():
            raise ValueError(f"all {date_column} values must equal {as_of_date.isoformat()}")

        with self.transaction() as connection:
            condition = f"{date_column} = ?"
            parameters: list[object] = [as_of_date]
            if preserve_symbols:
                condition += " AND symbol NOT IN (SELECT unnest(?))"
                parameters.append(list(preserve_symbols))
            connection.execute(f"DELETE FROM {table} WHERE {condition}", parameters)
            if not frame.empty:
                connection.register("incoming", frame)
                try:
                    connection.execute(f"INSERT INTO {table} BY NAME SELECT * FROM incoming")
                finally:
                    connection.unregister("incoming")

    def query_df(self, sql: str, parameters: Sequence[object] = ()) -> pd.DataFrame:
        # Read-your-writes must keep the writer's transaction when a repository
        # projects facts while holding connection()/transaction().
        context = (
            self.connection if getattr(self._writer_thread, "depth", 0) else self.read_connection
        )
        with context() as connection:
            return connection.execute(sql, list(parameters)).fetchdf()

    def record_snapshot(self, snapshot: SnapshotRecord, validation: ValidationResult) -> int:
        issues = [
            {"code": issue.code, "message": issue.message, "blocking": issue.blocking}
            for issue in validation.issues
        ]
        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT id FROM data_snapshots
                WHERE dataset = ? AND source = ? AND as_of_date = ? AND sha256 = ?
                """,
                [snapshot.dataset, snapshot.source, snapshot.as_of_date, snapshot.sha256],
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE data_snapshots SET status = ?, issues_json = ? WHERE id = ?",
                    [
                        validation.status.value,
                        json.dumps(issues, ensure_ascii=False),
                        existing[0],
                    ],
                )
                return int(existing[0])
            row = connection.execute(
                """
                INSERT INTO data_snapshots (
                    dataset, source, as_of_date, fetched_at, path, sha256,
                    row_count, status, issues_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                [
                    snapshot.dataset,
                    snapshot.source,
                    snapshot.as_of_date,
                    snapshot.fetched_at,
                    str(snapshot.path),
                    snapshot.sha256,
                    snapshot.row_count,
                    validation.status.value,
                    json.dumps(issues, ensure_ascii=False),
                ],
            ).fetchone()
            if row is None:
                raise RuntimeError("DuckDB did not return a snapshot id")
            return int(row[0])

    @classmethod
    def _require_writable(cls, table: str) -> None:
        if table not in cls.WRITABLE_TABLES:
            raise ValueError(f"table is not writable: {table}")
