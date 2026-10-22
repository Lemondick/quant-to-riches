"""F8: fund_execution.py vs stock engine caliber consistency (fees / settlement).

Locks the shared conventions so a future change to one engine cannot
silently diverge from the other:

- fees: both engines quantize money to cents with ROUND_HALF_UP;
- settlement: T+N counts *trading sessions* (weekends/holidays skipped),
  never calendar days.
"""

import sys
import unittest
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant_assistant.backtest.engine import BacktestEngine
from quant_assistant.backtest.fund_execution import (
    FundExecutionModel,
    FundSettlementRule,
    FundTradingStatus,
    _TradingCalendar,
)
from quant_assistant.backtest.models import (
    DailyBar,
    ExecutionState,
    StockFeeSchedule,
)
from quant_assistant.backtest.stock_execution import StockExecutionModel
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.funds.fees import (
    FundFeeSchedule,
    PurchaseTier,
    RedemptionTier,
    subscription_fee,
)
from quant_assistant.models.entities import Action, AssetType, ProposedOrder

CENT = Decimal("0.01")


def half_up(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def fund_schedule(**overrides):
    fields = {
        "symbol": "F",
        "share_class": "A",
        "purchase_rate": Decimal("0.015"),
        "annual_service_rate": Decimal("0"),
        "redemption_tiers": (
            RedemptionTier(0, 6, Decimal("0.015")),
            RedemptionTier(7, None, Decimal("0")),
        ),
        "verified_on": date(2026, 1, 1),
    }
    fields.update(overrides)
    return FundFeeSchedule(**fields)


def stock_schedule(**overrides):
    fields = {
        "commission_rate": Decimal("0.00025"),
        "minimum_commission": Decimal("5"),
        "sell_stamp_tax_rate": Decimal("0.001"),
        "transfer_fee_rate": Decimal("0.00001"),
        "slippage_bps": Decimal("0"),
        "verified_on": date(2026, 1, 1),
    }
    fields.update(overrides)
    return StockFeeSchedule(**fields)


class FeeCaliberTests(unittest.TestCase):
    def test_fund_subscription_fee_pinned_value(self):
        # External-rate method: 10000 - 10000/1.015, half-up to cents.
        self.assertEqual(
            subscription_fee(fund_schedule(), Decimal("10000")),
            half_up("147.78325123"),
        )
        self.assertEqual(subscription_fee(fund_schedule(), Decimal("10000")), Decimal("147.78"))

    def test_fund_fee_rounds_half_up_on_tie(self):
        schedule = fund_schedule(
            purchase_rate=Decimal("0"),
            purchase_tiers=(PurchaseTier(Decimal("0"), None, None, Decimal("2.345")),),
        )
        self.assertEqual(subscription_fee(schedule, Decimal("10000")), Decimal("2.35"))

    def test_stock_fee_pinned_values(self):
        model = StockExecutionModel(stock_schedule())
        # Buy 10000: commission max(2.5, 5) + transfer 0.1.
        self.assertEqual(
            model._calculate_fees(Decimal("10000"), is_sell=False),
            Decimal("5.10"),
        )
        # Sell 10000: + stamp tax 10.
        self.assertEqual(
            model._calculate_fees(Decimal("10000"), is_sell=True),
            Decimal("15.10"),
        )

    def test_stock_fee_rounds_half_up_on_tie(self):
        model = StockExecutionModel(
            stock_schedule(
                commission_rate=Decimal("0.00125"),
                minimum_commission=Decimal("0"),
                transfer_fee_rate=Decimal("0"),
            )
        )
        # 100 * 0.00125 = 0.125 -> 0.13 under half-up (0.12 under banker's).
        self.assertEqual(model._calculate_fees(Decimal("100"), is_sell=False), Decimal("0.13"))

    def test_both_engines_agree_with_half_up(self):
        # Property: each engine's fee equals the independently rounded raw value.
        fund_raw = Decimal("10000") - Decimal("10000") / Decimal("1.015")
        self.assertEqual(
            subscription_fee(fund_schedule(), Decimal("10000")),
            fund_raw.quantize(CENT, rounding=ROUND_HALF_UP),
        )
        stock_raw = Decimal("10000") * Decimal("0.00025")
        model = StockExecutionModel(
            stock_schedule(minimum_commission=Decimal("0"), transfer_fee_rate=Decimal("0"))
        )
        self.assertEqual(
            model._calculate_fees(Decimal("10000"), is_sell=False),
            stock_raw.quantize(CENT, rounding=ROUND_HALF_UP),
        )


class SettlementCaliberTests(unittest.TestCase):
    # 2026-01-05 is a Monday; 2026-01-07 (Wed) is the test holiday.
    HOLIDAYS = (date(2026, 1, 7),)

    def calendar(self):
        return _TradingCalendar(holidays=self.HOLIDAYS)

    def test_fund_add_counts_trading_sessions(self):
        calendar = self.calendar()
        monday = date(2026, 1, 5)
        self.assertEqual(calendar.add(monday, 0), monday)
        self.assertEqual(calendar.add(monday, 1), date(2026, 1, 6))
        # Skips the Wednesday holiday: Mon +2 sessions = Tue, Thu.
        self.assertEqual(calendar.add(monday, 2), date(2026, 1, 8))
        # Skips weekends: Fri +1 session = next Monday.
        self.assertEqual(calendar.add(date(2026, 1, 9), 1), date(2026, 1, 12))

    def test_fund_submit_confirm_date_uses_session_counting(self):
        rule = FundSettlementRule(
            subscription_confirm_days=1,
            redemption_cash_days=2,
            redemption_confirm_days=1,
            holidays=self.HOLIDAYS,
        )
        submitted_on = date(2026, 1, 5)  # Monday
        model = FundExecutionModel(
            rule,
            fee_schedules={"F": fund_schedule()},
            fund_statuses={
                "F": FundTradingStatus(
                    symbol="F",
                    status_date=submitted_on,
                    latest_nav_date=submitted_on,
                    purchase_open=True,
                    redemption_open=True,
                )
            },
        )
        state = ExecutionState(cash=Decimal("100000"))
        order = ProposedOrder(
            symbol="F",
            asset_type=AssetType.FUND,
            action=Action.BUY,
            amount=Decimal("10000"),
            quantity=None,
            target_weight=Decimal("0"),
            reasons=(),
            as_of_date=date(2026, 1, 4),
            valid_until=datetime(2026, 1, 6, tzinfo=SHANGHAI_TZ),
            data_snapshot_id=0,
            strategy_version="test",
        )
        pending = model.submit(order, submitted_on, state)
        # T+1 confirmation lands on Tuesday (a trading session), not +1 day blindly.
        self.assertEqual(pending.confirm_on, date(2026, 1, 6))
        self.assertEqual(pending.confirm_on, self.calendar().add(submitted_on, 1))

    def test_stock_t_plus_one_is_next_session(self):
        model = StockExecutionModel(stock_schedule())
        trade_date = date(2026, 1, 5)
        bar = DailyBar(
            symbol="600000.SH",
            trade_date=trade_date,
            open=Decimal("10"),
            high=Decimal("10.5"),
            low=Decimal("9.8"),
            close=Decimal("10.2"),
            previous_close=Decimal("10"),
            suspended=False,
            upper_limit=Decimal("11"),
            lower_limit=Decimal("9"),
        )
        order = ProposedOrder(
            symbol="600000.SH",
            asset_type=AssetType.STOCK,
            action=Action.BUY,
            amount=Decimal("1000"),
            quantity=Decimal("100"),
            target_weight=Decimal("0"),
            reasons=(),
            as_of_date=date(2026, 1, 4),
            valid_until=datetime(2026, 1, 6, tzinfo=SHANGHAI_TZ),
            data_snapshot_id=0,
            strategy_version="test",
        )
        state = ExecutionState(cash=Decimal("100000"))
        result = model.execute(order, bar, state)
        self.assertIsNone(result.rejection)
        # Bought shares are unsettled on T: not sellable yet.
        self.assertEqual(result.state.unsettled_stock["600000.SH"], Decimal("100"))
        self.assertEqual(result.state.settled_stock.get("600000.SH", Decimal("0")), Decimal("0"))
        # At the next session the engine releases T+1 (same session-counting
        # caliber as the fund's add(T, 1)).
        released = BacktestEngine._release_stock_t_plus_one(result.state)
        self.assertEqual(released.settled_stock["600000.SH"], Decimal("100"))
        self.assertEqual(released.unsettled_stock, {})


if __name__ == "__main__":
    unittest.main()
