"""Malformed public JSON/JSONP must fail before normalization; no live traffic."""

import unittest

from quant_assistant.providers.fund_valuation import _decode_jsonp


class PublicPayloadShapeTests(unittest.TestCase):
    def test_json_jsonp_and_malformed_shapes(self):
        for payload in ("not json", "jsonpgz([])", "jsonpgz({invalid})", "", "<html>error</html>"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                _decode_jsonp(payload)
        self.assertEqual(_decode_jsonp('{"fundcode":"000001"}'), {"fundcode": "000001"})
        self.assertEqual(_decode_jsonp('jsonpgz({"fundcode":"000001"});'), {"fundcode": "000001"})


if __name__ == "__main__":
    unittest.main()
