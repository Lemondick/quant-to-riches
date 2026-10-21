"""Worker op calendar.generate_next_year: structure + 2026 spot-checks."""

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calendar_generation import execute_calendar
from operations import CAPABILITIES, execute

import quant_assistant.providers.market_calendar as market_calendar_module


def builtin_2026(market):
    path = (
        Path(market_calendar_module.__file__).parent / "exchange_calendars.json"
    )
    payload = json.loads(path.read_text("utf-8"))
    for row in payload["calendars"]:
        if row["market"] == market and row["year"] == 2026:
            return {
                day
                for day in row["holidays"]
                if date.fromisoformat(day).weekday() < 5
            }
    raise AssertionError(f"no builtin 2026 calendar for {market}")


class CalendarGenerationTests(unittest.TestCase):
    def generate(self, market, year):
        return execute_calendar(
            "calendar.generate_next_year", {"market": market, "year": year}
        )

    def test_op_is_registered(self):
        self.assertIn("calendar.generate_next_year", CAPABILITIES)
        result = execute(
            "calendar.generate_next_year", {"market": "US", "year": 2027}
        )
        self.assertEqual(result["market"], "US")

    def test_response_shape(self):
        result = self.generate("CN", 2027)
        self.assertEqual(
            set(result),
            {"market", "year", "holidays", "source", "confidence", "notes"},
        )
        self.assertEqual(result["market"], "CN")
        self.assertEqual(result["year"], 2027)
        self.assertEqual(result["source"], "lunar_algorithm")
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["notes"])
        self.assertEqual(
            result["holidays"], sorted(set(result["holidays"]))
        )

    def test_source_and_confidence_enums(self):
        expected = {
            "CN": ("lunar_algorithm", "medium"),
            "US": ("nyse_rules", "high"),
            "JP": ("japan_law", "high"),
            "HK": ("hkex_rules", "high"),
        }
        for market, (source, confidence) in expected.items():
            result = self.generate(market, 2027)
            self.assertEqual(result["source"], source, market)
            self.assertEqual(result["confidence"], confidence, market)

    def test_holidays_are_weekdays_and_sane_count(self):
        for market in ("CN", "US", "JP", "HK"):
            result = self.generate(market, 2027)
            days = [date.fromisoformat(day) for day in result["holidays"]]
            self.assertTrue(
                all(day.weekday() < 5 for day in days), market
            )
            self.assertTrue(
                all(day.year == 2027 for day in days), market
            )
            # Sanity bounds: every market closes a handful of weekdays a year,
            # never anywhere near half of them.
            self.assertGreaterEqual(len(days), 4, market)
            self.assertLessEqual(len(days), 40, market)

    def test_cn_contains_anchor_dates(self):
        holidays = set(self.generate("CN", 2026)["holidays"])
        for anchor in ("2026-01-01", "2026-05-01", "2026-10-01"):
            self.assertIn(anchor, holidays)

    def test_cn_2026_matches_builtin_key_dates(self):
        # Spot-check the generated CN calendar against the SSE-sourced builtin:
        # Spring Festival block, Qingming, Dragon Boat, Mid-Autumn, National Day.
        holidays = set(self.generate("CN", 2026)["holidays"])
        builtin = builtin_2026("CN")
        for key in (
            "2026-02-17",  # 初一
            "2026-02-23",  # 调休顺延的周一
            "2026-04-06",  # 清明
            "2026-06-19",  # 端午
            "2026-09-25",  # 中秋
            "2026-10-01",
            "2026-10-07",
        ):
            self.assertIn(key, holidays, key)
            self.assertIn(key, builtin, key)
        # Full weekday-set equality for 2026: the heuristic reproduces the
        # official arrangement exactly this year.
        self.assertEqual(holidays, builtin)

    def test_us_jp_hk_2026_match_builtin(self):
        for market in ("US", "JP", "HK"):
            holidays = set(self.generate(market, 2026)["holidays"])
            self.assertEqual(holidays, builtin_2026(market), market)

    def test_invalid_payloads_raise(self):
        # KeyError (missing keys) and ValueError (bad values) both map to
        # invalid_request at the child_operation boundary.
        with self.assertRaises((KeyError, ValueError)):
            execute_calendar("calendar.generate_next_year", {})
        with self.assertRaises(ValueError):
            self.generate("XX", 2026)
        for bad_year in ("2026", 2026.0, True, None, 1999, 2101):
            with self.assertRaises(ValueError, msg=repr(bad_year)):
                self.generate("CN", bad_year)
        with self.assertRaises(ValueError):
            execute_calendar("calendar.other", {"market": "CN", "year": 2026})


if __name__ == "__main__":
    unittest.main()
