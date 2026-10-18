"""Cross-platform exclusion shared with the ASP.NET Core site host."""

import ctypes
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def stopped_site(root: Path):
    """Refuse a running site instead of archiving a moving financial ledger."""
    path = root / ".site.lock"
    if path.is_symlink():
        raise ValueError("Site lock must not be a symbolic link")
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ValueError("Stop the API and workers before this operation")
        try:
            yield
        finally:
            kernel.CloseHandle(handle)
    else:
        import fcntl

        with path.open("a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("Stop the API and workers before this operation") from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
