"""Persist explicit platform-to-ledger comparisons without changing financial balances."""

from decimal import Decimal
from uuid import uuid4

from quant_assistant.services.commands_common import require_decimal, require_text
from quant_assistant.services.ledger_review_common import audit, encoded, fingerprint, public, rows


class ReconciliationService:
    def __init__(self, database, now):
        self.database, self.now = database, now
        with database.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ledger_reconciliations("
                "reconciliation_id VARCHAR PRIMARY KEY,created_at TIMESTAMPTZ NOT NULL,"
                "source_fingerprint VARCHAR NOT NULL,result_json JSON NOT NULL,"
                "resolution VARCHAR,resolution_note VARCHAR,resolved_at TIMESTAMPTZ)"
            )

    def accounts(self):
        with self.database.connection() as conn:
            return public(
                {
                    "items": rows(
                        conn,
                        "SELECT account_id,name,platform,currency,status "
                        "FROM portfolio_accounts WHERE status='active' ORDER BY account_id",
                    )
                }
            )

    @staticmethod
    def _source(conn, account_ids):
        placeholders = ",".join("?" for _ in account_ids)
        local = rows(conn, "SELECT * FROM positions ORDER BY symbol")
        accounts = rows(
            conn,
            f"SELECT * FROM portfolio_accounts WHERE account_id IN ({placeholders}) "
            "ORDER BY account_id",
            account_ids,
        )
        snapshots = rows(
            conn,
            f"SELECT * FROM account_positions WHERE account_id IN ({placeholders}) "
            "ORDER BY account_id,symbol",
            account_ids,
        )
        fees = rows(
            conn,
            "SELECT source,external_id,fees,traded_at,reversed_by_correction_id "
            "FROM transactions ORDER BY id",
        )
        dividends = rows(
            conn, "SELECT fees,paid_at,reversed_by_correction_id FROM dividend_events ORDER BY id"
        )
        actions = rows(conn, "SELECT fees,effective_at FROM corporate_action_events ORDER BY id")
        return {
            "local": local,
            "accounts": accounts,
            "snapshots": snapshots,
            "fees": fees,
            "dividends": dividends,
            "actions": actions,
        }

    def compare(
        self,
        account_ids,
        *,
        complete_scope=False,
        expected_fees=None,
        start=None,
        end=None,
        evidence="",
    ):
        if not isinstance(account_ids, list) or not 1 <= len(account_ids) <= 30:
            raise ValueError("请选择1至30个已保存平台账户")
        if len(set(account_ids)) != len(account_ids) or any(
            not isinstance(x, str) for x in account_ids
        ):
            raise ValueError("平台账户编号无效或重复")
        if start and end and start > end:
            raise ValueError("开始日期不能晚于结束日期")
        if expected_fees is not None:
            expected_fees = require_decimal(expected_fees, "平台费用合计")
            if expected_fees < 0 or not start or not end:
                raise ValueError("核对费用需要非负金额及完整日期范围")
            evidence = require_text(evidence, "费用核对依据", limit=500)
        with self.database.transaction() as conn:
            source = self._source(conn, account_ids)
            if len(source["accounts"]) != len(account_ids):
                raise ValueError("所选平台账户不存在")
            if any(
                item["status"] != "active" or item["currency"] != "CNY"
                for item in source["accounts"]
            ):
                raise ValueError("只可对比启用的人民币账户；其他币种需要独立核对")
            blockers = [] if complete_scope else ["scope_unconfirmed"]
            snapshots = source["snapshots"]
            if not snapshots or any(
                not any(row["account_id"] == account_id for row in snapshots)
                for account_id in account_ids
            ):
                blockers.append("snapshot_missing")
            dates = {row["as_of_date"] for row in snapshots}
            local_dates = {row["as_of_date"] for row in source["local"]}
            if len(dates) != 1 or local_dates != dates:
                blockers.append("snapshot_dates_differ")
            local = {row["symbol"]: row for row in source["local"]}
            aggregated = {}
            for item in snapshots:
                row = aggregated.setdefault(
                    item["symbol"],
                    {
                        "quantity": Decimal(0),
                        "market_value": Decimal(0),
                        "cost_amount": Decimal(0),
                        "known_cost": True,
                        "asset_type": item["asset_type"],
                    },
                )
                if row["asset_type"] != item["asset_type"]:
                    raise ValueError("相同代码资产类型冲突，请核对原始快照")
                row["quantity"] += item["quantity"]
                row["market_value"] += item["market_value"]
                row["cost_amount"] += item["quantity"] * item["average_cost"]
                row["known_cost"] &= item["average_cost"] > 0
            items = []
            for symbol in sorted(set(local) | set(aggregated)):
                left, right = local.get(symbol), aggregated.get(symbol)
                if left and right and left["asset_type"] != right["asset_type"]:
                    raise ValueError("主账本和平台快照的相同代码资产类型冲突")
                known = complete_scope and bool(snapshots)
                quantity_left = left["quantity"] if left else Decimal(0) if known else None
                quantity_right = right["quantity"] if right else Decimal(0) if known else None
                delta = (
                    quantity_right - quantity_left
                    if quantity_left is not None and quantity_right is not None
                    else None
                )
                local_value = left["market_value"] if left else Decimal(0) if known else None
                platform_value = right["market_value"] if right else Decimal(0) if known else None
                cost_left = (
                    left["quantity"] * left["average_cost"]
                    if left and left["average_cost"] > 0
                    else Decimal(0)
                    if known and (not left or left["quantity"] == 0)
                    else None
                )
                cost_right = (
                    right["cost_amount"]
                    if right and right["known_cost"]
                    else Decimal(0)
                    if known and (not right or right["quantity"] == 0)
                    else None
                )
                items.append(
                    {
                        "symbol": symbol,
                        "asset_type": (left or right)["asset_type"],
                        "ledger_quantity": quantity_left,
                        "platform_quantity": quantity_right,
                        "quantity_delta": delta,
                        "ledger_value": local_value,
                        "platform_value": platform_value,
                        "value_delta": platform_value - local_value
                        if platform_value is not None and local_value is not None and not blockers
                        else None,
                        "ledger_cost": cost_left,
                        "platform_cost": cost_right,
                        "cost_delta": cost_right - cost_left
                        if cost_right is not None and cost_left is not None
                        else None,
                    }
                )
            if any(row["ledger_cost"] is None or row["platform_cost"] is None for row in items):
                blockers.append("cost_evidence_missing")
            actual_fees = Decimal(0)
            if start and end:
                for name, timestamp in (
                    ("fees", "traded_at"),
                    ("dividends", "paid_at"),
                    ("actions", "effective_at"),
                ):
                    actual_fees += sum(
                        (
                            row["fees"]
                            for row in source[name]
                            if row.get("reversed_by_correction_id") is None
                            and start <= row[timestamp].date() <= end
                        ),
                        Decimal(0),
                    )
            fees = {
                "start": start,
                "end": end,
                "ledger_total": actual_fees if start and end else None,
                "platform_total": expected_fees,
                "difference": expected_fees - actual_fees if expected_fees is not None else None,
                "evidence": evidence,
                "status": "unknown"
                if expected_fees is None
                else "matched"
                if expected_fees == actual_fees
                else "different",
            }
            if fees["status"] == "unknown":
                blockers.append("fee_evidence_missing")
            differing = any(
                row["quantity_delta"] not in (None, Decimal(0))
                or row["cost_delta"] is not None
                and abs(row["cost_delta"]) > Decimal("0.01")
                or row["value_delta"] is not None
                and abs(row["value_delta"]) > Decimal("0.01")
                for row in items
            )
            result = {
                "reconciliation_id": uuid4().hex,
                "account_ids": account_ids,
                "complete_scope": complete_scope,
                "created_at": self.now(),
                "items": items,
                "fees": fees,
                "blockers": blockers,
                "status": "differences"
                if differing or fees["status"] == "different"
                else "needs_review"
                if blockers
                else "matched",
                "limitations": [
                    "未提供平台费用证据时，费用差异显示未知。",
                    "比较和处理说明不会改动持仓、现金或交易记录。",
                ],
            }
            conn.execute(
                "INSERT INTO ledger_reconciliations VALUES(?,?,?,?,NULL,NULL,NULL)",
                [
                    result["reconciliation_id"],
                    result["created_at"],
                    fingerprint(source),
                    encoded(result),
                ],
            )
            audit(
                conn,
                "ledger_reconciliation",
                result["reconciliation_id"],
                self.now(),
                None,
                {"account_ids": account_ids, "status": result["status"]},
            )
            return public(result)

    def resolve(self, identity, resolution, note):
        if resolution not in {"explained", "follow_up", "matched"}:
            raise ValueError("处理结论无效")
        note = require_text(note, "处理说明", limit=1000)
        with self.database.transaction() as conn:
            import json

            record = conn.execute(
                "SELECT source_fingerprint,result_json FROM ledger_reconciliations "
                "WHERE reconciliation_id=?",
                [identity],
            ).fetchone()
            if not record:
                raise ValueError("对账记录不存在")
            result = json.loads(record[1])
            if fingerprint(self._source(conn, result["account_ids"])) != record[0]:
                raise ValueError("账本或平台快照已变化，请重新对账后处理")
            if resolution == "matched" and result["status"] != "matched":
                raise ValueError("仍存在差异或证据缺项，请记录解释或待核对")
            conn.execute(
                "UPDATE ledger_reconciliations SET resolution=?,resolution_note=?,resolved_at=? "
                "WHERE reconciliation_id=?",
                [resolution, note, self.now(), identity],
            )
            audit(
                conn,
                "reconciliation_resolution",
                identity,
                self.now(),
                None,
                {"resolution": resolution, "note": note},
            )
            return {"status": "recorded", "reconciliation_id": identity}

    def history(self, page=1, limit=20):
        if not 1 <= page <= 10000 or not 1 <= limit <= 100:
            raise ValueError("分页参数无效")
        with self.database.connection() as conn:
            return public(
                {
                    "items": rows(
                        conn,
                        "SELECT reconciliation_id,created_at,result_json,resolution,"
                        "resolution_note,resolved_at FROM ledger_reconciliations "
                        "ORDER BY created_at DESC,reconciliation_id "
                        "LIMIT ? OFFSET ?",
                        [limit, (page - 1) * limit],
                    ),
                    "total": conn.execute("SELECT count(*) FROM ledger_reconciliations").fetchone()[
                        0
                    ],
                }
            )
