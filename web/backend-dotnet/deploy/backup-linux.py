"""Stopped-site backup for the native Ubuntu systemd deployment.

Run as root with the release's Python interpreter. Configuration uses the flat
EnvironmentFile generated for this server, never shell evaluation. Private
recovery material stays in a separate root-only archive on the same server.
"""

import argparse
import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg2


def command(arguments, *, env=None):
    result = subprocess.run(arguments, env=env, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name} failed (exit {result.returncode})")
    return result.stdout.strip()


def backup(force=False):
    if os.geteuid() != 0:
        raise RuntimeError("Run the native backup service as root")
    config = Path("/etc/quant/environment")
    info = config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o027:
        raise RuntimeError("Backup configuration must be root-owned and private")
    values = {}
    for line in config.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if value.startswith(("'", '"')):
                raise RuntimeError("This wrapper requires unquoted flat EnvironmentFile values")
            values[key] = value
    output = Path(values["QUANT_BACKUP_DIR"])
    archives = sorted(output.glob("quant-site-*.zip"))
    if not force and archives and time.time() - archives[-1].stat().st_mtime < 8 * 3600:
        print("recent_native_backup_present")
        return
    env = dict(os.environ)
    env.update(values)
    # libpq configuration stays in the child environment and protected pgpass.
    for key in ["PGPASSWORD", "PGSERVICE", "PGSERVICEFILE"]:
        env.pop(key, None)
    previous = {key: os.environ.get(key) for key in values if key.startswith("PG")}
    try:
        for key in previous:
            os.environ[key] = values[key]
        with psycopg2.connect("") as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT count(*) FROM compute_jobs WHERE status IN "
                "('queued','running','cancel_requested')) + "
                "(SELECT count(*) FROM model_call_costs WHERE status='reserved')"
            )
            if cursor.fetchone()[0]:
                raise RuntimeError("Active jobs or reserved model calls; retry at the next timer window")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if command(["systemctl", "is-active", "quant-api.service"]) != "active":
        raise RuntimeError("API must already be running before scheduled maintenance")
    release = Path("/opt/quant/current").resolve(strict=True)
    restarting = False
    try:
        # Mark before stopping so a partial stop failure also attempts recovery.
        restarting = True
        command(["systemctl", "stop", "quant-api.service"])
        archive = command(
            [
                "runuser", "-u", "quant", "--", str(release / "backend/.venv/bin/python"),
                str(release / "tools/site_archive.py"), "backup", "--site-root",
                values["QUANT_SITE_ROOT"], "--output", str(output), "--retain",
                values.get("QUANT_BACKUP_RETAIN", "14"),
            ],
            env=env,
        )
        private_output = Path("/var/lib/quant/private-backups")
        private_output.mkdir(mode=0o700, exist_ok=True)
        if private_output.is_symlink() or private_output.stat().st_uid != 0:
            raise RuntimeError("Private backup directory must be owned by root without symlinks")
        private_output.chmod(0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        target = private_output / f"quant-private-{stamp}.tar.gz"
        staged = target.with_suffix(".tmp")
        with tarfile.open(staged, "w:gz") as tar:
            for path, name in [
                (config, "configuration/environment"),
                (Path("/etc/quant/tls"), "tls"),
                (Path(values["QUANT_SECRET_ROOT"]), "private"),
            ]:
                if path.is_symlink() or (path.is_dir() and any(p.is_symlink() for p in path.rglob("*"))):
                    raise RuntimeError("Private recovery material must not contain symlinks")
                tar.add(path, arcname=name)
        staged.chmod(0o600)
        with tarfile.open(staged, "r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    stream = tar.extractfile(member)
                    assert stream is not None
                    while stream.read(1024 * 1024):
                        pass
        staged.replace(target)
        for old in sorted(private_output.glob("quant-private-*.tar.gz"))[:-14]:
            if old.is_file() and not old.is_symlink():
                old.unlink()
        print(json.dumps({"archive": Path(archive).name, "private_archive": target.name}))
    finally:
        if restarting:
            command(["systemctl", "start", "quant-api.service"])
    command(["curl", "--fail", "--silent", "--show-error", "--retry", "20",
             "--retry-delay", "2", "--retry-connrefused", "--max-time", "5",
             "http://127.0.0.1:8765/readyz"])
    print("native_backup_and_service_recovery_verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Ignore the recent-backup window")
    args = parser.parse_args()
    def interrupted(_signal, _frame):
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    os.umask(0o077)
    descriptor = os.open("/run/lock/guanlan-backup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup(args.force)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, psycopg2.Error, tarfile.TarError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
