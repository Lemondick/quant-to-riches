"""Strict public index history contracts; no live provider or account access."""

import unittest

from public_charts import index_history


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "code": 0,
            "data": {
                "sh000300": {
                    "day": [
                        ["2026-10-15", "10", "12", "13", "9", "20"],
                        ["2026-10-16", "12", "13", "14", "11", "30"],
                    ]
                }
            },
        }


class PublicChartTests(unittest.TestCase):
    def test_true_ohlc_sorted_and_no_private_input(self):
        result = index_history("000300", http_get=lambda *args, **kwargs: Response())
        self.assertEqual(result["symbol"], "000300")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][-1]["close"], "13")
        self.assertEqual(result["source"], "tencent")
        self.assertFalse(result["stale"])

    def test_only_supported_index_symbols_are_accepted(self):
        with self.assertRaises(ValueError):
            index_history("http://invalid.local")

    def test_history_identity_and_market_are_from_shared_registry(self):
        from quant_assistant.providers.index_registry import resolve_legacy_index

        definition = resolve_legacy_index("000300")
        result = index_history("000300", http_get=lambda *args, **kwargs: Response())
        self.assertEqual(result["id"], definition.id)
        self.assertEqual(result["currency"], definition.currency)
        self.assertEqual(result["market"], definition.market)
        self.assertEqual(result["calendar_id"], definition.calendar_id)
        self.assertEqual(result["source_symbol"], definition.history.symbol)

    def test_registered_unconnected_history_does_not_borrow_another_series(self):
        with self.assertRaises(ValueError):
            index_history("000688", http_get=lambda *args, **kwargs: self.fail("unexpected HTTP"))

    def test_broken_ohlc_never_becomes_a_valid_chart(self):
        response = Response()
        response.json = lambda: {
            "code": 0,
            "data": {"sh000300": {"day": [["2026-10-16", "10", "20", "12", "9", "1"]]}},
        }
        with self.assertRaises(ValueError):
            index_history("000300", http_get=lambda *args, **kwargs: response)


if __name__ == "__main__":
    unittest.main()
