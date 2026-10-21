"""Worker op portfolio.backtest: multi-fund backtest with rebalancing."""

import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations import CAPABILITIES, execute
from portfolio_backtest_operations import execute_portfolio_backtest, portfolio_backtest
from worker_result import child_envelope


def _trading_days(start, count):
    days = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _nav_series(days, start_nav, drift):
    nav = Decimal(str(start_nav))
    series = []
    for day in days:
        series.append((day, nav))
        nav = (nav * Decimal(str(drift))).quantize(Decimal("0.0001"))
    return series


class PortfolioBacktestTests(unittest.TestCase):
    def setUp(self):
        self.days = _trading_days(date(2024, 1, 1), 260)
        self.start = self.days[0]
        self.end = self.days[-1]
        # A drifts up, B drifts down -> weights diverge -> rebalancing matters
        self.series = {
            "AAA": _nav_series(self.days, 1.0, 1.001),
            "BBB": _nav_series(self.days, 1.0, 0.9995),
        }
        self.funds = [
            {"code": "AAA", "weight": "0.6"},
            {"code": "BBB", "weight": "0.4"},
        ]

    def test_op_is_registered(self):
        self.assertIn("portfolio.backtest", CAPABILITIES)

    def test_period_rebalance(self):
        result = portfolio_backtest(
            self.funds,
            self.start,
            self.end,
            {"mode": "period", "period": "1M"},
            nav_series=self.series,
            initial_capital=Decimal("100000"),
        )
        events = result["rebalance_events"]
        # ~12 monthly anniversaries in a year
        self.assertGreaterEqual(len(events), 10)
        self.assertTrue(all(e["action"] == "rebalance_to_target" for e in events))
        self.assertTrue(all(e["reason"].startswith("period:1M") for e in events))
        dates = [e["date"] for e in events]
        self.assertEqual(dates, sorted(dates))
        metrics = result["metrics"]
        self.assertEqual(Decimal(metrics["total_invested"]), Decimal("100000"))
        self.assertGreater(Decimal(metrics["final_value"]), 0)
        self.assertIsNotNone(metrics["xirr"])
        self.assertLessEqual(metrics["max_drawdown"], 0)
        # curve is monthly sampled
        self.assertLess(len(result["curve"]), 20)
        self.assertEqual(result["curve"][-1]["date"], self.end.isoformat())

    def test_threshold_rebalance(self):
        result = portfolio_backtest(
            self.funds,
            self.start,
            self.end,
            {"mode": "threshold", "threshold_pct": 5},
            nav_series=self.series,
            initial_capital=Decimal("100000"),
        )
        events = result["rebalance_events"]
        self.assertTrue(events)
        self.assertTrue(all("threshold" in e["reason"] for e in events))

    def test_threshold_never_triggers_when_stable(self):
        flat = {
            "AAA": _nav_series(self.days, 1.0, 1.0),
            "BBB": _nav_series(self.days, 2.0, 1.0),
        }
        result = portfolio_backtest(
            self.funds,
            self.start,
            self.end,
            {"mode": "threshold", "threshold_pct": 5},
            nav_series=flat,
            initial_capital=Decimal("100000"),
        )
        self.assertEqual(result["rebalance_events"], [])

    def test_dca_cashflows(self):
        result = portfolio_backtest(
            self.funds,
            self.start,
            self.end,
            {"mode": "period", "period": "3M"},
            dca={"amount": "10000", "freq": "monthly"},
            nav_series=self.series,
            initial_capital=Decimal("100000"),
        )
        invested = Decimal(result["metrics"]["total_invested"])
        # 12 monthly inflows of 10000 + 100000 initial
        self.assertEqual(invested, Decimal("220000"))
        self.assertGreater(Decimal(result["metrics"]["final_value"]), 0)

    def test_weight_normalization(self):
        result = portfolio_backtest(
            [{"code": "AAA", "weight": "6"}, {"code": "BBB", "weight": "4"}],
            self.start,
            self.end,
            {"mode": "period", "period": "1M"},
            nav_series=self.series,
            initial_capital=Decimal("10000"),
        )
        self.assertEqual(Decimal(result["metrics"]["total_invested"]), Decimal("10000"))

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            portfolio_backtest([], self.start, self.end, {"mode": "period"},
                               nav_series=self.series)
        with self.assertRaises(ValueError):
            portfolio_backtest(self.funds, self.start, self.end, {"mode": "bogus"},
                               nav_series=self.series)
        with self.assertRaises(ValueError):
            portfolio_backtest(self.funds, self.start, self.end,
                               {"mode": "threshold"}, nav_series=self.series)
        with self.assertRaises(ValueError):
            portfolio_backtest(self.funds, self.end, self.start,
                               {"mode": "period"}, nav_series=self.series)
        with self.assertRaises(ValueError):
            portfolio_backtest(self.funds, self.start, self.end,
                               {"mode": "period"}, nav_series={"AAA": []})
        outcome = child_envelope(
            lambda: execute_portfolio_backtest("portfolio.backtest", {"funds": []})
        )
        self.assertEqual(outcome["error_code"], "invalid_request")

    def test_execute_dispatch(self):
        try:
            result = execute(
                "portfolio.backtest",
                {
                    "funds": self.funds,
                    "start": self.start.isoformat(),
                    "end": self.end.isoformat(),
                    "rebalance": {"mode": "period", "period": "1M"},
                },
            )
        except Exception:  # noqa: BLE001 - public fund feed unreachable in CI
            self.skipTest("public fund feed unreachable")
            return
        self.assertIn("rebalance_events", result)


if __name__ == "__main__":
    unittest.main()
