"""Shared daily-decision value objects and repository protocol."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

import pandas as pd

from quant_assistant.funds.fees import FundFeeSchedule
from quant_assistant.models.entities import (
    DataStatus,
    MarketRegime,
    Position,
    ProposedOrder,
    ReadinessStatus,
    Signal,
)
from quant_assistant.portfolio.models import PortfolioState, PortfolioValuationDraft


@dataclass(frozen=True, slots=True)
class DailyDecisionResult:
    run_id: str
    as_of_date: date
    generated_at: datetime
    status: DataStatus
    readiness: ReadinessStatus
    data_snapshot_id: int
    market_regime: MarketRegime
    signals: tuple[Signal, ...]
    orders: tuple[ProposedOrder, ...]
    blockers: tuple[str, ...]
    strategy_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        if not isinstance(self.data_snapshot_id, int) or isinstance(self.data_snapshot_id, bool):
            raise TypeError("data_snapshot_id must be an integer")
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "blockers", tuple(self.blockers))


@dataclass(frozen=True, slots=True)
class DecisionInputs:
    """Point-in-time frames and state consumed by the strategy composition."""

    as_of_date: date
    data_snapshot_id: int
    status: DataStatus
    blockers: tuple[str, ...]
    daily_prices: pd.DataFrame
    stock_rank_history: pd.DataFrame
    instruments: pd.DataFrame
    fund_pool: pd.DataFrame
    fund_status: pd.DataFrame
    fee_schedules: Mapping[str, FundFeeSchedule]
    positions: tuple[Position, ...]
    portfolio: PortfolioState
    portfolio_valuation: PortfolioValuationDraft
    prices: Mapping[str, Decimal]
    market_regime: MarketRegime
    strategy_versions: tuple[str, ...]
    # Macro observations are contextual evidence only.  Production risk
    # decisions remain unchanged until a separately validated macro policy is
    # enabled; keeping this optional preserves compatibility with old adapters.
    macro_context: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyRunArtifacts:
    """Signals plus the complete stock cross-section used to create them."""

    signals: tuple[Signal, ...]
    stock_ranking: pd.DataFrame

    def __post_init__(self) -> None:
        object.__setattr__(self, "signals", tuple(self.signals))
        # Strategy outputs are treated as read-only after construction; avoid a
        # full block copy of large ranking frames on every decision run.
        object.__setattr__(self, "stock_ranking", self.stock_ranking.copy(deep=False))


class DecisionRepository(Protocol):
    def load_decision_inputs(self, as_of_date: date) -> DecisionInputs: ...

    def save_decision(
        self,
        result: DailyDecisionResult,
        strategy_versions: Sequence[str],
        portfolio: PortfolioState,
        portfolio_valuation: PortfolioValuationDraft,
        stock_ranking: pd.DataFrame,
    ) -> None: ...
