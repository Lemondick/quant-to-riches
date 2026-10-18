"""Local multi-account labels and snapshot aggregation.

This module deliberately stores only user-provided snapshots.  It does not
authenticate to, scrape, synchronize with, or submit orders to any platform.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import AssetType
from quant_assistant.models.repositories.database import Database
from quant_assistant.services.account_snapshot import validate_complete_snapshot
from quant_assistant.services.commands_common import require_text
from quant_assistant.services.portfolio_commands import PositionInput

_ACCOUNT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


@dataclass(frozen=True, slots=True)
class AccountInput:
    """A user-labelled account, such as a brokerage or fund-platform account."""

    account_id: str
    name: str
    platform: str
    currency: str = "CNY"


@dataclass(frozen=True, slots=True)
class AccountSnapshotResult:
    account_id: str
    event_id: str
    as_of_date: date
    position_count: int
    total_value: Decimal


@dataclass(frozen=True, slots=True)
class AccountSummary:
    account_id: str
    name: str
    platform: str
    as_of_date: date | None
    position_count: int
    total_value: Decimal
    cash_value: Decimal


ClockLike = Callable[[], datetime] | object


class AccountRegistryService:
    """Register labelled accounts and atomically replace their snapshots."""

    def __init__(self, database: Database, clock: ClockLike | None = None) -> None:
        if not isinstance(database, Database):
            raise TypeError("database must be Database")
        self._database = database
        self._clock = clock or (lambda: datetime.now(SHANGHAI_TZ))

    def register(self, item: AccountInput) -> str:
        if not isinstance(item, AccountInput):
            raise TypeError("account must be AccountInput")
        account_id = _account_id(item.account_id)
        name = require_text(item.name, "账户名称", limit=120, coerce=True)
        platform = require_text(item.platform, "账户平台", limit=80, coerce=True)
        currency = require_text(item.currency, "账户币种", limit=12, coerce=True).upper()
        now = _clock_now(self._clock)
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_accounts (
                    account_id, name, platform, currency, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT (account_id) DO UPDATE SET
                    name = excluded.name,
                    platform = excluded.platform,
                    currency = excluded.currency,
                    status = 'active',
                    updated_at = excluded.updated_at
                """,
                [account_id, name, platform, currency, now, now],
            )
        return account_id

    def archive(self, account_id: str, *, reason: str) -> str:
        normalized = _account_id(account_id)
        normalized_reason = require_text(reason, "归档原因", limit=500, coerce=True)
        now = _clock_now(self._clock)
        with self._database.connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM portfolio_accounts WHERE account_id = ?", [normalized]
            ).fetchone()
            if exists is None:
                raise ValueError(f"账户不存在：{normalized}")
            connection.execute(
                """
                UPDATE portfolio_accounts
                SET status = 'archived', updated_at = ?
                WHERE account_id = ?
                """,
                [now, normalized],
            )
            connection.execute(
                """
                INSERT INTO account_events (
                    account_id, event_id, event_type, effective_date,
                    reason, details_json, recorded_at
                ) VALUES (?, ?, 'archive', ?, ?, '{}', ?)
                """,
                [
                    normalized,
                    f"archive:{normalized}:{now.isoformat()}",
                    now.date(),
                    normalized_reason,
                    now,
                ],
            )
        return normalized

    def import_snapshot(
        self,
        account_id: str,
        as_of_date: date,
        positions: tuple[PositionInput, ...],
        *,
        event_id: str,
        reason: str,
    ) -> AccountSnapshotResult:
        normalized_account = _account_id(account_id)
        normalized_event = require_text(event_id, "账户快照编号", limit=160, coerce=True)
        normalized_reason = require_text(reason, "账户快照原因", limit=500, coerce=True)
        if isinstance(as_of_date, datetime) or not isinstance(as_of_date, date):
            raise TypeError("账户快照日期必须是date")
        if not isinstance(positions, tuple):
            raise TypeError("账户快照持仓必须使用tuple")
        validate_complete_snapshot(positions)
        rows = _snapshot_rows(positions, as_of_date)
        fingerprint = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        now = _clock_now(self._clock)
        with self._database.transaction() as connection:
            try:
                account = connection.execute(
                    "SELECT status FROM portfolio_accounts WHERE account_id = ?",
                    [normalized_account],
                ).fetchone()
                if account is None:
                    raise ValueError(f"账户不存在，请先登记账户：{normalized_account}")
                if account[0] != "active":
                    raise ValueError(f"账户已归档：{normalized_account}")
                existing = connection.execute(
                    """
                    SELECT details_json, effective_date, reason
                    FROM account_events
                    WHERE account_id = ? AND event_id = ?
                    """,
                    [normalized_account, normalized_event],
                ).fetchone()
                if existing is not None:
                    details = _json_object(existing[0])
                    if (
                        existing[1] != as_of_date
                        or existing[2] != normalized_reason
                        or details.get("positions_fingerprint") != fingerprint
                    ):
                        raise ValueError("相同账户快照编号对应不同内容")
                    total = sum((item.market_value for item in positions), Decimal("0"))
                    return AccountSnapshotResult(
                        normalized_account, normalized_event, as_of_date, len(positions), total
                    )
                connection.execute(
                    "DELETE FROM account_positions WHERE account_id = ?", [normalized_account]
                )
                for row in rows:
                    connection.execute(
                        """
                        INSERT INTO account_positions (
                            account_id, symbol, asset_type, quantity, average_cost,
                            market_value, as_of_date, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            normalized_account,
                            row["symbol"],
                            row["asset_type"],
                            row["quantity"],
                            row["average_cost"],
                            row["market_value"],
                            as_of_date,
                            now,
                        ],
                    )
                connection.execute(
                    """
                    INSERT INTO account_events (
                        account_id, event_id, event_type, effective_date,
                        reason, details_json, recorded_at
                    ) VALUES (?, ?, 'snapshot', ?, ?, ?, ?)
                    """,
                    [
                        normalized_account,
                        normalized_event,
                        as_of_date,
                        normalized_reason,
                        json.dumps(
                            {
                                "positions_fingerprint": fingerprint,
                                "position_count": len(rows),
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ],
                )
            except Exception:
                raise
        total = sum((item.market_value for item in positions), Decimal("0"))
        return AccountSnapshotResult(
            normalized_account, normalized_event, as_of_date, len(positions), total
        )

    def accounts(self, *, include_archived: bool = False) -> pd.DataFrame:
        query = (
            "SELECT account_id, name, platform, currency, status, created_at, updated_at "
            "FROM portfolio_accounts"
        )
        if not include_archived:
            query += " WHERE status = 'active'"
        return self._database.query_df(query + " ORDER BY name, account_id")

    def summaries(self, *, include_archived: bool = False) -> tuple[AccountSummary, ...]:
        accounts = self.accounts(include_archived=include_archived)
        positions = self._database.query_df(
            """
            SELECT account_id, symbol, asset_type, quantity, market_value, as_of_date
            FROM account_positions ORDER BY account_id, symbol
            """
        )
        result: list[AccountSummary] = []
        for row in accounts.itertuples(index=False):
            subset = positions.loc[positions["account_id"].eq(row.account_id)]
            total = _sum_decimal(subset.get("market_value"))
            cash = _sum_decimal(
                subset.loc[subset["asset_type"].astype(str).eq("cash"), "market_value"]
                if not subset.empty
                else None
            )
            dates = pd.to_datetime(subset.get("as_of_date"), errors="coerce").dropna()
            as_of = dates.max().date() if not dates.empty else None
            result.append(
                AccountSummary(
                    account_id=str(row.account_id),
                    name=str(row.name),
                    platform=str(row.platform),
                    as_of_date=as_of,
                    position_count=len(subset),
                    total_value=total,
                    cash_value=cash,
                )
            )
        return tuple(result)

    def aggregate_positions(self, *, include_local_ledger: bool = True) -> pd.DataFrame:
        """Aggregate labelled account snapshots by instrument for dashboard use."""

        frame = self._database.query_df(
            """
            SELECT p.account_id, a.name AS account_name, a.platform,
                   p.symbol, p.asset_type, p.quantity, p.average_cost,
                   p.market_value, p.as_of_date
            FROM account_positions AS p
            JOIN portfolio_accounts AS a ON a.account_id = p.account_id
            WHERE a.status = 'active'
            ORDER BY p.symbol, p.account_id
            """
        )
        if include_local_ledger:
            local = self._database.query_df(
                """
                SELECT 'local' AS account_id, '本地主账本' AS account_name,
                       'local-ledger' AS platform, symbol, asset_type,
                       quantity, average_cost, market_value, as_of_date
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
        frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
        frame["average_cost"] = pd.to_numeric(frame["average_cost"], errors="coerce")
        frame["market_value"] = pd.to_numeric(frame["market_value"], errors="coerce")
        rows: list[dict[str, object]] = []
        for (symbol, asset_type), group in frame.groupby(
            ["symbol", "asset_type"], sort=True, dropna=False
        ):
            quantity = group["quantity"].sum()
            weighted_cost = (
                (group["quantity"] * group["average_cost"]).sum() / quantity if quantity > 0 else 0
            )
            dates = pd.to_datetime(group["as_of_date"], errors="coerce").dropna()
            rows.append(
                {
                    "symbol": str(symbol),
                    "asset_type": str(asset_type),
                    "account_count": int(group["account_id"].nunique()),
                    "account_names": "、".join(
                        dict.fromkeys(group["account_name"].astype(str).tolist())
                    ),
                    "quantity": quantity,
                    "average_cost": weighted_cost,
                    "market_value": group["market_value"].sum(),
                    "latest_date": dates.max().date() if not dates.empty else None,
                }
            )
        return pd.DataFrame(rows)


def _account_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not _ACCOUNT_ID_RE.fullmatch(normalized):
        raise ValueError("账户ID只能包含字母、数字、点、下划线或短横线")
    return normalized


def _clock_now(clock: ClockLike) -> datetime:
    candidate = getattr(clock, "now", clock)
    value = candidate() if callable(candidate) else None
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None:
        raise ValueError("clock datetime must be timezone-aware")
    return value.astimezone(SHANGHAI_TZ)


def _snapshot_rows(
    positions: Iterable[PositionInput], as_of_date: date
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    symbols: set[str] = set()
    for item in positions:
        if not isinstance(item, PositionInput):
            raise TypeError("账户快照持仓必须使用PositionInput")
        symbol = item.symbol.strip().upper()
        if not symbol or symbol in symbols:
            raise ValueError(f"账户快照包含重复或空标的：{symbol or '空'}")
        symbols.add(symbol)
        if item.as_of_date != as_of_date:
            raise ValueError("账户快照持仓日期必须一致")
        if not isinstance(item.asset_type, AssetType):
            raise TypeError("账户快照资产类型无效")
        for value, label in (
            (item.quantity, "数量"),
            (item.average_cost, "平均成本"),
            (item.market_value, "市值"),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{symbol}{label}必须是非负有限Decimal")
        rows.append(
            {
                "symbol": symbol,
                "asset_type": item.asset_type.value,
                "quantity": item.quantity,
                "average_cost": item.average_cost,
                "market_value": item.market_value,
            }
        )
    return tuple(rows)


def _sum_decimal(values: object) -> Decimal:
    if values is None or not hasattr(values, "tolist"):
        return Decimal("0")
    total = Decimal("0")
    for value in values.tolist():
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if parsed.is_finite():
            total += parsed
    return total


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
    else:
        decoded = value
    return dict(decoded) if isinstance(decoded, dict) else {}


__all__ = [
    "AccountInput",
    "AccountRegistryService",
    "AccountSnapshotResult",
    "AccountSummary",
]
