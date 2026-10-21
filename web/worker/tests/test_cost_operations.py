"""Worker op cost.positions: holding-cost, P&L, XIRR, break-even NAV."""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cost_operations import COST_CAPABILITIES, cost_positions, execute_cost  # noqa: E402
from operations import CAPABILITIES, execute  # noqa: E402

_TIERS = [
    {"min_days": 0, "max_days": 6, "rate": "0.015"},
    {"min_days": 7, "max_days": 364, "rate": "0.005"},
    {"min_days": 365, "rate": "0"},
]


def _fund(**overrides):
    fund = {
        "fund_code": "000001",
        "current_nav": "1.2000",
        "lots": [
            {
                "acquired_at": "2025-06-01",
                "shares": "1000",
                "cost_amount": "1100.00",
                "redemption_tiers": _TIERS,
            },
            {
                "acquired_at": "2025-09-01",
                "shares": "500",
                "cost_amount": "600.00",
                "redemption_tiers": _TIERS,
            },
        ],
    }
    fund.update(overrides)
    return fund


class CostPositionsTests(unittest.TestCase):
    def test_op_is_registered(self):
        self.assertIn("cost.positions", CAPABILITIES)
        self.assertIn("cost.positions", COST_CAPABILITIES)

    def test_basic_numbers(self):
        as_of = date(2026, 10, 21)
        result = cost_positions([_fund()], as_of=as_of)
        fund = result["funds"][0]
        # 1500 shares @ 1.2 = 1800 value; cost 1700 -> pnl 100
        self.assertEqual(fund["shares"], "1500")
        self.assertEqual(fund["cost_amount"], "1700.00")
        self.assertEqual(fund["current_value"], "1800.0000")
        self.assertEqual(fund["floating_pnl"], "100.0000")
        self.assertAlmostEqual(fund["floating_pnl_pct"], 100 / 1700, places=6)
        # Both lots held > 365 days? No: 2025-06-01 -> 507 days (tier 0),
        # 2025-09-01 -> 415 days (tier 0). Fee = 0.
        self.assertEqual(fund["est_redemption_fee"], "0.00")
        self.assertEqual(fund["breakeven_nav"], str(Decimal("1700.00") / Decimal("1500")))
        self.assertIn("含预估赎回费", fund["breakeven_note"])
        self.assertIsNotNone(fund["annual_return_xirr"])
        self.assertGreater(fund["annual_return_xirr"], 0)

    def test_redemption_fee_uses_holding_days_tier(self):
        as_of = date(2026, 1, 10)
        fund = _fund(
            lots=[
                {
                    "acquired_at": "2026-01-05",  # 5 days -> 1.5% tier
                    "shares": "1000",
                    "cost_amount": "1000.00",
                    "redemption_tiers": _TIERS,
                }
            ]
        )
        result = cost_positions([fund], as_of=as_of)
        # fee = 1000 * 1.2 * 0.015 = 18.00
        self.assertEqual(result["funds"][0]["est_redemption_fee"], "18.00")
        self.assertEqual(
            result["funds"][0]["breakeven_nav"], str(Decimal("1018.00") / Decimal("1000"))
        )

    def test_xirr_simple_case(self):
        # 10000 invested exactly ~1 year ago, now worth 11000 -> ~10% annualised.
        fund = _fund(
            current_nav="1.1000",
            lots=[
                {
                    "acquired_at": "2025-10-21",
                    "shares": "10000",
                    "cost_amount": "10000.00",
                    "redemption_tiers": _TIERS,
                }
            ],
        )
        result = cost_positions([fund], as_of=date(2026, 10, 21))
        self.assertAlmostEqual(result["funds"][0]["annual_return_xirr"], 0.10, places=2)

    def test_missing_tiers_gives_null_fee_with_warning(self):
        fund = _fund(lots=[{**_fund()["lots"][0], "redemption_tiers": None}])
        result = cost_positions([fund])
        item = result["funds"][0]
        self.assertIsNone(item["est_redemption_fee"])
        self.assertIn("未含赎回费", item["breakeven_note"])
        self.assertEqual(item["breakeven_nav"], str(Decimal("1100.00") / Decimal("1000")))
        self.assertTrue(result["warnings"])
        self.assertIsNone(result["summary"]["total_est_redemption_fee"])

    def test_no_txn_detail_fallback_has_null_xirr(self):
        fund = _fund(
            lots=[
                {
                    "acquired_at": date.today().isoformat(),
                    "shares": "1000",
                    "cost_amount": "1100.00",
                    "redemption_tiers": None,
                    "_no_txn_detail": True,
                }
            ]
        )
        result = cost_positions([fund])
        self.assertIsNone(result["funds"][0]["annual_return_xirr"])
        self.assertTrue(any("年化收益率无法计算" in w for w in result["warnings"]))

    def test_summary_aggregates(self):
        result = cost_positions([_fund(), _fund(fund_code="000002")])
        summary = result["summary"]
        self.assertEqual(summary["n_funds"], 2)
        self.assertEqual(summary["total_cost"], "3400.00")
        self.assertEqual(summary["total_floating_pnl"], "200.0000")
        self.assertEqual([f["fund_code"] for f in result["funds"]], ["000001", "000002"])

    def test_empty_funds_rejected(self):
        with self.assertRaises(ValueError):
            cost_positions([])

    def test_bad_shapes_rejected(self):
        with self.assertRaises(ValueError):
            cost_positions([{"fund_code": "", "current_nav": "1", "lots": []}])
        with self.assertRaises(ValueError):
            cost_positions([_fund(current_nav="-1")])
        with self.assertRaises(ValueError):
            cost_positions("nope")


class ExecuteCostTests(unittest.TestCase):
    def test_dispatch_via_registry(self):
        result = execute("cost.positions", {"funds": [_fund()]})
        self.assertEqual(result["summary"]["n_funds"], 1)

    def test_missing_inputs_rejected(self):
        with self.assertRaises(ValueError):
            execute_cost("cost.positions", {})

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            execute_cost("cost.nope", {"funds": [_fund()]})


if __name__ == "__main__":
    unittest.main()
