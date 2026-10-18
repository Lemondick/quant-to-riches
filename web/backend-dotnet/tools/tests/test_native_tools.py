"""Migration and recovery checks; PostgreSQL integration requires an explicit test DSN."""

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import export_legacy_site
import migrate_site
import site_archive
from site_files import evidence_relative
from site_lock import stopped_site

from quant_assistant.models.repositories.auth import AuthRepository
from quant_assistant.models.repositories.database import Database


class NativeToolTests(unittest.TestCase):
    def test_frozen_auth_reads_uncheckpointed_wal_without_changing_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.sqlite3"
            with closing(sqlite3.connect(path)) as live:
                live.execute("PRAGMA journal_mode=WAL")
                live.execute("PRAGMA wal_autocheckpoint=0")
                live.execute("CREATE TABLE synthetic(value TEXT)")
                live.execute("INSERT INTO synthetic VALUES('pending-wal-record')")
                live.commit()
                self.assertTrue(path.with_name(path.name + "-wal").is_file())
                before = migrate_site.inventory(path.parent)
                with migrate_site.frozen_auth(path) as copied:
                    self.assertEqual(
                        copied.execute("SELECT value FROM synthetic").fetchone()[0],
                        "pending-wal-record",
                    )
                self.assertEqual(before, migrate_site.inventory(path.parent))

    def test_decimal_limits_do_not_round_or_reject_insignificant_zeroes(self):
        fits = export_legacy_site.decimal_fits
        self.assertTrue(fits(Decimal("1.234567890000"), 28, 8))
        self.assertTrue(fits(Decimal("99999999999999999999.99999999"), 28, 8))
        self.assertFalse(fits(Decimal("1.234567891"), 28, 8))
        self.assertFalse(fits(Decimal("100000000000000000000"), 28, 8))
        self.assertFalse(fits(Decimal("NaN"), 28, 8))

    def test_evidence_suffix_matches_only_the_owning_account(self):
        files = {"users/first/prices/data.csv": "a", "users/second/prices/data.csv": "b"}
        self.assertEqual(
            evidence_relative("C:/old/prices/data.csv", "first", "user", files),
            "users/first/prices/data.csv",
        )
        self.assertIsNone(evidence_relative("", "first", "user", files))
        self.assertIsNone(evidence_relative("C:/old/prices/data.csv", "admin", "admin", files))

    def test_root_level_evidence_uses_the_complete_account_namespace(self):
        files = {"users/first/prices.parquet": "a", "users/second/prices.parquet": "b"}
        self.assertEqual(
            evidence_relative(r"C:\old\site\users\first\prices.parquet", "first", "user", files),
            "users/first/prices.parquet",
        )
        self.assertIsNone(evidence_relative("C:/old/prices.parquet", "first", "user", files))
        self.assertIsNone(
            evidence_relative("C:/old/users/second/prices.parquet", "first", "user", files)
        )

    def test_explicit_foreign_account_evidence_is_never_rebound_to_a_same_named_file(self):
        files = {"users/first/prices/data.csv": "a", "users/second/prices/data.csv": "b"}
        self.assertIsNone(
            evidence_relative("C:/old/users/second/prices/data.csv", "first", "user", files)
        )

    def test_running_site_excludes_a_second_archive_writer(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            stopped_site(Path(directory)),
            self.assertRaises(ValueError),
            stopped_site(Path(directory)),
        ):
            self.fail("A second writer acquired the site lock")


@unittest.skipUnless(os.environ.get("QUANT_TEST_PG_DSN"), "Set the isolated test PostgreSQL DSN")
class NativeMigrationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quant-migration-test-")
        self.root = Path(self.temporary.name)
        self.databases = []
        self.dsn = os.environ["QUANT_TEST_PG_DSN"]
        self.frozen = self.root / "frozen"
        self.frozen.mkdir()
        registry = AuthRepository(self.frozen)
        self.users = ["a" * 32, "b" * 32]
        with registry.connection(write=True) as auth:
            for user, name, role in zip(
                self.users, ("admin_test", "user_test"), ("admin", "user"), strict=True
            ):
                auth.execute(
                    "INSERT INTO users(id,username,password_hash,role,status,created_at) "
                    "VALUES(?,?,? ,?,'active',?)",
                    (user, name, "synthetic-test-hash", role, 1_700_000_000),
                )
        for prefix in (Path(), Path("users") / self.users[1]):
            database = Database(self.frozen / prefix / "quant_assistant.duckdb")
            with database.connection() as legacy:
                legacy.execute(
                    "INSERT INTO positions(symbol,asset_type,quantity,average_cost,market_value,"
                    "as_of_date,updated_at) VALUES('000001','fund',1.23456789,1.25,1.54320986,"
                    "DATE '2026-01-02',TIMESTAMPTZ '2026-01-02 15:00:00+08:00')"
                )
                legacy.execute(
                    "INSERT INTO audit_log(id,entity_type,entity_id,changed_at,after_json) "
                    "VALUES(42,'position','000001',TIMESTAMP '2026-01-02 15:00:00','{}')"
                )
            database.close()
        (self.frozen / "synthetic.key").write_text("synthetic-only", encoding="utf-8")

    def database(self, migrated=False):
        name = "quant_migration_test_" + uuid4().hex
        connection = psycopg2.connect(self.dsn)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        connection.close()
        self.databases.append(name)
        dsn = make_dsn(self.dsn, dbname=name)
        if migrated:
            with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE schema_migrations(version text PRIMARY KEY, "
                    "applied_at timestamptz NOT NULL DEFAULT now())"
                )
                for path in sorted(
                    (Path(__file__).resolve().parents[2] / "migrations").glob("*.sql")
                ):
                    cursor.execute(path.read_text(encoding="utf-8"))
                    cursor.execute(
                        "INSERT INTO schema_migrations(version) VALUES(%s)", (path.name,)
                    )
        return dsn

    def pg_environment(self, dsn):
        values = parse_dsn(dsn)
        return {
            "PGHOST": values.get("host", "127.0.0.1"),
            "PGPORT": values.get("port", "5432"),
            "PGUSER": values["user"],
            "PGDATABASE": values["dbname"],
            "PGPASSWORD": values.get("password", ""),
        }

    def imported(self):
        dsn = self.database(migrated=True)
        native = self.root / "native"
        result = migrate_site.migrate(self.frozen, native, dsn)
        self.assertFalse(result["already_imported"])
        self.assertTrue(migrate_site.migrate(self.frozen, native, dsn)["already_imported"])
        return native, dsn

    def test_forward_and_reverse_preserve_accounts_money_and_next_ids(self):
        native, dsn = self.imported()
        with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE positions SET quantity=2.23456789 WHERE user_id=%s", (self.users[1],)
            )
        rollback = self.root / "rollback"
        export_legacy_site.export(native, rollback, dsn)
        database = Database(rollback / "users" / self.users[1] / "quant_assistant.duckdb")
        try:
            with database.connection() as legacy:
                self.assertEqual(
                    legacy.execute("SELECT quantity FROM positions").fetchone()[0],
                    Decimal("2.23456789"),
                )
                identity = legacy.execute(
                    "INSERT INTO audit_log(entity_type,entity_id,changed_at,after_json) "
                    "VALUES('position','000001',current_timestamp,'{}') RETURNING id"
                ).fetchone()[0]
                self.assertEqual(identity, 43)
        finally:
            database.close()
        self.assertFalse((native / "synthetic.key").exists())

    def test_restore_can_resume_after_preparation_and_refuses_later_writes(self):
        native, dsn = self.imported()
        with patch.dict(os.environ, self.pg_environment(dsn)):
            archive = site_archive.backup(native, self.root / "backups", 14)
        restored_dsn = self.database()
        destination = self.root / "restored"
        with patch.dict(os.environ, self.pg_environment(restored_dsn)):
            with (
                patch.object(
                    site_archive, "prepare_restore", side_effect=OSError("synthetic fault")
                ),
                self.assertRaises(OSError),
            ):
                site_archive.restore(archive, destination)
            with psycopg2.connect(restored_dsn) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status,database_fingerprint FROM site_restore_runs "
                    "WHERE archive_sha256=%s",
                    (site_archive.sha256(archive),),
                )
                status, fingerprint = cursor.fetchone()
                self.assertEqual(status, "database_restored")
                self.assertEqual(fingerprint, site_archive.database_fingerprint(cursor))
            site_archive.complete_restore(archive, destination)
            self.assertTrue(destination.is_dir())
        self.assertTrue(site_archive.verify(archive))
        self.assertFalse((destination / "synthetic.key").exists())
        changed_dsn = self.database()
        with patch.dict(os.environ, self.pg_environment(changed_dsn)):
            with (
                patch.object(
                    site_archive, "prepare_restore", side_effect=OSError("synthetic fault")
                ),
                self.assertRaises(OSError),
            ):
                site_archive.restore(archive, self.root / "changed")
            with psycopg2.connect(changed_dsn) as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE positions SET quantity=quantity+1")
            with self.assertRaisesRegex(ValueError, "Database changed"):
                site_archive.complete_restore(archive, self.root / "changed")

    def tearDown(self):
        connection = psycopg2.connect(self.dsn)
        connection.autocommit = True
        with connection.cursor() as cursor:
            for name in self.databases:
                if not name.startswith("quant_migration_test_"):
                    raise AssertionError("Refusing to remove an unrelated test database")
                cursor.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
                )
        connection.close()
        self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
