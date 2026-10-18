"""File exchange transport: no API credentials, database mounts, or Docker socket."""

import json
import os
import re
import shutil
import time
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

try:
    from .process import ProcessFailure, SubprocessWorker
except ImportError:  # Standalone container entry point.
    from process import ProcessFailure, SubprocessWorker


def write_json(path, value):
    temporary = path.with_suffix("." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path, maximum=128000):
    if path.stat().st_size > maximum:
        raise ValueError("oversized task")
    return json.loads(path.read_text(encoding="utf-8"))


def age(path):
    try:
        return max(0, time.time() - path.stat().st_mtime)
    except OSError:
        return float("inf")


class ExchangeWorker:
    def __init__(self, directory):
        self.directory = Path(directory)

    def check_available(self, config):
        path = self.directory / "ready.json"
        try:
            valid = age(path) < 10 and read_json(path) == {"config": config, "ready": True}
        except (OSError, ValueError):
            valid = False
        if not valid:
            raise ProcessFailure("worker_unavailable")

    def run(self, task, cancel):
        self.check_available(task["config"])
        directory = self.directory / uuid4().hex
        directory.mkdir(mode=0o700)
        heartbeat = directory / "heartbeat"
        heartbeat.touch()
        write_json(directory / "request.json", task)
        deadline = time.monotonic() + task["timeout_seconds"]
        stop_code = None
        try:
            while True:
                heartbeat.touch()
                if (directory / "result.json").exists():
                    result = read_json(directory / "result.json")
                    if stop_code:
                        raise ProcessFailure(stop_code)
                    return result
                if stop_code is None and (cancel() or time.monotonic() >= deadline):
                    stop_code = "cancelled" if cancel() else "timed_out"
                    (directory / "cancel").touch()
                    deadline = time.monotonic() + 5
                if stop_code and time.monotonic() >= deadline:
                    raise ProcessFailure("interrupted")
                if age(self.directory / "ready.json") >= 10:
                    raise ProcessFailure("interrupted")
                time.sleep(0.1)
        finally:
            # Only remove this locally generated UUID directory, never user-supplied paths.
            if (directory / "result.json").exists():
                shutil.rmtree(directory)
            else:
                (directory / "cancel").touch()


def process_one(directory, config, runner):
    for task_dir in sorted(Path(directory).iterdir()):
        if not re.fullmatch(r"[a-f0-9]{32}", task_dir.name) or not task_dir.is_dir():
            continue
        if (task_dir / "result.json").exists() or not (task_dir / "request.json").exists():
            continue
        try:
            (task_dir / "claim").mkdir()
        except FileExistsError:
            # A claimed task from a previous daemon lifetime is never billed again.
            write_json(task_dir / "result.json", {"error_code": "interrupted"})
            return True
        try:
            task = read_json(task_dir / "request.json", 64000)
            if task.get("config") != config:
                raise ProcessFailure("model_not_configured")
            if not 1 <= task["timeout_seconds"] <= 180:
                raise ProcessFailure("invalid_output")

            def cancelled(task_dir=task_dir):
                return (task_dir / "cancel").exists() or age(task_dir / "heartbeat") > 3

            if cancelled():
                raise ProcessFailure("interrupted")
            result = runner.run(task, cancelled)
        except ProcessFailure as error:
            result = {"error_code": str(error)}
        except Exception:
            result = {"error_code": "invalid_output"}
        write_json(task_dir / "result.json", result)
        return True
    return False


def main():
    # Container-only entry point. flock prevents two daemons claiming separate tasks concurrently.
    import fcntl

    directory = Path(os.environ["QUANT_AGENT_EXCHANGE_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / "worker.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = {
        "provider": os.environ.get("QUANT_AGENT_PROVIDER", "mock"),
        "model": os.environ.get("QUANT_AGENT_MODEL", "mock-v1"),
        "endpoint": os.environ.get("QUANT_AGENT_ENDPOINT", ""),
    }
    key_file = os.environ.get("QUANT_AGENT_KEY_FILE")
    ready = config["provider"] == "mock" or bool(key_file and Path(key_file).is_file())
    stopping = Event()

    def heartbeat():
        while not stopping.wait(0.5):
            write_json(directory / "ready.json", {"config": config, "ready": ready})

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        runner = SubprocessWorker(key_file=key_file)
        while True:
            if not process_one(directory, config, runner):
                time.sleep(0.2)
            # Remove abandoned transport files after 24h. Reports are already archived by API.
            for item in directory.iterdir():
                if (
                    re.fullmatch(r"[a-f0-9]{32}", item.name)
                    and item.is_dir()
                    and age(item / "heartbeat") > 86400
                ):
                    shutil.rmtree(item)
    finally:
        stopping.set()
        thread.join(2)
        (directory / "ready.json").unlink(missing_ok=True)
        lock.close()


if __name__ == "__main__":
    main()
