"""Dated latest price-limit observations from two public quote sources.

Neither endpoint accepts a historical trade date. The expected date is a check,
not a request to retrieve that day's history.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.base import ProviderDataError


def quote_symbol(symbol: str) -> str:
    if not re.fullmatch(r"(?:6[08]\d{4}\.SH|(?:00|30)\d{4}\.SZ)", symbol):
        raise ValueError("only Shanghai/Shenzhen A-stock symbols are supported")
    code, exchange = symbol.split(".")
    return exchange.lower() + code


def _limits(upper: object, lower: object) -> tuple[Decimal, Decimal]:
    try:
        values = Decimal(str(upper)), Decimal(str(lower))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderDataError("price limits must be explicit finite prices") from exc
    if any(not value.is_finite() or value <= 0 for value in values) or values[0] < values[1]:
        raise ProviderDataError("missing, unlimited or invalid price-limit observations")
    return values


def normalize_tencent_limits(
    text: str, symbols: tuple[str, ...], expected_date: date
) -> pd.DataFrame:
    requested = {quote_symbol(symbol): symbol for symbol in symbols}
    rows = []
    for line in text.splitlines():
        match = re.fullmatch(r'v_([a-z]{2}\d{6})="(.*)";', line.strip())
        if match is None or match[1] not in requested:
            raise ProviderDataError("unexpected or malformed Tencent stock quote")
        fields = match[2].split("~")
        if len(fields) <= 48 or fields[2] != match[1][2:]:
            raise ProviderDataError("Tencent stock quote is incomplete or has the wrong code")
        if not re.fullmatch(r"\d{14}", fields[30]):
            raise ProviderDataError("Tencent quote needs a complete trade timestamp")
        try:
            observed = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI_TZ)
        except ValueError as exc:
            raise ProviderDataError("Tencent quote needs a complete trade timestamp") from exc
        if observed.date() != expected_date:
            raise ProviderDataError(f"Tencent quote date is {observed.date()}, not {expected_date}")
        upper, lower = _limits(fields[47], fields[48])
        rows.append(
            {
                "symbol": requested[match[1]],
                "trade_date": observed.date(),
                "upper_limit": upper,
                "lower_limit": lower,
                "quote_time": observed.isoformat(),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty or frame["symbol"].duplicated().any() or len(frame) != len(requested):
        raise ProviderDataError("Tencent quote batch is incomplete or duplicated")
    return frame


def normalize_eastmoney_limits(payload: dict, symbol: str, expected_date: date) -> dict:
    quote_symbol(symbol)
    data = payload.get("data")
    if payload.get("rc") != 0 or not isinstance(data, dict) or data.get("f57") != symbol[:6]:
        raise ProviderDataError("Eastmoney quote failed or has the wrong stock code")
    timestamp = data.get("f86")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        raise ProviderDataError("Eastmoney quote needs a complete trade timestamp")
    try:
        observed = datetime.fromtimestamp(timestamp, tz=SHANGHAI_TZ)
    except (OSError, OverflowError, ValueError) as exc:
        raise ProviderDataError("invalid Eastmoney quote timestamp") from exc
    if observed.date() != expected_date:
        raise ProviderDataError(f"Eastmoney quote date is {observed.date()}, not {expected_date}")
    upper, lower = _limits(data.get("f51"), data.get("f52"))
    return {
        "symbol": symbol,
        "trade_date": observed.date(),
        "upper_limit": upper,
        "lower_limit": lower,
        "quote_time": observed.isoformat(),
    }


def normalize_eastmoney_snapshot(payload: dict, symbol: str, expected_date: date) -> dict:
    """Read the dated raw snapshot used by efinance.get_quote_snapshot."""
    quote_symbol(symbol)
    quote = payload.get("realtimequote")
    if payload.get("code") != symbol[:6] or not isinstance(quote, dict):
        raise ProviderDataError("Eastmoney snapshot failed or has the wrong stock code")
    try:
        observed = datetime.strptime(
            f"{quote.get('date', '')}{quote.get('time', '')}", "%Y%m%d%H:%M:%S"
        ).replace(tzinfo=SHANGHAI_TZ)
    except ValueError as exc:
        raise ProviderDataError("Eastmoney snapshot needs a complete trade timestamp") from exc
    if observed.date() != expected_date:
        raise ProviderDataError(
            f"Eastmoney snapshot date is {observed.date()}, not {expected_date}"
        )
    upper, lower = _limits(payload.get("topprice"), payload.get("bottomprice"))
    return {
        "symbol": symbol,
        "trade_date": observed.date(),
        "upper_limit": upper,
        "lower_limit": lower,
        "quote_time": observed.isoformat(),
    }


def verify_quote_limits(tencent: pd.DataFrame, eastmoney: pd.DataFrame) -> pd.DataFrame:
    keys = ["symbol", "trade_date"]
    combined = tencent.merge(
        eastmoney,
        on=keys,
        how="outer",
        suffixes=("_tencent", "_eastmoney"),
        validate="one_to_one",
        indicator=True,
    )
    if combined.empty or not combined["_merge"].eq("both").all():
        raise ProviderDataError("quote sources do not cover the same stocks and dates")
    for field in ("upper_limit", "lower_limit"):
        if not combined[f"{field}_tencent"].eq(combined[f"{field}_eastmoney"]).all():
            raise ProviderDataError("quote sources disagree on actual price limits")
        combined[field] = combined[f"{field}_eastmoney"]
    return (
        combined.loc[
            :,
            [
                *keys,
                "upper_limit",
                "lower_limit",
                "quote_time_tencent",
                "quote_time_eastmoney",
            ],
        ]
        .sort_values(keys)
        .reset_index(drop=True)
    )
