"""Shared cash-market boundaries and holiday evidence, entirely offline."""

import json
import unittest
from datetime import date, datetime
from pathlib import Path

from quant_assistant.providers.data_quality import metadata
from quant_assistant.providers.market_calendar import (
    MarketCalendars,
    in_refresh_window,
    latest_session,
    market_session,
)

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/fixtures/global-market-sessions.json").read_text(
        "utf-8"
    )
)


class GlobalMarketSessionsTests(unittest.TestCase):
    def test_refresh_windows_include_own_closing_grace_and_exclude_lunch_holidays(self):
        for row in FIXTURES["refresh_windows"]:
            with self.subTest(row=row):
                self.assertEqual(
                    in_refresh_window(
                        datetime.fromisoformat(row["now"]), MarketCalendars.builtin(), row["market"]
                    ),
                    row["active"],
                )

    def test_shared_full_half_holiday_and_dst_sessions(self):
        calendars = MarketCalendars.builtin()
        for row in FIXTURES["sessions"]:
            with self.subTest(row=row):
                now = datetime.fromisoformat(row["now"])
                self.assertEqual(market_session(now, calendars, row["market"]), row["phase"])
                self.assertEqual(
                    latest_session(now, calendars, row["market"]), date.fromisoformat(row["latest"])
                )

    def test_shared_quality_never_uses_chinese_auction_or_closing_clock_overseas(self):
        for row in FIXTURES["quality"]:
            with self.subTest(row=row):
                result = metadata(
                    "realtime_quote",
                    "synthetic",
                    row["source"],
                    datetime.fromisoformat(row["now"]),
                    market=row["market"],
                    instrument_type="index",
                )
                self.assertEqual(result["quality_status"], row["status"])
                self.assertEqual(result["source_time"], row["source"])

    def test_unknown_market_year_and_calendar_do_not_infer_another_market(self):
        now = datetime.fromisoformat("2026-10-20T10:00:00+08:00")
        for market, calendars in [("GB", MarketCalendars.builtin()), ("HK", MarketCalendars())]:
            self.assertEqual(market_session(now, calendars, market), "unknown")
            self.assertIsNone(latest_session(now, calendars, market))
        self.assertEqual(
            market_session(now.replace(year=2028), MarketCalendars.builtin(), "HK"), "unknown"
        )
        self.assertIsNone(
            latest_session(
                datetime.fromisoformat("2026-01-01T08:00:00-05:00"), MarketCalendars.builtin(), "US"
            )
        )

    def test_builtin_holidays_are_exchange_specific_and_packaged(self):
        calendars = MarketCalendars.builtin()
        for market in ("CN", "HK", "US", "JP"):
            year = calendars.load(2026, market)
            self.assertIsNotNone(year)
            self.assertTrue(year.source.startswith("https://"))
            self.assertEqual(
                MarketCalendars.from_payload(calendars.to_payload()).load(2026, market), year
            )


if __name__ == "__main__":
    unittest.main()
