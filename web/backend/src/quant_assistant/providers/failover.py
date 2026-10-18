"""Bounded multi-source failover for market-data adapters.

Each source still has to normalize its response to ``DailyMarketProvider``'s
contract.  The wrapper only decides *which* source to call and records health;
it does not merge incompatible quotes or silently fill missing rows.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from quant_assistant.providers.base import (
    DailyMarketProvider,
    ProviderDataError,
    ProviderError,
    ProviderUnavailable,
)


@dataclass(frozen=True, slots=True)
class SourceHealth:
    name: str
    available: bool
    consecutive_failures: int
    last_error: str | None
    last_success_at: float | None
    cooldown_until: float | None


class FailoverMarketProvider:
    """Try sources in configured order with per-source circuit cooldown."""

    name = "market-failover"

    def __init__(
        self,
        providers: Sequence[DailyMarketProvider],
        *,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        if any(not getattr(provider, "name", "").strip() for provider in providers):
            raise ValueError("every provider must expose a non-empty name")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        self.providers = tuple(providers)
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._state: dict[str, dict[str, Any]] = {
            provider.name: {
                "failures": 0,
                "last_error": None,
                "last_success_at": None,
                "cooldown_until": None,
            }
            for provider in self.providers
        }
        self._active_source: str | None = None
        self._last_stock_history_source: str | None = None
        self._last_stock_daily_source: str | None = None

    @property
    def active_source(self) -> str | None:
        return self._active_source

    @property
    def last_stock_history_source(self) -> str | None:
        return self._last_stock_history_source

    @property
    def last_stock_daily_source(self) -> str | None:
        return self._last_stock_daily_source

    @property
    def history_snapshot_source_prefix(self) -> str | None:
        if len(self.providers) == 1:
            return getattr(self.providers[0], "history_snapshot_source_prefix", None)
        return None

    def health(self) -> tuple[SourceHealth, ...]:
        now = self._clock()
        return tuple(
            SourceHealth(
                name=provider.name,
                available=(
                    self._state[provider.name]["cooldown_until"] is None
                    or self._state[provider.name]["cooldown_until"] <= now
                ),
                consecutive_failures=int(self._state[provider.name]["failures"]),
                last_error=self._state[provider.name]["last_error"],
                last_success_at=self._state[provider.name]["last_success_at"],
                cooldown_until=self._state[provider.name]["cooldown_until"],
            )
            for provider in self.providers
        )

    def source_status(self) -> pd.DataFrame:
        """Return a UI-friendly health table without exposing exception text."""

        return pd.DataFrame(
            [
                {
                    "source": item.name,
                    "available": item.available,
                    "consecutive_failures": item.consecutive_failures,
                    "last_error": item.last_error,
                    "last_success_at": item.last_success_at,
                    "cooldown_until": item.cooldown_until,
                }
                for item in self.health()
            ]
        )

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> pd.DataFrame:
        now = self._clock()
        if method_name == "fetch_stock_history":
            self._last_stock_history_source = None
        if method_name == "fetch_stock_daily":
            self._last_stock_daily_source = None
        errors: list[str] = []
        attempted = False
        for provider in self.providers:
            state = self._state[provider.name]
            cooldown = state["cooldown_until"]
            if cooldown is not None and cooldown > now:
                continue
            attempted = True
            method = getattr(provider, method_name, None)
            if not callable(method):
                self._mark_failure(provider.name, f"{method_name} unsupported")
                errors.append(f"{provider.name}:unsupported")
                continue
            try:
                result = method(*args, **kwargs)
                if not isinstance(result, pd.DataFrame):
                    raise ProviderDataError(f"{method_name} returned non-DataFrame")
                if result.empty:
                    raise ProviderDataError(f"{method_name} returned empty data")
            except (ProviderError, ConnectionError, TimeoutError, OSError, ValueError) as exc:
                self._mark_failure(provider.name, type(exc).__name__)
                errors.append(f"{provider.name}:{type(exc).__name__}")
                continue
            self._mark_success(provider.name)
            self._active_source = provider.name
            if method_name == "fetch_stock_history":
                self._last_stock_history_source = (
                    getattr(provider, "last_stock_history_source", None) or provider.name
                )
            if method_name == "fetch_stock_daily":
                self._last_stock_daily_source = (
                    getattr(provider, "last_stock_daily_source", None) or provider.name
                )
            return result.copy(deep=True)
        if not attempted:
            raise ProviderUnavailable(f"all market sources are in cooldown for {method_name}")
        raise ProviderUnavailable(
            f"all market sources failed for {method_name}: {', '.join(errors)}"
        )

    def _mark_failure(self, name: str, error: str) -> None:
        state = self._state[name]
        state["failures"] += 1
        state["last_error"] = error
        state["cooldown_until"] = self._clock() + self.cooldown_seconds

    def _mark_success(self, name: str) -> None:
        state = self._state[name]
        state["failures"] = 0
        state["last_error"] = None
        state["last_success_at"] = self._clock()
        state["cooldown_until"] = None

    def fetch_stock_universe(self) -> pd.DataFrame:
        return self._call("fetch_stock_universe")

    def fetch_stock_daily(self, trade_date: date) -> pd.DataFrame:
        return self._call("fetch_stock_daily", trade_date)

    def fetch_stock_history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self._call("fetch_stock_history", symbol, start, end)

    def fetch_index_history(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        return self._call("fetch_index_history", symbols, start, end)

    def fetch_fund_daily(self) -> pd.DataFrame:
        return self._call("fetch_fund_daily")

    def fetch_fund_nav(self, symbol: str) -> pd.DataFrame:
        return self._call("fetch_fund_nav", symbol)

    def fetch_valuation_history(
        self, symbols: Sequence[str], start: date, end: date
    ) -> pd.DataFrame:
        return self._call("fetch_valuation_history", symbols, start, end)

    def fetch_manager_history(self, symbols: Sequence[str], as_of_date: date) -> pd.DataFrame:
        return self._call("fetch_manager_history", symbols, as_of_date)


def build_failover_provider(
    *providers: DailyMarketProvider, cooldown_seconds: float = 60.0
) -> FailoverMarketProvider:
    """Convenience constructor for Eastmoney → Sina → Tencent → official order."""

    return FailoverMarketProvider(providers, cooldown_seconds=cooldown_seconds)


__all__ = ["FailoverMarketProvider", "SourceHealth", "build_failover_provider"]
