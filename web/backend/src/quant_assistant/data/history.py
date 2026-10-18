"""Resumable, fail-closed initialization of point-in-time market history."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from quant_assistant.config import Settings
from quant_assistant.data.pipeline import (
    DEFAULT_RISK_INDEXES,
    _persist_normalized_dataset,
    _point_in_time_instruments,
    _with_system_instruments,
)
from quant_assistant.data.snapshots import SnapshotWriter
from quant_assistant.data.validation import (
    ValidationIssue,
    ValidationResult,
    validate_dataset,
)
from quant_assistant.models.entities import DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.base import DailyMarketProvider, ProviderError

MINIMUM_PRICE_SESSIONS = 200
MINIMUM_LISTING_AGE_DAYS = 300


@dataclass(frozen=True, slots=True)
class HistoryDatasetResult:
    dataset: str
    symbol: str
    status: DataStatus
    row_count: int
    snapshot_id: int | None
    skipped: bool = False
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class HistoryBootstrapResult:
    start_date: date
    end_date: date
    items: tuple[HistoryDatasetResult, ...]

    @property
    def ready(self) -> bool:
        required = [item for item in self.items if item.dataset != "fund_history"]
        return bool(required) and all(item.status is DataStatus.VALID for item in required)

    @property
    def failed(self) -> tuple[HistoryDatasetResult, ...]:
        return tuple(item for item in self.items if item.status is not DataStatus.VALID)


def initialize_market_history(
    settings: Settings,
    provider: DailyMarketProvider,
    start_date: date,
    end_date: date,
    *,
    symbols: Sequence[str] = (),
    fund_symbols: Sequence[str] = (),
    progress: Callable[[HistoryDatasetResult, int, int], None] | None = None,
) -> HistoryBootstrapResult:
    """Fetch enough immutable history for production decisions and backtests.

    Completed, content-addressed symbol snapshots are skipped on a repeated run.
    Invalid or incomplete slices are snapshotted for diagnosis but never persisted.
    """

    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    db = Database(settings.database_path)
    db.initialize()
    writer = SnapshotWriter(settings.raw_snapshot_dir, provider.name)

    instruments = provider.fetch_stock_universe()
    instruments = _point_in_time_instruments(instruments, end_date)
    instruments = _with_system_instruments(instruments, end_date)
    instrument_snapshot = writer.write("instruments", end_date, instruments)
    instrument_validation = validate_dataset("instruments", instruments, end_date)
    instrument_snapshot_id = db.record_snapshot(instrument_snapshot, instrument_validation)
    if instrument_validation.status is not DataStatus.VALID:
        messages = "; ".join(issue.message for issue in instrument_validation.issues)
        raise ValueError(f"instrument universe failed validation: {messages}")
    _persist_normalized_dataset(db, "instruments", end_date, instruments, instrument_snapshot_id)

    selected = _select_stock_symbols(instruments, end_date, symbols)
    funds = _unique_symbols(fund_symbols)
    total = len(selected) + len(DEFAULT_RISK_INDEXES) + len(funds)
    items: list[HistoryDatasetResult] = []
    for symbol in selected:
        item = _load_price_history(
            db,
            writer,
            provider,
            symbol,
            start_date,
            end_date,
            is_index=False,
        )
        items.append(item)
        if progress is not None:
            progress(item, len(items), total)
    for symbol in DEFAULT_RISK_INDEXES:
        item = _load_price_history(
            db,
            writer,
            provider,
            symbol,
            start_date,
            end_date,
            is_index=True,
        )
        items.append(item)
        if progress is not None:
            progress(item, len(items), total)
    for symbol in funds:
        item = _load_fund_history(db, writer, provider, symbol, start_date, end_date)
        items.append(item)
        if progress is not None:
            progress(item, len(items), total)
    return HistoryBootstrapResult(start_date, end_date, tuple(items))


def _load_price_history(
    db: Database,
    writer: SnapshotWriter,
    provider: DailyMarketProvider,
    symbol: str,
    start_date: date,
    end_date: date,
    *,
    is_index: bool,
) -> HistoryDatasetResult:
    dataset = "index_history" if is_index else "stock_history"
    snapshot_dataset = f"{dataset}_{_safe_symbol(symbol)}"
    source_prefix = getattr(provider, "history_snapshot_source_prefix", None)
    skipped = _completed_snapshot(
        db, snapshot_dataset, writer.source, end_date, source_prefix=source_prefix
    )
    if skipped is not None and _stored_price_sessions(db, symbol, start_date, end_date) >= (
        MINIMUM_PRICE_SESSIONS
    ):
        return HistoryDatasetResult(
            dataset, symbol, DataStatus.VALID, skipped[1], skipped[0], skipped=True
        )

    try:
        frame = (
            provider.fetch_index_history((symbol,), start_date, end_date)
            if is_index
            else provider.fetch_stock_history(symbol, start_date, end_date)
        )
    except ProviderError as error:
        return HistoryDatasetResult(
            dataset,
            symbol,
            DataStatus.BLOCKED,
            0,
            None,
            issues=(ValidationIssue("provider_error", str(error), True),),
        )
    frame = _slice_dates(frame, "trade_date", start_date, end_date)
    actual_source = getattr(provider, "last_stock_history_source", None) if not is_index else None
    snapshot_writer = SnapshotWriter(writer.root, actual_source) if actual_source else writer
    snapshot = snapshot_writer.write(snapshot_dataset, end_date, frame)
    validation = _minimum_history_validation(
        validate_dataset("stock_history", frame, end_date), len(frame), symbol
    )
    snapshot_id = db.record_snapshot(snapshot, validation)
    if validation.status is DataStatus.VALID:
        prices = frame.copy()
        prices["source_snapshot_id"] = snapshot_id
        db.upsert_frame("daily_prices", prices)
    return HistoryDatasetResult(
        dataset,
        symbol,
        validation.status,
        len(frame),
        snapshot_id,
        issues=validation.issues,
    )


def _load_fund_history(
    db: Database,
    writer: SnapshotWriter,
    provider: DailyMarketProvider,
    symbol: str,
    start_date: date,
    end_date: date,
) -> HistoryDatasetResult:
    dataset = "fund_history"
    snapshot_dataset = f"{dataset}_{_safe_symbol(symbol)}"
    skipped = _completed_snapshot(db, snapshot_dataset, writer.source, end_date)
    if skipped is not None:
        return HistoryDatasetResult(
            dataset, symbol, DataStatus.VALID, skipped[1], skipped[0], skipped=True
        )
    try:
        frame = provider.fetch_fund_nav(symbol)
    except ProviderError as error:
        return HistoryDatasetResult(
            dataset,
            symbol,
            DataStatus.BLOCKED,
            0,
            None,
            issues=(ValidationIssue("provider_error", str(error), True),),
        )
    frame = _slice_dates(frame, "nav_date", start_date, end_date)
    snapshot = writer.write(snapshot_dataset, end_date, frame)
    validation = validate_dataset("fund_history", frame, end_date)
    snapshot_id = db.record_snapshot(snapshot, validation)
    if validation.status is DataStatus.VALID:
        nav = frame.copy()
        nav["source_snapshot_id"] = snapshot_id
        db.upsert_frame("fund_nav", nav)
    return HistoryDatasetResult(
        dataset,
        symbol,
        validation.status,
        len(frame),
        snapshot_id,
        issues=validation.issues,
    )


def _minimum_history_validation(
    result: ValidationResult, row_count: int, symbol: str
) -> ValidationResult:
    if result.status is not DataStatus.VALID or row_count >= MINIMUM_PRICE_SESSIONS:
        return result
    issue = ValidationIssue(
        "insufficient_history",
        f"{symbol} returned {row_count} sessions; at least {MINIMUM_PRICE_SESSIONS} are required",
        True,
    )
    return ValidationResult(result.dataset, DataStatus.BLOCKED, (*result.issues, issue))


def _select_stock_symbols(
    instruments: pd.DataFrame, end_date: date, requested: Sequence[str]
) -> tuple[str, ...]:
    rows = instruments.loc[instruments["asset_type"].astype(str).eq("stock")].copy()
    symbols = rows["symbol"].astype(str).str.upper()
    listed_on = pd.to_datetime(rows["listed_on"], errors="coerce")
    eligible = (
        symbols.str.match(r"^(?:60[0135]\d{3}\.SH|00[0123]\d{3}\.SZ)$", na=False)
        & rows["board"].astype("string").str.lower().isin({"main", "main_board", "主板"})
        & listed_on.notna()
        & (pd.Timestamp(end_date) - listed_on).dt.days.ge(MINIMUM_LISTING_AGE_DAYS)
        & rows["industry"].notna()
        & rows["industry"].astype(str).str.strip().ne("")
    )
    available = tuple(sorted(symbols.loc[eligible].unique()))
    if not requested:
        return available
    by_code = {symbol.split(".", maxsplit=1)[0]: symbol for symbol in available}
    normalized: list[str] = []
    for raw in _unique_symbols(requested):
        match = raw.upper() if "." in raw else by_code.get(raw.zfill(6))
        if match not in available:
            raise ValueError(f"stock symbol is unknown or not strategy-eligible: {raw}")
        normalized.append(match)
    return tuple(sorted(set(normalized)))


def _slice_dates(frame: pd.DataFrame, column: str, start: date, end: date) -> pd.DataFrame:
    if column not in frame:
        return frame.copy(deep=True)
    dates = pd.to_datetime(frame[column], errors="coerce")
    return frame.loc[
        dates.notna() & dates.ge(pd.Timestamp(start)) & dates.le(pd.Timestamp(end))
    ].reset_index(drop=True)


def _completed_snapshot(
    db: Database,
    dataset: str,
    source: str,
    as_of_date: date,
    *,
    source_prefix: str | None = None,
) -> tuple[int, int] | None:
    source_filter = "source LIKE ?" if source_prefix else "source = ?"
    rows = db.query_df(
        f"""
        SELECT id, row_count FROM data_snapshots
        WHERE dataset = ? AND {source_filter} AND as_of_date = ? AND status = 'valid'
        ORDER BY id DESC LIMIT 1
        """,
        [dataset, f"{source_prefix}%" if source_prefix else source, as_of_date],
    )
    if rows.empty:
        return None
    return int(rows.iloc[0]["id"]), int(rows.iloc[0]["row_count"])


def _stored_price_sessions(db: Database, symbol: str, start: date, end: date) -> int:
    rows = db.query_df(
        """
        SELECT count(DISTINCT trade_date) AS sessions FROM daily_prices
        WHERE symbol = ? AND trade_date BETWEEN ? AND ?
        """,
        [symbol, start, end],
    )
    return int(rows.iloc[0]["sessions"])


def _unique_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({str(symbol).strip() for symbol in symbols if str(symbol).strip()}))


def _safe_symbol(symbol: str) -> str:
    return str(symbol).replace(".", "_").replace("-", "_")
