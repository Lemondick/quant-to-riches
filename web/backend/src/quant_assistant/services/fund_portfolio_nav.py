"""Explicit fund-only NAV posting; never runs a strategy or changes holdings."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.data.snapshots import SnapshotWriter
from quant_assistant.data.validation import validate_dataset
from quant_assistant.models.entities import DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.base import ProviderError
from quant_assistant.services.commands_common import require_decimal
from quant_assistant.services.decision_repository import DuckDBDecisionRepository


class FundPortfolioNavService:
    def __init__(self, database: Database, settings: Settings, provider: object, now):
        self.database = database
        self.settings = settings
        self.provider = provider
        self.now = now
        self.repository = DuckDBDecisionRepository(database)

    def run(self, as_of_date: date, *, confirmed: bool = False, preview_id: str | None = None):
        generated_at = self.now().astimezone(SHANGHAI_TZ)
        if as_of_date > generated_at.date():
            return self._blocked(as_of_date, ["不能使用未来日期净值"])
        with self.database.connection() as connection:
            existing = connection.execute(
                "SELECT run_id, as_of_date, generated_at, total_value, cash, unit_nav, "
                "high_water_mark, net_external_flow, is_baseline "
                "FROM portfolio_valuations WHERE as_of_date = ? "
                "ORDER BY generated_at DESC LIMIT 1",
                [as_of_date],
            ).fetchone()
            if existing:
                return {
                    "status": "existing",
                    "as_of_date": as_of_date,
                    "preview_id": None,
                    "point": self._point(existing),
                    "blockers": [],
                    "sources": [],
                }
            latest = connection.execute(
                "SELECT max(as_of_date) FROM portfolio_valuations"
            ).fetchone()[0]
            if latest is not None and as_of_date <= latest:
                return self._blocked(as_of_date, ["历史净值已入账，不能倒序追加或重写"])
            positions = connection.execute(
                "SELECT symbol, asset_type, quantity, as_of_date FROM positions ORDER BY symbol"
            ).fetchall()
            if not positions or any(row[3] > as_of_date for row in positions):
                return self._blocked(as_of_date, ["需先确认完整账户；入账日期不能早于当前持仓日期"])
            if any(row[1] not in {"fund", "cash"} for row in positions):
                return self._blocked(as_of_date, ["此入口只支持基金和现金账户"])
            symbols = [row[0] for row in positions if row[1] == "fund" and row[2] > 0]
            if not symbols or len(symbols) > 20:
                return self._blocked(as_of_date, ["需有1至20只已确认的基金持仓"])

        # Fetch outside the database lock so other pages remain usable.
        sources, blockers = self._sync(symbols, as_of_date)
        if blockers:
            return self._blocked(as_of_date, blockers, sources)
        with self.database.connection() as connection:
            generated_at = self.now().astimezone(SHANGHAI_TZ)
            # Another request may have posted while public data was loading.
            latest = connection.execute(
                "SELECT max(as_of_date) FROM portfolio_valuations"
            ).fetchone()[0]
            if latest is not None and as_of_date <= latest:
                return self._blocked(as_of_date, ["期间已保存新的净值，请刷新后查看历史记录"])
            inputs = self.repository.load_fund_valuation_inputs(as_of_date)
            draft = inputs.portfolio_valuation
            if inputs.blockers or not draft.valid:
                return self._blocked(as_of_date, list(inputs.blockers), sources)
            portfolio = inputs.portfolio
            token = hashlib.sha256(
                f"{as_of_date}|{draft.positions_fingerprint}|{draft.events_fingerprint}|"
                f"{draft.previous_run_id}|{portfolio.unit_nav}".encode()
            ).hexdigest()
            if confirmed and preview_id != token:
                return self._blocked(as_of_date, ["账户或净值已变更，请重新预览并核对"], sources)
            run_id = f"fund-nav-{token[:32]}"
            point = self._point(
                (
                    run_id,
                    as_of_date,
                    generated_at,
                    portfolio.total_value,
                    portfolio.cash,
                    portfolio.unit_nav,
                    portfolio.high_water_mark,
                    draft.net_external_flow,
                    draft.is_baseline,
                )
            )
            if not confirmed:
                return {
                    "status": "ready",
                    "as_of_date": as_of_date,
                    "preview_id": token,
                    "point": point,
                    "blockers": [],
                    "sources": sources,
                }
            with self.database.transaction() as transaction:
                transaction.execute(
                    """
                    INSERT INTO portfolio_valuations (
                        run_id, as_of_date, generated_at, data_snapshot_id, total_value,
                        cash, unit_nav, high_water_mark, net_external_flow, previous_run_id,
                        positions_fingerprint, events_fingerprint, is_baseline
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        as_of_date,
                        generated_at,
                        inputs.data_snapshot_id,
                        *[
                            require_decimal(value, "净值金额")
                            for value in (
                                portfolio.total_value,
                                portfolio.cash,
                                portfolio.unit_nav,
                                portfolio.high_water_mark,
                                draft.net_external_flow,
                            )
                        ],
                        draft.previous_run_id,
                        draft.positions_fingerprint,
                        draft.events_fingerprint,
                        draft.is_baseline,
                    ],
                )
            return {
                "status": "recorded",
                "as_of_date": as_of_date,
                "preview_id": token,
                "point": point,
                "blockers": [],
                "sources": sources,
            }

    def _sync(self, symbols: list[str], as_of_date: date):
        sources, blockers = [], []
        for symbol in symbols:
            try:
                history = self.provider.nav_history(symbol)
                observed_at = self.now().astimezone(SHANGHAI_TZ)
                points = [point for point in history if point.nav_date == as_of_date]
                if len(points) != 1:
                    blockers.append(
                        f"{symbol}尚无{as_of_date}的正式净值；不会用上一日或盘中估值替代"
                    )
                    continue
                point = points[0]
                if point.source not in {"eastmoney", "tencent"}:
                    blockers.append(f"{symbol}正式净值来源无法核对")
                    continue
                frame = pd.DataFrame(
                    [
                        {
                            "symbol": symbol,
                            "nav_date": as_of_date,
                            "unit_nav": float(point.unit_nav),
                            "accumulated_nav": None
                            if point.accumulated_nav is None
                            else float(point.accumulated_nav),
                        }
                    ]
                )
                validation = validate_dataset("fund_nav", frame, as_of_date)
                if validation.status is not DataStatus.VALID:
                    blockers.append(f"{symbol}正式净值数值验证未通过")
                    continue
                snapshot = SnapshotWriter(
                    self.settings.raw_snapshot_dir,
                    point.source,
                    clock=lambda stamp=observed_at: stamp,
                ).write("fund_nav", as_of_date, frame)
                snapshot_id = self.database.record_snapshot(snapshot, validation)
                with self.database.transaction() as connection:
                    old = connection.execute(
                        "SELECT unit_nav FROM fund_nav WHERE symbol = ? AND nav_date = ?",
                        [symbol, as_of_date],
                    ).fetchone()
                    if old is not None and Decimal(str(old[0])) != point.unit_nav:
                        blockers.append(f"{symbol}公开净值与已保存记录冲突，请核对来源；旧值未覆盖")
                        continue
                    connection.execute(
                        "INSERT INTO fund_nav VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT (symbol, nav_date) DO UPDATE SET "
                        "source_snapshot_id = excluded.source_snapshot_id",
                        [
                            symbol,
                            as_of_date,
                            float(point.unit_nav),
                            None if point.accumulated_nav is None else float(point.accumulated_nav),
                            snapshot_id,
                        ],
                    )
                sources.append(
                    {
                        "symbol": symbol,
                        "nav_date": as_of_date,
                        "unit_nav": format(point.unit_nav, "f"),
                        "source": point.source,
                        "source_url": f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js"
                        if point.source == "eastmoney"
                        else f"https://qt.gtimg.cn/q=jj{symbol}",
                        "observed_at": observed_at,
                    }
                )
            except (ProviderError, ConnectionError, OSError, ValueError, TypeError):
                blockers.append(f"{symbol}公开正式净值暂不可用，请稍后重新核对")
        return sources, blockers

    @staticmethod
    def _point(row):
        return dict(
            zip(
                (
                    "run_id",
                    "as_of_date",
                    "generated_at",
                    "total_value",
                    "cash",
                    "unit_nav",
                    "high_water_mark",
                    "net_external_flow",
                    "is_baseline",
                ),
                row,
                strict=True,
            )
        )

    @staticmethod
    def _blocked(as_of_date, blockers, sources=()):
        return {
            "status": "blocked",
            "as_of_date": as_of_date,
            "preview_id": None,
            "point": None,
            "blockers": blockers,
            "sources": list(sources),
        }
