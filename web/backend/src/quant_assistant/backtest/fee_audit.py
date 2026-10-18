"""Load broker fee evidence without accepting ambiguous or incomplete files."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quant_assistant.backtest.production import BacktestCostAssumptions

_REQUIRED = (
    "broker_name",
    "evidence_id",
    "source_document",
    "verified_on",
    "commission_rate",
    "minimum_commission",
    "sell_stamp_tax_rate",
    "transfer_fee_rate",
    "slippage_bps",
)
_PLACEHOLDER_PREFIXES = ("replace_", "placeholder", "your_")


def load_fee_schedule(path: Path) -> BacktestCostAssumptions:
    """Read a user-provided fee evidence JSON and return immutable cost assumptions."""

    if not isinstance(path, Path):
        raise TypeError("fee schedule path must be Path")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"费率证据文件无法读取：{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("费率证据必须是JSON对象")
    missing = [key for key in _REQUIRED if not _text(payload.get(key))]
    if missing:
        raise ValueError("费率证据缺少字段：" + ",".join(missing))
    placeholders = [
        key
        for key in ("broker_name", "evidence_id", "source_document")
        if _text(payload.get(key)).lower().startswith(_PLACEHOLDER_PREFIXES)
    ]
    if placeholders:
        raise ValueError("费率证据仍包含占位字段：" + ",".join(placeholders))
    try:
        verified_on = date.fromisoformat(str(payload["verified_on"]))
    except ValueError as exc:
        raise ValueError("verified_on必须是YYYY-MM-DD") from exc
    return BacktestCostAssumptions(
        commission_rate=_decimal(payload["commission_rate"], "commission_rate"),
        minimum_commission=_decimal(payload["minimum_commission"], "minimum_commission"),
        sell_stamp_tax_rate=_decimal(payload["sell_stamp_tax_rate"], "sell_stamp_tax_rate"),
        transfer_fee_rate=_decimal(payload["transfer_fee_rate"], "transfer_fee_rate"),
        slippage_bps=_decimal(payload["slippage_bps"], "slippage_bps"),
        verified_on=verified_on,
        evidence_id=str(payload["evidence_id"]).strip(),
        source_document=str(payload["source_document"]).strip(),
        broker_name=str(payload["broker_name"]).strip(),
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field}必须是有限十进制数") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field}必须是非负有限十进制数")
    return parsed


__all__ = ["load_fee_schedule"]
