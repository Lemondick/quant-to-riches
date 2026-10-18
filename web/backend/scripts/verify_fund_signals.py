"""Acceptance for fee-aware, benchmark-driven fund signals.

``strategies/funds.py`` turns a fund pool into the signals the daily decision
consumes, and it had no acceptance coverage. Its defining rules are fee-aware
and fail-closed:

* a share class may only be recommended when a verified product-specific fee
  schedule exists for **both** legs of the A/C family;
* the chosen (target) share must be demonstrably purchasable — an unknown or
  unparsable flag is treated as blocked, not as open;
* every blocked family still yields an explainable signal rather than silence;
* exactly one signal is emitted per family, deterministically.

Also locks the date strictness shared by the three strategy entry points: they
must reject a ``datetime`` ``as_of_date``, because ``datetime`` subclasses
``date`` and would otherwise slip through into date arithmetic and raise an
opaque ``TypeError`` deep inside the strategy.

Pure in-memory frames. No database, network or credentials.
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
from quant_assistant.funds.fees import FundFeeSchedule, RedemptionTier  # noqa: E402
from quant_assistant.models.entities import Action  # noqa: E402
from quant_assistant.strategies.funds import (  # noqa: E402
    FundStrategyConfig,
    benchmark_score,
    generate_fund_signals,
)

DAY = date(2026, 10, 5)
FEE_COLUMNS = {
    "ret_20": 0.10,
    "ret_60": 0.20,
    "ret_120": 0.30,
    "volatility_20": 0.40,
    "close": 120.0,
    "ma_120": 100.0,
    "ma_200": 90.0,
}
GOOD = dict(FEE_COLUMNS)
DOWNTREND = dict(FEE_COLUMNS, close=80.0)
NEGATIVE = dict(FEE_COLUMNS, ret_120=-0.30, close=80.0)


def schedule(symbol, share_class, purchase="0.0015", service="0.012"):
    return FundFeeSchedule(
        symbol=symbol,
        share_class=share_class,
        purchase_rate=Decimal(purchase),
        annual_service_rate=Decimal(service),
        redemption_tiers=(RedemptionTier(0, None, Decimal("0.005")),),
        verified_on=DAY,
    )


def pool_row(symbol, share_class, bucket="core", as_of=DAY):
    return {
        "symbol": symbol,
        "fund_symbol": symbol,
        "family_id": "FAM1",
        "share_class": share_class,
        "benchmark_symbol": "000300.SH",
        "as_of_date": pd.Timestamp(as_of),
        "fund_bucket": bucket,
    }


def status_row(symbol, purchasable=True, redeemable=True, as_of=DAY):
    return {
        "symbol": symbol,
        "is_purchasable": purchasable,
        "is_redeemable": redeemable,
        "nav_date": pd.Timestamp(as_of),
    }


def generate(
    benchmark=GOOD, pool=None, status=None, fee_schedules=None, as_of=DAY, raw_status=None
):
    benchmark_frame = pd.DataFrame(
        [{"symbol": "000300.SH", "trade_date": pd.Timestamp(as_of), **benchmark}]
    )
    rows = pool if pool is not None else [pool_row("000001", "A"), pool_row("000002", "C")]
    fund_pool = pd.DataFrame(rows)
    fund_status = (
        raw_status if raw_status is not None else pd.DataFrame(status if status is not None else [])
    )
    schedules = fee_schedules if fee_schedules is not None else {}
    return generate_fund_signals(fund_pool, benchmark_frame, fund_status, schedules, as_of, None)


def both_schedules():
    return {
        "000001": schedule("000001", "A", "0.0015", "0.012"),
        "000002": schedule("000002", "C", "0.0", "0.008"),
    }


OPEN_STATUS = [status_row("000001"), status_row("000002")]


class BenchmarkScoreTest(unittest.TestCase):
    def test_the_momentum_weights_are_locked(self):
        # 0.25 * 10% + 0.35 * 20% + 0.40 * 30% - 0.25 * 40% = 0.115
        score, trend_ok = benchmark_score(GOOD)
        self.assertAlmostEqual(score, 0.115, places=10)
        self.assertTrue(trend_ok)

    def test_the_trend_gate_needs_both_moving_averages(self):
        cases = (
            (dict(GOOD, close=90.0), False),  # close below ma_120
            (dict(GOOD, ma_120=90.0, ma_200=95.0), False),  # ma_120 below ma_200
            (dict(GOOD, close=100.0, ma_120=100.0, ma_200=90.0), False),  # not strictly above
            (dict(GOOD, close=100.0, ma_120=100.0, ma_200=100.0), False),
        )
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(benchmark_score(values)[1], expected)

    def test_a_mapping_and_a_series_score_alike(self):
        row = pd.Series(GOOD)
        self.assertEqual(benchmark_score(row), benchmark_score(GOOD))


class ConfigValidationTest(unittest.TestCase):
    def test_defaults_are_coherent(self):
        config = FundStrategyConfig()
        self.assertLess(config.rotation_target_weight, config.core_target_weight)
        self.assertGreaterEqual(config.rotation_holding_days, config.minimum_rotation_days)

    def test_invalid_values_are_rejected(self):
        cases = (
            {"version": "  "},
            {"core_holding_days": 0},
            {"rotation_holding_days": -1},
            {"minimum_rotation_days": 1.5},
            {"core_holding_days": True},
            {"rotation_holding_days": 60, "minimum_rotation_days": 90},
            {"fee_comparison_amount": Decimal("0")},
            {"fee_comparison_amount": 5000},
            {"core_target_weight": Decimal("1.5")},
            {"rotation_target_weight": Decimal("-0.1")},
            {"core_target_weight": 0.15},
        )
        for overrides in cases:
            with self.assertRaises((ValueError, TypeError), msg=overrides):
                FundStrategyConfig(**overrides)


class InputValidationTest(unittest.TestCase):
    def test_a_datetime_as_of_date_is_rejected(self):
        """datetime subclasses date; it would break every later date comparison."""
        for bad in (
            datetime(2026, 10, 5, 15, 0, tzinfo=SHANGHAI_TZ),
            20261005,
            "2026-10-05",
        ):
            with self.assertRaisesRegex(TypeError, "as_of_date must be a date"):
                generate(as_of=bad)

    def test_expected_holding_days_must_be_a_non_negative_integer_or_none(self):
        for bad in (True, -1, 1.5, "90"):
            with self.assertRaisesRegex(ValueError, "expected_holding_days"):
                generate(expected=None) if False else generate_fund_signals(
                    pd.DataFrame([pool_row("000001", "A")]),
                    pd.DataFrame(
                        [{"symbol": "000300.SH", "trade_date": pd.Timestamp(DAY), **GOOD}]
                    ),
                    pd.DataFrame(),
                    {},
                    DAY,
                    bad,
                )

    def test_structural_input_types_are_checked(self):
        with self.assertRaisesRegex(TypeError, "fund_status must be a pandas DataFrame"):
            generate(raw_status={})
        with self.assertRaisesRegex(TypeError, "fee_schedules must be a mapping"):
            generate_fund_signals(
                pd.DataFrame([pool_row("000001", "A")]),
                pd.DataFrame([{"symbol": "000300.SH", "trade_date": pd.Timestamp(DAY), **GOOD}]),
                pd.DataFrame(),
                [],
                DAY,
                None,
            )


class DateStrictnessTest(unittest.TestCase):
    """All three strategy entry points share the same date rule."""

    def test_each_strategy_rejects_a_datetime_as_of_date(self):
        from quant_assistant.strategies.macro_allocation import calculate_macro_allocation
        from quant_assistant.strategies.sector_rotation import calculate_sector_rotation

        empty = pd.DataFrame()
        cases = (
            ("funds", lambda d: generate(as_of=d)),
            ("macro_allocation", lambda d: calculate_macro_allocation(empty, d)),
            ("sector_rotation", lambda d: calculate_sector_rotation(empty, d)),
        )
        for name, call in cases:
            with self.subTest(strategy=name):
                with self.assertRaises(TypeError):
                    call(datetime(2026, 10, 5, 15, 0, tzinfo=SHANGHAI_TZ))
                call(DAY)  # the calendar-date path must keep working


class SignalGenerationTest(unittest.TestCase):
    def test_an_empty_pool_yields_no_signals(self):
        self.assertEqual(generate(pool=[]), [])

    def test_pool_rows_dated_after_as_of_are_ignored(self):
        signals = generate(pool=[pool_row("000001", "A", as_of=date(2026, 12, 31))])
        self.assertEqual(signals, [])

    def test_exactly_one_signal_is_emitted_per_family(self):
        rows = [
            pool_row("000001", "A", bucket="core"),
            pool_row("000002", "C", bucket="core"),
            pool_row("000003", "A", bucket="rotation"),
            pool_row("000004", "C", bucket="rotation"),
        ]
        rows[0]["family_id"] = "CORE"
        rows[1]["family_id"] = "CORE"
        rows[2]["family_id"] = "ROT"
        rows[3]["family_id"] = "ROT"
        signals = generate(pool=rows, status=OPEN_STATUS, fee_schedules=both_schedules())
        self.assertEqual(len(signals), 2)
        self.assertEqual({signal.reasons for signal in signals} and len(signals), 2)

    def test_identical_input_yields_identical_signals(self):
        self.assertEqual(
            generate(status=OPEN_STATUS, fee_schedules=both_schedules()),
            generate(status=OPEN_STATUS, fee_schedules=both_schedules()),
        )

    def test_signals_carry_the_strategy_version_and_calendar_dates(self):
        signals = generate(status=OPEN_STATUS, fee_schedules=both_schedules())
        signal = signals[0]
        self.assertEqual(signal.strategy_version, "fund-index-v1")
        self.assertEqual(signal.as_of_date, DAY)


class FailClosedTest(unittest.TestCase):
    """A blocked family must still produce an explainable signal, never silence."""

    def test_a_missing_fee_schedule_blocks_the_recommendation(self):
        signals = generate(status=OPEN_STATUS, fee_schedules={})
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.action, Action.NO_TRADE)
        self.assertEqual(signal.target_weight, Decimal("0"))
        self.assertIn("A/C份额映射或产品具体费率不完整，禁止推荐具体份额", signal.reasons)

    def test_a_one_sided_family_cannot_be_priced(self):
        """Only the A leg exists, so the A/C comparison cannot be made."""
        signals = generate(
            pool=[pool_row("000001", "A")],
            status=[status_row("000001")],
            fee_schedules={"000001": schedule("000001", "A")},
        )
        self.assertEqual(signals[0].action, Action.NO_TRADE)

    def test_a_schedule_for_a_different_symbol_is_not_used(self):
        schedules = {
            "000001": schedule("000001", "A"),
            "000002": schedule("WRONG", "C"),  # symbol mismatch
        }
        signals = generate(status=OPEN_STATUS, fee_schedules=schedules)
        self.assertEqual(signals[0].action, Action.NO_TRADE)

    def test_an_empty_status_table_blocks_with_a_clear_reason(self):
        signals = generate(status=[], fee_schedules=both_schedules())
        self.assertEqual(signals[0].action, Action.NO_TRADE)
        self.assertIn("缺少目标份额的最新净值与开放状态", signals[0].reasons)

    def test_an_unparsable_open_flag_is_treated_as_blocked(self):
        """An unreadable flag must not be read as "open".

        The C share is the fee-selected target, so its unreadable flag is the
        one that has to block the family.
        """
        rows = [
            status_row("000001"),
            dict(status_row("000002"), is_purchasable="不清楚"),
        ]
        signals = generate(status=rows, fee_schedules=both_schedules())
        self.assertEqual(signals[0].action, Action.NO_TRADE)
        self.assertIn("目标份额暂停申购或申购状态未确认", signals[0].reasons)

    def test_a_closed_target_share_blocks_but_a_closed_non_target_does_not(self):
        """Only the chosen share must be purchasable; the other leg is irrelevant."""
        closed_c = [status_row("000001"), status_row("000002", purchasable=False)]
        blocked = generate(status=closed_c, fee_schedules=both_schedules())
        self.assertEqual(blocked[0].action, Action.NO_TRADE)
        self.assertIn("目标份额暂停申购或申购状态未确认", blocked[0].reasons)

        closed_a = [status_row("000001", purchasable=False), status_row("000002")]
        allowed = generate(status=closed_a, fee_schedules=both_schedules())
        self.assertEqual(allowed[0].action, Action.BUY)


class FeeAwareSelectionTest(unittest.TestCase):
    def test_a_healthy_benchmark_buys_the_cheaper_share(self):
        signals = generate(status=OPEN_STATUS, fee_schedules=both_schedules())
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.action, Action.BUY)
        self.assertEqual(signal.symbol, "000002")  # the C share is cheaper here
        self.assertEqual(signal.target_weight, Decimal("0.15"))
        self.assertIn("基准000300.SH得分0.1150为正且满足120/200日上升趋势", signal.reasons)
        self.assertTrue(any("基金策略桶：核心" in reason for reason in signal.reasons))
        self.assertTrue(any("C类预计费用较低" in reason for reason in signal.reasons))

    def test_a_rotation_bucket_uses_the_rotation_weight(self):
        rows = [
            pool_row("000001", "A", bucket="rotation"),
            pool_row("000002", "C", bucket="rotation"),
        ]
        signals = generate(pool=rows, status=OPEN_STATUS, fee_schedules=both_schedules())
        self.assertEqual(signals[0].target_weight, Decimal("0.10"))

    def test_a_downtrend_does_not_buy_but_stays_explainable(self):
        signals = generate(benchmark=DOWNTREND, status=OPEN_STATUS, fee_schedules=both_schedules())
        self.assertEqual(signals[0].action, Action.HOLD)
        self.assertIn("基准000300.SH未同时满足买入或卖出规则", signals[0].reasons)

    def test_a_negative_score_signals_a_sell(self):
        signals = generate(benchmark=NEGATIVE, status=OPEN_STATUS, fee_schedules=both_schedules())
        self.assertEqual(signals[0].action, Action.SELL)
        self.assertIn("基准000300.SH得分-0.1250为负且跌破120日均线", signals[0].reasons)

    def test_the_expected_holding_days_drive_the_fee_window(self):
        config = FundStrategyConfig(rotation_holding_days=30, minimum_rotation_days=30)
        fee_schedules = {
            "000001": schedule("000001", "A"),
            "000002": schedule("000002", "C"),
        }
        long_window = generate(
            status=OPEN_STATUS,
            fee_schedules=fee_schedules,
        )
        short_window = generate_fund_signals(
            pd.DataFrame([pool_row("000001", "A"), pool_row("000002", "C")]),
            pd.DataFrame([{"symbol": "000300.SH", "trade_date": pd.Timestamp(DAY), **GOOD}]),
            pd.DataFrame(OPEN_STATUS),
            fee_schedules,
            DAY,
            30,
            config,
        )
        long_reason = next(r for r in long_window[0].reasons if "预计费用" in r)
        short_reason = next(r for r in short_window[0].reasons if "预计费用" in r)
        self.assertNotEqual(long_reason, short_reason)


if __name__ == "__main__":
    unittest.main()
