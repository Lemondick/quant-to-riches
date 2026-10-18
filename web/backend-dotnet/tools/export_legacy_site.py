"""Export a stopped native site into a NEW legacy-compatible rollback directory.

Never overwrite an existing site. Sessions and recovery tokens remain revoked;
model keys require a separate private credential export or reconfiguration.
Analytical files remain unchanged. Current business records come from PostgreSQL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg2
from migrate_site import ANALYTICAL_TABLES, AUTH_TABLES, SHANGHAI, inventory
from psycopg2 import sql
from site_files import evidence_relative
from site_lock import stopped_site

NATIVE_ONLY = {
    "users",
    "sessions",
    "site_settings",
    "admin_audit",
    "usage_daily",
    "account_activity",
    "recovery_tokens",
    "schema_migrations",
    "migration_batches",
    "user_documents",
    "compute_jobs",
    "worker_heartbeats",
    "operation_locks",
    "review_sessions",
    "site_restore_runs",
}


def legacy_value(value, kind):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if isinstance(value, datetime) and value.tzinfo:
        local = value.astimezone(SHANGHAI)
        return local if "WITH TIME ZONE" in kind else local.replace(tzinfo=None)
    return value


def duck_type(kind):
    return {
        "json": "JSON",
        "jsonb": "JSON",
        "timestamp with time zone": "TIMESTAMPTZ",
        "timestamp without time zone": "TIMESTAMP",
        "text": "VARCHAR",
        "character varying": "VARCHAR",
        "double precision": "DOUBLE",
        "real": "FLOAT",
        "bigint": "BIGINT",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "numeric": "DECIMAL(38,12)",
    }.get(kind) or fail_type(kind)


def fail_type(kind):
    raise ValueError(f"An export column needs an explicit type mapping: {kind}")


def decimal_fits(value: Decimal, precision: int, scale: int) -> bool:
    """Check representability without Decimal context rounding, including trailing zeroes."""
    if not value.is_finite():
        return False
    _, digits, exponent = value.as_tuple()
    coefficient = int("".join(map(str, digits)))
    if coefficient == 0:
        return True
    while coefficient % 10 == 0:
        coefficient //= 10
        exponent += 1
    return exponent >= -scale and len(str(coefficient)) + exponent <= precision - scale


def sequence_columns():
    from quant_assistant.models.repositories.database import Database

    template = Database(Path(":memory:"))
    try:
        with template.connection() as connection:
            columns = connection.execute(
                "SELECT table_name,column_name,column_default FROM duckdb_columns() "
                "WHERE schema_name='main' AND column_default LIKE 'nextval(%'"
            ).fetchall()
        mappings = {}
        for table, column, default in columns:
            match = re.fullmatch(r"nextval\('([a-z_][a-z0-9_]*)'\)", default)
            if not match:
                raise ValueError("An export sequence needs an explicit mapping")
            mappings.setdefault(match[1], []).append((table, column))
        return mappings
    finally:
        template.close()


def prepare_sequences(target: Path, original: Path, mappings, cursor, schema, user):
    """Seed sequences before schema initialization; explicit-ID inserts do not advance them."""
    import duckdb

    retained = duckdb.connect(str(original), read_only=True) if original.is_file() else None
    try:
        tables = (
            {row[0] for row in retained.execute("SHOW TABLES").fetchall()} if retained else set()
        )
        with duckdb.connect(str(target)) as connection:
            for sequence, columns in mappings.items():
                ceiling = 0
                for table, column in columns:
                    if table in ANALYTICAL_TABLES and table in tables:
                        maximum = retained.execute(
                            f'SELECT max("{column}") FROM "{table}"'
                        ).fetchone()[0]
                    elif table not in NATIVE_ONLY and {column, "user_id"} <= {
                        name for name, _ in schema.get(table, [])
                    }:
                        cursor.execute(
                            sql.SQL("SELECT max({}) FROM {} WHERE user_id=%s").format(
                                sql.Identifier(column), sql.Identifier(table)
                            ),
                            (user,),
                        )
                        maximum = cursor.fetchone()[0]
                    else:
                        maximum = None
                    ceiling = max(ceiling, maximum or 0)
                if ceiling >= 2**63 - 1:
                    raise ValueError("An export sequence has exhausted its integer range")
                connection.execute(f'CREATE SEQUENCE "{sequence}" START {ceiling + 1}')
    finally:
        if retained:
            retained.close()


def export(source: Path, destination: Path, dsn: str):
    from quant_assistant.models.repositories.auth import AuthRepository
    from quant_assistant.models.repositories.database import Database

    source, destination = source.resolve(strict=True), destination.resolve()
    if (
        source == destination
        or source.is_relative_to(destination)
        or destination.is_relative_to(source)
        or destination.exists()
    ):
        raise ValueError("Rollback export requires a new independent directory")
    with (
        stopped_site(source),
        closing(psycopg2.connect(dsn)) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute(
            "SELECT count(*) FROM compute_jobs WHERE "
            "status IN('queued','running','cancel_requested')"
        )
        if cursor.fetchone()[0]:
            raise ValueError("Recover or cancel all native jobs before exporting")
        before = inventory(source)
        stage = destination.with_name(destination.name + ".rollback-staging")
        if stage.exists():
            raise ValueError("Rollback staging evidence exists; select a new directory")
        stage.mkdir(parents=True)
        if os.name != "nt":
            stage.chmod(0o700)
        for relative in before:
            # Rebuild authoritative stores below instead of retaining stale copies.
            if relative.startswith("auth/") or Path(relative).name.startswith(
                "quant_assistant.duckdb"
            ):
                continue
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
        registry = AuthRepository(stage)
        counts = {}
        with registry.connection(write=True) as auth:
            auth.execute("DELETE FROM site_settings")
            for table in AUTH_TABLES:
                cursor.execute(sql.SQL("SELECT * FROM {} ORDER BY 1").format(sql.Identifier(table)))
                columns = [column.name for column in cursor.description]
                accepted = {row[1] for row in auth.execute(f'PRAGMA table_info("{table}")')}
                # Derived native cache versions have no legacy business meaning.
                # Keep the fail-closed check for every other unmapped column.
                derived = {"data_revision"} if table == "users" else set()
                if set(columns) - accepted - derived:
                    raise ValueError(f"An authentication export column needs a mapping: {table}")
                kept = [index for index, column in enumerate(columns) if column not in derived]
                records = [tuple(row[index] for index in kept) for row in cursor.fetchall()]
                columns = [columns[index] for index in kept]
                statement = (
                    f'INSERT INTO "{table}" ('
                    + ",".join('"' + c + '"' for c in columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")"
                )
                auth.executemany(
                    statement,
                    [
                        [
                            value.timestamp()
                            if isinstance(value, datetime)
                            else value.isoformat()
                            if hasattr(value, "isoformat")
                            else value
                            for value in row
                        ]
                        for row in records
                    ],
                )
                counts[table] = len(records)
        cursor.execute("SELECT id,role FROM users ORDER BY id")
        users = cursor.fetchall()
        cursor.execute(
            "SELECT table_name,column_name,data_type FROM "
            "information_schema.columns WHERE "
            "table_schema='public' ORDER BY "
            "table_name,ordinal_position"
        )
        schema = {}
        for table, column, kind in cursor.fetchall():
            schema.setdefault(table, []).append((column, kind))
        sequences = sequence_columns()
        for user, role in users:
            prefix = Path() if role == "admin" else Path("users") / user
            directory = stage / prefix
            directory.mkdir(parents=True, exist_ok=True)
            database = Database(directory / "quant_assistant.duckdb")
            original = source / prefix / "quant_assistant.duckdb"
            prepare_sequences(database.path, original, sequences, cursor, schema, user)
            database.initialize()
            with database.connection() as legacy:
                if original.is_file():
                    # ATTACH does not accept bound parameters in DuckDB.
                    escaped = str(original).replace("'", "''")
                    legacy.execute(f"ATTACH '{escaped}' AS retained (READ_ONLY)")
                    retained = {
                        row[0] for row in legacy.execute("SHOW TABLES FROM retained").fetchall()
                    }
                    for table in sorted(ANALYTICAL_TABLES & retained):
                        legacy.execute(f'DELETE FROM "{table}"')
                        legacy.execute(
                            f'INSERT INTO "{table}" BY NAME SELECT * FROM retained."{table}"'
                        )
                    legacy.execute("DETACH retained")
                for table, columns in schema.items():
                    if table in NATIVE_ONLY or not any(
                        column == "user_id" for column, _ in columns
                    ):
                        continue
                    selected = [(column, kind) for column, kind in columns if column != "user_id"]
                    existing = {row[0] for row in legacy.execute("SHOW TABLES").fetchall()}
                    if table not in existing:
                        definitions = ",".join(
                            '"' + column + '" ' + duck_type(kind) for column, kind in selected
                        )
                        legacy.execute(f'CREATE TABLE "{table}" ({definitions})')
                    types = {
                        row[0]: row[1] for row in legacy.execute(f'DESCRIBE "{table}"').fetchall()
                    }
                    if set(column for column, _ in selected) - set(types):
                        raise ValueError(f"A business export column needs a mapping: {table}")
                    legacy.execute(f'DELETE FROM "{table}"')
                    cursor.execute(
                        sql.SQL("SELECT {} FROM {} WHERE user_id=%s").format(
                            sql.SQL(",").join(sql.Identifier(column) for column, _ in selected),
                            sql.Identifier(table),
                        ),
                        (user,),
                    )
                    total = 0
                    statement = (
                        f'INSERT INTO "{table}" ('
                        + ",".join('"' + column + '"' for column, _ in selected)
                        + ") VALUES ("
                        + ",".join("?" for _ in selected)
                        + ")"
                    )
                    while rows := cursor.fetchmany(500):
                        converted = []
                        for row in rows:
                            values = [
                                legacy_value(value, types[column])
                                for (column, _), value in zip(selected, row, strict=True)
                            ]
                            if table == "data_snapshots":
                                index = [column for column, _ in selected].index("path")
                                relative = evidence_relative(values[index], user, role, before)
                                if relative:
                                    values[index] = str(destination / relative)
                            for (column, _), value in zip(selected, values, strict=True):
                                if isinstance(value, Decimal) and types[column].startswith(
                                    "DECIMAL"
                                ):
                                    precision, scale = map(int, types[column][8:-1].split(","))
                                    if not decimal_fits(value, precision, scale):
                                        raise ValueError(
                                            "Rollback export would round a financial value"
                                        )
                            converted.append(values)
                        legacy.executemany(statement, converted)
                        total += len(rows)
                    counts[user + "/" + table] = total
            database.close()
            cursor.execute(
                "SELECT document_key,payload FROM user_documents WHERE user_id=%s",
                (user,),
            )
            for key, payload in cursor.fetchall():
                filename = {
                    "preferences": "preferences.json",
                    "strategy-settings": "strategy-settings.json",
                    "model-provider": "advisor-provider.json",
                    "model-jev": "advisor-jev.json",
                }.get(key)
                if filename:
                    payload = {
                        name: value for name, value in payload.items() if name != "key_reference"
                    }
                    if key == "preferences":
                        payload["version"] = 1
                    (directory / filename).write_text(
                        json.dumps(payload, ensure_ascii=False, allow_nan=False),
                        encoding="utf-8",
                    )
        cursor.execute("SELECT key,value FROM site_settings WHERE key LIKE 'calendar:%%'")
        for key, value in cursor.fetchall():
            calendar = stage / "calendars" / (key.split(":")[1] + ".json")
            calendar.parent.mkdir(exist_ok=True)
            calendar.write_text(value, encoding="utf-8")
        if inventory(source) != before:
            raise ValueError("Source files changed during rollback export")
        report = {
            "version": 1,
            "counts": counts,
            "sessions_revoked": True,
            "model_secrets_exported": False,
        }
        (stage / "rollback-manifest.json").write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )
        stage.rename(destination)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--dsn-env", default="QUANT_PG_DSN")
    args = parser.parse_args()
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        parser.error("Configure the selected PostgreSQL DSN environment variable")
    result = export(args.site_root, args.destination, dsn)
    print(
        json.dumps(
            {
                "exported": True,
                "tables": len(result["counts"]),
                "rows": sum(result["counts"].values()),
            }
        )
    )


if __name__ == "__main__":
    main()
