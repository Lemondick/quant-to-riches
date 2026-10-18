"""Acceptance for the Brinson-Fachler attribution primitives.

``analysis/attribution.py`` computes allocation/selection/interaction by group
plus per-holding contributions. It had no acceptance coverage. The load-bearing
property is the **reconciliation identity**: when both weight vectors sum to
one, the three effects must sum exactly to the active return. A sign flip or a
swapped operand anywhere in the decomposition would silently produce a
plausible-looking waterfall that no longer ties out.

This module is pure arithmetic and never touches a database or the network. The
caller-side gating (which currently blocks the attribution panel because no
benchmark industry composition exists in this system) lives elsewhere; here we
lock the maths that the panel will eventually be allowed to call.
"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

import pandas as pd  # noqa: E402

from quant_assistant.analysis.attribution import (  # noqa: E402
    AttributionResult,
    brinson_attribution,
    calculate_attribution,
    holding_contributions,
)

PORTFOLIO = pd.DataFrame({"group": ["科技", "金融"], "weight": [0.6, 0.4], "return": [0.10, 0.05]})
BENCHMARK = pd.DataFrame({"group": ["科技", "金融"], "weight": [0.5, 0.5], "return": [0.08, 0.06]})
# Hand-computed for the fixture above:
EXPECTED_TECH = {
    "allocation": 0.001,
    "selection": 0.010,
    "interaction": 0.002,
    "total_effect": 0.013,
}
EXPECTED_FINANCE = {
    "allocation": 0.001,
    "selection": -0.005,
    "interaction": 0.001,
    "total_effect": -0.003,
}


def reconciles(result, tolerance=1e-9):
    return abs(result["total_effect"].sum() - result.attrs["active_return"]) <= tolerance


class BrinsonTest(unittest.TestCase):
    """The classic textbook decomposition, verified by hand."""

    def setUp(self):
        self.result = brinson_attribution(PORTFOLIO, BENCHMARK)

    def test_each_effect_matches_the_hand_computed_value(self):
        by_group = self.result.set_index("group")
        for group, expected in (("科技", EXPECTED_TECH), ("金融", EXPECTED_FINANCE)):
            for column, value in expected.items():
                self.assertAlmostEqual(
                    by_group.loc[group, column], value, places=12, msg=f"{group}.{column}"
                )

    def test_the_three_effects_reconcile_to_the_active_return(self):
        self.assertAlmostEqual(self.result.attrs["active_return"], 0.01, places=12)
        self.assertTrue(reconciles(self.result))

    def test_the_returns_are_reported_alongside_the_decomposition(self):
        self.assertAlmostEqual(self.result.attrs["portfolio_return"], 0.08, places=12)
        self.assertAlmostEqual(self.result.attrs["benchmark_return"], 0.07, places=12)

    def test_rows_are_ranked_by_total_effect(self):
        effects = self.result["total_effect"].tolist()
        self.assertEqual(effects, sorted(effects, reverse=True))

    def test_groups_present_on_only_one_side_are_still_attributed(self):
        """An outer merge must not silently drop a one-sided group."""
        portfolio = pd.DataFrame(
            {"group": ["科技", "医药"], "weight": [0.6, 0.4], "return": [0.10, 0.20]}
        )
        result = brinson_attribution(portfolio, BENCHMARK)
        self.assertEqual(set(result["group"]), {"科技", "金融", "医药"})

    def test_reconciliation_survives_a_one_sided_group(self):
        portfolio = pd.DataFrame(
            {"group": ["科技", "医药"], "weight": [0.6, 0.4], "return": [0.10, 0.20]}
        )
        self.assertTrue(reconciles(brinson_attribution(portfolio, BENCHMARK)))

        benchmark = pd.DataFrame(
            {"group": ["科技", "医药"], "weight": [0.5, 0.5], "return": [0.08, 0.02]}
        )
        self.assertTrue(reconciles(brinson_attribution(PORTFOLIO, benchmark)))

    def test_weights_that_do_not_sum_to_one_do_not_reconcile(self):
        """Documented precondition: the identity needs both weight sums to be 1."""
        portfolio = pd.DataFrame(
            {"group": ["科技", "金融"], "weight": [0.3, 0.3], "return": [0.10, 0.05]}
        )
        result = brinson_attribution(portfolio, BENCHMARK)
        self.assertGreater(abs(result["total_effect"].sum() - result.attrs["active_return"]), 0.01)


class ValidationTest(unittest.TestCase):
    """Unusable input must fail loudly rather than yield a waterfall."""

    def test_a_non_frame_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "pandas DataFrame"):
            brinson_attribution([{"group": "A"}], BENCHMARK)

    def test_missing_columns_are_named(self):
        frame = pd.DataFrame({"group": ["A"], "weight": [1.0]})
        with self.assertRaisesRegex(ValueError, "portfolio missing columns: return"):
            brinson_attribution(frame, BENCHMARK)

    def test_invalid_rows_are_dropped_and_an_all_invalid_input_is_rejected(self):
        frame = pd.DataFrame(
            {"group": ["", "A", "B"], "weight": [0.5, "x", None], "return": [0.1, 0.2, 0.3]}
        )
        with self.assertRaisesRegex(ValueError, "portfolio has no valid rows"):
            brinson_attribution(frame, BENCHMARK)

    def test_a_negative_weight_is_rejected(self):
        frame = pd.DataFrame({"group": ["A"], "weight": [-0.1], "return": [0.1]})
        with self.assertRaisesRegex(ValueError, "weight must be non-negative"):
            brinson_attribution(frame, BENCHMARK)

    def test_a_zero_weight_group_is_rejected(self):
        """A zero-weight group would divide by zero when deriving its return."""
        frame = pd.DataFrame({"group": ["A", "B"], "weight": [1.0, 0.0], "return": [0.1, 0.05]})
        with self.assertRaisesRegex(ValueError, "zero-weight group"):
            brinson_attribution(frame, BENCHMARK)

    def test_blank_group_names_are_dropped(self):
        frame = pd.DataFrame({"group": ["  ", "A"], "weight": [0.5, 0.5], "return": [0.1, 0.05]})
        result = brinson_attribution(frame, BENCHMARK)
        # The benchmark's own groups survive the outer merge; the blank one does not.
        self.assertEqual(set(result["group"]), {"科技", "金融", "A"})


class HoldingContributionsTest(unittest.TestCase):
    def test_weights_and_returns_produce_ranked_contributions(self):
        holdings = pd.DataFrame(
            {"symbol": ["B", "A"], "weight": [0.25, 0.75], "return": [-0.01, 0.02]}
        )
        frame = holding_contributions(holdings)
        self.assertEqual(list(frame["symbol"]), ["A", "B"])
        self.assertAlmostEqual(frame.loc[0, "contribution"], 0.015, places=12)
        self.assertEqual(frame.loc[0, "role"], "贡献")
        self.assertEqual(frame.loc[1, "role"], "拖累")

    def test_market_value_derives_the_weight(self):
        holdings = pd.DataFrame(
            {"symbol": ["A", "B"], "market_value": [3000.0, 1000.0], "return": [0.02, -0.01]}
        )
        frame = holding_contributions(holdings)
        self.assertAlmostEqual(frame.loc[0, "weight"], 0.75, places=12)
        self.assertAlmostEqual(frame.loc[1, "weight"], 0.25, places=12)

    def test_market_value_respects_an_explicit_total_value(self):
        holdings = pd.DataFrame({"symbol": ["A"], "market_value": [500.0], "return": [0.02]})
        frame = holding_contributions(holdings, total_value=1000.0)
        self.assertAlmostEqual(frame.loc[0, "weight"], 0.5, places=12)

    def test_a_non_positive_total_value_is_rejected(self):
        holdings = pd.DataFrame({"symbol": ["A"], "market_value": [500.0], "return": [0.02]})
        for bad in (0.0, -1000.0):
            with self.assertRaisesRegex(ValueError, "total_value must be positive"):
                holding_contributions(holdings, total_value=bad)

    def test_without_a_weight_or_market_value_the_input_is_rejected(self):
        holdings = pd.DataFrame({"symbol": ["A"], "return": [0.02]})
        with self.assertRaisesRegex(ValueError, "requires weight or market_value"):
            holding_contributions(holdings)

    def test_a_negative_holding_weight_is_rejected(self):
        holdings = pd.DataFrame({"symbol": ["A"], "weight": [-0.5], "return": [0.02]})
        with self.assertRaisesRegex(ValueError, "holding weight must be non-negative"):
            holding_contributions(holdings)

    def test_invalid_rows_are_dropped_before_ranking(self):
        holdings = pd.DataFrame(
            {
                "symbol": ["", "A", "B", "C"],
                "weight": [0.5, 1.0, None, 1.0],
                "return": [0.1, "x", 0.2, 0.05],
            }
        )
        frame = holding_contributions(holdings)
        # A's non-numeric return and B's missing weight both drop out.
        self.assertEqual(list(frame["symbol"]), ["C"])

    def test_a_zero_contribution_still_counts_as_a_contribution(self):
        """贡献 is the inclusive bucket: exactly zero is not a drag."""
        holdings = pd.DataFrame(
            {"symbol": ["A", "B"], "weight": [0.5, 0.5], "return": [0.0, -0.01]}
        )
        frame = holding_contributions(holdings).set_index("symbol")
        self.assertEqual(frame.loc["A", "role"], "贡献")
        self.assertEqual(frame.loc["B", "role"], "拖累")

    def test_contributions_sum_to_the_portfolio_return(self):
        holdings = pd.DataFrame(
            {"symbol": ["A", "B"], "weight": [0.75, 0.25], "return": [0.02, -0.01]}
        )
        frame = holding_contributions(holdings)
        self.assertAlmostEqual(frame["contribution"].sum(), 0.0125, places=12)


class WrapperTest(unittest.TestCase):
    def test_the_wrapper_returns_both_views_and_the_active_return(self):
        holdings = pd.DataFrame({"symbol": ["A"], "weight": [1.0], "return": [0.10]})
        result = calculate_attribution(PORTFOLIO, BENCHMARK, holdings)
        self.assertIsInstance(result, AttributionResult)
        self.assertAlmostEqual(result.total_return, 0.08, places=12)
        self.assertAlmostEqual(result.benchmark_return, 0.07, places=12)
        self.assertAlmostEqual(result.active_return, 0.01, places=12)
        self.assertEqual(len(result.holding_contributions), 1)

    def test_holdings_are_optional(self):
        result = calculate_attribution(PORTFOLIO, BENCHMARK)
        self.assertTrue(result.holding_contributions.empty)
        self.assertAlmostEqual(result.active_return, 0.01, places=12)

    def test_this_module_never_blocks_on_its_own(self):
        """Blocking for missing benchmark composition happens at the call site."""
        self.assertEqual(calculate_attribution(PORTFOLIO, BENCHMARK).blockers, ())


if __name__ == "__main__":
    unittest.main()
