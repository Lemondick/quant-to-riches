"""Confirmed screenshot holdings for display, without inventing a complete account."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import AssetType
from quant_assistant.services.portfolio_commands import PositionInput

ACCOUNT_ID = "fund-photo"
PLATFORM = "photo_import"


def _state(connection):
    account = connection.execute(
        "SELECT platform, status FROM portfolio_accounts WHERE account_id=?", [ACCOUNT_ID]
    ).fetchone()
    if account is not None and account != (PLATFORM, "active"):
        raise ValueError("截图持仓账户标识已被占用或归档，请先检查账户分组")
    rows = connection.execute(
        "SELECT symbol, quantity, average_cost, market_value, as_of_date "
        "FROM account_positions WHERE account_id=? ORDER BY symbol",
        [ACCOUNT_ID],
    ).fetchall()
    fingerprint = hashlib.sha256(json.dumps([account, rows], default=str).encode()).hexdigest()
    return rows, fingerprint


class PhotoHoldingsMixin:
    def review_holdings(self, token, rows):
        from quant_assistant.services.photo_upload import _positive

        if self.database is None:
            raise ValueError("截图持仓存储未配置")
        if not 1 <= len(rows) <= 100:
            raise ValueError("每次确认1至100只基金")
        with self.lock:
            preview = self._preview_session(token)
            codes = [row["symbol"].strip() for row in rows]
            if len(set(codes)) != len(codes):
                raise ValueError("重复基金请先合并核对")
            if any(len(code) != 6 or not code.isascii() or not code.isdigit() for code in codes):
                raise ValueError("基金代码必须为6位数字")
            try:
                positions = tuple(
                    PositionInput(
                        code,
                        AssetType.FUND,
                        _positive(row["quantity"]).quantize(Decimal("0.00000001")),
                        _positive(row["average_cost"]).quantize(Decimal("0.000001")),
                        _positive(row["market_value"]).quantize(Decimal("0.0001")),
                        preview.as_of_date,
                    )
                    for code, row in zip(codes, rows, strict=True)
                )
            except InvalidOperation as exc:
                raise ValueError("基金数值超出支持精度") from exc
            if any(min(p.quantity, p.average_cost, p.market_value) <= 0 for p in positions):
                raise ValueError("基金数值低于支持精度，请核对截图")
            metadata = self.recognizer.resolve_review_instruments(positions, preview.as_of_date)
            names = {p.symbol: p.name for p in metadata}
            with self.database.connection() as connection:
                before, fingerprint = _state(connection)
                names.update(
                    {
                        code: name
                        for code, name in connection.execute(
                            "SELECT symbol, name FROM instruments WHERE asset_type='fund'"
                        ).fetchall()
                        if code not in names
                    }
                )
            previous = {row[0]: row for row in before}
            differences = []
            for position in positions:
                old = previous.get(position.symbol)
                if old is not None and old[4] > preview.as_of_date:
                    raise ValueError(f"{position.symbol}截图日期早于已保存记录，请使用最新截图")
                differences.append(
                    {
                        "symbol": position.symbol,
                        "change": "更新" if old else "新增",
                        "before_quantity": str(old[1]) if old else "0",
                        "after_quantity": str(position.quantity),
                        "before_cost": str(old[2]) if old else "0",
                        "after_cost": str(position.average_cost),
                        "before_value": str(old[3]) if old else "0",
                        "after_value": str(position.market_value),
                    }
                )
            signature = json.dumps(
                {
                    "image_sha256": preview.image_sha256,
                    "date": preview.as_of_date,
                    "rows": [
                        (p.symbol, p.quantity, p.average_cost, p.market_value) for p in positions
                    ],
                },
                sort_keys=True,
                default=str,
            )
            event_id = "photo:" + hashlib.sha256(signature.encode()).hexdigest()
            review_id = uuid4().hex
            self._expire()
            if len(self.sessions) >= 20:
                raise ValueError("待核对记录过多，请稍后重试")
            self.sessions[review_id] = {
                "kind": "photo_holdings",
                "expires": self.clock() + 900,
                "positions": positions,
                "fingerprint": fingerprint,
                "event_id": event_id,
                "names": names,
                "inferred": bool(preview.inferences),
                "image_sha256": preview.image_sha256,
                "result": None,
            }
            return {
                "review_id": review_id,
                "scope": "fund_holdings",
                "cash": None,
                "total": str(sum(p.market_value for p in positions)),
                "names": names,
                "differences": differences,
                "external_flow": "0",
                "complete_account": False,
            }

    def commit_holdings(self, token, confirmed):
        if confirmed is not True:
            raise ValueError("请核对截图识别结果并确认保存")
        with self.lock:
            session = self._session(token)
            if session.get("kind") != "photo_holdings":
                raise ValueError("请使用截图持仓核对凭证")
            if session["result"] is not None:
                return session["result"]
            positions = session["positions"]
            result = {
                "status": "saved",
                "scope": "fund_holdings",
                "account_id": ACCOUNT_ID,
                "updated_count": len(positions),
                "cash": None,
                "complete_account": False,
                "readiness": "observation",
            }
            now = datetime.now(SHANGHAI_TZ)
            with self.database.transaction() as connection:
                _, fingerprint = _state(connection)
                existing = connection.execute(
                    "SELECT 1 FROM account_events WHERE account_id=? AND event_id=?",
                    [ACCOUNT_ID, session["event_id"]],
                ).fetchone()
                if existing is None:
                    if fingerprint != session["fingerprint"]:
                        raise ValueError("截图持仓已变化，请重新核对后保存")
                    connection.execute(
                        "INSERT INTO portfolio_accounts (account_id,name,platform,currency,status) "
                        "VALUES (?, '截图基金持仓', ?, 'CNY', 'active') ON CONFLICT DO NOTHING",
                        [ACCOUNT_ID, PLATFORM],
                    )
                    for position in positions:
                        connection.execute(
                            "INSERT INTO account_positions (account_id,symbol,asset_type,quantity, "
                            "average_cost,market_value,as_of_date,updated_at) "
                            "VALUES (?,?,'fund',?,?,?,?,?) "
                            "ON CONFLICT (account_id,symbol) DO UPDATE SET "
                            "quantity=excluded.quantity, average_cost=excluded.average_cost, "
                            "market_value=excluded.market_value, "
                            "as_of_date=excluded.as_of_date,updated_at=excluded.updated_at",
                            [
                                ACCOUNT_ID,
                                position.symbol,
                                position.quantity,
                                position.average_cost,
                                position.market_value,
                                position.as_of_date,
                                now,
                            ],
                        )
                    connection.execute(
                        "INSERT INTO account_events "
                        "(account_id,event_id,event_type,effective_date, "
                        "reason,details_json,recorded_at) VALUES (?,?,'snapshot',?,?,?,?)",
                        [
                            ACCOUNT_ID,
                            session["event_id"],
                            positions[0].as_of_date,
                            "用户确认截图基金持仓；仅更新图片中的基金，不构成完整账户基线",
                            json.dumps(
                                {
                                    "scope": "fund_holdings",
                                    "complete_account": False,
                                    "cash_known": False,
                                    "names": session["names"],
                                    "inferred": session["inferred"],
                                    "image_sha256": session["image_sha256"],
                                },
                                ensure_ascii=False,
                            ),
                            now,
                        ],
                    )
            session["result"] = result
            return result

    def photo_holdings(self):
        if self.database is None:
            raise ValueError("截图持仓存储未配置")
        with self.database.connection() as connection:
            rows, _ = _state(connection)
            details = connection.execute(
                "SELECT details_json FROM account_events WHERE account_id=? "
                "ORDER BY recorded_at DESC, id DESC LIMIT 500",
                [ACCOUNT_ID],
            ).fetchall()
        names = {}
        for (raw,) in reversed(details):
            names.update(json.loads(raw).get("names", {}))
        return {
            "scope": "fund_holdings",
            "complete_account": False,
            "cash": None,
            "items": [
                {
                    "symbol": code,
                    "name": names.get(code, code),
                    "quantity": str(quantity),
                    "average_cost": str(cost),
                    "market_value": str(value),
                    "as_of_date": day.isoformat(),
                }
                for code, quantity, cost, value, day in rows
            ],
        }
