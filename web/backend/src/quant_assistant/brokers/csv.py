"""Read-only broker export adapter.

Many brokers do not expose a stable local API.  A UTF-8 account export is therefore
the safest first integration: it is auditable, requires no credentials, and cannot
submit an order.  The strict account parser is shared with the CLI and dashboard.
"""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from types import MappingProxyType

from quant_assistant.brokers.base import BrokerAccountSnapshot, BrokerUnavailable
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.services.account_snapshot import parse_account_snapshot


class CsvBrokerAdapter:
    """Fetch a complete broker-exported account snapshot without network access."""

    def __init__(
        self,
        path: str | Path,
        *,
        broker_name: str = "broker_csv",
        account_id: str = "file-export",
        as_of_date=None,
    ) -> None:
        self._path = Path(path)
        self._broker_name = _required_text(broker_name, "broker_name")
        self._account_id = _required_text(account_id, "account_id")
        self._as_of_date = as_of_date

    def fetch_account_snapshot(self) -> BrokerAccountSnapshot:
        """Parse the export and return immutable, normalized values."""

        text = self._path.read_text(encoding="utf-8")
        as_of = self._as_of_date
        if as_of is None:
            raise ValueError("CSV券商适配器必须显式提供as_of_date")
        rows = parse_account_snapshot(text, as_of)
        positions = tuple(
            MappingProxyType(
                {
                    "symbol": row.symbol,
                    "asset_type": row.asset_type.value,
                    "quantity": row.quantity,
                    "average_cost": row.average_cost,
                    "market_value": row.market_value,
                    "as_of_date": row.as_of_date,
                }
            )
            for row in rows
        )
        cash = next(row.market_value for row in rows if row.asset_type.value == "cash")
        return BrokerAccountSnapshot(
            broker_name=self._broker_name,
            account_id=self._account_id,
            as_of=datetime.combine(as_of, time(16, 0), tzinfo=SHANGHAI_TZ),
            cash=cash,
            positions=positions,
        )

    def submit_order(self, order: object) -> str:
        """CSV exports are read-only by construction."""

        raise BrokerUnavailable("CSV券商适配器只读，不允许自动下单")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}必须是非空字符串")
    return value.strip()


__all__ = ["CsvBrokerAdapter"]
