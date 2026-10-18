"""Public fund profile fallback contracts, without external network access."""

from __future__ import annotations

import unittest
from datetime import date

from quant_assistant.providers.fund_detail import PublicFundDetailProvider


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class PublicFundProfileTests(unittest.TestCase):
    def test_sina_etf_field_names_are_normalized(self):
        raw = {
            "symbol": "510300",
            "jjjc": "沪深300ETF",
            "jjqc": "沪深300交易型开放式指数基金",
            "NewType2Name": "股票型",
            "glr": "测试基金管理人",
            "clrq": "2012-05-04 00:00:00",
            "bjjz": "沪深300指数",
            "tzmb": "跟踪指数",
        }
        provider = PublicFundDetailProvider(
            http_get=lambda *args, **kwargs: Response({"result": {"data": raw}})
        )
        result = provider._sina_basic("510300")
        self.assertEqual(result["fd_name"], "沪深300ETF")
        self.assertEqual(result["fd_full_name"], raw["jjqc"])
        self.assertEqual(result["type_desc"], "股票型")
        self.assertEqual(result["keeper_name"], raw["glr"])
        self.assertEqual(str(result["found_date"])[:10], date(2012, 5, 4).isoformat())
        self.assertEqual(result["performance_bench_mark"], raw["bjjz"])
        self.assertEqual(result["invest_target"], raw["tzmb"])
        self.assertEqual(provider.source_status()[1].status, "available")

    def test_existing_sina_names_keep_priority_and_missing_fields_stay_unknown(self):
        provider = PublicFundDetailProvider(
            http_get=lambda *args, **kwargs: Response(
                {"result": {"data": {"symbol": "510300", "基金简称": "已披露名称", "jjjc": "别名"}}}
            )
        )
        result = provider._sina_basic("510300")
        self.assertEqual(result["fd_name"], "已披露名称")
        self.assertEqual(result["_source"], "sina")
        self.assertNotIn("latest_scale", result)


if __name__ == "__main__":
    unittest.main()
