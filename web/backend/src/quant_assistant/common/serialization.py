"""Deterministic, secret-safe serialization primitives.

All persisted fingerprints and audit JSON should pass through this module.  The
normalizer intentionally only accepts JSON-like values (plus the small set of
standard-library/scalar types used by the domain models) so unsupported values
fail loudly instead of being stringified inconsistently by individual callers.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd


def decimal_coerce(
    value: object,
    field_name: str = "value",
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    """Convert a scalar to a finite :class:`Decimal` with consistent guards."""

    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be Decimal-compatible")
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid Decimal") from exc
    if not converted.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if positive and converted <= 0:
        raise ValueError(f"{field_name} must be positive")
    if non_negative and converted < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return converted


def flag(value: object, *, default: bool | None = False) -> bool | None:
    """Parse common provider boolean representations without guessing."""

    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "是", "停牌", "暂停", "st", "*st"}:
        return True
    if normalized in {"0", "false", "no", "n", "否", "正常"}:
        return False
    return default


def enum_text(value: object, default: str | None = None) -> str:
    """Return an enum's value or its textual representation."""

    if value is None and default is not None:
        return default
    return str(getattr(value, "value", value))


def json_default(value: object) -> object:
    """Encode domain values used in audit payloads.

    This function is deliberately suitable for ``json.dumps(default=...)``;
    canonicalization itself happens in :func:`canonical_json` first.
    """

    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: getattr(value, item.name)
            for item in fields(value)
            if not item.metadata.get("secret", False)
        }
    if isinstance(value, (date, datetime, Decimal, Path)):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def _canonical_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        # fields() keeps the declared dataclass order before the JSON sort pass,
        # matching the previous asdict-based representation.
        return {
            item.name: _canonical_value(getattr(value, item.name))
            for item in fields(value)
            if not item.metadata.get("secret", False)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_value(item) for item in value]
        return sorted(items, key=_canonical_json_from_value)
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, (date, datetime, Decimal, Path)):
        # Keep the historic str(Decimal) representation used by the backtest
        # and decision fingerprints; changing exponent formatting would alter
        # existing run IDs.
        return str(value)
    if hasattr(value, "item"):
        return _canonical_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        # Let json.dumps(allow_nan=False) raise for canonical payloads rather
        # than silently converting a floating NaN/inf into JSON null.
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _canonical_json_from_value(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=json_default,
    )


def canonical_json(value: object) -> str:
    """Return stable compact JSON for hashes and persisted audit fields."""

    return _canonical_json_from_value(_canonical_value(value))


__all__ = ["canonical_json", "decimal_coerce", "enum_text", "flag", "json_default"]
