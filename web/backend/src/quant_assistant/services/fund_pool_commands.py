"""Transactional commands for a local public-fund A/C share pool."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Protocol

import duckdb

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.funds.fee_terms import encode_fee_terms
from quant_assistant.funds.fees import (
    FundFeeSchedule,
    PurchaseTier,
    RedemptionTier,
    ServiceFeeRefundRule,
)
from quant_assistant.models.repositories.database import Database
from quant_assistant.services.commands_common import require_decimal, require_text

SHANGHAI = SHANGHAI_TZ


class Clock(Protocol):
    def now(self) -> datetime: ...


ClockLike = Clock | Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class FundShareInput:
    """One share class and the exact fee values verified by the user."""

    symbol: str
    share_class: str
    is_purchasable: bool
    is_redeemable: bool
    purchase_limit: Decimal | None
    purchase_fee_rate: Decimal
    service_fee_rate_annual: Decimal
    redemption_tiers: tuple[RedemptionTier, ...]
    effective_from: date | None = None
    effective_to: date | None = None
    purchase_tiers: tuple[PurchaseTier, ...] = ()
    channel: str | None = None
    evidence_reference: str | None = None
    service_refund_rule: ServiceFeeRefundRule | None = None


@dataclass(frozen=True, slots=True)
class FundFamilyInput:
    """An A/C mapping plus its benchmark, availability, and fee provenance."""

    family_id: str
    a_share: FundShareInput
    c_share: FundShareInput
    benchmark_symbol: str
    fund_type: str
    fee_source: str
    verified_on: date
    as_of_date: date

    @property
    def a_symbol(self) -> str:
        return self.a_share.symbol

    @property
    def c_symbol(self) -> str:
        return self.c_share.symbol


def _require_plain_date(value: object, label: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{label}必须是date")
    return value


def _clock_now(clock: ClockLike) -> datetime:
    now_method = getattr(clock, "now", None)
    value = now_method() if callable(now_method) else clock()
    if not isinstance(value, datetime):
        raise TypeError("当前时间必须是datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(hours=8):
        raise ValueError("当前时间必须为上海时区时间")
    return value.astimezone(SHANGHAI)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported audit value: {type(value).__name__}")


def _to_json(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _tiers_json(tiers: tuple[RedemptionTier, ...]) -> str:
    return (
        _to_json(
            [
                {"min_days": tier.min_days, "max_days": tier.max_days, "rate": tier.rate}
                for tier in tiers
            ]
        )
        or "[]"
    )


class FundPoolCommandService:
    """Maintain an A/C fund family, schedules, and provenance atomically."""

    def __init__(self, database: Database, clock: ClockLike) -> None:
        if not isinstance(database, Database):
            raise TypeError("database must be a Database")
        self._database = database
        self._clock = clock

    def upsert_family(self, item: FundFamilyInput) -> str:
        """Insert or update both share classes and their verified fee schedules."""

        family_id = require_text(item.family_id, "基金族ID")
        benchmark_symbol = require_text(item.benchmark_symbol, "基准").upper()
        fund_type = require_text(item.fund_type, "基金类型")
        fee_source = require_text(item.fee_source, "费率来源")
        verified_on = _require_plain_date(item.verified_on, "费率核验日期")
        as_of_date = _require_plain_date(item.as_of_date, "基金池日期")
        if verified_on > as_of_date:
            raise ValueError("费率核验日期不能晚于基金池日期")

        a_schedule = self._validate_share(item.a_share, "A", verified_on, as_of_date)
        c_schedule = self._validate_share(item.c_share, "C", verified_on, as_of_date)
        if a_schedule.symbol == c_schedule.symbol:
            raise ValueError("A/C份额必须使用不同标的代码")
        changed_at = _clock_now(self._clock)

        with self._database.transaction() as connection:
            try:
                self._validate_known_types(
                    connection, a_schedule.symbol, c_schedule.symbol, benchmark_symbol
                )
                self._require_unclaimed_symbols(
                    connection, family_id, a_schedule.symbol, c_schedule.symbol
                )
                before = self._family_state(connection, family_id)

                connection.execute(
                    """
                    DELETE FROM fund_share_families
                    WHERE family_id = ? AND symbol NOT IN (?, ?)
                    """,
                    [family_id, a_schedule.symbol, c_schedule.symbol],
                )
                self._upsert_share(
                    connection,
                    family_id,
                    item.a_share,
                    a_schedule,
                    benchmark_symbol,
                    fund_type,
                    as_of_date,
                )
                self._upsert_share(
                    connection,
                    family_id,
                    item.c_share,
                    c_schedule,
                    benchmark_symbol,
                    fund_type,
                    as_of_date,
                )
                self._upsert_schedule(connection, a_schedule, fee_source)
                self._upsert_schedule(connection, c_schedule, fee_source)

                after = self._audit_input(
                    family_id,
                    item,
                    a_schedule,
                    c_schedule,
                    benchmark_symbol,
                    fund_type,
                    fee_source,
                    verified_on,
                    as_of_date,
                )
                connection.execute(
                    """
                    INSERT INTO audit_log (
                        entity_type, entity_id, changed_at, before_json, after_json
                    ) VALUES ('fund_family', ?, ?, ?, ?)
                    """,
                    [family_id, changed_at, _to_json(before), _to_json(after)],
                )
            except Exception:
                raise
        return family_id

    @staticmethod
    def _validate_share(
        share: FundShareInput,
        expected_class: str,
        verified_on: date,
        as_of_date: date,
    ) -> FundFeeSchedule:
        if not isinstance(share, FundShareInput):
            raise TypeError(f"{expected_class}类份额必须是FundShareInput")
        symbol = require_text(share.symbol, f"{expected_class}类标的").upper()
        share_class = require_text(share.share_class, "份额类别").upper()
        if share_class != expected_class:
            raise ValueError(f"必须提供{expected_class}类份额映射")
        if type(share.is_purchasable) is not bool or type(share.is_redeemable) is not bool:
            raise TypeError("基金开放状态必须是bool")
        if share.purchase_limit is not None:
            limit = require_decimal(share.purchase_limit, "申购上限")
            if limit < 0:
                raise ValueError("申购上限不能为负")
        purchase_rate = require_decimal(share.purchase_fee_rate, "申购费率")
        service_rate = require_decimal(share.service_fee_rate_annual, "年服务费率")
        effective_from = (
            as_of_date
            if share.effective_from is None
            else _require_plain_date(share.effective_from, "费率生效日")
        )
        effective_to = (
            None
            if share.effective_to is None
            else _require_plain_date(share.effective_to, "费率失效日")
        )
        return FundFeeSchedule(
            symbol=symbol,
            share_class=share_class,
            purchase_rate=purchase_rate,
            annual_service_rate=service_rate,
            redemption_tiers=tuple(share.redemption_tiers),
            verified_on=verified_on,
            effective_from=effective_from,
            effective_to=effective_to,
            purchase_tiers=share.purchase_tiers,
            channel=share.channel,
            evidence_reference=share.evidence_reference,
            service_refund_rule=share.service_refund_rule,
        )

    @staticmethod
    def _validate_known_types(
        connection: duckdb.DuckDBPyConnection,
        a_symbol: str,
        c_symbol: str,
        benchmark_symbol: str,
    ) -> None:
        rows = connection.execute(
            "SELECT symbol, asset_type FROM instruments WHERE symbol IN (?, ?, ?)",
            [a_symbol, c_symbol, benchmark_symbol],
        ).fetchall()
        types = dict(rows)
        for symbol in (a_symbol, c_symbol):
            if symbol in types and types[symbol] != "fund":
                raise ValueError(f"基金份额标的{symbol}的资产类型不是fund")
        if benchmark_symbol in types and types[benchmark_symbol] != "index":
            raise ValueError(f"基准{benchmark_symbol}的资产类型不是index")

    @staticmethod
    def _require_unclaimed_symbols(
        connection: duckdb.DuckDBPyConnection,
        family_id: str,
        a_symbol: str,
        c_symbol: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT symbol, family_id FROM fund_share_families
            WHERE symbol IN (?, ?) AND family_id <> ? LIMIT 1
            """,
            [a_symbol, c_symbol, family_id],
        ).fetchone()
        if row is not None:
            raise ValueError(f"标的{row[0]}已属于基金族{row[1]}")

    @staticmethod
    def _upsert_share(
        connection: duckdb.DuckDBPyConnection,
        family_id: str,
        share: FundShareInput,
        schedule: FundFeeSchedule,
        benchmark_symbol: str,
        fund_type: str,
        as_of_date: date,
    ) -> None:
        connection.execute(
            """
            INSERT INTO fund_share_families (
                family_id, symbol, share_class, benchmark_symbol, fund_type,
                is_purchasable, is_redeemable, purchase_limit, as_of_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (symbol) DO UPDATE SET
                family_id = excluded.family_id,
                share_class = excluded.share_class,
                benchmark_symbol = excluded.benchmark_symbol,
                fund_type = excluded.fund_type,
                is_purchasable = excluded.is_purchasable,
                is_redeemable = excluded.is_redeemable,
                purchase_limit = excluded.purchase_limit,
                as_of_date = excluded.as_of_date
            """,
            [
                family_id,
                schedule.symbol,
                schedule.share_class,
                benchmark_symbol,
                fund_type,
                share.is_purchasable,
                share.is_redeemable,
                share.purchase_limit,
                as_of_date,
            ],
        )

    @staticmethod
    def _upsert_schedule(
        connection: duckdb.DuckDBPyConnection, schedule: FundFeeSchedule, fee_source: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO fund_fee_schedules (
                symbol, effective_from, effective_to, purchase_fee_rate,
                service_fee_rate_annual, redemption_tiers_json, source, verified_on, terms_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (symbol, effective_from) DO UPDATE SET
                effective_to = excluded.effective_to,
                purchase_fee_rate = excluded.purchase_fee_rate,
                service_fee_rate_annual = excluded.service_fee_rate_annual,
                redemption_tiers_json = excluded.redemption_tiers_json,
                source = excluded.source,
                verified_on = excluded.verified_on,
                terms_json = excluded.terms_json
            """,
            [
                schedule.symbol,
                schedule.effective_from,
                schedule.effective_to,
                schedule.purchase_rate,
                schedule.annual_service_rate,
                _tiers_json(schedule.redemption_tiers),
                fee_source,
                schedule.verified_on,
                encode_fee_terms(schedule),
            ],
        )

    @staticmethod
    def _family_state(
        connection: duckdb.DuckDBPyConnection, family_id: str
    ) -> dict[str, object] | None:
        cursor = connection.execute(
            """
            SELECT family_id, symbol, share_class, benchmark_symbol, fund_type,
                   is_purchasable, is_redeemable, purchase_limit, as_of_date
            FROM fund_share_families WHERE family_id = ? ORDER BY share_class
            """,
            [family_id],
        )
        columns = [column[0] for column in cursor.description]
        share_rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        if not share_rows:
            return None
        symbols = [row["symbol"] for row in share_rows]
        placeholders = ", ".join("?" for _ in symbols)
        fee_cursor = connection.execute(
            f"""
            SELECT symbol, effective_from, effective_to, purchase_fee_rate,
                   service_fee_rate_annual, redemption_tiers_json, source, verified_on
            FROM fund_fee_schedules WHERE symbol IN ({placeholders})
            ORDER BY symbol, effective_from
            """,
            symbols,
        )
        fee_columns = [column[0] for column in fee_cursor.description]
        fee_rows = [dict(zip(fee_columns, row, strict=True)) for row in fee_cursor.fetchall()]
        return {"shares": share_rows, "fee_schedules": fee_rows}

    @staticmethod
    def _audit_input(
        family_id: str,
        item: FundFamilyInput,
        a_schedule: FundFeeSchedule,
        c_schedule: FundFeeSchedule,
        benchmark_symbol: str,
        fund_type: str,
        fee_source: str,
        verified_on: date,
        as_of_date: date,
    ) -> dict[str, object]:
        def share_state(share: FundShareInput, schedule: FundFeeSchedule) -> dict[str, object]:
            return {
                "symbol": schedule.symbol,
                "share_class": schedule.share_class,
                "is_purchasable": share.is_purchasable,
                "is_redeemable": share.is_redeemable,
                "purchase_limit": share.purchase_limit,
                "purchase_fee_rate": schedule.purchase_rate,
                "service_fee_rate_annual": schedule.annual_service_rate,
                "redemption_tiers": [
                    {
                        "min_days": tier.min_days,
                        "max_days": tier.max_days,
                        "rate": tier.rate,
                    }
                    for tier in schedule.redemption_tiers
                ],
                "effective_from": schedule.effective_from,
                "effective_to": schedule.effective_to,
            }

        return {
            "family_id": family_id,
            "a_share": share_state(item.a_share, a_schedule),
            "c_share": share_state(item.c_share, c_schedule),
            "benchmark_symbol": benchmark_symbol,
            "fund_type": fund_type,
            "fee_source": fee_source,
            "verified_on": verified_on,
            "as_of_date": as_of_date,
        }
