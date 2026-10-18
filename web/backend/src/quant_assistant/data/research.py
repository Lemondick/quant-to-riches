"""Automatic research-evidence collection and point-in-time alignment.

The market provider remains responsible for talking to an external service and
normalising its response.  This module owns the boring but important part:
date cut-offs, duplicate detection, immutable snapshots, validation, and
persisting evidence that Smart DCA and fund factors can consume later.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import Any

import numpy as np
import pandas as pd

from quant_assistant.common.serialization import flag
from quant_assistant.config import Settings
from quant_assistant.data.snapshots import SnapshotWriter
from quant_assistant.data.validation import ValidationIssue, validate_dataset
from quant_assistant.models.entities import DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.base import ProviderError

DEFAULT_VALUATION_SYMBOLS = ("000300.SH",)
_flag_value = partial(flag, default=None)
VALUATION_COLUMNS = ("symbol", "as_of_date", "pe", "pb", "source")
MANAGER_COLUMNS = (
    "symbol",
    "as_of_date",
    "manager_name",
    "manager_tenure_years",
    "manager_win_rate",
    "alpha_stability",
    "drawdown_recovery_days",
    "source",
)
POINT_IN_TIME_EVIDENCE_COLUMNS = (
    "symbol",
    "trade_date",
    "suspended",
    "upper_limit",
    "lower_limit",
)


@dataclass(frozen=True, slots=True)
class ResearchDatasetResult:
    """Outcome for one automatically collected evidence dataset."""

    dataset: str
    status: DataStatus
    row_count: int
    snapshot_id: int | None
    issues: tuple[ValidationIssue, ...] = ()
    coverage_ready: bool = True
    coverage_blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchSyncResult:
    """Immutable summary returned by :func:`sync_research_evidence`."""

    start_date: date
    end_date: date
    datasets: tuple[ResearchDatasetResult, ...]

    @property
    def ready(self) -> bool:
        return bool(self.datasets) and all(
            item.status is DataStatus.VALID and item.coverage_ready for item in self.datasets
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(
            message
            for item in self.datasets
            for message in (
                *tuple(issue.message for issue in item.issues if issue.blocking),
                *item.coverage_blockers,
            )
        )


_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "fund_symbol", "code", "基金代码", "指数代码"),
    "date": ("as_of_date", "valuation_date", "trade_date", "date", "日期", "净值日期"),
    "pe": (
        "pe",
        "pe_ttm",
        "ttm_pe",
        "rolling_pe",
        "滚动市盈率",
        "动态市盈率",
        "市盈率",
    ),
    "pb": ("pb", "pb_lf", "rolling_pb", "市净率", "滚动市净率"),
    "manager_name": ("manager_name", "manager", "基金经理", "基金经理人", "姓名"),
    "tenure": (
        "manager_tenure_years",
        "manager_years",
        "manager_tenure",
        "经理任职年限",
        "累计从业时间",
    ),
    "win_rate": (
        "manager_win_rate",
        "manager_win",
        "win_rate",
        "经理胜率",
        "盈利概率",
    ),
    "alpha_stability": (
        "alpha_stability",
        "manager_alpha_stability",
        "selection_alpha_stability",
        "选股alpha稳定性",
    ),
    "recovery_days": (
        "drawdown_recovery_days",
        "manager_recovery_days",
        "最大回撤修复天数",
    ),
}


def _find_column(frame: pd.DataFrame, key: str) -> str | None:
    aliases = _ALIASES[key]
    for alias in aliases:
        if alias in frame.columns:
            return alias
    lowered = {str(column).strip().lower(): column for column in frame.columns}
    for alias in aliases:
        match = lowered.get(alias.lower())
        if match is not None:
            return match
    return None


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _normalise_symbol(value: object) -> str:
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        # A provider may hand us a non-scalar object.  Let the string
        # normalisation below retain an auditable representation instead of
        # failing while checking an optional field.
        pass
    text = str(value).strip().upper()
    if text.endswith((".A", ".C")):
        text = text[:-2]
    return text


def _deduplicate(frame: pd.DataFrame, keys: Sequence[str], label: str) -> pd.DataFrame:
    duplicate_mask = frame.duplicated(list(keys), keep=False)
    if not duplicate_mask.any():
        return frame
    conflicts: list[tuple[object, ...]] = []
    for key, group in frame.loc[duplicate_mask].groupby(list(keys), sort=False, dropna=False):
        if len(group.drop_duplicates()) > 1:
            conflicts.append(key if isinstance(key, tuple) else (key,))
    if conflicts:
        raise ValueError(f"{label}存在同一键的冲突记录")
    return frame.drop_duplicates(list(keys), keep="last")


def normalize_valuation_history(
    frame: pd.DataFrame,
    *,
    source: str,
    as_of_date: date | None = None,
    default_symbol: str | None = None,
) -> pd.DataFrame:
    """Normalize PE/PB history from an external provider.

    At least one of PE and PB may be absent for a row.  The downstream
    strategy decides whether the available metric is sufficient; this layer
    never substitutes a median or a zero.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("valuation history must be a pandas DataFrame")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must not be empty")
    if as_of_date is not None and not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date or None")
    result = pd.DataFrame(index=frame.index)
    symbol_column = _find_column(frame, "symbol")
    if symbol_column is None:
        if default_symbol is None:
            raise ValueError("估值历史缺少基金/指数代码")
        result["symbol"] = _normalise_symbol(default_symbol)
    else:
        result["symbol"] = frame[symbol_column].map(_normalise_symbol)
    date_column = _find_column(frame, "date")
    if date_column is None:
        if as_of_date is None:
            raise ValueError("估值历史缺少日期")
        result["as_of_date"] = as_of_date
    else:
        result["as_of_date"] = pd.to_datetime(frame[date_column], errors="coerce").dt.date
    for metric in ("pe", "pb"):
        column = _find_column(frame, metric)
        result[metric] = _numeric(frame[column]) if column is not None else np.nan
    result["source"] = source.strip()
    result = result.loc[result["symbol"].ne("") & result["as_of_date"].notna()].copy()
    if as_of_date is not None:
        result = result.loc[result["as_of_date"].le(as_of_date)]
    values_present = result[["pe", "pb"]].notna().any(axis=1)
    result = result.loc[values_present].copy()
    for metric in ("pe", "pb"):
        values = result[metric]
        result.loc[values.notna() & (values <= 0), metric] = np.nan
    result = _deduplicate(result, ("symbol", "as_of_date"), "估值历史")
    return (
        result.loc[:, VALUATION_COLUMNS]
        .sort_values(["symbol", "as_of_date"], kind="mergesort")
        .reset_index(drop=True)
    )


def normalize_manager_history(
    frame: pd.DataFrame,
    *,
    source: str,
    as_of_date: date | None = None,
    default_symbol: str | None = None,
) -> pd.DataFrame:
    """Normalize manager archive records with explicit unavailable fields."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("manager history must be a pandas DataFrame")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must not be empty")
    result = pd.DataFrame(index=frame.index)
    symbol_column = _find_column(frame, "symbol")
    if symbol_column is None:
        if default_symbol is None:
            raise ValueError("经理档案缺少基金代码")
        result["symbol"] = _normalise_symbol(default_symbol)
    else:
        result["symbol"] = frame[symbol_column].map(_normalise_symbol)
    date_column = _find_column(frame, "date")
    if date_column is None:
        if as_of_date is None:
            raise ValueError("经理档案缺少观察日期")
        result["as_of_date"] = as_of_date
    else:
        result["as_of_date"] = pd.to_datetime(frame[date_column], errors="coerce").dt.date
    manager_column = _find_column(frame, "manager_name")
    result["manager_name"] = (
        frame[manager_column].astype("string").str.strip() if manager_column is not None else pd.NA
    )
    for key, canonical in (
        ("tenure", "manager_tenure_years"),
        ("win_rate", "manager_win_rate"),
        ("alpha_stability", "alpha_stability"),
        ("recovery_days", "drawdown_recovery_days"),
    ):
        column = _find_column(frame, key)
        result[canonical] = _numeric(frame[column]).astype(float) if column is not None else np.nan
        if key == "tenure" and column in {"累计从业时间", "manager_tenure_days", "tenure_days"}:
            result[canonical] = result[canonical] / 365.25
    win_rate = result["manager_win_rate"]
    result.loc[win_rate.gt(1) & win_rate.le(100), "manager_win_rate"] = (
        win_rate.loc[win_rate.gt(1) & win_rate.le(100)] / 100
    )
    result["source"] = source.strip()
    result = result.loc[result["symbol"].ne("") & result["as_of_date"].notna()].copy()
    if as_of_date is not None:
        result = result.loc[result["as_of_date"].le(as_of_date)]
    result = _deduplicate(result, ("symbol", "as_of_date"), "经理档案")
    return (
        result.loc[:, MANAGER_COLUMNS]
        .sort_values(["symbol", "as_of_date"], kind="mergesort")
        .reset_index(drop=True)
    )


def valuation_coverage(
    frame: pd.DataFrame,
    *,
    minimum_observations: int = 60,
) -> pd.DataFrame:
    """Summarize whether PE/PB have enough point-in-time observations."""

    if isinstance(minimum_observations, bool) or minimum_observations < 2:
        raise ValueError("minimum_observations must be at least 2")
    normalized = normalize_valuation_history(frame, source="coverage")
    rows: list[dict[str, object]] = []
    for symbol, group in normalized.groupby("symbol", sort=True):
        rows.append(
            {
                "symbol": symbol,
                "pe_observations": int(group["pe"].notna().sum()),
                "pb_observations": int(group["pb"].notna().sum()),
                "first_date": group["as_of_date"].min(),
                "last_date": group["as_of_date"].max(),
                "eligible": bool(
                    group["pe"].notna().sum() >= minimum_observations
                    or group["pb"].notna().sum() >= minimum_observations
                ),
            }
        )
    return pd.DataFrame(rows)


def align_point_in_time_prices(
    prices: pd.DataFrame,
    evidence: pd.DataFrame,
    *,
    require_all: bool = True,
) -> pd.DataFrame:
    """Attach explicit suspension/limit evidence to bars by exact date key."""

    if not isinstance(prices, pd.DataFrame) or not isinstance(evidence, pd.DataFrame):
        raise TypeError("prices and evidence must be pandas DataFrames")
    required_price = {"symbol", "trade_date"}
    if not required_price.issubset(prices.columns):
        raise ValueError("prices必须包含symbol和trade_date")
    missing = [column for column in POINT_IN_TIME_EVIDENCE_COLUMNS if column not in evidence]
    if missing:
        raise ValueError("点时证据缺少列：" + ",".join(missing))
    base = prices.copy(deep=True)
    base["symbol"] = base["symbol"].map(_normalise_symbol)
    base["trade_date"] = pd.to_datetime(base["trade_date"], errors="coerce").dt.date
    if base[["symbol", "trade_date"]].isna().any().any() or base["symbol"].eq("").any():
        raise ValueError("prices包含无效symbol/trade_date")
    extra = evidence.loc[:, POINT_IN_TIME_EVIDENCE_COLUMNS].copy(deep=True)
    extra["symbol"] = extra["symbol"].map(_normalise_symbol)
    extra["trade_date"] = pd.to_datetime(extra["trade_date"], errors="coerce").dt.date
    if extra[["symbol", "trade_date"]].isna().any().any() or extra["symbol"].eq("").any():
        raise ValueError("点时证据包含无效symbol/trade_date")
    extra["suspended"] = extra["suspended"].map(_flag_value)
    for column in ("upper_limit", "lower_limit"):
        extra[column] = _numeric(extra[column])
    if extra["suspended"].isna().any():
        raise ValueError("点时证据的suspended不可缺失")
    if extra[["upper_limit", "lower_limit"]].isna().any().any():
        raise ValueError("点时证据的涨跌停价不可缺失")
    if (extra[["upper_limit", "lower_limit"]] <= 0).any().any():
        raise ValueError("点时证据的涨跌停价必须为正")
    if (extra["upper_limit"] < extra["lower_limit"]).any():
        raise ValueError("upper_limit不能低于lower_limit")
    extra = _deduplicate(extra, ("symbol", "trade_date"), "点时证据")
    merged = base.merge(extra, on=["symbol", "trade_date"], how="left", validate="one_to_one")
    if require_all and merged[["suspended", "upper_limit", "lower_limit"]].isna().any().any():
        missing_rows = int(merged["suspended"].isna().sum())
        raise ValueError(f"有{missing_rows}行行情缺少同日点时停牌/涨跌停证据")
    return merged.sort_values(["trade_date", "symbol"], kind="mergesort").reset_index(drop=True)


def sync_research_evidence(
    settings: Settings,
    provider: object,
    start_date: date,
    end_date: date,
    *,
    symbols: Sequence[str] = DEFAULT_VALUATION_SYMBOLS,
    fund_symbols: Sequence[str] = (),
) -> ResearchSyncResult:
    """Fetch, validate, snapshot, and persist valuation/manager evidence.

    Provider methods are optional so existing offline fixtures remain usable.
    Missing methods are recorded as blocked evidence rather than causing the
    ordinary price update to invent research inputs.
    """

    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise TypeError("start_date and end_date must be dates")
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    provider_name = (
        str(getattr(provider, "name", "external-research")).strip() or "external-research"
    )
    database = Database(settings.database_path)
    database.initialize()
    writer = SnapshotWriter(settings.raw_snapshot_dir, provider_name)
    results: list[ResearchDatasetResult] = []

    valuation_method = getattr(provider, "fetch_valuation_history", None)
    selected_symbols = tuple(
        dict.fromkeys(_normalise_symbol(item) for item in symbols if str(item).strip())
    )
    if not selected_symbols:
        selected_symbols = DEFAULT_VALUATION_SYMBOLS
    if not callable(valuation_method):
        results.append(_unsupported_result("fund_valuation_history", "估值历史抓取接口未配置"))
    else:
        results.append(
            _sync_one(
                database,
                writer,
                dataset="fund_valuation_history",
                frame_factory=lambda: valuation_method(selected_symbols, start_date, end_date),
                normalizer=lambda frame: normalize_valuation_history(
                    frame, source=provider_name, as_of_date=end_date
                ),
                expected_date=end_date,
                coverage_checker=_valuation_coverage_check,
            )
        )

    manager_method = getattr(provider, "fetch_manager_history", None)
    selected_funds = tuple(
        dict.fromkeys(_normalise_symbol(item) for item in fund_symbols if str(item).strip())
    )
    if not selected_funds:
        results.append(_unsupported_result("fund_manager_history", "未提供需同步的基金代码"))
    elif not callable(manager_method):
        results.append(_unsupported_result("fund_manager_history", "经理档案抓取接口未配置"))
    else:
        results.append(
            _sync_one(
                database,
                writer,
                dataset="fund_manager_history",
                frame_factory=lambda: manager_method(selected_funds, end_date),
                normalizer=lambda frame: normalize_manager_history(
                    frame, source=provider_name, as_of_date=end_date
                ),
                expected_date=end_date,
                coverage_checker=_manager_coverage_check,
            )
        )
    return ResearchSyncResult(start_date, end_date, tuple(results))


def _unsupported_result(dataset: str, message: str) -> ResearchDatasetResult:
    return ResearchDatasetResult(
        dataset,
        DataStatus.BLOCKED,
        0,
        None,
        (ValidationIssue("provider_unavailable", message, True),),
    )


def _sync_one(
    database: Database,
    writer: SnapshotWriter,
    *,
    dataset: str,
    frame_factory: Any,
    normalizer: Any,
    expected_date: date,
    coverage_checker: Callable[[pd.DataFrame], tuple[bool, tuple[str, ...]]] | None = None,
) -> ResearchDatasetResult:
    try:
        normalized = normalizer(frame_factory())
    except (ProviderError, ConnectionError, TimeoutError, OSError, TypeError, ValueError) as exc:
        return ResearchDatasetResult(
            dataset,
            DataStatus.BLOCKED,
            0,
            None,
            (ValidationIssue("provider_error", str(exc), True),),
        )
    snapshot = writer.write(dataset, expected_date, normalized)
    validation = validate_dataset(dataset, normalized, expected_date)
    snapshot_id = database.record_snapshot(snapshot, validation)
    if validation.status is DataStatus.VALID:
        incoming = normalized.copy(deep=True)
        incoming["source_snapshot_id"] = snapshot_id
        database.upsert_frame(dataset, incoming)
    coverage_ready = validation.status is DataStatus.VALID
    coverage_blockers: tuple[str, ...] = ()
    if coverage_ready and coverage_checker is not None:
        coverage_ready, coverage_blockers = coverage_checker(normalized)
    return ResearchDatasetResult(
        dataset,
        validation.status,
        len(normalized),
        snapshot_id,
        validation.issues,
        coverage_ready,
        coverage_blockers,
    )


def _valuation_coverage_check(frame: pd.DataFrame) -> tuple[bool, tuple[str, ...]]:
    coverage = valuation_coverage(frame, minimum_observations=60)
    if coverage.empty:
        return False, ("PE/PB历史没有可用的目标日前观测",)
    blockers = tuple(
        f"{row.symbol}的PE/PB历史只有"
        f"{max(row.pe_observations, row.pb_observations)}个观测，至少需要60个"
        for row in coverage.itertuples(index=False)
        if not bool(row.eligible)
    )
    return not blockers, blockers


def _manager_coverage_check(frame: pd.DataFrame) -> tuple[bool, tuple[str, ...]]:
    if frame.empty or "manager_win_rate" not in frame:
        return False, ("基金经理档案缺少经理胜率",)
    blockers = tuple(
        f"{symbol}缺少经理胜率证据"
        for symbol, group in frame.groupby("symbol", sort=True)
        if not group["manager_win_rate"].notna().any()
    )
    return not blockers, blockers


__all__ = [
    "DEFAULT_VALUATION_SYMBOLS",
    "MANAGER_COLUMNS",
    "POINT_IN_TIME_EVIDENCE_COLUMNS",
    "VALUATION_COLUMNS",
    "ResearchDatasetResult",
    "ResearchSyncResult",
    "align_point_in_time_prices",
    "normalize_manager_history",
    "normalize_valuation_history",
    "sync_research_evidence",
    "valuation_coverage",
]
