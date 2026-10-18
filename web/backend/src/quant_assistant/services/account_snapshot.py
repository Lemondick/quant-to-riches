"""Strict, read-only parsing and validation for complete account snapshots."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO

from quant_assistant.models.entities import AssetType
from quant_assistant.services.portfolio_commands import PositionInput

SNAPSHOT_COLUMNS = ("symbol", "asset_type", "quantity", "average_cost", "market_value")
_SUPPORTED_TYPES = frozenset({AssetType.STOCK, AssetType.FUND, AssetType.CASH})


def parse_account_snapshot(text: str, as_of_date: date) -> tuple[PositionInput, ...]:
    """Parse a complete account CSV without writing to the database."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("账户快照不能为空")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date必须是date")
    reader = csv.DictReader(StringIO(text.strip()))
    if tuple(reader.fieldnames or ()) != SNAPSHOT_COLUMNS:
        raise ValueError("账户快照列必须严格为: " + ",".join(SNAPSHOT_COLUMNS))

    rows: list[PositionInput] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(reader, start=2):
        if not any((value or "").strip() for value in raw.values()):
            continue
        if None in raw or any(value is None for value in raw.values()):
            raise ValueError(f"第{line_number}行列数与表头不一致")
        symbol = (raw.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError(f"第{line_number}行标的代码不能为空")
        if symbol in seen:
            raise ValueError(f"第{line_number}行标的代码重复: {symbol}")
        seen.add(symbol)
        try:
            asset_type = AssetType((raw.get("asset_type") or "").strip().lower())
        except ValueError as exc:
            raise ValueError(f"第{line_number}行资产类型无效") from exc
        if asset_type not in _SUPPORTED_TYPES:
            raise ValueError(f"第{line_number}行只允许stock、fund或cash")
        rows.append(
            PositionInput(
                symbol=symbol,
                asset_type=asset_type,
                quantity=_decimal(raw.get("quantity"), line_number, "quantity"),
                average_cost=_decimal(raw.get("average_cost"), line_number, "average_cost"),
                market_value=_decimal(raw.get("market_value"), line_number, "market_value"),
                as_of_date=as_of_date,
            )
        )
    validate_complete_snapshot(rows)
    return tuple(rows)


def validate_complete_snapshot(rows: Iterable[PositionInput]) -> None:
    """Enforce the invariants required before establishing the NAV baseline."""

    positions = tuple(rows)
    if not positions:
        raise ValueError("账户快照必须至少包含一行")
    cash = [item for item in positions if item.asset_type is AssetType.CASH]
    if len(cash) != 1:
        raise ValueError("账户快照必须且只能包含一个cash持仓")
    if cash[0].quantity < 0 or cash[0].market_value < 0:
        raise ValueError("现金持仓不能为负")
    if cash[0].average_cost != Decimal("1"):
        raise ValueError("现金持仓average_cost必须为1")
    if any(item.quantity < 0 for item in positions):
        raise ValueError("持仓数量不能为负")
    if any(item.average_cost < 0 or item.market_value < 0 for item in positions):
        raise ValueError("成本和市值不能为负")
    if sum((item.market_value for item in positions), Decimal("0")) <= 0:
        raise ValueError("组合总市值必须为正")


def snapshot_total(rows: Iterable[PositionInput]) -> Decimal:
    """Return the declared total market value after validation."""

    positions = tuple(rows)
    validate_complete_snapshot(positions)
    return sum((item.market_value for item in positions), Decimal("0"))


def _decimal(value: str | None, line_number: int, field: str) -> Decimal:
    try:
        parsed = Decimal((value or "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"第{line_number}行{field}必须是有限十进制数") from exc
    if not parsed.is_finite():
        raise ValueError(f"第{line_number}行{field}必须是有限十进制数")
    return parsed


__all__ = [
    "SNAPSHOT_COLUMNS",
    "parse_account_snapshot",
    "snapshot_total",
    "validate_complete_snapshot",
]
