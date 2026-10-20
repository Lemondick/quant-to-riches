"""One packaged cash-session authority, also embedded by the native gateway."""

import json
from datetime import datetime, time
from functools import cache
from importlib.resources import files
from types import MappingProxyType


def minute(clock):
    return clock.hour * 60 + clock.minute + clock.second / 60 + clock.microsecond / 60000000


def _parse(rows):
    result, end = [], 0
    for start_text, end_text, phase in rows:
        start = minute(time.fromisoformat(start_text))
        stop = 1440 if end_text == "24:00" else minute(time.fromisoformat(end_text))
        if (
            start != end
            or stop <= start
            or phase not in {"pre_market", "auction", "continuous_trading", "lunch_break", "closed"}
        ):
            raise ValueError("invalid session intervals")
        result.append((start, stop, phase))
        end = stop
    if end != 1440:
        raise ValueError("incomplete session intervals")
    return tuple(result)


@cache
def _rules():
    data = json.loads(files(__package__).joinpath("exchange_sessions.json").read_text("utf-8"))
    if data["format_version"] != 1 or not data["revision"]:
        raise ValueError("unsupported session authority")
    rows = {}
    for row in data["markets"]:
        if row["market"] in rows or not row["source"].startswith("https://"):
            raise ValueError("invalid session authority")
        rows[row["market"]] = (
            frozenset(row["years"]),
            _parse(row["regular"]),
            _parse(row["half_day"]) if row["half_day"] else (),
            frozenset(row["half_days"]),
        )
    return MappingProxyType(rows)


def periods(market, day):
    row = _rules().get(market)
    if row is None or row[0] and day.year not in row[0]:
        return None
    return row[2] if day.isoformat() in row[3] else row[1]


def at_minute(day, value, zone):
    return datetime.combine(day, time(int(value) // 60, int(value) % 60), zone)


def unsupported_auction(now, source, market):
    rows = periods(market, now.date())
    if rows is None or source.date() != now.date():
        return False
    clock = minute(now.time())
    for index, (start, end, phase) in enumerate(rows):
        if phase != "auction":
            continue
        if index + 1 < len(rows) and rows[index + 1][2] == "pre_market":
            end = rows[index + 1][1]
        if start <= clock < end and minute(source.time()) >= start:
            return True
    return False
