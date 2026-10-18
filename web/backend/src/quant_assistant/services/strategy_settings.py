"""Persist custom stock research parameters without granting production qualification."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.features.technical import calculate_stock_features
from quant_assistant.strategies.stocks import StockStrategyConfig, rank_stocks


class StrategyParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)

    top_candidate_count: int = Field(default=10, ge=1, le=30, strict=True)
    max_names: int = Field(default=5, ge=1, le=10, strict=True)
    target_weight: float = Field(default=0.06, gt=0, le=0.20)
    exit_percentile: float = Field(default=0.20, gt=0, le=1)
    stop_loss_pct: float | None = Field(default=None, gt=0, le=0.30)
    min_amount_20: float = Field(default=50_000_000, ge=10_000_000, le=10_000_000_000)

    @model_validator(mode="after")
    def check_budget(self):
        if self.max_names > self.top_candidate_count:
            raise ValueError("持股数量不能超过候选数量")
        if Decimal(str(self.target_weight)) * self.max_names > Decimal("0.80"):
            raise ValueError("股票研究总仓位不能超过80%")
        return self

    def stock_config(self) -> StockStrategyConfig:
        digest = hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]
        return StockStrategyConfig(
            version=f"stock-custom-{digest}-research-v1",
            **{
                **self.model_dump(),
                "target_weight": Decimal(str(self.target_weight)),
                "stop_loss_pct": None
                if self.stop_loss_pct is None
                else Decimal(str(self.stop_loss_pct)),
            },
        )


class StrategySettingsService:
    def __init__(self, path: Path, database, clock=None, research_evidence=None):
        self.path = path
        self.database = database
        self.clock = clock or (lambda: datetime.now(SHANGHAI_TZ))
        self.lock = RLock()
        self.research_evidence = research_evidence

    def load(self) -> dict:
        with self.lock:
            if not self.path.exists():
                return self._view(StrategyParameters(), None)
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return self._view(
                StrategyParameters.model_validate(payload["parameters"]), payload["updated_at"]
            )

    def save(self, parameters: StrategyParameters) -> dict:
        with self.lock:
            payload = self._view(parameters, self.clock().isoformat())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
            )
            os.replace(temporary, self.path)
            return payload

    @staticmethod
    def _view(parameters: StrategyParameters, updated_at) -> dict:
        config = parameters.stock_config()
        return {
            "parameters": parameters.model_dump(),
            "updated_at": updated_at,
            "research_only": True,
            "strategy_version": config.version,
            "stock_budget": float(config.target_weight * config.max_names),
            "config": json.loads(json.dumps(asdict(config), default=str)),
        }

    def preview(self, parameters: StrategyParameters) -> dict:
        result, inputs = self._calculate_preview(parameters)
        result.update(research_eligible=False, research_blockers=["research_disabled"])
        if self.research_evidence is not None:
            result["research_blockers"] = ["preview_blocked"]
            if inputs is not None:
                try:
                    result.update(self.research_evidence.freeze(parameters, result, *inputs))
                except Exception:
                    # Research failure must not break the existing candidate preview.
                    result["research_blockers"] = ["evidence_freeze_failed"]
        return result

    def research_fingerprint(self, parameters):
        result, inputs = self._calculate_preview(parameters)
        if inputs is None or self.research_evidence is None:
            return None
        return self.research_evidence.freeze(parameters, result, *inputs, persist=False)

    def _calculate_preview(self, parameters):
        result = {**self._view(parameters, None), "items": [], "status": "blocked", "reason": ""}
        target = self.clock().date()
        boundary = self.database.transaction() if self.research_evidence else nullcontext()
        with boundary:
            latest = self.database.query_df(
                "SELECT max(trade_date) AS day FROM daily_prices WHERE trade_date <= ?", [target]
            )["day"].iloc[0]
            if pd.isna(latest):
                return {
                    **result,
                    "reason": "没有本地股票行情，请先同步数据。",
                    "data_date": None,
                }, None
            day = latest.date()
            result["data_date"] = day.isoformat()
            if (target - day).days > 7:
                return {**result, "reason": "本地行情超过7天，已阻止使用旧行情筛选当前候选。"}, None
            prices = self.database.query_df(
                """SELECT * EXCLUDE (rn) FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY symbol ORDER BY trade_date DESC
                    ) AS rn FROM daily_prices WHERE trade_date <= ?
                ) WHERE rn <= 200 ORDER BY symbol, trade_date""",
                [day],
            )
            metadata = self.database.query_df(
                """SELECT * FROM instrument_history WHERE as_of_date <= ?
                   QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY as_of_date DESC) = 1""",
                [day],
            )
            sources = (
                self.research_evidence.source_records(prices) if self.research_evidence else []
            )
        if metadata.empty:
            return {**result, "reason": "缺少目标日股票池，无法计算候选。"}, None
        features = calculate_stock_features(prices, day)
        features = features.loc[pd.to_datetime(features["trade_date"]).dt.date.eq(day)]
        ranked = rank_stocks(features, metadata, day, parameters.stock_config())
        result["ranked_universe_size"] = len(ranked)
        names = {
            str(row["symbol"]): row["name"]
            for row in metadata.to_dict("records")
            if isinstance(row.get("name"), str) and row["name"].strip()
        }
        result["items"] = [
            {
                "symbol": str(row.symbol),
                "name": names.get(str(row.symbol)),
                "score": float(row.score),
                "rank": int(row.rank),
            }
            for row in ranked.head(parameters.top_candidate_count).itertuples()
        ]
        result["status"] = "research_only"
        result["reason"] = "使用自定义参数筛选，仅供研究；未通过回测前不替换正式策略。"
        return result, (prices, metadata, ranked, sources)
