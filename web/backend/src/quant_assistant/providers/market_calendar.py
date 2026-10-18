"""Immutable exchange calendars supplied by the gateway, with no database access."""

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache
from importlib.resources import files
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

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
    if market != "CN":
        return "unknown"
    clock = local.time()
    if clock < time(9, 15):
        return "pre_market"
    if clock < time(9, 25):
        return "auction"
    if clock < time(9, 30):
        return "pre_market"
    if clock < time(11, 30):
        return "continuous_trading"
    if clock < time(13):
        return "lunch_break"
    if clock < time(14, 57):
        return "continuous_trading"
    return "auction" if clock < time(15) else "closed"


def latest_session(now, calendars, market="CN", auction_supported=False):
    if market != "CN":
        return None
    now = now.astimezone(ZoneInfo(ZONES[market]))
    day = now.date()
    trading = calendars.trading_day(day, market)
    if trading is None:
        return None
    if trading and now.time() >= (time(9, 15) if auction_supported else time(9, 30)):
        return day
    for _ in range(370):
        day -= timedelta(days=1)
        previous = calendars.trading_day(day, market)
        if previous is None:
            return None
        if previous:
            return day
    return None


def session_boundary(now, session, auction_supported=False):
    if session != now.date() or now.time() >= time(15):
        return datetime.combine(session, time(15), now.tzinfo)
    if auction_supported and now.time() < time(9, 30):
        return min(now, datetime.combine(session, time(9, 25), now.tzinfo))
    if time(11, 30) <= now.time() < time(13):
        return datetime.combine(session, time(11, 30), now.tzinfo)
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
