"""Acceptance for point-in-time peer comparison and the fund radar.

``analysis/peer_comparison.py`` scores a fund against its peers across five
dimensions for the screening UI. It had no acceptance coverage. Its defining
promise is "comparable 0-100 scores **without imputing missing metrics**":

* every dimension is a percentile rank inside the peer set, never a hardcoded
  value or a population average borrowed from elsewhere;
* the composite is a weighted average that stays ``NaN`` unless **all five**
  dimensions are available — a missing dimension blocks the fund instead of
  being quietly averaged away;
* structural problems (empty input, missing fund code, too few peers) surface
  as explicit blockers rather than a degraded score.

Pure in-memory frames. No database, network or credentials.
"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

import pandas as pd  # noqa: E402

from quant_assistant.analysis.peer_comparison import (  # noqa: E402
    PeerComparisonConfig,
    build_fund_radar,
    compare_peers,
)

THREE = {
    "symbol": ["A", "B", "C"],
    "annualized_return": [0.10, 0.20, 0.30],
    "max_drawdown": [-0.10, -0.30, -0.50],
    "volatility": [0.40, 0.30, 0.20],
    "total_fee_rate": [0.010, 0.015, 0.020],
    "tracking_error": [0.02, 0.03, 0.04],
    "avg_daily_volume": [100.0, 200.0, 300.0],
}
DIMENSION_COLUMNS = (
    "returns_score",
    "risk_score",
    "cost_score",
    "tracking_score",
    "liquidity_score",
)
RADAR_COLUMNS = (*DIMENSION_COLUMNS, "symbol", "eligible")


def compare(rows=THREE, **kwargs):
    return compare_peers(pd.DataFrame(rows), **kwargs)


def identical_pair(**overrides):
    """Two peers identical in every metric, for isolating a single dimension."""
    rows = {
        "symbol": ["A", "B"],
        "annualized_return": [0.20, 0.20],
        "max_drawdown": [-0.20, -0.20],
        "volatility": [0.30, 0.30],
        "total_fee_rate": [0.012, 0.012],
        "tracking_error": [0.02, 0.02],
        "avg_daily_volume": [100.0, 100.0],
    }
    rows.update(overrides)
    return rows


class ConfigTest(unittest.TestCase):
    def test_the_default_weights_sum_to_one(self):
        config = PeerComparisonConfig()
        total = (
            config.returns_weight
            + config.risk_weight
            + config.cost_weight
            + config.tracking_weight
            + config.liquidity_weight
        )
        self.assertAlmostEqual(total, 1.0, places=10)
        self.assertEqual(config.min_peers, 2)

    def test_invalid_values_are_rejected(self):
        cases = (
            {"min_peers": 1},
            {"min_peers": True},
            {"returns_weight": -0.1},
            {"cost_weight": float("nan")},
            {
                "returns_weight": 0.0,
                "risk_weight": 0.0,
                "cost_weight": 0.0,
                "tracking_weight": 0.0,
                "liquidity_weight": 0.0,
            },
        )
        for overrides in cases:
            with self.assertRaises(ValueError, msg=overrides):
                PeerComparisonConfig(**overrides)


class StructuralBlockersTest(unittest.TestCase):
    """Unusable input must produce an explicit blocker, not a score."""

    def test_a_non_frame_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "pandas DataFrame"):
            compare_peers([{"symbol": "A"}])

    def test_empty_peers_block(self):
        result = compare_peers(pd.DataFrame())
        self.assertEqual(result.blockers, ("同类基金数据为空",))
        self.assertTrue(result.frame.empty)
        self.assertTrue(result.radar.empty)

    def test_peers_without_a_fund_code_block(self):
        result = compare_peers(pd.DataFrame({"annualized_return": [1.0, 2.0]}))
        self.assertEqual(result.blockers, ("同类数据缺少基金代码",))

    def test_too_few_peers_block(self):
        result = compare(pd.DataFrame(THREE).head(1))
        self.assertEqual(result.blockers, ("同类基金至少需要2只",))

    def test_an_unknown_dimension_is_reported(self):
        result = compare(required_dimensions=("returns", "nope"))
        self.assertEqual(result.blockers, ("不支持的雷达维度：nope",))

    def test_global_blockers_are_deduplicated(self):
        result = compare(required_dimensions=("nope", "nope"))
        self.assertEqual(result.blockers, ("不支持的雷达维度：nope",))


class ScoreTest(unittest.TestCase):
    def test_scores_are_percentile_ranks_of_the_peer_set(self):
        frame = compare().frame.set_index("symbol")
        self.assertEqual(frame.loc["C", "returns_score"], 100.0)  # best return
        self.assertEqual(frame.loc["A", "returns_score"], 33.33)  # worst return
        self.assertEqual(frame.loc["A", "cost_score"], 100.0)  # cheapest
        self.assertEqual(frame.loc["C", "cost_score"], 33.33)  # priciest

    def test_a_less_negative_drawdown_ranks_higher(self):
        """Drawdown is scored so that -10% beats -50%, all else equal."""
        rows = identical_pair()
        rows["max_drawdown"] = [-0.10, -0.50]
        frame = compare(rows).frame.set_index("symbol")
        self.assertGreater(frame.loc["A", "risk_score"], frame.loc["B", "risk_score"])

    def test_lower_volatility_ranks_higher(self):
        """With everything else equal, the calmer fund wins the risk score."""
        rows = identical_pair()
        rows["volatility"] = [0.20, 0.40]
        frame = compare(rows).frame.set_index("symbol")
        self.assertGreater(frame.loc["A", "risk_score"], frame.loc["B", "risk_score"])

    def test_offsetting_risk_profiles_tie(self):
        """Best drawdown with worst volatility ties its mirror image."""
        frame = compare().frame.set_index("symbol")
        self.assertEqual(frame.loc["A", "risk_score"], frame.loc["C", "risk_score"])

    def test_the_risk_score_averages_only_the_supplied_columns(self):
        """Without drawdown the risk score still exists, built from volatility."""
        rows = {key: value for key, value in THREE.items() if key != "max_drawdown"}
        frame = compare(rows).frame.set_index("symbol")
        # With one risk column the score is that column's percentile rank.
        self.assertEqual(frame.loc["C", "risk_score"], 100.0)
        self.assertEqual(frame.loc["A", "risk_score"], 33.33)

    def test_the_composite_is_the_weighted_mean_of_all_five_dimensions(self):
        frame = compare().frame.set_index("symbol")
        config = PeerComparisonConfig()
        expected = (
            frame.loc["C", "returns_score"] * config.returns_weight
            + frame.loc["C", "risk_score"] * config.risk_weight
            + frame.loc["C", "cost_score"] * config.cost_weight
            + frame.loc["C", "tracking_score"] * config.tracking_weight
            + frame.loc["C", "liquidity_score"] * config.liquidity_weight
        ) / 1.0
        self.assertAlmostEqual(frame.loc["C", "composite_score"], expected, places=10)

    def test_identical_peers_score_identically(self):
        rows = identical_pair()
        frame = compare(rows).frame
        self.assertEqual(frame["composite_score"].nunique(), 1)
        self.assertTrue(frame["eligible"].all())


class FailClosedTest(unittest.TestCase):
    """A missing dimension must block, never be averaged away."""

    def test_a_missing_dimension_kills_the_composite_for_every_peer(self):
        rows = {key: value for key, value in THREE.items() if key != "avg_daily_volume"}
        result = compare(rows)
        frame = result.frame
        self.assertTrue(frame["composite_score"].isna().all())
        self.assertFalse(frame["eligible"].any())
        self.assertEqual(set(frame["status"]), {"blocked"})
        for blockers in frame["blockers"]:
            self.assertIn("liquidity证据缺失", blockers)

    def test_a_missing_dimension_leaves_the_other_scores_intact(self):
        """The unavailable dimension blocks the row; the others are still scored."""
        rows = {key: value for key, value in THREE.items() if key != "avg_daily_volume"}
        frame = compare(rows).frame
        self.assertTrue(frame["composite_score"].isna().all())
        # returns/risk/cost/tracking are still real percentile ranks.
        self.assertFalse(frame["returns_score"].isna().any())
        self.assertFalse(frame["risk_score"].isna().any())

    def test_one_peer_missing_a_dimension_is_blocked_while_others_stay_eligible(self):
        rows = dict(THREE)
        rows["avg_daily_volume"] = [100.0, 200.0, None]  # only C is missing it
        frame = compare(rows).frame.set_index("symbol")
        self.assertTrue(frame.loc["A", "eligible"])
        self.assertTrue(frame.loc["B", "eligible"])
        self.assertFalse(frame.loc["C", "eligible"])
        self.assertTrue(pd.isna(frame.loc["C", "composite_score"]))
        self.assertIn("liquidity证据缺失", frame.loc["C", "blockers"])

    def test_a_non_numeric_metric_is_treated_as_missing(self):
        rows = dict(THREE)
        rows["total_fee_rate"] = ["便宜", "贵", "中等"]
        frame = compare(rows).frame
        # Unparseable text coerces to NaN, so the cost dimension is unavailable.
        self.assertTrue(frame["cost_score"].isna().all())
        self.assertFalse(frame["eligible"].any())


class SelectionTest(unittest.TestCase):
    def test_eligible_funds_rank_first_then_by_composite(self):
        rows = dict(THREE)
        rows["avg_daily_volume"] = [100.0, 200.0, None]
        frame = compare(rows).frame
        self.assertEqual(list(frame["eligible"]), [True, True, False])
        eligible = frame.loc[frame["eligible"], "composite_score"]
        self.assertEqual(list(eligible), sorted(eligible, reverse=True))

    def test_ties_break_by_symbol(self):
        rows = dict(THREE)
        rows["annualized_return"] = [0.10, 0.10, 0.10]
        frame = compare(rows).frame
        self.assertEqual(list(frame["symbol"]), ["A", "B", "C"])

    def test_duplicate_codes_keep_the_last_disclosure(self):
        """The later row wins, so the survivor carries the newer metrics."""
        rows = dict(THREE)
        rows["symbol"] = ["A", "A", "B"]
        rows["annualized_return"] = [0.10, 0.30, 0.20]
        frame = compare(rows).frame.set_index("symbol")
        self.assertEqual(len(frame), 2)
        self.assertEqual(set(frame.index), {"A", "B"})
        # Under keep="last" the surviving A carries the 0.30 return, so it
        # outranks B. Keeping the first row would flip the ranking. With two
        # peers the percentile ranks are 100.0 and 50.0.
        self.assertEqual(frame.loc["A", "returns_score"], 100.0)
        self.assertEqual(frame.loc["B", "returns_score"], 50.0)

    def test_blank_codes_are_dropped(self):
        rows = dict(THREE)
        rows["symbol"] = ["", "  ", "B"]
        result = compare(rows)
        # Two blank codes leave a single peer, below the minimum.
        self.assertEqual(result.blockers, ("同类基金至少需要2只",))

    def test_documented_column_aliases_are_accepted(self):
        rows = dict(THREE)
        rows["fund_symbol"] = rows.pop("symbol")
        rows["收益率"] = rows.pop("annualized_return")
        rows["规模"] = [1, 2, 3]
        frame = compare(rows).frame
        # Composite ranking is unchanged: the best return still ranks first.
        self.assertEqual(list(frame["symbol"]), ["C", "B", "A"])
        self.assertEqual(frame.loc[0, "returns_score"], 100.0)

    def test_the_radar_carries_exactly_the_documented_columns(self):
        radar = compare().radar
        self.assertEqual(list(radar.columns), ["symbol", *DIMENSION_COLUMNS, "eligible"])

    def test_build_fund_radar_returns_the_radar_frame(self):
        result = compare()
        self.assertEqual(build_fund_radar(pd.DataFrame(THREE)).equals(result.radar), True)


class WeightSensitivityTest(unittest.TestCase):
    def test_changing_a_weight_moves_the_composite(self):
        rows = {key: value for key, value in THREE.items() if key != "avg_daily_volume"}
        rows["avg_daily_volume"] = [100.0, 200.0, 300.0]
        neutral = compare(rows).frame.set_index("symbol")
        returns_heavy = compare(
            rows,
            config=PeerComparisonConfig(
                returns_weight=1.0,
                risk_weight=0.0,
                cost_weight=0.0,
                tracking_weight=0.0,
                liquidity_weight=0.0,
            ),
        ).frame.set_index("symbol")
        self.assertNotAlmostEqual(
            neutral.loc["C", "composite_score"], returns_heavy.loc["C", "composite_score"]
        )
        # With returns weighted at 100%, the composite is exactly that rank.
        self.assertAlmostEqual(returns_heavy.loc["C", "composite_score"], 100.0, places=6)

    def test_weights_are_confined_to_non_negative_finite_values(self):
        for bad in (float("nan"), float("inf"), -0.5):
            with self.assertRaises(ValueError):
                PeerComparisonConfig(returns_weight=bad)


if __name__ == "__main__":
    unittest.main()
