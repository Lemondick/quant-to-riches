"""Optional, versioned fund-fee terms shared by storage readers and writers."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from quant_assistant.funds.fees import FundFeeSchedule, PurchaseTier, ServiceFeeRefundRule


def encode_fee_terms(schedule: FundFeeSchedule) -> str:
    return json.dumps(
        {
            "version": 1,
            "share_class": schedule.share_class,
            "purchase_tiers": [asdict(tier) for tier in schedule.purchase_tiers],
            "channel": schedule.channel,
            "evidence_reference": schedule.evidence_reference,
            "service_refund_rule": (
                asdict(schedule.service_refund_rule) if schedule.service_refund_rule else None
            ),
        },
        ensure_ascii=False,
        default=str,
        sort_keys=True,
    )


def terms_object(raw: object) -> dict:
    value = json.loads(raw) if isinstance(raw, str) else raw
    if value is None:
        return {}
    if not isinstance(value, dict) or value.get("version", 1) != 1:
        raise ValueError("invalid or unsupported fund-fee terms")
    return value


def decode_fee_terms(raw: object) -> dict:
    value = terms_object(raw)
    refund = value.get("service_refund_rule")
    return {
        "purchase_tiers": tuple(
            PurchaseTier(
                Decimal(tier["min_amount"]),
                Decimal(tier["max_amount"]) if tier.get("max_amount") is not None else None,
                Decimal(tier["rate"]) if tier.get("rate") is not None else None,
                Decimal(tier["fixed_amount"]) if tier.get("fixed_amount") is not None else None,
            )
            for tier in value.get("purchase_tiers", [])
        ),
        "channel": value.get("channel"),
        "evidence_reference": value.get("evidence_reference"),
        "service_refund_rule": ServiceFeeRefundRule(
            date.fromisoformat(refund["effective_from"]),
            refund["after_holding_days"],
            refund["evidence_reference"],
        )
        if refund
        else None,
    }
