"""The same exchange boundaries and clock cases are consumed by .NET tests."""

import json
import unittest
from datetime import datetime
from pathlib import Path

from quant_assistant.providers.data_quality import enrich_payload, latest_session, metadata
from quant_assistant.providers.market_calendar import MarketCalendars

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/fixtures/public-quality-cases.json").read_text(
        encoding="utf-8"
    )
)


class PublicQualityBoundariesTests(unittest.TestCase):
    def test_cached_quote_is_reclassified_at_read_time_without_advancing_source_clock(self):
        value = "2026-10-16T15:00:00+08:00"
        data = {
            "items": [
                {
                    "symbol": "000300",
                    "source": "synthetic",
                    "source_time": value,
                    "fetched_at": value,
                    "quality_status": "valid",
                }
            ]
        }
        row = enrich_payload(
            "market.quotes", data, as_of=datetime.fromisoformat("2026-10-19T10:00:00+08:00")
        )["items"][0]
        self.assertEqual("stale", row["quality_status"])
        self.assertEqual(value, row["source_time"])
        self.assertEqual(value, row["fetched_at"])

    def test_shared_session_boundaries(self):
        for case in FIXTURE["sessions"]:
            now = datetime.fromisoformat(case["now"])
            calendar = MarketCalendars.from_payload(
                {
                    "format_version": 1,
                    "calendars": []
                    if case["trading"] is None
                    else [
                        {
                            "market": "CN",
                            "year": now.year,
                            "source": "https://www.sse.com.cn/test",
                            "revision": "fixture",
                            "holidays": [now.date().isoformat()] if not case["trading"] else [],
                        }
                    ],
                }
            )
            with self.subTest(now=case["now"]):
                self.assertEqual(
                    case["phase"],
                    metadata("realtime_quote", "test", None, now, calendar=calendar)[
                        "market_session"
                    ],
                )

    def test_auction_requires_source_capability_and_keeps_stale_quote_stale(self):
        now = datetime.fromisoformat("2026-10-19T09:20:00+08:00")
        for supported, value, expected in (
            (True, "2026-10-19T09:20:00+08:00", "valid"),
            (False, "2026-10-19T09:20:00+08:00", "unavailable"),
            (True, "2026-10-16T15:00:00+08:00", "stale"),
            (False, "2026-10-16T15:00:00+08:00", "valid"),
        ):
            with self.subTest(supported=supported, value=value):
                result = metadata("realtime_quote", "test", value, now, auction_supported=supported)
                self.assertEqual(expected, result["quality_status"])
                self.assertEqual(value, result["source_time"])
                self.assertEqual(supported, result.get("auction_supported"))
        self.assertIsNone(latest_session(datetime.fromisoformat("2027-01-01T09:20:00+08:00")))

    def test_timestamp_cases(self):
        for case in FIXTURE["timestamps"]:
            with self.subTest(value=case["value"]):
                info = metadata(
                    "historical_quote",
                    "synthetic",
                    case["value"],
                    datetime.fromisoformat(case["now"]),
                )
                self.assertEqual(case["quality"], info["quality_status"])
                self.assertEqual(
                    case["value"] if case["quality"] == "valid" else None, info["source_time"]
                )


if __name__ == "__main__":
    unittest.main()
