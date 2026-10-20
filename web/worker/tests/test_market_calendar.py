"""Calendar evidence is market scoped and immutable across resident worker tasks."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from quant_assistant.providers.market_calendar import MarketCalendars
from quant_assistant.services.trading_calendar import TradingCalendar


def record(market="CN", holidays=None, revision="test-v1"):
    return {
        "market": market,
        "year": 2026,
        "source": "https://www.sse.com.cn/synthetic-test",
        "revision": revision,
        "holidays": ["2026-10-19"] if holidays is None else holidays,
    }


def payload(*records):
    return {"format_version": 1, "calendars": list(records)}


class MarketCalendarTests(unittest.TestCase):
    def test_builtin_is_shared_with_legacy_entry_but_never_imputed_to_other_market(self):
        calendars = MarketCalendars.builtin()
        self.assertFalse(calendars.trading_day(date(2026, 10, 5)))
        self.assertTrue(calendars.trading_day(date(2026, 10, 19)))
        self.assertTrue(calendars.trading_day(date(2026, 10, 5), "HK"))
        self.assertIsNone(calendars.trading_day(date(2028, 10, 5), "HK"))
        self.assertIsNone(calendars.trading_day(date(2027, 1, 4)))
        legacy = TradingCalendar().load(2026)
        self.assertEqual(
            legacy["holidays"], [day.isoformat() for day in sorted(calendars.load(2026).holidays)]
        )

    def test_authenticated_snapshot_overrides_builtin_and_keeps_markets_separate(self):
        calendars = MarketCalendars.from_payload(payload(record(), record("HK", ["2026-10-20"])))
        self.assertFalse(calendars.trading_day(date(2026, 10, 19)))
        self.assertTrue(calendars.trading_day(date(2026, 10, 19), "HK"))
        self.assertIsNone(calendars.trading_day(date(2026, 10, 19), "US"))
        self.assertIsNone(calendars.trading_day(date(2025, 12, 31)))
        self.assertEqual(calendars, MarketCalendars.from_payload(calendars.to_payload()))

    def test_invalid_or_conflicting_evidence_fails_closed(self):
        for value in (
            None,
            {},
            {"format_version": True, "calendars": [record()]},
            {"format_version": 2, "calendars": [record()]},
            payload(record(), record(revision="conflicting-v2")),
            payload(record(holidays=["2025-10-19"])),
            payload(record(holidays=["2026-02-30"])),
            payload(record(revision="")),
        ):
            with self.subTest(value=value):
                self.assertIsNone(
                    MarketCalendars.from_payload(value).trading_day(date(2026, 10, 19))
                )

    def test_parallel_tasks_keep_their_own_revision_and_missing_payload_stays_unknown(self):
        closed = MarketCalendars.from_payload(payload(record(revision="closed")))
        opened = MarketCalendars.from_payload(payload(record(holidays=[], revision="opened")))
        with ThreadPoolExecutor(max_workers=4) as workers:
            futures = [
                workers.submit(calendars.trading_day, date(2026, 10, 19))
                for calendars in (closed, opened) * 20
            ]
            self.assertEqual([future.result() for future in futures], [False, True] * 20)
        self.assertIsNone(MarketCalendars.from_payload(None).trading_day(date(2026, 10, 19)))


if __name__ == "__main__":
    unittest.main()
