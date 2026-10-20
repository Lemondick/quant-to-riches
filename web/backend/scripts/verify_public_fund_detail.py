"""Public fund profile fallback contracts, without external network access."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from quant_assistant.controllers.fund_schemas import FundDetailResponse
from quant_assistant.providers.fund_detail import PublicFundDetailProvider
from quant_assistant.providers.fund_detail_parsing import classification_facts


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class PublicFundProfileTests(unittest.TestCase):
    def test_public_facts_survive_detail_response_serialization(self):
        provider = PublicFundDetailProvider()
        basic = {
            "fd_name": "明确测试夹具",
            "_source": "sina",
            "_fetched_at": "2026-10-20T10:00:00Z",
            "type_desc": "股票型",
            "performance_bench_mark": "沪深300指数收益率×95%",
        }
        with (
            patch.object(provider, "_basic", return_value=basic),
            patch.object(provider, "_extended", return_value={}),
            patch.object(provider, "nav_history", return_value=()),
            patch.object(provider, "_rules", return_value=((), None, {})),
            patch.object(provider, "_optional_announcements", return_value=()),
        ):
            result = FundDetailResponse.model_validate(provider._load("110020")).model_dump(
                mode="json"
            )
        self.assertEqual(
            result["classification_facts"]["fields"]["benchmark"]["value"],
            basic["performance_bench_mark"],
        )
        self.assertIsNone(result["classification_facts"]["source_time"])

    def test_classification_facts_use_only_explicit_profile_fields(self):
        basic = {
            "_source": "danjuan",
            "_fetched_at": "2026-10-20T10:00:00Z",
            "type_desc": "股票型-标准指数",
            "invest_orientation": "本基金为ETF联接基金",
            "performance_bench_mark": "国证通用航空产业指数收益率×95%",
            "benchmark_index": [{"name": "沪深300"}],
            "fd_name": "伪装C类黄金基金",
        }
        facts = classification_facts(basic, stale=True)
        self.assertEqual(
            facts["fields"]["product_description"]["value"], basic["invest_orientation"]
        )
        self.assertEqual(facts["fields"]["benchmark"]["source_field"], "performance_bench_mark")
        self.assertEqual(facts["fetched_at"], basic["_fetched_at"])
        self.assertIsNone(facts["source_time"])
        self.assertTrue(facts["is_stale"])
        self.assertNotIn("share_class", facts["fields"])
        self.assertNotIn("benchmark_index", str(facts))
        self.assertNotIn("伪装", str(facts))

    def test_sina_classification_fields_keep_the_actual_field_name(self):
        raw = {
            "symbol": "110020",
            "jjjc": "名称A",
            "NewType1Name": "开放式",
            "NewType3Name": "复制指数型",
            "tzfw": "投资沪深300指数成份股",
        }
        provider = PublicFundDetailProvider(
            http_get=lambda *a, **kw: Response({"result": {"data": raw}})
        )
        facts = classification_facts(provider._sina_basic("110020"))
        self.assertEqual(facts["fields"]["operation_mode"]["source_field"], "NewType1Name")
        self.assertEqual(facts["fields"]["strategy_type"]["value"], "复制指数型")
        self.assertEqual(facts["fields"]["investment_scope"]["source_field"], "tzfw")

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
