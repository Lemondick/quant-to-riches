"""Small BaoStock history boundary for explicit public-data research runs."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from quant_assistant.providers.base import ProviderDataError

STOCK_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,pctChg,isST"
)
INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg"


def sdk_symbol(symbol: str) -> str:
    match = re.fullmatch(r"(\d{6})\.(SH|SZ)", symbol)
    if match is None:
        raise ValueError("BaoStock symbol must be six digits followed by .SH or .SZ")
    return f"{match[2].lower()}.{match[1]}"


def read_result(result: Any) -> pd.DataFrame:
    """Reject server errors even when they arrive after a partial page."""
    if result.error_code != "0":
        raise ProviderDataError(f"BaoStock {result.error_code}: {result.error_msg}")
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise ProviderDataError(f"BaoStock {result.error_code}: {result.error_msg}")
    return pd.DataFrame(rows, columns=result.fields)


def normalize_history(raw: pd.DataFrame, symbol: str, *, is_index: bool = False) -> pd.DataFrame:
    """Keep unadjusted OHLC and supplied suspension evidence; leave limits unknown.

    The caller must verify the amount/volume units against independent evidence
    before declaring a source eligible for AI research or production acceptance.
    """
    numeric = ("open", "high", "low", "close", "volume", "amount")
    required = {"date", "code", *numeric}
    if not is_index:
        required.update(("adjustflag", "tradestatus", "isST"))
    missing = required - set(raw.columns)
    if raw.empty or missing:
        raise ProviderDataError(f"BaoStock empty history or missing columns: {sorted(missing)}")
    if not raw["code"].astype(str).eq(sdk_symbol(symbol)).all():
        raise ProviderDataError("BaoStock returned a different symbol")
    if not is_index:
        if not raw["adjustflag"].astype(str).eq("3").all():
            raise ProviderDataError("BaoStock history must explicitly be unadjusted (3)")
        for field in ("tradestatus", "isST"):
            if not raw[field].astype(str).isin(("0", "1")).all():
                raise ProviderDataError(f"BaoStock {field} must explicitly be 0 or 1")
    result = raw.loc[:, numeric].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(result.to_numpy(dtype=float)).all():
        raise ProviderDataError("BaoStock history contains missing or non-finite numbers")
    dates = pd.to_datetime(raw["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or dates.duplicated().any():
        raise ProviderDataError("BaoStock history contains invalid or duplicate dates")
    result.insert(0, "trade_date", dates.dt.date)
    result.insert(0, "symbol", symbol)
    result["suspended"] = None if is_index else raw["tradestatus"].astype(str).eq("0")
    result["upper_limit"] = np.nan
    result["lower_limit"] = np.nan
    return result.sort_values("trade_date", kind="stable").reset_index(drop=True)


def compare_sina_history(raw: pd.DataFrame, independent: pd.DataFrame) -> dict[str, object]:
    """Verify dates, unadjusted prices and unscaled amount/volume against Sina."""
    fields = ("open", "high", "low", "close", "amount", "volume")
    left = raw.loc[:, ["date", *fields]].copy()
    right = independent.loc[:, ["date", *fields]].copy()
    for frame in (left, right):
        frame["date"] = frame["date"].astype(str)
        frame[list(fields)] = (
            frame[list(fields)].apply(pd.to_numeric, errors="coerce").astype(float)
        )
        if (
            frame["date"].duplicated().any()
            or not np.isfinite(frame[list(fields)].to_numpy(dtype=float)).all()
        ):
            raise ProviderDataError("Cross-source check contains duplicate or invalid observations")
    merged = left.merge(right, on="date", suffixes=("_bao", "_sina"), validate="one_to_one")
    same_dates = len(merged) == len(left) == len(right) and bool(len(merged))
    price_match = all(
        np.allclose(merged[f"{field}_bao"], merged[f"{field}_sina"], rtol=0, atol=1e-6)
        for field in fields[:4]
    )
    units_match = all(
        np.allclose(merged[f"{field}_bao"], merged[f"{field}_sina"], rtol=1e-5, atol=1)
        for field in fields[4:]
    )
    return {
        "verified": bool(same_dates and price_match and units_match),
        "matched_dates": len(merged),
        "dates_match": same_dates,
        "ohlc_match": price_match,
        "unscaled_amount_volume_match": units_match,
    }
