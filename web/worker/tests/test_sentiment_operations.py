"""Worker op sentiment.fetch: sources, per-source degradation, no fabrication."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402
import sentiment_operations  # noqa: E402
from operations import CAPABILITIES, execute  # noqa: E402
from sentiment_operations import (  # noqa: E402
    SENTIMENT_CAPABILITIES,
    execute_sentiment,
    fetch_sentiment,
)


class _FakeResponse:
    def __init__(self, json_payload=None, text_payload=""):
        self._json = json_payload
        self.text = text_payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


_QUOTES = {
    "1.000001": {"rc": 0, "data": {"f48": 642751560386.8, "f168": 0.78}},
    "0.399001": {"rc": 0, "data": {"f48": 812345678901.2, "f168": 1.23}},
}

_BREADTH = {
    "rc": 0,
    "data": {
        "total": 2,
        "diff": [
            {"f104": 840, "f105": 1405, "f106": 112},
            {"f104": 1127, "f105": 1687, "f106": 122},
        ],
    },
}

_MARGIN = 'jsonpCallback1({"pageHelp":{"data":[{"opDate":"20261020","rzye":1257978189862}]}});'


def _fake_http_get(fail=()):
    def http_get(url, params=None, headers=None, timeout=None):
        for marker in fail:
            if marker in url:
                raise requests.ConnectionError("boom")
        if "stock/get" in url:
            return _FakeResponse(json_payload=_QUOTES[params["secid"]])
        if "ulist.np" in url:
            return _FakeResponse(json_payload=_BREADTH)
        if "queryMargin" in url:
            return _FakeResponse(text_payload=_MARGIN)
        raise AssertionError(f"unexpected url {url}")

    return http_get


class _FakeTracker:
    def __init__(self):
        self.records = []

    def record(self, source, *, ok, error=None, at=None):
        self.records.append({"source": source, "ok": ok, "error": error})


def _names(result):
    return [item["name"] for item in result["items"]]


class TestCapabilities(unittest.TestCase):
    def test_declares_sentiment_fetch(self):
        self.assertIsInstance(SENTIMENT_CAPABILITIES, frozenset)
        self.assertIn("sentiment.fetch", SENTIMENT_CAPABILITIES)

    def test_registered_in_dispatch_chain(self):
        self.assertIn("sentiment.fetch", CAPABILITIES)
        with mock.patch.object(sentiment_operations.requests, "get", _fake_http_get()):
            result = execute("sentiment.fetch", {"markets": ["CN"]})
        self.assertTrue(result["items"])
        self.assertIn("两市成交额", _names(result))


class TestFetch(unittest.TestCase):
    def test_full_success(self):
        result = fetch_sentiment(["CN"], http_get=_fake_http_get(), failure_tracker=None)
        names = _names(result)
        self.assertIn("两市成交额", names)
        self.assertIn("上证换手率", names)
        self.assertIn("深证换手率", names)
        self.assertIn("A股上涨家数", names)
        self.assertIn("A股下跌家数", names)
        self.assertIn("A股涨跌家数比", names)
        self.assertIn("融资余额（沪市）", names)
        by_name = {item["name"]: item for item in result["items"]}
        self.assertAlmostEqual(by_name["两市成交额"]["value"], 14550.97, places=2)
        self.assertEqual(by_name["两市成交额"]["unit"], "亿元")
        self.assertAlmostEqual(by_name["融资余额（沪市）"]["value"], 12579.78, places=2)
        self.assertEqual(by_name["融资余额（沪市）"]["updated_at"], "2026-10-20")
        self.assertEqual(by_name["A股上涨家数"]["value"], 1967)
        self.assertEqual(by_name["A股下跌家数"]["value"], 3092)
        self.assertAlmostEqual(by_name["A股涨跌家数比"]["value"], 0.6362, places=4)
        self.assertAlmostEqual(by_name["上证换手率"]["value"], 0.78)
        for item in result["items"]:
            for key in ("name", "value", "unit", "updated_at", "source"):
                self.assertIn(key, item)
        # 深市融资余额无合规源：如实声明缺失
        self.assertIn("warning", result)
        self.assertIn("深市融资余额", result["warning"])

    def test_single_source_failure_degrades_only_its_metric(self):
        result = fetch_sentiment(
            ["CN"], http_get=_fake_http_get(fail=("stock/get",)), failure_tracker=None
        )
        names = _names(result)
        self.assertNotIn("两市成交额", names)
        self.assertNotIn("上证换手率", names)
        self.assertNotIn("深证换手率", names)
        # 其他指标仍在
        self.assertIn("A股涨跌家数比", names)
        self.assertIn("融资余额（沪市）", names)
        self.assertIn("warning", result)
        self.assertTrue(result["items"])

    def test_all_sources_down_returns_no_source_warning(self):
        result = fetch_sentiment(
            ["CN"],
            http_get=_fake_http_get(fail=("stock/get", "ulist.np", "queryMargin")),
            failure_tracker=None,
        )
        self.assertEqual(result["items"], [])
        self.assertEqual(result["warning"], "数据源未接入")

    def test_never_fabricates_margin(self):
        # 融资余额来源挂掉 → 不出现任何融资余额条目，不拿沪市冒充两市
        result = fetch_sentiment(
            ["CN"], http_get=_fake_http_get(fail=("queryMargin",)), failure_tracker=None
        )
        names = _names(result)
        self.assertFalse([name for name in names if "融资余额" in name])
        self.assertIn("warning", result)

    def test_failure_tracker_records_outcomes(self):
        tracker = _FakeTracker()
        fetch_sentiment(
            ["CN"],
            http_get=_fake_http_get(fail=("queryMargin",)),
            failure_tracker=tracker,
        )
        by_source = {record["source"]: record for record in tracker.records}
        self.assertTrue(by_source["push2delay.eastmoney.com"]["ok"])
        self.assertFalse(by_source["query.sse.com.cn"]["ok"])
        self.assertEqual(by_source["query.sse.com.cn"]["error"], "ConnectionError")

    def test_unknown_source_skipped_with_warning(self):
        result = fetch_sentiment(
            ["CN"],
            sources=["eastmoney_index_quotes", "nope"],
            http_get=_fake_http_get(),
            failure_tracker=None,
        )
        self.assertTrue(any("nope" in w for w in result["warnings"]))
        self.assertIn("两市成交额", _names(result))

    def test_unsupported_market_has_no_source(self):
        result = fetch_sentiment(["US"], http_get=_fake_http_get(), failure_tracker=None)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["warning"], "数据源未接入")

    def test_bad_payload_rejected(self):
        with self.assertRaises(ValueError):
            fetch_sentiment([], http_get=_fake_http_get(), failure_tracker=None)
        with self.assertRaises(ValueError):
            execute_sentiment("sentiment.fetch", "not-a-mapping")
        with self.assertRaises(ValueError):
            execute_sentiment("sentiment.other", {})

    def test_execute_sentiment_uses_mapping_access_safely(self):
        # payload.get("markets", ["CN"]) 默认 CN；此处只验证参数校验路径，
        # 真实抓取走 http_get 注入的 fetch_sentiment。
        with self.assertRaises(ValueError):
            execute_sentiment("sentiment.fetch", {"markets": []})


if __name__ == "__main__":
    unittest.main()
