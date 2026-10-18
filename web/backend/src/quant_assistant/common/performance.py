"""Redacted timing context shared with public-provider thread-pool tasks."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)
_current: ContextVar[Trace | None] = ContextVar("quant_trace", default=None)


class Trace:
    def __init__(self, trace_id: str | None) -> None:
        self.trace_id = (
            trace_id if re.fullmatch(r"[0-9a-fA-F]{32}", trace_id or "") else uuid4().hex
        )
        self.timings: dict[str, float] = {}
        self.lock = Lock()

    def record(self, stage: str, milliseconds: float) -> None:
        with self.lock:
            self.timings[stage] = self.timings.get(stage, 0.0) + milliseconds


@contextmanager
def trace_scope(trace_id: str | None = None) -> Iterator[Trace]:
    trace = Trace(trace_id)
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)


@contextmanager
def measure(stage: str) -> Iterator[None]:
    trace, started = _current.get(), time.perf_counter()
    try:
        yield
    finally:
        if trace is not None:
            trace.record(stage, (time.perf_counter() - started) * 1000)


def traced_get(*args: Any, **kwargs: Any) -> Any:
    import requests

    with measure("external_io"):
        return requests.get(*args, **kwargs)


def log_slow(trace: Trace, kind: str, job_id: str) -> None:
    if trace.timings.get("worker", 0) > 500:
        logger.warning(
            "slow_worker trace_id=%s kind=%s job_id=%s timings=%s",
            trace.trace_id,
            kind,
            job_id,
            {name: round(value, 3) for name, value in trace.timings.items()},
        )
