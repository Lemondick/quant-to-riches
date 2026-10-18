"""Asset identity in OCR rows with names before codes and labelled quantities."""

import unittest
from datetime import date

from quant_assistant.models.entities import AssetType
from quant_assistant.providers.ocr import OcrResult
from quant_assistant.services.photo_import import parse_account_statement


class PhotoRowIdentityTests(unittest.TestCase):
    def statement(self, name):
        lines = (f"{name} 000001 份额100.00000000 成本价1.100000 市值120.00", "现金余额1000.00")
        ocr = OcrResult("paddleocr", "\n".join(lines), lines, "synthetic-image")
        return parse_account_statement(ocr, date(2026, 9, 30)).positions[0]

    def test_fund_name_before_code_is_not_replaced_by_quantity_label(self):
        row = self.statement("华夏成长混合")
        self.assertEqual(row.asset_type, AssetType.FUND)
        self.assertEqual(row.symbol, "000001")

    def test_same_code_for_a_stock_keeps_its_stock_identity(self):
        row = self.statement("平安银行")
        self.assertEqual(row.asset_type, AssetType.STOCK)
        self.assertEqual(row.symbol, "000001.SZ")


if __name__ == "__main__":
    unittest.main()
