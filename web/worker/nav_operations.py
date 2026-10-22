"""Collect exact formal NAV values and immutable source files, without touching the ledger."""

import hashlib
import os
from datetime import date, datetime

import pandas as pd
from analysis_context import site_directory

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.data.snapshots import SnapshotWriter, _frame_sha256
from quant_assistant.data.validation import validate_dataset
from quant_assistant.models.entities import DataStatus
from quant_assistant.providers.fund_detail import PublicFundDetailProvider

NAV_CAPABILITIES = ("fund.nav_sources",)

NAV_SYMBOLS_DEFAULT_MAX = 50


def _nav_symbols_max():
    try:
        return max(
            1, int(os.environ.get("QUANT_WORKER_NAV_SYMBOLS_MAX", str(NAV_SYMBOLS_DEFAULT_MAX)))
        )
    except (TypeError, ValueError):
        return NAV_SYMBOLS_DEFAULT_MAX


def execute_nav(_kind, payload):
    directory = site_directory(payload["snapshot"])
    day = date.fromisoformat(payload["as_of_date"])
    symbols = payload["symbols"]
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(symbol, str) for symbol in symbols)
    ):
        raise ValueError("invalid_nav_symbols")
    limit = _nav_symbols_max()
    if len(symbols) > limit:
        raise ValueError(f"too_many_nav_symbols:{len(symbols)}>{limit}")
    root = directory / "native-raw"
    if root.resolve() != root:
        raise ValueError("snapshot_path_escape")
    result, blockers = [], []
    provider = PublicFundDetailProvider()
    histories = provider.nav_histories(symbols)
    for symbol in symbols:
        try:
            points = histories.get(symbol)
            if points is None:
                raise ValueError("formal_nav_unavailable")
            rows = [row for row in points if row.nav_date == day]
            if len(rows) != 1 or rows[0].source not in {"eastmoney", "tencent"}:
                raise ValueError("formal_nav_missing")
            point, now = rows[0], datetime.now(SHANGHAI_TZ)
            frame = pd.DataFrame(
                [
                    {
                        "symbol": symbol,
                        "nav_date": day,
                        "unit_nav": float(point.unit_nav),
                        "accumulated_nav": None
                        if point.accumulated_nav is None
                        else float(point.accumulated_nav),
                    }
                ]
            )
            if validate_dataset("fund_nav", frame, day).status is not DataStatus.VALID:
                raise ValueError("formal_nav_invalid")
            snapshot = SnapshotWriter(root, point.source, clock=lambda now=now: now).write(
                "fund_nav", day, frame
            )
            if _frame_sha256(pd.read_parquet(snapshot.path)) != snapshot.sha256:
                raise ValueError("formal_nav_snapshot_changed")
            with snapshot.path.open("rb") as stream:
                file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            result.append(
                {
                    "symbol": symbol,
                    "nav_date": day.isoformat(),
                    "unit_nav": str(point.unit_nav),
                    "source": point.source,
                    "source_url": f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js"
                    if point.source == "eastmoney"
                    else f"https://qt.gtimg.cn/q=jj{symbol}",
                    "observed_at": now.isoformat(),
                    "snapshot_path": str(snapshot.path),
                    "snapshot_sha256": snapshot.sha256,
                    "snapshot_file_sha256": file_hash,
                }
            )
        except Exception:
            blockers.append(f"{symbol}尚无{day}可核验的正式净值；不会使用上一日或盘中估值替代")
    return {"sources": result, "blockers": blockers}
