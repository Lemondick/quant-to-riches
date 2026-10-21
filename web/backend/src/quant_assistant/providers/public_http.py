"""Bounded public HTTP transport. No redirects, credentials or response bodies in logs."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from datetime import UTC, datetime
from threading import BoundedSemaphore, RLock
from urllib.parse import urlsplit

import requests

from quant_assistant.providers.base import ProviderUnavailable

logger = logging.getLogger(__name__)
_SLOTS = BoundedSemaphore(4)


class PublicHttpError(ProviderUnavailable):
    def __init__(self, reason, http_status=None):
        super().__init__(reason)
        self.http_status = http_status


class PublicHttp:
    # Process-wide failure tracker (F3). The worker installs a persistent
    # PublicSourceFailureTracker here at startup; other hosts leave it None.
    _default_failure_tracker = None

    @classmethod
    def set_default_failure_tracker(cls, tracker):
        cls._default_failure_tracker = tracker

    def __init__(
        self,
        http_get=requests.get,
        *,
        sleep=time.sleep,
        clock=time.monotonic,
        now=None,
        failure_tracker=None,
    ):
        self._get, self._sleep, self._clock = http_get, sleep, clock
        self._states, self._lock = OrderedDict(), RLock()
        self._now = now or (lambda: datetime.now(UTC))
        self._failure_tracker = failure_tracker

    def _tracker(self):
        return (
            self._failure_tracker
            if self._failure_tracker is not None
            else PublicHttp._default_failure_tracker
        )

    def get(self, url, *, expected="data", module="public_market", **kwargs):
        host = urlsplit(url).hostname or "unknown"
        with self._lock:
            failures, until = self._states.get(host, (0, 0))
            if self._clock() < until:
                tracker = self._tracker()
                if tracker is not None:
                    tracker.record(host, ok=False, error="circuit_open")
                raise PublicHttpError("circuit_open")
        kwargs["allow_redirects"] = False
        kwargs["stream"] = True
        timeout = kwargs.get("timeout", 8)
        kwargs["timeout"] = (
            tuple(min(float(v), 12) for v in timeout)
            if isinstance(timeout, tuple)
            else min(float(timeout), 12)
        )
        error = None
        for attempt in range(2):
            started, status = self._clock(), None
            try:
                if not _SLOTS.acquire(timeout=2):
                    raise PublicHttpError("concurrency_limit")
                try:
                    response = self._get(url, **kwargs)
                    status = getattr(response, "status_code", 200)
                    if status != 200:
                        if isinstance(response, requests.Response):
                            response.close()
                        raise PublicHttpError(f"http_{status}")
                    if isinstance(response, requests.Response):
                        chunks, size = [], 0
                        try:
                            for chunk in response.iter_content(65536):
                                size += len(chunk)
                                if size > 8 * 1024 * 1024:
                                    raise PublicHttpError("oversized")
                                chunks.append(chunk)
                            response._content = b"".join(chunks)
                        finally:
                            response.close()
                finally:
                    _SLOTS.release()
                status = getattr(response, "status_code", 200)
                if status != 200:
                    raise PublicHttpError(f"http_{status}")
                body = getattr(response, "content", None)
                headers = getattr(response, "headers", {})
                content_type = str(headers.get("Content-Type", "")).lower()
                if body is not None:
                    if not body or len(body) > 8 * 1024 * 1024:
                        raise PublicHttpError("empty_or_oversized")
                    prefix = body.lstrip()[:200].lower()
                    if expected != "html" and (
                        prefix.startswith((b"<html", b"<!doctype"))
                        or expected != "text"
                        and "text/html" in content_type
                    ):
                        raise PublicHttpError("unexpected_html")
                with self._lock:
                    self._states[host] = (0, 0)
                    while len(self._states) > 64:
                        self._states.popitem(last=False)
                response.public_fetched_at = self._now()
                tracker = self._tracker()
                if tracker is not None:
                    tracker.record(host, ok=True)
                logger.info(
                    "public_http module=%s source=%s elapsed_ms=%.2f http_status=%s "
                    "fetched_at=%s cache=false transport=valid",
                    module,
                    host,
                    (self._clock() - started) * 1000,
                    status,
                    response.public_fetched_at.isoformat(),
                )
                return response
            except (requests.RequestException, OSError, PublicHttpError) as exc:
                error = str(exc) if isinstance(exc, PublicHttpError) else type(exc).__name__
                logger.warning(
                    "public_http module=%s source=%s elapsed_ms=%.2f http_status=%s "
                    "error_type=%s cache=false",
                    module,
                    host,
                    (self._clock() - started) * 1000,
                    status,
                    error,
                )
                transient = (
                    status is not None
                    and 500 <= status <= 599
                    or isinstance(exc, (requests.Timeout, requests.ConnectionError, OSError))
                )
                if not transient or attempt == 1:
                    break
                self._sleep(0.2 * (2**attempt))
        tracker = self._tracker()
        if tracker is not None:
            tracker.record(host, ok=False, error=error)
        with self._lock:
            failures = self._states.get(host, (0, 0))[0] + 1
            # Respect refusals immediately; never retry 403/429 inside a request.
            self._states[host] = (
                failures,
                self._clock() + 60 if failures >= 3 or status in (403, 429) else 0,
            )
            while len(self._states) > 64:
                self._states.popitem(last=False)
        raise PublicHttpError(error or "transport_unavailable", status)


def log_validation(
    module, source, *, count=0, source_time=None, fallback=False, reason=None, quality="valid"
):
    logger.info(
        "public_validation module=%s source=%s parse_ok=%s record_count=%s source_time=%s "
        "fallback=%s fallback_reason=%s quality_status=%s",
        module,
        source,
        quality not in {"invalid", "unavailable"},
        count,
        source_time,
        fallback,
        reason,
        quality,
    )
