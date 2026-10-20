"""Explicit synthetic daily bars exercise verified public history contracts."""

import copy
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from public_charts import index_history

from quant_assistant.providers.index_registry import definitions, get_index
from quant_assistant.providers.market_calendar import MarketCalendars
from quant_assistant.providers.public_http import PublicHttpError

NOW = datetime.fromisoformat("2026-10-20T14:43:21+00:00")


def payload(row):
    mapping = row.history or row.quote
    if row.id == "cn.bse.50":
        return {
            "rc": 0,
            "data": {
                "code": "899050",
                "name": "北证50",
                "market": 0,
                "klines": ["2026-10-16,10,12,13,9,20,30", "2026-10-20,12,13,14,11,30,40"],
            },
        }
    if row.market == "JP":
        zone = ZoneInfo(row.timezone)
        return {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": {
                            "symbol": "^N225",
                            "shortName": "Nikkei 225",
                            "instrumentType": "INDEX",
                            "currency": "JPY",
                            "exchangeName": "OSA",
                            "exchangeTimezoneName": row.timezone,
                            "regularMarketTime": int(
                                datetime(2026, 10, 20, 15, 45, tzinfo=zone).timestamp()
                            ),
                        },
                        "timestamp": [
                            int(datetime(2026, 10, day, 9, tzinfo=zone).timestamp())
                            for day in (19, 20)
                        ],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [10, 12],
                                    "close": [12, 13],
                                    "high": [13, 14],
                                    "low": [9, 11],
                                    "volume": [0, 0],
                                }
                            ]
                        },
                    }
                ],
            }
        }
    qt = [""] * 40
    qt[:3] = [mapping.response_type, mapping.response_names[0], mapping.response_symbol]
    qt[30] = (
        "2026-10-20 10:42:59"
        if row.market == "US"
        else "2026/10/20 16:08:42"
        if row.market == "HK"
        else "20261020161400"
    )
    return {
        "code": 0,
        "data": {
            mapping.symbol: {
                "qt": {mapping.symbol: qt},
                "day": [
                    ["2026-10-16", "10", "12", "13", "9", "20"],
                    ["2026-10-20", "12", "13", "14", "11", "30"],
                ],
            }
        },
    }


def fetch(row, body=None, **kwargs):
    body = payload(row) if body is None else body
    return index_history(
        index_id=row.id,
        now=NOW,
        http_get=lambda *a, **k: SimpleNamespace(
            json=lambda: copy.deepcopy(body), raise_for_status=lambda: None
        ),
        **kwargs,
    )


class GlobalIndexHistoryTests(unittest.TestCase):
    def test_all_sixteen_exact_identities_have_independent_real_history_capabilities(self):
        for row in definitions():
            with self.subTest(id=row.id):
                result = fetch(row)
                self.assertEqual(
                    (result["id"], result["symbol"], result["market"], result["currency"]),
                    (row.id, row.exchange_code, row.market, row.currency),
                )
                self.assertEqual(result["source"], row.history.provider)
                self.assertEqual(len(result["items"]), 2)
                self.assertEqual(result["items"][-1]["close"], "13")
                self.assertEqual(result["source_symbol"], row.history.symbol)
                self.assertIsNone(result["source_time"])
                self.assertNotIn("volume", result["items"][-1])

    def test_wrong_code_name_namespace_type_or_missing_identity_is_rejected(self):
        row = get_index("us.sp500")
        for key, value in [(0, "100"), (1, "另一个指数"), (2, ".DJI"), (30, "2099-10-20 10:42:59")]:
            body = payload(row)
            body["data"][row.quote.symbol]["qt"][row.quote.symbol][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                fetch(row, body)
        body = payload(row)
        del body["data"][row.quote.symbol]["qt"]
        with self.assertRaises(ValueError):
            fetch(row, body)

    def test_bj_history_does_not_use_the_inadequate_single_tencent_bar(self):
        row = get_index("cn.bse.50")
        self.assertEqual(row.history.provider, "eastmoney")
        for key, value in [("code", "000001"), ("name", "另一指数"), ("market", 1)]:
            body = payload(row)
            body["data"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                fetch(row, body)

    def test_nikkei_is_cash_index_in_jpy_not_futures_or_unknown_exchange(self):
        row = get_index("jp.nikkei225")
        for key, value in [
            ("instrumentType", "FUTURE"),
            ("currency", "USD"),
            ("symbol", "NK=F"),
            ("exchangeName", "CME"),
            ("exchangeTimezoneName", "America/New_York"),
        ]:
            body = payload(row)
            body["chart"]["result"][0]["meta"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                fetch(row, body)

    def test_dates_and_ohlc_are_strict_with_no_forward_fill_or_silent_sort(self):
        row = get_index("cn.csi.300")
        for bars in [
            [["2026-10-16", "10", "20", "12", "9", "1"]],
            [["2026-10-16", "NaN", "12", "13", "9", "1"]],
            [["2026-10-16", True, "12", "13", "9", "1"]],
            [["2026-10-16", "0", "12", "13", "9", "1"]],
            [["2026-10-17", "10", "12", "13", "9", "1"]],
            [["2026-10-05", "10", "12", "13", "9", "1"]],
            [["2026-10-21", "10", "12", "13", "9", "1"]],
            [["2026-10-16", "10", "12", "13", "9", "1"]] * 2,
            [
                ["2026-10-20", "10", "12", "13", "9", "1"],
                ["2026-10-16", "10", "12", "13", "9", "1"],
            ],
        ]:
            body = payload(row)
            body["data"][row.quote.symbol]["day"] = bars
            with self.subTest(bars=bars), self.assertRaises(ValueError):
                fetch(row, body)

    def test_us_current_bar_is_partial_and_completed_cn_and_jp_are_not(self):
        us = fetch(get_index("us.sp500"))
        self.assertTrue(us["last_bar_partial"])
        self.assertTrue(us["items"][-1]["is_partial"])
        self.assertFalse(us["items"][0]["is_partial"])
        self.assertEqual(us["source_quote_time"], "2026-10-20T10:42:59-04:00")
        self.assertFalse(fetch(get_index("cn.csi.300"))["last_bar_partial"])
        self.assertFalse(fetch(get_index("jp.nikkei225"))["last_bar_partial"])
        self.assertTrue(fetch(get_index("cn.bse.50"))["last_bar_partial"])

    def test_empty_calendar_is_unknown_and_older_last_session_is_stale(self):
        row = get_index("us.sp500")
        self.assertEqual(fetch(row, calendar=MarketCalendars())["quality_status"], "unknown_time")
        body = payload(row)
        body["data"][row.quote.symbol]["day"].pop()
        result = fetch(row, body)
        self.assertEqual(result["quality_status"], "stale")
        self.assertTrue(result["stale"])

    def test_unknown_and_conflicting_identifiers_are_rejected_before_http(self):
        for kwargs in [
            {"index_id": "us.futures"},
            {"symbol": "600000"},
            {"symbol": "000001", "index_id": "cn.csi.300"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                index_history(**kwargs, http_get=lambda *a, **k: self.fail("unexpected HTTP"))

    def test_tencent_extra_current_bar_is_validated_before_bounding_to_640(self):
        row = get_index("us.sp500")
        days, day = [], date(2026, 10, 20)
        while len(days) < 642:
            if day.weekday() < 5:
                days.append(day.isoformat())
            day -= timedelta(days=1)
        body = payload(row)
        part = body["data"][row.history.symbol]
        part["day"] = [[day, "10", "12", "13", "9", "20"] for day in reversed(days[:641])]
        result = fetch(row, body, calendar=MarketCalendars())
        self.assertEqual(len(result["items"]), 640)
        self.assertEqual(result["items"][0]["date"], days[639])
        part["day"][0][1] = "NaN"
        with self.assertRaises(ValueError):
            fetch(row, body, calendar=MarketCalendars())
        part["day"] = [[day, "10", "12", "13", "9", "20"] for day in reversed(days)]
        with self.assertRaises(ValueError):
            fetch(row, body, calendar=MarketCalendars())

    def test_nikkei_missing_null_or_differently_sized_ohlc_arrays_are_rejected(self):
        row = get_index("jp.nikkei225")
        for values in ([10], [10, None], [10, float("inf")]):
            body = payload(row)
            body["chart"]["result"][0]["indicators"]["quote"][0]["open"] = values
            with self.subTest(values=values), self.assertRaises(ValueError):
                fetch(row, body)

    def test_failure_in_one_history_does_not_poison_the_other_identifiers(self):
        with self.assertRaises(PublicHttpError):
            index_history(
                index_id="jp.nikkei225",
                now=NOW,
                http_get=lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic")),
            )
        self.assertEqual(fetch(get_index("cn.csi.300"))["id"], "cn.csi.300")

    def test_no_source_clock_is_not_replaced_with_fetch_time(self):
        row = get_index("cn.csi.300")
        body = payload(row)
        body["data"][row.history.symbol]["qt"][row.history.symbol][30] = ""
        result = fetch(row, body)
        self.assertIsNone(result["source_quote_time"])
        self.assertIsNone(result["source_time"])
        self.assertTrue(result["last_bar_partial"])


if __name__ == "__main__":
    unittest.main()
