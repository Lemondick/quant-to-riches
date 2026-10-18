"""Fail-closed monitoring of disclosed fund holding style drift.

Only public report-period holdings are compared.  The monitor stores the
observed rows immutably, computes weighted overlap against the immediately
preceding report, and emits a review event.  It never changes a portfolio,
creates an order, or feeds a score into the daily decision.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.base import ProviderError
from quant_assistant.providers.fund_detail import FundDetail, FundHolding
from quant_assistant.providers.fund_valuation import normalize_fund_symbol

if TYPE_CHECKING:
    from quant_assistant.config import Settings


STYLE_INFO_OVERLAP_THRESHOLD = Decimal("0.85")
STYLE_WARNING_OVERLAP_THRESHOLD = Decimal("0.70")
STYLE_CRITICAL_OVERLAP_THRESHOLD = Decimal("0.50")
MAX_DIFF_HOLDINGS = 5


@dataclass(frozen=True, slots=True)
class FundHoldingsSnapshot:
    """One immutable public report-period holdings observation."""

    symbol: str
    report_date: date
    source: str
    observed_at: datetime
    holdings: tuple[FundHolding, ...]
    source_snapshot_id: int | None = None


@dataclass(frozen=True, slots=True)
class StyleDriftComparison:
    """Weighted overlap and additions/removals between two reports."""

    previous_report_date: date
    report_date: date
    overlap_ratio: Decimal
    added_holdings: tuple[str, ...]
    removed_holdings: tuple[str, ...]
    severity: str
    drift_detected: bool


@dataclass(frozen=True, slots=True)
class FundStyleDriftEvent:
    """Persisted comparison suitable for review in every client."""

    id: int
    symbol: str
    report_date: date
    previous_report_date: date
    severity: str
    overlap_ratio: Decimal
    message: str
    added_holdings: tuple[str, ...]
    removed_holdings: tuple[str, ...]
    drift_detected: bool
    source: str
    details: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FundStyleSyncItem:
    symbol: str
    status: DataStatus
    event_count: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FundStyleSyncResult:
    as_of_date: date
    items: tuple[FundStyleSyncItem, ...]

    @property
    def ready(self) -> bool:
        return bool(self.items) and all(item.status is DataStatus.VALID for item in self.items)


class FundStyleDriftMonitor:
    """Persist holdings snapshots and compare adjacent report periods."""

    def __init__(
        self,
        database: Database | Path,
        *,
        info_overlap_threshold: Decimal = STYLE_INFO_OVERLAP_THRESHOLD,
        warning_overlap_threshold: Decimal = STYLE_WARNING_OVERLAP_THRESHOLD,
        critical_overlap_threshold: Decimal = STYLE_CRITICAL_OVERLAP_THRESHOLD,
    ) -> None:
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        self.info_overlap_threshold = _decimal_ratio(
            info_overlap_threshold, "info_overlap_threshold"
        )
        self.warning_overlap_threshold = _decimal_ratio(
            warning_overlap_threshold, "warning_overlap_threshold"
        )
        self.critical_overlap_threshold = _decimal_ratio(
            critical_overlap_threshold, "critical_overlap_threshold"
        )
        if not (
            self.critical_overlap_threshold
            < self.warning_overlap_threshold
            < self.info_overlap_threshold
            <= Decimal("1")
        ):
            raise ValueError("style overlap thresholds must satisfy critical < warning < info <= 1")

    def record_snapshot(self, snapshot: FundHoldingsSnapshot) -> tuple[FundStyleDriftEvent, ...]:
        """Insert one report period and return its newly created comparison."""

        current = normalize_holdings_snapshot(snapshot)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM fund_holding_history WHERE symbol = ? AND report_date = ? LIMIT 1",
                [current.symbol, current.report_date],
            ).fetchone()
            if existing is not None:
                return ()
            connection.executemany(
                """
                INSERT INTO fund_holding_history (
                    symbol, report_date, holding_symbol, holding_name, asset_type,
                    weight, source, observed_at, source_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [_holding_parameters(current, item) for item in current.holdings],
            )
            previous_header = connection.execute(
                """
                SELECT report_date FROM fund_holding_history
                WHERE symbol = ? AND report_date < ?
                ORDER BY report_date DESC LIMIT 1
                """,
                [current.symbol, current.report_date],
            ).fetchone()
            if previous_header is None:
                return ()
            previous_report_date = _required_date(previous_header[0], "previous_report_date")
            previous_rows = connection.execute(
                """
                SELECT holding_symbol, holding_name, asset_type, weight
                FROM fund_holding_history
                WHERE symbol = ? AND report_date = ?
                """,
                [current.symbol, previous_report_date],
            ).fetchall()
            previous = _holdings_from_rows(previous_rows, previous_report_date)
            comparison = compare_holdings(
                previous,
                current,
                info_overlap_threshold=self.info_overlap_threshold,
                warning_overlap_threshold=self.warning_overlap_threshold,
                critical_overlap_threshold=self.critical_overlap_threshold,
            )
            event = _event_payload(current, comparison)
            details_json = json.dumps(
                dict(event["details"]), ensure_ascii=False, sort_keys=True, default=str
            )
            row = connection.execute(
                """
                INSERT INTO fund_style_drift_events (
                    symbol, report_date, previous_report_date, severity, overlap_ratio,
                    message, added_json, removed_json, source, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol, report_date) DO NOTHING
                RETURNING id
                """,
                [
                    event["symbol"],
                    event["report_date"],
                    event["previous_report_date"],
                    event["severity"],
                    float(event["overlap_ratio"]),
                    event["message"],
                    json.dumps(event["added_holdings"], ensure_ascii=False),
                    json.dumps(event["removed_holdings"], ensure_ascii=False),
                    event["source"],
                    details_json,
                    event["created_at"],
                ],
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT id FROM fund_style_drift_events
                    WHERE symbol = ? AND report_date = ?
                    """,
                    [current.symbol, current.report_date],
                ).fetchone()
            if row is None:
                raise RuntimeError("style drift event insert did not return an id")
            return (_event_from_payload(int(row[0]), event),)


def snapshot_from_detail(detail: FundDetail) -> FundHoldingsSnapshot:
    """Convert a non-stale public fund detail into a holdings snapshot."""

    if not isinstance(detail, FundDetail):
        raise TypeError("detail must be FundDetail")
    if detail.stale:
        raise ValueError("fund detail is stale; holdings evidence is blocked")
    if detail.holdings_report_date is None:
        raise ValueError("fund detail has no holdings report date")
    return normalize_holdings_snapshot(
        FundHoldingsSnapshot(
            symbol=detail.symbol,
            report_date=detail.holdings_report_date,
            source=detail.source,
            observed_at=detail.observed_at,
            holdings=detail.holdings,
        )
    )


def normalize_holdings_snapshot(snapshot: FundHoldingsSnapshot) -> FundHoldingsSnapshot:
    """Validate and canonicalize report holdings before persistence."""

    if not isinstance(snapshot, FundHoldingsSnapshot):
        raise TypeError("snapshot must be FundHoldingsSnapshot")
    symbol = normalize_fund_symbol(snapshot.symbol)
    if not isinstance(snapshot.report_date, date) or isinstance(snapshot.report_date, datetime):
        raise TypeError("snapshot report_date must be a date")
    source = _required_text(snapshot.source, "source")
    if not isinstance(snapshot.observed_at, datetime):
        raise TypeError("snapshot observed_at must be datetime")
    observed_at = snapshot.observed_at
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("snapshot observed_at must be timezone-aware")
    if not isinstance(snapshot.holdings, Sequence) or isinstance(snapshot.holdings, (str, bytes)):
        raise TypeError("snapshot holdings must be a sequence")
    normalized: list[FundHolding] = []
    seen: set[tuple[str, str]] = set()
    for holding in snapshot.holdings:
        if not isinstance(holding, FundHolding):
            raise TypeError("snapshot holdings must contain FundHolding values")
        holding_symbol = _required_text(holding.symbol, "holding symbol").upper()
        name = _required_text(holding.name, "holding name")
        asset_type = _required_text(holding.asset_type, "holding asset_type").lower()
        if asset_type not in {"stock", "bond"}:
            raise ValueError("holding asset_type must be stock or bond")
        key = (asset_type, holding_symbol)
        if key in seen:
            raise ValueError(f"duplicate disclosed holding: {asset_type}:{holding_symbol}")
        seen.add(key)
        weight = _decimal_nonnegative(holding.weight, "holding weight")
        if weight > Decimal("100"):
            raise ValueError("holding weight must not exceed 100")
        normalized.append(
            FundHolding(holding_symbol, name, asset_type, weight, snapshot.report_date)
        )
    if not normalized:
        raise ValueError("fund detail has no disclosed holdings")
    normalized.sort(key=lambda item: (item.weight, item.asset_type, item.symbol), reverse=True)
    source_snapshot_id = snapshot.source_snapshot_id
    if source_snapshot_id is not None:
        if isinstance(source_snapshot_id, bool) or not isinstance(source_snapshot_id, int):
            raise TypeError("source_snapshot_id must be an integer or None")
        if source_snapshot_id < 0:
            raise ValueError("source_snapshot_id must be non-negative")
    return FundHoldingsSnapshot(
        symbol=symbol,
        report_date=snapshot.report_date,
        source=source,
        observed_at=observed_at.astimezone(SHANGHAI_TZ),
        holdings=tuple(normalized),
        source_snapshot_id=source_snapshot_id,
    )


def compare_holdings(
    previous: Sequence[FundHolding],
    current: FundHoldingsSnapshot,
    *,
    info_overlap_threshold: Decimal = STYLE_INFO_OVERLAP_THRESHOLD,
    warning_overlap_threshold: Decimal = STYLE_WARNING_OVERLAP_THRESHOLD,
    critical_overlap_threshold: Decimal = STYLE_CRITICAL_OVERLAP_THRESHOLD,
) -> StyleDriftComparison:
    """Compare weighted holdings and classify the overlap conservatively."""

    previous_map = _holding_map(previous)
    current_map = _holding_map(current.holdings)
    union = set(previous_map) | set(current_map)
    denominator = sum(
        (
            max(previous_map.get(key, Decimal("0")), current_map.get(key, Decimal("0")))
            for key in union
        ),
        Decimal("0"),
    )
    numerator = sum(
        (
            min(previous_map.get(key, Decimal("0")), current_map.get(key, Decimal("0")))
            for key in union
        ),
        Decimal("0"),
    )
    overlap = Decimal("0") if denominator == 0 else numerator / denominator
    if overlap < critical_overlap_threshold:
        severity, drift = "critical", True
    elif overlap < warning_overlap_threshold:
        severity, drift = "warning", True
    elif overlap < info_overlap_threshold:
        severity, drift = "info", True
    else:
        severity, drift = "info", False
    added = tuple(
        _holding_label(current_map, key)
        for key in sorted(
            set(current_map) - set(previous_map), key=lambda value: current_map[value], reverse=True
        )[:MAX_DIFF_HOLDINGS]
    )
    removed = tuple(
        _holding_label(previous_map, key)
        for key in sorted(
            set(previous_map) - set(current_map),
            key=lambda value: previous_map[value],
            reverse=True,
        )[:MAX_DIFF_HOLDINGS]
    )
    return StyleDriftComparison(
        previous_report_date=_required_date(
            previous[0].report_date if previous else current.report_date, "previous_report_date"
        ),
        report_date=current.report_date,
        overlap_ratio=overlap,
        added_holdings=added,
        removed_holdings=removed,
        severity=severity,
        drift_detected=drift,
    )


def sync_fund_style(
    settings: Settings,
    provider: object,
    as_of_date: date,
    symbols: Sequence[str],
) -> FundStyleSyncResult:
    """Fetch public report holdings and persist style-overlap evidence."""

    if not hasattr(settings, "database_path"):
        raise TypeError("settings must provide database_path")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")
    detail = getattr(provider, "detail", None)
    if not callable(detail):
        raise TypeError("fund style provider must provide detail()")
    selected = tuple(
        dict.fromkeys(normalize_fund_symbol(item) for item in symbols if str(item).strip())
    )
    if not selected:
        return FundStyleSyncResult(as_of_date, ())
    monitor = FundStyleDriftMonitor(settings.database_path)
    items: list[FundStyleSyncItem] = []
    for symbol in selected:
        try:
            snapshot = snapshot_from_detail(detail(symbol))
            if snapshot.report_date > as_of_date:
                raise ValueError("holdings report date is after observation date")
            events = monitor.record_snapshot(snapshot)
        except (ProviderError, ConnectionError, OSError, TypeError, ValueError) as exc:
            items.append(
                FundStyleSyncItem(symbol, DataStatus.BLOCKED, error=f"{type(exc).__name__}: {exc}")
            )
        else:
            items.append(FundStyleSyncItem(symbol, DataStatus.VALID, len(events)))
    return FundStyleSyncResult(as_of_date, tuple(items))


def _holding_parameters(snapshot: FundHoldingsSnapshot, holding: FundHolding) -> list[object]:
    return [
        snapshot.symbol,
        snapshot.report_date,
        holding.symbol,
        holding.name,
        holding.asset_type,
        float(holding.weight),
        snapshot.source,
        snapshot.observed_at,
        snapshot.source_snapshot_id,
    ]


def _holdings_from_rows(
    rows: Sequence[Sequence[object]], report_date: date
) -> tuple[FundHolding, ...]:
    result: list[FundHolding] = []
    for row in rows:
        result.append(
            FundHolding(
                str(row[0]),
                str(row[1]),
                str(row[2]),
                Decimal(str(row[3])),
                report_date,
            )
        )
    return tuple(result)


def _holding_map(holdings: Sequence[FundHolding]) -> dict[tuple[str, str], Decimal]:
    return {
        (item.asset_type.lower(), item.symbol.upper()): _decimal_nonnegative(item.weight, "weight")
        for item in holdings
    }


def _holding_label(holdings: Mapping[tuple[str, str], Decimal], key: tuple[str, str]) -> str:
    return f"{key[0]}:{key[1]} ({holdings[key]:.2f}%)"


def _event_payload(
    snapshot: FundHoldingsSnapshot, comparison: StyleDriftComparison
) -> dict[str, object]:
    ratio_text = f"{comparison.overlap_ratio:.1%}"
    if comparison.drift_detected:
        message = (
            f"基金{snapshot.symbol}报告期{snapshot.report_date.isoformat()}披露持仓重叠度"
            f"{ratio_text}，检测到{comparison.severity}级风格漂移，请人工复核"
        )
    else:
        message = (
            f"基金{snapshot.symbol}报告期{snapshot.report_date.isoformat()}披露持仓重叠度"
            f"{ratio_text}，未达到风格漂移阈值"
        )
    details = {
        "previous_report_date": comparison.previous_report_date.isoformat(),
        "current_report_date": comparison.report_date.isoformat(),
        "overlap_ratio": str(comparison.overlap_ratio),
        "added_holdings": list(comparison.added_holdings),
        "removed_holdings": list(comparison.removed_holdings),
        "drift_detected": comparison.drift_detected,
    }
    return {
        "symbol": snapshot.symbol,
        "report_date": comparison.report_date,
        "previous_report_date": comparison.previous_report_date,
        "severity": comparison.severity,
        "overlap_ratio": comparison.overlap_ratio,
        "message": message,
        "added_holdings": comparison.added_holdings,
        "removed_holdings": comparison.removed_holdings,
        "drift_detected": comparison.drift_detected,
        "source": snapshot.source,
        "details": details,
        "created_at": snapshot.observed_at,
    }


def _event_from_payload(event_id: int, payload: Mapping[str, object]) -> FundStyleDriftEvent:
    details = payload["details"]
    if not isinstance(details, Mapping):
        raise TypeError("style drift details must be a mapping")
    return FundStyleDriftEvent(
        id=event_id,
        symbol=str(payload["symbol"]),
        report_date=payload["report_date"],  # type: ignore[arg-type]
        previous_report_date=payload["previous_report_date"],  # type: ignore[arg-type]
        severity=str(payload["severity"]),
        overlap_ratio=payload["overlap_ratio"],  # type: ignore[arg-type]
        message=str(payload["message"]),
        added_holdings=tuple(str(item) for item in payload["added_holdings"]),  # type: ignore[arg-type]
        removed_holdings=tuple(str(item) for item in payload["removed_holdings"]),  # type: ignore[arg-type]
        drift_detected=bool(payload["drift_detected"]),
        source=str(payload["source"]),
        details=dict(details),
        created_at=payload["created_at"],  # type: ignore[arg-type]
    )


def _required_date(value: object, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise ValueError(f"{name} must be a date")
    return value


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _decimal_ratio(value: object, name: str) -> Decimal:
    converted = _decimal_nonnegative(value, name)
    if converted <= 0 or converted > 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return converted


def _decimal_nonnegative(value: object, name: str) -> Decimal:
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not converted.is_finite() or converted < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return converted


__all__ = [
    "FundHoldingsSnapshot",
    "FundStyleDriftEvent",
    "FundStyleDriftMonitor",
    "FundStyleSyncItem",
    "FundStyleSyncResult",
    "MAX_DIFF_HOLDINGS",
    "STYLE_CRITICAL_OVERLAP_THRESHOLD",
    "STYLE_INFO_OVERLAP_THRESHOLD",
    "STYLE_WARNING_OVERLAP_THRESHOLD",
    "StyleDriftComparison",
    "compare_holdings",
    "normalize_holdings_snapshot",
    "snapshot_from_detail",
    "sync_fund_style",
]
