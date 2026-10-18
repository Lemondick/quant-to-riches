"""Account-local token accounting and explicit prices; unknown spend fails closed."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.services.advisor_model_settings import ModelId


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_per_million: Decimal = Field(ge=0, le=1000000)
    output_per_million: Decimal = Field(ge=0, le=1000000)


class CostSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    daily_budget: Decimal | None = Field(default=None, gt=0, le=1000000)
    prices: dict[ModelId, ModelPrice] = Field(default_factory=dict, max_length=100)


class CostLimitError(ValueError):
    pass


def tokens(usage):
    if isinstance(usage, list):
        counts = [tokens(item) for item in usage]
        if not counts or any(item is None for item in counts):
            return None
        return sum(item[0] for item in counts), sum(item[1] for item in counts)
    if not isinstance(usage, dict):
        return None

    def count(primary, alias):
        values = [usage[key] for key in (primary, alias) if key in usage]
        if not values or any(
            type(value) is not int or not 0 <= value <= 2_000_000_000 for value in values
        ):
            return None
        return values[0] if all(value == values[0] for value in values) else None

    incoming = count("prompt_tokens", "input_tokens")
    outgoing = count("completion_tokens", "output_tokens")
    if incoming is None or outgoing is None:
        return None
    if "total_tokens" in usage and (
        type(usage["total_tokens"]) is not int or usage["total_tokens"] != incoming + outgoing
    ):
        return None
    return incoming, outgoing


class ModelCostService:
    def __init__(self, database, now=None):
        self.database = database
        self.now = now or (lambda: datetime.now(SHANGHAI_TZ))
        with database.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS model_cost_settings "
                "(id INTEGER PRIMARY KEY, payload JSON NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS model_call_costs "
                "(id VARCHAR PRIMARY KEY, called_at TIMESTAMPTZ NOT NULL, "
                "model VARCHAR NOT NULL, status VARCHAR NOT NULL, prices_json JSON, "
                "currency VARCHAR, input_tokens BIGINT, output_tokens BIGINT, "
                "reserved_cost VARCHAR, cost VARCHAR)"
            )

    @staticmethod
    def _settings(conn):
        row = conn.execute("SELECT payload FROM model_cost_settings WHERE id=1").fetchone()
        return CostSettings.model_validate_json(row[0]) if row else CostSettings(revision=0)

    def recover_pending(self):
        """Called once when the advisor service graph is built, before any worker starts."""
        with self.database.transaction() as conn:
            conn.execute("UPDATE model_call_costs SET status='interrupted' WHERE status='reserved'")

    def save(self, body):
        body = CostSettings.model_validate(body)
        with self.database.transaction() as conn:
            current = self._settings(conn)
            if current.revision != body.revision:
                raise CostLimitError("费用配置已变化，请重新读取")
            today = self._summary(conn, current)
            if current.currency != body.currency and today["calls"]:
                raise CostLimitError("当日已有调用，不能更换计费货币")
            if today["pending_calls"]:
                raise CostLimitError("已有模型调用执行中，请完成后修改费用配置")
            body.revision += 1
            conn.execute(
                "INSERT INTO model_cost_settings VALUES(1,?) ON CONFLICT(id) "
                "DO UPDATE SET payload=excluded.payload",
                [body.model_dump_json()],
            )
        return self.read()

    def _summary(self, conn, settings):
        rows = conn.execute(
            "SELECT status,cost,reserved_cost FROM model_call_costs "
            "WHERE CAST(called_at AS DATE)=?",
            [self.now().date()],
        ).fetchall()
        known = sum((Decimal(r[1]) for r in rows if r[1] is not None), Decimal(0))
        reserved = sum(
            (Decimal(r[2]) for r in rows if r[0] == "reserved" and r[2] is not None), Decimal(0)
        )
        unknown = sum(r[0] != "reserved" and r[1] is None for r in rows)
        pending = sum(r[0] == "reserved" for r in rows)
        budget = settings.daily_budget
        blocker = (
            "unknown_usage_or_price"
            if unknown
            else ("daily_budget" if budget is not None and known + reserved >= budget else None)
        )
        return {
            "date": self.now().date().isoformat(),
            "currency": settings.currency,
            "calls": len(rows),
            "known_cost": str(known),
            "total_cost": None if unknown or pending else str(known),
            "unknown_calls": unknown,
            "pending_calls": pending,
            "reserved_cost": str(reserved),
            "remaining_budget": None
            if budget is None or unknown
            else str(max(Decimal(0), budget - known - reserved)),
            "budget_blocker": blocker if budget is not None else None,
        }

    def read(self):
        with self.database.connection() as conn:
            settings = self._settings(conn)
            today = self._summary(conn, settings)
            rows = conn.execute(
                "SELECT id,called_at,model,status,input_tokens,output_tokens,cost,currency "
                "FROM model_call_costs ORDER BY called_at DESC LIMIT 50"
            ).fetchall()
        return {
            "settings": settings.model_dump(mode="json"),
            "today": today,
            "items": [
                dict(
                    zip(
                        (
                            "id",
                            "called_at",
                            "model",
                            "status",
                            "input_tokens",
                            "output_tokens",
                            "cost",
                            "currency",
                        ),
                        r,
                        strict=True,
                    )
                )
                for r in rows
            ],
            "basis": "provider_usage_and_user_prices",
            "unknown_cost_is_zero": False,
        }

    def reserve(self, model, *, max_input_tokens=None, max_output_tokens=None):
        with self.database.transaction() as conn:
            settings = self._settings(conn)
            price = settings.prices.get(model)
            bound = tokens({"input_tokens": max_input_tokens, "output_tokens": max_output_tokens})
            amount = (
                (
                    (price.input_per_million * bound[0] + price.output_per_million * bound[1])
                    / 1000000
                )
                if price and bound
                else None
            )
            summary = self._summary(conn, settings)
            if settings.daily_budget is not None and (
                summary["budget_blocker"]
                or amount is None
                or amount > Decimal(summary["remaining_budget"])
            ):
                raise CostLimitError("费用额度不足或调用费用上限未知；未调用模型")
            call_id = uuid4().hex
            conn.execute(
                "INSERT INTO model_call_costs VALUES(?,?,?,'reserved',?,?,NULL,NULL,?,NULL)",
                [
                    call_id,
                    self.now(),
                    model,
                    price.model_dump_json() if price else None,
                    settings.currency,
                    str(amount) if amount is not None else None,
                ],
            )
        return call_id

    def finish(self, call_id, usage):
        count = tokens(usage)
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT prices_json FROM model_call_costs WHERE id=? AND status='reserved'",
                [call_id],
            ).fetchone()
            if row is None:
                return
            price = ModelPrice.model_validate_json(row[0]) if row[0] else None
            cost = (
                (price.input_per_million * count[0] + price.output_per_million * count[1]) / 1000000
                if price and count
                else None
            )
            conn.execute(
                "UPDATE model_call_costs SET "
                "status='completed',input_tokens=?,output_tokens=?,cost=? WHERE id=?",
                [
                    count[0] if count else None,
                    count[1] if count else None,
                    str(cost) if cost is not None else None,
                    call_id,
                ],
            )
