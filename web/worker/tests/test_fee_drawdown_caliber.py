"""E4: cross-language caliber lock for fee quotes and drawdown validation.

Python side of the shared fixture ``tests/fixtures/fee-drawdown-caliber-vectors.json``.
The C# twin lives in ``web/backend-dotnet/tests/QuantAssistant.Api.Tests/
FundFeeDrawdownCaliberTests.cs``; both must agree on every vector, so a future
change to either implementation that drifts the caliber fails loudly.
"""

import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant_assistant.funds.fees import (
    FundFeeSchedule,
    PurchaseTier,
    RedemptionTier,
    ServiceFeeRefundRule,
    estimate_cost_breakdown,
)
from quant_assistant.portfolio.drawdown_control import validate_drawdown

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "fee-drawdown-caliber-vectors.json"


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def build_schedule(raw):
    rule = raw.get("service_refund_rule")
    return FundFeeSchedule(
        symbol=raw["symbol"],
        share_class=raw["share_class"],
        purchase_rate=Decimal(raw["purchase_rate"]),
        annual_service_rate=Decimal(raw["annual_service_rate"]),
        redemption_tiers=tuple(
            RedemptionTier(t["min_days"], t["max_days"], Decimal(t["rate"]))
            for t in raw["redemption_tiers"]
        ),
        purchase_tiers=tuple(
            PurchaseTier(
                Decimal(t["min_amount"]),
                Decimal(t["max_amount"]) if t["max_amount"] else None,
                Decimal(t["rate"]) if t["rate"] else None,
                Decimal(t["fixed_amount"]) if t["fixed_amount"] else None,
            )
            for t in raw.get("purchase_tiers", ())
        ),
        verified_on=date.fromisoformat(raw["verified_on"]),
        effective_from=date.fromisoformat(raw["effective_from"]),
        service_refund_rule=(
            ServiceFeeRefundRule(
                effective_from=date.fromisoformat(rule["effective_from"]),
                after_holding_days=rule["after_holding_days"],
                evidence_reference=rule["evidence_reference"],
            )
            if rule
            else None
        ),
    )


class FeeCaliberTests(unittest.TestCase):
    def test_vectors_match_python_implementation(self):
        data = load_fixture()
        schedule = build_schedule(data["schedule"])
        self.assertGreater(len(data["fee_cases"]), 0)
        for case in data["fee_cases"]:
            with self.subTest(amount=case["amount"], days=case["holding_days"],
                              nav_inc=case["nav_includes_service_fee"]):
                est = estimate_cost_breakdown(
                    schedule,
                    Decimal(case["amount"]),
                    case["holding_days"],
                    confirmed_on=date.fromisoformat(case["confirmed_on"]),
                    nav_includes_service_fee=case["nav_includes_service_fee"],
                )
                exp = case["expected"]
                self.assertEqual(est.purchase_fee, Decimal(exp["purchase_fee"]))
                self.assertEqual(est.redemption_fee, Decimal(exp["redemption_fee"]))
                self.assertEqual(est.service_fee_estimate, Decimal(exp["service_fee_estimate"]))
                self.assertEqual(est.expected_service_refund, Decimal(exp["expected_service_refund"]))
                self.assertEqual(est.net_cost_estimate, Decimal(exp["net_cost_estimate"]))


class DrawdownCaliberTests(unittest.TestCase):
    def test_vectors_match_python_implementation(self):
        data = load_fixture()
        self.assertGreater(len(data["drawdown_cases"]), 0)
        for case in data["drawdown_cases"]:
            with self.subTest(nav=case["nav"], threshold=case["threshold"]):
                rows = [
                    {"unit_nav": v, "as_of_date": f"2026-01-{i + 1:02d}"}
                    for i, v in enumerate(case["nav"])
                ]
                result = validate_drawdown("caliber", rows, threshold=case["threshold"])
                exp = case["expected"]
                self.assertAlmostEqual(result.max_drawdown, float(exp["max_drawdown"]), places=12)
                self.assertEqual(result.breach_count, exp["breach_count"])
                self.assertEqual(result.passed, exp["passed"])
                self.assertEqual(list(result.reasons), exp["reasons"])


if __name__ == "__main__":
    unittest.main()
