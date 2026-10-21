"""Native lease-bound Python computation process. Database writes belong to C#.

Public display jobs and pure strategy parameter views execute in this process;
they run on a small resident pool so one slow snapshot cannot block lighter
public queries. Financial and AI jobs keep child-process deadline isolation.
Internal endpoints authenticate the echoed owner token, user and attempt; stale
workers cannot publish.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlparse
from uuid import uuid4

import requests
from operations import CAPABILITIES, execute
from public_operations import IN_PROCESS_CAPABILITIES, child_public_operation
from worker_result import child_envelope

from quant_assistant.common.performance import Trace, log_slow, measure, trace_scope
from quant_assistant.integrations.tradingagents_worker.process import ProcessTree
from quant_assistant.providers.advisor_models import AdvisorModelError
from quant_assistant.services.model_costs import CostLimitError

MAX_PAYLOAD = 32 * 1024 * 1024
RESIDENT_CAPABILITIES = (*IN_PROCESS_CAPABILITIES, "strategy.view")
RESIDENT_MAX_CONCURRENCY = 8


def child_operation(kind, payload):
    # Operation-specific codes precede the shared contract mapping.
    return child_envelope(
        lambda: execute(kind, payload),
        pre_handlers=[
            (AdvisorModelError, lambda error: {"result": None, "error_code": str(error)}),
            (CostLimitError, lambda _error: {"result": None, "error_code": "model_budget_exceeded"}),
        ],
    )


class Worker:
    def __init__(self, api: str, secret: str, worker_id: str | None = None):
        parsed = urlparse(api)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("Invalid internal API URL")
        if parsed.scheme == "http" and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("Plain HTTP internal API must be on loopback")
        if len(secret) < 32:
            raise ValueError("Configure a worker secret of at least 32 characters")
        self.api, self.stopping = api.rstrip("/") + "/internal/v1/worker", Event()
        self.worker_id = worker_id or uuid4().hex
        if not 1 <= len(self.worker_id) <= 64:
            raise ValueError("Worker identity must contain 1 to 64 characters")
        try:
            resident_concurrency = int(os.environ.get("QUANT_RESIDENT_CONCURRENCY", "3"))
        except ValueError:
            raise ValueError("QUANT_RESIDENT_CONCURRENCY must be an integer") from None
        if not 1 <= resident_concurrency <= RESIDENT_MAX_CONCURRENCY:
            raise ValueError(
                f"QUANT_RESIDENT_CONCURRENCY must be between 1 and {RESIDENT_MAX_CONCURRENCY}"
            )
        self.resident = ThreadPoolExecutor(
            max_workers=resident_concurrency, thread_name_prefix="quant-resident"
        )
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["X-Quant-Worker-Secret"] = secret
        self.trace_id = None

    def post(self, action, payload, trace_id=None):
        trace = trace_id if trace_id is not None else self.trace_id
        headers = {"X-Trace-Id": trace} if trace and action != "claim" else {}
        return self.session.post(
            self.api + "/" + action, json=payload, headers=headers, timeout=(3, 10)
        )

    def run_public_job(self, job, lease, deadline, trace_id):
        done, lost = Event(), Event()

        def heartbeat():
            while not done.wait(5):
                try:
                    if (
                        self.stopping.is_set()
                        or datetime.now(UTC) >= deadline
                        or self.post("heartbeat", lease, trace_id).status_code != 200
                    ):
                        lost.set()
                        return
                except requests.RequestException:
                    lost.set()
                    return

        monitor = Thread(
            target=heartbeat, daemon=True, name=f"quant-public-heartbeat-{job['job_id'][:8]}"
        )
        monitor.start()
        try:
            with trace_scope(trace_id) as trace, measure("worker"):
                operation = (
                    child_operation if job["kind"] == "strategy.view" else child_public_operation
                )
                result = operation(job["kind"], job["input"])
            result["timings"] = trace.timings
            log_slow(trace, job["kind"], job["job_id"])
            if len(json.dumps(result, allow_nan=False).encode()) > MAX_PAYLOAD:
                result = {"result": None, "error_code": "worker_output_too_large"}
        finally:
            done.set()
            monitor.join()
        # In-process reads cannot kill a blocked library call. Never publish its
        # eventual result after shutdown, deadline expiry or loss of the lease.
        if not lost.is_set() and not self.stopping.is_set() and datetime.now(UTC) < deadline:
            self.post("finish", {**lease, **result}, trace_id)

    def run_job(self, job):
        if job.get("version") != 1 or job.get("kind") not in CAPABILITIES:
            return
        self.trace_id = Trace(job.get("trace_id")).trace_id
        lease = {key: job[key] for key in ("user_id", "job_id", "attempt", "owner_token")}
        deadline = datetime.fromisoformat(job["deadline"].replace("Z", "+00:00"))
        if (
            self.stopping.is_set()
            or datetime.now(UTC) >= deadline
            or self.post("heartbeat", lease).status_code != 200
        ):
            return
        if job["kind"] not in RESIDENT_CAPABILITIES:
            job["input"]["_execution"] = {
                "api": self.api,
                "secret": self.session.headers["X-Quant-Worker-Secret"],
                "lease": lease,
            }
        body = json.dumps(
            {
                "kind": job["kind"],
                "input": job["input"],
                "trace_id": self.trace_id,
                "started_at": time.perf_counter(),
            },
            allow_nan=False,
        ).encode()
        if len(body) > MAX_PAYLOAD:
            self.post(
                "finish",
                {**lease, "result": None, "error_code": "worker_input_too_large"},
            )
            return
        if job["kind"] in RESIDENT_CAPABILITIES:
            # Resident queries run on a bounded pool so a slow snapshot fetch
            # cannot serialize lighter public jobs behind it. The lease, the
            # heartbeat and the publish guard all travel with the job itself.
            self.resident.submit(self.run_public_job, job, lease, deadline, self.trace_id)
            return
        with tempfile.TemporaryDirectory(prefix="quant-native-job-") as directory:
            output_path = Path(directory) / "result.json"
            from worker_environment import worker_environment

            environment = {
                **worker_environment(os.environ),
                "QUANT_NATIVE_PARENT_PID": str(os.getpid()),
            }
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("job_child.py")),
                    str(output_path),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            try:
                tree = ProcessTree(process)
            except BaseException:
                process.kill()
                process.wait(timeout=5)
                raise
            heartbeat_at, owned = 0.0, True
            writer = None
            try:
                tree.resume()

                def feed_input():
                    try:
                        process.stdin.write(body)
                    except OSError:
                        pass  # Deadline cancellation can close the receiving pipe.
                    finally:
                        with suppress(OSError):
                            process.stdin.close()

                # Large inputs may block a pipe if a child stalls before reading.
                # Keep deadline and lease checks on the controlling thread.
                writer = Thread(target=feed_input, daemon=True, name="quant-job-input")
                writer.start()
                while process.poll() is None and not self.stopping.wait(0.2):
                    if datetime.now(UTC) >= deadline:
                        break
                    if time.monotonic() >= heartbeat_at:
                        if self.post("heartbeat", lease).status_code != 200:
                            owned = False
                            break
                        heartbeat_at = time.monotonic() + 5
                result = {"result": None, "error_code": "worker_interrupted"}
                if (
                    process.poll() == 0
                    and output_path.is_file()
                    and output_path.stat().st_size <= MAX_PAYLOAD
                ):
                    result = json.loads(output_path.read_bytes())
            finally:
                # Release descendants before publishing: a process that has lost
                # its lease cannot continue work after a successful finish call.
                tree.close()
                if writer is not None:
                    writer.join(timeout=5)
            if owned:
                self.post("finish", {**lease, **result})

    def run(self):
        try:
            while not self.stopping.is_set():
                try:
                    response = self.post(
                        "claim",
                        {"worker_id": self.worker_id, "capabilities": list(CAPABILITIES)},
                    )
                    if response.status_code == 204:
                        continue  # The API already waits for a queue notification.
                    else:
                        response.raise_for_status()
                        self.run_job(response.json())
                except requests.RequestException:
                    self.stopping.wait(2)
        finally:
            # Queued claims are dropped and reclaimed by the API lease; the
            # in-flight job decides by itself whether publishing is still legal.
            self.resident.shutdown(wait=True, cancel_futures=True)


def _init_public_source_failure_tracker():
    """Install the persistent public-source failure tracker (F3).

    Counts consecutive per-host public fetch failures across restarts;
    stream A consumes the state file for the critical alert at
    QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS (default 3).
    """
    from pathlib import Path

    from public_source_failures import (
        PublicSourceFailureTracker,
        alert_rounds_from_env,
    )
    from quant_assistant.providers.public_http import PublicHttp

    site_root = os.environ.get("QUANT_SITE_ROOT")
    if not site_root:
        return
    tracker = PublicSourceFailureTracker(
        Path(site_root) / ".public-source-failures.json",
        alert_rounds=alert_rounds_from_env(),
    )
    PublicHttp.set_default_failure_tracker(tracker)


def main():
    from worker_environment import worker_environment

    filtered = worker_environment(os.environ)
    os.environ.clear()
    os.environ.update(filtered)
    _init_public_source_failure_tracker()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api",
        default=os.environ.get("QUANT_INTERNAL_API_URL", "http://127.0.0.1:8765"),
    )
    parser.add_argument("--worker-id")
    arguments = parser.parse_args()
    worker = Worker(arguments.api, os.environ.get("QUANT_WORKER_SECRET", ""), arguments.worker_id)
    for value in (signal.SIGINT, signal.SIGTERM):
        signal.signal(value, lambda *_: worker.stopping.set())
    worker.run()


if __name__ == "__main__":
    main()
