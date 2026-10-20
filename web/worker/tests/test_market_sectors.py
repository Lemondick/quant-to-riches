"""Sector coverage and timestamps are source observations, never local estimates."""

import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_assistant.providers.market_sectors import read_sector_catalogs

NOW = datetime(2026, 10, 18, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
QUOTE_TIME = datetime(2026, 10, 16, 15, tzinfo=NOW.tzinfo)


class Response:
    def __init__(self, data=None, text=""):
        self.data = data
        self.content = (text or (json.dumps(data) if data is not None else "")).encode("gb18030")

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def em_row(number):
    return {
        "f12": f"BK{number:04d}",
        "f14": f"测试板块{number}",
        "f3": 0,
        "f2": 1234.56,
        "f6": 100000000,
        "f104": 10,
        "f105": 5,
        "f128": "测试股票",
        "f124": int(QUOTE_TIME.timestamp()),
    }


def feed(url, **kwargs):
    params = kwargs.get("params", {})
    if "sidemenu_new" in url:
        return Response(
            {
                "bklist": [
                    {
                        "code": em_row(i + kind * 1000)["f12"],
                        "name": em_row(i + kind * 1000)["f14"],
                        "type": kind,
                    }
                    for kind in (1, 2, 3)
                    for i in range(123)
                ]
            }
        )
    if "eastmoney" in url:
        # A category spanning multiple pages must never be silently truncated.
        assert params["fs"] in ("m:90+t:2+f:!50", "m:90+t:3+f:!50", "m:90+t:1+f:!50")
        start = (params["pn"] - 1) * params["pz"]
        rows = [
            em_row(i + int(params["fs"].split("t:")[1][0]) * 1000)
            for i in range(start, min(start + params["pz"], 123))
        ]
        return Response({"rc": 0, "data": {"total": 123, "diff": rows}})
    rows = {f"s{i}": f"s{i},测试{i},20,10,0,0,100,200,sh600000,1,1,1,测试股票" for i in range(25)}
    return Response(text="var x=" + json.dumps(rows, ensure_ascii=False) + ";")


class SectorCatalogTests(unittest.TestCase):
    def test_received_rows_never_manufacture_an_authoritative_catalog_total(self):
        _, catalogs = self.read()
        for catalog in catalogs:
            with self.subTest(source=catalog.source, category=catalog.category):
                self.assertIsNone(catalog.total)
                self.assertIsNone(catalog.complete)
                self.assertEqual("unknown", catalog.catalog_status)
                self.assertTrue(catalog.http_ok)
                self.assertTrue(catalog.parse_ok)
                self.assertFalse(catalog.cache_used)
                if catalog.source == "sina":
                    self.assertIsNone(catalog.quote_total)
                    self.assertFalse(catalog.source_time_known)
                else:
                    self.assertEqual(123, catalog.quote_total)
                    self.assertTrue(catalog.quote_payload_complete)

    def test_catalog_and_quote_coverage_are_separate(self):
        def extra(url, **kwargs):
            response = feed(url, **kwargs)
            if "sidemenu_new" in url:
                response.data["bklist"].append({"code": "BK9999", "name": "无报价测试", "type": 2})
            return response

        _, catalogs = self.read(extra)
        value = next(c for c in catalogs if c.source == "eastmoney" and c.category == "industry")
        self.assertIsNone(value.total)
        self.assertEqual((124, 123, 1), (value.received, value.quote_total, value.unquoted))
        self.assertFalse(value.quotes_complete)
        self.assertTrue(value.quote_payload_complete)

    def test_http_success_is_not_parse_success(self):
        for body in ("var x={bad-json}", "<html>rejected payload</html>"):

            def broken(url, body=body, **kwargs):
                return Response(text=body) if "sina" in url else feed(url, **kwargs)

            _, catalogs = self.read(broken)
            for catalog in (c for c in catalogs if c.source == "sina"):
                with self.subTest(body=body, category=catalog.category):
                    self.assertTrue(getattr(catalog, "http_ok", None))
                    self.assertFalse(catalog.parse_ok)
                    self.assertEqual(0, catalog.received)

    def test_cached_payload_retains_source_counts_and_original_clocks(self):
        with tempfile.TemporaryDirectory() as directory:
            original, _ = self.read(cache=Path(directory))

            def offline(*args, **kwargs):
                raise OSError("offline")

            rows, catalogs = self.read(offline, Path(directory))
            self.assertEqual(original, rows)
            for catalog in catalogs:
                self.assertIsNone(catalog.total)
                self.assertIsNone(catalog.complete)
                self.assertTrue(catalog.cache_used)
                self.assertTrue(catalog.stale)
                self.assertFalse(catalog.http_ok)
                self.assertEqual(NOW, catalog.fetched_at)

    def read(self, get=feed, cache=None, now=NOW):
        return read_sector_catalogs(http_get=get, now=now, cache_dir=cache)

    def test_full_catalogs_no_top_ten_limit_and_quote_time_is_not_fetch_time(self):
        rows, catalogs = self.read()
        self.assertEqual(len(rows), 3 * 123 + 3 * 25)
        self.assertEqual(len(catalogs), 6)
        self.assertTrue(
            all(item.complete is None and not item.stale and item.received > 0 for item in catalogs)
        )
        em = next(row for row in rows if row.source == "eastmoney")
        self.assertEqual(em.observed_at, QUOTE_TIME)
        self.assertEqual(em.fetched_at, NOW)
        self.assertEqual(em.change_rate, Decimal(0))
        sina = next(row for row in rows if row.source == "sina")
        self.assertIsNone(sina.observed_at)
        self.assertEqual(sina.change_rate, Decimal(0))

    def test_one_stale_section_does_not_block_new_observations(self):
        from quant_assistant.providers.market_feed import (
            AkshareMarketFeedProvider,
            _Section,
        )

        provider = AkshareMarketFeedProvider(now=lambda: NOW)

        def cached(key, loader):
            if key == "breadth":
                from quant_assistant.providers.base import ProviderUnavailable

                raise ProviderUnavailable("test")
            return _Section((), "test"), key == "fund_rankings"

        provider._cached = cached
        snapshot = provider.market_snapshot()
        self.assertFalse(snapshot.stale)
        self.assertEqual(snapshot.stale_sections, ("fund_rankings",))

    def test_registered_board_without_quote_remains_visible_with_null_return(self):
        def extra(url, **kwargs):
            response = feed(url, **kwargs)
            if "sidemenu_new" in url:
                response.data["bklist"].append({"code": "BK9999", "name": "无报价测试", "type": 2})
            return response

        rows, catalogs = self.read(extra)
        missing = next(row for row in rows if row.code == "BK9999")
        self.assertIsNone(missing.change_rate)
        self.assertIsNone(missing.observed_at)
        self.assertEqual(
            next(
                c for c in catalogs if c.source == "eastmoney" and c.category == "industry"
            ).unquoted,
            1,
        )

    def test_duplicate_page_does_not_claim_complete_or_contaminate_sina(self):
        def broken(url, **kwargs):
            if "eastmoney" not in url:
                return feed(url, **kwargs)
            return Response(
                {
                    "rc": 0,
                    "data": {"total": 123, "diff": [em_row(i) for i in range(100)]},
                }
            )

        rows, catalogs = self.read(broken)
        self.assertTrue(all(row.source == "sina" or row.change_rate is None for row in rows))
        self.assertTrue(
            all(not c.complete and c.error for c in catalogs if c.source == "eastmoney")
        )

    def test_nonfinite_or_future_quotes_are_rejected_without_fake_zeros(self):
        for bad in ("NaN", "Infinity", "-"):

            def invalid(url, bad=bad, **kwargs):
                response = feed(url, **kwargs)
                if "eastmoney" in url and "sidemenu_new" not in url:
                    response.data["data"]["diff"][0]["f3"] = bad
                return response

            rows, _ = self.read(invalid)
            self.assertTrue(all(row.source == "sina" or row.change_rate is None for row in rows))

        def future(url, **kwargs):
            response = feed(url, **kwargs)
            if "eastmoney" in url and "sidemenu_new" not in url:
                response.data["data"]["diff"][0]["f124"] = int(NOW.timestamp()) + 300
            return response

        rows, _ = self.read(future)
        self.assertTrue(all(row.source == "sina" or row.change_rate is None for row in rows))

    def test_outage_keeps_same_source_with_original_time_and_explicit_staleness(self):
        with tempfile.TemporaryDirectory() as directory:
            original, _ = self.read(cache=Path(directory))

            def offline(*args, **kwargs):
                raise OSError("offline")

            rows, catalogs = self.read(offline, Path(directory))
            self.assertEqual(rows, original)
            self.assertTrue(
                all(c.stale and c.error and c.complete is None and c.cache_used for c in catalogs)
            )
            self.assertTrue(all(c.fetched_at == NOW for c in catalogs))

    def test_corrupt_or_expired_cache_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.read(cache=Path(directory))

            def offline(*args, **kwargs):
                raise OSError("offline")

            rows, catalogs = self.read(offline, Path(directory), NOW.replace(day=27))
            self.assertEqual(rows, ())
            self.assertTrue(all(not c.complete and c.received == 0 for c in catalogs))

    def test_old_cache_row_count_is_not_upgraded_to_a_supplier_total(self):
        with tempfile.TemporaryDirectory() as directory:
            self.read(cache=Path(directory))
            for path in Path(directory).glob("*.json"):
                current = json.loads(path.read_text(encoding="utf-8"))
                legacy = {
                    "total": len(current["rows"]),
                    "fetched_at": current["fetched_at"],
                    "rows": current["rows"],
                }
                path.write_text(json.dumps(legacy), encoding="utf-8")

            def offline(*args, **kwargs):
                raise OSError("offline")

            _, catalogs = self.read(offline, Path(directory))
            for catalog in catalogs:
                self.assertIsNone(catalog.total)
                self.assertIsNone(catalog.complete)
                self.assertIsNone(catalog.quote_total)
                self.assertTrue(catalog.cache_used)


if __name__ == "__main__":
    unittest.main()
