"""Fund lifecycle evidence, change detection, and read-only alert events.

The monitor deliberately treats public fund profile data as observations rather
than facts about the future.  It stores one profile per fund/date, compares only
with the immediately preceding observation, and emits explainable events.  A
scale threshold is a review heuristic; it is never a liquidation conclusion or
an input to the trading decision.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.base import ProviderError
from quant_assistant.providers.fund_detail import FundDetail, FundFee
from quant_assistant.providers.fund_valuation import normalize_fund_symbol

if TYPE_CHECKING:
    from quant_assistant.config import Settings


SCALE_DROP_WARNING_RATIO = Decimal("0.20")
SCALE_DROP_CRITICAL_RATIO = Decimal("0.50")
LOW_SCALE_THRESHOLD = Decimal("50000000")
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_RESTRICTED_STATUS_WORDS = ("暂停", "关闭", "停止", "不开放", "封闭", "限购", "限大额", "限额")


@dataclass(frozen=True, slots=True)
class FundLifecycleSnapshot:
    """One normalized public profile observation for a fund/date."""

    symbol: str
    as_of_date: date
    source: str
    observed_at: datetime
    manager_names: tuple[str, ...] = ()
    scale_text: str | None = None
    scale_value: Decimal | None = None
    scale_unit: str | None = None
    purchase_status: str | None = None
    redemption_status: str | None = None
    fees: tuple[FundFee, ...] = ()
    source_snapshot_id: int | None = None


@dataclass(frozen=True, slots=True)
class FundLifecycleEvent:
    """An explainable lifecycle change emitted from two observations."""

    id: int
    symbol: str
    observed_on: date
    event_type: str
    severity: str
    message: str
    previous_value: str | None
    current_value: str | None
    source: str
    details: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FundLifecycleSyncItem:
    symbol: str
    status: DataStatus
    event_count: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FundLifecycleSyncResult:
    as_of_date: date
    items: tuple[FundLifecycleSyncItem, ...]

    @property
    def ready(self) -> bool:
        return bool(self.items) and all(item.status is DataStatus.VALID for item in self.items)


class FundLifecycleMonitor:
    """Persist profile observations and detect lifecycle changes."""

    def __init__(
        self,
        database: Database | Path,
        *,
        scale_drop_ratio: Decimal = SCALE_DROP_WARNING_RATIO,
        liquidation_threshold: Decimal = LOW_SCALE_THRESHOLD,
    ) -> None:
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        self.scale_drop_ratio = _decimal_ratio(scale_drop_ratio, "scale_drop_ratio")
        if self.scale_drop_ratio <= 0 or self.scale_drop_ratio >= 1:
            raise ValueError("scale_drop_ratio must be between 0 and 1")
        self.liquidation_threshold = _decimal_positive(
            liquidation_threshold, "liquidation_threshold"
        )

    def record_snapshot(self, snapshot: FundLifecycleSnapshot) -> tuple[FundLifecycleEvent, ...]:
        """Insert an immutable profile and return newly emitted events."""

        current = normalize_snapshot(snapshot)
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT 1 FROM fund_profile_history
                WHERE symbol = ? AND as_of_date = ?
                """,
                [current.symbol, current.as_of_date],
            ).fetchone()
            if existing is not None:
                return ()
            previous_row = connection.execute(
                """
                SELECT symbol, as_of_date, manager_names, scale_text, scale_value,
                       scale_unit, purchase_status, redemption_status, fees_json,
                       fee_fingerprint, source, observed_at, source_snapshot_id
                FROM fund_profile_history
                WHERE symbol = ? AND as_of_date < ?
                ORDER BY as_of_date DESC
                LIMIT 1
                """,
                [current.symbol, current.as_of_date],
            ).fetchone()
            connection.execute(
                """
                INSERT INTO fund_profile_history (
                    symbol, as_of_date, manager_names, scale_text, scale_value,
                    scale_unit, purchase_status, redemption_status, fees_json,
                    fee_fingerprint, source, observed_at, source_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _profile_parameters(current),
            )
            if previous_row is None:
                return ()
            previous = _snapshot_from_row(previous_row)
            events = detect_lifecycle_events(
                previous,
                current,
                scale_drop_ratio=self.scale_drop_ratio,
                liquidation_threshold=self.liquidation_threshold,
            )
            return tuple(_insert_event(connection, event) for event in events)


def snapshot_from_detail(
    detail: FundDetail, *, as_of_date: date | None = None
) -> FundLifecycleSnapshot:
    """Convert the public fund-detail DTO into a lifecycle observation."""

    if not isinstance(detail, FundDetail):
        raise TypeError("detail must be FundDetail")
    observed_at = detail.observed_at
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        observed_at = observed_at.replace(tzinfo=SHANGHAI_TZ)
    else:
        observed_at = observed_at.astimezone(SHANGHAI_TZ)
    return normalize_snapshot(
        FundLifecycleSnapshot(
            symbol=detail.symbol,
            as_of_date=as_of_date or observed_at.date(),
            source=detail.source,
            observed_at=observed_at,
            manager_names=tuple(item.name for item in detail.managers),
            scale_text=detail.latest_scale,
            purchase_status=getattr(detail, "purchase_status", None),
            redemption_status=getattr(detail, "redemption_status", None),
            fees=detail.fees,
        )
    )


def normalize_snapshot(snapshot: FundLifecycleSnapshot) -> FundLifecycleSnapshot:
    """Validate and canonicalize one profile before it reaches DuckDB."""

    if not isinstance(snapshot, FundLifecycleSnapshot):
        raise TypeError("snapshot must be FundLifecycleSnapshot")
    symbol = normalize_fund_symbol(snapshot.symbol)
    if not isinstance(snapshot.as_of_date, date) or isinstance(snapshot.as_of_date, datetime):
        # A datetime is not a date here: it would be compared against the DATE
        # column read back from storage and raise an opaque TypeError, after the
        # surrounding transaction had already been rolled back.
        raise TypeError("snapshot as_of_date must be a date")
    source = _required_text(snapshot.source, "source")
    if not isinstance(snapshot.observed_at, datetime):
        raise TypeError("snapshot observed_at must be datetime")
    observed_at = snapshot.observed_at
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("snapshot observed_at must be timezone-aware")
    manager_names = tuple(
        sorted(
            {
                _required_text(value, "manager name")
                for value in snapshot.manager_names
                if isinstance(value, str) and value.strip()
            }
        )
    )
    scale_text = _optional_text(snapshot.scale_text)
    parsed_scale, parsed_unit = parse_scale(scale_text) if scale_text else (None, None)
    scale_value = snapshot.scale_value if snapshot.scale_value is not None else parsed_scale
    if scale_value is not None:
        scale_value = _decimal_positive(scale_value, "scale_value")
    scale_unit = _optional_text(snapshot.scale_unit) or parsed_unit
    purchase_status = _optional_text(snapshot.purchase_status)
    redemption_status = _optional_text(snapshot.redemption_status)
    fees = _normalize_fees(snapshot.fees)
    source_snapshot_id = snapshot.source_snapshot_id
    if source_snapshot_id is not None:
        if isinstance(source_snapshot_id, bool) or not isinstance(source_snapshot_id, int):
            raise TypeError("source_snapshot_id must be an integer or None")
        if source_snapshot_id < 0:
            raise ValueError("source_snapshot_id must be non-negative")
    return FundLifecycleSnapshot(
        symbol=symbol,
        as_of_date=snapshot.as_of_date,
        source=source,
        observed_at=observed_at.astimezone(SHANGHAI_TZ),
        manager_names=manager_names,
        scale_text=scale_text,
        scale_value=scale_value,
        scale_unit=scale_unit,
        purchase_status=purchase_status,
        redemption_status=redemption_status,
        fees=fees,
        source_snapshot_id=source_snapshot_id,
    )


def parse_scale(value: str) -> tuple[Decimal | None, str | None]:
    """Parse a display scale into a comparable base value and unit label."""

    text = _optional_text(value)
    if text is None:
        return None, None
    match = _NUMBER_RE.search(text.replace(",", ""))
    if match is None:
        return None, None
    try:
        number = Decimal(match.group(0))
    except InvalidOperation:
        return None, None
    if number <= 0:
        return None, None
    if "兆" in text:
        multiplier = Decimal("1000000000000")
    elif "亿" in text:
        multiplier = Decimal("100000000")
    elif "万" in text:
        multiplier = Decimal("10000")
    elif "千" in text:
        multiplier = Decimal("1000")
    else:
        multiplier = Decimal("1")
    unit = "份" if "份" in text else "元" if "元" in text else None
    return number * multiplier, unit


def detect_lifecycle_events(
    previous: FundLifecycleSnapshot,
    current: FundLifecycleSnapshot,
    *,
    scale_drop_ratio: Decimal = SCALE_DROP_WARNING_RATIO,
    liquidation_threshold: Decimal = LOW_SCALE_THRESHOLD,
) -> tuple[dict[str, object], ...]:
    """Compare two normalized snapshots without writing or making decisions."""

    old = normalize_snapshot(previous)
    new = normalize_snapshot(current)
    if old.symbol != new.symbol:
        raise ValueError("lifecycle snapshots must use the same fund symbol")
    if new.as_of_date <= old.as_of_date:
        raise ValueError("current lifecycle snapshot must be later than previous")
    ratio = _decimal_ratio(scale_drop_ratio, "scale_drop_ratio")
    if ratio <= 0 or ratio >= 1:
        raise ValueError("scale_drop_ratio must be between 0 and 1")
    threshold = _decimal_positive(liquidation_threshold, "liquidation_threshold")
    events: list[dict[str, object]] = []
    if old.manager_names and old.manager_names != new.manager_names:
        events.append(
            _event_payload(
                new,
                "manager_change",
                "warning",
                "基金经理名单发生变化，请核对基金公司公告",
                _names_text(old.manager_names),
                _names_text(new.manager_names),
                {"previous": old.manager_names, "current": new.manager_names},
            )
        )
    if old.scale_value is not None and new.scale_value is not None and old.scale_value > 0:
        decline = (old.scale_value - new.scale_value) / old.scale_value
        if decline >= ratio:
            severity = "critical" if decline >= SCALE_DROP_CRITICAL_RATIO else "warning"
            events.append(
                _event_payload(
                    new,
                    "scale_drop",
                    severity,
                    f"基金规模较上一观测下降 {decline:.1%}，需核对官方公告",
                    _scale_text(old),
                    _scale_text(new),
                    {
                        "previous_value": str(old.scale_value),
                        "current_value": str(new.scale_value),
                        "decline_ratio": str(decline),
                        "heuristic": "relative_scale_drop",
                    },
                )
            )
    if (
        new.scale_value is not None
        and new.scale_value < threshold
        and new.scale_unit in {"元", "份"}
        and (
            old.scale_value is None
            or old.scale_value >= threshold
            or old.scale_unit != new.scale_unit
        )
    ):
        events.append(
            _event_payload(
                new,
                "liquidation_risk",
                "critical",
                "基金规模低于本地启发式复核阈值，不能据此认定清盘",
                _scale_text(old),
                _scale_text(new),
                {
                    "threshold": str(threshold),
                    "unit": new.scale_unit,
                    "heuristic": "absolute_scale_review_only",
                },
            )
        )
    for field, event_type, label in (
        ("purchase_status", "purchase_suspension", "申购"),
        ("redemption_status", "redemption_suspension", "赎回"),
    ):
        old_status = getattr(old, field)
        new_status = getattr(new, field)
        if new_status and _restricted_status(new_status) and new_status != old_status:
            events.append(
                _event_payload(
                    new,
                    event_type,
                    "warning",
                    f"基金{label}状态变为“{new_status}”，请核对官方渠道",
                    old_status,
                    new_status,
                    {"status_field": field},
                )
            )
    old_fee_fingerprint = _fee_fingerprint(old.fees)
    new_fee_fingerprint = _fee_fingerprint(new.fees)
    if old_fee_fingerprint and new_fee_fingerprint and old_fee_fingerprint != new_fee_fingerprint:
        events.append(
            _event_payload(
                new,
                "fee_change",
                "warning",
                "基金费率明细发生变化，请以实际渠道确认页为准",
                _fees_text(old.fees),
                _fees_text(new.fees),
                {
                    "previous_fingerprint": old_fee_fingerprint,
                    "current_fingerprint": new_fee_fingerprint,
                },
            )
        )
    return tuple(events)


def sync_fund_lifecycle(
    settings: Settings,
    provider: object,
    as_of_date: date,
    symbols: Sequence[str],
) -> FundLifecycleSyncResult:
    """Fetch public fund profiles and persist lifecycle evidence locally."""

    if not hasattr(settings, "database_path"):
        raise TypeError("settings must provide database_path")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")
    detail = getattr(provider, "detail", None)
    if not callable(detail):
        raise TypeError("fund lifecycle provider must provide detail()")
    selected = tuple(
        dict.fromkeys(normalize_fund_symbol(item) for item in symbols if str(item).strip())
    )
    if not selected:
        return FundLifecycleSyncResult(as_of_date, ())
    monitor = FundLifecycleMonitor(settings.database_path)
    items: list[FundLifecycleSyncItem] = []
    for symbol in selected:
        try:
            snapshot = snapshot_from_detail(detail(symbol), as_of_date=as_of_date)
            events = monitor.record_snapshot(snapshot)
        except (ProviderError, ConnectionError, OSError, TypeError, ValueError) as exc:
            items.append(
                FundLifecycleSyncItem(
                    symbol, DataStatus.BLOCKED, error=f"{type(exc).__name__}: {exc}"
                )
            )
        else:
            items.append(FundLifecycleSyncItem(symbol, DataStatus.VALID, len(events)))
    return FundLifecycleSyncResult(as_of_date, tuple(items))


def _profile_parameters(snapshot: FundLifecycleSnapshot) -> list[object]:
    return [
        snapshot.symbol,
        snapshot.as_of_date,
        _names_text(snapshot.manager_names) or None,
        snapshot.scale_text,
        None if snapshot.scale_value is None else float(snapshot.scale_value),
        snapshot.scale_unit,
        snapshot.purchase_status,
        snapshot.redemption_status,
        json.dumps(_fee_records(snapshot.fees), ensure_ascii=False, sort_keys=True),
        _fee_fingerprint(snapshot.fees) or None,
        snapshot.source,
        snapshot.observed_at,
        snapshot.source_snapshot_id,
    ]


def _snapshot_from_row(row: Sequence[object]) -> FundLifecycleSnapshot:
    fees_raw = row[8]
    fees: list[FundFee] = []
    try:
        parsed = json.loads(str(fees_raw)) if isinstance(fees_raw, str) else fees_raw
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes)):
        for item in parsed:
            if isinstance(item, Mapping):
                rate = item.get("rate")
                try:
                    rate_value = None if rate is None else Decimal(str(rate))
                except InvalidOperation:
                    rate_value = None
                fees.append(
                    FundFee(
                        str(item.get("fee_type", "")),
                        str(item.get("condition", "")),
                        rate_value,
                        fixed_amount=(
                            Decimal(str(item["fixed_amount"]))
                            if item.get("fixed_amount") is not None
                            else None
                        ),
                        discounted_rate=(
                            Decimal(str(item["discounted_rate"]))
                            if item.get("discounted_rate") is not None
                            else None
                        ),
                        discount_channel=item.get("discount_channel"),
                        source=str(item.get("source") or "danjuan"),
                        source_url=item.get("source_url"),
                    )
                )
    observed_at = row[11]
    if not isinstance(observed_at, datetime):
        raise TypeError("stored observed_at is invalid")
    return normalize_snapshot(
        FundLifecycleSnapshot(
            symbol=str(row[0]),
            as_of_date=row[1],  # type: ignore[arg-type]
            source=str(row[10]),
            observed_at=observed_at,
            manager_names=tuple(str(row[2]).split("、")) if row[2] else (),
            scale_text=None if row[3] is None else str(row[3]),
            scale_value=None if row[4] is None else Decimal(str(row[4])),
            scale_unit=None if row[5] is None else str(row[5]),
            purchase_status=None if row[6] is None else str(row[6]),
            redemption_status=None if row[7] is None else str(row[7]),
            fees=tuple(fees),
            source_snapshot_id=None if row[12] is None else int(row[12]),
        )
    )


def _insert_event(connection: object, payload: Mapping[str, object]) -> FundLifecycleEvent:
    details = payload["details"]
    if not isinstance(details, Mapping):
        raise TypeError("event details must be a mapping")
    details_json = json.dumps(dict(details), ensure_ascii=False, sort_keys=True, default=str)
    row = connection.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO fund_lifecycle_events (
            symbol, observed_on, event_type, severity, message,
            previous_value, current_value, source, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, observed_on, event_type) DO NOTHING
        RETURNING id
        """,
        [
            payload["symbol"],
            payload["observed_on"],
            payload["event_type"],
            payload["severity"],
            payload["message"],
            payload["previous_value"],
            payload["current_value"],
            payload["source"],
            details_json,
            payload["created_at"],
        ],
    ).fetchone()
    if row is None:
        row = connection.execute(  # type: ignore[attr-defined]
            """
            SELECT id FROM fund_lifecycle_events
            WHERE symbol = ? AND observed_on = ? AND event_type = ?
            """,
            [payload["symbol"], payload["observed_on"], payload["event_type"]],
        ).fetchone()
    if row is None:
        raise RuntimeError("lifecycle event insert did not return an id")
    return FundLifecycleEvent(
        id=int(row[0]),
        symbol=str(payload["symbol"]),
        observed_on=payload["observed_on"],  # type: ignore[arg-type]
        event_type=str(payload["event_type"]),
        severity=str(payload["severity"]),
        message=str(payload["message"]),
        previous_value=_optional_text(payload["previous_value"]),
        current_value=_optional_text(payload["current_value"]),
        source=str(payload["source"]),
        details=details,
        created_at=payload["created_at"],  # type: ignore[arg-type]
    )


def _event_payload(
    snapshot: FundLifecycleSnapshot,
    event_type: str,
    severity: str,
    message: str,
    previous_value: str | None,
    current_value: str | None,
    details: Mapping[str, object],
) -> dict[str, object]:
    return {
        "symbol": snapshot.symbol,
        "observed_on": snapshot.as_of_date,
        "event_type": event_type,
        "severity": severity,
        "message": message,
        "previous_value": previous_value,
        "current_value": current_value,
        "source": snapshot.source,
        "details": dict(details),
        "created_at": snapshot.observed_at,
    }


def _normalize_fees(value: Sequence[FundFee]) -> tuple[FundFee, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("fees must be a sequence")
    normalized: list[FundFee] = []
    for item in value:
        if not isinstance(item, FundFee):
            raise TypeError("fees must contain FundFee values")
        rate = None if item.rate is None else _decimal_nonnegative(item.rate, "fee rate")
        normalized.append(
            replace(
                item,
                fee_type=_required_text(item.fee_type, "fee_type"),
                condition=_required_text(item.condition, "fee condition"),
                rate=rate,
                fixed_amount=None
                if item.fixed_amount is None
                else _decimal_nonnegative(item.fixed_amount, "fixed fee"),
                discounted_rate=None
                if item.discounted_rate is None
                else _decimal_nonnegative(item.discounted_rate, "discounted fee"),
            )
        )
    return tuple(
        sorted(normalized, key=lambda item: (item.fee_type, item.condition, str(item.rate)))
    )


def _fee_records(fees: Sequence[FundFee]) -> list[dict[str, str | None]]:
    return [
        {
            "fee_type": item.fee_type,
            "condition": item.condition,
            "rate": None if item.rate is None else str(item.rate),
            "fixed_amount": None if item.fixed_amount is None else str(item.fixed_amount),
            "discounted_rate": None if item.discounted_rate is None else str(item.discounted_rate),
            "discount_channel": item.discount_channel,
            "source": item.source,
            "source_url": item.source_url,
        }
        for item in fees
    ]


def _fee_fingerprint(fees: Sequence[FundFee]) -> str:
    payload = json.dumps(
        _fee_records(fees), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest() if payload != "[]" else ""


def _fees_text(fees: Sequence[FundFee]) -> str:
    return (
        "；".join(
            f"{item.fee_type}/{item.condition}={item.rate if item.rate is not None else '未知'}"
            for item in fees
        )
        or "未提供"
    )


def _scale_text(snapshot: FundLifecycleSnapshot) -> str:
    if snapshot.scale_text:
        return snapshot.scale_text
    return str(snapshot.scale_value) if snapshot.scale_value is not None else "未提供"


def _names_text(names: Sequence[str]) -> str:
    return "、".join(names)


def _restricted_status(value: str) -> bool:
    text = value.strip()
    return any(word in text for word in _RESTRICTED_STATUS_WORDS)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal_ratio(value: object, name: str) -> Decimal:
    return _decimal_nonnegative(value, name)


def _decimal_positive(value: object, name: str) -> Decimal:
    converted = _decimal_nonnegative(value, name)
    if converted <= 0:
        raise ValueError(f"{name} must be positive")
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
    "FundLifecycleEvent",
    "FundLifecycleMonitor",
    "FundLifecycleSnapshot",
    "FundLifecycleSyncItem",
    "FundLifecycleSyncResult",
    "LOW_SCALE_THRESHOLD",
    "SCALE_DROP_CRITICAL_RATIO",
    "SCALE_DROP_WARNING_RATIO",
    "detect_lifecycle_events",
    "normalize_snapshot",
    "parse_scale",
    "snapshot_from_detail",
    "sync_fund_lifecycle",
]
