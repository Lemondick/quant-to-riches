"""Offline quality gates; no production upstream requests."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from threading import Lock
from time import sleep

import requests

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.data_quality import MemoryCache, metadata
from quant_assistant.providers.fund_detail import (
    FundNavPoint,
    PublicFundDetailProvider,
    _period_returns,
)
from quant_assistant.providers.fund_valuation import (
    LocalFundNavProvider,
    parse_eastmoney_estimate,
)
from quant_assistant.providers.market_feed import (
    AkshareMarketFeedProvider,
    _tencent_id,
    quote_conflict,
)
from quant_assistant.providers.public_http import PublicHttp, PublicHttpError

NOW = datetime(2026, 10, 18, 10, tzinfo=SHANGHAI_TZ)


class Response:
    def __init__(self, status=200, body=b'{"value":1}', content_type="application/json"):
        self.status_code, self.content = status, body
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError()


class TransportTests(unittest.TestCase):
    def test_redirects_denied_and_no_silent_html_or_empty_payloads(self):
        for response in (
            Response(301),
            Response(302),
            Response(body=b""),
            Response(body=b"<html>blocked</html>"),
            Response(content_type="text/html"),
        ):
            with (
                self.subTest(status=response.status_code, body=response.content),
                self.assertRaises(PublicHttpError),
            ):
                PublicHttp(lambda *a, response=response, **k: response, sleep=lambda _: None).get(
                    "https://qt.gtimg.cn/q=x"
                )

    def test_status_timeout_connection_retries_are_bounded_and_circuit_opens(self):
        for failure in (
            Response(403),
            Response(429),
            Response(500),
            requests.Timeout(),
            requests.ConnectionError(),
        ):
            count = []

            def get(*args, failure=failure, count=count, **kwargs):
                self.assertFalse(kwargs["allow_redirects"])
                count.append(1)
                if isinstance(failure, Exception):
                    raise failure
                return failure

            client = PublicHttp(get, sleep=lambda _: None)
            for _ in range(8):
                with self.assertRaises(PublicHttpError):
                    client.get("https://qt.gtimg.cn/q=x")
            self.assertLessEqual(len(count), 6)

    def test_success_and_html_only_when_explicitly_expected(self):
        response = Response()
        self.assertIs(
            PublicHttp(lambda *a, **k: response).get("https://qt.gtimg.cn/q=x"),
            response,
        )
        html = Response(body=b"<html>fees</html>", content_type="text/html")
        self.assertIs(
            PublicHttp(lambda *a, **k: html).get(
                "https://fund.eastmoney.com/fees", expected="html"
            ),
            html,
        )


class ClockAndCacheTests(unittest.TestCase):
    def test_closed_market_friday_time_is_valid_and_future_time_invalid(self):
        self.assertEqual(
            metadata("realtime_quote", "tencent", "2026-10-16T15:00:00+08:00", NOW)[
                "quality_status"
            ],
            "valid",
        )
        self.assertEqual(
            metadata("realtime_quote", "tencent", "2026-10-19T15:00:00+08:00", NOW)[
                "quality_status"
            ],
            "invalid",
        )
        self.assertEqual(
            metadata("realtime_quote", "sina", None, NOW)["quality_status"],
            "unknown_time",
        )
        # National Day closure uses the verified exchange calendar, not weekdays.
        holiday = NOW.replace(day=5)
        self.assertEqual(
            metadata("realtime_quote", "tencent", "2026-09-30T15:00:00+08:00", holiday)[
                "quality_status"
            ],
            "valid",
        )

    def test_date_precision_and_cache_time_stay_unchanged(self):
        item = metadata("official_nav", "eastmoney", "2026-10-16", NOW)
        self.assertEqual(item["updated_at"], "2026-10-16")
        self.assertIsNone(item["source_time"])
        cache = MemoryCache(capacity=2)
        first, _ = cache.read("nav", lambda: item, ttl=60)
        second, _ = cache.read("nav", lambda: self.fail("cache miss"), ttl=60)
        self.assertEqual(first, second)
        self.assertEqual(second["fetched_at"], NOW)
        cache.read("b", lambda: 2, ttl=60)
        cache.read("c", lambda: 3, ttl=60)
        self.assertEqual(len(cache), 2)

    def test_single_flight_and_stale_keeps_source_time(self):
        cache = MemoryCache(capacity=2)
        count, lock = [], Lock()

        def load():
            with lock:
                count.append(1)
            sleep(0.02)
            return {"updated_at": "2026-10-16"}

        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: cache.read("a", load, ttl=60), range(8)))
        self.assertEqual(len(count), 1)
        self.assertTrue(all(value[0]["updated_at"] == "2026-10-16" for value in values))
        cache.expire()

        def fail():
            raise ValueError("unavailable")

        value, stale = cache.read("a", fail, ttl=60)
        self.assertTrue(stale)
        self.assertEqual(value["updated_at"], "2026-10-16")


class ContractRegressionTests(unittest.TestCase):
    def test_profile_and_nav_fallbacks_record_actual_route(self):
        from unittest.mock import Mock

        provider = PublicFundDetailProvider(now=lambda: NOW)
        provider._json_data = Mock(side_effect=ValueError("invalid_primary"))
        provider._sina_basic = Mock(
            return_value={
                "fd_name": "fund",
                "_source": "sina",
                "_fetched_at": NOW.isoformat(),
            }
        )
        provider._extended = Mock(return_value={})
        provider.nav_history = Mock(
            return_value=(
                FundNavPoint(date(2026, 10, 16), Decimal(1), Decimal(2), None, "tencent"),
            )
        )
        provider._rules = Mock(return_value=((), None, {}))
        provider._optional_announcements = Mock(return_value=())
        detail = provider.detail("000001")
        self.assertTrue(detail.fallback_used)
        self.assertEqual(detail.fallback_reason, "ValueError")
        self.assertTrue(detail.nav_metadata["fallback_used"])
        self.assertEqual(detail.nav_metadata["source_family"], "tencent")

    def test_one_bad_sina_row_does_not_discard_other_instruments(self):
        provider, _ = self.quote_provider(primary_time="")
        original = provider._quote_http._get

        def get(url, **kwargs):
            response = original(url, **kwargs)
            if "hq.sinajs" in url:
                response.content += response.content.replace(b"sh510300", b"sh510500").replace(
                    b",1.01,", b",--,"
                )
            return response

        provider._quote_http._get = get
        result = provider.market_quotes(["510300", "510500"])
        self.assertEqual([row.symbol for row in result.items], ["510300"])
        self.assertEqual(result.unavailable, ("510500",))

    def test_empty_optional_failure_is_not_cached_and_previous_success_is_preserved(
        self,
    ):
        from quant_assistant.providers.base import ProviderUnavailable

        provider = PublicFundDetailProvider(now=lambda: NOW)
        with self.assertRaises(ProviderUnavailable):
            provider._part("rules:000001", lambda: ((), None, {}))
        value = (("fee",), None, {"purchase_status": "open"})
        self.assertEqual(provider._part("rules:000001", lambda: value), value)
        provider._parts.expire()
        self.assertEqual(provider._part("rules:000001", lambda: ((), None, {})), value)

    def test_unknown_year_calendar_gap_blocks_return_verification(self):
        import json

        rows = [
            {
                "x": int(datetime(2025, 12, day, tzinfo=SHANGHAI_TZ).timestamp() * 1000),
                "y": 1,
                "equityReturn": 0,
                "unitMoney": "",
            }
            for day in (22, 26)
        ]
        body = ('var fS_code="000001";var Data_netWorthTrend=' + json.dumps(rows) + ";").encode()
        nav = PublicFundDetailProvider(
            http_get=lambda *a, **k: Response(body=body), now=lambda: NOW
        )._nav_history("000001")
        self.assertFalse(nav[-1].return_verified)
        self.assertTrue(all(item.return_rate is None for item in _period_returns(nav)))

    def test_same_clock_conflict_is_detected_but_different_sessions_are_not_compared(
        self,
    ):
        provider, _ = self.quote_provider()
        row = provider.market_quotes(["510300"]).items[0]
        other = replace(row, source="sina", price=Decimal("1.20"))
        self.assertTrue(quote_conflict(row, other))
        self.assertFalse(
            quote_conflict(row, replace(other, source_time="2026-10-15T15:00:00+08:00"))
        )

    def test_nav_duplicate_future_zero_missing_and_action_gates(self):
        import json

        def point(day, value, rate=0, action=""):
            return {
                "x": int(datetime(2026, 10, day, tzinfo=SHANGHAI_TZ).timestamp() * 1000),
                "y": value,
                "equityReturn": rate,
                "unitMoney": action,
            }

        for rows in (
            [point(15, 1), point(15, 1)],
            [point(15, 1), point(19, 1)],
            [point(15, 1), point(16, 0)],
            [point(15, 1), None],
        ):

            def get(url, rows=rows, **kwargs):
                if "qt.gtimg" in url:
                    raise requests.ConnectionError()
                return Response(
                    body=(
                        'var fS_code="000001";var Data_netWorthTrend=' + json.dumps(rows) + ";"
                    ).encode()
                )

            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                PublicFundDetailProvider(http_get=get, now=lambda: NOW)._nav_history("000001")
        rows = [
            point(12, 1),
            point(14, 1.01, 1),
            point(16, 0.5, -50, "分红：每10份派现金元"),
        ]
        body = ('var fS_code="000001";var Data_netWorthTrend=' + json.dumps(rows) + ";").encode()
        nav = PublicFundDetailProvider(
            http_get=lambda *a, **k: Response(body=body), now=lambda: NOW
        )._nav_history("000001")
        self.assertIn("missing_trading_date", nav[1].validation_errors)
        self.assertIn("corporate_action_terms_unverified", nav[2].validation_errors)
        self.assertTrue(all(item.return_rate is None for item in _period_returns(nav)))

    def quote_provider(
        self, primary_time="20261016150000", backup_time="2026-10-16,15:00:00", **patch
    ):
        fields = [""] * 58
        for index, value in {
            1: "test",
            2: "510300",
            3: "1.01",
            4: "1",
            30: primary_time,
            31: ".01",
            32: "1",
        }.items():
            fields[index] = value
        for index, value in patch.items():
            fields[int(index.removeprefix("f"))] = value
        primary = ('v_sh510300="' + "~".join(fields) + '";').encode()
        sina = ["0"] * 33
        for i, value in {
            0: "test",
            2: "1",
            3: "1.01",
            30: backup_time.split(",")[0],
            31: backup_time.split(",")[1],
        }.items():
            sina[i] = value
        backup = ('var hq_str_sh510300="' + ",".join(sina) + '";').encode()
        calls = []

        def get(url, **kwargs):
            calls.append(url)
            return Response(body=primary if "qt.gtimg" in url else backup)

        return AkshareMarketFeedProvider(http_get=get, now=lambda: NOW), calls

    def test_fresh_quote_does_not_poll_backup_and_metadata_matches_identity(self):
        provider, calls = self.quote_provider()
        row = provider.market_quotes(["510300"]).items[0]
        self.assertEqual(len(calls), 1)
        self.assertEqual(row.instrument_type, "etf")
        self.assertEqual(row.quality_status, "valid")
        self.assertEqual(row.source_time, "2026-10-16T15:00:00+08:00")

    def test_stale_primary_fresh_backup_and_both_stale(self):
        provider, _ = self.quote_provider(primary_time="20261015150000")
        row = provider.market_quotes(["510300"]).items[0]
        self.assertEqual(row.source, "sina")
        self.assertTrue(row.fallback_used)
        self.assertEqual(row.fallback_reason, "stale")
        provider, _ = self.quote_provider(
            primary_time="20261015150000", backup_time="2026-10-15,15:00:00"
        )
        row = provider.market_quotes(["510300"]).items[0]
        self.assertEqual(row.quality_status, "stale")
        self.assertEqual(row.updated_at, "2026-10-15T15:00:00+08:00")

    def test_bad_quote_identity_time_and_percent_never_pass_as_valid_primary(self):
        for patch in (
            {"f2": "999999"},
            {"f3": "NaN"},
            {"f30": ""},
            {"f32": "100"},
            {"f3": "0"},
        ):
            provider, _ = self.quote_provider(**patch)
            row = provider.market_quotes(["510300"]).items[0]
            self.assertEqual(row.source, "sina")
            self.assertTrue(row.fallback_used)

    def test_tencent_nav_field_six_is_accumulated_nav_not_previous_nav(self):
        response = Response(
            body=b'v_jj000001="000001~name~0~0~~1.1060~3.6790~-0.0903~2026-10-16~";'
        )
        provider = PublicFundDetailProvider(http_get=lambda *a, **k: response, now=lambda: NOW)
        point = provider._tencent_nav_history("000001")[0]
        self.assertEqual(point.accumulated_nav, Decimal("3.6790"))

    def test_insufficient_history_and_unverified_action_cannot_generate_returns(self):
        one = [FundNavPoint(date(2026, 10, 16), Decimal(1), None, None, "tencent")]
        self.assertTrue(all(item.return_rate is None for item in _period_returns(one)))
        two = [
            FundNavPoint(date(2026, 10, 15), Decimal(2), None, None, "eastmoney"),
            FundNavPoint(date(2026, 10, 16), Decimal(1), None, None, "eastmoney"),
        ]
        self.assertTrue(all(item.return_rate is None for item in _period_returns(two)))

    def test_shanghai_etf_exchange_mapping(self):
        for symbol in ("510300", "510500", "510050", "588000"):
            self.assertEqual(_tencent_id(symbol, "stock"), "sh" + symbol)
        self.assertEqual(_tencent_id("159915", "stock"), "sz159915")

    def test_official_nav_never_occupies_estimate_fields(self):
        record = {
            "000001": {
                "observed_at": NOW.isoformat(),
                "points": [
                    {"nav_date": "2026-10-15", "unit_nav": "1", "source": "eastmoney"},
                    {
                        "nav_date": "2026-10-16",
                        "unit_nav": "1.01",
                        "source": "eastmoney",
                        "daily_return": "1",
                    },
                ],
            }
        }
        item = LocalFundNavProvider(record, now=lambda: NOW).estimate("000001")
        self.assertIsNone(item.estimate_nav)
        self.assertIsNone(item.change_rate)
        self.assertEqual(item.official_nav, Decimal("1.01"))
        self.assertEqual(item.updated_at, "2026-10-16")
        self.assertFalse(item.is_estimate)

    def test_estimate_identity_finite_percent_and_date_precision(self):
        good = {
            "fundcode": "000001",
            "gsz": "1.01",
            "dwjz": "1",
            "gszzl": "1",
            "gztime": "2026-10-16 15:00",
            "jzrq": "2026-10-15",
        }
        item = parse_eastmoney_estimate(good, "000001", now=NOW)
        self.assertEqual(item.updated_at, "2026-10-16T15:00+08:00")
        for patch in (
            {"fundcode": None},
            {"fundcode": "000002"},
            {"gsz": "NaN"},
            {"gsz": ""},
            {"gsz": "Infinity"},
            {"gszzl": "100"},
            {"gztime": "2026-10-16"},
            {"gztime": "2026-10-19 15:00"},
        ):
            with (
                self.subTest(patch=patch),
                self.assertRaises((ValueError, RuntimeError)),
            ):
                parse_eastmoney_estimate(good | patch, "000001", now=NOW)


if __name__ == "__main__":
    unittest.main()
