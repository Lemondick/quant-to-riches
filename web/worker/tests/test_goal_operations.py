"""Worker op goal.project: projection math, boundaries, registration."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goal_operations import (  # noqa: E402
    DEFAULT_ANNUAL_RETURN,
    DISCLAIMER,
    GOAL_CAPABILITIES,
    execute_goal,
    months_until,
    project_goal,
)
from operations import CAPABILITIES, execute  # noqa: E402
from worker_result import child_envelope  # noqa: E402


class TestCapabilities(unittest.TestCase):
    def test_declares_goal_project(self):
        self.assertIsInstance(GOAL_CAPABILITIES, frozenset)
        self.assertIn("goal.project", GOAL_CAPABILITIES)

    def test_registered_in_dispatch_chain(self):
        self.assertIn("goal.project", CAPABILITIES)
        result = execute(
            "goal.project",
            {
                "target_amount": 200000,
                "target_date": "2031-10-21",
                "current_amount": 50000,
            },
        )
        self.assertIn("required_monthly", result)
        self.assertIn("disclaimer", result)


class TestMonthsUntil(unittest.TestCase):
    def test_full_months(self):
        self.assertEqual(months_until(date(2027, 10, 21), date(2026, 10, 21)), 12)

    def test_partial_month_rounds_down(self):
        self.assertEqual(months_until(date(2026, 11, 20), date(2026, 10, 21)), 0)
        self.assertEqual(months_until(date(2026, 11, 21), date(2026, 10, 21)), 1)

    def test_past_date(self):
        self.assertLessEqual(months_until(date(2026, 10, 20), date(2026, 10, 21)), 0)


class TestProjection(unittest.TestCase):
    AS_OF = date(2026, 10, 21)

    def _payload(self, **overrides):
        base = {
            "target_amount": 200000.0,
            "target_date": "2027-10-21",
            "current_amount": 10000.0,
            "assumed_annual_return": 0.06,
        }
        base.update(overrides)
        return base

    def _independent(self, target, current, annual, months, monthly=None):
        rate = (1.0 + annual) ** (1.0 / 12.0) - 1.0
        growth = (1.0 + rate) ** months
        future_current = current * growth
        annuity = months if rate == 0.0 else (growth - 1.0) / rate
        required = max(0.0, (target - future_current) / annuity)
        projected = future_current if monthly is None else future_current + monthly * annuity
        return rate, round(required, 2), round(projected, 2), round(target - projected, 2)

    def test_backward_required_monthly(self):
        result = project_goal(as_of=self.AS_OF, **self._payload())
        rate, required, projected, gap = self._independent(200000.0, 10000.0, 0.06, 12)
        self.assertEqual(result["months_remaining"], 12)
        self.assertAlmostEqual(result["required_monthly"], required)
        self.assertAlmostEqual(result["projected_amount"], projected)
        self.assertAlmostEqual(result["gap"], gap)
        self.assertFalse(result["on_track"])
        self.assertEqual(result["disclaimer"], DISCLAIMER)
        self.assertEqual(result["assumptions"]["assumed_annual_return"], 0.06)
        self.assertEqual(result["assumptions"]["compounding"], "monthly")

    def test_forward_with_monthly_amount(self):
        result = project_goal(monthly_amount=16000.0, as_of=self.AS_OF, **self._payload())
        rate, required, projected, gap = self._independent(
            200000.0, 10000.0, 0.06, 12, monthly=16000.0
        )
        self.assertAlmostEqual(result["required_monthly"], required)
        self.assertAlmostEqual(result["projected_amount"], projected)
        self.assertAlmostEqual(result["gap"], gap)
        self.assertEqual(result["on_track"], gap <= 0)

    def test_on_track_when_ahead(self):
        result = project_goal(monthly_amount=50000.0, as_of=self.AS_OF, **self._payload())
        self.assertTrue(result["on_track"])
        self.assertLessEqual(result["gap"], 0)

    def test_zero_return(self):
        payload = self._payload(assumed_annual_return=0.0)
        result = project_goal(as_of=self.AS_OF, **payload)
        # (200000 - 10000) / 12 = 15833.33
        self.assertAlmostEqual(result["required_monthly"], 15833.33, places=2)
        self.assertAlmostEqual(result["projected_amount"], 10000.0)

    def test_already_funded_needs_zero(self):
        result = project_goal(
            as_of=self.AS_OF,
            **self._payload(target_amount=10000.0, current_amount=50000.0),
        )
        self.assertEqual(result["required_monthly"], 0.0)
        self.assertTrue(result["on_track"])

    def test_expired_target_degrades_to_current_comparison(self):
        result = project_goal(
            as_of=self.AS_OF,
            **self._payload(target_date="2026-10-20", monthly_amount=1000.0),
        )
        self.assertEqual(result["months_remaining"], 0)
        self.assertEqual(result["required_monthly"], 0.0)
        self.assertAlmostEqual(result["projected_amount"], 10000.0)
        self.assertAlmostEqual(result["gap"], 190000.0)
        self.assertFalse(result["on_track"])
        self.assertTrue(any("已到期" in w for w in result["warnings"]))

    def test_expired_target_already_met(self):
        result = project_goal(
            as_of=self.AS_OF,
            **self._payload(target_date="2026-10-20", current_amount=250000.0),
        )
        self.assertTrue(result["on_track"])
        self.assertLessEqual(result["gap"], 0)

    def test_default_annual_return(self):
        payload = self._payload()
        del payload["assumed_annual_return"]
        result = execute_goal("goal.project", payload)
        self.assertEqual(result["assumptions"]["assumed_annual_return"], DEFAULT_ANNUAL_RETURN)


class TestValidation(unittest.TestCase):
    def test_missing_key_raises_key_error(self):
        with self.assertRaises(KeyError):
            execute_goal("goal.project", {"target_amount": 1.0})

    def test_missing_key_maps_to_invalid_request(self):
        envelope = child_envelope(lambda: execute_goal("goal.project", {"target_amount": 1.0}))
        self.assertIsNone(envelope["result"])
        self.assertEqual(envelope["error_code"], "invalid_request")

    def test_bad_values_raise_value_error(self):
        good = {
            "target_amount": 200000.0,
            "target_date": "2027-10-21",
            "current_amount": 10000.0,
        }
        for bad in (
            {"target_amount": 0},
            {"target_amount": -5},
            {"current_amount": -1},
            {"target_date": "not-a-date"},
            {"target_date": "2027-13-40"},
            {"monthly_amount": -100},
            {"assumed_annual_return": float("nan")},
            {"assumed_annual_return": -2.0},
        ):
            payload = dict(good)
            payload.update(bad)
            with self.assertRaises(ValueError, msg=str(bad)):
                execute_goal("goal.project", payload)

    def test_unsupported_frequency_rejected(self):
        with self.assertRaises(ValueError):
            execute_goal(
                "goal.project",
                {
                    "target_amount": 200000.0,
                    "target_date": "2027-10-21",
                    "current_amount": 10000.0,
                    "frequency": "weekly",
                },
            )

    def test_non_mapping_payload_rejected(self):
        with self.assertRaises(ValueError):
            execute_goal("goal.project", ["not", "a", "mapping"])

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            execute_goal("goal.other", {})


if __name__ == "__main__":
    unittest.main()
