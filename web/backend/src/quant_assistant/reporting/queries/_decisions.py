"""Latest-decision, paper-order and open-alert read projections."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd

from quant_assistant.models.entities import (
    Action,
    DataStatus,
    MarketRegime,
    ReadinessStatus,
)
from quant_assistant.services.daily_decision import DailyDecisionResult

from ._base import _DecisionQueriesBase
from ._helpers import (
    _STATUS_PRIORITY,
    _data_status,
    _json_object,
    _optional_int,
    _reason_tuple,
    _required_date,
    _required_datetime,
    _required_run_id,
    _snapshot_blockers,
    _text_tuple,
    _validate_limit,
)
from ._serializers import _order_from_row, _signal_from_row

__all__ = ["_DecisionMixin"]

_RISK_INCREASING = frozenset({Action.BUY, Action.ADD})


class _DecisionMixin(_DecisionQueriesBase):
    """Per-day decision runs, paper orders and operational alerts."""

    def latest_decision(self) -> DailyDecisionResult | None:
        runs = self._query(
            """
            SELECT * FROM strategy_runs
            ORDER BY generated_at DESC, run_id DESC LIMIT 1
            """
        )
        if runs.empty:
            return None
        return self._decision_from_row(runs.iloc[0])

    def decision_by_id(self, run_id: str) -> DailyDecisionResult | None:
        """Return exactly ``run_id``; an unknown ID never falls back to latest."""

        normalized_id = _required_run_id(run_id)
        runs = self._query(
            "SELECT * FROM strategy_runs WHERE run_id = ?",
            [normalized_id],
        )
        if runs.empty:
            return None
        return self._decision_from_row(runs.iloc[0])

    def data_status(self) -> dict[str, object]:
        """Return the latest persisted decision status, or a fail-closed snapshot view."""

        runs = self._query(
            """
            SELECT * FROM strategy_runs
            ORDER BY generated_at DESC, run_id DESC LIMIT 1
            """
        )
        if not runs.empty:
            row = runs.iloc[0]
            decision = self._decision_from_row(row)
            parameters = _json_object(row.get("parameters_json"))
            versions = _text_tuple(parameters.get("strategy_versions"))
            if not versions:
                versions = tuple(item for item in str(row["strategy_version"]).split("+") if item)
            sessions = self._paper_session_count(versions, decision.as_of_date)
            return {
                "status": decision.status,
                "as_of_date": decision.as_of_date,
                "data_snapshot_id": decision.data_snapshot_id,
                "strategy_versions": versions,
                "paper_session_count": sessions,
                "blockers": decision.blockers,
                "logs": (f"已读取决策运行 {decision.run_id}",),
            }

        # A history bootstrap deliberately keeps failed/incomplete symbol snapshots
        # for audit.  They must not block the whole stock strategy when the symbol is
        # outside the current point-in-time eligible universe (for example, a recent
        # listing with fewer than 200 sessions).  Optional fund feeds are likewise not
        # required for the stock/index decision gate.
        snapshots = self._query(
            """
            WITH latest_date AS (
                SELECT max(as_of_date) AS as_of_date FROM data_snapshots
            ),
            eligible_history AS (
                SELECT 'stock_history_' || replace(symbol, '.', '_') AS dataset
                FROM instruments, latest_date
                WHERE asset_type = 'stock'
                  AND regexp_matches(
                      upper(symbol), '^(60[0135][0-9]{3}\\.SH|00[0123][0-9]{3}\\.SZ)$'
                  )
                  AND lower(coalesce(board, '')) IN ('main', 'main_board', '主板')
                  AND listed_on IS NOT NULL
                  AND listed_on <= latest_date.as_of_date - INTERVAL '300 days'
                  AND nullif(trim(coalesce(industry, '')), '') IS NOT NULL
            ),
            latest AS (
                SELECT *
                FROM data_snapshots
                WHERE as_of_date = (SELECT as_of_date FROM latest_date)
                QUALIFY row_number() OVER (
                    PARTITION BY dataset ORDER BY id DESC
                ) = 1
            )
            SELECT latest.*
            FROM latest
            WHERE dataset IN ('instruments', 'daily_prices')
               OR dataset LIKE 'index_history%'
               OR dataset IN (SELECT dataset FROM eligible_history)
            ORDER BY dataset
            """
        )
        if snapshots.empty:
            return {
                "status": DataStatus.BLOCKED,
                "as_of_date": None,
                "data_snapshot_id": None,
                "strategy_versions": (),
                "paper_session_count": 0,
                "blockers": ("尚无本地数据快照",),
                "logs": (),
            }
        statuses = tuple(_data_status(value) for value in snapshots["status"])
        blockers: list[str] = []
        for snapshot in snapshots.to_dict("records"):
            snapshot_status = _data_status(snapshot["status"])
            if snapshot_status is not DataStatus.VALID:
                blockers.append(f"{snapshot['dataset']}数据状态为{snapshot_status.value}")
            blockers.extend(_snapshot_blockers(snapshot.get("issues_json")))
        return {
            "status": max(statuses, key=_STATUS_PRIORITY.__getitem__),
            "as_of_date": _required_date(snapshots.iloc[0]["as_of_date"]),
            "data_snapshot_id": int(snapshots["id"].max()),
            "strategy_versions": (),
            "paper_session_count": 0,
            "blockers": tuple(dict.fromkeys(blockers)),
            "logs": ("尚无决策运行，已读取最新数据快照",),
        }

    def latest_backtest(self) -> dict[str, object] | None:
        rows = self._query(
            """
            SELECT * FROM backtest_runs
            WHERE completed_at IS NOT NULL
            ORDER BY completed_at DESC, run_id DESC LIMIT 1
            """
        )
        if rows.empty:
            return None
        row = rows.iloc[0]
        metrics = _json_object(row.get("metrics_json"))
        limitations = _reason_tuple(row.get("limitations_json"))
        readiness = str(row["readiness"])
        return {
            "run_id": str(row["run_id"]),
            "strategy_version": str(row["strategy_version"]),
            "completed_at": _required_datetime(row["completed_at"]),
            "status": str(row["status"]),
            "readiness": readiness,
            "research_only": readiness == ReadinessStatus.RESEARCH_ONLY.value,
            "metrics": dict(metrics),
            "limitations": limitations,
        }

    def paper_orders(self) -> pd.DataFrame:
        return self._query(
            """
            SELECT o.order_id, o.run_id, o.symbol, o.asset_type, o.action,
                   o.requested_amount, o.requested_quantity, o.status,
                   o.valid_until, o.note, o.updated_at,
                   count(f.fill_id) AS fill_count,
                   coalesce(sum(f.quantity), 0) AS filled_quantity,
                   coalesce(sum(f.quantity * f.price), 0) AS filled_amount,
                   coalesce(sum(f.fees), 0) AS fees
            FROM paper_orders AS o
            LEFT JOIN paper_fills AS f ON f.order_id = o.order_id
            GROUP BY ALL
            ORDER BY o.updated_at DESC, o.order_id
            """
        )

    def open_alerts(self) -> pd.DataFrame:
        return self._query(
            """
            SELECT id, alert_key, severity, message, first_seen_at,
                   last_seen_at, last_notified_at, silenced_until, details_json
            FROM operational_alerts WHERE status = 'open'
            ORDER BY CASE severity
                WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                last_seen_at DESC, id DESC
            """
        )

    def valuation_alerts(self, *, symbol: str | None = None, limit: int = 100) -> pd.DataFrame:
        """Read currently open fund valuation alerts with persisted quiet state."""

        _validate_limit(limit, "valuation alert limit", maximum=500)
        parameters: list[object] = []
        filters = ["status = 'open'", "alert_key LIKE 'fund-valuation-tail:%'"]
        if symbol is not None:
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol must be a non-empty string or None")
            filters.append("alert_key = ?")
            parameters.append(f"fund-valuation-tail:{symbol.strip().upper()}")
        parameters.append(limit)
        return self._query(
            f"""
            SELECT id, alert_key, severity, message, first_seen_at, last_seen_at,
                   last_notified_at, silenced_until, details_json
            FROM operational_alerts
            WHERE {" AND ".join(filters)}
            ORDER BY CASE severity
                WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                last_seen_at DESC, id DESC
            LIMIT ?
            """,
            parameters,
        )

    def _decision_from_row(self, row: pd.Series) -> DailyDecisionResult:
        run_id = str(row["run_id"])
        status = DataStatus(str(row["data_status"]))
        parameters = _json_object(row.get("parameters_json"))
        signals = tuple(
            _signal_from_row(item)
            for item in self._query(
                """
                SELECT * FROM signals WHERE run_id = ?
                ORDER BY symbol, strategy_version
                """,
                [run_id],
            ).to_dict("records")
        )
        orders = tuple(
            _order_from_row(item)
            for item in self._query(
                """
                SELECT * FROM proposed_orders WHERE run_id = ?
                ORDER BY symbol, strategy_version
                """,
                [run_id],
            ).to_dict("records")
        )
        if status is not DataStatus.VALID:
            signals = tuple(signal for signal in signals if signal.action not in _RISK_INCREASING)
            orders = tuple(order for order in orders if order.action not in _RISK_INCREASING)
        blockers = _reason_tuple(parameters.get("blockers"))
        if not blockers:
            blockers = _reason_tuple(row.get("error_message"))
        snapshot_id = _optional_int(row.get("data_snapshot_id")) or 0
        regime_value = parameters.get("market_regime", MarketRegime.RED.value)
        return DailyDecisionResult(
            run_id=run_id,
            as_of_date=_required_date(row["as_of_date"]),
            generated_at=_required_datetime(row["generated_at"]),
            status=status,
            readiness=ReadinessStatus(str(row["readiness"])),
            data_snapshot_id=snapshot_id,
            market_regime=MarketRegime(str(regime_value)),
            signals=signals,
            orders=orders,
            blockers=blockers,
        )

    def _paper_session_count(self, strategy_versions: Sequence[str], before_date: date) -> int:
        if not strategy_versions:
            return 0
        rows = self._query(
            """
            SELECT token AS strategy_version, count(DISTINCT as_of_date) AS session_count
            FROM strategy_runs,
                 UNNEST(string_split(strategy_version, '+')) AS versions(token)
            WHERE as_of_date < ? AND data_status = 'valid'
              AND readiness IN ('paper', 'reference') AND status = 'completed'
            GROUP BY token
            """,
            [before_date],
        )
        counts_by_version = {
            str(row["strategy_version"]): int(row["session_count"] or 0)
            for row in rows.to_dict("records")
        }
        counts = [counts_by_version.get(version, 0) for version in strategy_versions]
        return min(counts, default=0)
