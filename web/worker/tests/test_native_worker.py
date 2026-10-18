"""Worker boundaries with synthetic inputs and no external model calls."""

import hashlib
import os
import sys
import tempfile
import time
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import native_host
import nav_operations
import paid_operations
import public_model_http
import run_worker
import urllib3
from analysis_context import FACTS, build_context
from worker_environment import worker_environment

from quant_assistant.providers.advisor_models import AdvisorModelError


class NativeWorkerTests(unittest.TestCase):
    def test_tls_failure_tries_another_public_address_before_http_is_sent(self):
        addresses = ["2606:4700:4700::1111", "8.8.8.8"]
        connection = public_model_http.PinnedHTTPSConnection(
            addresses[0],
            port=443,
            candidate_addresses=addresses,
            timeout=5,
            server_hostname="models.example.org",
            assert_hostname="models.example.org",
        )
        visited = []

        def connect(instance):
            visited.append(instance.host)
            self.assertEqual(instance.server_hostname, "models.example.org")
            if len(visited) == 1:
                raise TimeoutError("synthetic TLS failure")

        with patch.object(urllib3.connection.HTTPSConnection, "connect", connect):
            connection.connect()
        self.assertEqual(visited, addresses)

    def test_read_timeout_after_http_is_sent_never_repeats_model_request(self):
        with (
            patch.object(
                public_model_http.socket,
                "getaddrinfo",
                return_value=[
                    (2, 1, 6, "", ("8.8.8.8", 443)),
                    (2, 1, 6, "", ("1.1.1.1", 443)),
                ],
            ),
            patch.object(public_model_http.urllib3, "HTTPSConnectionPool") as pool,
        ):
            pool.return_value.request.side_effect = urllib3.exceptions.ReadTimeoutError(
                None, "/", "synthetic"
            )
            with self.assertRaises(public_model_http.requests.ReadTimeout):
                public_model_http.post(
                    "https://models.example.org/v1/chat/completions",
                    headers={},
                    json={},
                    timeout=(1, 1),
                )
        self.assertEqual(pool.return_value.request.call_count, 1)

    def test_tls_address_fallback_has_one_total_connect_deadline(self):
        connection = public_model_http.PinnedHTTPSConnection(
            "8.8.8.8",
            candidate_addresses=["8.8.8.8", "1.1.1.1", "9.9.9.9"],
            timeout=5,
            server_hostname="models.example.org",
            assert_hostname="models.example.org",
        )
        with (
            patch.object(public_model_http.time, "monotonic", side_effect=[0, 0, 1, 6]),
            patch.object(
                urllib3.connection.HTTPSConnection,
                "connect",
                side_effect=TimeoutError("synthetic"),
            ) as connect,
            self.assertRaises(urllib3.exceptions.ConnectTimeoutError),
        ):
            connection.connect()
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(connection.timeout, 5)

    def test_populated_readiness_report_crosses_the_json_worker_boundary(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, QUANT_SITE_ROOT=directory),
        ):
            tables = {table: [] for table in FACTS}
            tables["positions"] = [
                {
                    "symbol": "000001",
                    "asset_type": "fund",
                    "quantity": "100",
                    "average_cost": "1.1",
                    "market_value": "120",
                    "as_of_date": "2026-01-02",
                    "updated_at": "2026-01-02T15:00:00+08:00",
                }
            ]
            snapshot = {
                "version": 1,
                "user_id": "a" * 32,
                "role": "admin",
                "tables": tables,
            }
            result = run_worker.child_operation("analysis.readiness", {"snapshot": snapshot})
            self.assertIsNone(result["error_code"])
            account = result["result"]["checks"]["account_baseline"]
            self.assertEqual(account["as_of_date"], "2026-01-02")
            self.assertFalse(account["passed"])
            tables["data_snapshots"] = [
                {
                    "id": 1,
                    "dataset": "daily_prices",
                    "source": "synthetic",
                    "as_of_date": "2026-01-02",
                    "fetched_at": "2026-01-02T18:00:00+08:00",
                    "path": str(Path(directory) / "missing-prices.parquet"),
                    "sha256": "a" * 64,
                    "row_count": 1,
                    "status": "valid",
                    "issues_json": [],
                }
            ]
            result = run_worker.child_operation("analysis.readiness", {"snapshot": snapshot})
            self.assertIsNone(result["error_code"])
            point = result["result"]["checks"]["point_in_time_data"]
            self.assertEqual(point["as_of"], "2026-01-02")
            self.assertFalse(point["passed"])
            self.assertIn("instrument_history_has_fewer_than_two_dates", point["blockers"])

    def test_native_host_loads_api_configuration_and_filters_worker_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = {
                "QUANT_PG_CONNECTION": "synthetic",
                "QUANT_SITE_ROOT": directory,
            }
            with (
                patch.object(
                    native_host.argparse.ArgumentParser,
                    "parse_args",
                    return_value=SimpleNamespace(release=root, dotnet="dotnet", workers=3),
                ),
                patch.object(native_host, "os", SimpleNamespace(name="nt", environ=environment)),
                patch.object(native_host.signal, "signal"),
                patch.object(native_host, "Event") as event,
                patch.object(native_host.subprocess, "CREATE_NO_WINDOW", 0, create=True),
                patch.object(native_host.subprocess, "Popen") as popen,
                patch.object(native_host, "ProcessTree") as tree,
            ):
                event.return_value.wait.return_value = True
                native_host.main()
            self.assertEqual(popen.call_count, 4)
            calls = popen.call_args_list
            self.assertEqual(calls[0].kwargs["cwd"], root / "api")
            self.assertEqual(calls[0].kwargs["env"], environment)
            for call in calls[1:]:
                self.assertEqual(call.kwargs["cwd"], root)
                self.assertEqual(call.kwargs["env"], {"QUANT_SITE_ROOT": directory})
            self.assertEqual(tree.return_value.close.call_count, 4)

    def test_account_snapshot_uses_existing_lot_table_and_keeps_exact_money(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, QUANT_SITE_ROOT=directory),
        ):
            tables = {table: [] for table in FACTS}
            tables["positions"] = [
                {
                    "symbol": "000001",
                    "asset_type": "fund",
                    "quantity": "1.23456789",
                    "average_cost": "1.250000",
                    "market_value": "1.54320986",
                    "as_of_date": "2026-01-02",
                    "updated_at": "2026-01-02T15:00:00+08:00",
                }
            ]
            snapshot = {
                "version": 1,
                "user_id": "a" * 32,
                "role": "admin",
                "tables": tables,
            }
            database, _, _ = build_context(snapshot)
            try:
                with database.connection() as connection:
                    self.assertEqual(
                        connection.execute("SELECT quantity FROM positions").fetchone()[0],
                        Decimal("1.23456789"),
                    )
                    self.assertEqual(
                        connection.execute("SELECT count(*) FROM fund_holding_lots").fetchone()[0],
                        0,
                    )
            finally:
                database.close()

    def test_formal_nav_keeps_separate_frame_and_file_hashes(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, QUANT_SITE_ROOT=directory),
        ):
            point = SimpleNamespace(
                nav_date=date(2026, 1, 2),
                source="eastmoney",
                unit_nav=Decimal("1.23456789"),
                accumulated_nav=None,
            )
            with patch.object(
                nav_operations.PublicFundDetailProvider,
                "nav_history",
                return_value=[point],
            ):
                result = nav_operations.execute_nav(
                    "fund.nav_sources",
                    {
                        "snapshot": {
                            "version": 1,
                            "user_id": "a" * 32,
                            "role": "admin",
                        },
                        "as_of_date": "2026-01-02",
                        "symbols": ["000001"],
                    },
                )
            self.assertEqual(result["blockers"], [])
            source = result["sources"][0]
            self.assertEqual(source["unit_nav"], "1.23456789")
            with Path(source["snapshot_path"]).open("rb") as stream:
                self.assertEqual(
                    source["snapshot_file_sha256"],
                    hashlib.file_digest(stream, "sha256").hexdigest(),
                )
            self.assertNotEqual(source["snapshot_file_sha256"], source["snapshot_sha256"])

    def test_compute_environment_does_not_inherit_database_or_server_keys(self):
        environment = {
            "PATH": "synthetic-path",
            "QUANT_SITE_ROOT": "synthetic-site",
            "QUANT_WORKER_SECRET": "synthetic-worker-secret",
            "PGPASSWORD": "synthetic-database-secret",
            "QUANT_PG_CONNECTION": "synthetic-connection",
            "ConnectionStrings__Default": "synthetic-connection",
            "QUANT_SECRET_ROOT": "synthetic-private-directory",
            "QUANT_INIT_ADMIN_PASSWORD": "synthetic-admin-secret",
        }
        filtered = worker_environment(environment)
        self.assertEqual(set(filtered), {"PATH", "QUANT_SITE_ROOT", "QUANT_WORKER_SECRET"})

    def test_blocked_input_pipe_does_not_prevent_deadline_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            child = Path(directory) / "job_child.py"
            child.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            worker = run_worker.Worker("http://127.0.0.1:8765", "synthetic-secret-" * 3)
            posts = []

            def post(action, payload):
                posts.append((action, payload))
                return SimpleNamespace(status_code=200)

            worker.post = post
            job = {
                "version": 1,
                "kind": "analysis.backtest",
                "user_id": "a" * 32,
                "job_id": "job",
                "attempt": 1,
                "owner_token": "synthetic-owner",
                "deadline": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
                "input": {"sample": "x" * (2 * 1024 * 1024)},
            }
            start = time.monotonic()
            with patch.object(run_worker, "__file__", str(Path(directory) / "run_worker.py")):
                worker.run_job(job)
            self.assertLess(time.monotonic() - start, 7)
            self.assertEqual(posts[-1][0], "finish")
            self.assertEqual(posts[-1][1]["error_code"], "worker_interrupted")
            worker.session.close()

    def test_private_and_mixed_dns_answers_never_open_a_model_socket(self):
        for addresses in [
            ("127.0.0.1",),
            ("8.8.8.8", "10.0.0.1"),
            ("::ffff:127.0.0.1",),
        ]:
            answers = [(2, 1, 6, "", (address, 443)) for address in addresses]
            with (
                patch.object(public_model_http.socket, "getaddrinfo", return_value=answers),
                patch.object(public_model_http.urllib3, "HTTPSConnectionPool") as pool,
                self.assertRaisesRegex(AdvisorModelError, "model_configuration"),
            ):
                public_model_http.post(
                    "https://models.example.org/v1/chat/completions",
                    headers={},
                    json={},
                    timeout=(1, 1),
                )
            pool.assert_not_called()

    def test_public_socket_keeps_tls_hostname_and_bounds_response(self):
        response = SimpleNamespace(status=200, read=lambda size: b"{}", close=lambda: None)
        with (
            patch.object(
                public_model_http.socket,
                "getaddrinfo",
                return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
            ),
            patch.object(public_model_http.urllib3, "HTTPSConnectionPool") as pool,
        ):
            pool.return_value.request.return_value = response
            result = public_model_http.post(
                "https://models.example.org/v1/chat/completions",
                headers={},
                json={},
                timeout=(1, 1),
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(pool.call_args.args[0], "8.8.8.8")
        self.assertEqual(pool.call_args.kwargs["server_hostname"], "models.example.org")
        self.assertEqual(pool.call_args.kwargs["assert_hostname"], "models.example.org")
        self.assertFalse(pool.return_value.request.call_args.kwargs["redirect"])

    def test_changed_preview_is_rejected_before_reserving_or_calling_a_model(self):
        payload = {
            "credentials": {
                "endpoint": "https://models.example.org/v1",
                "api_key": "synthetic",
                "jev_api_key": "synthetic",
            },
            "models": {"strategy": "synthetic-model"},
            "_execution": {
                "api": "http://127.0.0.1/internal",
                "secret": "synthetic",
                "lease": {},
            },
            "preview_expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
            "snapshot": {},
            "preview_parameters": {},
            "preview_content_hash": "original",
        }
        with (
            patch.object(
                paid_operations,
                "execute_strategy",
                return_value={"_evidence": {"content_hash": "changed"}},
            ),
            patch.object(paid_operations.CostClient, "reserve") as reserve,
            patch.object(paid_operations, "public_post") as call,
            self.assertRaisesRegex(AdvisorModelError, "preview_changed"),
        ):
            paid_operations.execute_paid("agent.research", payload)
        reserve.assert_not_called()
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
