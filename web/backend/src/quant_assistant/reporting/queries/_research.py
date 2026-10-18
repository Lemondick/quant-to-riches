"""Research evidence coverage and candidate-frame read projections."""

from __future__ import annotations

import pandas as pd

from quant_assistant.models.entities import AssetType, DataStatus
from quant_assistant.reporting.explanations import render_reasons

from ._base import _DecisionQueriesBase
from ._helpers import _reason_tuple, _required_run_id
from ._serializers import _empty_candidate_frame

__all__ = ["_ResearchMixin"]


class _ResearchMixin(_DecisionQueriesBase):
    """Research evidence summary and per-run candidate projections."""

    def research_evidence_status(self) -> dict[str, object]:
        """Summarize automatic evidence coverage for the system-status page."""

        valuation = self._query(
            """
            SELECT symbol, count(*) AS observations,
                   count(pe) AS pe_observations, count(pb) AS pb_observations,
                   max(as_of_date) AS last_date
            FROM fund_valuation_history
            GROUP BY symbol ORDER BY symbol
            """
        )
        manager = self._query(
            """
            SELECT symbol, count(*) AS observations,
                   count(manager_win_rate) AS win_rate_observations,
                   max(as_of_date) AS last_date
            FROM fund_manager_history
            GROUP BY symbol ORDER BY symbol
            """
        )
        valuation_ready = int(
            (
                (valuation.get("pe_observations", pd.Series(dtype=int)) >= 60)
                | (valuation.get("pb_observations", pd.Series(dtype=int)) >= 60)
            ).sum()
        )
        manager_ready = int((manager.get("win_rate_observations", pd.Series(dtype=int)) >= 1).sum())
        blockers: list[str] = []
        if valuation.empty:
            blockers.append("尚无PE/PB自动估值证据")
        elif valuation_ready == 0:
            blockers.append("PE/PB历史不足60个目标日前观测")
        if manager.empty:
            blockers.append("尚无基金经理自动档案证据")
        elif manager_ready < len(manager):
            blockers.append("部分基金经理档案缺少胜率证据")
        return {
            "status": DataStatus.VALID if not blockers else DataStatus.BLOCKED,
            "valuation": valuation,
            "manager": manager,
            "valuation_ready_symbols": valuation_ready,
            "manager_symbols_with_win_rate": manager_ready,
            "blockers": tuple(blockers),
        }

    def stock_candidates(self, run_id: str) -> pd.DataFrame:
        return self._candidates(run_id, AssetType.STOCK)

    def fund_candidates(self, run_id: str) -> pd.DataFrame:
        return self._candidates(run_id, AssetType.FUND)

    def _candidates(self, run_id: str, asset_type: AssetType) -> pd.DataFrame:
        normalized_id = _required_run_id(run_id)
        frame = self._query(
            """
            SELECT s.symbol, COALESCE(i.name, s.symbol) AS name,
                   s.asset_type, s.score, s.action, s.target_weight,
                   s.reasons_json, s.as_of_date, s.valid_until,
                   s.strategy_version, o.amount, o.quantity,
                   r.data_snapshot_id, r.data_status, r.readiness
            FROM signals AS s
            JOIN strategy_runs AS r ON r.run_id = s.run_id
            LEFT JOIN instruments AS i ON i.symbol = s.symbol
            LEFT JOIN proposed_orders AS o
              ON o.run_id = s.run_id AND o.symbol = s.symbol
            WHERE s.run_id = ? AND s.asset_type = ?
              AND (
                r.data_status = 'valid'
                OR s.action NOT IN ('buy', 'add')
              )
            ORDER BY s.score DESC, s.symbol
            """,
            [normalized_id, asset_type.value],
        )
        if frame.empty:
            return _empty_candidate_frame()
        reasons = frame["reasons_json"].map(_reason_tuple)
        result = frame.drop(columns=["reasons_json"]).copy(deep=True)
        result["reasons"] = reasons
        result["reason"] = reasons.map(render_reasons)
        return result
