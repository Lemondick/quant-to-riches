"""Focused integration regressions with account snapshots and hand-derived values."""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from analysis_context import FACTS
from calendar_generation import execute_calendar
from cost_operations import execute_cost
from paid_operations import execute_paid
from portfolio_backtest_operations import portfolio_backtest
from risk_operations import execute_risk
from smart_dca_backtest_operations import _rules, smart_dca_backtest

from quant_assistant.common.time import SHANGHAI_TZ


class NyseCalendarRegressions(unittest.TestCase):
    def test_new_year_on_saturday_does_not_close_prior_december_31(self):
        # NYSE published 2026-2028 calendar: no holiday observed for Jan 1, 2028.
        # https://www.nyse.com/trade/hours-calendars (checked 2026-10-22).
        holidays = execute_calendar("calendar.generate_next_year", {"market": "US", "year": 2027})[
            "holidays"
        ]
        self.assertNotIn("2027-12-31", holidays)
        self.assertIn("2027-01-01", holidays)
        self.assertIn("2027-06-18", holidays)
        sunday = execute_calendar("calendar.generate_next_year", {"market": "US", "year": 2023})[
            "holidays"
        ]
        self.assertIn("2023-01-02", sunday)


class PortfolioSnapshotRegressions(unittest.TestCase):
    def test_rebalance_preserves_the_pre_trade_historical_value(self):
        days = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 2, 2)]
        result = portfolio_backtest(
            [{"code": "AAA", "weight": "50"}, {"code": "BBB", "weight": "50"}],
            days[0],
            days[-1],
            {"mode": "period", "period": "1M"},
            initial_capital=Decimal("200"),
            nav_series={
                "AAA": list(zip(days, [Decimal("1"), Decimal("2"), Decimal("2")], strict=True)),
                "BBB": [(day, Decimal("1")) for day in days],
            },
        )
        self.assertEqual(
            [point["value"] for point in result["curve"]], ["199.70", "299.48", "299.48"]
        )
        self.assertEqual(result["curve"][-1]["invested"], "200")
        self.assertEqual(len(result["rebalance_events"]), 1)

    def test_monthly_contributions_value_only_shares_owned_on_each_date(self):
        result = portfolio_backtest(
            [{"code": "AAA", "weight": "1"}],
            date(2026, 1, 1),
            date(2026, 2, 2),
            {"mode": "period", "period": "1M"},
            {"amount": "100", "freq": "monthly"},
            initial_capital=Decimal("0"),
            nav_series={
                "AAA": [
                    (date(2026, 1, 1), Decimal("1")),
                    (date(2026, 2, 1), Decimal("1")),
                    (date(2026, 2, 2), Decimal("1")),
                ]
            },
        )
        self.assertEqual(
            [
                {key: point[key] for key in ("date", "invested", "value")}
                for point in result["curve"]
            ],
            [
                {"date": "2026-01-01", "invested": "100", "value": "99.85"},
                {"date": "2026-02-01", "invested": "200", "value": "199.70"},
                {"date": "2026-02-02", "invested": "200", "value": "199.70"},
            ],
        )
        self.assertEqual(result["metrics"]["total_invested"], "200")
        unit_nav = [point["unit_nav"] for point in result["curve"]]
        self.assertAlmostEqual(unit_nav[0], 1)
        self.assertAlmostEqual(unit_nav[1], (199.70 - 100) / 99.85)
        self.assertAlmostEqual(unit_nav[2], unit_nav[1])
        self.assertAlmostEqual(result["metrics"]["total_return"], unit_nav[-1] - 1)
        self.assertIn("sharpe_zero_rate", result["metrics"])
        # First purchase establishes the baseline; only the next 0.15 fee
        # reduces unit NAV: (199.70 - 100) / 99.85 - 1.
        self.assertAlmostEqual(result["metrics"]["max_drawdown"], -0.0015022533800701)

    def test_price_loss_and_inflow_have_cashflow_adjusted_drawdown(self):
        result = portfolio_backtest(
            [{"code": "AAA", "weight": "1"}],
            date(2026, 1, 1),
            date(2026, 2, 2),
            {"mode": "period", "period": "1M"},
            {"amount": "100", "freq": "monthly"},
            initial_capital=Decimal("0"),
            nav_series={
                "AAA": [
                    (date(2026, 1, 1), Decimal("1")),
                    (date(2026, 2, 1), Decimal("0.8")),
                    (date(2026, 2, 2), Decimal("0.8")),
                ]
            },
        )
        self.assertEqual(result["curve"][0]["value"], "99.85")
        self.assertEqual(result["metrics"]["final_value"], "179.73")
        # Existing holdings lose 19.97; the second deposit also pays 0.15.
        self.assertAlmostEqual(result["metrics"]["max_drawdown"], -0.2015022533800701)

    def test_optional_rebalance_and_weekly_contributions(self):
        days = [date(2026, 1, day) for day in (1, 2, 5, 9, 12)]
        result = portfolio_backtest(
            [{"code": "AAA", "weight": "100"}],
            days[0],
            days[-1],
            None,
            {"amount": "100", "freq": "weekly"},
            initial_capital=Decimal("0"),
            nav_series={"AAA": [(day, Decimal("1")) for day in days]},
        )
        self.assertEqual(result["metrics"]["total_invested"], "300")
        self.assertEqual(result["metrics"]["final_value"], "299.55")
        self.assertEqual(result["curve"][-1]["invested"], "300")
        self.assertEqual(result["rebalance_events"], [])


class SmartDcaSavedRuleRegressions(unittest.TestCase):
    def test_saved_halt_thresholds_cover_the_repository_fraction_range(self):
        for halt in (1, 50, 70, 100):
            rules = _rules({"low_pct_boost": 1, "high_pct_cut": 0.9, "halt_pct": halt})
            self.assertEqual(rules["halt_pct"], halt)

    def test_saved_adjustment_keys_control_contribution_multipliers(self):
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(200)]
        result = smart_dca_backtest(
            "000300",
            days[0],
            days[-1],
            Decimal("100"),
            {"low_pct_boost": 1, "high_pct_cut": 0.9, "halt_pct": 95},
            price_series=[(day, Decimal("1")) for day in days],
            valuation_series=[(day, 200 - i) for i, day in enumerate(days)],
        )
        boosted = [
            record
            for record in result["contributions"]
            if record["percentile"] is not None and record["percentile"] <= 30
        ]
        self.assertTrue(boosted)
        self.assertTrue(all(Decimal(record["amount"]) == Decimal("200") for record in boosted))
        # Choose a high but below-halt percentile from real valuation history.
        valuations = [(day, float(i)) for i, day in enumerate(days)]
        for index, day in enumerate(days):
            if day.day == 1 and index >= 60:
                valuations[index] = (day, (index - 1) * 0.8)
        result = smart_dca_backtest(
            "000300",
            days[0],
            days[-1],
            Decimal("100"),
            {"low_pct_boost": 1, "high_pct_cut": 0.9, "halt_pct": 95},
            price_series=[(day, Decimal("1")) for day in days],
            valuation_series=valuations,
        )
        cut = [
            record
            for record in result["contributions"]
            if record["percentile"] is not None and 70 <= record["percentile"] < 95
        ]
        self.assertTrue(cut)
        self.assertTrue(all(Decimal(record["amount"]) == Decimal("10") for record in cut))


class AccountAnalyticalRegressions(unittest.TestCase):
    @staticmethod
    def snapshot(user_id, total_value, quantity, market_value):
        tables = {table: [] for table in FACTS}
        tables["portfolio_valuations"] = [
            {
                "run_id": f"run-{index}",
                "as_of_date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "generated_at": "2026-02-01T00:00:00Z",
                "data_snapshot_id": 1,
                "total_value": str(total_value),
                "cash": "0",
                "unit_nav": "1",
                "high_water_mark": "1",
            }
            for index in range(30)
        ]
        tables["positions"] = [
            {
                "symbol": "AAA",
                "asset_type": "fund",
                "quantity": str(quantity),
                "average_cost": "1",
                "market_value": str(market_value),
                "as_of_date": "2026-01-30",
            }
        ]
        tables["transactions"] = [
            {
                "symbol": "AAA",
                "asset_type": "fund",
                "action": "buy",
                "quantity": str(quantity),
                "price": "1",
                "fees": "0",
                "traded_at": "2026-01-01T00:00:00Z",
            }
        ]
        return {"version": 1, "user_id": user_id, "role": "user", "tables": tables}

    def test_risk_and_cost_use_already_isolated_snapshot_tables(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"QUANT_SITE_ROOT": directory}),
        ):
            for user_id, total, quantity, value in (("a" * 32, 1000, 2, 4), ("b" * 32, 9000, 3, 9)):
                snapshot = self.snapshot(user_id, total, quantity, value)
                risk = execute_risk("risk.metrics", {"snapshot": snapshot, "portfolio_id": user_id})
                self.assertEqual(risk["n_points"], 30)
                self.assertEqual(risk["max_drawdown"], 0)
                stress = execute_risk(
                    "risk.stress_test", {"snapshot": snapshot, "portfolio_id": user_id}
                )
                self.assertEqual(
                    stress["scenarios"][0]["est_loss_amount"], -486 if quantity == 2 else -4374
                )
                costs = execute_cost(
                    "cost.positions", {"snapshot": snapshot, "portfolio_id": user_id}
                )
                self.assertEqual(Decimal(costs["summary"]["total_value"]), Decimal(value))
                self.assertEqual(Decimal(costs["summary"]["total_cost"]), Decimal(quantity))

    def test_cost_reconstructs_lots_from_account_snapshot(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"QUANT_SITE_ROOT": directory}),
        ):
            user_id = "c" * 32
            costs = execute_cost(
                "cost.positions",
                {"snapshot": self.snapshot(user_id, 1000, 2, 4), "portfolio_id": user_id},
            )
            self.assertEqual(Decimal(costs["summary"]["total_value"]), Decimal("4"))
            self.assertEqual(Decimal(costs["summary"]["total_cost"]), Decimal("2"))


class FullResearchPublicationRegressions(unittest.TestCase):
    def test_successful_full_engine_uses_report_and_usage_publication_envelope(self):
        task = {
            "credentials": {
                "endpoint": "https://models.example.com/v1",
                "api_key": "synthetic-key",
                "jev_api_key": "synthetic-key",
            },
            "models": {"strategy": "quick", "reviewer": "deep", "decision": "jev", "news": "quick"},
            "_execution": {
                "api": "http://127.0.0.1/internal/v1/worker",
                "secret": "x" * 40,
                "lease": {},
            },
            "research_engine": "tradingagents_full",
            "preview_expires_at": (datetime.now(SHANGHAI_TZ) + timedelta(days=1)).isoformat(),
            "preview_content_hash": "hash",
            "preview_parameters": {},
            "snapshot": {},
            "request": {
                "symbol": "600519.SH",
                "data_date": "2026-10-20",
                "facts": [],
                "warnings": [],
            },
        }
        verdict = {
            "answers": {
                "evidence": {"noul": 1.0},
                "candidate_0": {
                    "choice": "research_candidate",
                    "confidence": 1.0,
                    "probabilities": {"research_candidate": 1.0},
                },
            }
        }
        upstream = {
            "missing_data_complete": True,
            "analysts": {"market": "synthetic report"},
            "usage": {
                "llm_calls": 1,
                "tool_calls": 2,
                "input_tokens": 3,
                "output_tokens": 4,
                "usage_status": "reported",
            },
        }
        with (
            patch("paid_operations.AdvisorModels") as models,
            patch(
                "paid_operations.execute_strategy",
                return_value={"_evidence": {"content_hash": "hash"}},
            ),
            patch("tradingagents_operations.execute_full_tradingagents", return_value=upstream),
        ):
            models.return_value.decide.return_value = verdict
            result = execute_paid("agent.research", task)
        self.assertEqual(result["report"]["engine"], "tradingagents-astock-full")
        self.assertEqual(
            result["report"]["tradingagents"]["analysts"]["market"], "synthetic report"
        )
        self.assertEqual(result["report"]["final_status"], "research_candidate")
        self.assertEqual(result["usage"], upstream["usage"])


if __name__ == "__main__":
    unittest.main()
