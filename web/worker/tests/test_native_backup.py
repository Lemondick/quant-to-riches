"""Daily maintenance must stop only owned services and restore them on backup failure."""

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_backup
import native_host


class NativeBackupTests(unittest.TestCase):
    def environment(self, root):
        (root / "site").mkdir()
        (root / "release").mkdir()
        return dict(
            QUANT_SITE_ROOT=str(root / "site"),
            QUANT_BACKUP_DIR=str(root / "archives"),
            QUANT_NATIVE_BACKUP_TIME="02:00",
            PGDATABASE="synthetic",
            PGHOST="127.0.0.1",
            PGPORT="57449",
            PGUSER="review",
        )

    def test_daily_schedule_persists_success_and_does_not_repeat_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            before = datetime(2026, 10, 7, 17, 59, tzinfo=UTC)  # Shanghai 01:59 on the 8th
            due = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
            schedule = native_backup.NativeBackup(root / "release", env)
            self.assertFalse(schedule.due(before))
            self.assertTrue(schedule.due(due))
            calls = []
            archive = root / "archives" / "quant-site-synthetic.zip"
            with patch.object(
                schedule,
                "create_archive",
                side_effect=lambda: calls.append("archive") or archive,
            ):
                schedule.execute(lambda: calls.append("stop"), lambda: calls.append("start"), due)
            self.assertEqual(calls, ["stop", "archive", "start"])
            self.assertFalse(native_backup.NativeBackup(root / "release", env).due(due))
            state = json.loads((root / "archives" / ".native-backup-status.json").read_text())
            self.assertEqual(state["status"], "succeeded")
            self.assertEqual(state["archive"], archive.name)
            self.assertEqual(state["last_success_at"], due.isoformat())

    def test_failed_archive_always_restarts_services_preserves_last_success_and_redacts_errors(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schedule = native_backup.NativeBackup(root / "release", self.environment(root))
            due = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
            schedule.write_state(
                dict(status="succeeded", last_success_at="2026-10-07T00:00:00+00:00")
            )
            stopped, restarted = Mock(), Mock()
            with patch.object(
                schedule,
                "create_archive",
                side_effect=RuntimeError("synthetic-private-password"),
            ):
                self.assertFalse(schedule.execute(stopped, restarted, due))
            stopped.assert_called_once()
            restarted.assert_called_once()
            state = json.loads(schedule.state_path.read_text())
            self.assertEqual(state["last_success_at"], "2026-10-07T00:00:00+00:00")
            self.assertEqual(state["status"], "failed")
            self.assertNotIn("synthetic-private-password", schedule.state_path.read_text())
            self.assertFalse(schedule.due(due))

    def test_restart_failure_remains_visible_and_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schedule = native_backup.NativeBackup(root / "release", self.environment(root))
            with (
                patch.object(
                    schedule,
                    "create_archive",
                    return_value=root / "archives" / "quant-site.zip",
                ),
                self.assertRaises(RuntimeError),
            ):
                schedule.execute(
                    Mock(),
                    Mock(side_effect=RuntimeError("synthetic restart failure")),
                    datetime(2026, 10, 7, 18, 0, tzinfo=UTC),
                )
            self.assertEqual(
                json.loads(schedule.state_path.read_text())["status"], "restart_failed"
            )

    def test_config_rejects_site_release_and_invalid_time_before_any_service_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            for output, time in [
                (root / "site" / "backups", "02:00"),
                (root / "release" / "backups", "02:00"),
                (root / "archives", "25:00"),
            ]:
                with self.assertRaises(ValueError):
                    native_backup.NativeBackup(
                        root / "release",
                        dict(
                            env,
                            QUANT_BACKUP_DIR=str(output),
                            QUANT_NATIVE_BACKUP_TIME=time,
                        ),
                    )

    def test_busy_queue_defers_without_stopping_services_and_missing_database_is_not_idle(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schedule = native_backup.NativeBackup(root / "release", self.environment(root))
            with patch.object(
                native_backup.psycopg2,
                "connect",
                side_effect=Exception("private provider details"),
            ):
                self.assertFalse(schedule.idle())
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            cursor = Mock()
            cursor.__enter__ = Mock(return_value=cursor)
            cursor.__exit__ = Mock(return_value=False)
            cursor.fetchone.return_value = (1,)
            connection.cursor.return_value = cursor
            with patch.object(native_backup.psycopg2, "connect", return_value=connection):
                self.assertFalse(schedule.idle())
            cursor.fetchone.return_value = (0,)
            with patch.object(native_backup.psycopg2, "connect", return_value=connection):
                self.assertTrue(schedule.idle())

    def test_disabled_configuration_does_not_require_postgres_tools_or_create_directories(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(native_backup.configured(root, {}))
            self.assertEqual(list(root.iterdir()), [])

    def test_archive_timeout_closes_owned_dump_process_tree_before_service_restart(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schedule = native_backup.NativeBackup(root / "release", self.environment(root))
            with (
                patch.object(native_backup.subprocess, "Popen") as popen,
                patch.object(native_backup, "ProcessTree") as tree,
            ):
                popen.return_value.communicate.side_effect = (
                    native_backup.subprocess.TimeoutExpired("backup", 30)
                )
                with self.assertRaises(native_backup.subprocess.TimeoutExpired):
                    schedule.create_archive()
                tree.return_value.close.assert_called_once()
                tree.return_value.resume.assert_called_once()

    def test_native_host_connects_maintenance_to_owned_services_and_checks_restart_readiness(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schedule = Mock()
            schedule.due.return_value = schedule.idle.return_value = True
            schedule.execute.side_effect = lambda stop, start, now: (stop(), start())
            with (
                patch.object(
                    native_host.argparse.ArgumentParser,
                    "parse_args",
                    return_value=SimpleNamespace(release=root, dotnet="dotnet", workers=3),
                ),
                patch.object(native_host, "os", SimpleNamespace(name="nt", environ={})),
                patch.object(native_host.signal, "signal"),
                patch.object(native_host, "Event") as event,
                patch.object(native_host, "configured", return_value=schedule),
                patch.object(native_host, "wait_ready") as ready,
                patch.object(native_host.subprocess, "Popen"),
                patch.object(native_host.subprocess, "CREATE_NO_WINDOW", 0, create=True),
                patch.object(native_host, "ProcessTree") as tree,
            ):
                event.return_value.wait.side_effect = [False, True]
                event.return_value.is_set.return_value = False
                tree.return_value.process.poll.return_value = None
                native_host.main()
                self.assertEqual(tree.call_count, 8)
                self.assertEqual(tree.return_value.close.call_count, 8)
                ready.assert_called_once()
                schedule.execute.assert_called_once()

    def test_abandoned_running_state_is_reported_as_interrupted_on_supervisor_restart(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            schedule = native_backup.NativeBackup(root / "release", env)
            now = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
            schedule.write_state(dict(status="running", attempted_at=now.isoformat()))
            recovered = native_backup.NativeBackup(root / "release", env)
            self.assertEqual(recovered.read_state()["status"], "failed")
            self.assertEqual(recovered.read_state()["error_code"], "native_backup_interrupted")
            self.assertFalse(recovered.due(now))

    def test_unwritable_status_or_corrupt_state_does_not_stop_available_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schedule = native_backup.NativeBackup(root / "release", self.environment(root))
            now = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
            stopped = Mock()
            with patch.object(schedule, "write_state", side_effect=OSError("synthetic full disk")):
                self.assertFalse(schedule.execute(stopped, Mock(), now))
            stopped.assert_not_called()
            schedule.state_path.write_text("{bad json", encoding="utf-8")
            self.assertFalse(schedule.due(now))

    def test_archive_path_supports_unicode_under_legacy_windows_output_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            env.update(QUANT_BACKUP_DIR=str(root / "中文备份"), PYTHONIOENCODING="gbk")
            tools = root / "release" / "tools"
            tools.mkdir()
            (tools / "site_archive.py").write_text(
                "import sys\nfrom pathlib import Path\n"
                "archive=Path(sys.argv[sys.argv.index('--output')+1])/'quant-site-synthetic.zip'\n"
                "archive.write_bytes(b'synthetic archive protocol fixture')\nprint(archive)\n",
                encoding="utf-8",
            )
            schedule = native_backup.NativeBackup(root / "release", env)
            self.assertEqual(schedule.create_archive().parent, root / "中文备份")


if __name__ == "__main__":
    unittest.main()
