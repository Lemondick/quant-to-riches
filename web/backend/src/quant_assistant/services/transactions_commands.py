from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from quant_assistant.common.symbols import canonical_symbol
from quant_assistant.models.entities import Action, AssetType
from quant_assistant.services.commands_common import LedgerStateError

from ._portfolio_commands_impl import (
    BUY_ACTIONS,
    SELL_ACTIONS,
    TRANSACTION_ACTIONS,
    DividendInput,
    InstrumentInput,
    TransactionInput,
    _clock_now,
    _require_decimal,
    _require_shanghai_datetime,
    _require_symbol,
    _same_datetime,
)


@dataclass(frozen=True, slots=True)
class _ValidatedTransaction:
    """Canonical transaction values shared by single-row and batch paths."""

    symbol: str
    asset_type: AssetType
    action: Action
    quantity: Decimal
    price: Decimal
    fees: Decimal
    traded_at: datetime
    trade_date: date
    external_id: str
    source: str
    cash_symbol: str | None
    note: str | None
    gross_amount: Decimal
    cash_delta: Decimal


def _validate_transaction_input(item: TransactionInput) -> _ValidatedTransaction:
    """Normalize and validate one transaction before touching the database."""

    if not isinstance(item, TransactionInput):
        raise TypeError("成交必须使用TransactionInput")
    symbol = canonical_symbol(item.symbol, item.asset_type)
    if not isinstance(item.asset_type, AssetType):
        raise TypeError("资产类型必须是AssetType")
    if item.asset_type not in {AssetType.STOCK, AssetType.FUND}:
        raise ValueError("实际成交只接受股票或基金")
    if not isinstance(item.action, Action):
        raise TypeError("交易动作必须是Action")
    if item.action not in TRANSACTION_ACTIONS:
        raise ValueError("交易记录只接受BUY、ADD、REDUCE或SELL")
    quantity = _require_decimal(item.quantity, "交易数量")
    price = _require_decimal(item.price, "交易价格")
    fees = _require_decimal(item.fees, "手续费")
    if quantity <= 0:
        raise ValueError("交易数量必须大于0")
    if price <= 0:
        raise ValueError("交易价格必须大于0")
    if fees < 0:
        raise ValueError("手续费不能为负")
    traded_at = _require_shanghai_datetime(item.traded_at, "交易时间")
    external_id = item.external_id.strip()
    source = item.source.strip().lower()
    if not external_id:
        raise ValueError("成交编号不能为空")
    if not source:
        raise ValueError("成交来源不能为空")
    note = None if item.note is None else item.note.strip() or None
    cash_symbol = None if item.cash_symbol is None else _require_symbol(item.cash_symbol)
    if item.asset_type is AssetType.STOCK:
        if quantity != quantity.to_integral_value():
            raise ValueError("股票交易数量必须是整股")
        if item.action in BUY_ACTIONS and quantity % Decimal(100) != 0:
            raise ValueError("股票买入/加仓数量必须是100股的整数倍")
    gross_amount = quantity * price
    cash_delta = -(gross_amount + fees) if item.action in BUY_ACTIONS else gross_amount - fees
    if item.action in SELL_ACTIONS and cash_delta < 0:
        raise ValueError("卖出手续费不能超过成交金额")
    return _ValidatedTransaction(
        symbol=symbol,
        asset_type=item.asset_type,
        action=item.action,
        quantity=quantity,
        price=price,
        fees=fees,
        traded_at=traded_at,
        trade_date=traded_at.date(),
        external_id=external_id,
        source=source,
        cash_symbol=cash_symbol,
        note=note,
        gross_amount=gross_amount,
        cash_delta=cash_delta,
    )


class _PortfolioCommandTransactions:
    """Moved methods for PortfolioCommandService."""

    def record_transaction(self, item: TransactionInput) -> int:
        """Insert one idempotent trade and update security plus cash atomically."""
        with self._database.transaction() as connection:
            return self._record_transaction(item, connection)

    def _record_transaction(self, item: TransactionInput, connection) -> int:
        """Insert one idempotent trade and update security plus cash atomically."""

        validated = _validate_transaction_input(item)
        symbol = validated.symbol
        asset_type = validated.asset_type
        action = validated.action
        quantity = validated.quantity
        price = validated.price
        fees = validated.fees
        traded_at = validated.traded_at
        trade_date = validated.trade_date
        external_id = validated.external_id
        source = validated.source
        note = validated.note
        gross_amount = validated.gross_amount
        cash_delta = validated.cash_delta
        recorded_at = _clock_now(self._clock)
        if traded_at > recorded_at:
            raise ValueError("交易时间不能晚于记录时间")

        existing_trade = connection.execute(
            """
                SELECT id, symbol, asset_type, action, quantity, price, fees,
                       traded_at, cash_symbol, note
                FROM transactions WHERE source = ? AND external_id = ?
                """,
            [source, external_id],
        ).fetchone()
        if existing_trade is not None:
            expected_cash_symbol = validated.cash_symbol
            same = (
                existing_trade[1] == symbol
                and existing_trade[2] == asset_type.value
                and existing_trade[3] == action.value
                and existing_trade[4] == quantity
                and existing_trade[5] == price
                and existing_trade[6] == fees
                and _same_datetime(existing_trade[7], traded_at)
                and (expected_cash_symbol is None or existing_trade[8] == expected_cash_symbol)
                and existing_trade[9] == note
            )
            if not same:
                raise ValueError("相同成交来源与编号对应不同内容")
            return int(existing_trade[0])

        latest_valuation = connection.execute(
            """
                SELECT as_of_date FROM portfolio_valuations
                ORDER BY as_of_date DESC, generated_at DESC, run_id DESC LIMIT 1
                """
        ).fetchone()
        if latest_valuation is not None and trade_date <= latest_valuation[0]:
            raise ValueError("成交日期不得早于或等于最近已确认估值日")
        if latest_valuation is None:
            confirmed_baseline = connection.execute(
                """
                    SELECT count(*) FROM portfolio_events
                    WHERE event_type = 'baseline' AND status = 'confirmed'
                    """
            ).fetchone()[0]
            if confirmed_baseline:
                raise LedgerStateError(
                    "nav_baseline_pending", "请先完成净值基线对应的首个有效日决策"
                )

        self._require_known_instrument(connection, symbol, asset_type)
        cash = self._cash_state(connection, validated.cash_symbol)
        if cash["as_of_date"] > trade_date:
            raise ValueError("交易时间早于当前现金账本日期，无法重算现金")
        new_cash = cash["market_value"] + cash_delta
        if new_cash < 0:
            raise LedgerStateError(
                "insufficient_cash",
                f"可用现金{cash['market_value']}不足以支付{abs(cash_delta)}",
            )
        current = self._position_state(connection, symbol)
        if current is not None and current["as_of_date"] > trade_date:
            raise ValueError("交易时间早于当前持仓日期，无法重算持仓")

        old_quantity = Decimal("0") if current is None else current["quantity"]
        old_average_cost = Decimal("0") if current is None else current["average_cost"]
        if action in SELL_ACTIONS and quantity > old_quantity:
            raise LedgerStateError(
                "insufficient_position", f"卖出数量{quantity}超过实际持仓{old_quantity}"
            )

        result = connection.execute(
            """
                INSERT INTO transactions (
                    symbol, asset_type, action, quantity, price,
                    fees, traded_at, recorded_at, note, source, external_id,
                    gross_amount, cash_delta, cash_symbol
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
            [
                symbol,
                asset_type.value,
                action.value,
                quantity,
                price,
                fees,
                traded_at,
                recorded_at,
                note,
                source,
                external_id,
                gross_amount,
                cash_delta,
                cash["symbol"],
            ],
        ).fetchone()
        if result is None:
            raise RuntimeError("DuckDB did not return a transaction id")
        transaction_id = int(result[0])

        if action in BUY_ACTIONS:
            new_quantity = old_quantity + quantity
            new_average_cost = (
                old_quantity * old_average_cost + quantity * price + fees
            ) / new_quantity
        else:
            new_quantity = old_quantity - quantity
            new_average_cost = old_average_cost

        if new_quantity == 0:
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
                    asset_type.value,
                    new_quantity,
                    new_average_cost,
                    new_quantity * price,
                    trade_date,
                    recorded_at,
                ],
            )

        self._write_cash_position(
            connection,
            str(cash["symbol"]),
            new_cash,
            trade_date,
            recorded_at,
        )

        after = {
            "source": source,
            "external_id": external_id,
            "symbol": symbol,
            "asset_type": asset_type,
            "action": action,
            "quantity": quantity,
            "price": price,
            "fees": fees,
            "gross_amount": gross_amount,
            "cash_delta": cash_delta,
            "cash_symbol": cash["symbol"],
            "cash_before": cash["market_value"],
            "cash_after": new_cash,
            "traded_at": traded_at,
            "note": note,
        }
        self._insert_audit(
            connection,
            "transaction",
            str(transaction_id),
            recorded_at,
            current,
            after,
        )
        return transaction_id

    def record_dividend(self, item: DividendInput) -> int:
        """Record one idempotent cash dividend and update local cash atomically."""

        if not isinstance(item, DividendInput):
            raise TypeError("分红记录必须使用DividendInput")
        symbol = canonical_symbol(item.symbol, item.asset_type)
        if item.asset_type not in {AssetType.STOCK, AssetType.FUND}:
            raise ValueError("现金分红只接受股票或基金")
        gross_amount = _require_decimal(item.gross_amount, "分红金额")
        fees = _require_decimal(item.fees, "分红费用")
        if gross_amount <= 0 or fees < 0 or fees >= gross_amount:
            raise ValueError("分红金额必须为正且费用必须小于分红金额")
        paid_at = _require_shanghai_datetime(item.paid_at, "到账时间")
        recorded_at = _clock_now(self._clock)
        if paid_at > recorded_at:
            raise ValueError("分红到账时间不能晚于记录时间")
        source = item.source.strip().lower()
        external_id = item.external_id.strip()
        if not source or not external_id:
            raise ValueError("分红来源和编号不能为空")
        note = None if item.note is None else item.note.strip() or None
        net_amount = gross_amount - fees

        with self._database.transaction() as connection:
            existing = connection.execute(
                """
                    SELECT id, symbol, asset_type, gross_amount, fees, paid_at, note
                    FROM dividend_events WHERE source = ? AND external_id = ?
                    """,
                [source, external_id],
            ).fetchone()
            if existing is not None:
                if not (
                    existing[1] == symbol
                    and existing[2] == item.asset_type.value
                    and existing[3] == gross_amount
                    and existing[4] == fees
                    and _same_datetime(existing[5], paid_at)
                    and existing[6] == note
                ):
                    raise ValueError("相同分红来源与编号对应不同内容")
                return int(existing[0])

            self._require_known_instrument(connection, symbol, item.asset_type)
            cash = self._cash_state(connection, item.cash_symbol)
            if cash["as_of_date"] > paid_at.date():
                raise ValueError("分红时间早于当前现金账本日期，无法重算现金")
            result = connection.execute(
                """
                    INSERT INTO dividend_events (
                        symbol, asset_type, gross_amount, fees, net_amount,
                        paid_at, recorded_at, source, external_id, cash_symbol, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                [
                    symbol,
                    item.asset_type.value,
                    gross_amount,
                    fees,
                    net_amount,
                    paid_at,
                    recorded_at,
                    source,
                    external_id,
                    cash["symbol"],
                    note,
                ],
            ).fetchone()
            if result is None:
                raise RuntimeError("DuckDB did not return a dividend id")
            dividend_id = int(result[0])
            self._write_cash_position(
                connection,
                str(cash["symbol"]),
                cash["market_value"] + net_amount,
                paid_at.date(),
                recorded_at,
            )
            self._insert_audit(
                connection,
                "dividend",
                str(dividend_id),
                recorded_at,
                None,
                {
                    "symbol": symbol,
                    "asset_type": item.asset_type,
                    "gross_amount": gross_amount,
                    "fees": fees,
                    "net_amount": net_amount,
                    "paid_at": paid_at,
                    "source": source,
                    "external_id": external_id,
                },
            )
        return dividend_id

    def record_transaction_batch(
        self,
        items: tuple[TransactionInput, ...],
        *,
        instrument_metadata: tuple[InstrumentInput, ...] = (),
    ) -> tuple[int, ...]:
        """Commit all reviewed trades and metadata together, or roll everything back."""

        if not isinstance(items, tuple) or not items:
            raise ValueError("批量成交必须提交非空tuple")
        seen: set[tuple[str, str]] = set()
        for item in items:
            validated = _validate_transaction_input(item)
            key = (validated.source, validated.external_id)
            if key in seen:
                raise ValueError("批量成交包含重复来源与编号")
            seen.add(key)
        metadata = self._normalise_instrument_metadata(instrument_metadata, None)
        with self._database.transaction() as connection:
            self._preflight_transaction_batch(items, metadata)
            if metadata:
                self._insert_missing_instruments(connection, metadata)
                for item in metadata:
                    self._require_known_instrument(connection, item.symbol, item.asset_type)
            recorded = [self._record_transaction(item, connection) for item in items]
        return tuple(recorded)

    def record_transactions(
        self,
        items: tuple[TransactionInput, ...],
        *,
        instrument_metadata: tuple[InstrumentInput, ...] = (),
    ) -> tuple[int, ...]:
        """Alias used by import clients that prefer the plural command name."""

        return self.record_transaction_batch(items, instrument_metadata=instrument_metadata)

    def _preflight_transaction_batch(
        self,
        items: tuple[TransactionInput, ...],
        instrument_metadata: tuple[InstrumentInput, ...] = (),
    ) -> None:
        """Validate all rows against simulated cash and holdings within the batch transaction."""

        with self._database.connection() as connection:
            latest = connection.execute(
                """
                SELECT as_of_date FROM portfolio_valuations
                ORDER BY as_of_date DESC, generated_at DESC, run_id DESC LIMIT 1
                """
            ).fetchone()
            latest_date = None if latest is None else latest[0]
            baseline = connection.execute(
                """
                SELECT count(*) FROM portfolio_events
                WHERE event_type = 'baseline' AND status = 'confirmed'
                """
            ).fetchone()[0]
            cash_row = connection.execute(
                """
                SELECT symbol, market_value, as_of_date
                FROM positions WHERE asset_type = 'cash'
                ORDER BY symbol LIMIT 1
                """
            ).fetchone()
            if cash_row is None:
                raise LedgerStateError("missing_cash_position", "批量成交前必须存在现金持仓")
            cash_symbol, cash_value, cash_date = cash_row
            cash_value = Decimal(str(cash_value))
            quantities = {
                str(row[0]): Decimal(str(row[1]))
                for row in connection.execute(
                    "SELECT symbol, quantity FROM positions WHERE asset_type <> 'cash'"
                ).fetchall()
            }
            known = {
                str(row[0]).upper(): str(row[1])
                for row in connection.execute(
                    "SELECT symbol, asset_type FROM instruments"
                ).fetchall()
            }
            for metadata in instrument_metadata:
                existing_type = known.get(metadata.symbol)
                if existing_type is not None and existing_type != metadata.asset_type.value:
                    raise ValueError(
                        f"标的{metadata.symbol}的资产类型为{existing_type}，不是{metadata.asset_type.value}"
                    )
                known.setdefault(metadata.symbol, metadata.asset_type.value)
            for item in items:
                validated = _validate_transaction_input(item)
                symbol = validated.symbol
                source = validated.source
                external_id = validated.external_id
                existing = connection.execute(
                    """
                    SELECT symbol, asset_type, action, quantity, price, fees,
                           traded_at, note, cash_symbol
                    FROM transactions WHERE source = ? AND external_id = ?
                    """,
                    [source, external_id],
                ).fetchone()
                if existing is not None:
                    same_existing = (
                        existing[0] == symbol
                        and existing[1] == validated.asset_type.value
                        and existing[2] == validated.action.value
                        and existing[3] == validated.quantity
                        and existing[4] == validated.price
                        and existing[5] == validated.fees
                        and _same_datetime(existing[6], validated.traded_at)
                        and existing[7] == validated.note
                        and (validated.cash_symbol is None or existing[8] == validated.cash_symbol)
                    )
                    if not same_existing:
                        raise ValueError("相同成交来源与编号对应不同内容")
                    continue
                if known.get(symbol) != validated.asset_type.value:
                    raise ValueError(f"交易标的未登记或资产类型不匹配：{symbol}")
                quantity = validated.quantity
                traded_at = validated.traded_at
                if traded_at > _clock_now(self._clock):
                    raise ValueError("交易时间不能晚于记录时间")
                trade_date = traded_at.date()
                if latest_date is not None and trade_date <= latest_date:
                    raise ValueError("成交日期不得早于或等于最近已确认估值日")
                if latest_date is None and baseline:
                    raise LedgerStateError(
                        "nav_baseline_pending", "请先完成净值基线对应的首个有效日决策"
                    )
                if cash_date > trade_date:
                    raise ValueError("交易时间早于当前现金账本日期，无法重算现金")
                delta = validated.cash_delta
                cash_value += delta
                if cash_value < 0:
                    raise LedgerStateError("insufficient_cash", f"可用现金不足以支付{abs(delta)}")
                old_quantity = quantities.get(symbol, Decimal("0"))
                if validated.action in SELL_ACTIONS:
                    if quantity > old_quantity:
                        raise LedgerStateError(
                            "insufficient_position", f"卖出数量{quantity}超过实际持仓{old_quantity}"
                        )
                    quantities[symbol] = old_quantity - quantity
                else:
                    quantities[symbol] = old_quantity + quantity
