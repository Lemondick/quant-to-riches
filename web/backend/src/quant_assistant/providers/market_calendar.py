"""Immutable exchange calendars supplied by the gateway, with no database access."""

import json
from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib.resources import files
from urllib.parse import urlsplit

MARKETS = frozenset({"CN", "HK", "US", "JP"})


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
