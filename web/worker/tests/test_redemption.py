"""Worker op fund.redemption_windows: tier-drop reminders via FundFeeSchedule."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations import CAPABILITIES, execute
from redemption_operations import execute_redemption, redemption_windows
from worker_result import child_envelope

# Classic CN A-class redemption ladder (test fixture only).
TIERS = [
    {"min_days": 0, "max_days": 6, "rate": "0.015"},
    {"min_days": 7, "max_days": 29, "rate": "0.0075"},
    {"min_days": 30, "max_days": 364, "rate": "0.005"},
    {"min_days": 365, "max_days": 729, "rate": "0.0025"},
    {"min_days": 730, "max_days": None, "rate": "0"},
]

TODAY = date(2026, 10, 21)


class RedemptionWindowTests(unittest.TestCase):
    def test_op_is_registered(self):
        self.assertIn("fund.redemption_windows", CAPABILITIES)

    def test_next_tier_derived_from_tiers(self):
        # Acquired 2026-10-01 -> 20 holding days -> current tier [7,29] @0.75%;
        # next tier [30,364] starts 2026-10-31, 10 days away.
        result = redemption_windows(
            [
                {
                    "fund_code": "000001",
                    "acquired_at": "2026-10-01",
                    "amount": "10000",
                    "redemption_tiers": TIERS,
                }
            ],
            30,
            as_of=TODAY,
        )
        self.assertEqual(len(result["windows"]), 1)
        window = result["windows"][0]
        self.assertEqual(window["fund_code"], "000001")
        self.assertEqual(window["lot_id"], "000001#0")
        self.assertEqual(window["current_tier"]["rate"], "0.0075")
        self.assertEqual(window["next_tier"]["min_days"], 30)
        self.assertEqual(window["next_tier"]["rate"], "0.005")
        self.assertEqual(window["next_tier_at"], "2026-10-31")
        self.assertEqual(window["days_left"], 10)
        # save = 10000 * (0.0075 - 0.005) = 25.00
        self.assertEqual(window["save_estimate"], "25.00")

    def test_outside_days_ahead_is_filtered(self):
        result = redemption_windows(
            [
                {
                    "fund_code": "000001",
                    "acquired_at": "2026-10-01",
                    "amount": "10000",
                    "redemption_tiers": TIERS,
                }
            ],
            5,
            as_of=TODAY,
        )
        self.assertEqual(result["windows"], [])

    def test_cheapest_tier_has_no_window(self):
        result = redemption_windows(
            [
                {
                    "fund_code": "000001",
                    "acquired_at": "2023-01-01",
                    "amount": "10000",
                    "redemption_tiers": TIERS,
                }
            ],
            30,
            as_of=TODAY,
        )
        self.assertEqual(result["windows"], [])

    def test_missing_tiers_warns_without_error(self):
        result = redemption_windows(
            [{"fund_code": "000001", "acquired_at": "2026-10-01", "amount": "100"}],
            30,
            as_of=TODAY,
        )
        self.assertEqual(result["windows"], [])
        self.assertTrue(result["warnings"])

    def test_global_tiers_apply_to_lots(self):
        result = execute_redemption(
            "fund.redemption_windows",
            {
                "lots": [{"fund_code": "000001", "acquired_at": "2026-10-01", "amount": "10000"}],
                "redemption_tiers": TIERS,
                "days_ahead": 30,
            },
        )
        self.assertEqual(len(result["windows"]), 1)

    def test_non_uniform_ladder_not_hardcoded(self):
        # An exotic ladder: the op must follow the tiers, not 30d/1y/2y.
        tiers = [
            {"min_days": 0, "max_days": 44, "rate": "0.02"},
            {"min_days": 45, "max_days": 199, "rate": "0.01"},
            {"min_days": 200, "max_days": None, "rate": "0"},
        ]
        result = redemption_windows(
            [
                {
                    "fund_code": "X",
                    "acquired_at": "2026-09-21",
                    "amount": "5000",
                    "redemption_tiers": tiers,
                }
            ],
            60,
            as_of=TODAY,  # 30 holding days -> next tier at 45 days
        )
        window = result["windows"][0]
        self.assertEqual(window["next_tier"]["min_days"], 45)
        self.assertEqual(window["next_tier_at"], "2026-11-05")
        self.assertEqual(window["days_left"], 15)
        self.assertEqual(window["save_estimate"], "50.00")  # 5000 * 0.01

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            redemption_windows([], 30, as_of=TODAY)
        with self.assertRaises(ValueError):
            redemption_windows(
                [
                    {
                        "fund_code": "000001",
                        "acquired_at": "2026-12-01",
                        "amount": "1",
                        "redemption_tiers": TIERS,
                    }
                ],
                30,
                as_of=TODAY,
            )
        with self.assertRaises(ValueError):
            redemption_windows(
                [
                    {
                        "fund_code": "",
                        "acquired_at": "2026-10-01",
                        "amount": "1",
                        "redemption_tiers": TIERS,
                    }
                ],
                30,
                as_of=TODAY,
            )
        outcome = child_envelope(
            lambda: execute_redemption("fund.redemption_windows", {"lots": []})
        )
        self.assertEqual(outcome["error_code"], "invalid_request")

    def test_execute_dispatch(self):
        result = execute(
            "fund.redemption_windows",
            {
                "lots": [
                    {
                        "fund_code": "000001",
                        "acquired_at": "2026-10-01",
                        "amount": "10000",
                        "redemption_tiers": TIERS,
                    }
                ]
            },
        )
        self.assertIn("windows", result)


if __name__ == "__main__":
    unittest.main()
