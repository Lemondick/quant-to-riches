"""Row → domain serializers for reporting read projections."""

from __future__ import annotations

import pandas as pd

from quant_assistant.models.entities import Action, AssetType, ProposedOrder, Signal

from ._helpers import (
    _decimal,
    _is_missing,
    _reason_tuple,
    _required_date,
    _required_datetime,
)

__all__ = ["_signal_from_row", "_order_from_row", "_empty_candidate_frame"]


def _signal_from_row(row: pd.Series) -> Signal:
    return Signal(
        symbol=str(row["symbol"]),
        asset_type=AssetType(str(row["asset_type"])),
        action=Action(str(row["action"])),
        score=float(row["score"]),
        target_weight=_decimal(row["target_weight"]),
        reasons=_reason_tuple(row["reasons_json"]),
        as_of_date=_required_date(row["as_of_date"]),
        valid_until=_required_datetime(row["valid_until"]),
        strategy_version=str(row["strategy_version"]),
    )


def _order_from_row(row: pd.Series) -> ProposedOrder:
    quantity = None if _is_missing(row.get("quantity")) else _decimal(row["quantity"])
    return ProposedOrder(
        symbol=str(row["symbol"]),
        asset_type=AssetType(str(row["asset_type"])),
        action=Action(str(row["action"])),
        amount=_decimal(row["amount"]),
        quantity=quantity,
        target_weight=_decimal(row["target_weight"]),
        reasons=_reason_tuple(row["reasons_json"]),
        as_of_date=_required_date(row["as_of_date"]),
        valid_until=_required_datetime(row["valid_until"]),
        data_snapshot_id=int(row["data_snapshot_id"]),
        strategy_version=str(row["strategy_version"]),
    )


def _empty_candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "name",
            "asset_type",
            "score",
            "action",
            "target_weight",
            "as_of_date",
            "valid_until",
            "strategy_version",
            "amount",
            "quantity",
            "data_snapshot_id",
            "data_status",
            "readiness",
            "reasons",
            "reason",
        ]
    )
