"""Run real strategy computations for the isolated loopback browser acceptance test."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_worker  # noqa: E402
from worker_environment import worker_environment  # noqa: E402

if __name__ == "__main__":
    filtered = worker_environment(os.environ)
    os.environ.clear()
    os.environ.update(filtered)
    # Use the production lease, subprocess, notification and result paths. Only
    # advertise local strategy operations; this test never calls a paid model.
    run_worker.CAPABILITIES = ("strategy.view", "strategy.preview")
    run_worker.main()
