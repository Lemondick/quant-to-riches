"""DuckDB repository for point-in-time daily decisions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType

import pandas as pd

from quant_assistant.models.entities import DataStatus, MarketRegime
from quant_assistant.models.repositories.database import Database
from quant_assistant.portfolio.models import PortfolioState
from quant_assistant.portfolio.nav_continuity import (
    _build_portfolio,
    _canonical_json,
    _has_complete_stock_history,
    _to_date,
)
from quant_assistant.services.decision_models import (
    DailyDecisionResult,
    DecisionInputs,
    PortfolioValuationDraft,
)
from quant_assistant.services.decision_shapes import (
    _fee_schedules as _fee_schedules,
)
from quant_assistant.services.decision_shapes import (
    _fund_status_frame as _fund_status_frame,
)
from quant_assistant.services.decision_shapes import (
    _json_default as _json_default,
)
from quant_assistant.services.decision_shapes import (
    _json_value as _json_value,
)
from quant_assistant.services.decision_shapes import (
    _ranking_records as _ranking_records,
)
from quant_assistant.services.decision_shapes import (
    _redemption_tier as _redemption_tier,
)
from quant_assistant.services.decision_shapes import (
    _share_class as _share_class,
)
from quant_assistant.services.decision_shapes import (
    _snapshot_issues as _snapshot_issues,
)
from quant_assistant.services.decision_shapes import (
    _split_versions as _split_versions,
)
from quant_assistant.services.decision_shapes import (
    _strategy_stock_symbols as _strategy_stock_symbols,
)
from quant_assistant.services.decision_shapes import (
    _unique_strings as _unique_strings,
)
from quant_assistant.services.decision_shapes import (
    _with_fund_instruments as _with_fund_instruments,
)
from quant_assistant.services.forward_sessions import valid_forward_sessions
from quant_assistant.strategies.funds import _DEFAULT_CONFIG as _DEFAULT_FUND_CONFIG
from quant_assistant.strategies.market_regime import classify_market_regime
from quant_assistant.strategies.stocks import _DEFAULT_CONFIG as _DEFAULT_STOCK_CONFIG

_STATUS_PRIORITY = {
    DataStatus.VALID: 0,
    DataStatus.STALE: 1,
    DataStatus.BLOCKED: 2,
}


class DuckDBDecisionRepository:
    """Load point-in-time decision inputs and save a decision transactionally."""

    REQUIRED_SNAPSHOTS = ("instruments", "daily_prices", "index_history")

    def __init__(self, database: Database | Path):
        self.database = database if isinstance(database, Database) else Database(Path(database))
        self.database.initialize()

    def load_decision_inputs(self, as_of_date: date) -> DecisionInputs:
        return self._load_inputs(as_of_date, portfolio_only=False)

    def load_fund_valuation_inputs(self, as_of_date: date) -> DecisionInputs:
        """Mark a fund-only ledger without changing strategy admission checks."""
        return self._load_inputs(as_of_date, portfolio_only=True)

    def _load_inputs(self, as_of_date: date, *, portfolio_only: bool) -> DecisionInputs:
        with self.database.connection() as connection:
            snapshots = connection.execute(
                """
                SELECT * EXCLUDE (revision_rank) FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY dataset ORDER BY id DESC
                    ) AS revision_rank
                    FROM data_snapshots WHERE as_of_date = ?
                ) WHERE revision_rank = 1 ORDER BY dataset
                """,
                [as_of_date],
            ).fetchdf()
            instruments = connection.execute(
                """
                SELECT * EXCLUDE (history_rank) FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY symbol ORDER BY as_of_date DESC, updated_at DESC
                    ) AS history_rank
                    FROM instrument_history WHERE as_of_date <= ?
                ) WHERE history_rank = 1 ORDER BY symbol
                """,
                [as_of_date],
            ).fetchdf()
            if instruments.empty:
                instruments = connection.execute(
                    "SELECT * FROM instruments WHERE as_of_date <= ? ORDER BY symbol",
                    [as_of_date],
                ).fetchdf()
            daily_prices = connection.execute(
                "SELECT * FROM daily_prices WHERE trade_date <= ? ORDER BY symbol, trade_date",
                [as_of_date],
            ).fetchdf()
            fund_nav = connection.execute(
                "SELECT * FROM fund_nav WHERE nav_date <= ? ORDER BY symbol, nav_date",
                [as_of_date],
            ).fetchdf()
            fund_pool = connection.execute(
                """
                SELECT * FROM fund_share_families
                WHERE as_of_date <= ? ORDER BY family_id, symbol
                """,
                [as_of_date],
            ).fetchdf()
            fee_rows = connection.execute(
                """
                SELECT * FROM fund_fee_schedules
                WHERE effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)
                ORDER BY symbol, effective_from, verified_on
                """,
                [as_of_date, as_of_date],
            ).fetchdf()
            position_rows = connection.execute(
                "SELECT * FROM positions WHERE as_of_date <= ? ORDER BY symbol",
                [as_of_date],
            ).fetchdf()
            previous_valuation = connection.execute(
                """
                SELECT run_id, as_of_date, total_value, unit_nav,
                       high_water_mark, generated_at
                FROM portfolio_valuations
                WHERE as_of_date < ?
                ORDER BY as_of_date DESC, generated_at DESC, run_id DESC
                LIMIT 1
                """,
                [as_of_date],
            ).fetchdf()
            baseline_event = connection.execute(
                """
                SELECT event_id, effective_date, recorded_at
                FROM portfolio_events
                WHERE event_type = 'baseline' AND status = 'confirmed'
                  AND effective_date = ?
                ORDER BY recorded_at DESC, event_id DESC
                LIMIT 1
                """,
                [as_of_date],
            ).fetchdf()
            previous_date = (
                None
                if previous_valuation.empty
                else _to_date(previous_valuation.iloc[0]["as_of_date"])
            )
            previous_generated_at = (
                None if previous_valuation.empty else previous_valuation.iloc[0]["generated_at"]
            )
            confirmed_flow_events = connection.execute(
                """
                SELECT event_id, event_type, effective_date, amount, recorded_at
                FROM portfolio_events
                WHERE status = 'confirmed'
                  AND event_type IN ('deposit', 'withdrawal', 'calibration')
                  AND effective_date > ? AND effective_date <= ?
                ORDER BY effective_date, recorded_at, event_id
                """,
                [previous_date or as_of_date, as_of_date],
            ).fetchdf()
            pending_events = connection.execute(
                """
                SELECT event_id, event_type, effective_date, amount, recorded_at
                FROM portfolio_events
                WHERE status = 'pending' AND effective_date <= ?
                ORDER BY effective_date, recorded_at, event_id
                """,
                [as_of_date],
            ).fetchdf()
            late_events = (
                pd.DataFrame()
                if previous_date is None or previous_generated_at is None
                else connection.execute(
                    """
                    SELECT event_id, event_type, effective_date, amount, recorded_at
                    FROM portfolio_events
                    WHERE status <> 'void' AND effective_date <= ? AND recorded_at > ?
                    ORDER BY effective_date, recorded_at, event_id
                    """,
                    [previous_date, previous_generated_at],
                ).fetchdf()
            )
            if previous_generated_at is None:
                manual_activity_row = connection.execute(
                    """
                    SELECT max(activity_at) FROM (
                        SELECT changed_at AS activity_at FROM audit_log
                        WHERE entity_type = 'position' AND changed_at < ?
                        UNION ALL
                        SELECT p.updated_at AS activity_at FROM positions AS p
                        WHERE p.updated_at < ?
                          AND NOT EXISTS (
                              SELECT 1 FROM transactions AS t
                              WHERE t.recorded_at = p.updated_at
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM portfolio_events AS e
                              WHERE e.recorded_at = p.updated_at
                                AND e.event_type IN (
                                    'deposit', 'withdrawal', 'calibration'
                                )
                          )
                    )
                    """,
                    [as_of_date + timedelta(days=1), as_of_date + timedelta(days=1)],
                ).fetchone()
            else:
                manual_activity_row = connection.execute(
                    """
                    SELECT max(activity_at) FROM (
                        SELECT changed_at AS activity_at FROM audit_log
                        WHERE entity_type = 'position'
                          AND changed_at > ? AND changed_at < ?
                        UNION ALL
                        SELECT p.updated_at AS activity_at FROM positions AS p
                        WHERE p.updated_at > ? AND p.updated_at < ?
                          AND NOT EXISTS (
                              SELECT 1 FROM transactions AS t
                              WHERE t.recorded_at = p.updated_at
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM portfolio_events AS e
                              WHERE e.recorded_at = p.updated_at
                                AND e.event_type IN (
                                    'deposit', 'withdrawal', 'calibration'
                                )
                          )
                    )
                    """,
                    [
                        previous_generated_at,
                        as_of_date + timedelta(days=1),
                        previous_generated_at,
                        as_of_date + timedelta(days=1),
                    ],
                ).fetchone()
            stock_rank_history = connection.execute(
                """
                SELECT symbol, as_of_date, rank, universe_size, rank_percentile, score
                FROM (
                    SELECT r.*, row_number() OVER (
                        PARTITION BY r.symbol
                        ORDER BY r.as_of_date DESC, s.generated_at DESC, r.run_id DESC
                    ) AS history_rank
                    FROM stock_rankings AS r
                    INNER JOIN strategy_runs AS s ON s.run_id = r.run_id
                    WHERE r.strategy_version = ? AND r.as_of_date < ?
                      AND date_trunc('week', r.as_of_date)
                          < date_trunc('week', CAST(? AS DATE))
                      AND s.data_status = 'valid' AND s.status = 'completed'
                )
                WHERE history_rank = 1
                ORDER BY symbol
                """,
                [_DEFAULT_STOCK_CONFIG.version, as_of_date, as_of_date],
            ).fetchdf()
            macro_rows = connection.execute(
                """
                SELECT indicator, observed_on, value, unit, source
                FROM macro_observations
                WHERE observed_on <= ?
                QUALIFY row_number() OVER (
                    PARTITION BY indicator ORDER BY observed_on DESC, id DESC
                ) = 1
                ORDER BY indicator
                """,
                [as_of_date],
            ).fetchdf()

            if portfolio_only:
                # Valuation uses current reconciled holdings, not a historical
                # reconstruction of positions that have since been overwritten.
                position_rows = connection.execute(
                    "SELECT * FROM positions ORDER BY symbol"
                ).fetchdf()
                manual_activity_row = connection.execute(
                    """
                    SELECT max(activity_at) FROM (
                        SELECT changed_at AS activity_at FROM audit_log
                        WHERE entity_type = 'position'
                        UNION ALL
                        SELECT p.updated_at FROM positions p
                        WHERE NOT EXISTS (
                            SELECT 1 FROM transactions t WHERE t.recorded_at = p.updated_at
                        ) AND NOT EXISTS (
                            SELECT 1 FROM portfolio_events e WHERE e.recorded_at = p.updated_at
                            AND e.event_type IN ('deposit', 'withdrawal', 'calibration')
                        )
                    )
                    """
                ).fetchone()

        if portfolio_only:
            status, blockers, snapshot_id = self._fund_valuation_gate(position_rows, fund_nav)
        else:
            status, blockers, snapshot_id = self._snapshot_gate(
                snapshots, require_funds=not fund_pool.empty
            )
        positions, portfolio, valuation, marks, position_blockers = _build_portfolio(
            position_rows,
            daily_prices,
            fund_nav,
            as_of_date,
            previous_valuation,
            baseline_event,
            confirmed_flow_events,
            pending_events,
            late_events,
            None if manual_activity_row is None else manual_activity_row[0],
        )
        if status is not DataStatus.VALID:
            valuation = replace(valuation, valid=False)
        blockers.extend(position_blockers)
        if portfolio_only:
            return DecisionInputs(
                as_of_date=as_of_date,
                data_snapshot_id=snapshot_id,
                status=DataStatus.BLOCKED if blockers else status,
                blockers=_unique_strings(blockers),
                daily_prices=daily_prices,
                stock_rank_history=stock_rank_history,
                instruments=instruments,
                fund_pool=pd.DataFrame(),
                fund_status=pd.DataFrame(),
                fee_schedules=MappingProxyType({}),
                positions=positions,
                portfolio=portfolio,
                portfolio_valuation=valuation,
                prices=MappingProxyType(marks),
                market_regime=MarketRegime.RED,
                strategy_versions=(),
            )
        strategy_versions: list[str] = []
        stock_instruments = instruments.loc[
            instruments.get("asset_type", pd.Series(dtype=str)).astype(str).eq("stock")
        ]
        if not stock_instruments.empty:
            strategy_versions.append(_DEFAULT_STOCK_CONFIG.version)
            required_stock_symbols = _strategy_stock_symbols(stock_instruments, as_of_date)
            if required_stock_symbols and not _has_complete_stock_history(
                daily_prices, stock_instruments, required_stock_symbols, as_of_date
            ):
                blockers.append("A股策略缺少至少200个已完成交易日的价格历史")
        index_history = daily_prices.loc[daily_prices["symbol"].astype(str).eq("000300.SH")]
        if len(index_history) < 200:
            blockers.append("沪深300风险基准缺少至少200个已完成交易日")
            market_regime = MarketRegime.RED
        else:
            market_regime = classify_market_regime(index_history, as_of_date)
        if not fund_pool.empty:
            strategy_versions.append(_DEFAULT_FUND_CONFIG.version)

        if blockers and status is DataStatus.VALID:
            status = DataStatus.BLOCKED
        fee_schedules, fee_blockers = _fee_schedules(fee_rows)
        blockers.extend(fee_blockers)
        fund_status = _fund_status_frame(fund_pool, fund_nav)
        instruments = _with_fund_instruments(instruments, fund_pool)
        if snapshot_id <= 0:
            status = DataStatus.BLOCKED

        return DecisionInputs(
            as_of_date=as_of_date,
            data_snapshot_id=snapshot_id,
            status=status,
            blockers=_unique_strings(blockers),
            daily_prices=daily_prices.copy(deep=False),
            stock_rank_history=stock_rank_history.copy(deep=False),
            instruments=instruments.copy(deep=False),
            fund_pool=fund_pool.copy(deep=False),
            fund_status=fund_status,
            fee_schedules=MappingProxyType(fee_schedules),
            positions=positions,
            portfolio=portfolio,
            portfolio_valuation=valuation,
            prices=MappingProxyType(marks),
            market_regime=market_regime,
            strategy_versions=tuple(sorted(set(strategy_versions))),
            macro_context={
                str(row["indicator"]): {
                    "observed_on": _to_date(row["observed_on"]),
                    "value": float(row["value"]),
                    "unit": str(row["unit"]),
                    "source": str(row["source"]),
                }
                for row in macro_rows.to_dict("records")
            },
        )

    def _fund_valuation_gate(
        self, positions: pd.DataFrame, fund_nav: pd.DataFrame
    ) -> tuple[DataStatus, list[str], int]:
        blockers: list[str] = []
        if positions.empty:
            return DataStatus.BLOCKED, ["请先确认完整的本地主账本账户快照"], 0
        asset_types = positions["asset_type"].astype(str)
        cash = positions.loc[asset_types.eq("cash")]
        if len(cash) != 1 or (cash["market_value"] < 0).any():
            blockers.append("需要一个已确认的非负现金持仓")
        if not asset_types.isin({"cash", "fund"}).all():
            blockers.append("此入口只支持基金和现金账户；混合账户请使用每日数据同步流程")
        if (positions["quantity"] < 0).any():
            blockers.append("持仓份额不能为负")
        funds = positions.loc[asset_types.eq("fund") & positions["quantity"].gt(0)]
        if funds.empty:
            blockers.append("本地主账本尚无已确认的基金持仓")
        snapshot_ids: list[int] = []
        with self.database.connection() as connection:
            for symbol in funds["symbol"].astype(str):
                rows = fund_nav.loc[fund_nav["symbol"].astype(str).eq(symbol)]
                if rows.empty or pd.isna(rows.iloc[-1]["source_snapshot_id"]):
                    blockers.append(f"{symbol}缺少有来源证据的正式净值")
                    continue
                row = rows.iloc[-1]
                snapshot_id = int(row["source_snapshot_id"])
                evidence = connection.execute(
                    "SELECT dataset, as_of_date, status, issues_json, source "
                    "FROM data_snapshots WHERE id = ?",
                    [snapshot_id],
                ).fetchone()
                if (
                    evidence is None
                    or evidence[0] not in {"fund_nav", "fund_daily"}
                    or evidence[1] != _to_date(row["nav_date"])
                    or evidence[2] != "valid"
                    or _snapshot_issues(evidence[3])
                    or not str(evidence[4] or "").strip()
                ):
                    blockers.append(f"{symbol}正式净值的来源验证未通过")
                else:
                    snapshot_ids.append(snapshot_id)
        return (
            DataStatus.BLOCKED if blockers else DataStatus.VALID,
            blockers,
            max(snapshot_ids, default=0),
        )

    def _snapshot_gate(
        self, snapshots: pd.DataFrame, *, require_funds: bool
    ) -> tuple[DataStatus, list[str], int]:
        required = list(self.REQUIRED_SNAPSHOTS)
        if require_funds:
            required.append("fund_daily")
        by_dataset = {str(row["dataset"]): row for row in snapshots.to_dict("records")}
        statuses: list[DataStatus] = []
        blockers: list[str] = []
        snapshot_ids: list[int] = []
        for dataset in required:
            row = by_dataset.get(dataset)
            if row is None:
                statuses.append(DataStatus.BLOCKED)
                blockers.append(f"缺少{dataset}在策略日期的验证快照")
                continue
            try:
                data_status = DataStatus(str(row["status"]))
            except ValueError:
                data_status = DataStatus.BLOCKED
            statuses.append(data_status)
            snapshot_ids.append(int(row["id"]))
            if data_status is not DataStatus.VALID:
                blockers.append(f"{dataset}数据状态为{data_status.value}")
            blockers.extend(_snapshot_issues(row.get("issues_json")))
        status = max(statuses, key=_STATUS_PRIORITY.__getitem__, default=DataStatus.BLOCKED)
        return status, blockers, max(snapshot_ids, default=0)

    def valid_paper_session_dates(
        self, strategy_version: str, before_date: date
    ) -> tuple[date, ...]:
        return tuple(
            row["as_of_date"]
            for row in valid_forward_sessions(self.database, before_date=before_date)
            if row["strategy_version"] == strategy_version
        )

    def blocking_backtest_limitations(self, strategy_versions: Sequence[str]) -> tuple[str, ...]:
        rows = self.database.query_df(
            """
            SELECT strategy_version, readiness, status, completed_at, limitations_json
            FROM backtest_runs ORDER BY started_at DESC, run_id DESC
            """
        )
        blockers: list[str] = []
        for version in strategy_versions:
            matching = rows.loc[rows["strategy_version"].astype(str).eq(version)]
            if matching.empty:
                blockers.append(f"{version}缺少已完成且非研究模式的回测")
                continue
            latest = matching.iloc[0]
            limitations = _json_value(latest.get("limitations_json"), default=[])
            limitation_values = (
                tuple(str(value) for value in limitations)
                if isinstance(limitations, list)
                else (str(limitations),)
                if limitations
                else ()
            )
            completed = str(latest["status"]) == "completed" and not pd.isna(
                latest.get("completed_at")
            )
            ready = str(latest["readiness"]) in {"paper", "reference"}
            blockers.extend(limitation_values)
            if not completed or not ready:
                blockers.append(f"{version}回测尚未通过研究模式门槛")
        return _unique_strings(blockers)

    def save_decision(
        self,
        result: DailyDecisionResult,
        strategy_versions: Sequence[str],
        portfolio: PortfolioState,
        portfolio_valuation: PortfolioValuationDraft,
        stock_ranking: pd.DataFrame,
    ) -> None:
        versions = tuple(sorted(set(strategy_versions)))
        version_text = "+".join(versions) if versions else "daily-decision-v1"
        ranking_rows = _ranking_records(stock_ranking)
        parameters = {
            "market_regime": result.market_regime.value,
            "blockers": result.blockers,
            "strategy_versions": versions,
            "strategy_fingerprint": result.strategy_fingerprint,
            "manual_confirmation_required": True,
            "portfolio": {
                "total_value": portfolio.total_value,
                "unit_nav": portfolio.unit_nav,
                "high_water_mark": portfolio.high_water_mark,
                "net_external_flow": portfolio_valuation.net_external_flow,
                "previous_run_id": portfolio_valuation.previous_run_id,
                "positions_fingerprint": portfolio_valuation.positions_fingerprint,
                "events_fingerprint": portfolio_valuation.events_fingerprint,
                "is_baseline": portfolio_valuation.is_baseline,
                "valuation_valid": portfolio_valuation.valid,
            },
        }
        with self.database.transaction() as connection:
            try:
                connection.execute("DELETE FROM signals WHERE run_id = ?", [result.run_id])
                connection.execute("DELETE FROM proposed_orders WHERE run_id = ?", [result.run_id])
                connection.execute("DELETE FROM stock_rankings WHERE run_id = ?", [result.run_id])
                connection.execute(
                    "DELETE FROM portfolio_valuations WHERE run_id = ?", [result.run_id]
                )
                connection.execute("DELETE FROM strategy_runs WHERE run_id = ?", [result.run_id])
                connection.execute(
                    """
                    INSERT INTO strategy_runs (
                        run_id, as_of_date, generated_at, data_snapshot_id,
                        strategy_version, parameters_json, data_status,
                        readiness, status, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)
                    """,
                    [
                        result.run_id,
                        result.as_of_date,
                        result.generated_at,
                        result.data_snapshot_id,
                        version_text,
                        _canonical_json(parameters),
                        result.status.value,
                        result.readiness.value,
                        None if not result.blockers else _canonical_json(result.blockers),
                    ],
                )
                for signal in result.signals:
                    connection.execute(
                        """
                        INSERT INTO signals (
                            run_id, symbol, asset_type, action, score, target_weight,
                            reasons_json, as_of_date, valid_until, strategy_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            result.run_id,
                            signal.symbol,
                            signal.asset_type.value,
                            signal.action.value,
                            signal.score,
                            signal.target_weight,
                            _canonical_json(signal.reasons),
                            signal.as_of_date,
                            signal.valid_until,
                            signal.strategy_version,
                        ],
                    )
                for order in result.orders:
                    connection.execute(
                        """
                        INSERT INTO proposed_orders (
                            run_id, symbol, asset_type, action, amount, quantity,
                            target_weight, reasons_json, as_of_date, valid_until,
                            data_snapshot_id, strategy_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            result.run_id,
                            order.symbol,
                            order.asset_type.value,
                            order.action.value,
                            order.amount,
                            order.quantity,
                            order.target_weight,
                            _canonical_json(order.reasons),
                            order.as_of_date,
                            order.valid_until,
                            order.data_snapshot_id,
                            order.strategy_version,
                        ],
                    )
                if portfolio_valuation.valid:
                    connection.execute(
                        """
                        INSERT INTO portfolio_valuations (
                            run_id, as_of_date, generated_at, data_snapshot_id,
                            total_value, cash, unit_nav, high_water_mark,
                            net_external_flow, previous_run_id,
                            positions_fingerprint, events_fingerprint, is_baseline
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            result.run_id,
                            result.as_of_date,
                            result.generated_at,
                            result.data_snapshot_id,
                            portfolio.total_value,
                            portfolio.cash,
                            portfolio.unit_nav,
                            portfolio.high_water_mark,
                            portfolio_valuation.net_external_flow,
                            portfolio_valuation.previous_run_id,
                            portfolio_valuation.positions_fingerprint,
                            portfolio_valuation.events_fingerprint,
                            portfolio_valuation.is_baseline,
                        ],
                    )
                if result.status is DataStatus.VALID:
                    stock_version = _DEFAULT_STOCK_CONFIG.version
                    for row in ranking_rows:
                        connection.execute(
                            """
                            INSERT INTO stock_rankings (
                                run_id, strategy_version, as_of_date, data_snapshot_id,
                                symbol, momentum_z, trend_z, low_volatility_z,
                                liquidity_z, score, rank, universe_size, rank_percentile
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            [
                                result.run_id,
                                stock_version,
                                result.as_of_date,
                                result.data_snapshot_id,
                                row["symbol"],
                                row["momentum_z"],
                                row["trend_z"],
                                row["low_volatility_z"],
                                row["liquidity_z"],
                                row["score"],
                                row["rank"],
                                row["universe_size"],
                                row["rank_percentile"],
                            ],
                        )
            except Exception:
                raise
