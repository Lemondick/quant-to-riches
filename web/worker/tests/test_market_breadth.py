"""Public breadth must survive normalization without invented or mixed-date values."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant_assistant.controllers.schemas import MarketSnapshotResponse
from quant_assistant.providers.market_feed import AkshareMarketFeedProvider, MarketQuote, _Section

NOW = datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
HTML = (
    '<div class="market-activity-meta"><span class="market-activity-time">'
    """2026-10-16 15:00:00</span></div>
<table><tr><td>上涨</td><td>3061</td><td>下跌</td><td>2023</td><td>平盘</td><td>132</td></tr>
<tr><td>涨停</td><td>61</td><td>跌停</td><td>11</td></tr>
<tr><td>真实涨停</td><td>55</td><td>真实跌停</td><td>10</td></tr></table>"""
)


class Response:
    def __init__(self, *, text="", data=None):
        self.text, self.data = text, data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def get(url, **kwargs):
    if "legulegu" in url:
        return Response(text=HTML)
    symbol = kwargs["params"]["secid"].split(".")[1]
    amount = "300.00" if symbol == "000001" else "700.00"
    return Response(
        data={
            "rc": 0,
            "data": {
                "code": symbol,
                "klines": [
                    "2026-10-15,1,1,1,1,10,400.00",
                    "2026-10-16,1,1,1,1,10," + amount,
                ],
            },
        }
    )


class MarketBreadthTests(unittest.TestCase):
    def provider(self, http_get=get):
        provider = AkshareMarketFeedProvider(http_get=http_get, now=lambda: NOW)
        for name in [
            "_load_main_indices",
            "_load_fund_rankings",
            "_load_sector_rankings",
        ]:
            rows = (
                (
                    MarketQuote(
                        symbol="000001",
                        name="上证指数",
                        asset_type="index",
                        price=Decimal("100"),
                        change=Decimal("0"),
                        change_rate=Decimal("0"),
                        observed_at=NOW,
                        source="synthetic",
                    ),
                )
                if name == "_load_main_indices"
                else ()
            )
            patcher = patch.object(provider, name, return_value=_Section(rows, "synthetic"))
            patcher.start()
            self.addCleanup(patcher.stop)
        return provider

    def read(self, http_get=get, **kwargs):
        from quant_assistant.providers.market_breadth import read_market_breadth

        return read_market_breadth(http_get=http_get, now=NOW, **kwargs)

    def test_verified_closed_sessions_survive_a_process_restart_and_source_failure(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "turnover.json"
            first = self.read(cache_path=cache_path)

            def fetch(url, **kwargs):
                if "eastmoney" in url:
                    raise TimeoutError("closed sessions must remain readable offline")
                return get(url, **kwargs)

            result = self.read(fetch, cache_path=cache_path)
            self.assertEqual(first.turnover, Decimal("1000.00"))
            self.assertEqual(result.turnover, Decimal("1000.00"))
            self.assertEqual(result.turnover_ratio, Decimal("1.25"))
            self.assertEqual(result.observed_at.isoformat(), "2026-10-16T15:00:00+08:00")

    def test_cached_other_dates_or_non_finite_amounts_cannot_fill_current_statistics(
        self,
    ):
        for dates in [{"2026-10-15": "1000"}, {"2026-10-16": "NaN"}]:
            with self.subTest(dates=dates), tempfile.TemporaryDirectory() as directory:
                cache_path = Path(directory) / "turnover.json"
                cache_path.write_text(
                    json.dumps(
                        {
                            "source": "eastmoney",
                            "fetched_at": NOW.isoformat(),
                            "shanghai": dates,
                            "shenzhen": dates,
                        }
                    )
                )

                def fetch(url, **kwargs):
                    if "eastmoney" in url:
                        raise TimeoutError("synthetic")
                    return get(url, **kwargs)

                result = self.read(fetch, cache_path=cache_path)
                self.assertIsNone(result.turnover)
                self.assertIsNone(result.turnover_ratio)

    def test_unreadable_optional_cache_does_not_hide_fresh_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "turnover.json"
            with patch.object(
                type(cache_path), "is_file", side_effect=PermissionError("synthetic")
            ):
                result = self.read(cache_path=cache_path)
            self.assertEqual(result.advancing, 3061)
            self.assertEqual(result.turnover, Decimal("1000.00"))

    def test_snapshot_contract_exposes_actual_breadth_instead_of_discarding_it(self):
        result = MarketSnapshotResponse.model_validate(
            self.provider().market_snapshot()
        ).model_dump(mode="json")
        self.assertIn("breadth", result)
        self.assertEqual(result["breadth"]["advancing"], 3061)
        self.assertEqual(result["breadth"]["limit_up"], 61)
        self.assertEqual(result["breadth"]["turnover"], "1000.00")

    def test_exact_limit_counts_and_turnover_units_and_previous_session_ratio(self):
        result = self.read()
        self.assertEqual((result.advancing, result.declining, result.unchanged), (3061, 2023, 132))
        self.assertEqual((result.limit_up, result.limit_down), (61, 11))
        self.assertEqual(result.turnover, Decimal("1000.00"))
        self.assertEqual(result.previous_turnover, Decimal("800.00"))
        self.assertEqual(result.turnover_ratio, Decimal("1.25"))
        self.assertEqual(result.previous_trade_date, "2026-10-15")
        self.assertEqual(result.observed_at.isoformat(), "2026-10-16T15:00:00+08:00")

    def test_zero_is_a_valid_count_and_not_missing_data(self):
        def fetch(url, **kwargs):
            response = get(url, **kwargs)
            response.text = response.text.replace("3061", "0").replace(">61<", ">0<")
            return response

        self.assertEqual(self.read(fetch).advancing, 0)
        self.assertEqual(self.read(fetch).limit_up, 0)

    def test_missing_negative_or_future_statistics_are_rejected(self):
        for text in [
            HTML.replace("3061", "-1"),
            HTML.replace("上涨", "未知"),
            HTML.replace("2026-10-16", "2026-10-19"),
        ]:
            with self.subTest(text=text):

                def fetch(url, text=text, **kwargs):
                    return Response(text=text) if "legulegu" in url else get(url, **kwargs)

                with self.assertRaises(ValueError):
                    self.read(fetch)

    def test_turnover_failure_keeps_counts_without_making_up_zero(self):
        def fetch(url, **kwargs):
            if "eastmoney" in url:
                raise TimeoutError("synthetic upstream failure")
            return get(url, **kwargs)

        result = self.read(fetch)
        self.assertEqual(result.limit_up, 61)
        self.assertIsNone(result.turnover)
        self.assertIsNone(result.turnover_ratio)
        self.assertIn("turnover", result.unavailable)

    def test_mismatched_market_dates_do_not_produce_a_composite_turnover(self):
        def fetch(url, **kwargs):
            response = get(url, **kwargs)
            if "eastmoney" in url and kwargs["params"]["secid"] == "0.399001":
                response.data["data"]["klines"][-1] = "2026-10-14,1,1,1,1,10,700.00"
            return response

        result = self.read(fetch)
        self.assertIsNone(result.turnover)
        self.assertIsNone(result.turnover_ratio)

    def test_zero_previous_turnover_does_not_create_infinite_ratio(self):
        def fetch(url, **kwargs):
            response = get(url, **kwargs)
            if "eastmoney" in url:
                response.data["data"]["klines"][0] = "2026-10-15,1,1,1,1,10,0"
            return response

        result = self.read(fetch)
        self.assertEqual(result.turnover, Decimal("1000.00"))
        self.assertIsNone(result.turnover_ratio)

    def test_different_previous_sessions_do_not_produce_a_mislabelled_ratio(self):
        def fetch(url, **kwargs):
            response = get(url, **kwargs)
            if "eastmoney" in url:
                response.data["data"]["klines"].insert(0, "2026-10-14,1,1,1,1,10,300.00")
                if kwargs["params"]["secid"] == "0.399001":
                    response.data["data"]["klines"].pop(1)
            return response

        result = self.read(fetch)
        self.assertEqual(result.turnover, Decimal("1000.00"))
        self.assertIsNone(result.turnover_ratio)

    def test_breadth_source_failure_does_not_discard_other_market_sections(self):
        def fail(*_, **__):
            raise TimeoutError("synthetic")

        result = MarketSnapshotResponse.model_validate(
            self.provider(fail).market_snapshot()
        ).model_dump(mode="json")
        self.assertIn("breadth", result)
        self.assertIsNone(result["breadth"])
        self.assertEqual(result["indices"][0]["symbol"], "000001")
        self.assertEqual(result["unavailable"], [])
        self.assertFalse(result["stale"])

    def test_cached_breadth_is_marked_stale_without_staling_the_whole_snapshot(self):
        provider = self.provider()
        provider.market_snapshot()
        provider.expire_cache()
        provider._http_get = lambda *_, **__: (_ for _ in ()).throw(TimeoutError())
        provider._quote_http._get = provider._http_get
        result = MarketSnapshotResponse.model_validate(provider.market_snapshot()).model_dump(
            mode="json"
        )
        self.assertTrue(result["breadth"]["stale"])
        self.assertFalse(result["stale"])


if __name__ == "__main__":
    unittest.main()
