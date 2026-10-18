"""Database-backed production strategy replay and conservative qualification gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal
from math import isfinite
from types import MappingProxyType

import pandas as pd

from quant_assistant.backtest.engine import (
    BacktestContext,
    BacktestEngine,
    BacktestRequest,
    BacktestResult,
    FrameBacktestData,
)
from quant_assistant.backtest.metrics import PerformanceMetrics, calculate_performance
from quant_assistant.backtest.models import StockFeeSchedule
from quant_assistant.backtest.repository import BacktestRepository
from quant_assistant.backtest.stock_execution import StockExecutionModel
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.features.technical import (
    calculate_stock_feature_history,
)
from quant_assistant.models.repositories.database import Database
from quant_assistant.portfolio.drawdown_control import DrawdownControlService
from quant_assistant.strategies.market_regime import classify_market_regime
from quant_assistant.strategies.stocks import (
    _DEFAULT_CONFIG as _DEFAULT_STOCK_CONFIG,
)
from quant_assistant.strategies.stocks import (
    StockStrategyConfig,
    generate_stock_signals,
    rank_stocks,
)

MINIMUM_QUALIFICATION_SESSIONS = 120


@dataclass(frozen=True, slots=True)
class BacktestCostAssumptions:
    commission_rate: Decimal
    minimum_commission: Decimal
    sell_stamp_tax_rate: Decimal
    transfer_fee_rate: Decimal
    slippage_bps: Decimal
    verified_on: date | None = None
    evidence_id: str | None = None
    source_document: str | None = None
    broker_name: str | None = None

    def schedule(self, start_date: date, *, stress_multiplier: Decimal = Decimal("1")):
        verified_on = self.verified_on or start_date
        return StockFeeSchedule(
            commission_rate=self.commission_rate * stress_multiplier,
            minimum_commission=self.minimum_commission * stress_multiplier,
            sell_stamp_tax_rate=self.sell_stamp_tax_rate * stress_multiplier,
            transfer_fee_rate=self.transfer_fee_rate * stress_multiplier,
            slippage_bps=self.slippage_bps * stress_multiplier,
            verified_on=verified_on,
        )


@dataclass(frozen=True, slots=True)
class DatabaseBacktestResult:
    result: BacktestResult
    standard_metrics: PerformanceMetrics
    stress_metrics: PerformanceMetrics
    qualification_passed: bool
    qualification_failures: tuple[str, ...]


def _serializable_metrics(metrics: PerformanceMetrics) -> dict[str, object]:
    # Undefined annualization/Sharpe values stay unknown in stored JSON. The
    # qualification checks above still inspect the original non-finite values.
    return {
        key: None if isinstance(value, float) and not isfinite(value) else value
        for key, value in asdict(metrics).items()
    }


def run_database_backtest(
    settings: Settings,
    start_date: date,
    end_date: date,
    *,
    initial_cash: Decimal,
    costs: BacktestCostAssumptions,
    research_only_reasons: tuple[str, ...] = (),
) -> DatabaseBacktestResult:
    """Replay the locked stock strategy with ordinary and doubled execution costs."""

    if start_date >= end_date:
        raise ValueError("backtest start_date must precede end_date")
    if costs.verified_on is not None and costs.verified_on > start_date:
        raise ValueError("fee verified_on must not be after the backtest start date")
    database = Database(settings.database_path)
    database.initialize()
    loader, benchmark = _database_frames(database, start_date, end_date)
    fee_limitations = (
        ("unverified_broker_fee_assumptions",)
        if (
            costs.verified_on is None
            or not costs.evidence_id
            or not costs.source_document
            or not costs.broker_name
        )
        else ()
    )
    parameters = MappingProxyType(
        {
            "strategy": "locked_stock_base_v1",
            "costs": {key: str(value) for key, value in asdict(costs).items()},
        }
    )
    request = BacktestRequest(
        start=start_date,
        end=end_date,
        initial_cash=initial_cash,
        strategy_version=_DEFAULT_STOCK_CONFIG.version,
        parameters=parameters,
        research_only_reasons=(*fee_limitations, *research_only_reasons),
    )
    strategy = ProductionStockBacktestStrategy()
    standard = BacktestEngine(
        loader,
        stock_execution=StockExecutionModel(costs.schedule(start_date)),
        strategy=strategy,
    ).run(request)
    stress = BacktestEngine(
        loader,
        stock_execution=StockExecutionModel(
            costs.schedule(start_date, stress_multiplier=Decimal("2"))
        ),
        strategy=strategy,
    ).run(replace(request, parameters={**dict(parameters), "cost_stress": "2x"}))
    standard_metrics = calculate_performance(standard.nav, benchmark)
    stress_metrics = calculate_performance(stress.nav, benchmark)
    drawdown_report = DrawdownControlService(database).validate_and_store(
        standard.run_id, standard.nav, threshold=0.20
    )
    failures = _qualification_failures(
        len(standard.trading_dates),
        standard_metrics,
        stress_metrics,
        drawdown_report=drawdown_report,
    )
    limitations = tuple(
        dict.fromkeys(
            (
                *standard.limitations,
                *(f"qualification:{failure}" for failure in failures),
            )
        )
    )
    qualified_result = replace(
        standard,
        research_only=bool(limitations),
        limitations=limitations,
    )
    metrics_payload = {
        **_serializable_metrics(standard_metrics),
        "stress": _serializable_metrics(stress_metrics),
        "qualification_passed": not failures,
        "qualification_failures": failures,
        "standard_costs": asdict(costs),
        "stress_multiplier": "2",
        "drawdown_control": asdict(drawdown_report),
    }
    now = datetime.now(tz=SHANGHAI_TZ)
    BacktestRepository(database).save_result(
        request,
        qualified_result,
        started_at=now,
        completed_at=now,
        metrics=metrics_payload,
    )
    return DatabaseBacktestResult(
        qualified_result,
        standard_metrics,
        stress_metrics,
        not failures,
        failures,
    )


class ProductionStockBacktestStrategy:
    """Adapter that reuses the same feature, ranking, regime, and signal functions."""

    def __init__(self, config: StockStrategyConfig | None = None):
        self.config = config or _DEFAULT_STOCK_CONFIG
        self._feature_history: pd.DataFrame | None = None
        self._feature_source: int | None = None
        self._point_in_time_features: pd.DataFrame | None = None
        self._trading_dates: tuple[date, ...] = ()
        self._features_by_date: dict[date, pd.DataFrame] = {}
        self._ranked_by_date: dict[date, pd.DataFrame] = {}
        self._prior_rank_by_date: dict[date, pd.DataFrame] = {}

    def __call__(self, context: BacktestContext):
        features = self._features_on(context, context.as_of_date)
        ranked = self._ranked_on(context, context.as_of_date, features)
        index_history = context.prices.loc[context.prices["symbol"].astype(str).eq("000300.SH")]
        regime = classify_market_regime(index_history, context.as_of_date)
        rank_history = self._previous_week_ranking(context)
        valid_until = datetime.combine(
            context.execution_date,
            time(15, 0),
            tzinfo=SHANGHAI_TZ,
        )
        return generate_stock_signals(
            ranked,
            context.prices,
            rank_history,
            context.portfolio.positions,
            regime,
            context.as_of_date,
            valid_until,
            self.config,
        )

    def _previous_week_ranking(self, context: BacktestContext) -> pd.DataFrame:
        cached = self._prior_rank_by_date.get(context.as_of_date)
        if cached is not None:
            return cached.copy(deep=True)
        dates = sorted(
            value
            for value in self._trading_dates
            if value < context.as_of_date
            and value.isocalendar()[:2] < context.as_of_date.isocalendar()[:2]
        )
        if not dates:
            return pd.DataFrame()
        prior_date = dates[-1]
        features = self._features_on(context, prior_date)
        ranked = self._ranked_on(context, prior_date, features)
        if ranked.empty:
            return ranked
        result = ranked.assign(as_of_date=prior_date)
        self._prior_rank_by_date[context.as_of_date] = result.copy(deep=True)
        return result

    def _features_on(self, context: BacktestContext, as_of_date: date) -> pd.DataFrame:
        source = context.prices_all if context.prices_all is not None else context.prices
        source_id = id(source)
        if self._feature_history is None or self._feature_source != source_id:
            self._feature_history = calculate_stock_feature_history(source)
            self._feature_source = source_id
            self._trading_dates = tuple(
                sorted(
                    pd.to_datetime(source["trade_date"], errors="coerce").dt.date.dropna().unique()
                )
            )
            self._point_in_time_features = _build_point_in_time_features(
                self._feature_history, self._trading_dates
            )
            self._features_by_date.clear()
            self._ranked_by_date.clear()
            self._prior_rank_by_date.clear()
        cached = self._features_by_date.get(as_of_date)
        if cached is None:
            timestamp = pd.Timestamp(as_of_date)
            if (
                self._point_in_time_features is None
                or timestamp not in self._point_in_time_features.index
            ):
                cached = pd.DataFrame()
            else:
                cached = self._point_in_time_features.loc[[timestamp]].reset_index(drop=True)
                cached = cached.dropna(
                    subset=["ret_120", "ma_200", "volatility_20", "amount_20"]
                ).copy()
                cached["trade_date"] = cached["trade_date"].dt.date
            self._features_by_date[as_of_date] = cached
        return cached.copy(deep=True)

    def _ranked_on(
        self, context: BacktestContext, as_of_date: date, features: pd.DataFrame
    ) -> pd.DataFrame:
        cached = self._ranked_by_date.get(as_of_date)
        if cached is None:
            cached = rank_stocks(features, context.instruments, as_of_date, self.config)
            self._ranked_by_date[as_of_date] = cached
        return cached.copy(deep=True)


def _build_point_in_time_features(
    history: pd.DataFrame, trading_dates: tuple[date, ...]
) -> pd.DataFrame:
    """Forward-fill each symbol's causal feature row onto the market calendar."""

    columns = [
        "symbol",
        "trade_date",
        "close",
        "ret_20",
        "ret_60",
        "ret_120",
        "ma_120",
        "ma_200",
        "trend_120",
        "trend_200",
        "volatility_20",
        "amount_20",
    ]
    compact = history.loc[:, columns].copy()
    compact["symbol"] = compact["symbol"].astype(str)
    compact["trade_date"] = pd.to_datetime(compact["trade_date"], errors="coerce").astype(
        "datetime64[ns]"
    )
    compact = compact.dropna(subset=["symbol", "trade_date"])
    symbols = tuple(compact["symbol"].astype(str).drop_duplicates())
    calendar = pd.MultiIndex.from_product(
        [pd.to_datetime(trading_dates).astype("datetime64[ns]"), symbols],
        names=["trade_date", "symbol"],
    ).to_frame(index=False)
    calendar["symbol"] = calendar["symbol"].astype(str)
    calendar = calendar.sort_values(["trade_date", "symbol"], kind="mergesort")
    compact = compact.sort_values(["trade_date", "symbol"], kind="mergesort")
    merged = pd.merge_asof(
        calendar,
        compact,
        on="trade_date",
        by="symbol",
        direction="backward",
        allow_exact_matches=True,
        suffixes=("", "_history"),
    )
    return merged.set_index("trade_date", drop=False).sort_index()


def _database_frames(
    database: Database, start_date: date, end_date: date
) -> tuple[FrameBacktestData, pd.DataFrame]:
    prices = database.query_df(
        "SELECT * FROM daily_prices WHERE trade_date <= ? ORDER BY trade_date, symbol",
        [end_date],
    )
    if prices.empty:
        raise ValueError("database contains no price history")
    benchmark = prices.loc[
        prices["symbol"].astype(str).eq("000300.SH")
        & pd.to_datetime(prices["trade_date"], errors="coerce").ge(pd.Timestamp(start_date))
    ].copy()
    if benchmark["trade_date"].nunique() < 2:
        raise ValueError("沪深300 benchmark history is incomplete for the backtest period")
    instruments = database.query_df(
        "SELECT * FROM instrument_history WHERE as_of_date <= ? ORDER BY symbol, as_of_date",
        [end_date],
    )
    limitations: list[str] = []
    if instruments.empty:
        instruments = database.query_df(
            "SELECT * FROM instruments WHERE as_of_date <= ? ORDER BY symbol", [end_date]
        )
        limitations.append("instrument_metadata_not_point_in_time")
    if instruments.empty:
        instruments = database.query_df("SELECT * FROM instruments ORDER BY symbol")
        if not instruments.empty:
            limitations.append("historical_universe_reconstructed_from_current_snapshot")
    if instruments.empty:
        raise ValueError("database contains no instrument metadata")
    earliest_price = pd.to_datetime(prices["trade_date"], errors="coerce").min().date()
    earliest_metadata = pd.to_datetime(instruments["as_of_date"], errors="coerce").min().date()
    if earliest_metadata > start_date:
        latest = (
            instruments.sort_values(["symbol", "as_of_date"])
            .groupby("symbol", as_index=False)
            .tail(1)
        )
        listed_on = pd.to_datetime(latest["listed_on"], errors="coerce").dt.date
        latest["as_of_date"] = [
            max(earliest_price, value) if pd.notna(value) else earliest_price for value in listed_on
        ]
        instruments = latest
        limitations.append("historical_universe_reconstructed_from_current_snapshot")
    loader = FrameBacktestData(
        prices=prices,
        instruments=instruments,
        limitations=tuple(limitations),
    )
    return loader, benchmark


def _qualification_failures(
    sessions: int,
    standard: PerformanceMetrics,
    stress: PerformanceMetrics,
    *,
    drawdown_report: object | None = None,
) -> tuple[str, ...]:
    failures: list[str] = []
    if sessions < MINIMUM_QUALIFICATION_SESSIONS:
        failures.append(f"sessions_below_{MINIMUM_QUALIFICATION_SESSIONS}")
    required = {
        "standard_sharpe": standard.sharpe_zero_rate,
        "standard_excess": standard.excess_return,
        "standard_drawdown": standard.max_drawdown,
        "stress_total_return": stress.total_return,
        "stress_drawdown": stress.max_drawdown,
    }
    failures.extend(f"non_finite_{name}" for name, value in required.items() if not isfinite(value))
    if isfinite(standard.sharpe_zero_rate) and standard.sharpe_zero_rate < 0:
        failures.append("negative_standard_sharpe")
    if isfinite(standard.excess_return) and standard.excess_return < 0:
        failures.append("negative_standard_excess_return")
    if isfinite(standard.max_drawdown) and standard.max_drawdown < -0.25:
        failures.append("standard_drawdown_below_minus_25pct")
    if isfinite(stress.total_return) and stress.total_return < 0:
        failures.append("negative_stress_total_return")
    if isfinite(stress.max_drawdown) and stress.max_drawdown < -0.30:
        failures.append("stress_drawdown_below_minus_30pct")
    if drawdown_report is not None and not bool(getattr(drawdown_report, "passed", False)):
        failures.append("drawdown_control_line_not_validated")
    return tuple(dict.fromkeys(failures))


__all__ = [
    "BacktestCostAssumptions",
    "DatabaseBacktestResult",
    "MINIMUM_QUALIFICATION_SESSIONS",
    "ProductionStockBacktestStrategy",
    "run_database_backtest",
]
