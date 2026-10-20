"""Synthetic, independently calculated OOS math and evidence-contract regressions."""

import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from math import prod, sqrt

import numpy as np
import pandas as pd

from quant_assistant.backtest.engine_components import BacktestRequest
from quant_assistant.backtest.metrics import PerformanceMetrics
from quant_assistant.backtest.walk_forward import (
    ValidationWindow,
    WalkForwardRequest,
    run_walk_forward,
)
from quant_assistant.reporting.backtest_report import (
    backtest_report_payload,
    render_backtest_markdown,
)


def summary(score=0.1):
    return PerformanceMetrics(score, score, 0.1, -0.01, score, 10, Decimal("99"), score, 0)


class Runner:
    def __init__(self, outputs):
        self.outputs = outputs

    def __call__(self, request):
        # Summary-only train/validation is sufficient for parameter selection.
        return self.outputs.get((request.start, request.end), summary(request.parameters["x"]))


def scenario(*return_lists):
    outputs, windows, benchmarks = {}, [], []
    for number, returns in enumerate(return_lists):
        start = date(2026, 3 + number * 3, 1)
        dates = pd.date_range(start, periods=len(returns) + 1)
        values = np.r_[1.0, np.cumprod(1 + np.asarray(returns, dtype=float))]
        nav = pd.DataFrame(
            {
                "trade_date": dates,
                "unit_nav": values,
                "total_value": values * 100,
                "external_cash_flow": 0,
                "fees": [Decimal("1")] * len(dates),
                "buy_amount": 1,
                "sell_amount": 0,
            }
        )
        benchmark = pd.DataFrame(
            {"trade_date": dates, "close": 100 * 1.005 ** np.arange(len(dates))}
        )
        window = ValidationWindow(
            start - timedelta(days=20),
            start - timedelta(days=15),
            start - timedelta(days=10),
            start - timedelta(days=5),
            dates[0].date(),
            dates[-1].date(),
        )
        windows.append(window)
        benchmarks.append(benchmark)
        outputs[(window.test_start, window.test_end)] = {
            "nav": nav,
            "benchmark": benchmark,
            "trading_dates": tuple(dates.date),
            "snapshot_ids": (100 + number,),
        }
    request = WalkForwardRequest(
        base_request=BacktestRequest(
            windows[0].train_start, windows[-1].test_end, Decimal("100"), "synthetic-oos"
        ),
        windows=tuple(windows),
        parameter_candidates=({"x": 0}, {"x": 1}),
        engine=Runner(outputs),
        benchmark=pd.concat(benchmarks, ignore_index=True),
    )
    return request, outputs


class OosMathTests(unittest.TestCase):
    def test_drawdown_accumulates_across_independent_fold_boundaries(self):
        request, _ = scenario([-0.1], [-0.1])
        result = run_walk_forward(request)
        self.assertAlmostEqual(-0.19, result.aggregate_metrics.max_drawdown)
        self.assertAlmostEqual(-0.19, result.aggregate_metrics.total_return)
        self.assertEqual((100, 101), result.snapshot_ids)
        self.assertTrue(all(fold.selected_parameters["x"] == 1 for fold in result.folds))

    def test_unequal_folds_recompute_global_statistics_from_all_returns(self):
        short = [-0.01, 0.02] * 5
        long = [-0.04, 0.06] * 20
        request, _ = scenario(short, long)
        result = run_walk_forward(request)
        returns = np.asarray(short + long)
        expected = {
            "total_return": prod(1 + returns) - 1,
            "annualized_return": prod(1 + returns) ** (252 / len(returns)) - 1,
            "annualized_volatility": float(returns.std(ddof=1)) * sqrt(252),
            "sharpe_zero_rate": float(returns.mean() / returns.std(ddof=1)) * sqrt(252),
            "benchmark_return": 1.005 ** len(returns) - 1,
        }
        for name, value in expected.items():
            with self.subTest(metric=name):
                self.assertAlmostEqual(value, getattr(result.aggregate_metrics, name))
        self.assertEqual(Decimal("52.00"), result.aggregate_metrics.total_fees)
        self.assertAlmostEqual(
            sum(fold.test_metrics.turnover for fold in result.folds),
            result.aggregate_metrics.turnover,
        )

    def test_each_fold_metrics_come_from_the_actual_nav_not_supplied_summary(self):
        request, outputs = scenario([-0.1])
        window = request.windows[0]
        outputs[(window.test_start, window.test_end)]["metrics"] = summary()
        result = run_walk_forward(request)
        self.assertAlmostEqual(-0.1, result.folds[0].test_metrics.total_return)
        self.assertEqual(Decimal("2.00"), result.folds[0].test_metrics.total_fees)

    def test_gap_is_disclosed_and_does_not_insert_zero_returns(self):
        request, _ = scenario([0.1, -0.05], [0.03])
        result = run_walk_forward(request)
        payload = backtest_report_payload(result)
        self.assertIn("aggregation", payload)
        aggregation = payload["aggregation"]
        self.assertEqual("concatenated_oos_returns", aggregation["method"])
        self.assertEqual("omit_unobserved_returns", aggregation["gap_policy"])
        self.assertEqual(3, aggregation["return_observations"])
        self.assertEqual(1, len(aggregation["excluded_intervals"]))
        self.assertEqual("sum_fold_actual_fees", aggregation["fees_basis"])
        self.assertEqual("sum_fold_two_sided_turnover", aggregation["turnover_basis"])
        self.assertEqual([2, 1], [len(fold["oos"]["returns"]) for fold in payload["folds"]])
        self.assertIn("不填零", render_backtest_markdown(result))
        self.assertIn("各折双边换手合计", render_backtest_markdown(result))

    def test_summary_only_test_output_cannot_manufacture_a_return_curve(self):
        request, outputs = scenario([0.1])
        window = request.windows[0]
        outputs[(window.test_start, window.test_end)] = summary()
        with self.assertRaisesRegex(ValueError, "oos_nav_required"):
            run_walk_forward(request)

    def test_legacy_manual_result_does_not_claim_verified_oos_or_cost_basis(self):
        request, _ = scenario([0.1])
        current = run_walk_forward(request)
        legacy = replace(
            current,
            folds=(replace(current.folds[0], oos=None),),
            aggregation_method="unknown",
            oos_gap_policy="unknown",
            oos_return_observations=0,
            research_only=False,
        )
        payload = backtest_report_payload(legacy)
        for key in ("method", "return_basis", "fees_basis", "turnover_basis"):
            with self.subTest(key=key):
                self.assertEqual("unknown", payload["aggregation"][key])
        self.assertTrue(payload["research_only"])
        self.assertIn("oos_aggregation_unverified", payload["limitations"])
        self.assertIn("聚合口径未确认", render_backtest_markdown(legacy))

    def test_missing_nav_or_benchmark_observation_is_rejected(self):
        for missing in ("nav", "benchmark"):
            request, outputs = scenario([0.1, 0.2])
            window = request.windows[0]
            output = outputs[(window.test_start, window.test_end)]
            output[missing] = output[missing].drop(index=1)
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "oos_.*dates"):
                run_walk_forward(request)

    def test_declared_trading_dates_must_match_actual_nav(self):
        request, outputs = scenario([0.1, 0.2])
        window = request.windows[0]
        output = outputs[(window.test_start, window.test_end)]
        output["trading_dates"] = output["trading_dates"][:-1]
        with self.assertRaisesRegex(ValueError, "oos_.*dates"):
            run_walk_forward(request)

    def test_missing_expected_session_declaration_is_not_guessed_from_prices(self):
        request, outputs = scenario([0.1, 0.2])
        window = request.windows[0]
        del outputs[(window.test_start, window.test_end)]["trading_dates"]
        with self.assertRaisesRegex(ValueError, "oos_trading_dates_required"):
            run_walk_forward(request)

    def test_one_close_cannot_be_promoted_to_a_daily_return(self):
        request, _ = scenario([])
        with self.assertRaisesRegex(ValueError, "insufficient_oos_observations"):
            run_walk_forward(request)

    def test_overlap_remains_rejected(self):
        request, _ = scenario([0.1], [0.2])
        request = replace(request, windows=(request.windows[0], request.windows[0]))
        with self.assertRaisesRegex(ValueError, "overlap"):
            run_walk_forward(request)


if __name__ == "__main__":
    unittest.main()
