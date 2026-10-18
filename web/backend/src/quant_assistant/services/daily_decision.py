"""Compose validated local data into auditable daily investment decisions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.features.technical import calculate_stock_features
from quant_assistant.models.entities import (
    Action,
    DataStatus,
    ProposedOrder,
    ReadinessStatus,
    Signal,
)
from quant_assistant.models.repositories.database import Database
from quant_assistant.portfolio.models import PortfolioState, RiskPolicy
from quant_assistant.portfolio.nav_continuity import (
    _build_portfolio as _build_portfolio,
)
from quant_assistant.portfolio.nav_continuity import (
    _canonical_json,
)
from quant_assistant.portfolio.nav_continuity import (
    _decimal as _decimal,
)
from quant_assistant.portfolio.nav_continuity import (
    _decimal_or_none as _decimal_or_none,
)
from quant_assistant.portfolio.nav_continuity import (
    _has_complete_stock_history as _has_complete_stock_history,
)
from quant_assistant.portfolio.nav_continuity import (
    _latest_marks as _latest_marks,
)
from quant_assistant.portfolio.nav_continuity import (
    _portfolio_events_fingerprint as _portfolio_events_fingerprint,
)
from quant_assistant.portfolio.nav_continuity import (
    _portfolio_nav_state as _portfolio_nav_state,
)
from quant_assistant.portfolio.nav_continuity import (
    _portfolio_positions_fingerprint as _portfolio_positions_fingerprint,
)
from quant_assistant.portfolio.nav_continuity import (
    _required_date as _required_date,
)
from quant_assistant.portfolio.nav_continuity import (
    _to_date as _to_date,
)
from quant_assistant.portfolio.nav_continuity import (
    _utc_timestamp as _utc_timestamp,
)
from quant_assistant.portfolio.orders import propose_orders
from quant_assistant.services.decision_models import (
    DailyDecisionResult,
    DecisionInputs,
    DecisionRepository,
    PortfolioValuationDraft,
    StrategyRunArtifacts,
)
from quant_assistant.services.decision_repository import (
    DuckDBDecisionRepository,
    _ranking_records,
    _unique_strings,
)
from quant_assistant.services.decision_repository import (
    _fee_schedules as _fee_schedules,
)
from quant_assistant.services.decision_repository import (
    _fund_status_frame as _fund_status_frame,
)
from quant_assistant.services.decision_repository import (
    _json_default as _json_default,
)
from quant_assistant.services.decision_repository import (
    _json_value as _json_value,
)
from quant_assistant.services.decision_repository import (
    _redemption_tier as _redemption_tier,
)
from quant_assistant.services.decision_repository import (
    _share_class as _share_class,
)
from quant_assistant.services.decision_repository import (
    _snapshot_issues as _snapshot_issues,
)
from quant_assistant.services.decision_repository import (
    _split_versions as _split_versions,
)
from quant_assistant.services.decision_repository import (
    _strategy_stock_symbols as _strategy_stock_symbols,
)
from quant_assistant.services.decision_repository import (
    _with_fund_instruments as _with_fund_instruments,
)
from quant_assistant.services.forward_sessions import strategy_fingerprint
from quant_assistant.strategies.funds import (
    _DEFAULT_CONFIG as _DEFAULT_FUND_CONFIG,
)
from quant_assistant.strategies.funds import (
    FundStrategyConfig,
    generate_fund_signals,
)
from quant_assistant.strategies.stocks import (
    _DEFAULT_CONFIG as _DEFAULT_STOCK_CONFIG,
)
from quant_assistant.strategies.stocks import (
    StockStrategyConfig,
    generate_stock_signals,
    rank_stocks,
)

_ZERO = Decimal("0")
_STATUS_PRIORITY = {
    DataStatus.VALID: 0,
    DataStatus.STALE: 1,
    DataStatus.BLOCKED: 2,
}
_RISK_INCREASING = frozenset({Action.BUY, Action.ADD})
_RISK_REDUCING = frozenset({Action.REDUCE, Action.SELL})


class _Clock(Protocol):
    def now(self) -> datetime: ...


class ShanghaiClock:
    def now(self) -> datetime:
        return datetime.now(tz=SHANGHAI_TZ)


class DecisionInputGate:
    """Expose blockers already tied to concrete snapshot or input checks."""

    def blockers(self, inputs: DecisionInputs) -> tuple[str, ...]:
        return tuple(inputs.blockers)


class DefaultStrategyRunner:
    """Run the production stock and fund strategy functions on repository inputs."""

    def __init__(
        self,
        stock_config: StockStrategyConfig | None = None,
        fund_config: FundStrategyConfig | None = None,
    ) -> None:
        self.stock_config = stock_config if stock_config is not None else _DEFAULT_STOCK_CONFIG
        self.fund_config = fund_config if fund_config is not None else _DEFAULT_FUND_CONFIG

    def run_with_artifacts(self, inputs: DecisionInputs) -> StrategyRunArtifacts:
        signals: list[Signal] = []
        stock_ranking = pd.DataFrame()
        if self.stock_config.version in inputs.strategy_versions:
            features = calculate_stock_features(inputs.daily_prices, inputs.as_of_date)
            stock_ranking = rank_stocks(
                features,
                inputs.instruments,
                inputs.as_of_date,
                self.stock_config,
            )
            signals.extend(
                generate_stock_signals(
                    stock_ranking,
                    inputs.daily_prices,
                    inputs.stock_rank_history,
                    inputs.positions,
                    inputs.market_regime,
                    inputs.as_of_date,
                    _next_submission_deadline(inputs.as_of_date),
                    self.stock_config,
                )
            )
        if self.fund_config.version in inputs.strategy_versions:
            signals.extend(
                generate_fund_signals(
                    inputs.fund_pool,
                    inputs.daily_prices,
                    inputs.fund_status,
                    inputs.fee_schedules,
                    inputs.as_of_date,
                    expected_holding_days=self.fund_config.rotation_holding_days,
                    config=self.fund_config,
                )
            )
        return StrategyRunArtifacts(tuple(signals), stock_ranking)

    def run(self, inputs: DecisionInputs) -> list[Signal]:
        """Keep the original runner surface for injected and external callers."""

        return list(self.run_with_artifacts(inputs).signals)

    def evidence_fingerprint(
        self, versions: Sequence[str], *, policy: RiskPolicy | None = None
    ) -> str:
        return strategy_fingerprint(versions, (self.stock_config, self.fund_config), policy=policy)

    def risk_reducing_only(self, inputs: DecisionInputs) -> list[Signal]:
        signals = self.run(inputs)
        reason = (
            "数据状态为过期，禁止新增风险"
            if inputs.status is DataStatus.STALE
            else "数据校验被阻断，禁止新增风险"
        )
        return [_block_risk_increase(signal, reason) for signal in signals]


class ReadinessGate:
    """Require safe data, usable backtests, and 20 prior paper sessions."""

    def __init__(self, repository: object, minimum_paper_sessions: int = 20):
        if minimum_paper_sessions <= 0:
            raise ValueError("minimum_paper_sessions must be positive")
        self.repository = repository
        self.minimum_paper_sessions = minimum_paper_sessions

    def evaluate(
        self,
        strategy_versions: Sequence[str],
        status: DataStatus,
        as_of_date: date,
    ) -> ReadinessStatus:
        versions = tuple(sorted(set(strategy_versions)))
        if status is not DataStatus.VALID or not versions:
            return ReadinessStatus.RESEARCH_ONLY
        limitation_loader = getattr(self.repository, "blocking_backtest_limitations", None)
        if not callable(limitation_loader) or limitation_loader(versions):
            return ReadinessStatus.RESEARCH_ONLY
        date_loader = getattr(self.repository, "valid_paper_session_dates", None)
        if not callable(date_loader):
            return ReadinessStatus.RESEARCH_ONLY
        for version in versions:
            dates = date_loader(version, as_of_date)
            if len(set(dates)) < self.minimum_paper_sessions:
                return ReadinessStatus.PAPER
        return ReadinessStatus.REFERENCE


class DailyDecisionService:
    """Create and atomically persist one deterministic daily decision."""

    def __init__(
        self,
        repository: DecisionRepository | Database | Path,
        clock: _Clock | Callable[[], datetime] | None = None,
        *,
        strategy_runner: object | None = None,
        input_gate: object | None = None,
        readiness_gate: object | None = None,
        policy: RiskPolicy | None = None,
        order_proposer: Callable[..., list[ProposedOrder]] = propose_orders,
        run_id_factory: Callable[..., str] | None = None,
    ) -> None:
        if isinstance(repository, (Database, Path)):
            repository = DuckDBDecisionRepository(repository)
        self.repository = repository
        self.clock = clock or ShanghaiClock()
        self.strategy_runner = strategy_runner or DefaultStrategyRunner()
        self.input_gate = input_gate or DecisionInputGate()
        self.readiness_gate = readiness_gate or ReadinessGate(repository)
        self.policy = policy or RiskPolicy()
        self.order_proposer = order_proposer
        self.run_id_factory = run_id_factory

    def run(self, as_of_date: date) -> DailyDecisionResult:
        if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
            raise TypeError("as_of_date must be a date")
        generated_at = _clock_now(self.clock)
        inputs = self.repository.load_decision_inputs(as_of_date)
        blockers = _unique_strings(self.input_gate.blockers(inputs))
        status = inputs.status
        if blockers and status is DataStatus.VALID:
            status = DataStatus.BLOCKED

        stock_ranking = pd.DataFrame()
        try:
            if status is DataStatus.VALID:
                run_with_artifacts = getattr(self.strategy_runner, "run_with_artifacts", None)
                if callable(run_with_artifacts):
                    artifacts = _strategy_run_artifacts(run_with_artifacts(inputs))
                    generated_signals = artifacts.signals
                    stock_ranking = artifacts.stock_ranking
                else:
                    generated_signals = self.strategy_runner.run(inputs)
            else:
                risk_only = getattr(self.strategy_runner, "risk_reducing_only", None)
                generated_signals = (
                    risk_only(inputs) if callable(risk_only) else self.strategy_runner.run(inputs)
                )
        except Exception as exc:
            blockers = _unique_strings((*blockers, f"策略运行失败：{type(exc).__name__}"))
            status = DataStatus.BLOCKED
            generated_signals = []

        signals, signal_blockers = _validate_signals(generated_signals, inputs, status)
        if signal_blockers:
            blockers = _unique_strings((*blockers, *signal_blockers))
            status = DataStatus.BLOCKED
            signals = tuple(
                _block_risk_increase(signal, "信号输入校验被阻断，禁止新增风险")
                for signal in signals
            )

        try:
            proposed = self.order_proposer(
                signals,
                inputs.portfolio,
                inputs.prices,
                inputs.instruments,
                self.policy,
                inputs.data_snapshot_id,
            )
        except Exception as exc:
            blockers = _unique_strings((*blockers, f"组合约束运行失败：{type(exc).__name__}"))
            status = DataStatus.BLOCKED
            proposed = []
        orders, order_blockers = _validate_orders(
            proposed, as_of_date, inputs.data_snapshot_id, status
        )
        if order_blockers:
            blockers = _unique_strings((*blockers, *order_blockers))
            status = DataStatus.BLOCKED
            orders = tuple(order for order in orders if order.action in _RISK_REDUCING)

        readiness = _evaluate_readiness(
            self.readiness_gate,
            inputs.strategy_versions,
            status,
            as_of_date,
        )
        fingerprint = (
            self.strategy_runner.evidence_fingerprint(inputs.strategy_versions, policy=self.policy)
            if type(self.strategy_runner) is DefaultStrategyRunner
            and self.order_proposer is propose_orders
            else ""
        )
        if readiness is ReadinessStatus.REFERENCE and (
            not fingerprint or fingerprint != strategy_fingerprint(inputs.strategy_versions)
        ):
            # A reused version label cannot qualify custom execution/configuration
            # against a different frozen strategy's forward evidence.
            readiness = ReadinessStatus.RESEARCH_ONLY
        payload = _run_payload(
            as_of_date,
            status,
            readiness,
            inputs,
            signals,
            orders,
            blockers,
            stock_ranking,
        )
        payload = {**payload, "strategy_fingerprint": fingerprint}
        run_id = self._run_id(payload)
        result = DailyDecisionResult(
            run_id=run_id,
            as_of_date=as_of_date,
            generated_at=generated_at,
            status=status,
            readiness=readiness,
            data_snapshot_id=inputs.data_snapshot_id,
            market_regime=inputs.market_regime,
            signals=signals,
            orders=orders,
            blockers=blockers,
            strategy_fingerprint=fingerprint,
        )
        persisted_ranking = stock_ranking if status is DataStatus.VALID else pd.DataFrame()
        _save_decision(
            self.repository,
            result,
            inputs.strategy_versions,
            inputs.portfolio,
            inputs.portfolio_valuation,
            persisted_ranking,
        )
        return result

    def _run_id(self, payload: Mapping[str, object]) -> str:
        if self.run_id_factory is None:
            encoded = _canonical_json(payload).encode("utf-8")
            return hashlib.sha256(encoded).hexdigest()[:32]
        try:
            run_id = self.run_id_factory(payload)
        except TypeError:
            run_id = self.run_id_factory()
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id_factory must return a non-empty string")
        return run_id.strip()


def build_daily_decision_service(
    database: Database | Path,
    clock: _Clock | Callable[[], datetime] | None = None,
    **kwargs: object,
) -> DailyDecisionService:
    repository = DuckDBDecisionRepository(database)
    return DailyDecisionService(repository, clock, **kwargs)


def _clock_now(clock: _Clock | Callable[[], datetime]) -> datetime:
    value = clock.now() if hasattr(clock, "now") else clock()
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(SHANGHAI_TZ)


def _strategy_run_artifacts(value: object) -> StrategyRunArtifacts:
    if not isinstance(value, StrategyRunArtifacts):
        raise TypeError("run_with_artifacts() must return StrategyRunArtifacts")
    _ranking_records(value.stock_ranking)
    return value


def _ranking_digest(ranking: pd.DataFrame) -> str:
    encoded = _canonical_json(_ranking_records(ranking)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evaluate_readiness(
    gate: object,
    strategy_versions: Sequence[str],
    status: DataStatus,
    as_of_date: date,
) -> ReadinessStatus:
    evaluate = getattr(gate, "evaluate", None)
    if not callable(evaluate):
        raise TypeError("readiness_gate must provide evaluate()")
    # All readiness implementations implement the single protocol above.  A
    # direct call keeps the contract explicit and avoids signature reflection
    # that could mask a real TypeError raised by the implementation.
    value = evaluate(strategy_versions, status, as_of_date)
    try:
        return value if isinstance(value, ReadinessStatus) else ReadinessStatus(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("readiness_gate returned an invalid status") from exc


def _save_decision(
    repository: object,
    result: DailyDecisionResult,
    strategy_versions: Sequence[str],
    portfolio: PortfolioState,
    portfolio_valuation: PortfolioValuationDraft,
    stock_ranking: pd.DataFrame,
) -> None:
    save = getattr(repository, "save_decision", None)
    if not callable(save):
        raise TypeError("repository must provide save_decision()")
    # DecisionRepository exposes one canonical persistence signature.  Calling
    # it directly makes incompatible adapters fail at the boundary instead of
    # silently selecting a legacy argument shape.
    save(result, strategy_versions, portfolio, portfolio_valuation, stock_ranking)


def _next_submission_deadline(as_of_date: date) -> datetime:
    next_date = as_of_date + timedelta(days=1)
    while next_date.weekday() >= 5:
        next_date += timedelta(days=1)
    return datetime.combine(next_date, time(15), tzinfo=SHANGHAI_TZ)


def _block_risk_increase(signal: Signal, reason: str) -> Signal:
    if signal.action not in _RISK_INCREASING:
        return signal
    reasons = _unique_strings((*signal.reasons, reason))
    return replace(
        signal,
        action=Action.NO_TRADE,
        target_weight=_ZERO,
        reasons=reasons,
    )


def _validate_signals(
    values: object, inputs: DecisionInputs, status: DataStatus
) -> tuple[tuple[Signal, ...], tuple[str, ...]]:
    if values is None:
        return (), ("策略返回空对象而不是信号序列",)
    if not isinstance(values, Sequence):
        return (), ("策略返回值不是信号序列",)
    valid: list[Signal] = []
    blockers: list[str] = []
    for value in values:
        if not isinstance(value, Signal):
            blockers.append("策略返回了非Signal对象")
            continue
        if value.as_of_date != inputs.as_of_date:
            blockers.append(f"{value.symbol}信号日期与策略日期不一致")
            continue
        if value.valid_until.tzinfo is None or value.valid_until.utcoffset() is None:
            blockers.append(f"{value.symbol}信号失效时间缺少时区")
            continue
        valid.append(
            _block_risk_increase(
                value,
                "数据校验未通过，禁止新增风险",
            )
            if status is not DataStatus.VALID
            else value
        )
    return _deduplicate_signals(valid), _unique_strings(blockers)


def _deduplicate_signals(signals: Sequence[Signal]) -> tuple[Signal, ...]:
    priority = {
        Action.SELL: 0,
        Action.REDUCE: 1,
        Action.NO_TRADE: 2,
        Action.HOLD: 3,
        Action.ADD: 4,
        Action.BUY: 5,
    }
    selected: dict[str, Signal] = {}
    for signal in sorted(
        signals,
        key=lambda item: (
            item.symbol,
            priority[item.action],
            item.strategy_version,
            str(item.valid_until),
        ),
    ):
        selected.setdefault(signal.symbol, signal)
    return tuple(selected[symbol] for symbol in sorted(selected))


def _validate_orders(
    values: object,
    as_of_date: date,
    snapshot_id: int,
    status: DataStatus,
) -> tuple[tuple[ProposedOrder, ...], tuple[str, ...]]:
    if values is None or not isinstance(values, Sequence):
        return (), ("组合引擎返回值不是订单序列",)
    orders: list[ProposedOrder] = []
    blockers: list[str] = []
    for value in values:
        if not isinstance(value, ProposedOrder):
            blockers.append("组合引擎返回了非ProposedOrder对象")
            continue
        if value.as_of_date != as_of_date:
            blockers.append(f"{value.symbol}建议单日期与策略日期不一致")
            continue
        if value.data_snapshot_id != snapshot_id:
            blockers.append(f"{value.symbol}建议单快照ID与运行不一致")
            continue
        if value.valid_until.tzinfo is None or value.valid_until.utcoffset() is None:
            blockers.append(f"{value.symbol}建议单失效时间缺少时区")
            continue
        if status is not DataStatus.VALID and value.action in _RISK_INCREASING:
            blockers.append(f"{value.symbol}在非有效数据状态下产生新增风险建议")
            continue
        orders.append(value)
    orders.sort(
        key=lambda item: (
            0 if item.action in _RISK_REDUCING else 1,
            item.symbol,
            item.action.value,
        )
    )
    return tuple(orders), _unique_strings(blockers)


def _run_payload(
    as_of_date: date,
    status: DataStatus,
    readiness: ReadinessStatus,
    inputs: DecisionInputs,
    signals: Sequence[Signal],
    orders: Sequence[ProposedOrder],
    blockers: Sequence[str],
    stock_ranking: pd.DataFrame,
) -> Mapping[str, object]:
    return {
        "as_of_date": as_of_date,
        "data_snapshot_id": inputs.data_snapshot_id,
        "status": status.value,
        "readiness": readiness.value,
        "market_regime": inputs.market_regime.value,
        "macro_context": inputs.macro_context,
        "strategy_versions": inputs.strategy_versions,
        "portfolio": {
            "total_value": inputs.portfolio.total_value,
            "cash": inputs.portfolio.cash,
            "unit_nav": inputs.portfolio.unit_nav,
            "high_water_mark": inputs.portfolio.high_water_mark,
            "net_external_flow": inputs.portfolio_valuation.net_external_flow,
            "previous_run_id": inputs.portfolio_valuation.previous_run_id,
            "positions_fingerprint": inputs.portfolio_valuation.positions_fingerprint,
            "events_fingerprint": inputs.portfolio_valuation.events_fingerprint,
            "is_baseline": inputs.portfolio_valuation.is_baseline,
            "valuation_valid": inputs.portfolio_valuation.valid,
        },
        "stock_ranking_rows": len(stock_ranking),
        "stock_ranking_sha256": _ranking_digest(stock_ranking),
        "signals": tuple(_signal_payload(value) for value in signals),
        "orders": tuple(_order_payload(value) for value in orders),
        "blockers": tuple(blockers),
    }


def _signal_payload(signal: Signal) -> Mapping[str, object]:
    return {
        "symbol": signal.symbol,
        "asset_type": signal.asset_type.value,
        "action": signal.action.value,
        "score": signal.score,
        "target_weight": signal.target_weight,
        "reasons": signal.reasons,
        "as_of_date": signal.as_of_date,
        "valid_until": signal.valid_until,
        "strategy_version": signal.strategy_version,
    }


def _order_payload(order: ProposedOrder) -> Mapping[str, object]:
    return {
        "symbol": order.symbol,
        "asset_type": order.asset_type.value,
        "action": order.action.value,
        "amount": order.amount,
        "quantity": order.quantity,
        "target_weight": order.target_weight,
        "reasons": order.reasons,
        "as_of_date": order.as_of_date,
        "valid_until": order.valid_until,
        "data_snapshot_id": order.data_snapshot_id,
        "strategy_version": order.strategy_version,
    }
