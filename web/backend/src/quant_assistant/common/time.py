"""Timezone and clock primitives shared by domain, services, and adapters."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now_from_clock(clock: object | None = None) -> datetime:
    """Normalize an injected clock to a timezone-aware Shanghai ``datetime``.

    ``clock`` may be a callable returning a ``datetime``, an object exposing
    ``now()`` (test doubles), or ``None`` for the wall clock.  Anything that
    does not produce a timezone-aware ``datetime`` fails loudly so callers
    never persist naive or ambiguous timestamps.
    """

    clock_any: Any = clock
    result: object
    if clock is not None and hasattr(clock, "now") and callable(clock_any.now):
        result = cast(Callable[[], datetime], clock_any.now)()
    elif clock is not None and callable(clock):
        result = cast(Callable[[], datetime], clock)()
    else:
        result = clock
    if result is None:
        result = datetime.now(SHANGHAI_TZ)
    if not isinstance(result, datetime):
        raise TypeError("clock must return datetime")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return result.astimezone(SHANGHAI_TZ)


__all__ = ["SHANGHAI_TZ", "now_from_clock"]
