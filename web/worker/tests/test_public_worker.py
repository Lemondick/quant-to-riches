"""Public reads reuse providers without weakening lease publication checks."""

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import public_operations
import run_worker


class SynchronousExecutor:
    """Run submitted resident jobs inline so assertions stay deterministic."""

    def submit(self, function, *args):
        function(*args)
        return SimpleNamespace(cancel=lambda: True)

    def shutdown(self, wait=True, cancel_futures=False):
        pass


class PublicWorkerTests(unittest.TestCase):
    def test_catalog_uses_bounded_resident_provider_and_rejects_private_inputs(self):
        provider = SimpleNamespace(
            read=lambda: {"items": [], "source_time": None}, expire_cache=lambda: None
        )
        with patch.object(public_operations, "_catalog_provider", return_value=provider) as factory:
            self.assertIn("fund.catalog", run_worker.RESIDENT_CAPABILITIES)
            self.assertIsNone(public_operations.execute_public("fund.catalog", {})["source_time"])
            for key in ("user_id", "snapshot", "credentials", "symbol"):
                with self.assertRaises(ValueError):
                    public_operations.execute_public("fund.catalog", {key: "private"})
            self.assertEqual(factory.call_count, 1)

    def setUp(self):
        self.worker = run_worker.Worker("http://127.0.0.1:8765", "synthetic-secret-" * 3)
        self.worker.resident = SynchronousExecutor()
        self.addCleanup(self.worker.session.close)
        self.posts = []

        def post(action, payload, trace_id=None):
            self.posts.append((action, payload))
            return SimpleNamespace(status_code=200)

        self.worker.post = post

    def job(self, kind="market.snapshot", **overrides):
        return {
            "version": 1,
            "kind": kind,
            "user_id": "a" * 32,
            "job_id": "synthetic-job",
            "attempt": 1,
            "owner_token": "synthetic-owner",
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "input": {},
            **overrides,
        }

    def test_all_public_kinds_execute_without_child_process_or_execution_secrets(self):
        for kind in public_operations.IN_PROCESS_CAPABILITIES:
            with (
                self.subTest(kind=kind),
                patch.object(run_worker.subprocess, "Popen") as process,
                patch.object(
                    run_worker,
                    "child_public_operation",
                    return_value={"result": {"kind": kind}, "error_code": None},
                ) as execute,
            ):
                job = self.job(kind)
                self.worker.run_job(job)
                process.assert_not_called()
                execute.assert_called_once_with(kind, {})
                self.assertNotIn("_execution", job["input"])
                action, payload = self.posts[-1]
                self.assertEqual(action, "finish")
                self.assertEqual(payload["owner_token"], job["owner_token"])
                self.assertEqual(payload["attempt"], job["attempt"])
                self.assertEqual(payload["result"], {"kind": kind})

    def test_expired_rejected_and_stopping_jobs_never_execute(self):
        for condition in ("expired", "rejected", "stopping"):
            self.posts.clear()
            self.worker.stopping.clear()
            job = self.job()
            if condition == "expired":
                job["deadline"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            elif condition == "stopping":
                self.worker.stopping.set()
            with (
                self.subTest(condition=condition),
                patch.object(run_worker, "child_public_operation") as execute,
                patch.object(self.worker, "post", return_value=SimpleNamespace(status_code=409)),
            ):
                self.worker.run_job(job)
                execute.assert_not_called()

    def test_strategy_parameter_views_are_resident_and_do_not_share_account_results(
        self,
    ):
        with (
            patch.object(run_worker.subprocess, "Popen", side_effect=AssertionError("cold child")),
            patch.object(
                run_worker,
                "child_public_operation",
                side_effect=AssertionError("private view"),
            ),
        ):
            for user, weight in (("a" * 32, 0.02), ("b" * 32, 0.04)):
                job = self.job(
                    "strategy.view",
                    user_id=user,
                    input={"parameters": {"target_weight": weight}},
                )
                self.worker.run_job(job)
                action, payload = self.posts[-1]
                self.assertEqual(action, "finish")
                self.assertEqual(payload["user_id"], user)
                self.assertEqual(payload["result"]["parameters"]["target_weight"], weight)
                self.assertNotIn("_execution", job["input"])

    def test_result_after_deadline_or_shutdown_is_not_published(self):
        for condition in ("expired", "stopping"):
            self.posts.clear()
            self.worker.stopping.clear()
            job = self.job()
            deadline = datetime.fromisoformat(job["deadline"])

            def execute(*_, condition=condition):
                if condition == "stopping":
                    self.worker.stopping.set()
                return {"result": {}, "error_code": None}

            with (
                self.subTest(condition=condition),
                patch.object(run_worker, "child_public_operation", side_effect=execute),
                patch.object(run_worker, "datetime") as clock,
            ):
                clock.fromisoformat.return_value = deadline
                clock.now.side_effect = [
                    deadline - timedelta(seconds=1),
                    deadline + timedelta(seconds=1),
                ]
                self.worker.run_job(job)
            self.assertEqual([action for action, _ in self.posts], ["heartbeat"])

    def test_heartbeat_loss_during_public_read_prevents_publication(self):
        from unittest.mock import Mock

        done, lost = Mock(), Event()
        done.wait.return_value = False
        job = self.job()
        with (
            patch.object(run_worker, "Event", side_effect=[done, lost]),
            patch.object(
                self.worker, "post", return_value=SimpleNamespace(status_code=409)
            ) as post,
            patch.object(
                run_worker,
                "child_public_operation",
                return_value={"result": {}, "error_code": None},
            ),
        ):
            self.worker.run_public_job(job, {}, datetime.fromisoformat(job["deadline"]), None)
        self.assertTrue(lost.is_set())
        self.assertEqual([call.args[0] for call in post.call_args_list], ["heartbeat"])

    def test_oversized_public_input_and_output_are_rejected(self):
        with (
            patch.object(run_worker, "MAX_PAYLOAD", 50),
            patch.object(run_worker, "child_public_operation") as execute,
        ):
            self.worker.run_job(self.job(input={"large": "x" * 100}))
            execute.assert_not_called()
            self.assertEqual(self.posts[-1][1]["error_code"], "worker_input_too_large")
        with (
            patch.object(run_worker, "MAX_PAYLOAD", 256),
            patch.object(
                run_worker,
                "child_public_operation",
                return_value={"result": {"large": "x" * 512}, "error_code": None},
            ),
        ):
            self.worker.run_job(self.job())
            self.assertEqual(self.posts[-1][1]["error_code"], "worker_output_too_large")

    def test_forced_market_refresh_expires_cache_but_retains_dated_fallbacks(self):
        for factory, namespace in (
            (public_operations._detail_provider, "provider.fund_detail"),
            (public_operations._market_provider, "provider.market_feed"),
            (public_operations._valuation_provider, "provider.fund_valuation"),
        ):
            public_operations.cache_invalidate(namespace)
            self.addCleanup(public_operations.cache_invalidate, namespace)
            self.assertIs(factory(), factory())
        previous = public_operations._market_provider()
        with (
            patch.object(type(previous), "market_snapshot", side_effect=ValueError("synthetic")),
            patch.object(type(previous), "expire_cache") as expire,
        ):
            public_operations.child_public_operation("market.snapshot", {"_refresh": True})
        expire.assert_called_once()
        self.assertIs(previous, public_operations._market_provider())


if __name__ == "__main__":
    unittest.main()
