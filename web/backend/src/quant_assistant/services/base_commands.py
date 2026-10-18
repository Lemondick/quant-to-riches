from __future__ import annotations

from datetime import date

import duckdb

from quant_assistant.models.entities import AssetType
from quant_assistant.models.repositories.database import Database

from ._portfolio_commands_impl import (
    ClockLike,
    InstrumentInput,
    NavBaselineInput,
    PositionInput,
    _clock_now,
    _require_decimal,
    _require_plain_date,
    _to_json,
)


class _PortfolioCommandBase:
    """Moved methods for PortfolioCommandService."""

    def __init__(self, database: Database, clock: ClockLike) -> None:
        if not isinstance(database, Database):
            raise TypeError("database must be a Database")
        self._database = database
        self._clock = clock

    def confirm_nav_baseline(self, item: NavBaselineInput) -> str:
        """Confirm the one-time starting point; never infer it from legacy holdings."""

        if not isinstance(item, NavBaselineInput):
            raise TypeError("净值基线必须使用NavBaselineInput")
        event_id = item.event_id.strip()
        if not event_id:
            raise ValueError("基线确认键不能为空")
        as_of_date = _require_plain_date(item.as_of_date, "基线日期")
        reason = item.reason.strip()
        if not reason:
            raise ValueError("基线确认原因不能为空")
        recorded_at = _clock_now(self._clock)

        with self._database.transaction() as connection:
            try:
                existing = connection.execute(
                    """
                    SELECT event_type, effective_date, status, reason
                    FROM portfolio_events WHERE event_id = ?
                    """,
                    [event_id],
                ).fetchone()
                expected = ("baseline", as_of_date, "confirmed", reason)
                if existing is not None:
                    if tuple(existing) != expected:
                        raise ValueError("相同基线确认键对应不同内容")
                    return event_id

                if connection.execute("SELECT count(*) FROM portfolio_valuations").fetchone()[0]:
                    raise ValueError("组合净值基线已经建立，不能静默重置历史高水位")

                state = connection.execute(
                    """
                    SELECT
                        count(*) FILTER (WHERE as_of_date <= ?) AS position_count,
                        coalesce(sum(market_value) FILTER (
                            WHERE asset_type = 'cash' AND as_of_date <= ?
                        ), 0) AS cash_value,
                        coalesce(sum(market_value) FILTER (WHERE as_of_date <= ?), 0)
                            AS total_value,
                        count(*) FILTER (WHERE asset_type = 'cash' AND as_of_date <= ?)
                            AS cash_count
                    FROM positions
                    """,
                    [as_of_date, as_of_date, as_of_date, as_of_date],
                ).fetchone()
                if state is None or int(state[0]) <= 0:
                    raise ValueError("建立净值基线前必须先录入完整持仓")
                if int(state[3]) != 1 or _require_decimal(state[1], "现金持仓") < 0:
                    raise ValueError("建立净值基线前必须录入一个非负现金持仓")
                if _require_decimal(state[2], "组合总资产") <= 0:
                    raise ValueError("建立净值基线前组合总资产必须为正")

                connection.execute(
                    """
                    INSERT INTO portfolio_events (
                        event_id, event_type, effective_date, amount,
                        status, reason, details_json, recorded_at
                    ) VALUES (?, 'baseline', ?, NULL, 'confirmed', ?, '{}', ?)
                    """,
                    [event_id, as_of_date, reason, recorded_at],
                )
                self._insert_audit(
                    connection,
                    "portfolio_baseline",
                    event_id,
                    recorded_at,
                    None,
                    {
                        "event_id": event_id,
                        "as_of_date": as_of_date,
                        "reason": reason,
                    },
                )
            except Exception:
                raise
        return event_id

    def import_initial_account(
        self,
        event_id: str,
        as_of_date: date,
        positions: tuple[PositionInput, ...],
        *,
        reason: str,
        instrument_metadata: tuple[InstrumentInput, ...] = (),
    ) -> str:
        """Atomically seed a previously empty account and its NAV baseline.

        This is deliberately narrower than portfolio calibration: it can only be used
        before any baseline, valuation, or transaction exists.  The caller must have
        already validated that the rows came from a complete external account export.
        """

        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("账户导入确认键不能为空")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("账户导入原因不能为空")
        normalized_date = _require_plain_date(as_of_date, "账户导入日期")
        rows = self._normalise_position_snapshot(positions, normalized_date)
        metadata = self._normalise_instrument_metadata(instrument_metadata, normalized_date)
        recorded_at = _clock_now(self._clock)

        with self._database.transaction() as connection:
            try:
                existing = connection.execute(
                    """
                    SELECT event_type, effective_date, status, reason
                    FROM portfolio_events WHERE event_id = ?
                    """,
                    [normalized_event_id],
                ).fetchone()
                expected = ("baseline", normalized_date, "confirmed", normalized_reason)
                if existing is not None:
                    if tuple(existing) != expected:
                        raise ValueError("相同账户导入确认键对应不同内容")
                    return normalized_event_id

                state = connection.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM portfolio_valuations),
                        (SELECT count(*) FROM portfolio_events
                         WHERE event_type = 'baseline' AND status = 'confirmed'),
                        (SELECT count(*) FROM transactions)
                    """
                ).fetchone()
                if state is None or any(int(value) for value in state):
                    raise ValueError("账户已经开始跟踪，请使用完整账户对账校准")

                self._insert_missing_instruments(connection, metadata)
                for row in rows:
                    self._require_known_instrument(
                        connection, str(row["symbol"]), row["asset_type"]
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
                            normalized_date,
                            recorded_at,
                        ],
                    )
                connection.execute(
                    """
                    INSERT INTO portfolio_events (
                        event_id, event_type, effective_date, amount,
                        status, reason, details_json, recorded_at
                    ) VALUES (?, 'baseline', ?, NULL, 'confirmed', ?, ?, ?)
                    """,
                    [
                        normalized_event_id,
                        normalized_date,
                        normalized_reason,
                        _to_json(
                            {
                                "source": "account_snapshot_import",
                                "symbols": [row["symbol"] for row in rows],
                                "external_instruments": [item.symbol for item in metadata],
                            }
                        ),
                        recorded_at,
                    ],
                )
                self._insert_audit(
                    connection,
                    "portfolio_account_import",
                    normalized_event_id,
                    recorded_at,
                    before_rows,
                    {
                        "event_id": normalized_event_id,
                        "as_of_date": normalized_date,
                        "positions": rows,
                        "instrument_metadata": [
                            {
                                "symbol": item.symbol,
                                "asset_type": item.asset_type,
                                "name": item.name,
                                "exchange": item.exchange,
                                "as_of_date": item.as_of_date,
                            }
                            for item in metadata
                        ],
                        "reason": normalized_reason,
                    },
                )
            except Exception:
                raise
        return normalized_event_id

    @staticmethod
    def _normalise_instrument_metadata(
        metadata: tuple[InstrumentInput, ...], as_of_date: date | None
    ) -> tuple[InstrumentInput, ...]:
        if not isinstance(metadata, tuple):
            raise TypeError("外部标的元数据必须使用tuple")
        result: list[InstrumentInput] = []
        symbols: set[str] = set()
        for item in metadata:
            if not isinstance(item, InstrumentInput):
                raise TypeError("外部标的元数据必须使用InstrumentInput")
            from quant_assistant.common.symbols import canonical_symbol

            symbol = canonical_symbol(item.symbol, item.asset_type, item.exchange)
            if symbol in symbols:
                raise ValueError(f"外部标的元数据包含重复代码：{symbol}")
            symbols.add(symbol)
            if item.asset_type not in {AssetType.STOCK, AssetType.FUND, AssetType.CASH}:
                raise ValueError(f"外部标的不支持资产类型：{item.asset_type}")
            name = item.name.strip()
            if not name:
                raise ValueError(f"外部标的名称不能为空：{symbol}")
            item_date = _require_plain_date(item.as_of_date, f"{symbol}元数据日期")
            if as_of_date is not None and item_date != as_of_date:
                raise ValueError("外部标的元数据日期必须与账户日期一致")
            exchange = None if item.exchange is None else item.exchange.strip().upper() or None
            result.append(InstrumentInput(symbol, item.asset_type, name, exchange, item_date))
        return tuple(sorted(result, key=lambda item: item.symbol))

    @staticmethod
    def _insert_missing_instruments(
        connection: duckdb.DuckDBPyConnection,
        metadata: tuple[InstrumentInput, ...],
    ) -> None:
        for item in metadata:
            exchange = item.exchange or (
                "OTC"
                if item.asset_type is AssetType.FUND
                else item.symbol.rsplit(".", 1)[-1]
                if item.asset_type is AssetType.STOCK
                and item.symbol.endswith((".SH", ".SZ", ".BJ"))
                else "LOCAL"
            )
            connection.execute(
                """
                INSERT INTO instruments (
                    symbol, asset_type, name, exchange, industry, board,
                    listed_on, delisted_on, is_st, is_suspended, status, as_of_date
                ) VALUES (?, ?, ?, ?, NULL, ?, NULL, NULL, FALSE, FALSE, 'active', ?)
                ON CONFLICT (symbol) DO NOTHING
                """,
                [
                    item.symbol,
                    item.asset_type.value,
                    item.name,
                    exchange,
                    item.asset_type.value,
                    item.as_of_date,
                ],
            )
            connection.execute(
                """
                INSERT INTO instrument_history (
                    symbol, asset_type, name, exchange, industry, board,
                    listed_on, delisted_on, is_st, is_suspended, status, as_of_date
                ) VALUES (?, ?, ?, ?, NULL, ?, NULL, NULL, FALSE, FALSE, 'active', ?)
                ON CONFLICT (symbol, as_of_date) DO NOTHING
                """,
                [
                    item.symbol,
                    item.asset_type.value,
                    item.name,
                    exchange,
                    item.asset_type.value,
                    item.as_of_date,
                ],
            )
