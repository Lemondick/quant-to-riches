"""Shared OCR parsing primitives."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from quant_assistant.models.entities import AssetType

from ._models import (
    _CASH_VALUE_KEYWORDS,
    _CODE_RE,
    _FUND_KEYWORDS,
    _HEADER_MARKERS,
    _NUMBER_RE,
)


def _normalise_line(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).replace("￥", "¥").strip()


def _first_code(line: str) -> tuple[str, str | None, int, int] | None:
    match = _CODE_RE.search(line)
    if match is None:
        return None
    raw = match.group(1).upper()
    if "." in raw:
        code, suffix = raw.split(".", maxsplit=1)
        return code, suffix, match.start(), match.end()
    if raw[-1:] in {"A", "C"}:
        return raw[:-1], raw[-1], match.start(), match.end()
    return raw, None, match.start(), match.end()


def _numeric_only(value: str) -> bool:
    return _NUMBER_RE.fullmatch(value.strip()) is not None


def _asset_type(code: str, suffix: str | None, name: str) -> AssetType:
    if suffix == "OF" or suffix in {"A", "C"}:
        return AssetType.FUND
    upper_name = name.upper()
    if any(keyword in upper_name for keyword in _FUND_KEYWORDS):
        return AssetType.FUND
    del code
    return AssetType.STOCK


def _normalise_symbol(code: str, suffix: str | None, asset_type: AssetType) -> str:
    if asset_type is AssetType.FUND:
        return f"{code}{suffix if suffix in {'A', 'C'} else ''}".upper()
    if suffix in {"SH", "SZ", "BJ"}:
        return f"{code}.{suffix}"
    if code.startswith(("6", "9")):
        exchange = "SH"
    elif code.startswith(("0", "2", "3")):
        exchange = "SZ"
    else:
        exchange = "BJ"
    return f"{code}.{exchange}"


def _is_cash_line(line: str) -> bool:
    """Return true for a cash balance row, not a securities table header."""

    non_cash_header_markers = tuple(
        marker for marker in _HEADER_MARKERS if marker not in _CASH_VALUE_KEYWORDS
    )
    if any(marker in line for marker in non_cash_header_markers):
        return False
    keyword_positions = [line.find(keyword) for keyword in _CASH_VALUE_KEYWORDS if keyword in line]
    if not keyword_positions:
        return False
    code = _first_code(line)
    return code is None or min(keyword_positions) < code[2]


def _labelled_number_pattern(labels: Sequence[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    return re.compile(
        rf"(?:{alternatives})\s*(?:[:：=]\s*)?(?P<value>{_NUMBER_RE.pattern})",
        re.IGNORECASE,
    )


__all__ = [
    "_normalise_line",
    "_first_code",
    "_numeric_only",
    "_asset_type",
    "_normalise_symbol",
    "_is_cash_line",
    "_labelled_number_pattern",
]
