"""Synthetic index payloads test identity, clocks and isolation without network."""

import copy
import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from quant_assistant.providers.index_registry import definitions, get_index

NOW = datetime.fromisoformat("2026-10-20T10:01:00+08:00")


def tencent_line(row, **changes):
    fields = [""] * 40
    fields[0:5] = [
        row.quote.response_type,
        row.quote.response_names[0],
        row.quote.response_symbol,
        "100",
        "100",
    ]
    fields[30] = (
        "2026-10-19 16:00:00"
        if row.market == "US"
        else "2026/10/20 10:00:00"
        if row.market == "HK"
        else "20261020100000"
    )
    fields[31:33] = ["0", "0"]
    for index, value in changes.items():
        fields[int(index.removeprefix("f"))] = value
    return f'v_{row.quote.symbol}="' + "~".join(fields) + '";'


def yahoo_payload():
    zone = ZoneInfo("Asia/Tokyo")
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
                        "exchangeTimezoneName": "Asia/Tokyo",
                        "regularMarketTime": int(
                            datetime(2026, 10, 20, 11, tzinfo=zone).timestamp()
                        ),
                        "regularMarketPrice": 110,
                        "chartPreviousClose": 999,
                        "priceHint": 2,
                    },
                    "timestamp": [
                        int(datetime(2026, 10, day, 9, tzinfo=zone).timestamp()) for day in (19, 20)
                    ],
                    "indicators": {"quote": [{"close": [100, 110]}]},
                }
            ],
        }
    }


class SyntheticHttp:
    def __init__(self):
        self.now = NOW
        self.lines = {
            row.id: tencent_line(row) for row in definitions() if row.quote.provider == "tencent"
        }
        self.yahoo = yahoo_payload()
        self.failed = set()
        self.calls = []
        self.extra = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        provider = "yahoo" if "finance.yahoo.com" in url else "tencent"
        if provider in self.failed:
            raise OSError("synthetic source unavailable")
        if provider == "yahoo":
            body = copy.deepcopy(self.yahoo)
            return SimpleNamespace(json=lambda: body, public_fetched_at=self.now)
        return SimpleNamespace(
            content="\n".join([*reversed(list(self.lines.values())), *self.extra]).encode(
                "gb18030"
            ),
            public_fetched_at=self.now,
        )


def provider(http, **kwargs):
    from quant_assistant.providers.index_quotes import IndexQuoteProvider

    return IndexQuoteProvider(http=http, now=lambda: http.now, ttl_seconds=5, **kwargs)


class IndexQuoteTests(unittest.TestCase):
    def test_all_sixteen_are_associated_by_identity_in_registry_order(self):
        http = SyntheticHttp()
        batch = provider(http).fetch()
        self.assertEqual([row.id for row in batch.items], [row.id for row in definitions()])
        self.assertEqual(len(batch.statuses), 16)
        self.assertEqual(len(http.calls), 2)
        zero = next(row for row in batch.items if row.id == "cn.sse.composite")
        self.assertEqual(
            (zero.price, zero.change, zero.change_rate),
            (Decimal("100"), Decimal("0"), Decimal("0")),
        )
        self.assertEqual(zero.currency_basis, "registry_definition_and_provider_namespace")
        japan = next(row for row in batch.items if row.id == "jp.nikkei225")
        self.assertEqual(
            (japan.currency, japan.market, japan.market_timezone), ("JPY", "JP", "Asia/Tokyo")
        )

    def test_cash_nikkei_daily_change_uses_actual_prior_close_not_chart_baseline(self):
        batch = provider(SyntheticHttp()).fetch()
        row = next(row for row in batch.items if row.id == "jp.nikkei225")
        self.assertEqual((row.change, row.change_rate), (Decimal("10"), Decimal("10")))
        self.assertEqual(row.change_basis, "previous_daily_close")

    def test_one_wrong_code_name_or_namespace_only_invalidates_that_index(self):
        for changes in ({"f2": "600000"}, {"f1": "浦发银行"}, {"f0": "51"}):
            with self.subTest(changes=changes):
                http = SyntheticHttp()
                http.lines["cn.sse.composite"] = tencent_line(
                    get_index("cn.sse.composite"), **changes
                )
                batch = provider(http).fetch()
                self.assertEqual(len(batch.items), 15)
                self.assertNotIn("cn.sse.composite", {row.id for row in batch.items})
                status = next(row for row in batch.statuses if row.id == "cn.sse.composite")
                self.assertEqual(status.quality_status, "invalid")
                self.assertEqual(status.quality_reason, "identity_mismatch")

    def test_duplicate_rows_cannot_choose_a_price_by_response_order(self):
        http = SyntheticHttp()
        http.extra = [http.lines["cn.sse.composite"]]
        batch = provider(http).fetch()
        self.assertEqual(len(batch.items), 15)
        self.assertEqual(
            next(row for row in batch.statuses if row.id == "cn.sse.composite").quality_reason,
            "duplicate_provider_identity",
        )

    def test_extra_unrequested_security_does_not_become_an_index(self):
        http = SyntheticHttp()
        http.extra = ['v_sh600000="1~synthetic stock~600000~100~100";']
        batch = provider(http).fetch()
        self.assertEqual(len(batch.items), 16)
        self.assertNotIn("600000", {row.symbol for row in batch.items})

    def test_nonfinite_or_inconsistent_numbers_are_not_successful_quotes(self):
        for changes in (
            {"f3": "NaN"},
            {"f3": "0"},
            {"f31": "5"},
            {"f32": "3"},
            {"f31": ""},
            {"f4": "-1"},
        ):
            with self.subTest(changes=changes):
                http = SyntheticHttp()
                http.lines["cn.sse.composite"] = tencent_line(
                    get_index("cn.sse.composite"), **changes
                )
                batch = provider(http).fetch()
                self.assertEqual(len(batch.items), 15)
                self.assertEqual(
                    next(
                        row for row in batch.statuses if row.id == "cn.sse.composite"
                    ).quality_status,
                    "invalid",
                )

    def test_overseas_clocks_use_their_market_and_unknown_calendar_stays_unknown(self):
        from quant_assistant.providers.market_calendar import MarketCalendars

        batch = provider(SyntheticHttp(), calendar=MarketCalendars()).fetch()
        us = next(row for row in batch.items if row.id == "us.dowjones")
        self.assertEqual(us.source_time, "2026-10-19T16:00:00-04:00")
        self.assertEqual(us.trade_date, "2026-10-19")
        self.assertEqual(us.market_timezone, "America/New_York")
        self.assertEqual(us.quality_status, "unknown_time")
        self.assertEqual(us.quality_reason, "calendar_or_session_unavailable")
        hk = next(row for row in batch.items if row.id == "hk.hsi")
        self.assertEqual(hk.source_time, "2026-10-20T10:00:00+08:00")
        self.assertEqual(hk.market, "HK")

    def test_source_without_clock_does_not_use_receipt_time_as_observation(self):
        http = SyntheticHttp()
        http.lines["cn.sse.composite"] = tencent_line(get_index("cn.sse.composite"), f30="")
        batch = provider(http).fetch()
        row = next(row for row in batch.items if row.id == "cn.sse.composite")
        self.assertIsNone(row.source_time)
        self.assertIsNone(row.observed_at)
        self.assertEqual(row.fetched_at, NOW)
        self.assertEqual(row.quality_status, "unknown_time")
        self.assertFalse(row.trade_time_verified)

    def test_future_or_malformed_source_clock_is_invalid_without_affecting_others(self):
        for stamp in ("20261021100000", "20260230100000", "not-a-date"):
            with self.subTest(stamp=stamp):
                http = SyntheticHttp()
                http.lines["cn.sse.composite"] = tencent_line(
                    get_index("cn.sse.composite"), f30=stamp
                )
                batch = provider(http).fetch()
                self.assertEqual(len(batch.items), 15)
                self.assertEqual(
                    next(
                        row for row in batch.statuses if row.id == "cn.sse.composite"
                    ).quality_status,
                    "invalid",
                )

    def test_japanese_future_or_wrong_currency_zone_exchange_identity_is_rejected(self):
        for key, value in (
            ("symbol", "NKD=F"),
            ("instrumentType", "FUTURE"),
            ("currency", "USD"),
            ("exchangeTimezoneName", "America/Chicago"),
            ("exchangeName", "CME"),
            ("shortName", "Nikkei futures"),
        ):
            with self.subTest(key=key):
                http = SyntheticHttp()
                http.yahoo["chart"]["result"][0]["meta"][key] = value
                batch = provider(http).fetch()
                self.assertEqual(len(batch.items), 15)
                self.assertNotIn("jp.nikkei225", {row.id for row in batch.items})

    def test_one_provider_failure_keeps_the_other_fifteen(self):
        http = SyntheticHttp()
        http.failed.add("yahoo")
        batch = provider(http).fetch()
        self.assertEqual(len(batch.items), 15)
        self.assertEqual(
            next(row for row in batch.statuses if row.id == "jp.nikkei225").quality_status,
            "unavailable",
        )

    def test_invalid_json_is_distinguished_from_a_transport_outage(self):
        http = SyntheticHttp()
        original = http.get

        def malformed(url, **kwargs):
            response = original(url, **kwargs)
            if "finance.yahoo.com" in url:
                response.json = lambda: json.loads("synthetic malformed JSON")
            return response

        http.get = malformed
        batch = provider(http).fetch()
        self.assertEqual(len(batch.items), 15)
        self.assertEqual(
            next(row for row in batch.statuses if row.id == "jp.nikkei225").quality_status,
            "invalid",
        )

    def test_missing_or_invalid_refresh_preserves_only_that_ids_original_value_and_clocks(self):
        http = SyntheticHttp()
        source = provider(http)
        original = source.fetch()
        old = next(row for row in original.items if row.id == "cn.sse.composite")
        http.now += timedelta(seconds=10)
        http.lines.pop("cn.sse.composite")
        http.lines["cn.csi.300"] = tencent_line(
            get_index("cn.csi.300"), f3="111", f31="11", f32="11"
        )
        source.expire()
        changed = source.fetch()
        fallback = next(row for row in changed.items if row.id == "cn.sse.composite")
        self.assertEqual(
            (fallback.price, fallback.source_time, fallback.fetched_at),
            (old.price, old.source_time, old.fetched_at),
        )
        self.assertTrue(fallback.is_stale)
        self.assertTrue(fallback.cache_fallback)
        self.assertEqual(fallback.quality_status, "stale")
        other = next(row for row in changed.items if row.id == "cn.csi.300")
        self.assertEqual(other.price, Decimal("111"))
        self.assertFalse(other.cache_fallback)
        self.assertEqual(other.fetched_at, http.now)
        source.expire()
        http.lines["cn.sse.composite"] = tencent_line(
            get_index("cn.sse.composite"), f3="105", f31="5", f32="5"
        )
        recovered = source.fetch()
        self.assertEqual(
            next(row for row in recovered.items if row.id == old.id).price, Decimal("105")
        )


if __name__ == "__main__":
    unittest.main()
