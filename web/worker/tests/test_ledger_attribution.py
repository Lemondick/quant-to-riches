import copy
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attribution_operations import EvidenceMissing
from ledger_attribution import calculate_periods


class Marks:
    def price(self, symbol, day):
        return Decimal(
            {date(2026, 1, 2): "10", date(2026, 1, 15): "11", date(2026, 1, 30): "12"}[day]
        ), 1

    def classification(self, symbol, day):
        return "科技", "2026-01-02"

    def holding(self, symbol, asset, weight, left, right, benchmark=False):
        return {
            "symbol": symbol,
            "sector_name": "科技",
            "initial_value": str(weight),
            "period_return": str(self.price(symbol, right)[0] / self.price(symbol, left)[0] - 1),
        }


class LedgerAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tables = {
            "transactions": [],
            "dividend_events": [],
            "portfolio_events": [],
            "corporate_action_events": [],
            "ledger_corrections": [],
            "audit_log": [],
        }
        self.positions = [
            {"symbol": "A", "asset_type": "stock", "quantity": "20"},
            {"symbol": "CNY", "asset_type": "cash", "quantity": "800"},
        ]
        self.baseline = {"effective_date": "2026-01-02"}

    def add(self, table, row, category):
        self.tables[table].append(row)
        self.tables["audit_log"].append(
            {
                "id": len(self.tables["audit_log"]) + 1,
                "entity_type": category,
                "entity_id": str(row.get("id", row.get("event_id"))),
            }
        )

    def run_periods(self):
        return calculate_periods(
            self.tables,
            self.baseline,
            self.positions,
            {"A": "1"},
            date(2026, 1, 2),
            date(2026, 1, 30),
            Marks(),
        )

    def trade(self, action="sell", **kwargs):
        return {
            "id": 1,
            "symbol": "A",
            "asset_type": "stock",
            "action": action,
            "quantity": "10",
            "price": "11",
            "fees": "1",
            "traded_at": "2026-01-15T10:00:00+08:00",
            **kwargs,
        }

    def test_sale_fee_and_reinvestment_change_next_period_weights_and_reconcile(self):
        self.add("transactions", self.trade(), "transaction")
        before = copy.deepcopy(self.tables)
        periods = self.run_periods()
        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[0]["profit"], "19")
        self.assertEqual(periods[0]["ending_value"], "1019")
        self.assertEqual(periods[1]["initial_value"], "1019")
        self.assertEqual(periods[1]["profit"], "10")
        self.assertEqual(periods[1]["ending_value"], "1029")
        gross = Decimal(1)
        for period in periods:
            gross *= 1 + Decimal(period["portfolio_return"])
            self.assertEqual(
                sum(
                    (
                        Decimal(row["initial_value"]) * Decimal(row["period_return"])
                        for row in period["portfolio"]
                    ),
                    Decimal(0),
                ),
                Decimal(period["profit"]),
            )
        self.assertLess(abs(gross - Decimal("1.029")), Decimal("1e-25"))
        self.assertEqual(self.tables, before)

    def test_cash_dividend_and_deposit_are_distinguished_from_investment_profit(self):
        self.add(
            "dividend_events",
            {
                "id": 2,
                "symbol": "A",
                "asset_type": "stock",
                "gross_amount": "10",
                "fees": "2",
                "paid_at": "2026-01-30T11:00:00+08:00",
            },
            "dividend",
        )
        self.add(
            "portfolio_events",
            {
                "event_id": "deposit",
                "event_type": "deposit",
                "status": "confirmed",
                "amount": "200",
                "effective_date": "2026-01-30",
            },
            "external_cash_flow",
        )
        period = self.run_periods()[0]
        self.assertEqual(period["profit"], "48")
        self.assertEqual(period["net_external_flow"], "200")
        self.assertEqual(period["ending_value"], "1248")
        self.assertEqual(period["portfolio_return"], "0.048")

    def test_new_symbol_execution_contribution_is_disclosed_in_cash(self):
        self.add("transactions", self.trade("buy", symbol="B", price="10"), "transaction")
        period = self.run_periods()[0]
        cash = next(row for row in period["portfolio"] if row["symbol"] == "CNY")
        self.assertEqual(
            Decimal(cash["initial_value"]) * Decimal(cash["period_return"]), Decimal(9)
        )
        self.assertEqual(period["profit"], "29")

    def test_missing_audit_negative_holdings_bad_cash_delta_and_unconfirmed_flow_block(self):
        for case in ("audit", "oversell", "delta", "pending"):
            with self.subTest(case=case):
                self.setUp()
                if case == "pending":
                    self.add(
                        "portfolio_events",
                        {
                            "event_id": "pending",
                            "event_type": "deposit",
                            "status": "pending",
                            "amount": "100",
                            "effective_date": "2026-01-30",
                        },
                        "external_cash_flow",
                    )
                else:
                    self.add(
                        "transactions",
                        self.trade(
                            **(
                                {"quantity": "21"}
                                if case == "oversell"
                                else {"cash_delta": "500"}
                                if case == "delta"
                                else {}
                            )
                        ),
                        "transaction",
                    )
                    if case == "audit":
                        self.tables["audit_log"] = []
                with self.assertRaises(EvidenceMissing):
                    self.run_periods()

    def action(self, action="split", **kwargs):
        return {
            "id": 7,
            "symbol": "A",
            "asset_type": "stock",
            "action_type": action,
            "quantity": "20",
            "target_quantity": "40",
            "ratio": "2",
            "fees": "0",
            "cash_delta": "0",
            "effective_at": "2026-01-15T10:00:00+08:00",
            **kwargs,
        }

    def test_split_reconciles_unadjusted_price_drop_without_fake_loss(self):
        self.add("corporate_action_events", self.action(), "corporate_action")

        class SplitMarks(Marks):
            def price(self, symbol, day):
                return Decimal(
                    {date(2026, 1, 2): "10", date(2026, 1, 15): "5", date(2026, 1, 30): "6"}[day]
                ), 1

        result = calculate_periods(
            self.tables,
            self.baseline,
            self.positions,
            {"A": "1"},
            date(2026, 1, 2),
            date(2026, 1, 30),
            SplitMarks(),
        )
        self.assertEqual(result[0]["profit"], "0")
        self.assertEqual(result[-1]["ending_value"], "1040")

    def test_reinvestment_and_custody_transfer_follow_actual_ledger_cash_rules(self):
        self.add(
            "corporate_action_events",
            self.action(
                "dividend_reinvestment",
                quantity="30",
                price="11",
                gross_amount="330",
                fees="1",
                cash_delta="-331",
            ),
            "corporate_action",
        )
        periods = self.run_periods()
        self.assertEqual(periods[0]["profit"], "19")
        self.assertEqual(periods[-1]["ending_value"], "1069")
        self.setUp()
        self.add(
            "corporate_action_events",
            self.action("custody_transfer", from_account="old", to_account="new"),
            "corporate_action",
        )
        self.assertEqual(self.run_periods()[-1]["ending_value"], "1040")

    def test_trade_and_dividend_corrections_apply_on_effective_date(self):
        for category in ("transaction", "dividend"):
            with self.subTest(category=category):
                self.setUp()
                if category == "transaction":
                    row = self.trade("buy", reversed_by_correction_id=7)
                    self.add("transactions", row, category)
                else:
                    self.add(
                        "dividend_events",
                        {
                            "id": 1,
                            "symbol": "A",
                            "asset_type": "stock",
                            "gross_amount": "10",
                            "fees": "2",
                            "net_amount": "8",
                            "paid_at": "2026-01-15T10:00:00+08:00",
                            "reversed_by_correction_id": 7,
                        },
                        category,
                    )
                self.add(
                    "ledger_corrections",
                    {
                        "id": 7,
                        "target_type": category,
                        "target_id": 1,
                        "effective_at": "2026-01-30T10:00:00+08:00",
                    },
                    "ledger_correction",
                )
                before = copy.deepcopy(self.tables)
                periods = self.run_periods()
                self.assertEqual(periods[0]["profit"], "19" if category == "transaction" else "28")
                self.assertEqual(periods[-1]["ending_value"], "1040")
                self.assertEqual(self.tables, before)
                self.tables["ledger_corrections"][0]["target_id"] = 999
                with self.assertRaises(EvidenceMissing):
                    self.run_periods()

    def test_fund_conversion_preserves_total_units_and_reconciles_fee(self):
        self.positions[0]["asset_type"] = "fund"

        class FundMarks(Marks):
            def price(self, symbol, day, *, asset="stock"):
                return super().price(symbol, day)

            def fund_breakdown(self, holding, start):
                return [holding]

        self.add(
            "corporate_action_events",
            self.action(
                "fund_conversion",
                asset_type="fund",
                target_symbol="B",
                quantity="10",
                target_quantity="10",
                fees="1",
                cash_delta="-1",
            ),
            "corporate_action",
        )
        periods = calculate_periods(
            self.tables,
            self.baseline,
            self.positions,
            {"A": "1"},
            date(2026, 1, 2),
            date(2026, 1, 30),
            FundMarks(),
        )
        self.assertEqual(periods[0]["profit"], "19")
        self.assertEqual(periods[-1]["ending_value"], "1039")

    def test_more_than_sixty_event_dates_are_supported_with_bounded_limit(self):
        class ConstantMarks(Marks):
            def price(self, symbol, day):
                return Decimal(10), 1

        start = date(2026, 1, 2)
        for index in range(70):
            self.add(
                "portfolio_events",
                {
                    "event_id": str(index),
                    "event_type": "deposit",
                    "status": "confirmed",
                    "amount": "1",
                    "effective_date": str(start + timedelta(days=index + 1)),
                },
                "external_cash_flow",
            )
        periods = calculate_periods(
            self.tables,
            self.baseline,
            self.positions,
            {"A": "1"},
            start,
            start + timedelta(days=71),
            ConstantMarks(),
        )
        self.assertEqual(len(periods), 71)
        self.assertEqual(periods[-1]["ending_value"], "1070")
        self.assertTrue(all(row["profit"] == "0" for row in periods))


if __name__ == "__main__":
    unittest.main()
