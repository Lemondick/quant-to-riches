"""Persistent consecutive-failure counting for public data sources (F3).

``PublicHttp`` (the single choke point for public fetches) reports each
terminal per-host outcome here. Counts survive worker restarts via a small
JSON state file so stream A can raise a critical alert when a source fails
``QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS`` (default 3) consecutive times.

State file (``<QUANT_SITE_ROOT>/.public-source-failures.json``), for A's
alert consumer::

    {"version": 1,
     "sources": {"<host>": {"consecutive_failures": 2,
                            "last_failure_at": "2026-10-21T12:00:00+00:00",
                            "last_error": "http_500",
                            "alerting": false}}}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

_logger = logging.getLogger(__name__)

_STATE_VERSION = 1
_MAX_STATE_BYTES = 65536


def alert_rounds_from_env(environment=None):
    """Read ``QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS`` (default 3, minimum 1)."""
    raw = (environment or os.environ).get("QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS", "3")
    try:
        rounds = int(raw)
    except (TypeError, ValueError):
        _logger.warning("invalid QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS=%r; using 3", raw)
        return 3
    return max(1, rounds)


class PublicSourceFailureTracker:
    """Thread-safe persistent consecutive-failure counter per public source."""

    def __init__(self, state_path, alert_rounds=3):
        self.state_path = Path(state_path)
        self.alert_rounds = max(1, int(alert_rounds))
        self._lock = RLock()
        self._sources = self._read()

    # -- persistence ----------------------------------------------------

    def _read(self):
        path = self.state_path
        if not path.exists():
            return {}
        if path.is_symlink() or path.stat().st_size > _MAX_STATE_BYTES:
            raise ValueError("Invalid public source failure state file")
        state = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(state, dict)
            or state.get("version") != _STATE_VERSION
            or not isinstance(state.get("sources"), dict)
        ):
            raise ValueError("Invalid public source failure state file")
        sources = {}
        for host, entry in state["sources"].items():
            if not isinstance(entry, dict):
                continue
            try:
                sources[str(host)] = {
                    "consecutive_failures": max(0, int(entry.get("consecutive_failures", 0))),
                    "last_failure_at": entry.get("last_failure_at"),
                    "last_error": entry.get("last_error"),
                }
            except (TypeError, ValueError):
                continue
        return sources

    def _persist(self):
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        target = path.with_suffix(".tmp")
        if target.is_symlink() or path.is_symlink():
            raise ValueError("Invalid public source failure state path")
        payload = {
            "version": _STATE_VERSION,
            "sources": {
                host: {**entry, "alerting": entry["consecutive_failures"] >= self.alert_rounds}
                for host, entry in sorted(self._sources.items())
            },
        }
        with target.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            target.chmod(0o600)
        target.replace(path)

    # -- recording ------------------------------------------------------

    def record(self, source, *, ok, error=None, at=None):
        """Record a terminal fetch outcome for ``source`` (host).

        Returns True when this record newly trips the alert threshold
        (consecutive failures reached ``alert_rounds``); False otherwise.
        Success resets the counter.
        """
        host = str(source or "unknown")
        with self._lock:
            entry = self._sources.get(host)
            if ok:
                if not entry or entry["consecutive_failures"] == 0:
                    return False  # steady state: no write
                _logger.info(
                    "public source recovered",
                    extra={"source": host, "previous_failures": entry["consecutive_failures"]},
                )
                self._sources[host] = {
                    "consecutive_failures": 0,
                    "last_failure_at": entry["last_failure_at"],
                    "last_error": entry["last_error"],
                }
                self._persist()
                return False
            failures = (entry["consecutive_failures"] if entry else 0) + 1
            self._sources[host] = {
                "consecutive_failures": failures,
                "last_failure_at": (at or datetime.now(UTC)).isoformat(),
                "last_error": str(error)[:200] if error else None,
            }
            self._persist()
            tripped = failures == self.alert_rounds
            if tripped:
                _logger.warning(
                    "public source consecutive failures reached alert threshold",
                    extra={
                        "source": host,
                        "consecutive_failures": failures,
                        "alert_rounds": self.alert_rounds,
                        "last_error": self._sources[host]["last_error"],
                    },
                )
            return tripped

    # -- consumption ----------------------------------------------------

    def alerting_sources(self):
        """Hosts currently at/above the alert threshold, for A's consumer."""
        with self._lock:
            return {
                host: {**entry, "alerting": True}
                for host, entry in self._sources.items()
                if entry["consecutive_failures"] >= self.alert_rounds
            }

    def snapshot(self):
        """Full counter state (for diagnostics / A's consumer)."""
        with self._lock:
            return {
                host: {
                    **entry,
                    "alerting": entry["consecutive_failures"] >= self.alert_rounds,
                }
                for host, entry in sorted(self._sources.items())
            }
