"""Public DTO and cache-read boundaries preserve index identity and source clocks."""

import unittest
from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace

from quant_assistant.controllers.schemas import MarketSnapshotResponse
from quant_assistant.providers.data_quality import enrich_payload
from quant_assistant.providers.index_registry import definitions, registry_version

NOW = datetime.fromisoformat("2026-10-20T10:01:00+08:00")


class IndexContractTests(unittest.TestCase):
    def test_existing_snapshot_dto_preserves_registry_status_and_unknown_source_clock(self):
        row = {
            "id": "cn.sse.composite",
            "symbol": "000001",
            "name": "上证指数",
            "asset_type": "index",
            "price": "100",
            "change": "0",
            "change_rate": "0",
            "observed_at": None,
            "source": "synthetic",
            "source_time": None,
            "fetched_at": NOW,
            "currency": "CNY",
            "calendar_id": "CN",
            "currency_basis": "registry_definition_and_provider_namespace",
            "source_symbol": "sh000001",
            "raw_source_time": None,
            "quality_status": "unknown_time",
            "trade_time_verified": False,
        }
        data = MarketSnapshotResponse.model_validate(
            {
                "indices": [row],
                "fund_rankings": [],
                "sector_rankings": [],
                "observed_at": NOW,
                "source": "synthetic",
                "stale": False,
                "unavailable": [],
                "registry_version": registry_version(),
                "index_registry": [asdict(value) for value in definitions()],
                "index_statuses": [
                    {
                        "id": "cn.sse.composite",
                        "quality_status": "unknown_time",
                        "quality_reason": None,
                        "source": "synthetic",
                        "source_time": None,
                        "fetched_at": NOW,
                        "cache_read_at": NOW,
                    }
                ],
            }
        ).model_dump(mode="json")
        self.assertEqual(len(data["index_registry"]), 15)
        self.assertEqual(data["index_statuses"][0]["quality_status"], "unknown_time")
        self.assertEqual(data["indices"][0]["id"], "cn.sse.composite")
        self.assertIsNone(data["indices"][0]["observed_at"])
        self.assertIsNone(data["indices"][0]["source_time"])
        self.assertEqual(data["indices"][0]["currency"], "CNY")

    def test_cache_fallback_is_not_promoted_to_fresh_during_payload_enrichment(self):
        source_time = "2026-10-20T10:00:00+08:00"
        fetched = "2026-10-20T10:00:01+08:00"
        row = {
            "id": "cn.sse.composite",
            "source": "synthetic",
            "market": "CN",
            "source_time": source_time,
            "fetched_at": fetched,
            "cache_fallback": True,
            "quality_status": "stale",
            "is_stale": True,
            "fallback_used": True,
            "fallback_reason": "index_refresh_failed",
        }
        data = enrich_payload("market.snapshot", {"indices": [row]}, as_of=NOW)
        actual = data["indices"][0]
        self.assertEqual(actual["source_time"], source_time)
        self.assertEqual(actual["fetched_at"], fetched)
        self.assertTrue(actual["is_stale"])
        self.assertEqual(actual["quality_status"], "stale")

    def test_index_section_uses_adapter_and_returns_item_status_without_requesting_funds(self):
        from unittest.mock import patch

        from quant_assistant.providers.index_quotes import IndexQuoteBatch, IndexQuoteStatus
        from quant_assistant.providers.market_feed import AkshareMarketFeedProvider, MarketQuote

        row = MarketQuote(
            id="cn.sse.composite",
            symbol="000001",
            name="上证指数",
            asset_type="index",
            price=100,
            change=0,
            change_rate=0,
            observed_at=NOW,
            source="synthetic",
            trade_time_verified=True,
            source_time=NOW.isoformat(),
            fetched_at=NOW,
        )
        statuses = tuple(
            IndexQuoteStatus(
                value.id,
                "valid" if value.id == row.id else "unavailable",
                None,
                "synthetic",
                row.source_time if value.id == row.id else None,
                NOW if value.id == row.id else None,
                NOW,
            )
            for value in definitions()
        )
        source = AkshareMarketFeedProvider(now=lambda: NOW)
        with (
            patch.object(
                source,
                "_index_quotes",
                SimpleNamespace(fetch=lambda: IndexQuoteBatch((row,), statuses)),
            ),
            patch.object(
                source, "_load_fund_rankings", side_effect=AssertionError("unexpected funds")
            ),
        ):
            result = source.market_snapshot("indices")
        self.assertEqual(len(result.indices), 1)
        self.assertEqual(len(result.index_statuses), 15)
        self.assertEqual(len(result.index_registry), 15)
        self.assertEqual(result.registry_version, registry_version())
        self.assertFalse(result.stale)

    def test_legacy_quote_request_does_not_confuse_stock_and_index_with_same_code(self):
        from unittest.mock import patch

        from quant_assistant.providers.index_quotes import IndexQuoteBatch, IndexQuoteStatus
        from quant_assistant.providers.market_feed import AkshareMarketFeedProvider, MarketQuote

        stock = MarketQuote(
            symbol="000001",
            name="synthetic stock",
            asset_type="stock",
            price=100,
            change=0,
            change_rate=0,
            observed_at=NOW,
            source="synthetic",
            trade_time_verified=True,
            fetched_at=NOW,
            source_time=NOW.isoformat(),
        )
        status = IndexQuoteStatus(
            "cn.sse.composite", "unavailable", "quote_not_returned", "tencent", None, None, NOW
        )
        source = AkshareMarketFeedProvider(now=lambda: NOW)
        with (
            patch.object(
                source,
                "_index_quotes",
                SimpleNamespace(fetch=lambda ids: IndexQuoteBatch((), (status,))),
            ),
            patch.object(source, "_fetch_tencent_quotes", return_value=(stock,)) as fetch_stock,
        ):
            result = source.market_quotes(("000001",), ("000001",))
        self.assertEqual(result.items, (stock,))
        self.assertEqual(result.unavailable, ("000001",))
        self.assertEqual(fetch_stock.call_args.args[0], (("000001", "stock"),))
        self.assertEqual(result.index_statuses[0].id, "cn.sse.composite")


if __name__ == "__main__":
    unittest.main()
