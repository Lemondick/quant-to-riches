"""Source clocks and small process-memory caches for anonymous public data.

Calendars come from the gateway or its shared legacy resource. Unknown market
years fail closed; fetch times never manufacture a source clock.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from threading import RLock
from zoneinfo import ZoneInfo

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.market_calendar import (
    ZONES,
    MarketCalendars,
    market_session,
    session_boundary,
)
from quant_assistant.providers.market_calendar import latest_session as _latest_session
from quant_assistant.providers.market_session_rules import unsupported_auction
from quant_assistant.providers.nav_freshness import nav_freshness, policy_for_fund

logger = logging.getLogger(__name__)
_INSTANT = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,7})?)?(?:Z|[+-]\d{2}:\d{2})"
)
_NO_ZONE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,7})?)?")
SOURCE_FAMILIES = {
    "eastmoney_fundgz": "eastmoney",
    "tiantian": "eastmoney",
    "akshare:eastmoney": "eastmoney",
    "akshare:eastmoney_latest_nav": "eastmoney",
}


def trading_day(day: date, calendar=None, market="CN") -> bool | None:
    return (calendar if calendar is not None else MarketCalendars.builtin()).trading_day(
        day, market
    )


def latest_session(
    now: datetime, calendar=None, market="CN", auction_supported=False
) -> date | None:
    return _latest_session(
        now,
        calendar if calendar is not None else MarketCalendars.builtin(),
        market,
        auction_supported,
    )


def metadata(
    data_type: str,
    source: str,
    source_value: str | None,
    fetched_at: datetime,
    *,
    instrument_type: str = "fund",
    fallback_used=False,
    fallback_reason=None,
    calendar: MarketCalendars | None = None,
    market="CN",
    auction_supported=False,
    as_of: datetime | None = None,
    nav_policy=None,
    fund_type=None,
    nav_publication_frequency=None,
    nav_valuation_weekday=None,
    nav_publication_lag_sessions=None,
) -> dict:
    fetched = (
        fetched_at.replace(tzinfo=SHANGHAI_TZ)
        if fetched_at.tzinfo is None
        else fetched_at.astimezone(SHANGHAI_TZ)
    )
    calendar = calendar if calendar is not None else MarketCalendars.builtin()
    market = market.strip().upper()
    zone_id = ZONES.get(market)
    now = (as_of or fetched).astimezone(ZoneInfo(zone_id or "UTC"))
    status, stamp, day, updated, reason = "unknown_time", None, None, None, None
    if source_value:
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_value):
                if data_type not in {"official_nav", "historical_quote", "fund_profile"}:
                    raise ValueError("date cannot identify an intraday observation")
                day = date.fromisoformat(source_value)
                updated = day.isoformat()
                status = "invalid" if day > now.date() else "valid"
            elif len(source_value) <= 64 and _INSTANT.fullmatch(source_value):
                if not source_value.endswith("Z"):
                    hours, minutes = int(source_value[-5:-3]), int(source_value[-2:])
                    if minutes >= 60 or hours > 14 or (hours == 14 and minutes):
                        raise ValueError("invalid timezone offset")
                parsed = datetime.fromisoformat(source_value)
                parsed = parsed.astimezone(ZoneInfo(zone_id or "UTC"))
                stamp, updated, day = source_value, source_value, parsed.date()
                status = "invalid" if parsed > now + timedelta(minutes=1) else "valid"
                if status == "valid" and data_type in {"realtime_quote", "intraday_estimate"}:
                    session = latest_session(now, calendar, market, auction_supported)
                    if session is None:
                        status, reason = "unknown_time", "calendar_or_session_unavailable"
                    elif (
                        not auction_supported
                        and day == now.date()
                        and unsupported_auction(now, parsed, market)
                    ):
                        status, reason = "unavailable", "auction_not_supported"
                    elif day < session:
                        status = "stale"
                    elif day > session:
                        status = "invalid"
                    else:
                        boundary = session_boundary(now, session, auction_supported, market)
                        tolerance = timedelta(minutes=15 if data_type == "intraday_estimate" else 2)
                        if boundary - parsed > tolerance:
                            status = "stale"
            elif len(source_value) <= 64 and _NO_ZONE.fullmatch(source_value):
                datetime.fromisoformat(source_value)  # Validate its wall-clock syntax only.
                status, reason = "unknown_time", "source_timezone_missing"
            else:
                raise ValueError("invalid source time")
        except (ValueError, TypeError):
            status, stamp, updated, day = "invalid", None, None, None
    if status == "invalid":
        stamp, updated, day, reason = None, None, None, "invalid_source_time"
    year = calendar.load(now.year, market)
    result = dict(
        instrument_type=instrument_type,
        data_type=data_type,
        source_family=SOURCE_FAMILIES.get(source, source.removeprefix("local:")),
        source_time=stamp,
        updated_at=updated,
        fetched_at=fetched,
        trade_date=day.isoformat() if day else None,
        is_estimate=data_type == "intraday_estimate",
        is_stale=status == "stale",
        fallback_used=bool(fallback_used),
        fallback_reason=fallback_reason,
        quality_status=status,
        market=market,
        market_timezone=zone_id,
        market_session=market_session(now, calendar, market),
        calendar_revision=year.revision if year else None,
        calendar_source=year.source if year else None,
        quality_reason=reason,
        auction_supported=bool(auction_supported),
    )
    if data_type == "official_nav" and day is not None and status != "invalid":
        result.update(
            nav_freshness(
                day,
                now,
                calendar,
                policy=nav_policy
                if nav_policy is not None
                else policy_for_fund(fund_type, nav_publication_frequency),
                weekday=nav_valuation_weekday,
                market=market,
                publication_lag=nav_publication_lag_sessions,
            )
        )
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservationMetadata:
    nav_publication_lag_sessions: int | None = None
    nav_freshness_policy: str | None = None
    nav_age_sessions: int | None = None
    nav_freshness_status: str | None = None
    nav_valuation_weekday: int | None = None
    nav_policy_basis: str | None = None
    auction_supported: bool = False
    market: str = "CN"
    market_timezone: str | None = None
    market_session: str = "unknown"
    calendar_revision: str | None = None
    calendar_source: str | None = None
    quality_reason: str | None = None
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


def enrich_payload(kind: str, data: dict, *, calendar=None, as_of=None) -> dict:
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
            calendar=calendar,
            market=row.get("market", "CN"),
            as_of=as_of or datetime.now(SHANGHAI_TZ),
            auction_supported=row.get("auction_supported") is True,
            nav_policy=row.get("nav_freshness_policy"),
            fund_type=row.get("fund_type"),
            nav_publication_frequency=row.get("nav_publication_frequency"),
            nav_valuation_weekday=row.get("nav_valuation_weekday"),
            nav_publication_lag_sessions=row.get("nav_publication_lag_sessions"),
        )
        info["fetched_at"] = info["fetched_at"].isoformat()
        if row.get("cache_fallback") is True and info["quality_status"] != "invalid":
            info["is_stale"] = True
            if info["quality_status"] == "valid":
                info.update(quality_status="stale", quality_reason="index_refresh_failed")
        row.update(info)

    if kind == "market.quotes":
        for row in data.get("items", []):
            source_time = row.get("source_time") or (
                row.get("observed_at") if row.get("trade_time_verified") else None
            )
            apply(
                row,
                "realtime_quote",
                source_time,
                row.get("instrument_type") or row.get("asset_type", "stock"),
            )
    if kind == "fund.valuations":
        for row in data.get("items", []):
            official = row.get("valuation_type") == "latest_nav"
            apply(
                row,
                "official_nav" if official else "intraday_estimate",
                row.get("nav_date")
                if official
                else row.get("source_time") or row.get("observed_at"),
            )
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
