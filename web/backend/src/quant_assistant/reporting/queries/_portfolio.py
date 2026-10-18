"""Portfolio positions, valuations, transactions and ledger read projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from quant_assistant.funds.fee_terms import decode_fee_terms, terms_object
from quant_assistant.funds.fees import FundFeeSchedule, RedemptionTier

from ._base import _DecisionQueriesBase
from ._helpers import (
    _is_missing,
    _json_value,
    _required_account_id,
    _required_date,
    _validate_limit,
    _validate_offset,
)

__all__ = ["_PortfolioMixin"]


class _PortfolioMixin(_DecisionQueriesBase):
    """Positions, valuations, transactions, dividends, ledger and fee schedules."""

    def current_positions(self) -> pd.DataFrame:
        return self._query(
            """
            SELECT p.symbol, COALESCE(i.name, p.symbol) AS name,
                   p.asset_type, p.quantity, p.average_cost, p.market_value,
                   p.as_of_date, p.updated_at
            FROM positions AS p
            LEFT JOIN instruments AS i ON i.symbol = p.symbol
            ORDER BY p.asset_type, p.symbol
            """
        )

    def account_positions(self, account_id: str | None = None) -> pd.DataFrame:
        """Read labelled external snapshots without mixing them into NAV ledger."""

        if account_id is None:
            return self._query(
                """
                SELECT p.account_id, a.name AS account_name, a.platform,
                       p.symbol, p.asset_type, p.quantity, p.average_cost,
                       p.market_value, p.as_of_date, p.updated_at
                FROM account_positions AS p
                JOIN portfolio_accounts AS a ON a.account_id = p.account_id
                WHERE a.status = 'active'
                ORDER BY a.name, p.asset_type, p.symbol
                """
            )
        normalized = _required_account_id(account_id)
        return self._query(
            """
            SELECT p.account_id, a.name AS account_name, a.platform,
                   p.symbol, p.asset_type, p.quantity, p.average_cost,
                   p.market_value, p.as_of_date, p.updated_at
            FROM account_positions AS p
            JOIN portfolio_accounts AS a ON a.account_id = p.account_id
            WHERE a.status = 'active' AND p.account_id = ?
            ORDER BY p.asset_type, p.symbol
            """,
            [normalized],
        )

    def aggregate_account_positions(self, *, include_local_ledger: bool = True) -> pd.DataFrame:
        """Aggregate all labelled snapshots by symbol for a read-only dashboard view."""

        frame = self.account_positions()
        if include_local_ledger:
            local = self._query(
                """
                SELECT 'local' AS account_id, '本地主账本' AS account_name,
                       'local-ledger' AS platform, symbol, asset_type,
                       quantity, average_cost, market_value, as_of_date,
                       updated_at
                FROM positions ORDER BY symbol
                """
            )
            if not local.empty:
                frame = pd.concat([frame, local], ignore_index=True)
        if frame.empty:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "asset_type",
                    "account_count",
                    "account_names",
                    "quantity",
                    "average_cost",
                    "market_value",
                    "latest_date",
                ]
            )
        for column in ("quantity", "average_cost", "market_value"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
        rows: list[dict[str, object]] = []
        for (symbol, asset_type), group in frame.groupby(
            ["symbol", "asset_type"], sort=True, dropna=False
        ):
            quantity = group["quantity"].sum()
            rows.append(
                {
                    "symbol": str(symbol),
                    "asset_type": str(asset_type),
                    "account_count": int(group["account_id"].nunique()),
                    "account_names": "、".join(
                        dict.fromkeys(group["account_name"].astype(str).tolist())
                    ),
                    "quantity": quantity,
                    "average_cost": (
                        (group["quantity"] * group["average_cost"]).sum() / quantity
                        if quantity > 0
                        else 0
                    ),
                    "market_value": group["market_value"].sum(),
                    "latest_date": pd.to_datetime(group["as_of_date"], errors="coerce")
                    .max()
                    .date(),
                }
            )
        return pd.DataFrame(rows)

    def portfolio_valuations(
        self, *, start_date: date | None = None, limit: int | None = None
    ) -> pd.DataFrame:
        """Read the immutable valuation ledger for calendar/report views."""

        sql = """
            SELECT run_id, as_of_date, generated_at, data_snapshot_id,
                   total_value, cash, unit_nav, high_water_mark,
                   net_external_flow, previous_run_id, is_baseline
            FROM portfolio_valuations
        """
        parameters: list[object] = []
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            sql += " WHERE as_of_date >= ?"
            parameters.append(start_date)
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 730:
                raise ValueError("portfolio valuation limit must be between 1 and 730")
            sql += " ORDER BY as_of_date DESC, generated_at DESC, run_id DESC LIMIT ?"
            parameters.append(limit)
        else:
            sql += " ORDER BY as_of_date, generated_at, run_id"
        return self._query(sql, parameters)

    def valuation_history(
        self, *, start_date: date | None = None, limit: int | None = None
    ) -> pd.DataFrame:
        """Compatibility alias used by report integrations."""

        return self.portfolio_valuations(start_date=start_date, limit=limit)

    def transaction_history(
        self,
        *,
        start_date: date | None = None,
        limit: int | None = None,
        offset: int = 0,
        descending: bool | None = None,
    ) -> pd.DataFrame:
        """Read transaction evidence without exposing account credentials."""

        sql = """
            SELECT t.id, t.symbol, coalesce(i.name, t.symbol) AS name,
                   t.asset_type, t.action, t.quantity, t.price, t.fees,
                   t.traded_at, t.recorded_at, t.source, t.external_id,
                   t.gross_amount, t.cash_delta, t.cash_symbol, t.note,
                   t.reversed_by_correction_id
            FROM transactions t
            LEFT JOIN instruments i ON i.symbol = t.symbol
        """
        parameters: list[object] = []
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            sql += " WHERE CAST(traded_at AS DATE) >= ?"
            parameters.append(start_date)
        _validate_offset(offset, "transaction history offset")
        order_desc = limit is not None if descending is None else descending
        sql += " ORDER BY traded_at DESC, id DESC" if order_desc else " ORDER BY traded_at, id"
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
                raise ValueError("transaction history limit must be between 1 and 500")
            sql += " LIMIT ?"
            parameters.append(limit)
            if offset:
                sql += " OFFSET ?"
                parameters.append(offset)
        return self._query(sql, parameters)

    def transactions(
        self,
        *,
        start_date: date | None = None,
        limit: int | None = None,
        offset: int = 0,
        descending: bool | None = None,
    ) -> pd.DataFrame:
        """Compatibility alias for transaction-history consumers."""

        return self.transaction_history(
            start_date=start_date,
            limit=limit,
            offset=offset,
            descending=descending,
        )

    def dividend_history(
        self,
        *,
        start_date: date | None = None,
        limit: int | None = None,
        offset: int = 0,
        descending: bool | None = None,
    ) -> pd.DataFrame:
        """Read confirmed cash dividends and their explicit costs."""

        sql = """
            SELECT d.id, d.symbol, coalesce(i.name, d.symbol) AS name,
                   d.asset_type, d.gross_amount, d.fees, d.net_amount,
                   d.paid_at, d.recorded_at, d.source, d.external_id,
                   d.cash_symbol, d.note, d.reversed_by_correction_id
            FROM dividend_events d
            LEFT JOIN instruments i ON i.symbol = d.symbol
        """
        parameters: list[object] = []
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            sql += " WHERE CAST(paid_at AS DATE) >= ?"
            parameters.append(start_date)
        _validate_offset(offset, "dividend history offset")
        order_desc = limit is not None if descending is None else descending
        sql += " ORDER BY paid_at DESC, id DESC" if order_desc else " ORDER BY paid_at, id"
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
                raise ValueError("dividend history limit must be between 1 and 500")
            sql += " LIMIT ?"
            parameters.append(limit)
            if offset:
                sql += " OFFSET ?"
                parameters.append(offset)
        return self._query(sql, parameters)

    def ledger_history(
        self,
        *,
        start_date: date | None = None,
        limit: int | None = None,
        offset: int = 0,
        descending: bool | None = None,
    ) -> pd.DataFrame:
        """Read transactions and dividends as one SQL-paginated stream.

        A single UNION keeps pagination correct when a large ledger contains
        both trades and dividends at the same timestamp.  Callers can replay
        the full history in bounded pages instead of materializing thousands of
        rows before attribution.
        """

        _validate_offset(offset, "ledger history offset")
        order_desc = limit is not None if descending is None else descending
        sql = """
            SELECT id, entry_type, symbol, name, asset_type, quantity, price,
                   gross_amount, fees, net_amount, occurred_at, external_id,
                   note, reversed_by_correction_id
            FROM (
                SELECT t.id,
                       CASE WHEN lower(coalesce(t.source, '')) = 'dca'
                            THEN 'dca'
                            WHEN t.action IN ('buy', 'add') THEN 'buy'
                            ELSE 'sell' END AS entry_type,
                       t.symbol, coalesce(i.name, t.symbol) AS name,
                       t.asset_type, t.quantity, t.price, t.gross_amount,
                       t.fees, t.cash_delta AS net_amount, t.traded_at AS occurred_at,
                       t.external_id, t.note, t.reversed_by_correction_id
                FROM transactions t
                LEFT JOIN instruments i ON i.symbol = t.symbol
                UNION ALL
                SELECT d.id, 'dividend' AS entry_type, d.symbol,
                       coalesce(i.name, d.symbol) AS name, d.asset_type,
                       NULL AS quantity, NULL AS price, d.gross_amount,
                       d.fees, d.net_amount, d.paid_at AS occurred_at,
                       d.external_id, d.note, d.reversed_by_correction_id
                FROM dividend_events d
                LEFT JOIN instruments i ON i.symbol = d.symbol
            ) AS ledger
        """
        parameters: list[object] = []
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            sql += " WHERE CAST(occurred_at AS DATE) >= ?"
            parameters.append(start_date)
        sql += (
            " ORDER BY occurred_at DESC, entry_type, id DESC"
            if order_desc
            else " ORDER BY occurred_at, entry_type, id"
        )
        if limit is not None:
            _validate_limit(limit, "ledger history limit", maximum=500)
            sql += " LIMIT ?"
            parameters.append(limit)
            if offset:
                sql += " OFFSET ?"
                parameters.append(offset)
        elif offset:
            # DuckDB requires a LIMIT clause before OFFSET when paging an
            # unbounded query; use an explicit sentinel.
            sql += " LIMIT ALL OFFSET ?"
            parameters.append(offset)
        return self._query(sql, parameters)

    def ledger_totals(self) -> tuple[Decimal, Decimal]:
        """Aggregate fees and dividends in DuckDB instead of materializing rows."""

        row = self._query(
            """
            SELECT
                coalesce((SELECT sum(fees) FROM transactions
                          WHERE reversed_by_correction_id IS NULL), 0)
                + coalesce((SELECT sum(fees) FROM dividend_events
                            WHERE reversed_by_correction_id IS NULL), 0) AS total_fees,
                coalesce((SELECT sum(net_amount) FROM dividend_events
                          WHERE reversed_by_correction_id IS NULL), 0)
                    AS total_dividends
            """
        ).iloc[0]
        return Decimal(str(row["total_fees"])), Decimal(str(row["total_dividends"]))

    def corporate_action_history(
        self,
        *,
        start_date: date | None = None,
        limit: int | None = None,
        offset: int = 0,
        descending: bool | None = None,
    ) -> pd.DataFrame:
        """Read immutable company-action events for ledger and audit views."""

        sql = """
            SELECT id, action_type, symbol, target_symbol, asset_type, quantity,
                   target_quantity, price, target_price, gross_amount, fees,
                   cash_delta, ratio, effective_at, recorded_at, source,
                   external_id, cash_symbol, from_account, to_account, note
            FROM corporate_action_events
        """
        parameters: list[object] = []
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            sql += " WHERE CAST(effective_at AS DATE) >= ?"
            parameters.append(start_date)
        _validate_offset(offset, "corporate action history offset")
        order_desc = limit is not None if descending is None else bool(descending)
        sql += (
            " ORDER BY effective_at DESC, id DESC" if order_desc else " ORDER BY effective_at, id"
        )
        if limit is not None:
            _validate_limit(limit, "corporate action history limit", maximum=500)
            sql += " LIMIT ?"
            parameters.append(limit)
        elif offset:
            sql += " LIMIT ALL"
        if offset:
            sql += " OFFSET ?"
            parameters.append(offset)
        return self._query(sql, parameters)

    def ledger_correction_history(
        self,
        *,
        start_date: date | None = None,
        limit: int | None = None,
        offset: int = 0,
        descending: bool | None = None,
    ) -> pd.DataFrame:
        """Read immutable correction records without changing original entries."""

        sql = """
            SELECT id, target_type, target_id, reason, effective_at, recorded_at,
                   source, external_id, note
            FROM ledger_corrections
        """
        parameters: list[object] = []
        if start_date is not None:
            if not isinstance(start_date, date):
                raise TypeError("start_date must be a date or None")
            sql += " WHERE CAST(effective_at AS DATE) >= ?"
            parameters.append(start_date)
        _validate_offset(offset, "ledger correction history offset")
        order_desc = limit is not None if descending is None else bool(descending)
        sql += (
            " ORDER BY effective_at DESC, id DESC" if order_desc else " ORDER BY effective_at, id"
        )
        if limit is not None:
            _validate_limit(limit, "ledger correction history limit", maximum=500)
            sql += " LIMIT ?"
            parameters.append(limit)
        elif offset:
            sql += " LIMIT ALL"
        if offset:
            sql += " OFFSET ?"
            parameters.append(offset)
        return self._query(sql, parameters)

    def fund_fee_schedules(self, *, as_of_date: date | None = None) -> dict[str, FundFeeSchedule]:
        """Materialize the latest verified fee schedule per fund symbol."""

        decision_date = as_of_date or date.today()
        if not isinstance(decision_date, date):
            raise TypeError("as_of_date must be a date or None")
        rows = self._query(
            """
            SELECT symbol, effective_from, effective_to, purchase_fee_rate,
                   service_fee_rate_annual, redemption_tiers_json, verified_on, terms_json
            FROM fund_fee_schedules
            WHERE effective_from <= ?
              AND (effective_to IS NULL OR effective_to >= ?)
              AND verified_on <= ?
            ORDER BY symbol, effective_from DESC, id DESC
            """,
            [decision_date, decision_date, decision_date],
        )
        schedules: dict[str, FundFeeSchedule] = {}
        for row in rows.to_dict("records"):
            symbol = str(row["symbol"]).strip()
            if not symbol or symbol in schedules:
                continue
            raw_tiers = _json_value(row.get("redemption_tiers_json"), [])
            if not isinstance(raw_tiers, Sequence) or isinstance(raw_tiers, (str, bytes)):
                continue
            try:
                tiers = tuple(
                    RedemptionTier(
                        int(item["min_days"]),
                        None if item.get("max_days") is None else int(item["max_days"]),
                        Decimal(str(item["rate"])),
                    )
                    for item in raw_tiers
                    if isinstance(item, Mapping)
                )
                schedule = FundFeeSchedule(
                    symbol=symbol,
                    share_class=terms_object(row.get("terms_json", "{}")).get("share_class")
                    or (str(symbol[-1:]).upper() if symbol[-1:].upper() in {"A", "C"} else "A"),
                    purchase_rate=Decimal(str(row["purchase_fee_rate"])),
                    annual_service_rate=Decimal(str(row["service_fee_rate_annual"])),
                    redemption_tiers=tiers,
                    verified_on=_required_date(row["verified_on"]),
                    effective_from=_required_date(row["effective_from"]),
                    effective_to=(
                        None
                        if _is_missing(row.get("effective_to"))
                        else _required_date(row["effective_to"])
                    ),
                    **decode_fee_terms(row.get("terms_json", "{}")),
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
            schedules[symbol] = schedule
        return schedules

    def position_holding_days(self, *, as_of_date: date | None = None) -> pd.DataFrame:
        """Derive conservative holding days from recorded buy/add transactions."""

        decision_date = as_of_date or date.today()
        if not isinstance(decision_date, date):
            raise TypeError("as_of_date must be a date or None")
        rows = self._query(
            """
            SELECT symbol, min(CAST(traded_at AS DATE)) AS first_trade_date
            FROM transactions
            WHERE action IN ('buy', 'add') AND CAST(traded_at AS DATE) <= ?
            GROUP BY symbol
            ORDER BY symbol
            """,
            [decision_date],
        )
        if rows.empty:
            return pd.DataFrame(columns=["symbol", "held_days"])
        rows["first_trade_date"] = pd.to_datetime(rows["first_trade_date"], errors="coerce").dt.date
        rows["held_days"] = rows["first_trade_date"].map(
            lambda value: (decision_date - value).days if isinstance(value, date) else None
        )
        return rows[["symbol", "held_days"]]
