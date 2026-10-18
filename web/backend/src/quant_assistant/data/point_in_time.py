"""Import explicit historical suspension and price-limit evidence from CSV."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd

from quant_assistant.common.serialization import flag
from quant_assistant.config import Settings
from quant_assistant.data.snapshots import SnapshotWriter
from quant_assistant.data.validation import ValidationIssue, ValidationResult, validate_dataset
from quant_assistant.models.entities import AssetType, DataStatus
from quant_assistant.models.repositories.database import Database

POINT_IN_TIME_COLUMNS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "suspended",
    "upper_limit",
    "lower_limit",
)
POINT_IN_TIME_UNIVERSE_COLUMNS = (
    "symbol",
    "asset_type",
    "name",
    "exchange",
    "industry",
    "board",
    "listed_on",
    "delisted_on",
    "is_st",
    "is_suspended",
    "status",
    "as_of_date",
)

_flag = partial(flag, default=None)
_UNIVERSE_ASSET_TYPES = frozenset(item.value for item in AssetType)


@dataclass(frozen=True, slots=True)
class PointInTimeImportResult:
    """Outcome of one immutable point-in-time CSV import."""

    as_of_date: date
    status: DataStatus
    row_count: int
    snapshot_id: int
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class PointInTimeUniverseImportResult:
    """Outcome of one immutable historical instrument-universe import."""

    as_of_date: date
    status: DataStatus
    row_count: int
    snapshot_id: int
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class PointInTimeCoverageReport:
    """Coverage and consistency audit for suspension/limit evidence."""

    status: DataStatus
    row_count: int
    symbol_count: int
    date_count: int
    missing_suspension_rows: int
    invalid_limit_rows: int
    duplicate_key_rows: int
    missing_dates: tuple[date, ...]
    issues: tuple[str, ...]


def load_point_in_time_prices(path: Path) -> pd.DataFrame:
    """Read and normalize a strict evidence CSV without writing any state."""

    if not isinstance(path, Path):
        raise TypeError("point-in-time CSV path must be Path")
    try:
        frame = pd.read_csv(path, dtype="string", keep_default_na=False)
    except FileNotFoundError:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
    ) as exc:
        raise ValueError(f"点时行情文件无法读取：{path}") from exc
    if tuple(str(column) for column in frame.columns) != POINT_IN_TIME_COLUMNS:
        raise ValueError("点时行情列必须严格为: " + ",".join(POINT_IN_TIME_COLUMNS))
    if frame.empty:
        raise ValueError("点时行情文件不能为空")

    result = frame.copy()
    result["symbol"] = result["symbol"].astype("string").str.strip().str.upper()
    if result["symbol"].eq("").any():
        raise ValueError("symbol不能为空")
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.date
    if result["trade_date"].isna().any():
        raise ValueError("trade_date必须是YYYY-MM-DD")
    if result.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("点时行情包含重复的symbol/trade_date")

    numeric_columns = ["open", "high", "low", "close", "volume", "amount"]
    numeric_columns.extend(["upper_limit", "lower_limit"])
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
        if not np.isfinite(result[column].to_numpy(dtype="float64", na_value=np.nan)).all():
            raise ValueError(f"{column}必须是有限数字")
    result["suspended"] = result["suspended"].map(_flag)
    if result["suspended"].isna().any():
        raise ValueError("suspended只能使用0/1、true/false或中英文状态值")
    result["suspended"] = result["suspended"].astype(bool)
    return (
        result.loc[:, POINT_IN_TIME_COLUMNS]
        .sort_values(["trade_date", "symbol"], kind="mergesort")
        .reset_index(drop=True)
    )


def import_point_in_time_prices(settings: Settings, path: Path) -> PointInTimeImportResult:
    """Snapshot, validate, and persist explicit historical bar evidence."""

    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    frame = load_point_in_time_prices(path)
    as_of_date = max(frame["trade_date"])
    database = Database(settings.database_path)
    database.initialize()
    writer = SnapshotWriter(settings.raw_snapshot_dir, "point_in_time_csv")
    snapshot = writer.write("point_in_time_prices", as_of_date, frame)
    validation = validate_dataset("point_in_time_prices", frame, as_of_date)
    snapshot_id = database.record_snapshot(snapshot, validation)
    if validation.status is DataStatus.VALID:
        persisted = frame.copy()
        persisted["source_snapshot_id"] = snapshot_id
        database.upsert_frame("daily_prices", persisted)
    return PointInTimeImportResult(
        as_of_date=as_of_date,
        status=validation.status,
        row_count=len(frame),
        snapshot_id=snapshot_id,
        issues=validation.issues,
    )


def clean_point_in_time_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize an already loaded evidence frame without inventing flags.

    Exact duplicate rows are removed.  Conflicting rows for the same symbol and
    date are rejected because choosing one would create an untraceable bar.
    Missing suspension or limit values remain invalid rather than being
    inferred from zero volume or unchanged prices.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    missing = [column for column in POINT_IN_TIME_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError("点时行情缺少列：" + ",".join(missing))
    if frame.empty:
        raise ValueError("点时行情不能为空")
    result = frame.loc[:, POINT_IN_TIME_COLUMNS].copy(deep=True)
    result["symbol"] = result["symbol"].astype("string").str.strip().str.upper()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.date
    if (
        result["symbol"].isna().any()
        or result["symbol"].eq("").any()
        or result["trade_date"].isna().any()
    ):
        raise ValueError("symbol/trade_date包含无效值")
    numeric = ["open", "high", "low", "close", "volume", "amount", "upper_limit", "lower_limit"]
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if not np.isfinite(result[numeric].to_numpy(dtype="float64", na_value=np.nan)).all():
        raise ValueError("点时行情数值列必须是有限数字，不能缺失")
    result["suspended"] = result["suspended"].map(_flag)
    if result["suspended"].isna().any():
        raise ValueError("suspended包含无法识别的状态")
    result["suspended"] = result["suspended"].astype(bool)
    duplicate_mask = result.duplicated(["symbol", "trade_date"], keep=False)
    if duplicate_mask.any():
        duplicate_groups = result.loc[duplicate_mask].groupby(["symbol", "trade_date"], sort=False)
        conflicting = [key for key, group in duplicate_groups if len(group.drop_duplicates()) > 1]
        if conflicting:
            raise ValueError("点时行情同一symbol/trade_date存在冲突记录")
        result = result.drop_duplicates().copy()
    invalid_limits = result["upper_limit"] < result["lower_limit"]
    if invalid_limits.any():
        raise ValueError("upper_limit不能低于lower_limit")
    return result.sort_values(["trade_date", "symbol"], kind="mergesort").reset_index(drop=True)


def audit_point_in_time_coverage(
    frame: pd.DataFrame,
    *,
    expected_dates: Sequence[date] | None = None,
) -> PointInTimeCoverageReport:
    """Audit explicit PIT flags and identify missing trading-date coverage."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    issues: list[str] = []
    raw_missing_suspension = 0
    if "suspended" in frame:
        raw_missing_suspension = int(frame["suspended"].map(_flag).isna().sum())
    raw_invalid_limits = 0
    if {"upper_limit", "lower_limit"}.issubset(frame.columns):
        upper = pd.to_numeric(frame["upper_limit"], errors="coerce")
        lower = pd.to_numeric(frame["lower_limit"], errors="coerce")
        raw_invalid_limits = int(
            (
                ~np.isfinite(upper.to_numpy(dtype="float64", na_value=np.nan))
                | ~np.isfinite(lower.to_numpy(dtype="float64", na_value=np.nan))
                | upper.le(0)
                | lower.le(0)
                | upper.lt(lower)
            ).sum()
        )
    try:
        cleaned = clean_point_in_time_prices(frame)
    except ValueError as exc:
        return PointInTimeCoverageReport(
            status=DataStatus.BLOCKED,
            row_count=len(frame),
            symbol_count=int(frame["symbol"].nunique()) if "symbol" in frame else 0,
            date_count=int(frame["trade_date"].nunique()) if "trade_date" in frame else 0,
            missing_suspension_rows=raw_missing_suspension,
            invalid_limit_rows=raw_invalid_limits,
            duplicate_key_rows=int(frame.duplicated(["symbol", "trade_date"]).sum())
            if {"symbol", "trade_date"}.issubset(frame.columns)
            else 0,
            missing_dates=(),
            issues=(str(exc),),
        )
    missing_suspension = int(cleaned["suspended"].isna().sum())
    invalid_limits = int(
        (
            cleaned["upper_limit"].le(0)
            | cleaned["lower_limit"].le(0)
            | cleaned["upper_limit"].lt(cleaned["lower_limit"])
        ).sum()
    )
    dates = tuple(sorted(set(cleaned["trade_date"])))
    validation = validate_dataset("point_in_time_prices", cleaned, max(dates))
    issues.extend(issue.message for issue in validation.issues)
    missing_dates: tuple[date, ...] = ()
    if expected_dates is not None:
        requested = {item for item in expected_dates if isinstance(item, date)}
        missing_dates = tuple(sorted(requested - set(dates)))
        if missing_dates:
            issues.append(f"缺少{len(missing_dates)}个目标交易日")
    if missing_suspension:
        issues.append("存在缺失停牌标记")
    if invalid_limits:
        issues.append("存在上下限价倒挂记录")
    status = DataStatus.VALID if not issues else DataStatus.BLOCKED
    return PointInTimeCoverageReport(
        status=status,
        row_count=len(cleaned),
        symbol_count=int(cleaned["symbol"].nunique()),
        date_count=len(dates),
        missing_suspension_rows=missing_suspension,
        invalid_limit_rows=invalid_limits,
        duplicate_key_rows=0,
        missing_dates=missing_dates,
        issues=tuple(dict.fromkeys(issues)),
    )


def load_point_in_time_universe(path: Path) -> pd.DataFrame:
    """Read historical instrument metadata with one row per symbol and date."""

    if not isinstance(path, Path):
        raise TypeError("point-in-time universe path must be Path")
    try:
        frame = pd.read_csv(path, dtype="string", keep_default_na=False)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError(f"点时股票池文件无法读取：{path}") from exc
    if tuple(str(column) for column in frame.columns) != POINT_IN_TIME_UNIVERSE_COLUMNS:
        raise ValueError("点时股票池列必须严格为: " + ",".join(POINT_IN_TIME_UNIVERSE_COLUMNS))
    if frame.empty:
        raise ValueError("点时股票池文件不能为空")

    result = frame.copy()
    result["symbol"] = result["symbol"].astype("string").str.strip().str.upper()
    if result["symbol"].eq("").any():
        raise ValueError("symbol不能为空")
    result["asset_type"] = result["asset_type"].astype("string").str.strip().str.lower()
    if (~result["asset_type"].isin(_UNIVERSE_ASSET_TYPES)).any():
        raise ValueError("asset_type包含不支持的资产类型")
    result["name"] = result["name"].astype("string").str.strip()
    if result["name"].eq("").any():
        raise ValueError("name不能为空")
    for column in ("listed_on", "delisted_on", "as_of_date"):
        parsed = pd.to_datetime(result[column], errors="coerce")
        if column == "as_of_date" and parsed.isna().any():
            raise ValueError(f"{column}必须是YYYY-MM-DD")
        if column == "listed_on":
            supplied = result[column].astype("string").str.strip().ne("")
            if (supplied & parsed.isna()).any():
                raise ValueError("listed_on必须是YYYY-MM-DD或留空")
            missing_stock_listing = parsed.isna() & result["asset_type"].eq("stock")
            if missing_stock_listing.any():
                raise ValueError("股票的listed_on必须是YYYY-MM-DD")
        if column == "delisted_on":
            supplied = result[column].astype("string").str.strip().ne("")
            if (supplied & parsed.isna()).any():
                raise ValueError("delisted_on必须是YYYY-MM-DD或留空")
        result[column] = parsed.dt.date
    for column in ("is_st", "is_suspended"):
        result[column] = result[column].map(_flag)
        if result[column].isna().any():
            raise ValueError(f"{column}只能使用0/1、true/false或中英文状态值")
        result[column] = result[column].astype(bool)
    if result.duplicated(["symbol", "as_of_date"]).any():
        raise ValueError("点时股票池包含重复的symbol/as_of_date")
    invalid_listing = result["listed_on"] > result["as_of_date"]
    if invalid_listing.any():
        raise ValueError("listed_on不能晚于as_of_date")
    invalid_delisting = result["delisted_on"].notna() & (
        result["delisted_on"] <= result["as_of_date"]
    )
    if invalid_delisting.any():
        raise ValueError("delisted_on必须晚于as_of_date")
    return (
        result.loc[:, POINT_IN_TIME_UNIVERSE_COLUMNS]
        .sort_values(["as_of_date", "symbol"], kind="mergesort")
        .reset_index(drop=True)
    )


def import_point_in_time_universe(
    settings: Settings, path: Path
) -> PointInTimeUniverseImportResult:
    """Snapshot, validate, and persist historical instrument metadata."""

    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    frame = load_point_in_time_universe(path)
    as_of_date = max(frame["as_of_date"])
    database = Database(settings.database_path)
    database.initialize()
    writer = SnapshotWriter(settings.raw_snapshot_dir, "point_in_time_csv")
    snapshot = writer.write("point_in_time_universe", as_of_date, frame)
    validation = _validate_point_in_time_universe(frame, as_of_date)
    snapshot_id = database.record_snapshot(snapshot, validation)
    if validation.status is DataStatus.VALID:
        database.upsert_frame("instrument_history", frame)
    return PointInTimeUniverseImportResult(
        as_of_date=as_of_date,
        status=validation.status,
        row_count=len(frame),
        snapshot_id=snapshot_id,
        issues=validation.issues,
    )


def _validate_point_in_time_universe(frame: pd.DataFrame, expected_date: date) -> ValidationResult:
    issues: list[ValidationIssue] = []
    if frame["as_of_date"].gt(expected_date).any():
        issues.append(
            ValidationIssue(
                "future_date",
                f"as_of_date不能晚于{expected_date.isoformat()}",
                True,
            )
        )
    stocks = frame.loc[frame["asset_type"].astype(str).eq("stock")]
    if stocks.empty:
        issues.append(ValidationIssue("stock_universe_empty", "点时股票池没有股票行", True))
    return ValidationResult(
        "point_in_time_universe",
        DataStatus.BLOCKED if issues else DataStatus.VALID,
        tuple(issues),
    )


__all__ = [
    "POINT_IN_TIME_COLUMNS",
    "POINT_IN_TIME_UNIVERSE_COLUMNS",
    "PointInTimeImportResult",
    "PointInTimeCoverageReport",
    "PointInTimeUniverseImportResult",
    "import_point_in_time_prices",
    "import_point_in_time_universe",
    "load_point_in_time_prices",
    "load_point_in_time_universe",
    "clean_point_in_time_prices",
    "audit_point_in_time_coverage",
]
