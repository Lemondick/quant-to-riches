"""Protocols and explicit failures for external market-data providers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

import pandas as pd


class ProviderError(RuntimeError):
    """Base class for errors at the external-data boundary."""


class ProviderUnavailable(ProviderError):
    """The provider could not be reached after bounded retries."""


class ProviderDataError(ProviderError):
    """The provider response cannot be normalized safely."""


class DailyMarketProvider(Protocol):
    name: str

    def fetch_stock_universe(self) -> pd.DataFrame: ...

    def fetch_stock_daily(self, trade_date: date) -> pd.DataFrame: ...

    def fetch_stock_history(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...

    def fetch_index_history(
        self, symbols: Sequence[str], start: date, end: date
    ) -> pd.DataFrame: ...

    def fetch_fund_daily(self) -> pd.DataFrame: ...

    def fetch_fund_nav(self, symbol: str) -> pd.DataFrame: ...


class ResearchEvidenceProvider(Protocol):
    """Optional evidence contract used by the automatic research sync."""

    name: str

    def fetch_valuation_history(
        self, symbols: Sequence[str], start: date, end: date
    ) -> pd.DataFrame: ...

    def fetch_manager_history(self, symbols: Sequence[str], as_of_date: date) -> pd.DataFrame: ...
