"""Read-only consistency checks for the local account ledger and NAV baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from quant_assistant.models.repositories.database import Database

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class AccountAuditReport:
    """A conservative account reconciliation result."""

    as_of_date: date | None
    position_count: int
    total_value: Decimal
    cash_value: Decimal
    nav_baseline_date: date | None
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers


def audit_account(database: Database, as_of_date: date | None = None) -> AccountAuditReport:
    """Check positions, cash, and the latest NAV valuation without mutating state."""

    if not isinstance(database, Database):
        raise TypeError("database must be Database")
    positions = database.query_df(
        """
        SELECT symbol, asset_type, quantity, average_cost, market_value, as_of_date
        FROM positions ORDER BY symbol
        """
    )
    if as_of_date is not None and not positions.empty:
        dates = pd.to_datetime(positions["as_of_date"], errors="coerce").dt.date
        positions = positions.loc[dates.le(as_of_date)].copy()

    blockers: list[str] = []
    position_count = len(positions)
    if positions.empty:
        blockers.append("no_positions_snapshot")

    cash_rows = (
        positions.loc[positions["asset_type"].astype(str).eq("cash")]
        if not positions.empty
        else positions
    )
    cash_value = Decimal("0")
    if len(cash_rows) != 1:
        blockers.append("cash_position_count_is_not_one")
    else:
        cash = cash_rows.iloc[0]
        cash_value = _decimal(cash["market_value"], "cash.market_value", blockers)
        cash_quantity = _decimal(cash["quantity"], "cash.quantity", blockers)
        cash_cost = _decimal(cash["average_cost"], "cash.average_cost", blockers)
        if cash_quantity < 0 or cash_value < 0:
            blockers.append("cash_position_negative")
        if cash_cost != Decimal("1"):
            blockers.append("cash_average_cost_is_not_one")

    total_value = Decimal("0")
    for row in positions.to_dict("records"):
        quantity = _decimal(row["quantity"], "position.quantity", blockers)
        average_cost = _decimal(row["average_cost"], "position.average_cost", blockers)
        market_value = _decimal(row["market_value"], "position.market_value", blockers)
        total_value += market_value
        if quantity < 0 or average_cost < 0 or market_value < 0:
            blockers.append(f"negative_position_value:{row['symbol']}")
    if total_value <= 0:
        blockers.append("portfolio_total_value_not_positive")

    valuations = database.query_df(
        """
        SELECT as_of_date, total_value, cash, is_baseline
        FROM portfolio_valuations ORDER BY as_of_date DESC, generated_at DESC
        """
    )
    if as_of_date is not None and not valuations.empty:
        valuation_dates = pd.to_datetime(valuations["as_of_date"], errors="coerce").dt.date
        valuations = valuations.loc[valuation_dates.le(as_of_date)].copy()
    nav_baseline_date: date | None = None
    if valuations.empty:
        blockers.append("nav_baseline_missing")
    else:
        baseline_rows = valuations.loc[valuations["is_baseline"].fillna(False).astype(bool)]
        if baseline_rows.empty:
            blockers.append("nav_baseline_not_explicit")
        else:
            nav_baseline_date = _date_value(baseline_rows.iloc[0]["as_of_date"])
        valuation = valuations.iloc[0]
        nav_total = _decimal(valuation["total_value"], "nav.total_value", blockers)
        nav_cash = _decimal(valuation["cash"], "nav.cash", blockers)
        if abs(nav_total - total_value) > _CENT:
            blockers.append("nav_total_value_mismatch")
        if abs(nav_cash - cash_value) > _CENT:
            blockers.append("nav_cash_value_mismatch")

    latest_position_date = _latest_date(positions.get("as_of_date"))
    effective_date = as_of_date or latest_position_date or nav_baseline_date
    return AccountAuditReport(
        as_of_date=effective_date,
        position_count=position_count,
        total_value=total_value,
        cash_value=cash_value,
        nav_baseline_date=nav_baseline_date,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _decimal(value: object, field: str, blockers: list[str]) -> Decimal:
    try:
        if pd.isna(value):
            raise ValueError
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        blockers.append(f"invalid_decimal:{field}")
        return Decimal("0")
    if not parsed.is_finite():
        blockers.append(f"invalid_decimal:{field}")
        return Decimal("0")
    return parsed


def _date_value(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _latest_date(values: pd.Series | None) -> date | None:
    if values is None or values.empty:
        return None
    parsed = pd.to_datetime(values, errors="coerce").dropna()
    return None if parsed.empty else parsed.max().date()


__all__ = ["AccountAuditReport", "audit_account"]
