"""Reconciled fund lot evidence and explicit, read-only redemption planning."""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal, localcontext

from quant_assistant.funds.fees import FundRedemptionLot, _verify_schedule, estimate_redemption_lots
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.fund_valuation import normalize_fund_symbol
from quant_assistant.services.commands_common import LedgerStateError, require_decimal
from quant_assistant.services.fund_fee_evidence import FundFeeEvidenceService


class FundLotService:
    def __init__(self, database: Database, now):
        self.database, self.now = database, now
        self.fees = FundFeeEvidenceService(database, now=now)

    def _position(self, symbol):
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT asset_type, quantity, as_of_date, updated_at FROM positions WHERE symbol=?",
                [symbol],
            ).fetchone()
        if row is None or row[0] != "fund" or row[1] <= 0:
            return None, None
        fingerprint = hashlib.sha256(
            json.dumps([symbol, *row], default=str, separators=(",", ":")).encode()
        ).hexdigest()
        return row, fingerprint

    def list(self, symbol: str) -> dict:
        symbol = normalize_fund_symbol(symbol)
        with self.database.connection() as connection:
            position, fingerprint = self._position(symbol)
            snapshot = connection.execute(
                "SELECT id, position_fingerprint, verified_on, evidence_reference "
                "FROM fund_lot_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1",
                [symbol],
            ).fetchone()
            items = (
                []
                if snapshot is None
                else [
                    {"lot_id": row[0], "confirmed_on": row[1], "remaining_units": row[2]}
                    for row in connection.execute(
                        "SELECT lot_id,confirmed_on,remaining_units FROM fund_holding_lots "
                        "WHERE snapshot_id=? ORDER BY confirmed_on,lot_id",
                        [snapshot[0]],
                    ).fetchall()
                ]
            )
            status = "missing" if snapshot is None else "reconciled"
            blockers = []
            if position is None:
                status = "unavailable"
                blockers.append("本地主账本尚无该基金的已确认持仓")
            elif snapshot is None:
                blockers.append("请按确认单核对每个批次的剩余份额和买入确认日")
            elif (
                snapshot[1] != fingerprint
                or sum((item["remaining_units"] for item in items), Decimal("0")) != position[1]
            ):
                status = "stale"
                blockers.append("持仓已变化，请重新核对所有剩余批次；不会推断已卖出的批次")
            return {
                "symbol": symbol,
                "status": status,
                "position_quantity": None if position is None else position[1],
                "position_as_of_date": None if position is None else position[2],
                "position_fingerprint": fingerprint,
                "snapshot_id": None if snapshot is None else snapshot[0],
                "verified_on": None if snapshot is None else snapshot[2],
                "evidence_reference": None if snapshot is None else snapshot[3],
                "items": items,
                "blockers": blockers,
            }

    def save(
        self, symbol, *, items, verified_on, evidence_reference, position_fingerprint, confirmed
    ):
        if confirmed is not True:
            raise LedgerStateError("fund_lots_unconfirmed", "请明确核对确认单")
        symbol = normalize_fund_symbol(symbol)
        if not evidence_reference.strip() or not 1 <= len(items) <= 100:
            raise ValueError("批次证据及条目不能为空")
        normalized = []
        for item in items:
            units = require_decimal(item["remaining_units"], "剩余份额")
            with localcontext() as precision:
                precision.prec = 40
                valid_units = 0 < units < Decimal("1e20") and units == units.quantize(
                    Decimal("0.00000001")
                )
            if not valid_units:
                raise ValueError("批次份额必须为正且最多8位小数")
            if not item["lot_id"].strip() or item["confirmed_on"] > verified_on:
                raise ValueError("批次编号或确认日期无效")
            normalized.append(
                {
                    "lot_id": item["lot_id"].strip(),
                    "confirmed_on": item["confirmed_on"],
                    "remaining_units": units,
                }
            )
        if len({item["lot_id"] for item in normalized}) != len(normalized):
            raise ValueError("批次编号不能重复")
        normalized.sort(key=lambda item: (item["confirmed_on"], item["lot_id"]))
        with self.database.transaction() as connection:
            position, fingerprint = self._position(symbol)
            if position is None or fingerprint != position_fingerprint:
                raise LedgerStateError("fund_lots_stale", "持仓已变化，请刷新后核对")
            if not position[2] <= verified_on <= self.now().date():
                raise ValueError("核对日期不得早于持仓日期或晚于今天")
            if sum((item["remaining_units"] for item in normalized), Decimal("0")) != position[1]:
                raise LedgerStateError("fund_lots_mismatch", "批次总份额必须与持仓一致")
            payload = [symbol, fingerprint, verified_on, evidence_reference.strip(), normalized]
            request = hashlib.sha256(
                json.dumps(payload, default=str, sort_keys=True).encode()
            ).hexdigest()
            if not connection.execute(
                "SELECT id FROM fund_lot_snapshots WHERE request_fingerprint=?", [request]
            ).fetchone():
                snapshot_id = connection.execute(
                    "INSERT INTO fund_lot_snapshots "
                    "(symbol,position_fingerprint,request_fingerprint,"
                    "verified_on,evidence_reference,created_at) "
                    "VALUES (?,?,?,?,?,?) RETURNING id",
                    [
                        symbol,
                        fingerprint,
                        request,
                        verified_on,
                        evidence_reference.strip(),
                        self.now(),
                    ],
                ).fetchone()[0]
                for item in normalized:
                    connection.execute(
                        "INSERT INTO fund_holding_lots VALUES (?,?,?,?)",
                        [
                            snapshot_id,
                            item["lot_id"],
                            item["confirmed_on"],
                            item["remaining_units"],
                        ],
                    )
        return self.list(symbol)

    def quote(self, symbol, *, snapshot_id, selections, confirmed_on, assumed_nav):
        if not 1 <= len(selections) <= 100 or not assumed_nav.is_finite() or assumed_nav <= 0:
            raise ValueError("需要明确选择批次及规划净值")
        with self.database.connection(), localcontext() as precision:
            precision.prec = 60
            evidence = self.list(symbol)
            if evidence["status"] != "reconciled" or evidence["snapshot_id"] != snapshot_id:
                raise LedgerStateError("fund_lots_stale", "请先重新核对剩余批次")
            if confirmed_on < self.now().date():
                raise ValueError("此接口按当前费率规划，不能回算历史赎回")
            schedule = self.fees._current_schedule(symbol)
            _verify_schedule(schedule, confirmed_on)
            by_id = {item["lot_id"]: item for item in evidence["items"]}
            selected_ids = set()
            lots = []
            for selection in selections:
                lot_id, quantity = selection["lot_id"], selection["quantity"]
                if (
                    lot_id not in by_id
                    or lot_id in selected_ids
                    or not quantity.is_finite()
                    or not (0 < quantity <= by_id[lot_id]["remaining_units"])
                ):
                    raise LedgerStateError("fund_lots_selection_invalid", "选择份额超限或批次重复")
                selected_ids.add(lot_id)
                gross = (quantity * assumed_nav).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                lots.append(FundRedemptionLot(by_id[lot_id]["confirmed_on"], gross))
            estimates = estimate_redemption_lots(schedule, tuple(lots), confirmed_on)
            items = [
                {**estimate, **selection}
                for estimate, selection in zip(estimates, selections, strict=True)
            ]
            gross = sum((item["gross_amount"] for item in items), Decimal("0"))
            fees = sum((item["fee"] for item in items), Decimal("0"))
            return {
                "symbol": normalize_fund_symbol(symbol),
                "snapshot_id": snapshot_id,
                "confirmed_on": confirmed_on,
                "assumed_nav": assumed_nav,
                "items": items,
                "total_gross": gross,
                "total_fee": fees,
                "estimated_net": gross - fees,
                "cash_credit": Decimal("0"),
                "fee_evidence_reference": schedule.evidence_reference,
                "basis": "selected_reconciled_lots_assumed_nav",
                "research_only": True,
            }
