"""Unified worker-side cache: TTL + capacity bound + explicit invalidation.

Cross-stream contract (section 4):
- cache keys are strings ``"{namespace}:{version}:{sha256(payload)}"`` with an
  explicit version so cached shapes can be retired deliberately;
- ``functools.lru_cache`` is reserved for small, bounded key spaces; anything
  with a TTL goes through this abstraction;
- public API: :func:`cache_get`, :func:`cache_set`, :func:`cache_invalidate`.

The cache is process-local and thread-safe (the worker serves resident jobs
from a thread pool). It never persists across restarts.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def cache_key(namespace, version, payload):
    """Build a contract-compliant string key ``"{namespace}:{version}:{sha256}"``."""
    if not namespace or ":" in namespace:
        raise ValueError("cache namespace must be a non-empty string without ':'")
    digest = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()
    return f"{namespace}:{version}:{digest}"


class WorkerCache:
    """Bounded LRU cache with per-entry TTL and namespace invalidation."""

    def __init__(self, *, max_entries=128, default_ttl_s=3600):
        if max_entries < 1:
            raise ValueError("cache max_entries must be >= 1")
        if default_ttl_s <= 0:
            raise ValueError("cache default_ttl_s must be > 0")
        self._max_entries = max_entries
        self._default_ttl_s = default_ttl_s
        self._lock = threading.RLock()
        self._entries = OrderedDict()  # key -> (expires_at_monotonic, value)

    def get(self, key):
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key, value, ttl_s=None):
        ttl = self._default_ttl_s if ttl_s is None else ttl_s
        if ttl <= 0:
            raise ValueError("cache ttl_s must be > 0")
        with self._lock:
            self._entries[key] = (time.monotonic() + ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, namespace):
        """Drop every entry whose key belongs to ``namespace``."""
        prefix = f"{namespace}:"
        with self._lock:
            for key in [key for key in self._entries if key.startswith(prefix)]:
                del self._entries[key]

    def clear(self):
        with self._lock:
            self._entries.clear()

    def __len__(self):
        with self._lock:
            return len(self._entries)


_default_cache = WorkerCache(
    max_entries=_env_int("QUANT_WORKER_CACHE_MAX_ENTRIES", 128),
    default_ttl_s=_env_int("QUANT_WORKER_CACHE_TTL_S", 3600),
)


def cache_get(key):
    return _default_cache.get(key)


def cache_set(key, value, ttl_s=None):
    _default_cache.set(key, value, ttl_s=ttl_s)


def cache_invalidate(namespace):
    _default_cache.invalidate(namespace)
