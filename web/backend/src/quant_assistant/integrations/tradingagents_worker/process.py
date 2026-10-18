"""Owned process trees with fail-closed Windows Job Objects and POSIX groups."""

import ctypes
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path


class ProcessFailure(Exception):
    pass


class ProcessTree:
    def __init__(self, process):
        self.process, self.job = process, None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("minimum_ws", ctypes.c_size_t),
                ("maximum_ws", ctypes.c_size_t),
                ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.job = self.kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (
            not self.job
            or not self.kernel.SetInformationJobObject(
                self.job, 9, ctypes.byref(info), ctypes.sizeof(info)
            )
            or not self.kernel.AssignProcessToJobObject(self.job, int(process._handle))
        ):
            self.close()
            raise ProcessFailure("worker_unavailable")

    def resume(self):
        """Resume the suspended Windows primary thread only after Job ownership."""

        if os.name != "nt":
            return
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("usage", wintypes.DWORD),
                ("thread_id", wintypes.DWORD),
                ("process_id", wintypes.DWORD),
                ("base_priority", wintypes.LONG),
                ("delta_priority", wintypes.LONG),
                ("flags", wintypes.DWORD),
            ]

        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenThread.restype = wintypes.HANDLE
        self.kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == wintypes.HANDLE(-1).value:
            raise ProcessFailure("worker_unavailable")
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.process_id == self.process.pid:
                    thread = self.kernel.OpenThread(0x2, False, entry.thread_id)
                    if not thread:
                        raise ProcessFailure("worker_unavailable")
                    try:
                        if self.kernel.ResumeThread(thread) != 1:
                            raise ProcessFailure("worker_unavailable")
                    finally:
                        self.kernel.CloseHandle(thread)
                    return
                entry.size = ctypes.sizeof(entry)
                found = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise ProcessFailure("worker_unavailable")
        finally:
            self.kernel.CloseHandle(snapshot)

    def close(self):
        if self.job:
            from ctypes import wintypes

            class Accounting(ctypes.Structure):
                _fields_ = [
                    ("user", ctypes.c_longlong),
                    ("kernel", ctypes.c_longlong),
                    ("period_user", ctypes.c_longlong),
                    ("period_kernel", ctypes.c_longlong),
                    ("page_faults", wintypes.DWORD),
                    ("total", wintypes.DWORD),
                    ("active", wintypes.DWORD),
                    ("terminated", wintypes.DWORD),
                ]

            # A venv launcher can exit before its Python child releases stdout.
            # Keep the job handle until every owned process has stopped.
            handles = []
            try:
                self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                self.kernel.OpenProcess.restype = wintypes.HANDLE
                self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                self.kernel.WaitForSingleObject.restype = wintypes.DWORD
                capacity = 16
                while True:

                    class ProcessIds(ctypes.Structure):
                        _fields_ = [
                            ("assigned", wintypes.DWORD),
                            ("count", wintypes.DWORD),
                            ("ids", ctypes.c_size_t * capacity),
                        ]

                    members = ProcessIds()
                    queried = self.kernel.QueryInformationJobObject(
                        self.job, 3, ctypes.byref(members), ctypes.sizeof(members), None
                    )
                    if queried and members.count == members.assigned:
                        break
                    if not queried and ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
                        raise ProcessFailure("worker_unavailable")
                    capacity = max(capacity * 2, members.assigned)
                for pid in members.ids[: members.count]:
                    handle = self.kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
                    if handle:
                        handles.append(handle)
                    elif ctypes.get_last_error() != 87:  # Already exited: ERROR_INVALID_PARAMETER
                        raise ProcessFailure("worker_unavailable")
                if not self.kernel.TerminateJobObject(self.job, 1):
                    raise ProcessFailure("worker_unavailable")
                deadline = time.monotonic() + 5
                while True:
                    info = Accounting()
                    if not self.kernel.QueryInformationJobObject(
                        self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None
                    ):
                        raise ProcessFailure("worker_unavailable")
                    if info.active == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise ProcessFailure("worker_unavailable")
                    time.sleep(0.01)
                # The accounting counter can reach zero while kernel exit cleanup is pending.
                for handle in handles:
                    remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                    if self.kernel.WaitForSingleObject(handle, remaining_ms) != 0:
                        raise ProcessFailure("worker_unavailable")
            finally:
                for handle in handles:
                    self.kernel.CloseHandle(handle)
                self.kernel.CloseHandle(self.job)
                self.job = None
        elif os.name != "nt":
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()


class SubprocessWorker:
    def __init__(self, python_executable="", key_file=None):
        self.python = python_executable or sys.executable
        self.key_file = key_file

    def run(self, task, cancel):
        body = json.dumps(task, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(body) > 64000:
            raise ProcessFailure("invalid_output")
        environment = {
            key: os.environ[key]
            for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }
        environment["QUANT_AGENT_PARENT_PID"] = str(os.getpid())
        if self.key_file:
            environment["QUANT_AGENT_KEY_FILE"] = str(Path(self.key_file).resolve())
        with tempfile.TemporaryDirectory(prefix="quant-research-") as directory:
            output_path = Path(directory) / "result.json"
            with output_path.open("wb") as output:
                process = subprocess.Popen(
                    [self.python, "-I", str(Path(__file__).with_name("main.py"))],
                    cwd=directory,
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
                tree = ProcessTree(process)
                deadline = time.monotonic() + task["timeout_seconds"]
                try:
                    # CREATE_SUSPENDED prevents venv launchers spawning before Job assignment.
                    tree.resume()
                    process.stdin.write(body)
                    process.stdin.close()
                    while process.poll() is None:
                        if cancel():
                            raise ProcessFailure("cancelled")
                        if time.monotonic() >= deadline:
                            raise ProcessFailure("timed_out")
                        time.sleep(0.05)
                    if process.returncode:
                        raise ProcessFailure("model_error")
                finally:
                    tree.close()
            if output_path.stat().st_size > 128000:
                raise ProcessFailure("invalid_output")
            try:
                return json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ProcessFailure("invalid_output") from exc
