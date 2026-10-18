"""Strict coercion primitives shared by feature services and portfolio gates.

These helpers validate untrusted input at service boundaries with stable,
field-specific error messages.  They intentionally do not guess or repair
values: anything unexpected fails loudly (fail-closed).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal

import pandas as pd


def text(value: object, name: str, *, maximum: int = 200) -> str:
    """Return a stripped non-empty string bounded by ``maximum`` characters."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise ValueError(f"{name} is too long")
    return result


def date_value(value: object, name: str) -> date:
    """Return a calendar date, rejecting ``datetime`` instances."""

    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{name} must be a date")
    return value


def decimal_value(value: object, name: str, *, positive: bool = False) -> Decimal:
    """Convert a scalar to a finite non-negative (or positive) ``Decimal``."""

    try:
        result = Decimal(str(value))
    except Exception as exc:  # Decimal raises several implementation-specific errors.
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'}")
    return result


def json_payload(value: object) -> str:
    """Serialize an audit payload deterministically (sorted keys, unicode-safe)."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def records(values: object) -> list[Mapping[str, object]]:
    """Coerce a dataframe, mapping, or sequence of mappings to a list of mappings."""

    if isinstance(values, pd.DataFrame):
        return [dict(item) for item in values.to_dict("records")]
    if isinstance(values, Mapping):
        return [values]
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        rows: list[Mapping[str, object]] = []
        for value in values:
            if not isinstance(value, Mapping):
                raise TypeError("rows must contain mappings")
            rows.append(value)
        return rows
    raise TypeError("rows must be a dataframe or sequence of mappings")


__all__ = ["date_value", "decimal_value", "json_payload", "records", "text"]
