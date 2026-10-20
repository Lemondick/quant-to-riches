"""Daily financial observations must not depend on which conflicting row came last."""

import unittest
from dataclasses import asdict
from decimal import Decimal

import pandas as pd

from quant_assistant.backtest.metrics import calculate_performance


def frames():
    days = pd.date_range("2026-01-05", periods=25)
    nav = pd.DataFrame(
        {
            "trade_date": days,
            "total_value": [Decimal("100") + Decimal(number) / 10 for number in range(25)],
            "external_cash_flow": Decimal("0"),
            "fees": Decimal("0.01"),
            "turnover_amount": Decimal("1"),
        }
    )
    benchmark = pd.DataFrame({"trade_date": days, "close": [200 + number for number in range(25)]})
    return nav, benchmark


class DuplicateObservationTests(unittest.TestCase):
    def test_conflicting_nav_cashflow_fee_and_turnover_are_rejected_in_both_orders(self):
        for column, changed in (
            ("total_value", Decimal("101")),
            ("external_cash_flow", Decimal("1")),
            ("fees", Decimal("0.02")),
            ("turnover_amount", Decimal("2")),
        ):
            nav, benchmark = frames()
            duplicate = nav.iloc[[0]].copy()
            duplicate[column] = changed
            for first in (False, True):
                rows = [duplicate, nav] if first else [nav, duplicate]
                with (
                    self.subTest(column=column, first=first),
                    self.assertRaisesRegex(ValueError, "conflicting_duplicate_date"),
                ):
                    calculate_performance(pd.concat(rows, ignore_index=True), benchmark)

    def test_conflicting_benchmark_close_is_rejected(self):
        nav, benchmark = frames()
        duplicate = benchmark.iloc[[0]].copy()
        duplicate["close"] = 201
        with self.assertRaisesRegex(ValueError, "conflicting_duplicate_date"):
            calculate_performance(nav, pd.concat([benchmark, duplicate], ignore_index=True))

    def test_decimal_difference_is_detected_before_float_rounding(self):
        nav, benchmark = frames()
        duplicate = nav.iloc[[0]].copy()
        duplicate["total_value"] = Decimal("100.000000000000000001")
        self.assertEqual(float(duplicate.iloc[0]["total_value"]), float(nav.iloc[0]["total_value"]))
        with self.assertRaisesRegex(ValueError, "conflicting_duplicate_date"):
            calculate_performance(pd.concat([nav, duplicate], ignore_index=True), benchmark)

    def test_exact_duplicates_and_random_input_order_preserve_all_metrics(self):
        nav, benchmark = frames()
        expected = asdict(calculate_performance(nav, benchmark))
        for seed in range(8):
            shuffled_nav = pd.concat([nav, nav.iloc[[0, 4, 12]]], ignore_index=True).sample(
                frac=1, random_state=seed
            )
            shuffled_benchmark = pd.concat(
                [benchmark, benchmark.iloc[[0, 9]]], ignore_index=True
            ).sample(frac=1, random_state=seed)
            with self.subTest(seed=seed):
                self.assertEqual(
                    expected, asdict(calculate_performance(shuffled_nav, shuffled_benchmark))
                )

    def test_intraday_labels_normalize_to_same_daily_observation(self):
        nav, benchmark = frames()
        nav["trade_date"] = nav["trade_date"].dt.strftime("%Y-%m-%dT00:00:00")
        duplicate = nav.iloc[[0]].copy()
        duplicate["trade_date"] = "2026-01-05T18:00:00"
        expected = asdict(calculate_performance(nav, benchmark))
        self.assertEqual(
            expected,
            asdict(
                calculate_performance(pd.concat([nav, duplicate], ignore_index=True), benchmark)
            ),
        )
        duplicate["external_cash_flow"] = Decimal("1")
        with self.assertRaisesRegex(ValueError, "conflicting_duplicate_date"):
            calculate_performance(pd.concat([nav, duplicate], ignore_index=True), benchmark)

    def test_datetime_index_uses_same_daily_conflict_rule(self):
        nav, benchmark = frames()
        nav = nav.set_index("trade_date")
        nav.index = nav.index.tz_localize("Asia/Shanghai")
        benchmark = benchmark.set_index("trade_date")
        benchmark.index = benchmark.index.tz_localize("Asia/Shanghai")
        duplicate = nav.iloc[[0]].copy()
        duplicate.index = pd.DatetimeIndex([duplicate.index[0].replace(hour=18)])
        duplicate["external_cash_flow"] = Decimal("1")
        with self.assertRaisesRegex(ValueError, "conflicting_duplicate_date"):
            calculate_performance(pd.concat([nav, duplicate]), benchmark)

    def test_missing_dates_are_invalid_for_both_nav_and_benchmark(self):
        for missing in ("nav", "benchmark"):
            nav, benchmark = frames()
            target = nav if missing == "nav" else benchmark
            target.loc[0, "trade_date"] = pd.NaT
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "invalid dates"):
                calculate_performance(nav, benchmark)

    def test_missing_index_label_is_not_silently_treated_as_an_observation(self):
        nav, benchmark = frames()
        nav = nav.drop(columns="trade_date")
        benchmark = benchmark.drop(columns="trade_date")
        nav.index = benchmark.index = pd.Index([None, *range(1, 25)])
        with self.assertRaisesRegex(ValueError, "invalid index dates"):
            calculate_performance(nav, benchmark)


if __name__ == "__main__":
    unittest.main()
