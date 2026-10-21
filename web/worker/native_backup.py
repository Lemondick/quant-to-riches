"""Daily stopped-site backups controlled by the native service supervisor.

Ordinary archives exclude private keys. Configure libpq PG* variables separately
from the API connection string. State files contain only bounded diagnostic codes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import psycopg2

from quant_assistant.integrations.tradingagents_worker.process import ProcessTree

SHANGHAI = timezone(timedelta(hours=8))

_logger = logging.getLogger(__name__)


def configured(release, environment):
    return (
        NativeBackup(release, environment) if environment.get("QUANT_NATIVE_BACKUP_TIME") else None
    )


class NativeBackup:
    def __init__(self, release, environment):
        self.release = Path(release).resolve()
        self.environment = dict(environment)
        value = environment.get("QUANT_NATIVE_BACKUP_TIME", "")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("QUANT_NATIVE_BACKUP_TIME must be HH:MM in Asia/Shanghai")
        self.hour, self.minute = map(int, value.split(":"))
        if (
            not environment.get("QUANT_SITE_ROOT")
            or not environment.get("QUANT_BACKUP_DIR")
            or not environment.get("PGDATABASE")
        ):
            raise ValueError(
                "Daily backups require site/output directories and explicit PGDATABASE"
            )
        self.site = Path(environment["QUANT_SITE_ROOT"]).resolve()
        self.output = Path(environment["QUANT_BACKUP_DIR"]).resolve()
        if (
            self.output == self.site
            or self.output.is_relative_to(self.site)
            or self.output == self.release
            or self.output.is_relative_to(self.release)
        ):
            raise ValueError("Backup output must be outside the site and immutable release")
        self.retain = int(environment.get("QUANT_BACKUP_RETAIN", "14"))
        self.timeout = int(environment.get("QUANT_BACKUP_TIMEOUT_SECONDS", "1800"))
        if not 1 <= self.retain <= 365 or not 30 <= self.timeout <= 7200:
            raise ValueError("Invalid backup retention or execution timeout")
        self.output.mkdir(parents=True, exist_ok=True)
        self.state_path = self.output / ".native-backup-status.json"
        self.last_check = None
        try:
            previous = self.read_state()
            if previous.get("status") == "running":
                self.persist_state(
                    {
                        **previous,
                        "status": "failed",
                        "error_code": "native_backup_interrupted",
                    }
                )
        except (OSError, ValueError):
            print("Native backup status unavailable; services can still start", flush=True)

    def read_state(self):
        if not self.state_path.exists():
            return {}
        if self.state_path.is_symlink() or self.state_path.stat().st_size > 16384:
            raise ValueError("Invalid backup status file")
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("Invalid backup status file")
        return state

    def write_state(self, state):
        self.output.mkdir(parents=True, exist_ok=True)
        target = self.state_path.with_suffix(".tmp")
        if target.is_symlink() or self.state_path.is_symlink():
            raise ValueError("Invalid backup status path")
        with target.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            target.chmod(0o600)
        target.replace(self.state_path)

    def due(self, now):
        if self.last_check is not None and (now - self.last_check).total_seconds() < 60:
            return False
        local = now.astimezone(SHANGHAI)
        if (local.hour, local.minute) < (self.hour, self.minute):
            return False
        try:
            state = self.read_state()
            if state.get("attempted_at"):
                previous = datetime.fromisoformat(state["attempted_at"]).astimezone(SHANGHAI)
                if previous.date() == local.date() and state.get("status") != "deferred":
                    return False
        except (OSError, ValueError, TypeError):
            self.last_check = now
            print("Native backup status unavailable; backup skipped", flush=True)
            return False
        return True

    def persist_state(self, state):
        try:
            self.write_state(state)
            return True
        except (OSError, ValueError):
            print("Native backup status could not be saved", flush=True)
            return False

    def idle(self):
        options = {
            key: self.environment[source]
            for key, source in [
                ("host", "PGHOST"),
                ("port", "PGPORT"),
                ("dbname", "PGDATABASE"),
                ("user", "PGUSER"),
                ("password", "PGPASSWORD"),
                ("passfile", "PGPASSFILE"),
                ("sslmode", "PGSSLMODE"),
            ]
            if self.environment.get(source)
        }
        try:
            with (
                psycopg2.connect(**options, connect_timeout=5) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "SELECT (SELECT count(*) FROM compute_jobs "
                    "WHERE status IN('queued','running','cancel_requested'))+"
                    "(SELECT count(*) FROM model_call_costs WHERE status='reserved')"
                )
                return cursor.fetchone()[0] == 0
        except Exception as error:
            # E23: never swallow the probe failure silently. A False return
            # defers the backup as 'active_tasks_or_database_unavailable';
            # without this log a database outage is indistinguishable from a
            # busy site during long deferrals.
            _logger.warning(
                "backup idleness probe failed",
                extra={
                    "probe": "native_backup.idle",
                    "error_type": type(error).__name__,
                    "error": str(error)[:300],
                },
            )
            return False

    def defer(self, now):
        self.last_check = now
        previous = self.read_state()
        self.persist_state(
            {
                **previous,
                "status": "deferred",
                "attempted_at": now.isoformat(),
                "error_code": "active_tasks_or_database_unavailable",
            }
        )

    def create_archive(self):
        options = (
            {"creationflags": subprocess.CREATE_NO_WINDOW | 0x4}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        process = subprocess.Popen(
            [
                sys.executable,
                str(self.release / "tools/site_archive.py"),
                "backup",
                "--site-root",
                str(self.site),
                "--output",
                str(self.output),
                "--retain",
                str(self.retain),
            ],
            env={**self.environment, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            **options,
        )
        try:
            tree = ProcessTree(process)
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            raise
        try:
            tree.resume()
            output, _ = process.communicate(timeout=self.timeout)
        finally:
            # Includes pg_dump/pg_restore descendants even on timeout.
            tree.close()
        if process.returncode:
            raise RuntimeError("native_backup_failed")
        # The tool publishes only a fully verified archive and prints its path.
        archive = Path(output.strip()).resolve()
        if (
            archive.parent != self.output
            or not archive.name.startswith("quant-site-")
            or archive.suffix != ".zip"
            or not archive.is_file()
        ):
            raise RuntimeError("native_backup_result_invalid")
        return archive

    def execute(self, stop_services, start_services, now=None):
        now = now or datetime.now(UTC)
        try:
            previous = self.read_state()
        except (OSError, ValueError):
            self.last_check = now
            print("Native backup status unavailable; backup skipped", flush=True)
            return False
        state = {
            **previous,
            "status": "running",
            "attempted_at": now.isoformat(),
            "error_code": None,
        }
        if not self.persist_state(state):
            self.last_check = now
            return False
        stopped = False
        success = False
        try:
            stopped = True
            stop_services()
            archive = self.create_archive()
            state.update(
                status="succeeded",
                archive=archive.name,
                last_success_at=now.isoformat(),
            )
            success = True
        except Exception:
            state.update(status="failed", error_code="native_backup_failed")
        finally:
            if stopped:
                try:
                    start_services()
                except Exception as error:
                    state.update(
                        status="restart_failed",
                        error_code="native_services_restart_failed",
                    )
                    self.persist_state(state)
                    raise RuntimeError(
                        "Native services could not restart after maintenance"
                    ) from error
            if not self.persist_state(state):
                success = False
        print(
            "Native backup succeeded" if success else "Native backup failed; services restarted",
            flush=True,
        )
        return success
