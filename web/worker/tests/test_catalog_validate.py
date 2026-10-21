"""Worker op fund.catalog_validate: equivalence with the 0024 trigger predicate.

Vectors mirror each disjunct of the trigger's fund.catalog WHERE clause
(web/backend-dotnet/migrations/0024_public_fund_catalog.sql); the regex is
the verbatim ``^[0-9]{6}$`` from the SQL (see module docstring).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_validate_operations import (
    CATALOG_VALIDATE_CAPABILITIES,
    catalog_validate,
    execute_catalog_validate,
)
from operations import CAPABILITIES, execute
from worker_result import child_envelope


def _ok_item(**overrides):
    item = {"symbol": "000001", "name": "基金A", "fund_type": "混合型"}
    item.update(overrides)
    return item


class CatalogValidateTests(unittest.TestCase):
    def test_op_is_registered(self):
        self.assertIn("fund.catalog_validate", CAPABILITIES)
        self.assertIn("fund.catalog_validate", CATALOG_VALIDATE_CAPABILITIES)

    def test_valid_payload(self):
        result = catalog_validate({"items": [_ok_item(), _ok_item(symbol="000002")]})
        self.assertEqual(result, {"ok": True, "errors": []})

    def test_missing_fund_type_passes_like_trigger(self):
        # The trigger only rejects *extra* keys; symbol/name are re-checked.
        item = {"symbol": "000001", "name": "基金A"}
        self.assertEqual(catalog_validate({"items": [item]}), {"ok": True, "errors": []})

    def test_extra_key_rejected(self):
        result = catalog_validate({"items": [_ok_item(extra="x")]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["index"], 0)
        self.assertIn("unexpected keys", result["errors"][0]["message"])

    def test_symbol_regex_vectors(self):
        for bad in ("00001", "0000011", "ABCDEF", "", "00000a", " 000001"):
            with self.subTest(symbol=bad):
                result = catalog_validate({"items": [_ok_item(symbol=bad)]})
                self.assertFalse(result["ok"])
                self.assertIn("symbol", result["errors"][0]["message"])
        for good in ("000001", "999999"):
            with self.subTest(symbol=good):
                self.assertTrue(catalog_validate({"items": [_ok_item(symbol=good)]})["ok"])
        # missing symbol -> COALESCE '' -> regex fails
        item = {"name": "基金A", "fund_type": "混合型"}
        result = catalog_validate({"items": [item]})
        self.assertFalse(result["ok"])

    def test_name_vectors(self):
        for bad in ("",):
            result = catalog_validate({"items": [_ok_item(name=bad)]})
            self.assertFalse(result["ok"])
            self.assertIn("name", result["errors"][0]["message"])
        item = {"symbol": "000001", "fund_type": "混合型"}
        self.assertFalse(catalog_validate({"items": [item]})["ok"])

    def test_non_object_items(self):
        for bad in ("x", 42, None, [1], True):
            with self.subTest(item=bad):
                result = catalog_validate({"items": [bad]})
                self.assertFalse(result["ok"])
                self.assertIn("object", result["errors"][0]["message"])

    def test_length_bounds(self):
        self.assertFalse(catalog_validate({"items": []})["ok"])
        self.assertEqual(catalog_validate({"items": []})["errors"][0]["index"], -1)
        big = [_ok_item(symbol=f"{i:06d}") for i in range(50000)]
        self.assertTrue(catalog_validate({"items": big})["ok"])
        too_big = big + [_ok_item(symbol="999999")]
        result = catalog_validate({"items": too_big})
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["index"], -1)

    def test_items_not_a_list(self):
        result = catalog_validate({"items": "nope"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["index"], -1)
        result = catalog_validate({})
        self.assertFalse(result["ok"])

    def test_multiple_errors_reported(self):
        result = catalog_validate(
            {"items": [_ok_item(), _ok_item(symbol="bad"), _ok_item(name="")]}
        )
        self.assertFalse(result["ok"])
        self.assertEqual([e["index"] for e in result["errors"]], [1, 2])

    def test_invalid_payload_envelope(self):
        with self.assertRaises(ValueError):
            execute_catalog_validate("fund.catalog_validate", None)
        outcome = child_envelope(
            lambda: execute_catalog_validate("fund.catalog_validate", None)
        )
        self.assertEqual(outcome["error_code"], "invalid_request")

    def test_execute_dispatch(self):
        result = execute("fund.catalog_validate", {"items": [_ok_item()]})
        self.assertEqual(result, {"ok": True, "errors": []})


if __name__ == "__main__":
    unittest.main()
