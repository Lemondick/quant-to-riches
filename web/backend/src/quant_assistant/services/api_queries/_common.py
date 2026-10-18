"""Shared constants and row coercion helpers used across the api_queries splits.

The helpers in this module were originally private to ``api_queries.py`` and are
consumed by the View mappers, the ``LocalApiQueries`` mixins, and the public
analysis helpers.  Grouping them avoids duplicating the coercion rules across
the split modules.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from quant_assistant.common.dates import required_date, required_datetime
from quant_assistant.common.serialization import decimal_coerce

MAX_USER_TEXT_CODEPOINTS = 2_000
_MISSING: Any = object()
_NO_DEFAULT: Any = object()


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _nonempty_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise TypeError("text collection must be a sequence")
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _decimal(value: object, field_name: str) -> Decimal:
    try:
        return decimal_coerce(value, field_name)
    except (TypeError, ValueError) as exc:
        if "finite" in str(exc):
            raise ValueError(f"{field_name} must be finite") from exc
        raise ValueError(f"{field_name} is invalid") from exc


def _optional_decimal(value: Decimal | None, label: str) -> Decimal | None:
    return None if value is None else _decimal(value, label)


def _optional_decimal_value(value: object, label: str) -> Decimal | None:
    if value is None or _is_missing(value):
        return None
    return _decimal(value, label)


def _finite_float(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be numeric")
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{field_name} must be finite")
    return converted


def _optional_finite_float(value: object, field_name: str) -> float | None:
    if _is_missing(value):
        return None
    return _finite_float(value, field_name)


def _optional_int(value: object, field_name: str) -> int | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")
    converted = int(value)
    if converted < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return converted


def _count(row: object, field_name: str) -> int:
    value = _optional_int(_field(row, field_name, 0), field_name)
    return 0 if value is None else value


def _date(value: object, field_name: str) -> date:
    return required_date(value, field_name)


def _optional_date(value: object) -> date | None:
    return None if _is_missing(value) else _date(value, "as_of_date")


def _datetime(value: object, field_name: str) -> datetime:
    return required_datetime(value, field_name)


def _optional_datetime(value: object, field_name: str) -> datetime | None:
    if _is_missing(value):
        return None
    return _datetime(value, field_name)


def _json_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _json_list(value: object) -> tuple[object, ...]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        value = parsed
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(value)
    return ()


def _boolean(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _records(value: object) -> tuple[object, ...]:
    if isinstance(value, pd.DataFrame):
        return tuple(value.to_dict(orient="records"))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    raise TypeError("query result must be a DataFrame or record sequence")


def _field(value: object, name: str, default: object = _NO_DEFAULT) -> object:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
    elif hasattr(value, name):
        return getattr(value, name)
    if default is not _NO_DEFAULT:
        return default
    raise ValueError(f"missing required field: {name}")


def _run_id(value: object) -> str:
    return _nonempty_text(value, "run_id")


def _evidence_id(run_id: str, source_kind: str, symbol: str, reason_index: int) -> str:
    source = f"{run_id}:{source_kind}:{symbol}:{reason_index}"
    return "ev-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _enum_value(enum_type: type, value: object, field_name: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc


def _metric(raw: object, metrics: object, name: str) -> float:
    raw_value = _field(raw, name, _MISSING)
    if raw_value is not _MISSING:
        return _finite_float(raw_value, name)
    if isinstance(metrics, Mapping) and name in metrics:
        return _finite_float(metrics[name], name)
    raise ValueError(f"backtest metric missing: {name}")
