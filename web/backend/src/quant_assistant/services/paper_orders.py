"""Auditable paper-order lifecycle isolated from the real portfolio ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import duckdb

from quant_assistant.common.serialization import decimal_coerce
from quant_assistant.models.repositories.database import Database


class PaperOrderStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


_TRANSITIONS = {
    PaperOrderStatus.DRAFT: {
        PaperOrderStatus.APPROVED,
        PaperOrderStatus.CANCELLED,
        PaperOrderStatus.EXPIRED,
    },
    PaperOrderStatus.APPROVED: {
        PaperOrderStatus.SUBMITTED,
        PaperOrderStatus.CANCELLED,
        PaperOrderStatus.EXPIRED,
    },
    PaperOrderStatus.SUBMITTED: {
        PaperOrderStatus.CANCELLED,
        PaperOrderStatus.REJECTED,
        PaperOrderStatus.EXPIRED,
    },
    PaperOrderStatus.PARTIALLY_FILLED: {PaperOrderStatus.CANCELLED},
}


@dataclass(frozen=True, slots=True)
class PaperFillInput:
    fill_id: str
    order_id: str
    quantity: Decimal
    price: Decimal
    fees: Decimal
    filled_at: datetime
    final: bool
    note: str = ""


@dataclass(frozen=True, slots=True)
class PaperReconciliationResult:
    reconciliation_id: str
    order_id: str
    status: str
    details: dict[str, object]


class PaperOrderService:
    def __init__(self, database: Database | Path, clock: object):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()
        now = getattr(clock, "now", clock)
        if not callable(now):
            raise TypeError("clock must be callable or provide now()")
        self._now = now

    def stage_run(self, run_id: str) -> tuple[str, ...]:
        normalized_run_id = _required_text(run_id, "run_id")
        now = _aware(self._now(), "clock")
        with self.database.connection() as connection:
            run = connection.execute(
                """
                SELECT data_status, readiness, status FROM strategy_runs WHERE run_id = ?
                """,
                [normalized_run_id],
            ).fetchone()
            if run is None:
                raise ValueError("strategy run does not exist")
            if str(run[0]) != "valid" or str(run[2]) != "completed":
                raise ValueError("only completed runs with valid data can enter paper tracking")
            proposals = connection.execute(
                """
                SELECT id, symbol, asset_type, action, amount, quantity, valid_until
                FROM proposed_orders WHERE run_id = ?
                  AND action IN ('buy', 'add', 'reduce', 'sell')
                ORDER BY id
                """,
                [normalized_run_id],
            ).fetchall()
            staged: list[str] = []
            with self.database.transaction() as connection:
                note = (
                    "来源决策仍为研究模式，仅用于纸面跟踪"
                    if str(run[1]) == "research_only"
                    else "来源决策已进入模拟/参考模式"
                )
                rows_to_insert: list[list[object]] = []
                for proposal in proposals:
                    proposal_id = int(proposal[0])
                    valid_until = _aware(proposal[6], "valid_until")
                    if valid_until <= now:
                        continue
                    idempotency_key = f"proposal:{normalized_run_id}:{proposal_id}"
                    order_id = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
                    rows_to_insert.append(
                        [
                            order_id,
                            proposal_id,
                            normalized_run_id,
                            str(proposal[1]),
                            str(proposal[2]),
                            str(proposal[3]),
                            proposal[4],
                            proposal[5],
                            valid_until,
                            idempotency_key,
                            note,
                            now,
                            now,
                        ]
                    )
                    staged.append(order_id)
                if rows_to_insert:
                    connection.executemany(
                        """
                        INSERT INTO paper_orders (
                            order_id, proposal_id, run_id, symbol, asset_type, action,
                            requested_amount, requested_quantity, status, valid_until,
                            idempotency_key, note, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                        ON CONFLICT (idempotency_key) DO NOTHING
                        """,
                        rows_to_insert,
                    )
        return tuple(staged)

    def transition(
        self,
        order_id: str,
        expected_status: PaperOrderStatus | str,
        target_status: PaperOrderStatus | str,
        *,
        note: str,
    ) -> None:
        normalized_order_id = _required_text(order_id, "order_id")
        expected = PaperOrderStatus(expected_status)
        target = PaperOrderStatus(target_status)
        if target not in _TRANSITIONS.get(expected, set()):
            raise ValueError(f"invalid paper order transition: {expected.value} -> {target.value}")
        normalized_note = _required_text(note, "note")
        now = _aware(self._now(), "clock")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT status, valid_until, note FROM paper_orders WHERE order_id = ?",
                [normalized_order_id],
            ).fetchone()
            if row is None:
                raise ValueError("paper order does not exist")
            actual = PaperOrderStatus(str(row[0]))
            if actual is not expected:
                raise RuntimeError(
                    f"paper_order_state_conflict: expected {expected.value}, found {actual.value}"
                )
            valid_until = _aware(row[1], "valid_until")
            if now > valid_until and target is not PaperOrderStatus.EXPIRED:
                raise ValueError("paper order has expired")
            before = {"status": actual.value, "note": row[2]}
            connection.execute(
                """
                UPDATE paper_orders SET status = ?, note = ?, updated_at = ?
                WHERE order_id = ? AND status = ?
                """,
                [target.value, normalized_note, now, normalized_order_id, expected.value],
            )
            _audit(
                connection,
                "paper_order",
                normalized_order_id,
                now,
                before,
                {"status": target.value, "note": normalized_note},
            )

    def record_fill(self, item: PaperFillInput) -> str:
        if not isinstance(item, PaperFillInput):
            raise TypeError("item must be PaperFillInput")
        fill_id = _required_text(item.fill_id, "fill_id")
        order_id = _required_text(item.order_id, "order_id")
        quantity = _positive_decimal(item.quantity, "quantity")
        price = _positive_decimal(item.price, "price")
        fees = _non_negative_decimal(item.fees, "fees")
        filled_at = _aware(item.filled_at, "filled_at")
        now = _aware(self._now(), "clock")
        if filled_at > now:
            raise ValueError("filled_at must not be in the future")
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT order_id, quantity, price, fees, filled_at
                FROM paper_fills WHERE fill_id = ?
                """,
                [fill_id],
            ).fetchone()
            payload = (order_id, quantity, price, fees, filled_at)
            if existing is not None:
                stored = (
                    str(existing[0]),
                    Decimal(str(existing[1])),
                    Decimal(str(existing[2])),
                    Decimal(str(existing[3])),
                    _aware(existing[4], "filled_at"),
                )
                if stored != payload:
                    raise RuntimeError("paper_fill_id_conflict")
                return fill_id
            order = connection.execute(
                """
                SELECT status, requested_quantity, valid_until
                FROM paper_orders WHERE order_id = ?
                """,
                [order_id],
            ).fetchone()
            if order is None:
                raise ValueError("paper order does not exist")
            status = PaperOrderStatus(str(order[0]))
            if status not in {
                PaperOrderStatus.SUBMITTED,
                PaperOrderStatus.PARTIALLY_FILLED,
            }:
                raise ValueError("paper fill requires a submitted order")
            if filled_at > _aware(order[2], "valid_until"):
                raise ValueError("paper fill occurred after order expiry")
            previous = connection.execute(
                "SELECT coalesce(sum(quantity), 0) FROM paper_fills WHERE order_id = ?",
                [order_id],
            ).fetchone()
            total_quantity = Decimal(str(previous[0])) + quantity
            requested = None if order[1] is None else Decimal(str(order[1]))
            if requested is not None and total_quantity > requested:
                raise ValueError("paper fills exceed requested quantity")
            if item.final and requested is not None and total_quantity != requested:
                raise ValueError("final paper fill must reconcile to requested quantity")
            target = PaperOrderStatus.FILLED if item.final else PaperOrderStatus.PARTIALLY_FILLED
            connection.execute(
                """
                INSERT INTO paper_fills (
                    fill_id, order_id, quantity, price, fees,
                    filled_at, recorded_at, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [fill_id, order_id, quantity, price, fees, filled_at, now, item.note],
            )
            connection.execute(
                "UPDATE paper_orders SET status = ?, updated_at = ? WHERE order_id = ?",
                [target.value, now, order_id],
            )
            _audit(
                connection,
                "paper_fill",
                fill_id,
                now,
                None,
                {
                    "order_id": order_id,
                    "quantity": str(quantity),
                    "price": str(price),
                    "fees": str(fees),
                    "status": target.value,
                },
            )
        return fill_id

    def reconcile(self, order_id: str) -> PaperReconciliationResult:
        normalized_order_id = _required_text(order_id, "order_id")
        now = _aware(self._now(), "clock")
        with self.database.connection() as connection:
            order = connection.execute(
                """
                SELECT status, requested_quantity, requested_amount
                FROM paper_orders WHERE order_id = ?
                """,
                [normalized_order_id],
            ).fetchone()
            if order is None:
                raise ValueError("paper order does not exist")
            fills = connection.execute(
                """
                SELECT count(*), coalesce(sum(quantity), 0),
                       coalesce(sum(quantity * price), 0), coalesce(sum(fees), 0)
                FROM paper_fills WHERE order_id = ?
                """,
                [normalized_order_id],
            ).fetchone()
            fill_count = int(fills[0])
            filled_quantity = Decimal(str(fills[1]))
            requested_quantity = None if order[1] is None else Decimal(str(order[1]))
            state = PaperOrderStatus(str(order[0]))
            issues: list[str] = []
            if state is PaperOrderStatus.FILLED and fill_count == 0:
                issues.append("filled_order_has_no_fills")
            if state in {PaperOrderStatus.DRAFT, PaperOrderStatus.APPROVED} and fill_count:
                issues.append("unsubmitted_order_has_fills")
            if requested_quantity is not None and filled_quantity > requested_quantity:
                issues.append("filled_quantity_exceeds_request")
            if (
                state is PaperOrderStatus.FILLED
                and requested_quantity is not None
                and filled_quantity != requested_quantity
            ):
                issues.append("filled_quantity_does_not_match_request")
            status = "matched" if not issues else "mismatch"
            details: dict[str, object] = {
                "order_status": state.value,
                "fill_count": fill_count,
                "requested_quantity": (
                    None if requested_quantity is None else str(requested_quantity)
                ),
                "filled_quantity": str(filled_quantity),
                "filled_gross_amount": str(fills[2]),
                "fees": str(fills[3]),
                "issues": issues,
            }
            payload = json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            reconciliation_id = hashlib.sha256(
                f"{normalized_order_id}:{payload}".encode()
            ).hexdigest()[:32]
            connection.execute(
                """
                INSERT OR REPLACE INTO paper_reconciliations (
                    reconciliation_id, order_id, status, details_json, checked_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [reconciliation_id, normalized_order_id, status, payload, now],
            )
        return PaperReconciliationResult(reconciliation_id, normalized_order_id, status, details)


def _audit(
    connection: duckdb.DuckDBPyConnection,
    entity_type: str,
    entity_id: str,
    changed_at: datetime,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_log (
            entity_type, entity_id, changed_at, before_json, after_json, actor
        ) VALUES (?, ?, ?, ?, ?, 'local_user')
        """,
        [
            entity_type,
            entity_id,
            changed_at,
            None if before is None else json.dumps(before, ensure_ascii=False),
            None if after is None else json.dumps(after, ensure_ascii=False),
        ],
    )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _positive_decimal(value: object, name: str) -> Decimal:
    result = _decimal(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _non_negative_decimal(value: object, name: str) -> Decimal:
    result = _decimal(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _decimal(value: object, name: str) -> Decimal:
    try:
        return decimal_coerce(value, name)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc


__all__ = [
    "PaperFillInput",
    "PaperOrderService",
    "PaperOrderStatus",
    "PaperReconciliationResult",
]
