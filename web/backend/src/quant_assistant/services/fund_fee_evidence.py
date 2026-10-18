"""User-confirmed channel fees. Public display rates never enter this service."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

from quant_assistant.common.time import now_from_clock
from quant_assistant.funds.fee_terms import decode_fee_terms, terms_object
from quant_assistant.funds.fees import (
    FundFeeSchedule,
    FundRedemptionLot,
    RedemptionTier,
    UnverifiedFeeSchedule,
    _verify_schedule,
    estimate_cost_breakdown,
    estimate_redemption_lots,
)
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.fund_valuation import normalize_fund_symbol
from quant_assistant.services.fund_pool_commands import FundPoolCommandService


class FundFeeEvidenceService:
    def __init__(self, database: Database, *, now: Callable[[], datetime] = now_from_clock):
        self.database = database
        self.now = now

    def save(self, schedule: FundFeeSchedule) -> dict:
        today = self.now().date()
        if normalize_fund_symbol(schedule.symbol) != schedule.symbol:
            raise ValueError("费率证据必须使用规范六位基金代码")
        if schedule.channel != "alipay" or not (schedule.evidence_reference or "").strip():
            raise ValueError("支付宝实际费率必须提供渠道及人工核对证据")
        if schedule.verified_on > today:
            raise ValueError("核验日期不能晚于当前日期")
        if schedule.effective_from is None:
            raise ValueError("必须提供费率生效日期")
        if schedule.purchase_tiers:
            first, last = schedule.purchase_tiers[0], schedule.purchase_tiers[-1]
            if first.min_amount != 0 or last.max_amount is not None:
                raise ValueError("申购金额档必须覆盖0至无限，缺失档不能默认为零费率")
            if any(
                a.max_amount != b.min_amount
                for a, b in zip(schedule.purchase_tiers, schedule.purchase_tiers[1:], strict=False)
            ):
                raise ValueError("申购金额档之间不能存在空档")
        tiers = schedule.redemption_tiers
        if (
            tiers[0].min_days != 0
            or tiers[-1].max_days is not None
            or any(a.max_days + 1 != b.min_days for a, b in zip(tiers, tiers[1:], strict=False))
        ):
            raise ValueError("赎回费档必须连续覆盖全部持有天数")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "SELECT * FROM fund_fee_schedules WHERE symbol=? AND effective_from=?",
                [schedule.symbol, schedule.effective_from],
            )
            columns = [column[0] for column in cursor.description]
            raw = cursor.fetchone()
            before = dict(zip(columns, raw, strict=True)) if raw else None
            FundPoolCommandService._upsert_schedule(
                connection, schedule, schedule.evidence_reference
            )
            connection.execute(
                """INSERT INTO audit_log
                   (entity_type, entity_id, changed_at, before_json, after_json)
                   VALUES ('fund_fee_evidence', ?, ?, ?, ?)""",
                [
                    schedule.symbol,
                    self.now(),
                    json.dumps(before, default=str) if before else None,
                    json.dumps(asdict(schedule), default=str, ensure_ascii=False),
                ],
            )
        return self.list(schedule.symbol)

    def list(self, symbol: str) -> dict:
        code = normalize_fund_symbol(symbol)
        rows = self.database.query_df(
            "SELECT * FROM fund_fee_schedules WHERE symbol=? ORDER BY effective_from DESC, id DESC",
            [code],
        )
        items = []
        current_selected = False
        today = self.now().date()
        for row in rows.to_dict("records"):
            terms = terms_object(row.get("terms_json", "{}"))
            # Legacy product/demo rates do not become an Alipay confirmation.
            if terms.get("channel") != "alipay" or not terms.get("evidence_reference"):
                continue
            schedule = self._schedule(row)
            # Once a later schedule takes effect, a stale/expired record must
            # block quotes rather than falling back to its superseded price.
            valid = not current_selected and schedule.effective_from <= today
            if valid:
                current_selected = True
            try:
                _verify_schedule(schedule, today)
            except UnverifiedFeeSchedule:
                valid = False
            items.append({**asdict(schedule), "usable_today": valid})
        return {"symbol": code, "items": items}

    def quote(
        self,
        symbol: str,
        amount: Decimal,
        holding_days: int,
        *,
        confirmed_on: date | None = None,
        nav_includes_service_fee: bool = False,
    ) -> dict:
        schedule = self._current_schedule(symbol)
        breakdown = estimate_cost_breakdown(
            schedule,
            amount,
            holding_days,
            confirmed_on=confirmed_on,
            nav_includes_service_fee=nav_includes_service_fee,
        )
        return {
            "symbol": schedule.symbol,
            **asdict(breakdown),
            "estimated_on": self.now().date(),
            "basis": "constant_capital_planning",
            "evidence_reference": schedule.evidence_reference,
            "research_only": True,
        }

    def redemption_quote(
        self, symbol: str, lots: tuple[FundRedemptionLot, ...], confirmed_on: date
    ) -> dict:
        schedule = self._current_schedule(symbol)
        items = estimate_redemption_lots(schedule, lots, confirmed_on)
        return {
            "symbol": schedule.symbol,
            "items": items,
            "total_fee": sum((item["fee"] for item in items), Decimal("0")),
            "cash_credit": Decimal("0"),
            "research_only": True,
        }

    def _current_schedule(self, symbol: str) -> FundFeeSchedule:
        available = self.list(symbol)["items"]
        selected = next((item for item in available if item["usable_today"]), None)
        if selected is None:
            raise UnverifiedFeeSchedule("没有当前有效且30日内核对的支付宝实际费率")
        kwargs = {key: value for key, value in selected.items() if key != "usable_today"}
        kwargs["redemption_tiers"] = tuple(
            RedemptionTier(**tier) for tier in kwargs["redemption_tiers"]
        )
        kwargs.update(
            decode_fee_terms(
                json.dumps(
                    {
                        "purchase_tiers": kwargs["purchase_tiers"],
                        "channel": kwargs["channel"],
                        "evidence_reference": kwargs["evidence_reference"],
                        "service_refund_rule": kwargs["service_refund_rule"],
                    },
                    default=str,
                )
            )
        )
        return FundFeeSchedule(**kwargs)

    @staticmethod
    def _schedule(row: dict) -> FundFeeSchedule:
        terms = terms_object(row["terms_json"])
        effective_to = row.get("effective_to")
        return FundFeeSchedule(
            symbol=row["symbol"],
            share_class=terms["share_class"],
            purchase_rate=Decimal(str(row["purchase_fee_rate"])),
            annual_service_rate=Decimal(str(row["service_fee_rate_annual"])),
            redemption_tiers=tuple(
                RedemptionTier(tier["min_days"], tier.get("max_days"), Decimal(str(tier["rate"])))
                for tier in json.loads(row["redemption_tiers_json"])
            ),
            verified_on=row["verified_on"].date(),
            effective_from=row["effective_from"].date(),
            effective_to=effective_to.date()
            if effective_to is not None and str(effective_to) != "NaT"
            else None,
            **decode_fee_terms(row["terms_json"]),
        )
