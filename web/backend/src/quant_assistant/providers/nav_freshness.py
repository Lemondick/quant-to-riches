"""Configured publication tolerances; a source date alone never proves daily frequency."""

import json
from datetime import time, timedelta
from functools import cache
from importlib.resources import files
from zoneinfo import ZoneInfo

from quant_assistant.providers.market_calendar import ZONES, latest_session


@cache
def policies():
    return json.loads(
        files(__package__).joinpath("nav_freshness_policies.json").read_text("utf-8")
    )["policies"]


def policy_for_fund(fund_type=None, frequency=None):
    kind = fund_type.strip().upper() if isinstance(fund_type, str) else ""
    if frequency == "weekly":
        return "weekly"
    if kind.startswith("QDII"):
        return "qdii_t3"
    if frequency == "daily" or kind.startswith(("股票型", "混合型", "债券型")):
        return "daily_t1"
    # Currency funds, FOFs and a generic index label do not prove publication frequency.
    return "unknown"


def nav_freshness(
    day, now, calendar, *, policy="unknown", weekday=None, market="CN", publication_lag=None
):
    now = now.astimezone(ZoneInfo(ZONES.get(market, "UTC")))
    result = {
        "nav_freshness_policy": policy,
        "nav_age_sessions": None,
        "nav_freshness_status": "unknown",
        "nav_valuation_weekday": weekday,
        "nav_publication_lag_sessions": publication_lag,
        "nav_policy_basis": "application_config",
        "quality_status": "unknown_time",
        "quality_reason": "nav_publication_policy_unknown",
        "is_stale": day < now.date(),
    }
    config = policies().get(policy)
    if config is None:
        return result
    # Only the verified CN schedule currently defines completed sessions.
    if market != "CN":
        result["quality_reason"] = "calendar_or_session_unavailable"
        return result
    cutoff = (
        now if now.time() >= time(15) else now.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    completed = latest_session(cutoff, calendar, market)
    if completed is None:
        result["quality_reason"] = "calendar_or_session_unavailable"
        return result
    if policy == "weekly" and (type(weekday) is not int or not 0 <= weekday <= 6):
        result["quality_reason"] = "nav_weekly_schedule_unknown"
        return result
    count, current = 0, day
    while current < completed:
        current += timedelta(days=1)
        trading = calendar.trading_day(current, market)
        if trading is None:
            result["quality_reason"] = "calendar_or_session_unavailable"
            return result
        count += int(trading)
    result["nav_age_sessions"] = count
    if policy == "weekly":
        lag = config.get("publication_lag_sessions") if publication_lag is None else publication_lag
        if type(lag) is not int or not 0 <= lag <= 10:
            result["quality_reason"] = "nav_weekly_schedule_unknown"
            return result
        result["nav_publication_lag_sessions"] = lag
        due = completed
        while lag:
            due -= timedelta(days=1)
            trading = calendar.trading_day(due, market)
            if trading is None:
                result["quality_reason"] = "calendar_or_session_unavailable"
                return result
            lag -= int(trading)
        expected = due - timedelta(days=(due.weekday() - weekday) % 7)
        for _ in range(370):
            trading = calendar.trading_day(expected, market)
            if trading is None:
                result["quality_reason"] = "calendar_or_session_unavailable"
                return result
            if trading:
                break
            expected -= timedelta(days=1)
        else:
            return result
        stale = day < expected
    else:
        maximum = config.get("max_age_sessions")
        if type(maximum) is not int or not 0 <= maximum <= 30:
            return result
        stale = count > maximum
    result.update(
        nav_freshness_status="stale" if stale else "fresh",
        quality_status="stale" if stale else "valid",
        quality_reason="nav_publication_overdue" if stale else None,
        is_stale=stale,
    )
    return result
