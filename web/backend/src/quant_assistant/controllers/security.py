"""Small dependency-free security and metrics primitives for deployment."""

from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from urllib.parse import urlparse

from fastapi import Request


@dataclass(frozen=True, slots=True)
class ApiSecuritySettings:
    """Runtime policy for a single-user deployment.

    The token is intentionally supplied by the deployment layer and never included
    in logs or response bodies.  Health probes stay unauthenticated so a reverse
    proxy can determine whether the process is alive.
    """

    auth_token: str | None = None
    require_auth: bool = False
    rate_limit_per_minute: int = 120
    public_paths: frozenset[str] = frozenset({"/healthz", "/readyz"})
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.auth_token is not None and len(self.auth_token) < 32:
            raise ValueError("auth token must contain at least 32 characters")
        if self.require_auth and not self.auth_token:
            raise ValueError("auth token is required for this deployment")
        if isinstance(self.rate_limit_per_minute, bool) or self.rate_limit_per_minute <= 0:
            raise ValueError("rate_limit_per_minute must be positive")
        normalized: list[str] = []
        for origin in self.allowed_origins:
            if not isinstance(origin, str) or not origin.strip() or origin.strip() == "*":
                raise ValueError("CORS origins must be explicit HTTP(S) origins")
            value = origin.strip().rstrip("/")
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("CORS origins must be explicit HTTP(S) origins")
            if parsed.path or parsed.params or parsed.query or parsed.fragment:
                raise ValueError("CORS origins must not include paths or query strings")
            if value not in normalized:
                normalized.append(value)
        object.__setattr__(self, "allowed_origins", tuple(normalized))


class SlidingWindowRateLimiter:
    """Bounded in-memory request limiter suitable for one process."""

    def __init__(self, limit: int, *, window_seconds: float = 60.0) -> None:
        if isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        if not isinstance(key, str) or not key:
            raise ValueError("rate limiter key must be non-empty")
        current = time.monotonic() if now is None else float(now)
        cutoff = current - self._window_seconds
        with self._lock:
            entries = self._entries[key]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= self._limit:
                return False
            entries.append(current)
            if len(self._entries) > 2048:
                self._prune(cutoff)
            return True

    def _prune(self, cutoff: float) -> None:
        for key in tuple(self._entries):
            entries = self._entries[key]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if not entries:
                del self._entries[key]


@dataclass(slots=True)
class MetricsCollector:
    """Prometheus text-format counters without a runtime dependency."""

    _counts: dict[tuple[str, str], int] = field(default_factory=dict)
    _durations: dict[tuple[str, str], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, path: str, status_code: int, duration_seconds: float) -> None:
        key = (_label(path), str(int(status_code)))
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            self._durations[key] = self._durations.get(key, 0.0) + max(0.0, float(duration_seconds))

    def render(self) -> str:
        lines = [
            "# HELP quant_assistant_http_requests_total Total HTTP requests.",
            "# TYPE quant_assistant_http_requests_total counter",
            "# HELP quant_assistant_http_request_duration_seconds_sum Total request duration.",
            "# TYPE quant_assistant_http_request_duration_seconds_sum counter",
        ]
        with self._lock:
            rows = tuple(sorted(self._counts))
            for path, status in rows:
                labels = f'path="{path}",status="{status}"'
                lines.append(
                    "quant_assistant_http_requests_total"
                    f"{{{labels}}} {self._counts[(path, status)]}"
                )
                lines.append(
                    "quant_assistant_http_request_duration_seconds_sum"
                    f"{{{labels}}} {self._durations[(path, status)]:.9f}"
                )
        return "\n".join(lines) + "\n"


def request_client_key(request: Request) -> str:
    """Return a non-sensitive limiter key without trusting proxy headers."""

    client = request.client
    return client.host if client is not None and client.host else "unknown"


def valid_bearer(request: Request, expected: str | None) -> bool:
    if not expected:
        return True
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip(), expected)


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


__all__ = [
    "ApiSecuritySettings",
    "MetricsCollector",
    "SlidingWindowRateLimiter",
    "request_client_key",
    "valid_bearer",
]
