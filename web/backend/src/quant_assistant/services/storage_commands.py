from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import duckdb

from quant_assistant.models.entities import AssetType

from ._portfolio_commands_impl import (
    PositionInput,
    _require_decimal,
    _require_plain_date,
    _require_symbol,
    _to_json,
)


class _PortfolioCommandStorage:
    """Moved methods for PortfolioCommandService."""

    @staticmethod
    def _normalise_position_snapshot(
        positions: tuple[PositionInput, ...], as_of_date: date
    ) -> tuple[dict[str, object], ...]:
        if not isinstance(positions, tuple) or not positions:
            raise ValueError("组合校准必须提交非空的完整持仓元组")
        rows: list[dict[str, object]] = []
        symbols: set[str] = set()
        cash_count = 0
        for item in positions:
            if not isinstance(item, PositionInput):
                raise TypeError("校准持仓必须使用PositionInput")
            from quant_assistant.common.symbols import canonical_symbol

            symbol = canonical_symbol(item.symbol, item.asset_type)
            if symbol in symbols:
                raise ValueError(f"校准持仓包含重复标的：{symbol}")
            symbols.add(symbol)
            if item.asset_type not in {AssetType.STOCK, AssetType.FUND, AssetType.CASH}:
                raise ValueError(f"校准不接受资产类型：{item.asset_type}")
            quantity = _require_decimal(item.quantity, f"{symbol}数量")
            average_cost = _require_decimal(item.average_cost, f"{symbol}平均成本")
            market_value = _require_decimal(item.market_value, f"{symbol}市值")
            if _require_plain_date(item.as_of_date, f"{symbol}持仓日期") != as_of_date:
                raise ValueError("校准快照中的持仓日期必须完全一致")
            if quantity < 0 or average_cost < 0 or market_value < 0:
                raise ValueError(f"{symbol}数量、成本和市值不能为负")
            if item.asset_type is AssetType.STOCK and quantity != quantity.to_integral_value():
                raise ValueError(f"{symbol}股票数量必须是整股")
            if item.asset_type is AssetType.CASH:
                cash_count += 1
                if average_cost != Decimal("1") or quantity != market_value:
                    raise ValueError("现金持仓必须使用成本1且数量等于余额")
            elif quantity <= 0:
                raise ValueError(f"{symbol}非现金持仓数量必须大于0")
            rows.append(
                {
                    "symbol": symbol,
                    "asset_type": item.asset_type,
                    "quantity": quantity,
                    "average_cost": average_cost,
                    "market_value": market_value,
                    "as_of_date": as_of_date,
                }
            )
        if cash_count != 1:
            raise ValueError("组合校准必须且只能包含一个现金持仓")
        return tuple(sorted(rows, key=lambda row: str(row["symbol"])))

    @staticmethod
    def _portfolio_tracking_started(connection: duckdb.DuckDBPyConnection) -> bool:
        row = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM portfolio_valuations)
              + (SELECT count(*) FROM portfolio_events
                 WHERE event_type = 'baseline' AND status = 'confirmed')
            """
        ).fetchone()
        return bool(row and row[0])

    @staticmethod
    def _cash_state(
        connection: duckdb.DuckDBPyConnection,
        requested_symbol: str | None,
    ) -> dict[str, object]:
        if requested_symbol is None:
            rows = connection.execute(
                """
                SELECT symbol, quantity, average_cost, market_value, as_of_date
                FROM positions WHERE asset_type = 'cash' ORDER BY symbol
                """
            ).fetchall()
            if not rows:
                raise ValueError("缺少现金持仓，无法维护双分录账本")
            if len(rows) != 1:
                raise ValueError("存在多个现金持仓，请显式指定cash_symbol")
            row = rows[0]
        else:
            symbol = _require_symbol(requested_symbol)
            row = connection.execute(
                """
                SELECT symbol, quantity, average_cost, market_value, as_of_date
                FROM positions WHERE symbol = ? AND asset_type = 'cash'
                """,
                [symbol],
            ).fetchone()
            if row is None:
                raise ValueError(f"现金持仓不存在：{symbol}")
        return {
            "symbol": row[0],
            "quantity": _require_decimal(row[1], "现金数量"),
            "average_cost": _require_decimal(row[2], "现金成本"),
            "market_value": _require_decimal(row[3], "现金余额"),
            "as_of_date": row[4],
        }

    @staticmethod
    def _write_cash_position(
        connection: duckdb.DuckDBPyConnection,
        symbol: str,
        balance: Decimal,
        as_of_date: date,
        updated_at: datetime,
    ) -> None:
        if balance < 0:
            raise ValueError("现金余额不能为负")
        connection.execute(
            """
            UPDATE positions
            SET quantity = ?, average_cost = 1, market_value = ?,
                as_of_date = ?, updated_at = ?
            WHERE symbol = ? AND asset_type = 'cash'
            """,
            [balance, balance, as_of_date, updated_at, symbol],
        )
        if (
            connection.execute(
                "SELECT count(*) FROM positions WHERE symbol = ?", [symbol]
            ).fetchone()[0]
            != 1
        ):
            raise RuntimeError("现金持仓更新失败")

    @staticmethod
    def _require_known_instrument(
        connection: duckdb.DuckDBPyConnection, symbol: str, asset_type: AssetType
    ) -> None:
        row = connection.execute(
            "SELECT asset_type FROM instruments WHERE symbol = ?", [symbol]
        ).fetchone()
        if row is None:
            raise ValueError(f"未知标的：{symbol}")
        if row[0] != asset_type.value:
            raise ValueError(f"标的{symbol}的资产类型为{row[0]}，不是{asset_type.value}")

    @staticmethod
    def _position_state(
        connection: duckdb.DuckDBPyConnection, symbol: str
    ) -> dict[str, object] | None:
        row = connection.execute(
            """
            SELECT symbol, asset_type, quantity, average_cost, market_value, as_of_date
            FROM positions WHERE symbol = ?
            """,
            [symbol],
        ).fetchone()
        if row is None:
            return None
        return {
            "symbol": row[0],
            "asset_type": row[1],
            "quantity": row[2],
            "average_cost": row[3],
            "market_value": row[4],
            "as_of_date": row[5],
        }

    @staticmethod
    def _insert_audit(
        connection: duckdb.DuckDBPyConnection,
        entity_type: str,
        entity_id: str,
        changed_at: datetime,
        before: object | None,
        after: object,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log (
                entity_type, entity_id, changed_at, before_json, after_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [entity_type, entity_id, changed_at, _to_json(before), _to_json(after)],
        )
