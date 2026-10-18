"""Authenticated calendars must not leak between resident public jobs."""

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import public_operations  # noqa: E402

from quant_assistant.providers.market_feed import AkshareMarketFeedProvider  # noqa: E402


class WorkerCalendarContextTests(unittest.TestCase):
    def test_parallel_public_jobs_use_their_own_authoritative_calendar(self):
        public_operations._market_provider.cache_clear()
        self.addCleanup(public_operations._market_provider.cache_clear)
        seen = []

        def quotes(provider, *args):
            calendar = getattr(provider, "_calendar", None)
            seen.append(None if calendar is None else calendar.trading_day(date(2026, 10, 19)))
            return {
                "items": [],
                "unavailable": [],
                "observed_at": "2026-10-19T10:00:00+08:00",
                "source": "test",
                "stale": False,
            }

        def run(closed):
            snapshot = {
                "format_version": 1,
                "calendars": [
                    {
                        "market": "CN",
                        "year": 2026,
                        "source": "https://www.sse.com.cn/test",
                        "revision": str(closed),
                        "holidays": ["2026-10-19"] if closed else [],
                    }
                ],
            }
            return public_operations.execute_public(
                "market.quotes", {"stocks": [], "indices": [], "_calendars": snapshot}
            )

        with (
            patch.object(
                AkshareMarketFeedProvider, "market_quotes", autospec=True, side_effect=quotes
            ),
            ThreadPoolExecutor(max_workers=2) as workers,
        ):
            results = list(workers.map(run, (True, False)))
        self.assertCountEqual(seen, [False, True])
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
