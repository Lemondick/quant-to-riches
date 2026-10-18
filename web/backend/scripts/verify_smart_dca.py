"""Acceptance for evidence-gated Smart DCA proposals.

``strategies/smart_dca.py`` turns a point-in-time valuation snapshot into one
explainable contribution proposal. It had no acceptance coverage. Its defining
rules:

* a proposal is **blocked** whenever the valuation percentile cannot be
  established — missing PE/PB evidence must never be read as a neutral market;
* the percentile is computed point-in-time (observations after the target date
  are excluded) and ties resolve conservatively;
* every action that is not ``invest`` must be impossible to mistake for a
  recommendation: ``hold`` keeps the computed amount visible but is never
  executable, and ``take_profit`` zeroes it.

Pure in-memory maths on ``Decimal``. No database, network or credentials.
"""

import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

import pandas as pd  # noqa: E402

from quant_assistant.common.time import SHANGHAI_TZ  # noqa: E402
from quant_assistant.strategies.smart_dca import (  # noqa: E402
    SmartDcaAction,
    SmartDcaConfig,
    SmartDcaInput,
    calculate_smart_dca,
    valuation_percentile,
)

DAY = date(2026, 10, 5)
# 100 ascending observations: pe=N lands exactly at the Nth percentile.
HISTORY = pd.DataFrame(
    {
        "valuation_date": pd.bdate_range("2024-01-01", periods=100),
        "pe": [float(value) for value in range(1, 101)],
    }
)


def item(pe=50.0, **overrides):
    values = {
        "symbol": "000001",
        "as_of_date": DAY,
        "base_amount": Decimal("1000"),
        "current_price": 1.5,
        "ma120": 1.4,
        "ma200": 1.3,
        "valuation_history": HISTORY,
        "pe": pe,
    }
    values.update(overrides)
    return SmartDcaInput(**values)


def decide(**kwargs):
    return calculate_smart_dca(item(**kwargs))


class ConfigValidationTest(unittest.TestCase):
    def test_defaults_are_coherent(self):
        config = SmartDcaConfig()
        self.assertLess(config.low_valuation_percentile, config.high_valuation_percentile)
        self.assertLessEqual(config.min_trend_multiplier, config.max_trend_multiplier)

    def test_invalid_values_are_rejected(self):
        cases = (
            {"min_valuation_observations": 1},
            {"min_valuation_observations": True},
            {"low_valuation_percentile": 1.5},
            {"high_valuation_percentile": -0.1},
            {"low_valuation_percentile": 0.8, "high_valuation_percentile": 0.2},
            {"low_valuation_multiplier": Decimal("-1")},
            {"minimum_investment": Decimal("0")},
            {"amount_rounding": Decimal("0")},
            {"min_trend_multiplier": Decimal("2"), "max_trend_multiplier": Decimal("1")},
            {"ma_sensitivity": -1.0},
            {"target_annualized_return": 1.0},
            {"trailing_stop_drawdown": -0.1},
        )
        for overrides in cases:
            with self.assertRaises(ValueError, msg=overrides):
                SmartDcaConfig(**overrides)

    def test_optional_targets_can_be_disabled(self):
        config = SmartDcaConfig(target_annualized_return=None, trailing_stop_drawdown=None)
        self.assertIsNone(config.target_annualized_return)


class InputValidationTest(unittest.TestCase):
    def test_a_datetime_as_of_date_is_rejected(self):
        """datetime subclasses date; a timestamp breaks the history filter."""
        for bad in (
            datetime(2026, 10, 5, 15, 0, tzinfo=SHANGHAI_TZ),
            20261005,
            "2026-10-05",
        ):
            with self.assertRaises(TypeError):
                item(as_of_date=bad)

    def test_structural_input_rules(self):
        with self.assertRaisesRegex(ValueError, "symbol must not be empty"):
            item(symbol="  ")
        with self.assertRaisesRegex(ValueError, "base_amount"):
            item(base_amount=Decimal("-1"))
        for field in ("current_price", "ma120", "ma200"):
            with self.assertRaises(ValueError, msg=field):
                item(**{field: 0.0})
        with self.assertRaisesRegex(ValueError, "holding_days"):
            item(holding_days=-1)


class PercentileTest(unittest.TestCase):
    def test_the_percentile_is_empirical_and_inclusive(self):
        # 50 of the 100 observations are <= 50, so the tie resolves to 0.50.
        self.assertEqual(valuation_percentile(50.0, HISTORY, DAY, minimum_observations=10), 0.5)

    def test_observations_after_the_target_date_are_excluded(self):
        future_heavy = pd.DataFrame(
            {
                "valuation_date": pd.bdate_range("2026-01-01", periods=120),
                "pe": [float(value) for value in range(1, 121)],
            }
        )
        # Truncated to March, the history holds 1..~40, so 50 sits above all of it.
        self.assertEqual(
            valuation_percentile(50.0, future_heavy, date(2026, 3, 1), minimum_observations=10),
            1.0,
        )

    def test_too_few_observations_return_none(self):
        self.assertIsNone(
            valuation_percentile(50.0, HISTORY.head(10), DAY, minimum_observations=60)
        )

    def test_a_missing_current_value_returns_none(self):
        self.assertIsNone(valuation_percentile(None, HISTORY, DAY, minimum_observations=10))

    def test_non_positive_observations_are_dropped(self):
        values = [0.0, -1.0, 5.0, 10.0]
        self.assertEqual(valuation_percentile(5.0, values, DAY, minimum_observations=2), 0.5)

    def test_a_dataframe_without_a_recognised_value_column_is_empty(self):
        frame = pd.DataFrame({"valuation_date": pd.bdate_range("2024-01-01", periods=100)})
        self.assertIsNone(valuation_percentile(50.0, frame, DAY, minimum_observations=10))


class DecisionTest(unittest.TestCase):
    def test_a_neutral_valuation_invests_the_trend_adjusted_amount(self):
        decision = decide(pe=50.0)
        self.assertEqual(decision.action, SmartDcaAction.INVEST)
        self.assertEqual(decision.valuation_multiplier, Decimal("1"))
        # 1000 * 1.0 * 0.9286, rounded down to the cent.
        self.assertEqual(decision.amount, Decimal("928.60"))
        self.assertTrue(decision.executable)
        self.assertIn("估值分位50.0%处于中性区间", decision.reasons)
        self.assertIn("建议投入928.60元", decision.reasons)

    def test_a_low_valuation_scales_up(self):
        decision = decide(pe=10.0)
        self.assertEqual(decision.action, SmartDcaAction.INVEST)
        self.assertEqual(decision.valuation_multiplier, Decimal("1.50"))
        self.assertEqual(decision.amount, Decimal("1392.90"))
        self.assertIn("估值分位10.0%≤低估阈值，放大定投", decision.reasons)

    def test_a_high_valuation_holds_instead_of_investing(self):
        decision = decide(pe=95.0)
        self.assertEqual(decision.action, SmartDcaAction.HOLD)
        self.assertEqual(decision.valuation_multiplier, Decimal("0.50"))
        self.assertIn("高估区仅保留观察，不增加风险敞口", decision.reasons)

    def test_hold_keeps_the_computed_amount_but_is_not_executable(self):
        """A hold must not be mistaken for a recommendation to pay money."""
        decision = decide(pe=95.0)
        self.assertGreater(decision.amount, 0)
        self.assertFalse(decision.executable)

    def test_missing_evidence_blocks_the_proposal(self):
        decision = decide(pe=50.0, valuation_history=HISTORY.head(10))
        self.assertEqual(decision.action, SmartDcaAction.BLOCKED)
        self.assertEqual(decision.amount, Decimal("0"))
        self.assertEqual(
            decision.blockers, ("PE/PB历史分位证据不足（需要按目标日截断的历史序列）",)
        )
        self.assertFalse(decision.executable)

    def test_the_amount_is_rounded_down_to_the_configured_step(self):
        """123.45 * 0.9286 = 114.6357 -> 114.63, never rounded up to 114.64."""
        decision = decide(pe=50.0, base_amount=Decimal("123.45"))
        self.assertEqual(decision.amount, Decimal("114.63"))

    def test_an_amount_below_the_minimum_pauses(self):
        decision = decide(pe=50.0, base_amount=Decimal("0.01"))
        self.assertEqual(decision.action, SmartDcaAction.PAUSE)
        self.assertIn("动态金额低于最小定投金额，暂停本次投入", decision.reasons)


class TakeProfitTest(unittest.TestCase):
    def test_reaching_the_target_annualized_return_takes_profit(self):
        decision = decide(pe=50.0, current_cost=1.0, holding_days=365)
        # 1.5 / 1.0 over 365 days annualises to exactly 50%.
        self.assertAlmostEqual(decision.annualized_return, 0.5, places=10)
        config = SmartDcaConfig(target_annualized_return=0.05)
        decision = calculate_smart_dca(item(pe=50.0, current_cost=1.0, holding_days=365), config)
        self.assertEqual(decision.action, SmartDcaAction.TAKE_PROFIT)
        self.assertEqual(decision.amount, Decimal("0"))
        self.assertIn("达到目标年化收益，触发目标止盈", decision.reasons)

    def test_reaching_the_trailing_drawdown_takes_profit(self):
        decision = decide(pe=50.0, high_water_mark=1.7)
        self.assertAlmostEqual(decision.trailing_drawdown, 1 - 1.5 / 1.7, places=10)
        self.assertEqual(decision.action, SmartDcaAction.TAKE_PROFIT)
        self.assertIn("达到高位回撤阈值，触发移动止盈", decision.reasons)

    def test_a_below_target_gain_keeps_investing(self):
        # 1.5 / 1.4 over 365 days annualises to ~7.1%, below the 12% target.
        decision = decide(pe=50.0, current_cost=1.4, holding_days=365)
        self.assertLess(decision.annualized_return, 0.12)
        self.assertEqual(decision.action, SmartDcaAction.INVEST)

    def test_disabled_targets_never_take_profit(self):
        config = SmartDcaConfig(target_annualized_return=None, trailing_stop_drawdown=None)
        decision = calculate_smart_dca(
            item(pe=50.0, current_cost=1.0, holding_days=365, high_water_mark=10.0), config
        )
        self.assertEqual(decision.action, SmartDcaAction.INVEST)


class TrendMultiplierTest(unittest.TestCase):
    def test_the_multiplier_reacts_inversely_to_deviation(self):
        decision = decide(pe=50.0)
        # price 1.5 / ma120 1.4 -> deviation 7.14%, multiplier 0.9286.
        self.assertAlmostEqual(decision.trend_deviation, 1.5 / 1.4 - 1.0, places=10)
        self.assertEqual(decision.trend_multiplier, Decimal("0.9286"))

    def test_the_multiplier_is_clamped_to_the_configured_bounds(self):
        upper = decide(pe=50.0, current_price=0.7, ma120=1.4)  # deviation -50%
        self.assertEqual(upper.trend_multiplier, Decimal("1.5000"))
        lower = decide(pe=50.0, current_price=2.1, ma120=1.4)  # deviation +50%
        self.assertEqual(lower.trend_multiplier, Decimal("0.5000"))

    def test_losing_the_ma120_ma200_trend_caps_the_multiplier(self):
        decision = decide(pe=50.0, ma200=1.5)  # ma120 (1.4) below ma200
        self.assertEqual(decision.trend_multiplier, Decimal("0.7500"))
        self.assertIn("MA120未站上MA200，趋势门槛偏谨慎", decision.reasons)


class OutputHygieneTest(unittest.TestCase):
    def test_reasons_are_deduplicated_and_ordered(self):
        decision = decide(pe=50.0)
        self.assertEqual(len(decision.reasons), len(set(decision.reasons)))

    def test_blockers_and_reasons_stay_separate(self):
        decision = decide(pe=50.0, valuation_history=HISTORY.head(10))
        self.assertTrue(decision.blockers)
        self.assertNotIn("PE/PB历史分位证据不足", decision.reasons)
        self.assertIn("证据门禁未通过，不生成投入金额", decision.reasons)

    def test_identical_input_yields_an_identical_decision(self):
        self.assertEqual(decide(pe=50.0), decide(pe=50.0))

    def test_a_sequence_history_behaves_like_a_frame(self):
        sequence = [float(value) for value in range(1, 101)]
        from_frame = decide(pe=50.0)
        from_sequence = calculate_smart_dca(item(pe=50.0, valuation_history=sequence))
        self.assertEqual(from_frame.action, from_sequence.action)
        self.assertEqual(from_frame.amount, from_sequence.amount)


if __name__ == "__main__":
    unittest.main()
