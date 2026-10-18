"""Safe broker adapter contracts with no default network or order capability."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class BrokerUnavailable(RuntimeError):
    """Raised when no explicitly configured broker integration is available."""


@dataclass(frozen=True, slots=True)
class BrokerAccountSnapshot:
    broker_name: str
    account_id: str
    as_of: datetime
    cash: Decimal
    positions: tuple[Mapping[str, object], ...]


class BrokerAdapter(Protocol):
    """The smallest boundary needed for future read-only reconciliation."""

    def fetch_account_snapshot(self) -> BrokerAccountSnapshot:
        """Fetch a broker snapshot; implementations must not mutate local state."""

    def submit_order(self, order: Mapping[str, object]) -> str:
        """Submit an order only in an explicitly authorized adapter."""


class DisabledBrokerAdapter:
    """Fail closed until a reviewed broker integration is explicitly installed."""

    def fetch_account_snapshot(self) -> BrokerAccountSnapshot:
        raise BrokerUnavailable("未配置券商只读适配器；不会猜测账户资产")

    def submit_order(self, order: Mapping[str, object]) -> str:
        raise BrokerUnavailable("当前系统不允许自动下单；请在官方客户端人工确认")


__all__ = [
    "BrokerAccountSnapshot",
    "BrokerAdapter",
    "BrokerUnavailable",
    "DisabledBrokerAdapter",
]
