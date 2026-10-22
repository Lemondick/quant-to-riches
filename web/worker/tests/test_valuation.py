"""Worker op valuation.percentiles: percentiles, ERP, temperature, degradation."""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations import CAPABILITIES, execute
from public_operations import IN_PROCESS_CAPABILITIES
from valuation_operations import (
    _temperature_label,
    bond_yield,
    execute_valuation,
    percentile_of,
    trailing_percentile,
    valuation_percentiles,
)
from worker_result import child_envelope


def _monthly_series(months, pe_start, pe_end, pb_start, pb_end):
    start = date(2016, 1, 4)
    points = []
    for i in range(months):
        day = date(start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1, 4)
        frac = i / (months - 1)
        pe = pe_start + (pe_end - pe_start) * frac
        pb = pb_start + (pb_end - pb_start) * frac
        points.append((day, pe, pb))
    return points


class _FakeSource:
    def __init__(self, series):
        self._series = series

    def history(self, symbol, market, years):
        return self._series.get(symbol)


class PercentileMathTests(unittest.TestCase):
    def test_percentile_of(self):
        self.assertEqual(percentile_of([1, 2, 3, 4, 5], 3), 60.0)
        self.assertEqual(percentile_of([1, 2, 3, 4, 5], 5), 100.0)
        self.assertEqual(percentile_of([1, 2, 3, 4, 5], 0.5), 0.0)
        self.assertEqual(percentile_of([2, 2, 2], 2), 100.0)

    def test_percentile_of_empty_raises(self):
        with self.assertRaises(ValueError):
            percentile_of([], 1.0)

    def test_trailing_percentile(self):
        base = date(2020, 1, 1)
        series = [(base + timedelta(days=30 * i), float(i)) for i in range(120)]
        at = base + timedelta(days=30 * 119)
        pct = trailing_percentile(series, at, 10)
        self.assertAlmostEqual(pct, 100.0)
        # too few observations -> None
        self.assertIsNone(trailing_percentile(series[:10], series[9][0], 10))
        # date not in series -> None
        self.assertIsNone(trailing_percentile(series, date(1999, 1, 1), 10))

    def test_temperature_labels(self):
        self.assertEqual(_temperature_label(None), "数据不足")
        self.assertEqual(_temperature_label(10), "冰点")
        self.assertEqual(_temperature_label(30), "偏冷")
        self.assertEqual(_temperature_label(50), "适中")
        self.assertEqual(_temperature_label(70), "偏热")
        self.assertEqual(_temperature_label(95), "过热")


class ValuationOpTests(unittest.TestCase):
    def setUp(self):
        self.source = _FakeSource(
            {
                "000300": _monthly_series(120, 10.0, 20.0, 1.0, 2.0),
                "SPX": _monthly_series(120, 25.0, 15.0, 3.0, 2.0),
            }
        )

    def test_op_is_registered_in_process(self):
        self.assertIn("valuation.percentiles", CAPABILITIES)
        self.assertIn("valuation.percentiles", IN_PROCESS_CAPABILITIES)

    def test_items_and_erp(self):
        result = valuation_percentiles(
            [{"symbol": "000300", "market": "CN"}],
            10,
            source=self.source,
            as_of=date(2026, 10, 21),
        )
        self.assertEqual(result["as_of"], "2026-10-21")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["symbol"], "000300")
        self.assertEqual(item["pe"], 20.0)
        self.assertEqual(item["pb"], 2.0)
        self.assertEqual(item["pe_pct"], 100.0)  # rising series, current is max
        self.assertEqual(item["pb_pct"], 100.0)
        self.assertAlmostEqual(item["erp"], 1 / 20.0 - bond_yield("CN"), places=6)
        self.assertEqual(result["warnings"], [])
        temp = result["temperature"]
        self.assertIn(temp["label"], ("偏热", "过热"))
        self.assertGreaterEqual(temp["score"], 60)

    def test_cheap_market_scores_cold(self):
        result = valuation_percentiles(
            [{"symbol": "SPX", "market": "US"}],
            10,
            source=self.source,
            as_of=date(2026, 10, 21),
        )
        item = result["items"][0]
        self.assertEqual(item["pe_pct"], 0.8)  # current is the minimum
        self.assertAlmostEqual(item["erp"], round(1 / 15.0 - bond_yield("US"), 4))
        # cheap PE but ERP heat still weighs in; label must be a known bucket
        self.assertIn(
            result["temperature"]["label"],
            ("冰点", "偏冷", "适中", "偏热", "过热"),
        )

    def test_no_data_degrades_without_error(self):
        result = valuation_percentiles(
            [{"symbol": "000300", "market": "CN"}], 10, as_of=date(2026, 10, 21)
        )
        self.assertEqual(result["items"], [])
        self.assertEqual(result["temperature"]["label"], "数据不足")
        self.assertIsNone(result["temperature"]["score"])
        self.assertTrue(result["warnings"])

    def test_short_history_skipped_with_warning(self):
        source = _FakeSource({"000300": _monthly_series(12, 10.0, 12.0, 1.0, 1.2)})
        result = valuation_percentiles([{"symbol": "000300", "market": "CN"}], 10, source=source)
        self.assertEqual(result["items"], [])
        self.assertTrue(any("不足" in warning for warning in result["warnings"]))

    def test_invalid_payloads(self):
        with self.assertRaises(ValueError):
            execute_valuation("valuation.percentiles", {"indices": []})
        with self.assertRaises(ValueError):
            execute_valuation(
                "valuation.percentiles", {"indices": [{"symbol": "x", "market": "XX"}]}
            )
        with self.assertRaises(ValueError):
            execute_valuation(
                "valuation.percentiles",
                {"indices": [{"symbol": "000300", "market": "CN"}], "years": 99},
            )
        with self.assertRaises(ValueError):
            execute_valuation("valuation.percentiles", {"indices": [{"market": "CN"}]})

    def test_invalid_request_envelope(self):
        outcome = child_envelope(
            lambda: execute_valuation("valuation.percentiles", {"indices": []})
        )
        self.assertEqual(outcome["error_code"], "invalid_request")

    def test_execute_dispatch(self):
        result = execute(
            "valuation.percentiles",
            {"indices": [{"symbol": "000300", "market": "CN"}]},
        )
        self.assertEqual(result["items"], [])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
