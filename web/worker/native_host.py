"""Windows native service supervisor with owned process trees.

Run this command under a service manager configured to restart on failure.
Closing or killing this supervisor closes its Job handles and kills descendants.
Linux deployments use the supplied systemd units and their control groups.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from native_backup import configured
from worker_environment import worker_environment

from quant_assistant.integrations.tradingagents_worker.process import ProcessTree


def wait_ready(environment, owned, stopping):
    url = environment.get("QUANT_INTERNAL_API_URL", "http://127.0.0.1:8765").rstrip("/") + "/readyz"
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if stopping.is_set() or any(tree.process.poll() is not None for tree in owned):
            break
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        if stopping.wait(0.5):
            break
    raise RuntimeError("Native services did not become ready after maintenance")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--workers", type=int, choices=range(1, 4), default=3)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Use the supplied systemd units on Linux")
    root = args.release.resolve(strict=True)
    maintenance = configured(root, os.environ)
    stopping = Event()
    for value in (signal.SIGINT, signal.SIGTERM):
        signal.signal(value, lambda *_: stopping.set())
    commands = [[args.dotnet, str(root / "api" / "QuantAssistant.Api.dll")]]
    commands.extend(
        [
            [
                str(root / "backend/.venv/Scripts/python.exe"),
                str(root / "worker/run_worker.py"),
                "--worker-id",
                f"native-{index}",
            ]
            for index in range(args.workers)
        ]
    )
    owned = []

    def stop_owned():
        failure = None
        # Retain handles until each close succeeds so the final cleanup can retry.
        for tree in reversed(owned[:]):
            try:
                tree.close()
                owned.remove(tree)
            except Exception as error:
                failure = failure or error
        if failure:
            raise failure

    def start_owned():
        for index, command in enumerate(commands):
            process = subprocess.Popen(
                command,
                # ASP.NET resolves appsettings.json from its content directory.
                cwd=root / "api" if index == 0 else root,
                env=dict(os.environ) if index == 0 else worker_environment(os.environ),
                stdout=sys.stdout,
                stderr=sys.stderr,
                creationflags=subprocess.CREATE_NO_WINDOW | 0x4,
            )
            try:
                tree = ProcessTree(process)
            except BaseException:
                process.kill()
                process.wait(timeout=5)
                raise
            owned.append(tree)
            tree.resume()

    def restart_owned():
        if stopping.is_set():
            return
        start_owned()
        wait_ready(os.environ, owned, stopping)

    try:
        start_owned()
        while not stopping.wait(0.5):
            if any(tree.process.poll() is not None for tree in owned):
                raise RuntimeError("A native service exited; all owned services were stopped")
            now = datetime.now(UTC)
            if maintenance and maintenance.due(now):
                if maintenance.idle():
                    maintenance.execute(stop_owned, restart_owned, now)
                else:
                    maintenance.defer(now)
    finally:
        stop_owned()


if __name__ == "__main__":
    main()
