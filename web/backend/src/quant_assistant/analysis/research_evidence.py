"""Freeze server-calculated cross sections; never send a ledger to a model."""

import json
import math
import re
from datetime import timedelta
from uuid import uuid4

import pandas as pd

from quant_assistant.analysis.research_models import (
    ADAPTER_VERSION,
    ResearchError,
    canonical,
    fingerprint,
)
from quant_assistant.data.validation import validate_daily_prices

METRICS = {
    "ret_20": ("20日价格涨跌幅", "ratio"),
    "ret_60": ("60日价格涨跌幅", "ratio"),
    "ret_120": ("120日价格涨跌幅", "ratio"),
    "trend_120": ("相对120日均线偏离", "ratio"),
    "trend_200": ("相对200日均线偏离", "ratio"),
    "volatility_20": ("20日年化波动率", "ratio"),
    "amount_20": ("20日平均成交额", "CNY"),
    "score": ("截面综合分", "score"),
    "rank": ("截面名次", "rank"),
    "rank_percentile": ("截面排名比例", "ratio"),
}
WEIGHTS = {"momentum_z": 0.40, "trend_z": 0.25, "low_volatility_z": 0.20, "liquidity_z": 0.15}


def records(frame):
    # Normalize timestamps and nullable NumPy scalars without permitting NaN JSON.
    return json.loads(frame.to_json(orient="records", date_format="iso", double_precision=15))


class ResearchEvidence:
    def __init__(self, database, clock, manifest_path=None):
        self.database, self.clock, self.manifest_path = database, clock, manifest_path

    def source_records(self, prices):
        ids = sorted(int(x) for x in prices["source_snapshot_id"].dropna().unique())
        if not ids:
            return []
        return records(
            self.database.query_df(
                "SELECT id,dataset,source,as_of_date,sha256,status,issues_json FROM data_snapshots "
                f"WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY id",
                ids,
            )
        )

    def manifest(self):
        if self.manifest_path is None:
            return {}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def freeze(self, parameters, result, prices, metadata, ranked, sources, *, persist=True):
        day = pd.Timestamp(result["data_date"]).date()
        symbols = [item["symbol"] for item in result["items"]]
        source_map = {int(s["id"]): s for s in sources}
        declarations = self.manifest()
        blockers, facts = [], {}
        # Ranking depends on every eligible row, so verify that entire cross section.
        for row in ranked.to_dict("records"):
            symbol = str(row["symbol"])
            history = prices.loc[prices.symbol.eq(symbol)]
            codes = [i.code for i in validate_daily_prices("daily_prices", history, day)]
            if len(history) != 200:
                codes.append("history_requires_200_rows")
            for key in (*METRICS, *WEIGHTS):
                if not math.isfinite(float(row[key])):
                    codes.append("nonfinite_factor")
            if not str(row.get("name") or "").strip():
                codes.append("missing_stock_identity")
            if history.source_snapshot_id.isna().any():
                codes.append("missing_price_source")
            for source_id in history.source_snapshot_id.dropna().unique():
                source = source_map.get(int(source_id))
                if (
                    not source
                    or source["status"] != "valid"
                    or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
                    or not re.fullmatch(
                        r"daily_prices|point_in_time_prices|stock_history(?:_\d{6}_(?:SH|SZ))?",
                        source["dataset"],
                    )
                ):
                    codes.append("invalid_price_source")
                    continue
                declaration = declarations.get(source["sha256"], {})
                if (
                    declaration.get("price_basis") != "unadjusted"
                    or declaration.get("amount_unit") != "CNY"
                    or not declaration.get("evidence")
                ):
                    codes.append("price_basis_or_unit_unconfirmed")
            blockers.extend(f"{symbol}:{code}" for code in sorted(set(codes)))
            if symbol in symbols:
                facts[symbol] = (
                    [
                        {"fact_id": key, "label": label, "value": float(row[key]), "unit": unit}
                        for key, (label, unit) in METRICS.items()
                    ]
                    + [
                        {
                            "fact_id": key + "_contribution",
                            "label": key + " 加权贡献",
                            "value": float(row[key]) * weight,
                            "unit": "score",
                        }
                        for key, weight in WEIGHTS.items()
                    ]
                    + [
                        {
                            "fact_id": "universe_size",
                            "label": "参与排名股票数",
                            "value": len(ranked),
                            "unit": "count",
                        }
                    ]
                )
        if not symbols:
            blockers.append("no_candidates")
        payload = {
            "adapter_version": ADAPTER_VERSION,
            "parameters": parameters.model_dump(),
            "strategy_version": result["strategy_version"],
            "data_date": result["data_date"],
            "candidates": symbols,
            "ranking": records(ranked),
            "metadata": records(
                metadata.drop(columns=["updated_at"], errors="ignore").sort_values("symbol")
            ),
            "candidate_prices": records(prices.loc[prices.symbol.isin(symbols)]),
            "sources": sources,
            "source_declarations": {
                s["sha256"]: declarations.get(s["sha256"], {}) for s in sources
            },
            "facts": facts,
            "blockers": blockers,
            "warnings": ["metadata_provenance_partial", "unadjusted_price_not_total_return"],
        }
        digest = fingerprint(payload)
        if not persist:
            return digest
        snapshot_id, now = uuid4().hex, self.clock()
        expires = now + timedelta(hours=24)
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT INTO agent_preview_snapshots VALUES (?,?,?,?,?)",
                [
                    snapshot_id,
                    digest,
                    canonical(payload),
                    now,
                    expires,
                ],
            )
        return {
            "preview_snapshot_id": snapshot_id,
            "input_fingerprint": digest,
            "research_eligible": not blockers,
            "research_blockers": blockers,
            "research_warnings": payload["warnings"],
            "snapshot_expires_at": expires.isoformat(),
        }

    def get(self, snapshot_id):
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM agent_preview_snapshots WHERE snapshot_id=?", [snapshot_id]
            ).fetchone()
        if row is None:
            raise ResearchError("preview_not_found", 404)
        return {
            "snapshot_id": row[0],
            "content_hash": row[1],
            "payload": json.loads(row[2]),
            "created_at": row[3],
            "expires_at": row[4],
        }
