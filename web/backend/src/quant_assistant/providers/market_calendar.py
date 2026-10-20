"""Immutable exchange calendars supplied by the gateway, with no database access."""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache
from importlib.resources import files
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from quant_assistant.providers.market_session_rules import at_minute, minute, periods

MARKETS = frozenset({"CN", "HK", "US", "JP"})
ZONES = {
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "US": "America/New_York",
    "JP": "Asia/Tokyo",
}


def market_session(now, calendars, market="CN"):
    zone = ZONES.get(market)
    if zone is None:
        return "unknown"
    local = now.astimezone(ZoneInfo(zone))
    trading = calendars.trading_day(local.date(), market)
    if trading is None:
        return "unknown"
    if not trading:
        return "closed"
    rows = periods(market, local.date())
    if rows is None:
        return "unknown"
    return next(phase for start, end, phase in rows if start <= minute(local.time()) < end)


def latest_session(now, calendars, market="CN", auction_supported=False):
    if market not in ZONES:
        return None
    now = now.astimezone(ZoneInfo(ZONES[market]))
    day = now.date()
    if periods(market, day) is None:
        return None
    for offset in range(371):
        trading = calendars.trading_day(day, market)
        rows = periods(market, day)
        if trading is None or rows is None:
            return None
        opening = min(
            start
            for start, _, phase in rows
            if phase == "continuous_trading" or auction_supported and phase == "auction"
        )
        if trading and (offset > 0 or minute(now.time()) >= opening):
            return day
        day -= timedelta(days=1)
    return None


def in_refresh_window(now, calendars, market="CN"):
    if market not in ZONES:
        return False
    local = now.astimezone(ZoneInfo(ZONES[market]))
    rows = periods(market, local.date())
    if rows is None or calendars.trading_day(local.date(), market) is not True:
        return False
    close = max(end for _, end, phase in rows if phase in {"continuous_trading", "auction"})
    return (
        market_session(now, calendars, market) in {"continuous_trading", "auction"}
        or close <= minute(local.time()) <= close + 5
    )


def session_boundary(now, session, auction_supported=False, market="CN"):
    rows = periods(market, session)
    if rows is None:
        raise ValueError("session schedule unavailable")
    close = max(end for _, end, phase in rows if phase in {"continuous_trading", "auction"})
    clock = minute(now.time())
    if session != now.date() or clock >= close:
        return at_minute(session, close, now.tzinfo)
    for start, end, phase in rows:
        if start <= clock < end:
            if phase == "continuous_trading" or phase == "auction" and auction_supported:
                return now
            return at_minute(session, start, now.tzinfo)
    return now


@dataclass(frozen=True, slots=True)
class CalendarYear:
    market: str
    year: int
    source: str
    revision: str
    holidays: frozenset[date]


@dataclass(frozen=True, slots=True)
class MarketCalendars:
    years: tuple[CalendarYear, ...] = ()

    @classmethod
    def from_payload(cls, value):
        if (
            not isinstance(value, dict)
            or type(value.get("format_version")) is not int
            or value.get("format_version") != 1
            or not isinstance(value.get("calendars"), list)
            or len(value["calendars"]) > 24
        ):
            return cls()
        result, duplicates = {}, set()
        for row in value["calendars"]:
            try:
                market, year = row["market"], row["year"]
                if market not in MARKETS or type(year) is not int or not 2000 <= year <= 2100:
                    continue
                key = (market, year)
                source, revision, raw = row["source"], row["revision"], row["holidays"]
                url = urlsplit(source)
                if (
                    not isinstance(source, str)
                    or url.scheme != "https"
                    or not url.hostname
                    or url.username is not None
                    or url.password is not None
                    or not isinstance(revision, str)
                    or not revision
                    or not isinstance(raw, list)
                    or len(raw) > 366
                ):
                    continue
                holidays = frozenset(date.fromisoformat(day) for day in raw)
                if any(day.year != year for day in holidays) or any(
                    not isinstance(day, str) or len(day) != 10 for day in raw
                ):
                    continue
                if key in result:
                    duplicates.add(key)
                result[key] = CalendarYear(market, year, source, revision, holidays)
            except (KeyError, TypeError, ValueError):
                continue
        return cls(tuple(result[key] for key in sorted(result) if key not in duplicates))

    @classmethod
    @cache
    def builtin(cls):
        # This resource is also embedded into the native gateway assembly.
        return cls.from_payload(
            json.loads(files(__package__).joinpath("exchange_calendars.json").read_text("utf-8"))
        )

    def load(self, year, market="CN"):
        return next((row for row in self.years if row.market == market and row.year == year), None)

    def trading_day(self, day: date, market="CN") -> bool | None:
        year = self.load(day.year, market)
        return None if year is None else day.weekday() < 5 and day not in year.holidays

    def to_payload(self):
        return {
            "format_version": 1,
            "calendars": [
                {
                    "market": row.market,
                    "year": row.year,
                    "source": row.source,
                    "revision": row.revision,
                    "holidays": [day.isoformat() for day in sorted(row.holidays)],
                }
                for row in self.years
            ],
        }
