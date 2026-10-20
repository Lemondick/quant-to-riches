"""Index identity is shared configuration, never a guessed security prefix."""

import unittest
from dataclasses import FrozenInstanceError
from importlib.resources import files

from quant_assistant.providers.market_feed_parsing import _tencent_id


class IndexRegistryTests(unittest.TestCase):
    def test_unknown_index_cannot_be_inferred_as_a_shanghai_security(self):
        for symbol in ("600000", "999999"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                _tencent_id(symbol, "index")

    def test_beijing_index_has_verified_namespace_separate_from_stock_mapping(self):
        self.assertEqual(_tencent_id("899050", "index"), "bj899050")
        self.assertEqual(_tencent_id("000001", "index"), "sh000001")
        self.assertEqual(_tencent_id("000001", "stock"), "sz000001")

    def test_registry_is_packaged_unique_ordered_and_complete(self):
        from quant_assistant.providers.index_registry import definitions

        rows = definitions()
        self.assertEqual(len(rows), 16)
        self.assertEqual(len({row.id for row in rows}), 16)
        self.assertEqual([row.display_order for row in rows], list(range(1, 17)))
        expected = {
            "CN": ("CNY", "Asia/Shanghai"),
            "HK": ("HKD", "Asia/Hong_Kong"),
            "JP": ("JPY", "Asia/Tokyo"),
            "US": ("USD", "America/New_York"),
        }
        for row in rows:
            self.assertEqual((row.currency, row.timezone), expected[row.market])
            self.assertEqual(row.calendar_id, row.market)
            self.assertTrue(row.enabled)
            self.assertEqual(row.quote.verification_status, "verified")
            self.assertTrue(row.quote.response_symbol)
            self.assertTrue(row.quote.response_names)
            self.assertTrue(row.quote.evidence_urls)
            self.assertEqual(row.quote.source_timezone, row.timezone)
        self.assertTrue(
            files("quant_assistant.providers").joinpath("index_registry.json").is_file()
        )

    def test_provisional_product_is_distinct_from_provider_verification(self):
        from quant_assistant.providers.index_registry import definitions, get_index

        rows = definitions()
        self.assertEqual(
            [row.id for row in rows if row.product_status == "provisional"], ["cn.csi.1000"]
        )
        self.assertEqual(get_index("cn.csi.1000").quote.verification_status, "verified")
        self.assertEqual(sum(row.id == "jp.nikkei225" for row in rows), 1)
        self.assertEqual(get_index("jp.nikkei225").quote.response_type, "INDEX")
        self.assertEqual(get_index("cn.sse.return").exchange_code, "000888")
        self.assertIn("全收益", get_index("cn.sse.return").official_name)

    def test_legacy_aliases_and_verified_history_capabilities(self):
        from quant_assistant.providers.index_registry import definitions, resolve_legacy_index

        for symbol in ("000001", "399001", "399006", "000300"):
            row = resolve_legacy_index(symbol)
            self.assertEqual(row.legacy_history_symbol, symbol)
            self.assertEqual(row.history.verification_status, "verified")
        self.assertEqual(sum(row.history is not None for row in definitions()), 16)
        self.assertEqual(resolve_legacy_index("000888").history.provider, "tencent")
        self.assertEqual(resolve_legacy_index("899050").history.provider, "eastmoney")
        self.assertEqual(sum(row.legacy_history_symbol is not None for row in definitions()), 4)
        for unknown in ("600000", "999999", "N225", "not-an-index"):
            with self.subTest(unknown=unknown), self.assertRaises(ValueError):
                resolve_legacy_index(unknown)

    def test_definitions_are_immutable_and_bad_ids_are_rejected(self):
        from quant_assistant.providers.index_registry import definitions, get_index

        row = definitions()[0]
        with self.assertRaises(FrozenInstanceError):
            row.name = "changed"
        with self.assertRaises(FrozenInstanceError):
            row.quote.symbol = "changed"
        with self.assertRaises(ValueError):
            get_index("us.nasdaq100")


if __name__ == "__main__":
    unittest.main()
