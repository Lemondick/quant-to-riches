"""Frame shaping and validation helpers for the DuckDB decision repository.

These were module-level functions in :mod:`decision_repository`. Moving them
here keeps that module under the source-size ceiling while leaving the public
import surface unchanged: ``decision_repository`` re-exports every name, so
existing importers keep working.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from quant_assistant.common.serialization import json_default
from quant_assistant.funds.fee_terms import decode_fee_terms, terms_object
from quant_assistant.funds.fees import FundFeeSchedule, RedemptionTier
from quant_assistant.models.entities import AssetType
from quant_assistant.portfolio.nav_continuity import (
    _decimal,
    _required_date,
    _to_date,
)
from quant_assistant.strategies.stocks import _DEFAULT_CONFIG as _DEFAULT_STOCK_CONFIG


def _ranking_records(ranking: pd.DataFrame) -> tuple[dict[str, object], ...]:
    if not isinstance(ranking, pd.DataFrame):
        raise TypeError("stock ranking must be a DataFrame")
    if ranking.empty:
        return ()
    numeric_columns = (
        "momentum_z",
        "trend_z",
        "low_volatility_z",
        "liquidity_z",
        "score",
        "rank_percentile",
    )
    required = {"symbol", "rank", *numeric_columns}
    missing = required.difference(ranking.columns)
    if missing:
        raise ValueError(f"stock ranking missing columns: {', '.join(sorted(missing))}")

    universe_size = len(ranking)
    records: list[dict[str, object]] = []
    symbols: set[str] = set()
    ordered = ranking.sort_values(["rank", "symbol"], kind="mergesort")
    for row in ordered.to_dict("records"):
        symbol = str(row["symbol"]).strip().upper()
        if not symbol or symbol in symbols:
            raise ValueError("stock ranking symbols must be non-empty and unique")
        symbols.add(symbol)
        raw_rank = pd.to_numeric(pd.Series([row["rank"]]), errors="coerce").iloc[0]
        if pd.isna(raw_rank) or not float(raw_rank).is_integer():
            raise ValueError(f"{symbol} stock rank must be an integer")
        rank = int(raw_rank)
        values = {column: float(row[column]) for column in numeric_columns}
        if rank <= 0 or not all(np.isfinite(value) for value in values.values()):
            raise ValueError(f"{symbol} stock ranking contains invalid values")
        expected_percentile = rank / universe_size
        if not np.isclose(values["rank_percentile"], expected_percentile, atol=1e-12):
            raise ValueError(f"{symbol} stock rank percentile is inconsistent")
        records.append(
            {
                "symbol": symbol,
                "rank": rank,
                "universe_size": universe_size,
                **values,
            }
        )
    if {int(row["rank"]) for row in records} != set(range(1, universe_size + 1)):
        raise ValueError("stock ranks must form a contiguous sequence")
    return tuple(records)


def _strategy_stock_symbols(instruments: pd.DataFrame, as_of_date: date) -> tuple[str, ...]:
    """Return only securities that can actually enter the locked stock strategy."""

    if instruments.empty:
        return ()
    rows = instruments.copy(deep=True)
    listed_on = pd.to_datetime(rows.get("listed_on"), errors="coerce")
    symbols = rows.get("symbol", pd.Series(dtype="string")).astype("string").str.upper()
    boards = rows.get("board", pd.Series(index=rows.index, dtype="string")).astype("string")
    industries = rows.get("industry", pd.Series(index=rows.index, dtype="string"))
    is_st = rows.get("is_st", pd.Series(True, index=rows.index)).fillna(True).astype(bool)
    is_suspended = (
        rows.get("is_suspended", pd.Series(True, index=rows.index)).fillna(True).astype(bool)
    )
    eligible = (
        symbols.str.match(r"^(?:60[0135]\d{3}\.SH|00[0123]\d{3}\.SZ)$", na=False)
        & boards.str.lower().isin({"main", "main_board", "主板"})
        & listed_on.notna()
        & (pd.Timestamp(as_of_date) - listed_on).dt.days.ge(_DEFAULT_STOCK_CONFIG.min_listing_days)
        & industries.notna()
        & industries.astype(str).str.strip().ne("")
        & ~is_st
        & ~is_suspended
    )
    if "delisted_on" in rows:
        delisted_on = pd.to_datetime(rows["delisted_on"], errors="coerce")
        eligible &= delisted_on.isna() | delisted_on.gt(pd.Timestamp(as_of_date))
    return tuple(sorted(symbols.loc[eligible].dropna().astype(str).unique()))


def _fee_schedules(
    rows: pd.DataFrame,
) -> tuple[dict[str, FundFeeSchedule], list[str]]:
    schedules: dict[str, FundFeeSchedule] = {}
    blockers: list[str] = []
    for symbol, group in rows.groupby("symbol", sort=True):
        row = group.iloc[-1]
        try:
            raw_tiers = _json_value(row["redemption_tiers_json"], default=[])
            tiers = tuple(_redemption_tier(value) for value in raw_tiers)
            schedule = FundFeeSchedule(
                symbol=str(symbol),
                share_class=terms_object(row.get("terms_json", "{}")).get("share_class")
                or _share_class(str(symbol), row),
                purchase_rate=_decimal(row["purchase_fee_rate"]),
                annual_service_rate=_decimal(row["service_fee_rate_annual"]),
                redemption_tiers=tiers,
                verified_on=_required_date(row["verified_on"]),
                effective_from=_required_date(row["effective_from"]),
                effective_to=_to_date(row.get("effective_to")),
                **decode_fee_terms(row.get("terms_json", "{}")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            blockers.append(f"{symbol}费率记录无效：{exc}")
            continue
        schedules[str(symbol)] = schedule
    return schedules, blockers


def _redemption_tier(value: object) -> RedemptionTier:
    if isinstance(value, Mapping):
        return RedemptionTier(
            int(value["min_days"]),
            None if value.get("max_days") is None else int(value["max_days"]),
            _decimal(value["rate"]),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 3:
        return RedemptionTier(
            int(value[0]),
            None if value[1] is None else int(value[1]),
            _decimal(value[2]),
        )
    raise ValueError("赎回费档格式无效")


def _share_class(symbol: str, row: pd.Series) -> str:
    raw = row.get("share_class")
    if raw is not None and not pd.isna(raw):
        value = str(raw).strip().upper().replace("类", "")
        if value in {"A", "C"}:
            return value
    suffix = symbol[-1:].upper()
    if suffix not in {"A", "C"}:
        raise ValueError("无法确定A/C份额类别")
    return suffix


def _fund_status_frame(fund_pool: pd.DataFrame, fund_nav: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "symbol",
        "nav_date",
        "is_purchasable",
        "is_redeemable",
        "purchase_limit",
        "as_of_date",
    ]
    if fund_pool.empty:
        return pd.DataFrame(columns=columns)
    latest_nav = (
        fund_nav.sort_values(["symbol", "nav_date"])
        .groupby("symbol", as_index=False)
        .tail(1)[["symbol", "nav_date"]]
        if not fund_nav.empty
        else pd.DataFrame(columns=["symbol", "nav_date"])
    )
    status = fund_pool.merge(latest_nav, on="symbol", how="left")
    for column, default in (
        ("is_purchasable", False),
        ("is_redeemable", False),
        ("purchase_limit", None),
    ):
        if column not in status:
            status[column] = default
    return status.loc[:, columns].reset_index(drop=True)


def _with_fund_instruments(instruments: pd.DataFrame, fund_pool: pd.DataFrame) -> pd.DataFrame:
    if fund_pool.empty:
        return instruments
    existing = set(instruments["symbol"].astype(str)) if "symbol" in instruments else set()
    rows = []
    for item in fund_pool.to_dict("records"):
        symbol = str(item["symbol"])
        if symbol in existing:
            continue
        rows.append(
            {
                "symbol": symbol,
                "asset_type": AssetType.FUND.value,
                "name": symbol,
                "industry": None,
                "is_st": False,
                "is_suspended": False,
                "as_of_date": item["as_of_date"],
                "is_purchasable": item.get("is_purchasable", False),
            }
        )
    return pd.concat([instruments, pd.DataFrame(rows)], ignore_index=True, sort=False)


def _snapshot_issues(value: object) -> list[str]:
    issues = _json_value(value, default=[])
    results = []
    for issue in issues if isinstance(issues, list) else []:
        if isinstance(issue, Mapping):
            if issue.get("blocking") is False:
                continue
            message = str(issue.get("message", issue.get("code", ""))).strip()
        else:
            message = str(issue).strip()
        if message:
            results.append(message)
    return results


def _split_versions(value: str) -> set[str]:
    return {item for item in value.split("+") if item}


def _json_value(value: object, *, default: object) -> Any:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _unique_strings(values: Sequence[object]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _json_default(value: object) -> object:
    return json_default(value)


__all__ = [
    "_fee_schedules",
    "_fund_status_frame",
    "_json_default",
    "_json_value",
    "_ranking_records",
    "_redemption_tier",
    "_share_class",
    "_snapshot_issues",
    "_split_versions",
    "_strategy_stock_symbols",
    "_unique_strings",
    "_with_fund_instruments",
]
