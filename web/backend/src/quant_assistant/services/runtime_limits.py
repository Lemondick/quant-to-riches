"""Process-wide public data cache and cap on model jobs across account contexts."""

import os
import time
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from threading import BoundedSemaphore, Condition, RLock


class ModelJobGate:
    def __init__(self, limit=None):
        self.limit = int(
            limit if limit is not None else os.environ.get("QUANT_MAX_MODEL_CONCURRENCY", "3")
        )
        if not 1 <= self.limit <= 30:
            raise ValueError("QUANT_MAX_MODEL_CONCURRENCY must be between 1 and 30")
        self.slots = BoundedSemaphore(self.limit)
        self.stopping = False
        self.condition = Condition()
        self.active = 0
        self.in_flight = 0

    @contextmanager
    def acquire(self, allowed, *, wait=False):
        acquired = False
        admitted = False
        with self.condition:
            self.in_flight += 1
        try:
            while not self.stopping and allowed():
                acquired = self.slots.acquire(timeout=0.5 if wait else 0)
                if acquired or not wait:
                    break
            with self.condition:
                admitted = acquired and not self.stopping and allowed()
                if admitted:
                    self.active += 1
            yield admitted
        finally:
            with self.condition:
                if admitted:
                    self.active -= 1
                self.in_flight -= 1
                self.condition.notify_all()
            if acquired:
                self.slots.release()

    def drain(self, timeout=None):
        with self.condition:
            return self.condition.wait_for(lambda: self.in_flight == 0, timeout)

    def status(self):
        with self.condition:
            return {
                "limit": self.limit,
                "active": self.active,
                "in_flight": self.in_flight,
                "stopping": self.stopping,
            }


class PublicReadCache:
    """Share only whitelisted anonymous provider reads, never user query services."""

    def __init__(self, provider, ttl=30):
        self.provider, self.ttl = provider, ttl
        self.lock = RLock()
        self.stripes = [RLock() for _ in range(64)]
        self.cache = {}

    def __getattr__(self, name):
        method = getattr(self.provider, name)
        if name not in {"estimate", "detail", "market_snapshot", "market_quotes", "market_news"}:
            return method

        def read(*args, **kwargs):
            key = repr((name, args, sorted(kwargs.items())))
            stripe = self.stripes[
                int.from_bytes(sha256(key.encode()).digest()[:2]) % len(self.stripes)
            ]
            with stripe:
                with self.lock:
                    saved = self.cache.get(key)
                    if saved and time.monotonic() - saved[0] < self.ttl:
                        return deepcopy(saved[1])
                result = method(*args, **kwargs)
                with self.lock:
                    if len(self.cache) >= 512:
                        del self.cache[next(iter(self.cache))]
                    self.cache[key] = (time.monotonic(), deepcopy(result))
                return result

        return read
