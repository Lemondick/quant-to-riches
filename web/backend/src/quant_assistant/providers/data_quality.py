"""Source clocks and small process-memory caches for anonymous public data.

The 2026 closures mirror the existing gateway SSE calendar fallback. Unknown
calendar years fail closed; fetch times never manufacture a source clock.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as day_time
from hashlib import sha256
from threading import RLock

from quant_assistant.common.time import SHANGHAI_TZ

logger = logging.getLogger(__name__)
_CLOSURES_2026 = (
    ("01-01", "01-03"),
    ("02-15", "02-23"),
    ("04-04", "04-06"),
    ("05-01", "05-05"),
    ("06-19", "06-21"),
    ("09-25", "09-27"),
    ("10-01", "10-07"),
)
SOURCE_FAMILIES = {
    "eastmoney_fundgz": "eastmoney",
    "tiantian": "eastmoney",
    "akshare:eastmoney": "eastmoney",
    "akshare:eastmoney_latest_nav": "eastmoney",
}


def trading_day(day: date) -> bool | None:
    if day.year != 2026:
        return None
    text = day.strftime("%m-%d")
    return day.weekday() < 5 and not any(a <= text <= b for a, b in _CLOSURES_2026)


def latest_session(now: datetime) -> date | None:
    day = now.date()
    if trading_day(day) is None:
        return None
    if now.time() < day_time(9, 30):
        day -= timedelta(days=1)
    for _ in range(40):
        if trading_day(day):
            return day
        day -= timedelta(days=1)
    return None


def metadata(
    data_type: str,
    source: str,
    source_value: str | None,
    fetched_at: datetime,
    *,
    instrument_type: str = "fund",
    fallback_used=False,
    fallback_reason=None,
) -> dict:
    now = (
        fetched_at.replace(tzinfo=SHANGHAI_TZ)
        if fetched_at.tzinfo is None
        else fetched_at.astimezone(SHANGHAI_TZ)
    )
    status, stamp, day, updated = "unknown_time", None, None, None
    if source_value:
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_value):
                if data_type not in {"official_nav", "historical_quote", "fund_profile"}:
                    raise ValueError("date cannot identify an intraday observation")
                day = date.fromisoformat(source_value)
                updated = day.isoformat()
                status = "invalid" if day > now.date() else "valid"
            else:
                parsed = datetime.fromisoformat(source_value)
                if parsed.tzinfo is None:
                    raise ValueError("source clock requires a timezone")
                parsed = parsed.astimezone(SHANGHAI_TZ)
                stamp, updated, day = source_value, source_value, parsed.date()
                status = "invalid" if parsed > now + timedelta(minutes=1) else "valid"
                if status == "valid" and data_type in {"realtime_quote", "intraday_estimate"}:
                    session = latest_session(now)
                    if session is None:
                        status = "unknown_time"
                    elif day < session:
                        status = "stale"
                    elif day > session:
                        status = "invalid"
                    elif trading_day(now.date()) and now.time() >= day_time(9, 30):
                        # Lunch and after close use the most recent session boundary.
                        boundary = min(now, datetime.combine(now.date(), day_time(15), SHANGHAI_TZ))
                        if day_time(11, 30) <= now.time() < day_time(13):
                            boundary = datetime.combine(now.date(), day_time(11, 30), SHANGHAI_TZ)
                        tolerance = timedelta(minutes=15 if data_type == "intraday_estimate" else 2)
                        if boundary - parsed > tolerance:
                            status = "stale"
            if status == "valid" and data_type == "official_nav" and (now.date() - day).days > 14:
                status = "stale"
        except (ValueError, TypeError):
            status, stamp, updated, day = "invalid", None, None, None
    if status == "invalid":
        stamp, updated = None, None
    return dict(
        instrument_type=instrument_type,
        data_type=data_type,
        source_family=SOURCE_FAMILIES.get(source, source.removeprefix("local:")),
        source_time=stamp,
        updated_at=updated,
        fetched_at=now,
        trade_date=day.isoformat() if day else None,
        is_estimate=data_type == "intraday_estimate",
        is_stale=status == "stale",
        fallback_used=bool(fallback_used),
        fallback_reason=fallback_reason,
        quality_status=status,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservationMetadata:
    instrument_type: str | None = None
    data_type: str | None = None
    source_family: str | None = None
    source_time: str | None = None
    updated_at: str | None = None
    fetched_at: datetime | None = None
    trade_date: str | None = None
    is_estimate: bool = False
    is_stale: bool = False
    fallback_used: bool = False
    fallback_reason: str | None = None
    quality_status: str = "unknown_time"


class MemoryCache:
    """Bounded LRU, single-flight per key; failed reload retains original value."""

    def __init__(self, capacity=64, clock=time.monotonic):
        self.capacity, self.clock = capacity, clock
        self._values, self._lock = OrderedDict(), RLock()
        self._stripes = [RLock() for _ in range(32)]

    def __len__(self):
        with self._lock:
            return len(self._values)

    def read(self, key, loader, *, ttl, stale_on_error=True):
        stripe = self._stripes[int.from_bytes(sha256(str(key).encode()).digest()[:2]) % 32]
        with stripe:
            with self._lock:
                saved = self._values.get(key)
                if saved and self.clock() < saved[0]:
                    self._values.move_to_end(key)
                    logger.info("public_cache hit=true key_family=%s", str(key).split(":")[0])
                    return saved[1], False
            try:
                value = loader()
            except Exception:
                if saved and stale_on_error:
                    logger.warning(
                        "public_cache hit=true stale=true key_family=%s "
                        "fallback_reason=reload_failed",
                        str(key).split(":")[0],
                    )
                    return saved[1], True
                raise
            with self._lock:
                lifetime = ttl(value) if callable(ttl) else ttl
                self._values[key] = (self.clock() + lifetime, value)
                self._values.move_to_end(key)
                while len(self._values) > self.capacity:
                    self._values.popitem(last=False)
            return value, False

    def expire(self):
        with self._lock:
            self._values = OrderedDict(
                (k, (float("-inf"), v)) for k, (_, v) in self._values.items()
            )


def enrich_payload(kind: str, data: dict) -> dict:
    """Add metadata after DTO serialization; collection clocks stay separate."""

    def apply(row, data_type, source_value, instrument="fund", fetched=None):
        captured = row.get("fetched_at") or fetched
        if not captured:
            # Unknown fetch time is not reconstructed from a cache read.
            row["updated_at"] = None
            row["quality_status"] = "unknown_time"
            return
        stamp = datetime.fromisoformat(captured) if isinstance(captured, str) else captured
        info = metadata(
            data_type,
            row.get("source", "unknown"),
            source_value,
            stamp,
            instrument_type=instrument,
            fallback_used=row.get("fallback_used", False),
            fallback_reason=row.get("fallback_reason"),
        )
        info["fetched_at"] = info["fetched_at"].isoformat()
        row.update(info)

    if kind == "market.snapshot":
        for row in data.get("indices", []):
            apply(row, "realtime_quote", row.get("source_time"), "index")
        for row in data.get("fund_rankings", []):
            apply(row, "official_nav", row.get("nav_date"))
        for row in data.get("sector_rankings", []):
            row["standard_id"] = ":".join([row["source"], row["category"], row["code"]])
            apply(row, "realtime_quote", row.get("observed_at"), "sector")
            if row.get("change_rate") is None:
                row["quality_status"] = "unavailable"
        for row in data.get("sector_catalogs", []):
            apply(row, "realtime_quote", row.get("observed_at"), "sector")
            if row.get("stale"):
                row["is_stale"] = True
                row["quality_status"] = (
                    "unavailable" if row.get("unquoted") == row.get("received") else "stale"
                )
        if isinstance(data.get("breadth"), dict):
            row = data["breadth"]
            # Breadth explicitly dates its published daily statistic.
            apply(
                row,
                "historical_quote",
                row.get("observed_at", "")[:10] or None,
                "market_breadth",
                data.get("observed_at"),
            )
    if kind == "fund.detail":
        history = data.get("nav_history", [])
        data["nav_updated_at"] = history[-1]["nav_date"] if history else None
    return data
