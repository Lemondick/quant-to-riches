"""Public directory contracts; all HTTP responses here are explicit fixtures."""

import json
import unittest
from datetime import UTC, datetime, timedelta

from quant_assistant.providers.base import ProviderDataError
from quant_assistant.providers.fund_catalog import FundCatalogProvider, parse_catalog


def wrapped(rows):
    return ("var r = " + json.dumps(rows, ensure_ascii=False) + ";").encode()


def row(code="000001", name="名称不用于推断A类ETF", kind="混合型-偏股"):
    return [code, "PY", name, kind, "PINYIN"]


class FundCatalogTests(unittest.TestCase):
    def test_strict_wrapper_and_non_finite_values(self):
        for body in (b"[]", b"var r=[];alert(1)", b"var r=NaN;", b"var r={};", b"var r=[];"):
            with self.subTest(body=body), self.assertRaises(ProviderDataError):
                parse_catalog(body)

    def test_identity_quarantine_conflicting_duplicates_and_unknown_types(self):
        items, rejected = parse_catalog(
            wrapped(
                [
                    row(),
                    row(),
                    row("000002", kind="未来供应商新类别"),
                    row("000003"),
                    row("000003", name="冲突名称"),
                    row("１２３４５６"),
                    row("x"),
                    ["000004"],
                ]
            )
        )
        self.assertEqual([item[0] for item in items], ["000001", "000002"])
        self.assertEqual(items[1][2], "未来供应商新类别")
        self.assertEqual(rejected, 5)

    def test_empty_type_is_unknown_and_bounds_are_not_sixteen(self):
        items, rejected = parse_catalog(wrapped([row(f"{i:06}", kind="") for i in range(100)]))
        self.assertEqual(len(items), 100)
        self.assertIsNone(items[0][2])
        self.assertEqual(rejected, 0)
        with self.assertRaises(ProviderDataError):
            parse_catalog(wrapped([row()] * 50001))

    def test_failed_reload_preserves_real_clock_and_cached_data_is_detached(self):
        clock = [datetime(2026, 10, 20, tzinfo=UTC)]
        calls = []

        class Response:
            status_code = 200
            headers = {}
            content = wrapped([row()])

        def fetch(*args, **kwargs):
            calls.append(kwargs)
            return Response()

        provider = FundCatalogProvider(http_get=fetch, now=lambda: clock[0])
        first = provider.read()
        self.assertIsNone(first["source_time"])
        self.assertEqual(first["quality_status"], "unknown_time")
        self.assertFalse(first["fallback_used"])
        first["items"][0]["name"] = "caller mutation"
        self.assertNotEqual(provider.read()["items"][0]["name"], "caller mutation")
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["allow_redirects"])
        clock[0] += timedelta(days=1)
        Response.content = wrapped([])
        provider.expire_cache()
        stale = provider.read()
        self.assertEqual(stale["fetched_at"], first["fetched_at"])
        self.assertTrue(stale["is_stale"])
        self.assertTrue(stale["fallback_used"])
        self.assertEqual(stale["quality_status"], "stale")
        self.assertEqual(set(stale["items"][0]), {"symbol", "name", "fund_type"})
        for forbidden in ("nav", "unit_nav", "user_id", "share_class", "credentials"):
            self.assertNotIn(forbidden, stale["items"][0])


if __name__ == "__main__":
    unittest.main()
