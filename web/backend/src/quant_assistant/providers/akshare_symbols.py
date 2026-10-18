"""Pure AKShare symbol, column, and field-normalization helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

import pandas as pd

from quant_assistant.common.serialization import flag
from quant_assistant.common.tabular import first_column
from quant_assistant.providers.base import ProviderDataError


def first_matching_column(frame: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    """Find a provider column with exact or case-insensitive matching."""

    direct = first_column(frame, candidates)
    if direct is not None:
        return direct
    lowered = {str(column).strip().lower(): str(column) for column in frame.columns}
    return next(
        (
            lowered.get(candidate.lower())
            for candidate in candidates
            if lowered.get(candidate.lower())
        ),
        None,
    )


def coerce_suspended_flag(value: object) -> bool | None:
    return flag(value, default=None)


def bare_symbol(symbol: str) -> str:
    return (
        str(symbol)
        .split(".", maxsplit=1)[0]
        .removeprefix("sh")
        .removeprefix("sz")
        .removeprefix("bj")
    )


def stock_exchange(symbol: str) -> str:
    code = bare_symbol(symbol)
    if code.startswith(("4", "8", "92")):
        return "BJ"
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    return "BJ"


def stock_board(symbol: str) -> str:
    code = bare_symbol(symbol)
    if code.startswith("688"):
        return "star"
    if code.startswith("3"):
        return "chinext"
    if code.startswith(("4", "8", "92")):
        return "beijing"
    return "main"


def tx_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().lower()
    if normalized.startswith(("sh", "sz", "bj")):
        return normalized
    code = bare_symbol(normalized).zfill(6)
    return f"{stock_exchange(code).lower()}{code}"


def normalize_a_share_symbol(symbol: object) -> str:
    code = bare_symbol(str(symbol)).zfill(6)
    return f"{code}.{stock_exchange(code)}"


def normalize_index_symbol(symbol: str) -> str:
    text = str(symbol)
    if "." in text:
        code, exchange = text.upper().split(".", maxsplit=1)
        return f"{code.zfill(6)}.{exchange}"
    code = bare_symbol(text).zfill(6)
    prefix = text[:2].lower()
    exchange = "SZ" if prefix == "sz" or code.startswith("399") else "SH"
    return f"{code}.{exchange}"


def akshare_index_symbol(symbol: str) -> str:
    normalized = normalize_index_symbol(symbol)
    code, exchange = normalized.split(".")
    return f"{exchange.lower()}{code}"


def find_fund_column(frame: pd.DataFrame, suffix: str, *, required: bool = True) -> str | None:
    candidates = [str(column) for column in frame.columns if str(column).endswith(suffix)]
    if candidates:
        return candidates[0]
    if required:
        raise ProviderDataError(f"fund_daily: no column ending with {suffix!r}")
    return None


def date_from_fund_column(column: str) -> date:
    match = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", column)
    if not match:
        raise ProviderDataError(f"fund_daily: cannot derive NAV date from column {column!r}")
    year, month, day = (int(value) for value in match.groups())
    return date(year, month, day)


__all__ = [
    "akshare_index_symbol",
    "bare_symbol",
    "coerce_suspended_flag",
    "date_from_fund_column",
    "find_fund_column",
    "first_matching_column",
    "normalize_a_share_symbol",
    "normalize_index_symbol",
    "stock_board",
    "stock_exchange",
    "tx_symbol",
]
