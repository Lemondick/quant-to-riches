"""Worker op dca.smart_plan_backtest: valuation-aware DCA simulation."""

import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest_shared import charge_purchase_fee, default_purchase_schedule, xirr
from operations import CAPABILITIES, execute
from smart_dca_backtest_operations import execute_smart_dca, smart_dca_backtest
from worker_result import child_envelope


def _trading_days(start, count):
    days = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _price_series(days, drift):
    price = Decimal("100")
    series = []
    for day in days:
        series.append((day, price))
        price = (price * Decimal(str(drift))).quantize(Decimal("0.0001"))
    return series


class SmartDcaBacktestTests(unittest.TestCase):
    def setUp(self):
        self.days = _trading_days(date(2024, 1, 1), 400)
        self.start = self.days[0]
        self.end = self.days[-1]

    def test_op_is_registered(self):
        self.assertIn("dca.smart_plan_backtest", CAPABILITIES)

    def test_rising_market_halts(self):
        result = smart_dca_backtest(
            "000300",
            self.start,
            self.end,
            Decimal("1000"),
            {"low_pct": 30, "boost": "1.5", "high_pct": 70, "cut": "0.5", "halt_pct": 90},
            price_series=_price_series(self.days, 1.001),
        )
        self.assertEqual(result["valuation_basis"], "price_proxy")
        self.assertTrue(result["warnings"])
        contribs = result["contributions"]
        # early sessions: <60 obs -> neutral; later: percentile ~100 -> halt
        self.assertFalse(contribs[0]["paused"])
        self.assertEqual(contribs[0]["multiplier"], "1")
        halted = [c for c in contribs if c["paused"]]
        self.assertTrue(len(halted) > len(contribs) / 2)
        metrics = result["metrics"]
        self.assertEqual(Decimal(metrics["total_invested"]), Decimal("1000") * (len(contribs) - len(halted)))
        self.assertGreater(Decimal(metrics["final_value"]), 0)
        self.assertIsNotNone(metrics["xirr"])
        self.assertLessEqual(metrics["max_drawdown"], 0)
        self.assertEqual(len(result["curve"]), len(contribs) + 1)
        self.assertEqual(result["curve"][-1]["date"], self.end.isoformat())

    def test_falling_market_boosts(self):
        result = smart_dca_backtest(
            "000300",
            self.start,
            self.end,
            Decimal("1000"),
            None,
            price_series=_price_series(self.days, 0.999),
        )
        contribs = result["contributions"]
        boosted = [c for c in contribs if c["multiplier"] == "1.5"]
        self.assertTrue(len(boosted) > len(contribs) / 2)
        self.assertFalse(any(c["paused"] for c in contribs))
        # boosted months invest 1500
        self.assertEqual(
            Decimal(result["metrics"]["total_invested"]),
            Decimal("1000") * (len(contribs) - len(boosted)) + Decimal("1500") * len(boosted),
        )

    def test_pe_valuation_basis(self):
        pe_series = [(day, 30.0 - 0.01 * i) for i, day in enumerate(self.days)]
        result = smart_dca_backtest(
            "000300",
            self.start,
            self.end,
            Decimal("1000"),
            None,
            price_series=_price_series(self.days, 1.0),
            valuation_series=pe_series,
        )
        self.assertEqual(result["valuation_basis"], "pe_percentile")
        self.assertEqual(result["warnings"], [])
        boosted = [c for c in result["contributions"] if c["multiplier"] == "1.5"]
        self.assertTrue(boosted)

    def test_invalid_inputs(self):
        prices = _price_series(self.days, 1.0)
        with self.assertRaises(ValueError):
            smart_dca_backtest("000300", self.end, self.start, Decimal("1000"), None,
                               price_series=prices)
        with self.assertRaises(ValueError):
            smart_dca_backtest("000300", self.start, self.end, Decimal("0"), None,
                               price_series=prices)
        with self.assertRaises(ValueError):
            smart_dca_backtest("000300", self.start, self.end, Decimal("1000"),
                               {"low_pct": 80, "high_pct": 70, "halt_pct": 90},
                               price_series=prices)
        with self.assertRaises(ValueError):
            smart_dca_backtest("000300", self.start, self.end, Decimal("1000"), None,
                               price_series=[])
        outcome = child_envelope(
            lambda: execute_smart_dca("dca.smart_plan_backtest", {"index_symbol": "x"})
        )
        self.assertEqual(outcome["error_code"], "invalid_request")

    def test_execute_dispatch(self):
        try:
            result = execute(
                "dca.smart_plan_backtest",
                {
                    "index_symbol": "000300",
                    "start": self.start.isoformat(),
                    "end": self.end.isoformat(),
                    "base_amount": "1000",
                    "rules": {"low_pct": 30, "boost": 1.5, "high_pct": 70, "cut": 0.5, "halt_pct": 90},
                },
            )
        except Exception:  # noqa: BLE001 - public quote feed unreachable in CI
            self.skipTest("public quote feed unreachable")
            return
        self.assertIn("metrics", result)


class BacktestSharedTests(unittest.TestCase):
    def test_purchase_fee_caliber(self):
        # 10000 @ 0.15% external rate -> 14.98 (cents, HALF_UP)
        self.assertEqual(
            charge_purchase_fee(default_purchase_schedule(), Decimal("10000")),
            Decimal("14.98"),
        )

    def test_xirr(self):
        start = date(2024, 1, 1)
        rate = xirr([(start, Decimal("-1000")), (date(2025, 1, 1), Decimal("1100"))])
        self.assertAlmostEqual(rate, 0.1, places=3)
        self.assertIsNone(xirr([(start, Decimal("-1000"))]))
        self.assertIsNone(xirr([(start, Decimal("-1000")), (date(2025, 1, 1), Decimal("-500"))]))


if __name__ == "__main__":
    unittest.main()
