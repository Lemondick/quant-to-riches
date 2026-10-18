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
