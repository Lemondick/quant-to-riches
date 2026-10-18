"""Exercise deployment guards without installing packages or starting host services."""

import configparser
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
BASH = os.environ.get("QUANT_TEST_BASH") or (shutil.which("bash") if os.name != "nt" else None)


@unittest.skipUnless(BASH, "Set QUANT_TEST_BASH on Windows; Linux uses its native bash")
class DeploymentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quant-deploy-check-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "deploy").mkdir()
        shutil.copyfile(DEPLOY / "prepare-linux.sh", self.root / "deploy/prepare-linux.sh")
        for name in [
            "api/QuantAssistant.Api.dll",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "worker/run_worker.py",
            "migrations/0019_model_bill_evidence.sql",
        ]:
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.touch()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        scripts = {
            "uname": 'if [[ "$1" == -s ]]; then echo "${TEST_KERNEL:-Linux}"; else echo x86_64; fi',
            "dotnet": (
                'echo "Microsoft.NETCore.App 10.0.12 [/runtime]"\n'
                '[[ "${TEST_MISSING_ASPNET:-0}" == 1 ]] || '
                'echo "Microsoft.AspNetCore.App 10.0.12 [/runtime]"\nexit 0'
            ),
            "id": 'echo "${TEST_UID:-1001}"',
            "uv": 'echo invoked > "$TEST_UV_MARKER"\nexit "${TEST_UV_EXIT:-1}"',
            "pg_dump": 'echo "pg_dump (PostgreSQL) ${TEST_PG_MAJOR:-18}.0"',
            "pg_restore": 'echo "pg_restore (PostgreSQL) ${TEST_PG_MAJOR:-18}.0"',
            "psql": 'echo "psql (PostgreSQL) ${TEST_PG_MAJOR:-18}.0"',
            "curl": "exit 0",
            "systemctl": "exit 0",
            "caddy": "exit 0",
        }
        for name, body in scripts.items():
            path = self.bin / name
            path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
            path.chmod(0o755)

    def check(self, mode="--check", **overrides):
        env = dict(os.environ)
        env.pop("QUANT_PG_BIN", None)
        env.update(
            TEST_UV_MARKER=(self.root / "uv-invoked").as_posix(),
            **overrides,
        )
        return subprocess.run(
            [
                BASH,
                "-c",
                'export PATH="$PWD/bin:$PATH"; exec bash deploy/prepare-linux.sh "$1"',
                "preflight-test",
                mode,
            ],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def test_non_linux_host_is_rejected_before_any_installation(self):
        result = self.check(TEST_KERNEL="MINGW64_NT")
        self.assertEqual(result.returncode, 2)
        self.assertIn("target Linux host", result.stderr)
        self.assertFalse((self.root / "uv-invoked").exists())

    def test_missing_aspnet_runtime_is_not_accepted_as_plain_dotnet(self):
        result = self.check(TEST_MISSING_ASPNET="1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Missing .NET 10 runtime: Microsoft.AspNetCore.App", result.stderr)

    def test_old_postgresql_clients_are_rejected(self):
        result = self.check(TEST_PG_MAJOR="17")
        self.assertEqual(result.returncode, 2)
        self.assertIn("PostgreSQL client 18", result.stderr)

    def test_read_only_check_does_not_create_a_virtual_environment(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("certificate checks remain", result.stdout)
        self.assertFalse((self.root / "uv-invoked").exists())
        self.assertFalse((self.root / "backend/.venv").exists())

    def test_failed_sync_is_not_reported_as_ready(self):
        result = self.check("--sync", TEST_UV_EXIT="23")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertTrue((self.root / "uv-invoked").exists())
        self.assertNotIn("environment ready", result.stdout)

    def test_sync_as_root_is_rejected(self):
        result = self.check("--sync", TEST_UID="0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not root", result.stderr)
        self.assertFalse((self.root / "uv-invoked").exists())


class WorkerRecoveryContractTests(unittest.TestCase):
    def test_api_start_pulls_back_the_workers_it_stops(self):
        api = configparser.ConfigParser(interpolation=None, strict=False)
        api.read(DEPLOY / "quant-api.service")
        worker = configparser.ConfigParser(interpolation=None, strict=False)
        worker.read(DEPLOY / "quant-worker@.service")
        self.assertEqual(
            set(api["Unit"]["Wants"].split()),
            {"quant-worker@1.service", "quant-worker@2.service", "quant-worker@3.service"},
        )
        self.assertIn("quant-api.service", worker["Unit"]["Requires"].split())
        self.assertIn("quant-api.service", worker["Unit"]["PartOf"].split())
        self.assertIn("quant-api.service", worker["Unit"]["After"].split())
        self.assertEqual(api["Service"]["Restart"], "on-failure")
        self.assertEqual(worker["Service"]["Restart"], "on-failure")
