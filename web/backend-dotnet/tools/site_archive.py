"""Native PostgreSQL and site-file backup/restore, on Windows or Linux.

Set standard PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD or PGSERVICE variables.
Credentials are inherited by PostgreSQL tools and never included in arguments.
Stop the API and workers before restoring. Restore requires a new empty database
and a new file directory. No existing database or user directory is deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import psycopg2
from psycopg2 import sql
from site_files import private_file, relocate_evidence
from site_lock import stopped_site

EXCLUDED = {"server-secrets", "research-secrets", "secrets", ".env"}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files(root):
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Site files must not contain symbolic links")
        relative = path.relative_to(root)
        if private_file(relative):
            continue
        if path.is_file() and path.name not in {".site.lock", ".site-lock"}:
            found[relative.as_posix()] = sha256(path)
    return found


def tool(name):
    directory = os.environ.get("QUANT_PG_BIN")
    path = (
        Path(directory) / (name + (".exe" if os.name == "nt" else ""))
        if directory
        else shutil.which(name)
    )
    if not path or not Path(path).is_file():
        raise ValueError(f"Install PostgreSQL client tools or set QUANT_PG_BIN ({name})")
    return str(path)


def run_tool(name, arguments):
    result = subprocess.run([tool(name), *map(str, arguments)], capture_output=True, check=False)
    if result.returncode:
        # Provider/database diagnostics can contain private connection details.
        raise RuntimeError(f"{name} failed (exit {result.returncode}); archive was not published")


def backup(root: Path, output: Path, retain: int):
    root = root.resolve(strict=True)
    with stopped_site(root):
        return _backup(root, output, retain)


def _backup(root: Path, output: Path, retain: int):
    root = root.resolve(strict=True)
    output = output.resolve()
    if not root.is_dir() or output == root or output.is_relative_to(root):
        raise ValueError("Archive directory must be outside the site directory")
    if not 1 <= retain <= 365:
        raise ValueError("Retention must be between 1 and 365")
    output.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        output.chmod(0o700)
    inventory = files(root)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = output / f"quant-site-{stamp}.zip"
    with tempfile.TemporaryDirectory(prefix=".quant-backup-", dir=output) as temporary:
        temporary = Path(temporary)
        dump = temporary / "database.dump"
        run_tool(
            "pg_dump",
            [
                "--no-password",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                dump,
            ],
        )
        run_tool("pg_restore", ["--list", dump])
        manifest = {
            "version": 1,
            "database_sha256": sha256(dump),
            "files": inventory,
            "created_at": stamp,
            "secrets_included": False,
        }
        staged = temporary / "archive.zip"
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(dump, "database.dump")
            for relative in inventory:
                archive.write(root / relative, "site/" + relative)
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        if files(root) != inventory:
            raise ValueError("Site files changed during backup; stop writers and retry")
        verify(staged)
        if os.name != "nt":
            staged.chmod(0o600)
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        staged.replace(target)
    # Only remove older archives after the new one is complete and verified.
    for old in sorted(output.glob("quant-site-*.zip"), reverse=True)[retain:]:
        if old.is_file() and not old.is_symlink() and old.resolve().parent == output:
            old.unlink()
    return target


def verify(path: Path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        unique = {name.casefold() if os.name == "nt" else name for name in names}
        if len(names) != len(unique) or archive.testzip() is not None:
            raise ValueError("Archive contains duplicate or corrupt entries")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("version") != 1 or manifest.get("secrets_included") is not False:
            raise ValueError("Unsupported archive format")
        expected = {
            "manifest.json",
            "database.dump",
            *("site/" + item for item in manifest["files"]),
        }
        if set(names) != expected:
            raise ValueError("Archive file inventory differs from manifest")
        for name in names:
            parts = PurePosixPath(name)
            if parts.is_absolute() or ".." in parts.parts or "\\" in name or ":" in name:
                raise ValueError("Unsafe archive path")
            if name.startswith("site/") and private_file(Path(name[5:])):
                raise ValueError("Ordinary archives must not include private credentials")
            entry = archive.getinfo(name)
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Symbolic archive entry")
        for name, expected_hash in [
            ("database.dump", manifest["database_sha256"]),
            *(("site/" + k, v) for k, v in manifest["files"].items()),
        ]:
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
                    raise ValueError("Archive checksum mismatch")
        return manifest


def restore(path: Path, destination: Path):
    path = path.resolve(strict=True)
    manifest = verify(path)
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Restore requires a new site directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Serialize against import and another restore; never drop existing tables.
    with closing(psycopg2.connect("")) as connection, connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtextextended('site:migration',0))")
        cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        if cursor.fetchone()[0]:
            raise ValueError("Restore requires a new empty PostgreSQL database")
        staged = Path(tempfile.mkdtemp(prefix=".quant-restore-", dir=destination.parent))
        try:
            with zipfile.ZipFile(path) as archive:
                archive.extractall(staged)  # verified inventory and path validation above
            (staged / "site").mkdir(exist_ok=True)
            if files(staged / "site") != manifest["files"]:
                raise ValueError("Restored file checksum mismatch")
            database = os.environ.get("PGDATABASE")
            if not database:
                raise ValueError("Set PGDATABASE explicitly for restore")
            script = staged / "restore.sql"
            run_tool(
                "pg_restore",
                ["--no-owner", "--no-acl", "--file", script, staged / "database.dump"],
            )
            marker = cursor.mogrify(
                "INSERT INTO "
                "site_restore_runs(archive_sha256,destination,staging,status) "
                "VALUES(%s,%s,%s,'database_restored');",
                (sha256(path), str(destination), str(staged)),
            )
            with script.open("ab") as stream:
                # pg_restore clears search_path in its generated SQL.
                stream.write(b"\nSET LOCAL search_path TO public;\n" + marker + b"\n")
                stream.write(restore_fingerprint_sql(sha256(path)).encode())
            # The dump, recovery marker and its fingerprint commit together.
            # A SQL failure leaves an empty destination database; an interrupted
            # publication always has enough evidence for complete-restore.
            run_tool(
                "psql",
                [
                    "--no-password",
                    "--set",
                    "ON_ERROR_STOP=1",
                    "--single-transaction",
                    "--dbname",
                    database,
                    "--file",
                    script,
                ],
            )
            script.unlink()
            prepare_restore(cursor, connection, path, destination, manifest)
            publish_restore(cursor, connection, path, destination, staged, manifest)
        except BaseException:
            # Keep only this tool-created staging path for recovery; do not delete
            # or overwrite any selected user directory.
            raise
    return destination


def database_fingerprint(cursor):
    cursor.execute(
        "SELECT table_name FROM "
        "information_schema.tables WHERE "
        "table_schema='public' AND table_type='BASE "
        "TABLE' AND table_name<>'site_restore_runs' "
        "ORDER BY table_name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    hashes = []
    for table in tables:
        cursor.execute(
            sql.SQL(
                "SELECT count(*),md5(coalesce(string_agg(hash,'' ORDER BY hash),'')) "
                "FROM (SELECT md5(row_to_json(t)::text) AS hash FROM {} t) rows"
            ).format(sql.Identifier(table))
        )
        count, value = cursor.fetchone()
        hashes.append((table, count, value))
    cursor.execute(
        "SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex')",
        (json.dumps(hashes),),
    )
    return cursor.fetchone()[0]


def restore_fingerprint_sql(archive_sha256):
    if not re.fullmatch(r"[a-f0-9]{64}", archive_sha256):
        raise ValueError("Invalid restore archive identity")
    return (
        """
DO $restore_fingerprint$
DECLARE name text; size bigint; checksum text; pairs jsonb := '[]'::jsonb;
BEGIN
  FOR name IN SELECT table_name FROM information_schema.tables
    WHERE table_schema='public' AND table_type='BASE TABLE'
      AND table_name<>'site_restore_runs' ORDER BY table_name
  LOOP
    EXECUTE format($table_hash$SELECT count(*),md5(coalesce(string_agg(hash,'' ORDER BY hash),''))
      FROM (SELECT md5(row_to_json(t)::text) AS hash FROM %I t) rows$table_hash$,name)
      INTO size,checksum;
    pairs := pairs || jsonb_build_array(jsonb_build_array(name,size,checksum));
  END LOOP;
  UPDATE site_restore_runs
    SET database_fingerprint=encode(sha256(convert_to(pairs::text,'UTF8')),'hex')
    WHERE archive_sha256='"""
        + archive_sha256
        + """';
END
$restore_fingerprint$;
"""
    )


def prepare_restore(cursor, connection, path, destination, manifest):
    relocate_evidence(cursor, destination, manifest["files"])
    # Revocation also prevents sessions from an older archive becoming
    # valid again after restoring a database into a new environment.
    cursor.execute("DELETE FROM sessions")
    cursor.execute("DELETE FROM recovery_tokens")
    cursor.execute(
        "UPDATE compute_jobs SET "
        "status='interrupted',error_code='restore_interrupted',completed_at=now() "
        "WHERE status IN('queued','running','cancel_requested')"
    )
    cursor.execute(
        "UPDATE market_advisor_reports SET "
        "status='interrupted',error_code='restore_interrupted' WHERE status IN('queued','running')"
    )
    cursor.execute(
        "UPDATE agent_research_jobs SET "
        "status='interrupted',phase='interrupted',error_code='restore_interrupted' "
        "WHERE status IN('queued','running','cancel_requested')"
    )
    cursor.execute(
        "UPDATE agent_research_attempts SET "
        "execution_status='interrupted',completed_at=now() "
        "WHERE execution_status IN('queued','running')"
    )
    cursor.execute("UPDATE model_call_costs SET status='interrupted' WHERE status='reserved'")
    cursor.execute("DELETE FROM worker_heartbeats")
    cursor.execute("DELETE FROM review_sessions WHERE result IS NULL")
    cursor.execute("UPDATE ledger_replay_previews SET expires_at=now() WHERE consumed_at IS NULL")
    cursor.execute(
        "UPDATE site_restore_runs SET "
        "status='prepared',database_fingerprint=%s "
        "WHERE archive_sha256=%s",
        (database_fingerprint(cursor), sha256(path)),
    )
    connection.commit()


def publish_restore(cursor, connection, path, destination, staged, manifest):
    if files(staged / "site") != manifest["files"]:
        raise ValueError("Staged restore files changed before publication")
    if destination.exists():
        raise ValueError("Restore publication requires a new destination")
    (staged / "site").rename(destination)
    (staged / "database.dump").unlink()
    (staged / "manifest.json").unlink()
    staged.rmdir()
    proof = {
        "version": 1,
        "restored": True,
        "archive_sha256": sha256(path),
        "restored_at": datetime.now(UTC).isoformat(),
        "files": len(manifest["files"]),
    }
    proof_file = path.with_name(path.name + ".restore.json")
    staged_proof = path.with_name(path.name + ".restore.tmp")
    staged_proof.write_text(json.dumps(proof, sort_keys=True), encoding="utf-8")
    staged_proof.replace(proof_file)
    cursor.execute(
        "UPDATE site_restore_runs SET status='published' WHERE archive_sha256=%s",
        (sha256(path),),
    )
    connection.commit()


def complete_restore(path: Path, destination: Path):
    path, destination = path.resolve(strict=True), destination.resolve()
    manifest, fingerprint = verify(path), sha256(path)
    with closing(psycopg2.connect("")) as connection, connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtextextended('site:migration',0))")
        cursor.execute(
            "SELECT destination,staging,status,database_fingerprint "
            "FROM site_restore_runs WHERE archive_sha256=%s",
            (fingerprint,),
        )
        row = cursor.fetchone()
        if not row or row[0] != str(destination):
            raise ValueError("No matching interrupted restore exists in this database")
        staged = Path(row[1])
        if (
            staged.parent != destination.parent
            or not staged.name.startswith(".quant-restore-")
            or staged.is_symlink()
        ):
            raise ValueError("Invalid restore staging path")
        if destination.exists():
            # A successful rename followed by a failed proof write can be recovered.
            with stopped_site(destination):
                if (
                    files(destination) != manifest["files"]
                    or database_fingerprint(cursor) != row[3]
                ):
                    raise ValueError("Published files or database changed since restore")
                proof = {
                    "version": 1,
                    "restored": True,
                    "archive_sha256": fingerprint,
                    "restored_at": datetime.now(UTC).isoformat(),
                    "files": len(manifest["files"]),
                }
                path.with_name(path.name + ".restore.json").write_text(
                    json.dumps(proof, sort_keys=True), encoding="utf-8"
                )
                cursor.execute(
                    "UPDATE site_restore_runs SET status='published' WHERE archive_sha256=%s",
                    (fingerprint,),
                )
                connection.commit()
        else:
            if not staged.is_dir() or files(staged / "site") != manifest["files"]:
                raise ValueError("Verified staged files are missing or changed")
            if row[2] == "database_restored":
                if database_fingerprint(cursor) != row[3]:
                    raise ValueError("Database changed before restore preparation")
                prepare_restore(cursor, connection, path, destination, manifest)
            elif row[2] != "prepared" or database_fingerprint(cursor) != row[3]:
                raise ValueError("Database changed after restore; publication was refused")
            publish_restore(cursor, connection, path, destination, staged, manifest)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("backup")
    save.add_argument("--site-root", type=Path, required=True)
    save.add_argument("--output", type=Path, required=True)
    save.add_argument("--retain", type=int, default=14)
    check = commands.add_parser("verify")
    check.add_argument("archive", type=Path)
    load = commands.add_parser("restore")
    load.add_argument("archive", type=Path)
    load.add_argument("--destination", type=Path, required=True)
    resume = commands.add_parser("complete-restore")
    resume.add_argument("archive", type=Path)
    resume.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        print(backup(args.site_root, args.output, args.retain))
    elif args.command == "restore":
        print(restore(args.archive, args.destination))
    elif args.command == "complete-restore":
        print(complete_restore(args.archive, args.destination))
    else:
        result = verify(args.archive)
        print(
            json.dumps(
                {
                    "verified": True,
                    "files": len(result["files"]),
                    "version": result["version"],
                }
            )
        )


if __name__ == "__main__":
    main()
