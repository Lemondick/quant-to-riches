"""Worker ops risk.metrics / risk.stress_test: methodology + boundaries."""

import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations import CAPABILITIES, execute  # noqa: E402
from risk_operations import (  # noqa: E402
    MIN_POINTS,
    RISK_CAPABILITIES,
    STRESS_SCENARIOS,
    execute_risk,
    normalize_nav_series,
    risk_metrics,
    stress_test,
)


def _series(values, start=date(2026, 1, 5)):
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "nav": value}
        for i, value in enumerate(values)
    ]


def _expected_var95(returns):
    ordered = sorted(returns)
    rank = (len(ordered) - 1) * 0.05
    low, frac = math.floor(rank), rank - math.floor(rank)
    return max(0.0, -(ordered[low] * (1 - frac) + ordered[low + 1] * frac))


class NormalizeTests(unittest.TestCase):
    def test_accepts_plain_numbers(self):
        points = normalize_nav_series([1.0, 1.1, 1.2])
        self.assertEqual([nav for _, nav in points], [1.0, 1.1, 1.2])

    def test_accepts_pairs_and_sorts_by_date(self):
        points = normalize_nav_series(
            [("2026-02-01", 1.2), ("2026-01-01", 1.0)]
        )
        self.assertEqual(points[0][0], date(2026, 1, 1))

    def test_rejects_empty_and_non_positive(self):
        with self.assertRaises(ValueError):
            normalize_nav_series([])
        with self.assertRaises(ValueError):
            normalize_nav_series([1.0, 0.0])


class RiskMetricsTests(unittest.TestCase):
    def test_op_is_registered(self):
        self.assertIn("risk.metrics", CAPABILITIES)
        self.assertIn("risk.stress_test", CAPABILITIES)
        self.assertIn("risk.metrics", RISK_CAPABILITIES)

    def test_metrics_match_independent_computation(self):
        values = [1.0]
        for i in range(1, 60):
            values.append(values[-1] * (1 + 0.002 * math.sin(i) - 0.001))
        result = risk_metrics(_series(values))
        returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
        self.assertAlmostEqual(result["var95"], round(_expected_var95(returns), 6))
        self.assertAlmostEqual(
            result["ann_volatility"], round(stdev(returns) * math.sqrt(252), 6)
        )
        self.assertAlmostEqual(
            result["sharpe"],
            round((mean(returns) * 252 - 0.02) / (stdev(returns) * math.sqrt(252)), 6),
        )
        self.assertEqual(result["n_points"], 60)
        self.assertEqual(result["period_days"], 59)
        self.assertIn("历史模拟法", result["method"])
        self.assertFalse(result["warnings"])

    def test_max_drawdown_dates(self):
        # Peak at day 10 (nav 2.0), trough at day 20 (nav 1.0) -> 50% drawdown.
        values = [1.0 + 0.1 * i for i in range(11)]  # 1.0 .. 2.0
        values += [2.0 - 0.1 * i for i in range(1, 11)]  # .. 1.0
        values += [1.0 + 0.05 * i for i in range(1, 20)]  # recovery, never exceeds 2.0
        result = risk_metrics(_series(values, date(2026, 3, 1)))
        self.assertAlmostEqual(result["max_drawdown"], 0.5)
        self.assertEqual(result["max_drawdown_peak_date"], "2026-03-11")
        self.assertEqual(result["max_drawdown_trough_date"], "2026-03-21")
        self.assertIsNotNone(result["calmar"])

    def test_flat_series_sharpe_is_none(self):
        result = risk_metrics(_series([1.0] * 40))
        self.assertEqual(result["ann_volatility"], 0.0)
        self.assertIsNone(result["sharpe"])
        self.assertIsNone(result["calmar"])
        self.assertEqual(result["max_drawdown"], 0.0)

    def test_insufficient_points_returns_nulls_with_warning(self):
        result = risk_metrics(_series([1.0] * (MIN_POINTS - 1)))
        for key in ("var95", "max_drawdown", "ann_volatility", "sharpe", "calmar"):
            self.assertIsNone(result[key])
        self.assertTrue(any("29" in warning for warning in result["warnings"]))

    def test_single_point_returns_nulls(self):
        result = risk_metrics([1.5])
        self.assertIsNone(result["var95"])
        self.assertTrue(result["warnings"])

    def test_empty_series_is_invalid_request(self):
        with self.assertRaises(ValueError):
            risk_metrics([])

    def test_period_days_slices_recent_window(self):
        result = risk_metrics(_series([1.0 + 0.001 * i for i in range(60)]), period_days=30)
        self.assertEqual(result["n_points"], 30)

    def test_bad_period_days_rejected(self):
        with self.assertRaises(ValueError):
            risk_metrics(_series([1.0] * 40), period_days=0)


class StressTestTests(unittest.TestCase):
    def test_three_hardcoded_scenarios(self):
        self.assertEqual(len(STRESS_SCENARIOS), 3)
        names = [scenario["name"] for scenario in STRESS_SCENARIOS]
        self.assertTrue(any("2015" in name for name in names))
        self.assertTrue(any("2020" in name for name in names))
        self.assertTrue(any("2022" in name for name in names))
        for scenario in STRESS_SCENARIOS:
            self.assertLess(scenario["index_drop_pct"], 0)
            self.assertIn("interval", scenario)
            self.assertIn("source_note", scenario)

    def test_loss_scales_with_beta_and_value(self):
        result = stress_test(portfolio_value=100000.0, beta=1.0)
        first = result["scenarios"][0]
        self.assertAlmostEqual(first["est_loss_pct"], first["index_drop_pct"])
        self.assertAlmostEqual(first["est_loss_amount"], 100000.0 * first["index_drop_pct"], places=2)
        half = stress_test(portfolio_value=100000.0, beta=0.5)["scenarios"][0]
        self.assertAlmostEqual(half["est_loss_pct"], first["index_drop_pct"] * 0.5)
        self.assertIn("disclaimer", result)
        self.assertIn("beta", result["disclaimer"])

    def test_missing_value_gives_null_amount_with_warning(self):
        result = stress_test()
        self.assertTrue(all(s["est_loss_amount"] is None for s in result["scenarios"]))
        self.assertTrue(result["warnings"])

    def test_invalid_beta_rejected(self):
        with self.assertRaises(ValueError):
            stress_test(portfolio_value=1.0, beta=-1.0)


class ExecuteRiskTests(unittest.TestCase):
    def test_dispatch_metrics_via_registry(self):
        result = execute("risk.metrics", {"nav_series": _series([1.0 + 0.001 * i for i in range(40)])})
        self.assertIn("sharpe", result)

    def test_dispatch_stress_via_registry(self):
        result = execute("risk.stress_test", {"portfolio_value": 50000.0})
        self.assertEqual(len(result["scenarios"]), 3)

    def test_missing_inputs_rejected(self):
        with self.assertRaises(ValueError):
            execute_risk("risk.metrics", {})

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            execute_risk("risk.nope", {"nav_series": [1.0] * 40})


if __name__ == "__main__":
    unittest.main()
