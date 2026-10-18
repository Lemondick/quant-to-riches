"""MVCC reads must not wait on the scheduler's writer connection."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import duckdb

from quant_assistant.models.repositories.database import Database


class ConcurrentReadTests(unittest.TestCase):
    def setUp(self):
        self.database = Database(Path(":memory:"))
        self.database.initialize()
        self.addCleanup(self.database.close)
        with self.database.connection() as connection:
            connection.execute("CREATE TABLE concurrent_sample(value INTEGER)")
            connection.execute("INSERT INTO concurrent_sample VALUES (1)")

    def test_front_read_finishes_while_background_write_is_uncommitted(self):
        entered, release = Event(), Event()

        def writer():
            with self.database.transaction() as connection:
                connection.execute("UPDATE concurrent_sample SET value=2")
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test writer was not released")

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(writer)
            try:
                self.assertTrue(entered.wait(2))
                reader = pool.submit(self.database.query_df, "SELECT value FROM concurrent_sample")
                self.assertEqual(int(reader.result(timeout=1).iloc[0, 0]), 1)
            finally:
                release.set()
            pending.result(timeout=2)
        self.assertEqual(
            int(self.database.query_df("SELECT value FROM concurrent_sample").iloc[0, 0]), 2
        )

    def test_writer_reads_its_own_changes_and_rollback_still_isolates_other_readers(self):
        with (
            self.assertRaisesRegex(ValueError, "rollback"),
            self.database.transaction() as connection,
        ):
            connection.execute("UPDATE concurrent_sample SET value=2")
            self.assertEqual(
                int(self.database.query_df("SELECT value FROM concurrent_sample").iloc[0, 0]), 2
            )
            raise ValueError("rollback")
        self.assertEqual(
            int(self.database.query_df("SELECT value FROM concurrent_sample").iloc[0, 0]), 1
        )

    def test_read_pool_enforces_read_only_transactions(self):
        with (
            self.assertRaises(duckdb.TransactionException),
            self.database.read_connection() as connection,
        ):
            connection.execute("INSERT INTO concurrent_sample VALUES(2)")
        self.assertEqual(len(self.database.query_df("SELECT * FROM concurrent_sample")), 1)

    def test_parallel_read_cursors_are_independent_and_idle_pool_is_bounded(self):
        barrier = Barrier(12)

        def read():
            with self.database.read_connection() as connection:
                barrier.wait(timeout=3)
                return connection.execute("SELECT value FROM concurrent_sample").fetchone()[0]

        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(list(pool.map(lambda _: read(), range(12))), [1] * 12)
        self.assertEqual(self.database._active_readers, 0)
        self.assertLessEqual(self.database._idle_readers.qsize(), 8)

    def test_independent_readers_keep_shanghai_dates(self):
        frame = self.database.query_df(
            "SELECT CAST(TIMESTAMPTZ '2026-10-16 18:00:00+00' AS DATE) AS day"
        )
        self.assertEqual(str(frame.iloc[0, 0].date()), "2026-10-17")


if __name__ == "__main__":
    unittest.main()
