"""Historical replay in disposable storage; the live account is never opened for writes."""

from datetime import datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from analysis_context import build_context

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import Action, AssetType
from quant_assistant.services.ledger_replay_engine import load_source, simulate
from quant_assistant.services.ledger_review_common import public
from quant_assistant.services.portfolio_commands import TransactionInput

REPLAY_CAPABILITIES = ("ledger.replay",)


def execute_replay(_kind, payload):
    database, _queries, _directory = build_context(payload["snapshot"])
    try:
        rows = payload.get("transactions", [payload.get("transaction")])
        if (
            not isinstance(rows, list)
            or not 1 <= len(rows) <= 1000
            or not all(isinstance(row, dict) for row in rows)
        ):
            raise ValueError("历史成交批次无效")
        transactions = [
            TransactionInput(
                row["symbol"],
                AssetType(row["asset_type"]),
                Action(row["action"]),
                Decimal(str(row["quantity"])),
                Decimal(str(row["price"])),
                Decimal(str(row["fees"])),
                datetime.fromisoformat(row["traded_at"]),
                row["external_id"],
                "historical-manual",
                row.get("cash_symbol"),
                row.get("note"),
            )
            for row in rows
        ]
        with database.connection() as connection:
            source = load_source(connection)
        try:
            with TemporaryDirectory(prefix="quant-native-replay-") as temporary:
                result = simulate(source, transactions, temporary, datetime.now(SHANGHAI_TZ))
        except (ValueError, RuntimeError) as error:
            return {
                "status": "blocked",
                "blockers": [
                    str(error)[:500]
                    if isinstance(error, ValueError) and str(error)
                    else "历史事件顺序或原始估值审计证据不完整，不能重建历史。"
                ],
            }
        return {"status": "ready", "blockers": [], **public(result)}
    finally:
        database.close()
