"""Worker op research.fetch: source registry, dedup, failure tolerance."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations import CAPABILITIES, execute  # noqa: E402
from research_fetch_operations import (  # noqa: E402
    DEFAULT_RESEARCH_SOURCES,
    RESEARCH_FETCH_CAPABILITIES,
    execute_research_fetch,
    fetch_research,
)


class _FakeResponse:
    def __init__(self, json_payload=None, text_payload=""):
        self._json = json_payload
        self.text = text_payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


_REPORT_ROWS = {
    "data": [
        {
            "title": "公司深度：增长可期",
            "infoCode": "AP202610210000000001",
            "orgSName": "测试证券",
            "emRatingName": "买入",
            "publishDate": "2026-10-20 00:00:00.000",
        },
        {
            "title": "公司深度：增长可期",
            "infoCode": "AP202610210000000001",  # same URL -> dedup target
            "orgSName": "测试证券",
            "emRatingName": "买入",
            "publishDate": "2026-10-20 00:00:00.000",
        },
        {"title": "", "infoCode": "AP202610210000000002"},  # malformed -> skipped
    ]
}

_NEWS_BODY = (
    'jQuery_research_1({"code":0,"result":{"cmsArticleWebOld":['
    '{"date":"2026-10-21 10:00:00","title":"<em>000001</em>获机构调研",'
    '"content":"多家机构参与调研，关注增长。",'
    '"mediaName":"证券时报网","url":"http://finance.eastmoney.com/a/1.html"},'
    '{"date":"2026-10-20 09:00:00","title":"基金调仓",'
    '"content":"多只基金调整持仓。",'
    '"mediaName":"上海证券报","url":"http://finance.eastmoney.com/a/1.html"}'
    "]}});"
)


def _fake_http(url, **kwargs):
    if "reportapi.eastmoney.com" in url:
        return _FakeResponse(json_payload=_REPORT_ROWS)
    if "search-api-web.eastmoney.com" in url:
        return _FakeResponse(text_payload=_NEWS_BODY)
    raise AssertionError(f"unexpected url {url}")


def _exploding_http(url, **kwargs):
    raise ConnectionError("boom")


class ResearchFetchTests(unittest.TestCase):
    def test_op_is_registered(self):
        self.assertIn("research.fetch", CAPABILITIES)
        self.assertIn("research.fetch", RESEARCH_FETCH_CAPABILITIES)
        self.assertEqual(DEFAULT_RESEARCH_SOURCES, ("eastmoney_reports", "eastmoney_news"))

    def test_aggregates_both_kinds(self):
        result = fetch_research(["000001"], http_get=_fake_http)
        kinds = {item["kind"] for item in result["items"]}
        self.assertEqual(kinds, {"report", "news"})
        self.assertTrue(result["deduped"])
        for item in result["items"]:
            for key in ("title", "summary", "source", "url", "published_at", "symbol"):
                self.assertIn(key, item)

    def test_dedups_by_url(self):
        result = fetch_research(["000001"], http_get=_fake_http)
        urls = [item["url"] for item in result["items"]]
        self.assertEqual(len(urls), len(set(urls)))
        # 3 report rows (1 malformed, 1 duplicate) + 2 news rows (1 duplicate URL)
        self.assertEqual(len(urls), 2)

    def test_kinds_filter(self):
        result = fetch_research(["000001"], kinds=["report"], http_get=_fake_http)
        self.assertEqual({item["kind"] for item in result["items"]}, {"report"})
        self.assertEqual(result["sources_used"], ["eastmoney_reports"])

    def test_unknown_source_warns_and_continues(self):
        result = fetch_research(["000001"], sources=["nope", "eastmoney_news"], http_get=_fake_http)
        self.assertEqual({item["kind"] for item in result["items"]}, {"news"})
        self.assertTrue(any("未知研报来源" in w for w in result["warnings"]))

    def test_failing_source_does_not_block(self):
        result = fetch_research(["000001"], http_get=_exploding_http)
        self.assertEqual(result["items"], [])
        self.assertEqual(len(result["warnings"]), 2)

    def test_empty_symbols_rejected(self):
        with self.assertRaises(ValueError):
            fetch_research([], http_get=_fake_http)
        with self.assertRaises(ValueError):
            fetch_research("000001", http_get=_fake_http)

    def test_bad_kinds_rejected(self):
        with self.assertRaises(ValueError):
            fetch_research(["000001"], kinds=["blog"], http_get=_fake_http)


class ExecuteResearchFetchTests(unittest.TestCase):
    def test_dispatch_via_registry(self):
        from unittest import mock

        import research_fetch_operations as module

        with mock.patch.object(module.requests, "get", side_effect=_fake_http):
            result = execute("research.fetch", {"symbols": ["000001"], "kinds": ["news"]})
        self.assertTrue(result["deduped"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["kind"], "news")

    def test_missing_symbols_rejected(self):
        with self.assertRaises(ValueError):
            execute_research_fetch("research.fetch", {})

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            execute_research_fetch("research.nope", {"symbols": ["000001"]})


if __name__ == "__main__":
    unittest.main()
