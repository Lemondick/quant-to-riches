"""Synthetic replay tests for the strategy's point-in-time boundary."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pandas as pd

from quant_assistant.backtest.engine import BacktestEngine, BacktestRequest, FrameBacktestData
from quant_assistant.backtest.models import StockFeeSchedule
from quant_assistant.backtest.production import ProductionStockBacktestStrategy
from quant_assistant.backtest.stock_execution import StockExecutionModel
from quant_assistant.backtest.stock_features import PreparedStockFeatures
from quant_assistant.features.technical import (
    calculate_stock_feature_history,
    calculate_stock_features,
)


def synthetic_loader(sessions=210, symbols=2):
    dates = tuple(pd.bdate_range("2024-01-02", periods=sessions).date)
    prices = pd.DataFrame(
        [
            {
                "symbol": f"{symbol:06d}.SZ",
                "trade_date": day,
                "open": 10 + index / 100 + symbol,
                "high": 11 + index / 100 + symbol,
                "low": 9 + index / 100 + symbol,
                "close": 10 + index / 100 + symbol,
                "volume": 100000,
                "amount": 10000000,
            }
            for index, day in enumerate(dates)
            for symbol in range(1, symbols + 1)
        ]
    )
    instruments = pd.DataFrame(
        [
            {
                "symbol": f"{symbol:06d}.SZ",
                "as_of_date": dates[0],
                "listed_on": date(2020, 1, 1),
                "delisted_on": None,
                "board": "main",
                "industry": "synthetic",
                "is_st": False,
                "is_suspended": False,
            }
            for symbol in range(1, symbols + 1)
        ]
    )
    return FrameBacktestData(
        prices, instruments, {day: index + 1 for index, day in enumerate(dates)}
    )


def replay(loader, strategy, *, last_sessions=4):
    dates = loader.trading_dates(date(2000, 1, 1), date(2099, 1, 1))
    fees = StockFeeSchedule(
        commission_rate=Decimal("0"),
        minimum_commission=Decimal("0"),
        sell_stamp_tax_rate=Decimal("0"),
        transfer_fee_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        verified_on=date(2000, 1, 1),
    )
    return BacktestEngine(
        loader,
        stock_execution=StockExecutionModel(fees),
        strategy=strategy,
        order_proposer=lambda *args: [],
    ).run(BacktestRequest(dates[-last_sessions], dates[-1], Decimal("10000"), "synthetic"))


class BacktestCausalityTests(unittest.TestCase):
    def test_future_price_attribute_is_not_available_to_a_strategy(self):
        observations = []

        def strategy(context):
            try:
                future = context.prices_all
            except AttributeError:
                observations.append("denied")
            else:
                observations.append(len(future))
            return []

        replay(synthetic_loader(), strategy)
        self.assertEqual(observations, ["denied"] * 4)

    def test_price_prefix_includes_cutoff_and_cannot_mutate_source(self):
        loader = synthetic_loader()
        original = loader.prices.copy(deep=True)
        observations = []

        def strategy(context):
            observations.append(
                (
                    max(context.prices.trade_date),
                    context.as_of_date,
                    context.execution_date in set(context.prices.trade_date),
                )
            )
            context.prices.loc[:, "close"] = -999
            context.instruments.loc[:, "symbol"] = "changed"
            return []

        replay(loader, strategy)
        self.assertEqual(len(observations), 4)
        self.assertTrue(
            all(latest == cutoff and not future for latest, cutoff, future in observations)
        )
        pd.testing.assert_frame_equal(loader.prices, original)
        self.assertNotIn("changed", set(loader.instruments.symbol))

    def test_prefix_calculator_never_receives_future_rows(self):
        observations = []
        strategy = ProductionStockBacktestStrategy()
        active_cutoff = []

        def calculate(source):
            observations.append((max(source.trade_date), active_cutoff[-1]))
            return calculate_stock_feature_history(source)

        def callback(context):
            active_cutoff.append(context.as_of_date)
            strategy._features_on(context, context.as_of_date)
            return []

        with patch(
            "quant_assistant.backtest.production.calculate_stock_feature_history", calculate
        ):
            result = replay(synthetic_loader(), callback)
        self.assertEqual(len(observations), 4)
        self.assertTrue(all(latest <= cutoff for latest, cutoff in observations))
        self.assertFalse(any(item.startswith("strategy_error:") for item in result.limitations))

    def test_future_perturbation_does_not_change_past_features(self):
        first = synthetic_loader()
        dates = first.trading_dates(date(2000, 1, 1), date(2099, 1, 1))
        second_prices = first.prices.copy(deep=True)
        second_prices.loc[second_prices.trade_date.gt(dates[-3]), "close"] *= 100
        second = FrameBacktestData(second_prices, first.instruments, first.snapshot_ids)
        observations = []
        for loader in (first, second):
            strategy = ProductionStockBacktestStrategy()
            rows = []

            def callback(context, rows=rows, strategy=strategy):
                if context.as_of_date <= dates[-3]:
                    rows.append(strategy._features_on(context, context.as_of_date))
                return []

            result = replay(loader, callback)
            self.assertFalse(any(item.startswith("strategy_error:") for item in result.limitations))
            observations.append(rows)
        self.assertEqual(len(observations[0]), 3)
        for left, right in zip(*observations, strict=True):
            pd.testing.assert_frame_equal(left, right)

    def test_future_metadata_and_missing_snapshot_gates_remain(self):
        loader = synthetic_loader()
        missing = FrameBacktestData(loader.prices, loader.instruments)
        result = replay(missing, lambda context: [])
        self.assertTrue(
            any(item.startswith("missing_data_snapshot_id:") for item in result.limitations)
        )
        dates = loader.trading_dates(date(2000, 1, 1), date(2099, 1, 1))
        observations = []
        instruments = loader.instruments.copy(deep=True)
        instruments.loc[0, "listed_on"] = dates[-1]
        instruments.loc[1, "delisted_on"] = dates[-5]

        def callback(context):
            observations.append(context.instruments.empty)
            return []

        replay(FrameBacktestData(loader.prices, instruments, loader.snapshot_ids), callback)
        self.assertEqual(observations, [True] * 4)

    def test_prepared_view_is_past_only_detached_and_matches_prefix_calculation(self):
        observations = []
        loader = synthetic_loader()

        def callback(context):
            view = context.stock_features
            current = view.on(context.as_of_date)
            expected = calculate_stock_features(context.prices, context.as_of_date)
            expected["symbol"] = expected["symbol"].astype(object)
            observations.append((context.as_of_date, view.trading_dates, current.copy(), expected))
            current.loc[:, "close"] = -123
            observations.append(view.on(context.as_of_date).close.gt(0).all())
            try:
                view.on(context.execution_date)
            except ValueError:
                observations.append("denied")
            return []

        callback.requires_stock_features = True
        result = replay(loader, callback)
        self.assertFalse(any(item.startswith("strategy_error:") for item in result.limitations))
        self.assertEqual(len(observations), 12)
        for cutoff, dates, current, expected in observations[::3]:
            self.assertTrue(all(day <= cutoff for day in dates))
            columns = list(current.columns)
            pd.testing.assert_frame_equal(current, expected[columns])
        self.assertTrue(all(observations[1::3]))
        self.assertEqual(observations[2::3], ["denied"] * 4)

    def test_engine_prepares_features_once_and_new_run_invalidates_strategy_cache(self):
        loader = synthetic_loader()
        strategy = ProductionStockBacktestStrategy()
        observations = []

        def callback(context):
            observations.append(strategy._features_on(context, context.as_of_date))
            return []

        callback.requires_stock_features = True
        with patch(
            "quant_assistant.features.technical.calculate_stock_feature_history",
            wraps=calculate_stock_feature_history,
        ) as calculate:
            result = replay(loader, callback)
            self.assertFalse(any(item.startswith("strategy_error:") for item in result.limitations))
            self.assertEqual(calculate.call_count, 1)
        changed = loader.prices.copy(deep=True)
        changed.loc[:, "close"] *= 2
        second = FrameBacktestData(changed, loader.instruments, loader.snapshot_ids)
        replay(second, callback)
        self.assertEqual(len(observations), 8)
        self.assertAlmostEqual(observations[4].close.iloc[0], observations[0].close.iloc[0] * 2)

    def test_prepared_features_are_unchanged_when_only_future_prices_change(self):
        loader = synthetic_loader()
        dates = loader._trading_date_index
        changed = loader.prices.copy(deep=True)
        changed.loc[changed.trade_date.gt(dates[-3]), "close"] *= 100
        observations = []
        for source in (loader, FrameBacktestData(changed, loader.instruments, loader.snapshot_ids)):
            strategy = ProductionStockBacktestStrategy()
            rows = []

            def callback(context, rows=rows, strategy=strategy):
                if context.as_of_date <= dates[-3]:
                    rows.append(strategy._features_on(context, context.as_of_date))
                return []

            callback.requires_stock_features = True
            result = replay(source, callback)
            self.assertFalse(any(item.startswith("strategy_error:") for item in result.limitations))
            observations.append(rows)
        self.assertEqual(len(observations[0]), 3)
        for left, right in zip(*observations, strict=True):
            pd.testing.assert_frame_equal(left, right)

    def test_incomplete_feature_columns_stay_explicit_strategy_limitations(self):
        loader = synthetic_loader()
        incomplete = FrameBacktestData(
            loader.prices.drop(columns=["amount"]), loader.instruments, loader.snapshot_ids
        )
        result = replay(incomplete, ProductionStockBacktestStrategy())
        self.assertIn("strategy_error:ValueError", result.limitations)

    def test_builtin_strategy_runs_without_future_or_feature_errors(self):
        result = replay(synthetic_loader(), ProductionStockBacktestStrategy())
        self.assertFalse(any(item.startswith("strategy_error:") for item in result.limitations))
        self.assertEqual(len(result.nav), 4)

    def test_prepared_prior_week_and_suspended_symbol_match_historical_prefix(self):
        loader = synthetic_loader()
        day = loader._trading_date_index[-1]
        prices = loader.prices.loc[
            ~((loader.prices.symbol == "000001.SZ") & loader.prices.trade_date.eq(day))
        ].copy()
        prepared = PreparedStockFeatures(prices)
        view = prepared.view(day)
        prior = max(
            value for value in view.trading_dates if value.isocalendar()[:2] < day.isocalendar()[:2]
        )
        for cutoff in (day, prior):
            expected = calculate_stock_features(prices.loc[prices.trade_date.le(cutoff)], cutoff)
            actual = view.on(cutoff)
            # Calendar alignment labels the observation date; rolling values stay causal.
            expected["trade_date"] = cutoff
            expected["symbol"] = expected["symbol"].astype(object)
            pd.testing.assert_frame_equal(actual, expected[list(actual.columns)])

    def test_builtin_prepared_and_prefix_paths_generate_the_same_signals(self):
        loader = synthetic_loader()
        prices = loader.prices.copy()
        prices["amount"] = 100000000
        benchmark = prices.loc[prices.symbol.eq("000001.SZ")].assign(symbol="000300.SH")
        prices = pd.concat([prices, benchmark], ignore_index=True)
        loader = FrameBacktestData(prices, loader.instruments, loader.snapshot_ids)

        class PrefixOnlyLoader:
            def __getattr__(self, name):
                return getattr(loader, name)

        class RecordingStrategy(ProductionStockBacktestStrategy):
            def __init__(self):
                super().__init__()
                self.observations = []
                self.errors = []

            def __call__(self, context):
                try:
                    signals = super().__call__(context)
                except Exception as exc:
                    self.errors.append(repr(exc))
                    raise
                self.observations.append(tuple(str(signal) for signal in signals))
                return signals

        recorded = []
        for source in (loader, PrefixOnlyLoader()):
            strategy = RecordingStrategy()
            result = replay(source, strategy)
            self.assertEqual(strategy.errors, [])
            self.assertFalse(any(item.startswith("strategy_error:") for item in result.limitations))
            self.assertEqual(len(strategy.observations), 4)
            recorded.append(strategy.observations)
        self.assertTrue(any(recorded[0]))
        self.assertEqual(*recorded)


if __name__ == "__main__":
    unittest.main()
