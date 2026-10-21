"""Monthly automated restore drill for the native Ubuntu deployment.

Run as root from quant-restore-drill.service. Restores the newest
``quant-site-*.zip`` archive into a throwaway database + directory, verifies
the manifest fingerprints (via site_archive.py itself), sanity-checks a few
core tables, and leaves the ``<archive>.restore.json`` proof next to the
archive so the existing NativeBackupEvidence mechanism shows
``restore_verified=true`` on the admin backup page.

The production database and site directory are never touched.

Configuration: /etc/quant/environment (flat unquoted KEY=value, root-owned,
same format as backup-linux.py). Recognized keys:

  QUANT_BACKUP_DIR              archive directory (required)
  QUANT_RESTORE_DRILL_ALERT_URL optional webhook (POST JSON) on drill failure
  QUANT_PG_BIN                  optional PostgreSQL client bin dir
  PG*                           libpq variables for the quant database user

Failure handling: writes ``<archive>.restore.json`` with ``restored:false``
and a redacted error, logs to the systemd journal (``logger -p user.err``),
optionally POSTs the webhook, and exits non-zero so the timer unit shows
failed.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

CONFIG_PATH = Path("/etc/quant/environment")
DEPLOY_DIR = Path(__file__).resolve().parent
SITE_ARCHIVE = DEPLOY_DIR / ".." / "tools" / "site_archive.py"
BUSINESS_TABLES = ("users", "instruments", "positions", "portfolio_accounts")


def fail(message):
    print(f"restore-drill: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_config():
    if os.geteuid() != 0:
        fail("run the restore drill as root")
    info = CONFIG_PATH.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o027:
        fail("configuration must be root-owned and private")
    values = {}
    for line in CONFIG_PATH.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if value.startswith(("'", '"')):
                fail("this script requires unquoted flat EnvironmentFile values")
            values[key] = value
    return values


def pg_bin(config):
    candidates = [
        config.get("QUANT_PG_BIN"),
        "/usr/lib/postgresql/18/bin",
    ]
    path = os.environ.get("PATH", "")
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            path = candidate + os.pathsep + path
    return path


def run(arguments, *, env=None, check=True, timeout=1500):
    result = subprocess.run(arguments, env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        tail = (result.stderr or result.stdout or "")[-2000:]
        raise RuntimeError(f"{Path(arguments[0]).name} failed (exit {result.returncode}): {tail}")
    return result


def superuser_psql(config, sql):
    return run(
        ["psql", "-h", "/var/run/postgresql", "-U", "postgres", "-d", "postgres",
         "-v", "ON_ERROR_STOP=1", "-c", sql],
        env={**os.environ, "PATH": pg_bin(config), "PGPASSWORD": ""},
    )


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_proof(archive, payload):
    staged = archive.with_name(archive.name + ".restore.tmp")
    staged.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.chmod(staged, 0o600)
    staged.replace(archive.with_name(archive.name + ".restore.json"))


def alert(config, archive, error):
    message = f"restore drill failed for {archive.name}: {error}"
    try:
        subprocess.run(["logger", "-p", "user.err", "-t", "quant-restore-drill", message], check=False)
    except OSError:
        pass
    url = config.get("QUANT_RESTORE_DRILL_ALERT_URL")
    if url:
        body = json.dumps({
            "event": "restore_drill_failed",
            "archive": archive.name,
            "error": error,
            "at": datetime.now(UTC).isoformat(),
        }).encode()
        try:
            request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=10).close()
        except Exception as exc:  # alert transport must not mask the drill result
            print(f"restore-drill: alert webhook failed: {type(exc).__name__}", file=sys.stderr)


def business_checks(config, dbname):
    import psycopg2

    env = dict(os.environ)
    env.update({k: v for k, v in config.items() if k.startswith("PG") or k == "QUANT_PG_BIN"})
    env["PGDATABASE"] = dbname
    connection = psycopg2.connect(host=env.get("PGHOST", "127.0.0.1"),
                                  port=env.get("PGPORT", "5432"),
                                  user=env.get("PGUSER", "quant"),
                                  dbname=dbname)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT status, database_fingerprint FROM site_restore_runs ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if not row or row[0] != "published":
                raise RuntimeError(f"site_restore_runs status is {row[0] if row else None!r}, expected 'published'")
            print(f"restore-drill: site_restore_runs status=published fingerprint={row[1][:16]}...")
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            existing = {r[0] for r in cursor.fetchall()}
            for table in BUSINESS_TABLES:
                if table in existing:
                    cursor.execute(f'SELECT count(*) FROM "{table}"')
                    print(f"restore-drill: table {table}: {cursor.fetchone()[0]} rows")
    finally:
        connection.close()


def cleanup(config, dbname, tmpbase):
    try:
        superuser_psql(config, f'DROP DATABASE IF EXISTS "{dbname}"')
    except Exception as exc:
        print(f"restore-drill: warning: could not drop drill database: {exc}", file=sys.stderr)
    shutil.rmtree(tmpbase, ignore_errors=True)


def main():
    config = read_config()
    for key in ("QUANT_BACKUP_DIR",):
        if not config.get(key):
            fail(f"{key} is not set in {CONFIG_PATH}")
    # placeholders filled by drill(); kept outside try so cleanup always runs
    dbname = None
    tmpbase = None
    archive = None
    try:
        archive, fingerprint, dbname, tmpbase = restore_into(config)
        business_checks(config, dbname)
    except SystemExit:
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        if archive is not None:
            try:
                write_proof(archive, {
                    "version": 1,
                    "restored": False,
                    "archive_sha256": sha256_file(archive),
                    "restored_at": datetime.now(UTC).isoformat(),
                    "error": error,
                })
            except Exception as proof_exc:
                print(f"restore-drill: could not write failure proof: {proof_exc}", file=sys.stderr)
            alert(config, archive, error)
        else:
            print(f"restore-drill: {error}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        if dbname and tmpbase:
            cleanup(config, dbname, tmpbase)
    print(f"restore-drill: OK for {archive.name}")


def restore_into(config):
    """Steps 1-4 of the drill; split out so main() can clean up on failure."""
    backup_dir = Path(config["QUANT_BACKUP_DIR"])
    archives = sorted(p for p in backup_dir.glob("quant-site-*.zip") if p.is_file() and not p.is_symlink())
    if not archives:
        fail(f"no archives in {backup_dir}; cannot run restore drill")
    archive = archives[-1]
    fingerprint = sha256_file(archive)
    print(f"restore-drill: archive={archive.name}")

    python = os.environ.get("QUANT_PYTHON", sys.executable)
    site_archive = str(SITE_ARCHIVE.resolve())

    verify_out = run([python, site_archive, "verify", str(archive)])
    verify = json.loads(verify_out.stdout)
    if not verify.get("verified"):
        fail(f"archive failed fingerprint verification: {archive.name}")
    print(f"restore-drill: manifest verified, files={verify.get('files')}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    dbname = f"quant_restore_drill_{stamp}"
    tmpbase = Path(tempfile.mkdtemp(prefix=".quant-drill-", dir=backup_dir.parent))
    destination = tmpbase / "site"
    os.chmod(tmpbase, 0o700)
    superuser_psql(config, f'CREATE DATABASE "{dbname}" OWNER "{config.get("PGUSER", "quant")}"')
    print(f"restore-drill: drill database={dbname} destination={destination}")

    child_env = dict(os.environ)
    child_env.update(config)
    child_env["PGDATABASE"] = dbname
    child_env["PATH"] = pg_bin(config)
    run([python, site_archive, "restore", str(archive), "--destination", str(destination)], env=child_env)

    proof_path = archive.with_name(archive.name + ".restore.json")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if not (proof.get("version") == 1 and proof.get("restored") is True
            and proof.get("archive_sha256") == fingerprint):
        fail("restore proof (.restore.json) missing or mismatched after restore")
    print("restore-drill: .restore.json proof written by restore tool")
    return archive, fingerprint, dbname, tmpbase


if __name__ == "__main__":
    main()
