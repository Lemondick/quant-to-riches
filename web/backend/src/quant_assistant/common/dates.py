"""Strict date/time coercion shared by persistence and API boundaries."""

from __future__ import annotations

from datetime import date, datetime
from typing import cast

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ


def optional_date(value: object) -> date | None:
    """Return a calendar date or ``None`` for missing/unparseable values."""

    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def required_date(value: object, label: str = "date") -> date:
    """Parse a required date and raise a stable, field-specific error."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = optional_date(value)
    if parsed is None:
        raise ValueError(f"{label} is invalid")
    return parsed


def required_datetime(value: object, label: str = "datetime") -> datetime:
    """Parse a timestamp and normalize it to the application's Shanghai zone."""

    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        raise ValueError(f"{label} is invalid")
    return cast(datetime, parsed.to_pydatetime()).astimezone(SHANGHAI_TZ)


def optional_timestamp(value: object) -> pd.Timestamp | None:
    """Parse an optional UTC timestamp for sorting and display."""

    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed


__all__ = ["optional_date", "optional_timestamp", "required_date", "required_datetime"]
