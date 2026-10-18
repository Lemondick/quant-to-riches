"""Deduplicated account-local evidence alerts, with no model or market requests."""

import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.services.fund_fee_evidence import FundFeeEvidenceService


class AlertSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0, strict=True)
    stale_after_days: int = Field(default=7, ge=0, le=365, strict=True)
    drawdown_threshold: float = Field(default=0.1, gt=0, le=1)
    task_failure_days: int = Field(default=7, ge=1, le=90, strict=True)


class AlertCenter:
    def __init__(self, database, now=None):
        self.database = database
        self.now = now or (lambda: datetime.now(SHANGHAI_TZ))
        with database.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS alert_center_settings (id INTEGER PRIMARY "
                "KEY,payload JSON NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS user_alert_center (id VARCHAR PRIMARY KEY,kind "
                "VARCHAR NOT NULL,subject VARCHAR NOT NULL,detail JSON NOT NULL,first_seen "
                "TIMESTAMPTZ NOT NULL,last_seen TIMESTAMPTZ NOT NULL,active BOOLEAN NOT "
                "NULL,acknowledged BOOLEAN NOT NULL)"
            )

    @staticmethod
    def _settings(conn):
        row = conn.execute("SELECT payload FROM alert_center_settings WHERE id=1").fetchone()
        return AlertSettings.model_validate_json(row[0]) if row else AlertSettings()

    def settings(self):
        with self.database.connection() as conn:
            return self._settings(conn).model_dump()

    def save(self, body):
        body = AlertSettings.model_validate(body)
        with self.database.transaction() as conn:
            if self._settings(conn).revision != body.revision:
                raise ValueError("告警阈值已变化，请重新读取")
            body.revision += 1
            conn.execute(
                "INSERT INTO alert_center_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET "
                "payload=excluded.payload",
                [body.model_dump_json()],
            )
        return self.settings()

    def acknowledge(self, alert_id):
        with self.database.transaction() as conn:
            row = conn.execute(
                "UPDATE user_alert_center SET acknowledged=true WHERE id=? RETURNING id", [alert_id]
            ).fetchone()
        if row is None:
            raise KeyError("alert_not_found")
        return {"status": "acknowledged"}

    def read(self):
        settings, today = self.settings(), self.now().date()
        found = []

        def add(kind, subject, message, **evidence):
            found.append((kind, subject, {"message": message, **evidence}))

        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT symbol,asset_type,as_of_date FROM positions WHERE asset_type!='cash' AND "
                "quantity>0 ORDER BY symbol"
            ).fetchall()
            main_ledger = bool(rows)
            if not rows:
                rows = conn.execute(
                    "SELECT p.symbol,p.asset_type,p.as_of_date FROM account_positions p JOIN "
                    "portfolio_accounts a USING(account_id) WHERE a.status='active' AND "
                    "p.asset_type!='cash' AND p.quantity>0 ORDER BY p.symbol"
                ).fetchall()
            for symbol, asset, as_of in rows:
                date_column, table = (
                    ("nav_date", "fund_nav") if asset == "fund" else ("trade_date", "daily_prices")
                )
                recent = conn.execute(
                    f"SELECT max({date_column}) FROM {table} WHERE symbol=?", [symbol]
                ).fetchone()[0]
                if recent is None or recent > today or as_of > today:
                    add("data_unknown", symbol, "价格或净值日期未知，不能确认数据有效")
                elif (today - recent).days > settings["stale_after_days"] or (
                    today - as_of
                ).days > settings["stale_after_days"]:
                    add(
                        "data_stale",
                        symbol,
                        "价格、净值或持仓快照超过设置时限",
                        data_date=str(recent),
                        position_date=str(as_of),
                    )
                if asset == "fund":
                    try:
                        fees = FundFeeEvidenceService(self.database, now=self.now).list(symbol)[
                            "items"
                        ]
                        usable = any(item["usable_today"] for item in fees)
                    except (KeyError, TypeError, ValueError, ArithmeticError):
                        usable = False
                    if not usable:
                        add(
                            "fee_evidence", symbol, "缺少当前有效的人工核验渠道费率；费用不能视为零"
                        )
            point = conn.execute(
                "SELECT as_of_date,unit_nav,high_water_mark FROM portfolio_valuations ORDER BY "
                "as_of_date DESC,generated_at DESC LIMIT 1"
            ).fetchone()
            if rows:
                if (
                    not main_ledger
                    or point is None
                    or point[1] is None
                    or point[2] is None
                    or not 0 < point[1] <= point[2]
                    or point[0] > today
                    or point[0] < max(row[2] for row in rows)
                    or (today - point[0]).days > settings["stale_after_days"]
                ):
                    add("drawdown_unknown", "portfolio", "有效账户净值或高水位不足，当前回撤未知")
                else:
                    drawdown = 1 - Decimal(str(point[1])) / Decimal(str(point[2]))
                    if drawdown >= Decimal(str(settings["drawdown_threshold"])):
                        add(
                            "drawdown",
                            "portfolio",
                            "账户净值回撤达到设置阈值",
                            drawdown=float(drawdown),
                            threshold=settings["drawdown_threshold"],
                            as_of_date=str(point[0]),
                        )
            failures = conn.execute(
                "SELECT report_id,error_code,updated_at FROM market_advisor_reports WHERE "
                "status='failed' AND updated_at>=? - INTERVAL 1 DAY * ? ORDER BY updated_at DESC "
                "LIMIT 100",
                [self.now(), settings["task_failure_days"]],
            ).fetchall()
            failures += conn.execute(
                "SELECT job_id,error_code,created_at FROM agent_research_jobs WHERE status IN "
                "('failed','timed_out','interrupted') AND created_at>=? - INTERVAL 1 DAY * ? "
                "ORDER BY created_at DESC LIMIT 100",
                [self.now(), settings["task_failure_days"]],
            ).fetchall()
            for job_id, code, occurred in failures:
                add(
                    "task_failure",
                    job_id,
                    "研究任务失败，请检查任务详情",
                    error_code=code,
                    occurred_at=occurred.isoformat(),
                )
        with self.database.transaction() as conn:
            conn.execute("UPDATE user_alert_center SET active=false")
            for kind, subject, detail in found:
                existing = conn.execute(
                    "SELECT id FROM user_alert_center WHERE kind=? AND subject=?",
                    [kind, subject],
                ).fetchone()
                alert_id = existing[0] if existing else uuid4().hex
                conn.execute(
                    "INSERT INTO user_alert_center VALUES(?,?,?,?,?,?,true,false) ON "
                    "CONFLICT(id) DO UPDATE SET "
                    "detail=excluded.detail,last_seen=excluded.last_seen,active=true",
                    [
                        alert_id,
                        kind,
                        subject,
                        json.dumps(detail, ensure_ascii=False),
                        self.now(),
                        self.now(),
                    ],
                )
            rows = conn.execute(
                "SELECT id,kind,subject,detail,first_seen,last_seen,acknowledged FROM "
                "user_alert_center WHERE active ORDER BY acknowledged,first_seen DESC"
            ).fetchall()
        return {
            "items": [
                {
                    "id": r[0],
                    "kind": r[1],
                    "subject": r[2],
                    "detail": json.loads(r[3]),
                    "first_seen": r[4],
                    "last_seen": r[5],
                    "acknowledged": r[6],
                }
                for r in rows
            ],
            "settings": settings,
            "scope": "current_account",
            "unknown_is_safe": False,
        }
