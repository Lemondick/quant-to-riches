"""Atomic company-action and immutable ledger-correction commands."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import duckdb

from quant_assistant.models.entities import AssetType
from quant_assistant.services.commands_common import LedgerStateError

from ._portfolio_commands_impl import (
    CorporateActionInput,
    DividendNotFoundError,
    LedgerCorrectionInput,
    TransactionNotFoundError,
    _clock_now,
    _require_decimal,
    _require_shanghai_datetime,
    _require_symbol,
    _same_datetime,
)

_ACTION_TYPES = frozenset({"dividend_reinvestment", "fund_conversion", "split", "custody_transfer"})
_ZERO = Decimal("0")


class _PortfolioCommandCorporateActions:
    """Methods mixed into :class:`PortfolioCommandService`."""

    def record_corporate_action(self, item: CorporateActionInput) -> int:
        """Apply one confirmed company action and persist its source event.

        The event is idempotent by ``(source, external_id)``.  Position and cash
        updates happen in the same transaction, so a malformed action cannot
        leave half-applied state.
        """

        if not isinstance(item, CorporateActionInput):
            raise TypeError("公司行动必须使用CorporateActionInput")
        action_type = str(item.action_type).strip().lower()
        if action_type not in _ACTION_TYPES:
            raise ValueError("不支持的公司行动类型")
        if not isinstance(item.asset_type, AssetType) or item.asset_type not in {
            AssetType.STOCK,
            AssetType.FUND,
        }:
            raise ValueError("公司行动只接受股票或基金")
        from quant_assistant.common.symbols import canonical_symbol

        symbol = canonical_symbol(item.symbol, item.asset_type)
        target_symbol = (
            None
            if item.target_symbol is None
            else canonical_symbol(item.target_symbol, item.asset_type)
        )
        if target_symbol == symbol and action_type == "fund_conversion":
            raise ValueError("基金转换的目标不能与来源相同")
        effective_at = _require_shanghai_datetime(item.effective_at, "公司行动时间")
        recorded_at = _clock_now(self._clock)
        if effective_at > recorded_at:
            raise ValueError("公司行动时间不能晚于记录时间")
        source = str(item.source).strip().lower()
        external_id = str(item.external_id).strip()
        if not source or not external_id:
            raise ValueError("公司行动来源和编号不能为空")
        note = None if item.note is None else str(item.note).strip() or None

        quantity = _optional_positive(item.quantity, "公司行动数量")
        target_quantity = _optional_positive(item.target_quantity, "目标数量")
        price = _optional_positive(item.price, "公司行动价格")
        target_price = _optional_positive(item.target_price, "目标价格")
        if action_type == "fund_conversion" and target_price is None:
            target_price = price
        gross_amount = _require_decimal(item.gross_amount, "公司行动金额")
        fees = _require_decimal(item.fees, "公司行动费用")
        ratio = _optional_positive(item.ratio, "折算比例")
        if gross_amount < 0 or fees < 0:
            raise ValueError("公司行动金额和费用不能为负")
        requested_cash_symbol = (
            None if item.cash_symbol is None else _require_symbol(item.cash_symbol)
        )

        with self._database.transaction() as connection:
            try:
                duplicate = connection.execute(
                    """
                    SELECT id, action_type, symbol, target_symbol, effective_at,
                           quantity, target_quantity, price, target_price,
                           gross_amount, fees, ratio, cash_symbol,
                           from_account, to_account
                    FROM corporate_action_events
                    WHERE source = ? AND external_id = ?
                    """,
                    [source, external_id],
                ).fetchone()
                if duplicate is not None:
                    duplicate_matches = (
                        str(duplicate[1]) == action_type
                        and str(duplicate[2]) == symbol
                        and _same_text(duplicate[3], target_symbol)
                        and _same_datetime(duplicate[4], effective_at)
                        and _same_supplied_decimal(duplicate[5], quantity, item.quantity)
                        and _same_supplied_decimal(
                            duplicate[6], target_quantity, item.target_quantity
                        )
                        and _same_supplied_decimal(duplicate[7], price, item.price)
                        and _same_supplied_decimal(duplicate[8], target_price, item.target_price)
                        and _same_decimal(duplicate[9], gross_amount)
                        and _same_decimal(duplicate[10], fees)
                        and _same_supplied_decimal(duplicate[11], ratio, item.ratio)
                        and _same_supplied_text(duplicate[12], item.cash_symbol)
                        and _same_supplied_text(duplicate[13], item.from_account)
                        and _same_supplied_text(duplicate[14], item.to_account)
                    )
                    if not duplicate_matches:
                        raise ValueError("相同公司行动来源与编号对应不同内容")
                    return int(duplicate[0])

                latest = connection.execute(
                    """
                    SELECT as_of_date FROM portfolio_valuations
                    ORDER BY as_of_date DESC, generated_at DESC, run_id DESC LIMIT 1
                    """
                ).fetchone()
                if latest is not None and effective_at.date() <= latest[0]:
                    raise ValueError("公司行动日期不得早于或等于最近已确认估值日")
                self._require_known_instrument(connection, symbol, item.asset_type)
                source_position = self._position_state(connection, symbol)
                if source_position is None and action_type != "custody_transfer":
                    raise ValueError(f"公司行动标的没有当前持仓：{symbol}")

                cash_delta = _ZERO
                stored_cash_symbol = requested_cash_symbol
                stored_quantity = quantity
                stored_target_quantity = target_quantity
                stored_ratio = ratio

                if action_type == "dividend_reinvestment":
                    if price is None:
                        raise ValueError("红利再投资必须提供再投资价格")
                    if gross_amount <= 0:
                        raise ValueError("红利再投资金额必须大于0")
                    if stored_quantity is None:
                        stored_quantity = (gross_amount / price).normalize()
                    if stored_quantity <= 0:
                        raise ValueError("红利再投资数量必须大于0")
                    cash = self._cash_state(connection, requested_cash_symbol)
                    stored_cash_symbol = str(cash["symbol"])
                    cash_delta = -(gross_amount + fees)
                    if cash["market_value"] + cash_delta < 0:
                        raise ValueError("可用现金不足以完成红利再投资")
                    old_quantity = _decimal_from_row(source_position, "quantity")
                    old_cost = _decimal_from_row(source_position, "average_cost")
                    new_quantity = old_quantity + stored_quantity
                    new_cost = (old_quantity * old_cost + gross_amount + fees) / new_quantity
                    self._write_asset_position(
                        connection,
                        symbol,
                        item.asset_type,
                        new_quantity,
                        new_cost,
                        new_quantity * price,
                        effective_at.date(),
                        recorded_at,
                    )
                    self._write_cash_position(
                        connection,
                        str(cash["symbol"]),
                        cash["market_value"] + cash_delta,
                        effective_at.date(),
                        recorded_at,
                    )

                elif action_type == "fund_conversion":
                    if item.asset_type is not AssetType.FUND:
                        raise ValueError("基金转换的来源必须是基金")
                    if target_symbol is None or target_quantity is None:
                        raise ValueError("基金转换必须提供目标代码和目标数量")
                    self._require_known_instrument(connection, target_symbol, AssetType.FUND)
                    old_quantity = _decimal_from_row(source_position, "quantity")
                    if quantity is None:
                        quantity = old_quantity
                        stored_quantity = quantity
                    if quantity > old_quantity:
                        raise ValueError("基金转换数量超过当前持仓")
                    old_cost = _decimal_from_row(source_position, "average_cost")
                    transferred_cost = old_cost * quantity
                    remaining = old_quantity - quantity
                    if remaining == 0:
                        connection.execute("DELETE FROM positions WHERE symbol = ?", [symbol])
                    else:
                        self._write_asset_position(
                            connection,
                            symbol,
                            item.asset_type,
                            remaining,
                            old_cost,
                            _decimal_from_row(source_position, "market_value")
                            * remaining
                            / old_quantity,
                            effective_at.date(),
                            recorded_at,
                        )
                    target_position = self._position_state(connection, target_symbol)
                    target_old_quantity = (
                        _decimal_from_row(target_position, "quantity")
                        if target_position is not None
                        else _ZERO
                    )
                    target_old_cost = (
                        _decimal_from_row(target_position, "average_cost")
                        if target_position is not None
                        else _ZERO
                    )
                    new_target_quantity = target_old_quantity + target_quantity
                    new_target_cost = (
                        target_old_quantity * target_old_cost + transferred_cost
                    ) / new_target_quantity
                    target_mark = (
                        new_target_quantity * target_price
                        if target_price is not None
                        else new_target_quantity * new_target_cost
                    )
                    self._write_asset_position(
                        connection,
                        target_symbol,
                        AssetType.FUND,
                        new_target_quantity,
                        new_target_cost,
                        target_mark,
                        effective_at.date(),
                        recorded_at,
                    )

                elif action_type == "split":
                    if ratio is None:
                        raise ValueError("拆分折算必须提供正数比例")
                    old_quantity = _decimal_from_row(source_position, "quantity")
                    if quantity is None:
                        quantity = old_quantity
                        stored_quantity = quantity
                    if quantity > old_quantity:
                        raise ValueError("拆分数量超过当前持仓")
                    stored_target_quantity = quantity * ratio
                    new_total = old_quantity - quantity + stored_target_quantity
                    old_cost_basis = old_quantity * _decimal_from_row(
                        source_position, "average_cost"
                    )
                    new_cost = old_cost_basis / new_total
                    mark = (
                        _decimal_from_row(source_position, "market_value")
                        if price is None
                        else new_total * price
                    )
                    self._write_asset_position(
                        connection,
                        symbol,
                        item.asset_type,
                        new_total,
                        new_cost,
                        mark,
                        effective_at.date(),
                        recorded_at,
                    )

                else:  # custody_transfer
                    if quantity is None or quantity <= 0:
                        raise ValueError("转托管必须提供正数数量")
                    if source_position is not None and quantity > _decimal_from_row(
                        source_position, "quantity"
                    ):
                        raise ValueError("转托管数量超过当前持仓")
                    if (
                        not str(item.from_account or "").strip()
                        or not str(item.to_account or "").strip()
                    ):
                        raise ValueError("转托管必须填写转出和转入账户")

                row = connection.execute(
                    """
                    INSERT INTO corporate_action_events (
                        action_type, symbol, target_symbol, asset_type, quantity,
                        target_quantity, price, target_price, gross_amount, fees,
                        cash_delta, ratio, effective_at, recorded_at, source,
                        external_id, cash_symbol, from_account, to_account, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    [
                        action_type,
                        symbol,
                        target_symbol,
                        item.asset_type.value,
                        stored_quantity,
                        stored_target_quantity,
                        price,
                        target_price,
                        gross_amount,
                        fees,
                        cash_delta,
                        stored_ratio,
                        effective_at,
                        recorded_at,
                        source,
                        external_id,
                        stored_cash_symbol,
                        item.from_account,
                        item.to_account,
                        note,
                    ],
                ).fetchone()
                if row is None:
                    raise RuntimeError("DuckDB did not return a corporate action id")
                action_id = int(row[0])
                self._insert_audit(
                    connection,
                    "corporate_action",
                    str(action_id),
                    recorded_at,
                    None,
                    {
                        "action_type": action_type,
                        "symbol": symbol,
                        "target_symbol": target_symbol,
                        "quantity": stored_quantity,
                        "target_quantity": stored_target_quantity,
                        "cash_delta": cash_delta,
                        "external_id": external_id,
                    },
                )
                return action_id
            except Exception:
                # ``Database.transaction`` owns rollback; keeping this boundary
                # explicit makes the command's atomicity obvious without
                # duplicating connection-level transaction handling.
                raise

    def correct_ledger_entry(self, item: LedgerCorrectionInput) -> int:
        """Reverse a transaction/dividend without mutating its original row."""

        if not isinstance(item, LedgerCorrectionInput):
            raise TypeError("冲正必须使用LedgerCorrectionInput")
        target_type = str(item.target_type).strip().lower()
        if target_type not in {"transaction", "dividend"}:
            raise ValueError("冲正目标必须是transaction或dividend")
        if (
            isinstance(item.target_id, bool)
            or not isinstance(item.target_id, int)
            or item.target_id <= 0
        ):
            raise ValueError("冲正目标编号无效")
        reason = str(item.reason).strip()
        if not reason:
            raise ValueError("冲正原因不能为空")
        effective_at = _require_shanghai_datetime(item.effective_at, "冲正时间")
        recorded_at = _clock_now(self._clock)
        if effective_at > recorded_at:
            raise ValueError("冲正时间不能晚于记录时间")
        source = str(item.source).strip().lower()
        external_id = str(item.external_id).strip()
        if not source or not external_id:
            raise ValueError("冲正来源和编号不能为空")
        note = None if item.note is None else str(item.note).strip() or None

        with self._database.transaction() as connection:
            try:
                duplicate = connection.execute(
                    """
                    SELECT id, target_type, target_id, reason, effective_at, note
                    FROM ledger_corrections WHERE source = ? AND external_id = ?
                    """,
                    [source, external_id],
                ).fetchone()
                if duplicate is not None:
                    if not (
                        str(duplicate[1]) == target_type
                        and int(duplicate[2]) == item.target_id
                        and str(duplicate[3]) == reason
                        and _same_datetime(duplicate[4], effective_at)
                        and _same_text(duplicate[5], note)
                    ):
                        raise ValueError("相同冲正来源与编号对应不同内容")
                    return int(duplicate[0])
                latest = connection.execute(
                    """
                    SELECT as_of_date FROM portfolio_valuations
                    ORDER BY as_of_date DESC, generated_at DESC, run_id DESC LIMIT 1
                    """
                ).fetchone()
                if latest is not None and effective_at.date() <= latest[0]:
                    raise ValueError("冲正日期不得早于或等于最近已确认估值日")
                if target_type == "transaction":
                    row = connection.execute(
                        """
                        SELECT id, symbol, asset_type, action, quantity, price, fees,
                               gross_amount, cash_delta, cash_symbol, traded_at,
                               reversed_by_correction_id
                        FROM transactions WHERE id = ?
                        """,
                        [item.target_id],
                    ).fetchone()
                    if row is None:
                        raise TransactionNotFoundError()
                    if row[11] is not None:
                        raise ValueError("该成交已经冲正")
                    symbol, asset_type, action = str(row[1]), AssetType(row[2]), str(row[3])
                    quantity = _decimal_from_value(row[4], "交易数量")
                    price = _decimal_from_value(row[5], "交易价格")
                    fees = _decimal_from_value(row[6], "交易费用")
                    position = self._position_state(connection, symbol)
                    cash = self._cash_state(connection, row[9])
                    _require_correction_date(effective_at, row[10], cash, position)
                    audit = connection.execute(
                        "SELECT id, before_json FROM audit_log WHERE entity_type='transaction' "
                        "AND entity_id=? ORDER BY id LIMIT 1",
                        [str(item.target_id)],
                    ).fetchone()
                    revision = connection.execute(
                        "SELECT before_json FROM audit_log WHERE entity_type='transaction_replay' "
                        "AND entity_id=? ORDER BY id DESC LIMIT 1",
                        [str(item.target_id)],
                    ).fetchone()
                    dated_dependency = connection.execute(
                        "SELECT 1 FROM corporate_action_events WHERE effective_at>=? "
                        "AND (symbol=? OR target_symbol=?) UNION ALL "
                        "SELECT 1 FROM portfolio_events WHERE event_type='calibration' "
                        "AND status='confirmed' AND effective_date>=? LIMIT 1",
                        [row[10], symbol, symbol, row[10].date()],
                    ).fetchone()
                    if (
                        dated_dependency
                        or audit
                        and connection.execute(
                            "SELECT 1 FROM audit_log WHERE id>? AND ("
                            "entity_type='portfolio_calibration' OR "
                            "(entity_type='position' AND entity_id=?) OR "
                            "(entity_type='corporate_action' AND "
                            "(json_extract_string(after_json, '$.symbol')=? OR "
                            "json_extract_string(after_json, '$.target_symbol')=?))) LIMIT 1",
                            [audit[0], symbol, symbol, symbol],
                        ).fetchone()
                    ):
                        raise LedgerStateError(
                            "ledger_correction_dependent_events",
                            "成交后已有份额变化或账户校准，不能使用原单位成本直接冲正",
                        )
                    current_quantity = (
                        _decimal_from_row(position, "quantity") if position is not None else _ZERO
                    )
                    buy = action in {"buy", "add"}
                    if (
                        buy
                        and connection.execute(
                            "SELECT 1 FROM transactions WHERE symbol=? "
                            "AND (traded_at>? OR (traded_at=? AND id>?)) "
                            "AND action IN ('sell','reduce') "
                            "AND reversed_by_correction_id IS NULL LIMIT 1",
                            [symbol, row[10], row[10], item.target_id],
                        ).fetchone()
                    ):
                        raise LedgerStateError(
                            "ledger_correction_dependent_events",
                            "买入后已有未冲正卖出，需先核对关联记录，不能直接扣除原买入成本",
                        )
                    new_quantity = (
                        current_quantity - quantity if buy else current_quantity + quantity
                    )
                    if new_quantity < 0:
                        raise ValueError("冲正将导致持仓为负")
                    if buy and position is not None and current_quantity > 0:
                        basis = _decimal_from_row(position, "average_cost") * current_quantity
                        new_basis = basis - (quantity * price + fees)
                        if new_quantity and new_basis < 0:
                            raise LedgerStateError(
                                "ledger_correction_dependent_events",
                                "冲正后持仓成本为负，不能按零成本保存",
                            )
                        new_average = new_basis / new_quantity if new_quantity else _ZERO
                    else:
                        before_json = revision[0] if revision else audit[1] if audit else None
                        before = json.loads(before_json) if before_json else None
                        if not isinstance(before, dict) or before.get("symbol") != symbol:
                            raise LedgerStateError(
                                "ledger_correction_cost_missing",
                                "卖出冲正缺少成交前成本，不能用卖出价替代持仓成本",
                            )
                        original_average = Decimal(str(before["average_cost"]))
                        old_basis = (
                            _decimal_from_row(position, "average_cost") * current_quantity
                            if position is not None
                            else _ZERO
                        )
                        new_average = (old_basis + quantity * original_average) / new_quantity
                    # Reversing a ledger entry does not reprice the remaining
                    # asset to its cost basis. Keep its last known unit value.
                    unit_value = (
                        _decimal_from_row(position, "market_value") / current_quantity
                        if position is not None and current_quantity > 0
                        else price
                    )
                    if new_quantity == 0:
                        connection.execute("DELETE FROM positions WHERE symbol = ?", [symbol])
                    else:
                        self._write_asset_position(
                            connection,
                            symbol,
                            asset_type,
                            new_quantity,
                            new_average,
                            new_quantity * unit_value,
                            effective_at.date(),
                            recorded_at,
                        )
                    self._write_cash_position(
                        connection,
                        str(cash["symbol"]),
                        cash["market_value"] - _decimal_from_value(row[8], "现金变动"),
                        effective_at.date(),
                        recorded_at,
                    )
                else:
                    row = connection.execute(
                        """
                        SELECT id, gross_amount, net_amount, cash_symbol,
                               reversed_by_correction_id, paid_at
                        FROM dividend_events WHERE id = ?
                        """,
                        [item.target_id],
                    ).fetchone()
                    if row is None:
                        raise DividendNotFoundError()
                    if row[4] is not None:
                        raise ValueError("该分红已经冲正")
                    cash = self._cash_state(connection, row[3])
                    _require_correction_date(effective_at, row[5], cash)
                    net = _decimal_from_value(row[2], "分红净额")
                    if cash["market_value"] < net:
                        raise ValueError("冲正将导致现金为负")
                    self._write_cash_position(
                        connection,
                        str(cash["symbol"]),
                        cash["market_value"] - net,
                        effective_at.date(),
                        recorded_at,
                    )

                correction_row = connection.execute(
                    """
                    INSERT INTO ledger_corrections (
                        target_type, target_id, reason, effective_at, recorded_at,
                        source, external_id, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    [
                        target_type,
                        item.target_id,
                        reason,
                        effective_at,
                        recorded_at,
                        source,
                        external_id,
                        note,
                    ],
                ).fetchone()
                if correction_row is None:
                    raise RuntimeError("DuckDB did not return a correction id")
                correction_id = int(correction_row[0])
                table = "transactions" if target_type == "transaction" else "dividend_events"
                connection.execute(
                    f"UPDATE {table} SET reversed_by_correction_id = ? WHERE id = ?",
                    [correction_id, item.target_id],
                )
                self._insert_audit(
                    connection,
                    "ledger_correction",
                    str(correction_id),
                    recorded_at,
                    None,
                    {
                        "target_type": target_type,
                        "target_id": item.target_id,
                        "reason": reason,
                    },
                )
                return correction_id
            except Exception:
                raise

    @staticmethod
    def _write_asset_position(
        connection: duckdb.DuckDBPyConnection,
        symbol: str,
        asset_type: AssetType,
        quantity: Decimal,
        average_cost: Decimal,
        market_value: Decimal,
        as_of_date: date,
        updated_at: object,
    ) -> None:
        if quantity <= 0:
            connection.execute("DELETE FROM positions WHERE symbol = ?", [symbol])
            return
        connection.execute(
            """
            INSERT INTO positions (
                symbol, asset_type, quantity, average_cost, market_value,
                as_of_date, updated_at
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
                asset_type.value,
                quantity,
                average_cost,
                market_value,
                as_of_date,
                updated_at,
            ],
        )


def _require_correction_date(effective_at, original_at, cash, position=None):
    if (
        effective_at < original_at
        or effective_at.date() < cash["as_of_date"]
        or (position is not None and effective_at.date() < position["as_of_date"])
    ):
        raise LedgerStateError(
            "ledger_correction_date_invalid",
            "冲正不得早于原记录或当前账本日期",
        )


def _optional_positive(value: object | None, label: str) -> Decimal | None:
    if value is None:
        return None
    converted = _require_decimal(value, label)
    if converted <= 0:
        raise ValueError(f"{label}必须大于0")
    return converted


def _decimal_from_value(value: object, label: str) -> Decimal:
    return _require_decimal(value, label)


def _decimal_from_row(row: dict[str, object] | None, key: str) -> Decimal:
    if row is None or key not in row:
        raise ValueError("持仓状态缺失")
    return _decimal_from_value(row[key], key)


def _same_decimal(actual: object, expected: Decimal | None) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    return _decimal_from_value(actual, "公司行动字段") == expected


def _same_text(actual: object, expected: object | None) -> bool:
    actual_text = None if actual is None else str(actual).strip() or None
    expected_text = None if expected is None else str(expected).strip() or None
    return actual_text == expected_text


def _same_supplied_decimal(actual: object, expected: Decimal | None, supplied: object) -> bool:
    """Treat omitted optional action fields as their original default."""

    return supplied is None or _same_decimal(actual, expected)


def _same_supplied_text(actual: object, supplied: object | None) -> bool:
    return supplied is None or _same_text(actual, supplied)


__all__ = ["_PortfolioCommandCorporateActions"]
