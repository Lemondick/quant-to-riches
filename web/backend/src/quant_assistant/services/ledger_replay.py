"""Expiring historical trade previews and atomic, auditable ledger revisions."""

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory
from uuid import uuid4

from quant_assistant.models.entities import Action, AssetType
from quant_assistant.services.commands_common import require_text
from quant_assistant.services.ledger_replay_engine import load_source, simulate
from quant_assistant.services.ledger_review_common import audit, encoded, fingerprint, public, rows
from quant_assistant.services.portfolio_commands import TransactionInput
from quant_assistant.services.transactions_commands import _validate_transaction_input


class LedgerReplayService:
    def __init__(self, database, now):
        self.database, self.now = database, now
        with database.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ledger_replay_previews("
                "preview_id VARCHAR PRIMARY KEY,source_fingerprint VARCHAR NOT NULL,"
                "plan_json JSON NOT NULL,result_json JSON NOT NULL,created_at TIMESTAMPTZ NOT NULL,"
                "expires_at TIMESTAMPTZ NOT NULL,"
                "consumed_at TIMESTAMPTZ,transaction_id BIGINT)"
            )

    def preview(self, transaction, *, reason):
        reason = require_text(reason, "补录原因及核对依据", limit=1000)
        validated = _validate_transaction_input(transaction)
        if validated.source != "historical-manual":
            raise ValueError("历史补录必须使用独立来源historical-manual")
        transaction = TransactionInput(
            validated.symbol,
            validated.asset_type,
            validated.action,
            validated.quantity,
            validated.price,
            validated.fees,
            validated.traded_at,
            validated.external_id,
            validated.source,
            validated.cash_symbol,
            validated.note,
        )
        now = self.now()
        with self.database.connection() as conn:
            if conn.execute(
                "SELECT 1 FROM transactions WHERE source=? AND external_id=?",
                [transaction.source, transaction.external_id],
            ).fetchone():
                raise ValueError("这个补录编号已存在，请查看历史记录")
            source = load_source(conn)
        try:
            with TemporaryDirectory(prefix="quant-ledger-preview-") as temporary:
                result = simulate(source, transaction, temporary, now)
        except (ValueError, RuntimeError) as error:
            return {"status": "blocked", "blockers": [str(error)], "preview_id": None}
        identity = uuid4().hex
        plan = {"transaction": asdict(transaction), "reason": reason}
        payload = {
            "status": "ready",
            "preview_id": identity,
            "expires_at": now + timedelta(minutes=15),
            "positions": result["positions"],
            "valuations": result["valuations"],
            "before_positions": source["positions"],
            "before_valuations": source["portfolio_valuations"],
            "blockers": [],
            "reason": reason,
            "limitations": [
                "原始成交和报告保留；回算会追加新成交及净值修订审计。",
                "持仓变化后需重新核对基金批次和费率；历史回算不计入前向模拟天数。",
            ],
        }
        with self.database.transaction() as conn:
            current = load_source(conn)
            if fingerprint(current) != fingerprint(source):
                return {
                    "status": "blocked",
                    "blockers": ["账本或行情已变化，请重新预览"],
                    "preview_id": None,
                }
            conn.execute(
                "INSERT INTO ledger_replay_previews VALUES(?,?,?,?,?,?,NULL,NULL)",
                [
                    identity,
                    fingerprint(source),
                    encoded(plan),
                    encoded({**payload, "transaction_audits": result["transaction_audits"]}),
                    now,
                    now + timedelta(minutes=15),
                ],
            )
        return public(payload)

    def confirm(self, identity):
        with self.database.transaction() as conn:
            preview = conn.execute(
                "SELECT source_fingerprint,plan_json,result_json,expires_at,"
                "consumed_at,transaction_id "
                "FROM ledger_replay_previews WHERE preview_id=?",
                [identity],
            ).fetchone()
            if not preview:
                raise ValueError("补录预览不存在或不属于当前账号")
            if preview[4] is not None:
                return {"status": "recorded", "transaction_id": preview[5], "preview_id": identity}
            if self.now() >= preview[3]:
                raise ValueError("补录预览已过期，请重新核对")
            source = load_source(conn)
            if fingerprint(source) != preview[0]:
                raise ValueError("账户、账本或估值证据已变化，请重新预览")
            plan, result = json.loads(preview[1]), json.loads(preview[2])
            row = plan["transaction"]
            transaction = TransactionInput(
                row["symbol"],
                AssetType(row["asset_type"]),
                Action(row["action"]),
                Decimal(row["quantity"]),
                Decimal(row["price"]),
                Decimal(row["fees"]),
                datetime.fromisoformat(row["traded_at"]),
                row["external_id"],
                row["source"],
                row["cash_symbol"],
                row["note"],
            )
            # Recheck the evidence files and all event semantics, not only the stored preview JSON.
            with TemporaryDirectory(prefix="quant-ledger-confirm-") as temporary:
                rebuilt = simulate(source, transaction, temporary, self.now())
            for key in ("positions", "valuations"):
                earlier = result[key]
                current = public(rebuilt[key])
                for items in (earlier, current):
                    for item in items:
                        item.pop("updated_at", None)
                        item.pop("generated_at", None)
                if earlier != current:
                    raise ValueError("重算结果与预览不一致，请重新核对")
            validated = _validate_transaction_input(transaction)
            cash = next(row for row in rebuilt["positions"] if row["asset_type"] == "cash")
            transaction_id = conn.execute(
                "INSERT INTO transactions(symbol,asset_type,action,quantity,price,fees,"
                "traded_at,recorded_at,note,source,external_id,gross_amount,"
                "cash_delta,cash_symbol) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
                [
                    validated.symbol,
                    validated.asset_type.value,
                    validated.action.value,
                    validated.quantity,
                    validated.price,
                    validated.fees,
                    validated.traded_at,
                    self.now(),
                    validated.note,
                    validated.source,
                    validated.external_id,
                    validated.gross_amount,
                    validated.cash_delta,
                    cash["symbol"],
                ],
            ).fetchone()[0]
            conn.execute("DELETE FROM positions")
            for position in rebuilt["positions"]:
                names = list(position)
                conn.execute(
                    f"INSERT INTO positions({','.join(names)}) "
                    f"VALUES({','.join('?' for _ in names)})",
                    [position[name] for name in names],
                )
            for valuation in rebuilt["valuations"]:
                names = [name for name in valuation if name != "run_id"]
                conn.execute(
                    f"UPDATE portfolio_valuations SET {','.join(f'{name}=?' for name in names)} "
                    "WHERE run_id=?",
                    [valuation[name] for name in names] + [valuation["run_id"]],
                )
            for revised in rebuilt["transaction_audits"]:
                target = conn.execute(
                    "SELECT id FROM transactions WHERE source=? AND external_id=?",
                    [revised["source"], revised["external_id"]],
                ).fetchone()[0]
                audit(
                    conn,
                    "transaction" if target == transaction_id else "transaction_replay",
                    target,
                    self.now(),
                    json.loads(revised["before_json"]) if revised["before_json"] else None,
                    json.loads(revised["after_json"]),
                )
            audit(
                conn,
                "ledger_replay",
                identity,
                self.now(),
                {"positions": source["positions"], "valuations": source["portfolio_valuations"]},
                {
                    "transaction_id": transaction_id,
                    "effective_date": transaction.traded_at.date(),
                    "reason": plan["reason"],
                    "positions": rebuilt["positions"],
                    "valuations": rebuilt["valuations"],
                },
            )
            conn.execute(
                "UPDATE ledger_replay_previews SET consumed_at=?,transaction_id=? "
                "WHERE preview_id=?",
                [self.now(), transaction_id, identity],
            )
            return {"status": "recorded", "transaction_id": transaction_id, "preview_id": identity}

    def history(self, page=1, limit=20):
        if not 1 <= page <= 10000 or not 1 <= limit <= 100:
            raise ValueError("分页参数无效")
        with self.database.connection() as conn:
            return public(
                {
                    "items": rows(
                        conn,
                        "SELECT preview_id,created_at,consumed_at,transaction_id,plan_json "
                        "FROM ledger_replay_previews WHERE consumed_at IS NOT NULL "
                        "ORDER BY consumed_at DESC,preview_id "
                        "LIMIT ? OFFSET ?",
                        [limit, (page - 1) * limit],
                    ),
                    "total": conn.execute(
                        "SELECT count(*) FROM ledger_replay_previews WHERE consumed_at IS NOT NULL"
                    ).fetchone()[0],
                }
            )
