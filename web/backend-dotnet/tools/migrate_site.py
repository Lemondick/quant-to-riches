"""Import a frozen legacy site into an empty PostgreSQL database without Docker.

Account IDs, per-account ledger IDs, Decimal values and existing fingerprints are
preserved. Sessions/recovery credentials are revoked. Unknown source columns fail
the import; analytical DuckDB tables and original evidence remain in the copied
file tree. No credential or account content is written to stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json
from site_files import private_file, relocate_evidence
from site_lock import stopped_site

AUTH_TABLES = (
    "users",
    "site_settings",
    "admin_audit",
    "usage_daily",
    "account_activity",
)
SKIP_TABLES = {
    "sessions",
    "recovery_tokens",
    "schema_migrations",
    "migration_batches",
    "compute_jobs",
    "worker_heartbeats",
    "operation_locks",
}
PRIVATE_PARTS = {"server-secrets", "research-secrets", "secrets", ".env"}
SHANGHAI = timezone(timedelta(hours=8))
ANALYTICAL_TABLES = {
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


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        result = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Frozen sources must not contain symbolic links")
        relative = path.relative_to(root)
        if private_file(relative):
            continue
        if path.is_file() and path.name not in {
            ".site.lock",
            ".site-lock",
            "migration-manifest.json",
        }:
            if not path.resolve().is_relative_to(root):
                raise ValueError("Source file escapes the frozen root")
            result[relative.as_posix()] = digest(path)
    return result


def convert(value, data_type: str):
    if value is None:
        return None
    if data_type in {"json", "jsonb"}:
        return Json(json.loads(value) if isinstance(value, str) else value)
    if data_type == "timestamp with time zone":
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value
    if data_type == "date" and isinstance(value, datetime):
        return value.date()
    if data_type.startswith("numeric"):
        if isinstance(value, float):
            raise ValueError("A monetary value passed through floating point")
        amount = Decimal(value)
        if not amount.is_finite() or amount.copy_abs() > Decimal(79228162514264337593543950335):
            raise ValueError("A monetary value exceeds C# Decimal range")
        digits, exponent = amount.as_tuple().digits, amount.as_tuple().exponent
        while exponent < 0 and digits and digits[-1] == 0:
            digits, exponent = digits[:-1], exponent + 1
        if exponent < -28 or len(digits) + max(exponent, 0) > 29:
            raise ValueError("A monetary value exceeds C# Decimal precision")
        bounds = re.fullmatch(r"numeric\((\d+),(\d+)\)", data_type)
        if bounds:
            precision, scale = map(int, bounds.groups())
            if exponent < -scale or amount.copy_abs() >= Decimal(10) ** (precision - scale):
                raise ValueError("A monetary value would be rounded or overflow in PostgreSQL")
        return amount
    if data_type == "boolean" and type(value) is int and value in (0, 1):
        return bool(value)
    if data_type == "text" and isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def target_schema(cursor) -> dict[str, dict[str, str]]:
    cursor.execute(
        "SELECT table_name,column_name,data_type,numeric_precision,numeric_scale "
        "FROM information_schema.columns "
        "WHERE table_schema='public' ORDER BY table_name,ordinal_position"
    )
    schema = {}
    for table, column, kind, precision, scale in cursor.fetchall():
        if kind == "numeric" and precision is not None:
            kind = f"numeric({precision},{scale})"
        schema.setdefault(table, {})[column] = kind
    return schema


def insert_rows(cursor, schema, table, columns, rows, user_id=None):
    target = schema[table]
    columns = list(columns)
    if user_id is not None:
        columns.insert(0, "user_id")
    unknown = set(columns) - set(target)
    if unknown:
        raise ValueError(f"Unmapped columns in {table}: {sorted(unknown)}")
    statement = sql.SQL("INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.SQL(",").join(sql.Placeholder() for _ in columns),
    )
    count = 0
    for row in rows:
        values = list(row)
        if user_id is not None:
            values.insert(0, user_id)
        cursor.execute(
            statement,
            [convert(value, target[column]) for column, value in zip(columns, values, strict=True)],
        )
        count += 1
    return count


def table_order(cursor, tables):
    cursor.execute(
        "SELECT child.relname,parent.relname FROM pg_constraint c "
        "JOIN pg_class child ON child.oid=c.conrelid "
        "JOIN pg_class parent ON "
        "parent.oid=c.confrelid "
        "JOIN pg_namespace n ON n.oid=child.relnamespace WHERE c.contype='f' AND n.nspname='public'"
    )
    dependencies = {
        (child, parent)
        for child, parent in cursor.fetchall()
        if child in tables and parent in tables and child != parent
    }
    remaining, ordered = set(tables), []
    while remaining:
        ready = sorted(
            name
            for name in remaining
            if not any(child == name and parent in remaining for child, parent in dependencies)
        )
        if not ready:
            raise ValueError("Cyclic database dependency requires an explicit migration")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def migrate(source: Path, destination: Path, dsn: str) -> dict:
    source = source.resolve(strict=True)
    with stopped_site(source):
        return _migrate(source, destination, dsn)


@contextmanager
def frozen_auth(path: Path):
    # SQLite may create/update WAL shared-memory files even for read-only
    # connections. Read a private copy so the frozen source stays unchanged.
    # Preserve a pending WAL; immutable=1 would silently ignore its records.
    with tempfile.TemporaryDirectory(prefix="quant-auth-import-") as directory:
        copied = Path(directory) / path.name
        shutil.copy2(path, copied)
        wal = path.with_name(path.name + "-wal")
        if wal.is_file():
            shutil.copy2(wal, copied.with_name(copied.name + "-wal"))
        with closing(sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)) as auth:
            auth.execute("BEGIN")
            yield auth


def _migrate(source: Path, destination: Path, dsn: str) -> dict:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if (
        source == destination
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise ValueError("Source and destination must be independent directories")
    auth_path = source / "auth" / "accounts.sqlite3"
    if not auth_path.is_file():
        raise ValueError("Frozen source does not contain the legacy account database")
    files = inventory(source)
    fingerprint = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    staging = destination.with_name(destination.name + ".import-" + fingerprint[:12])
    with closing(psycopg2.connect(dsn)) as connection, connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('site:migration',0))")
        cursor.execute(
            "SELECT manifest_json FROM migration_batches WHERE fingerprint=%s",
            (fingerprint,),
        )
        previous = cursor.fetchone()
        if previous:
            if not destination.exists() and staging.is_dir() and inventory(staging) == files:
                staging.rename(destination)
            if not destination.is_dir() or inventory(destination) != files:
                raise ValueError("Previously imported files are missing or changed")
            return {
                "already_imported": True,
                "fingerprint": fingerprint,
                "counts": previous[0]["counts"],
            }
        cursor.execute("SELECT count(*) FROM users")
        if cursor.fetchone()[0] or destination.exists():
            raise ValueError("Import requires an empty database and a new destination directory")
        if staging.exists():
            raise ValueError("Uncommitted staging evidence exists; use a new destination directory")
        schema = target_schema(cursor)
        cursor.execute("DELETE FROM site_settings")
        counts = {}
        with frozen_auth(auth_path) as auth:
            users = auth.execute("SELECT id,role FROM users ORDER BY id").fetchall()
            for user_id, _ in users:
                if not re.fullmatch(r"[a-f0-9]{32}", user_id):
                    raise ValueError("Legacy account identity is invalid")
            for table in AUTH_TABLES:
                rows = auth.execute(f'SELECT * FROM "{table}"')
                counts[table] = insert_rows(
                    cursor, schema, table, [c[0] for c in rows.description], rows
                )
            for user_id, role in users:
                directory = source if role == "admin" else source / "users" / user_id
                database_path = directory / "quant_assistant.duckdb"
                if database_path.exists():
                    with duckdb.connect(str(database_path), read_only=True) as database:
                        tables = [row[0] for row in database.execute("SHOW TABLES").fetchall()]
                        unknown = set(tables) - set(schema) - SKIP_TABLES - ANALYTICAL_TABLES
                        if unknown:
                            raise ValueError(f"Unmapped legacy tables: {sorted(unknown)}")
                        for table in table_order(cursor, tables):
                            if table in ANALYTICAL_TABLES or table in SKIP_TABLES:
                                counts[user_id + "/retained/" + table] = database.execute(
                                    'SELECT count(*) FROM "' + table + '"'
                                ).fetchone()[0]
                                continue
                            if "user_id" not in schema[table]:
                                raise ValueError(
                                    f"Unexpected global table in user database: {table}"
                                )
                            result = database.execute(
                                'SELECT * FROM "' + table.replace('"', '""') + '"'
                            )
                            total = 0
                            columns = [c[0] for c in result.description]
                            if "user_id" in columns:
                                raise ValueError(
                                    "Legacy database unexpectedly already "
                                    "contains account identities"
                                )
                            while batch := result.fetchmany(500):
                                total += insert_rows(cursor, schema, table, columns, batch, user_id)
                            counts[user_id + "/" + table] = total
                import_documents(cursor, directory, user_id)
        for path in sorted((source / "calendars").glob("*.json")):
            from quant_assistant.services.trading_calendar import TradingCalendar

            payload = TradingCalendar.validate(json.loads(path.read_text(encoding="utf-8")))
            cursor.execute(
                "INSERT INTO site_settings(key,value) "
                "VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET "
                "value=EXCLUDED.value",
                ("calendar:" + str(payload["year"]), json.dumps(payload)),
            )
        path_mappings = relocate_evidence(cursor, destination, files)
        # Old active work must be inspected; it must never restart paid calls.
        cursor.execute(
            "UPDATE agent_research_jobs SET "
            "status='interrupted',phase='interrupted',error_code='migration_interrupted' "
            "WHERE status IN ('queued','running','cancel_requested')"
        )
        cursor.execute(
            "UPDATE agent_research_attempts SET "
            "execution_status='interrupted',completed_at=now() "
            "WHERE execution_status IN ('queued','running')"
        )
        cursor.execute(
            "UPDATE market_advisor_reports SET "
            "status='interrupted',error_code='migration_interrupted' "
            "WHERE status IN ('queued','running')"
        )
        cursor.execute("UPDATE model_call_costs SET status='interrupted' WHERE status='reserved'")
        cursor.execute(
            "SELECT c.table_name,c.column_name,"
            "pg_get_serial_sequence(quote_ident(c.table_name),c.column_name),c.column_default "
            "FROM information_schema.columns c WHERE "
            "c.table_schema='public' AND "
            "(c.column_default LIKE 'nextval%%' OR "
            "c.is_identity='YES')"
        )
        for table, column, sequence, default in cursor.fetchall():
            if sequence is None and default:
                match = re.fullmatch(r"nextval\('([a-z_0-9]+)'::regclass\)", default)
                sequence = match[1] if match else None
            if sequence:
                cursor.execute(
                    sql.SQL("SELECT max({}) FROM {}").format(
                        sql.Identifier(column), sql.Identifier(table)
                    )
                )
                maximum = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT setval(%s,%s,%s)",
                    (sequence, max(1, maximum or 1), maximum is not None),
                )
        if inventory(source) != files:
            raise ValueError("Source changed during import; use a frozen backup")
        staging.mkdir(parents=True)
        if os.name != "nt":
            staging.chmod(0o700)
        try:
            for relative in files:
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, target)
                if os.name != "nt":
                    target.chmod(0o600)
            if inventory(staging) != files:
                raise ValueError("Copied files failed checksum verification")
            manifest = {
                "version": 1,
                "fingerprint": fingerprint,
                "counts": counts,
                "files": files,
                "path_mappings": path_mappings,
                "sessions_revoked": True,
                "model_secrets_imported": False,
            }
            cursor.execute(
                "INSERT INTO "
                "migration_batches(fingerprint,format_version,manifest_json) VALUES(%s,1,%s)",
                (fingerprint, Json(manifest)),
            )
            # Commit the database before publication. A failed rename leaves a
            # marked staging directory; operators can finish publication after
            # checking its hashes, without importing records a second time.
            connection.commit()
            staging.rename(destination)
        except BaseException:
            # Retain staging evidence; never remove user-selected directories.
            raise
    return {"already_imported": False, "fingerprint": fingerprint, "counts": counts}


def import_documents(cursor, directory, user_id):
    from dataclasses import asdict

    from quant_assistant.services.preferences import LocalPreferenceStore
    from quant_assistant.services.strategy_settings import (
        StrategyParameters,
        StrategySettingsService,
    )

    snapshot = LocalPreferenceStore(
        directory / "preferences.json",
        legacy_watchlist_path=directory / "watchlist.json",
    ).load()
    preferences = {
        "watchlist": [asdict(item) for item in snapshot.watchlist],
        "read_news_ids": list(snapshot.read_news_ids),
        "revision": snapshot.revision,
        "updated_at": snapshot.updated_at.isoformat() if snapshot.updated_at else None,
    }
    documents = {"preferences": preferences}
    strategy = directory / "strategy-settings.json"
    if strategy.is_file():
        raw = json.loads(strategy.read_text(encoding="utf-8"))
        documents["strategy-settings"] = StrategySettingsService._view(
            StrategyParameters.model_validate(raw["parameters"]), raw.get("updated_at")
        )
    for filename, key in (
        ("advisor-provider.json", "model-provider"),
        ("advisor-jev.json", "model-jev"),
    ):
        path = directory / filename
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            allowed = {"revision", "endpoint", "models", "credential_file"}
            if set(raw) - allowed or type(raw.get("revision")) is not int or raw["revision"] < 0:
                raise ValueError("Legacy model connection requires an explicit mapping")
            documents[key] = {
                name: value for name, value in raw.items() if name != "credential_file"
            }
            documents[key]["key_reference"] = None
    for key, payload in documents.items():
        cursor.execute(
            "INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES(%s,%s,%s,%s)",
            (user_id, key, payload.get("revision", 0), Json(payload)),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--dsn-env", default="QUANT_PG_DSN")
    arguments = parser.parse_args()
    dsn = os.environ.get(arguments.dsn_env)
    if not dsn:
        parser.error("Configure the PostgreSQL DSN in the selected environment variable")
    result = migrate(arguments.frozen_source, arguments.destination, dsn)
    print(
        json.dumps(
            {
                "already_imported": result["already_imported"],
                "fingerprint": result["fingerprint"],
                "tables": len(result["counts"]),
                "rows": sum(result["counts"].values()),
            }
        )
    )


if __name__ == "__main__":
    main()
