"""Execute one named computation in the parent's owned process tree."""

import ctypes
import json
import os
import signal
import sys
import time
from pathlib import Path

from public_operations import PUBLIC_CAPABILITIES, child_public_operation

MAX_PAYLOAD = 32 * 1024 * 1024


def main():
    if sys.platform == "linux":
        # The native systemd unit also kills the complete worker control group.
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0:
            raise SystemExit(1)
        if os.getppid() != int(os.environ.get("QUANT_NATIVE_PARENT_PID", "-1")):
            raise SystemExit(1)
    content = sys.stdin.buffer.read(MAX_PAYLOAD + 1)
    if len(content) > MAX_PAYLOAD:
        raise SystemExit(1)
    task = json.loads(content)
    from quant_assistant.common.performance import log_slow, measure, trace_scope

    if task["kind"] not in PUBLIC_CAPABILITIES:
        from run_worker import child_operation
    with trace_scope(task.get("trace_id")) as trace:
        trace.record(
            "startup",
            max(0, (time.perf_counter() - task.get("started_at", time.perf_counter())) * 1000),
        )
        with measure("worker"):
            outcome = (
                child_public_operation if task["kind"] in PUBLIC_CAPABILITIES else child_operation
            )(task["kind"], task["input"])
    outcome["timings"] = trace.timings
    log_slow(trace, task["kind"], "child")
    result = json.dumps(outcome, allow_nan=False).encode()
    if len(result) > MAX_PAYLOAD:
        result = b'{"result":null,"error_code":"worker_output_too_large"}'
    target = Path(sys.argv[1])
    target.write_bytes(result)
    if os.name != "nt":
        target.chmod(0o600)


if __name__ == "__main__":
    main()
