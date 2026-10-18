from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from quant_assistant.models.entities import AssetType, Position

from ._portfolio_commands_impl import (
    CashFlowType,
    ExternalCashFlowInput,
    PortfolioCalibrationInput,
    PositionInput,
    _clock_now,
    _require_decimal,
    _require_plain_date,
    _require_symbol,
    _to_json,
)


class _PortfolioCommandFlows:
    """Moved methods for PortfolioCommandService."""

    def record_external_cash_flow(self, item: ExternalCashFlowInput) -> str:
        """Apply a confirmed external flow to cash and the NAV event ledger atomically."""

        if not isinstance(item, ExternalCashFlowInput):
            raise TypeError("外部资金流必须使用ExternalCashFlowInput")
        event_id = item.event_id.strip()
        if not event_id:
            raise ValueError("资金流确认键不能为空")
        if not isinstance(item.flow_type, CashFlowType):
            raise TypeError("资金流类型必须是CashFlowType")
        amount = _require_decimal(item.amount, "资金流金额")
        if amount <= 0:
            raise ValueError("资金流金额必须大于0")
        effective_date = _require_plain_date(item.effective_date, "资金流日期")
        reason = item.reason.strip()
        if not reason:
            raise ValueError("资金流原因不能为空")
        recorded_at = _clock_now(self._clock)
        signed_amount = amount if item.flow_type is CashFlowType.DEPOSIT else -amount

        with self._database.transaction() as connection:
            try:
                existing = connection.execute(
                    """
                    SELECT event_type, effective_date, amount, status, reason, details_json
                    FROM portfolio_events WHERE event_id = ?
                    """,
                    [event_id],
                ).fetchone()
                if existing is not None:
                    same = (
                        existing[0] == item.flow_type.value
                        and existing[1] == effective_date
                        and existing[2] == signed_amount
                        and existing[3] == "confirmed"
                        and existing[4] == reason
                    )
                    details = json.loads(existing[5]) if isinstance(existing[5], str) else {}
                    if item.cash_symbol is not None:
                        same = same and details.get("cash_symbol") == _require_symbol(
                            item.cash_symbol
                        )
                    if not same:
                        raise ValueError("相同资金流确认键对应不同内容")
                    return event_id

                latest = connection.execute(
                    """
                    SELECT as_of_date FROM portfolio_valuations
                    ORDER BY as_of_date DESC, generated_at DESC, run_id DESC LIMIT 1
                    """
                ).fetchone()
                if latest is None:
                    raise ValueError("请先完成净值基线对应的首个有效日决策")
                if effective_date <= latest[0]:
                    raise ValueError("资金流日期不得早于或等于最近已确认估值日")

                cash = self._cash_state(connection, item.cash_symbol)
                new_cash = cash["market_value"] + signed_amount
                if new_cash < 0:
                    raise ValueError("出金金额超过当前现金余额")
                self._write_cash_position(
                    connection,
                    str(cash["symbol"]),
                    new_cash,
                    effective_date,
                    recorded_at,
                )
                details_json = _to_json({"cash_symbol": cash["symbol"]})
                connection.execute(
                    """
                    INSERT INTO portfolio_events (
                        event_id, event_type, effective_date, amount,
                        status, reason, details_json, recorded_at
                    ) VALUES (?, ?, ?, ?, 'confirmed', ?, ?, ?)
                    """,
                    [
                        event_id,
                        item.flow_type.value,
                        effective_date,
                        signed_amount,
                        reason,
                        details_json,
                        recorded_at,
                    ],
                )
                self._insert_audit(
                    connection,
                    "external_cash_flow",
                    event_id,
                    recorded_at,
                    None,
                    {
                        "event_id": event_id,
                        "flow_type": item.flow_type,
                        "amount": signed_amount,
                        "effective_date": effective_date,
                        "cash_symbol": cash["symbol"],
                        "cash_before": cash["market_value"],
                        "cash_after": new_cash,
                        "reason": reason,
                    },
                )
            except Exception:
                raise
        return event_id

    def record_portfolio_calibration(
        self, item: PortfolioCalibrationInput, *, instrument_metadata=()
    ) -> str:
        """Replace the complete ledger only with an explicit reconciliation event."""

        if not isinstance(item, PortfolioCalibrationInput):
            raise TypeError("组合校准必须使用PortfolioCalibrationInput")
        event_id = item.event_id.strip()
        if not event_id:
            raise ValueError("校准确认键不能为空")
        as_of_date = _require_plain_date(item.as_of_date, "校准日期")
        reason = item.reason.strip()
        if not reason:
            raise ValueError("校准原因不能为空")
        metadata = self._normalise_instrument_metadata(instrument_metadata, as_of_date)
        net_external_flow = _require_decimal(item.net_external_flow, "校准净外部资金流")
        rows = self._normalise_position_snapshot(item.positions, as_of_date)
        total_value = sum((row["market_value"] for row in rows), Decimal("0"))
        if total_value - net_external_flow <= 0:
            raise ValueError("扣除校准净外部资金流后的组合价值必须为正")
        snapshot_json = _to_json(rows)
        if snapshot_json is None:
            raise RuntimeError("无法生成校准快照")
        snapshot_fingerprint = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        details = {
            "positions_fingerprint": snapshot_fingerprint,
            "symbols": [row["symbol"] for row in rows],
        }
        details_json = _to_json(details)
        recorded_at = _clock_now(self._clock)

        with self._database.transaction() as connection:
            try:
                existing = connection.execute(
                    """
                    SELECT event_type, effective_date, amount, status, reason, details_json
                    FROM portfolio_events WHERE event_id = ?
                    """,
                    [event_id],
                ).fetchone()
                if existing is not None:
                    existing_details = (
                        json.loads(existing[5]) if isinstance(existing[5], str) else existing[5]
                    )
                    same = (
                        existing[0] == "calibration"
                        and existing[1] == as_of_date
                        and existing[2] == net_external_flow
                        and existing[3] == "confirmed"
                        and existing[4] == reason
                        and existing_details == details
                    )
                    if not same:
                        raise ValueError("相同校准确认键对应不同内容")
                    return event_id

                latest = connection.execute(
                    """
                    SELECT as_of_date FROM portfolio_valuations
                    ORDER BY as_of_date DESC, generated_at DESC, run_id DESC LIMIT 1
                    """
                ).fetchone()
                if latest is None:
                    raise ValueError("组合校准前必须先建立首个有效净值")
                if as_of_date <= latest[0]:
                    raise ValueError("校准日期不得早于或等于最近已确认估值日")
                self._insert_missing_instruments(connection, metadata)
                for row in rows:
                    self._require_known_instrument(
                        connection,
                        str(row["symbol"]),
                        row["asset_type"],
                    )

                before_rows = connection.execute(
                    """
                    SELECT symbol, asset_type, quantity, average_cost,
                           market_value, as_of_date
                    FROM positions ORDER BY symbol
                    """
                ).fetchall()
                connection.execute("DELETE FROM positions")
                for row in rows:
                    connection.execute(
                        """
                        INSERT INTO positions (
                            symbol, asset_type, quantity, average_cost,
                            market_value, as_of_date, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            row["symbol"],
                            row["asset_type"].value,
                            row["quantity"],
                            row["average_cost"],
                            row["market_value"],
                            as_of_date,
                            recorded_at,
                        ],
                    )
                connection.execute(
                    """
                    INSERT INTO portfolio_events (
                        event_id, event_type, effective_date, amount,
                        status, reason, details_json, recorded_at
                    ) VALUES (?, 'calibration', ?, ?, 'confirmed', ?, ?, ?)
                    """,
                    [
                        event_id,
                        as_of_date,
                        net_external_flow,
                        reason,
                        details_json,
                        recorded_at,
                    ],
                )
                self._insert_audit(
                    connection,
                    "portfolio_calibration",
                    event_id,
                    recorded_at,
                    before_rows,
                    {
                        "event_id": event_id,
                        "as_of_date": as_of_date,
                        "net_external_flow": net_external_flow,
                        "positions_fingerprint": snapshot_fingerprint,
                        "positions": rows,
                        "reason": reason,
                    },
                )
            except Exception:
                raise
        return event_id

    def record_position(self, item: PositionInput | Position) -> str:
        """Upsert a user-entered current position and retain its prior value."""

        symbol = _require_symbol(item.symbol)
        if not isinstance(item.asset_type, AssetType):
            raise TypeError("资产类型必须是AssetType")
        quantity = _require_decimal(item.quantity, "持仓数量")
        average_cost = _require_decimal(item.average_cost, "平均成本")
        market_value = _require_decimal(item.market_value, "市值")
        as_of_date = _require_plain_date(item.as_of_date, "持仓日期")
        if quantity < 0:
            raise ValueError("持仓数量不能为负")
        if average_cost < 0:
            raise ValueError("平均成本不能为负")
        if market_value < 0:
            raise ValueError("市值不能为负")
        if item.asset_type is AssetType.STOCK and quantity != quantity.to_integral_value():
            raise ValueError("股票持仓数量必须是整股")
        changed_at = _clock_now(self._clock)

        with self._database.transaction() as connection:
            try:
                self._require_known_instrument(connection, symbol, item.asset_type)
                if self._portfolio_tracking_started(connection):
                    raise ValueError(
                        "净值基线建立后不能直接覆盖持仓；请补录成交、资金流或使用对账校准"
                    )
                before = self._position_state(connection, symbol)
                if quantity == 0:
                    connection.execute("DELETE FROM positions WHERE symbol = ?", [symbol])
                else:
                    connection.execute(
                        """
                        INSERT INTO positions (
                            symbol, asset_type, quantity, average_cost,
                            market_value, as_of_date, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (symbol) DO UPDATE SET
                            asset_type = excluded.asset_type,
                            quantity = excluded.quantity,
                            average_cost = excluded.average_cost,
                            market_value = excluded.market_value,
                            as_of_date = excluded.as_of_date,
                            updated_at = excluded.updated_at
                        """,
                        [
                            symbol,
                            item.asset_type.value,
                            quantity,
                            average_cost,
                            market_value,
                            as_of_date,
                            changed_at,
                        ],
                    )
                after = {
                    "symbol": symbol,
                    "asset_type": item.asset_type,
                    "quantity": quantity,
                    "average_cost": average_cost,
                    "market_value": market_value,
                    "as_of_date": as_of_date,
                }
                self._insert_audit(connection, "position", symbol, changed_at, before, after)
            except Exception:
                raise
        return symbol
