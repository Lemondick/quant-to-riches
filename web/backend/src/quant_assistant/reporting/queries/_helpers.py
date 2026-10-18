from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from quant_assistant.common.dates import optional_date, required_datetime
from quant_assistant.common.serialization import decimal_coerce
from quant_assistant.models.entities import DataStatus

__all__ = [
    "_STATUS_PRIORITY",
    "_data_status",
    "_decimal",
    "_is_missing",
    "_json_object",
    "_json_value",
    "_optional_int",
    "_reason_tuple",
    "_required_account_id",
    "_required_date",
    "_required_datetime",
    "_required_run_id",
    "_snapshot_blockers",
    "_text_tuple",
    "_validate_limit",
    "_validate_offset",
]


# ``_RISK_INCREASING`` is owned by ``_decisions.py`` (only the decision mixin
# uses it). Shared cross-mixin ordering needs only ``_STATUS_PRIORITY``.
_STATUS_PRIORITY = {
    DataStatus.VALID: 0,
    DataStatus.STALE: 1,
    DataStatus.BLOCKED: 2,
}


def _required_run_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("run_id must be a non-empty string")
    return value.strip()


def _required_account_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("account_id must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > 64 or not all(
        character.isalnum() or character in ".-_" for character in normalized
    ):
        raise ValueError("account_id contains unsupported characters")
    return normalized


def _json_value(value: object, default: object) -> Any:
    if _is_missing(value):
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _json_object(value: object) -> Mapping[str, object]:
    parsed = _json_value(value, {})
    return parsed if isinstance(parsed, Mapping) else {}


def _text_tuple(value: object) -> tuple[str, ...]:
    parsed = _json_value(value, ())
    if isinstance(parsed, str):
        parsed = (parsed,)
    if not isinstance(parsed, Sequence) or isinstance(parsed, (bytes, bytearray)):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in parsed if str(item).strip()))


def _reason_tuple(value: object) -> tuple[str, ...]:
    return _text_tuple(value)


def _data_status(value: object) -> DataStatus:
    try:
        return value if isinstance(value, DataStatus) else DataStatus(str(value))
    except (TypeError, ValueError):
        return DataStatus.BLOCKED


def _snapshot_blockers(value: object) -> tuple[str, ...]:
    issues = _json_value(value, ())
    if not isinstance(issues, Sequence) or isinstance(issues, (str, bytes)):
        return ()
    messages: list[str] = []
    for issue in issues:
        if isinstance(issue, Mapping):
            if issue.get("blocking") is False:
                continue
            text = str(issue.get("message", issue.get("code", ""))).strip()
        else:
            text = str(issue).strip()
        if text and text not in messages:
            messages.append(text)
    return tuple(messages)


def _decimal(value: object) -> Decimal:
    try:
        return decimal_coerce(value, "persisted decimal")
    except (TypeError, ValueError) as exc:
        raise ValueError("persisted decimal is invalid") from exc


def _required_date(value: object) -> date:
    parsed = optional_date(value)
    if parsed is None:
        raise ValueError("persisted date is invalid")
    return parsed


def _required_datetime(value: object) -> datetime:
    return required_datetime(value, "persisted datetime")


def _optional_int(value: object) -> int | None:
    return None if _is_missing(value) else int(value)


def _validate_limit(value: object, label: str, *, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be between 1 and {maximum}")


def _validate_offset(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(missing)
    except (TypeError, ValueError):
        return False
