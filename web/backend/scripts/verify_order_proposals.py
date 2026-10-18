"""Acceptance for deterministic proposed-order generation.

``portfolio/orders.py`` turns capital-independent signals into the proposed
orders that the rest of the pipeline consumes, yet it had no acceptance
coverage. Its defining rules are risk-first and fail-closed:

* risk-reducing orders are evaluated first and bypass every exposure, cash,
  drawdown and turnover ceiling;
* risk-increasing orders are refused whenever the inputs cannot be trusted
  (expired or blocked signals, unusable drawdown, incomplete positions, unknown
  fund bucket or stock industry, duplicate instrument rows, bad snapshot id);
* every cap that trims an amount leaves an auditable reason, and the output is
  independent of caller input order.

All pure in-memory maths on ``Decimal``. No database, network or credentials.
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
from quant_assistant.models.entities import (  # noqa: E402
    Action,
    AssetType,
    Position,
    Signal,
)
from quant_assistant.portfolio.models import PortfolioState, RiskPolicy  # noqa: E402
from quant_assistant.portfolio.orders import propose_orders  # noqa: E402

DAY = date(2026, 10, 5)
FUTURE = datetime(2026, 10, 10, tzinfo=SHANGHAI_TZ)
PAST = datetime(2026, 9, 1, tzinfo=SHANGHAI_TZ)

CORE = {"symbol": "F1", "asset_type": "fund", "fund_bucket": "core"}
CORE2 = {"symbol": "F2", "asset_type": "fund", "fund_bucket": "core"}
ROTATION = {"symbol": "R1", "asset_type": "fund", "fund_bucket": "rotation"}
STOCK = {"symbol": "600000.SH", "asset_type": "stock", "industry": "银行"}


def position(symbol, asset_type, quantity, market_value, as_of=DAY):
    return Position(
        symbol=symbol,
        asset_type=asset_type,
        quantity=Decimal(quantity),
        average_cost=Decimal("10"),
        market_value=Decimal(market_value),
        as_of_date=as_of,
    )


def signal(
    symbol="F1",
    asset_type=AssetType.FUND,
    action=Action.BUY,
    score=1.0,
    target_weight="0.25",
    reasons=(),
    valid_until=FUTURE,
    as_of=DAY,
    strategy_version="v1",
):
    return Signal(
        symbol=symbol,
        asset_type=asset_type,
        action=action,
        score=score,
        target_weight=Decimal(target_weight),
        reasons=tuple(reasons),
        as_of_date=as_of,
        valid_until=valid_until,
        strategy_version=strategy_version,
    )


def state(cash="100000", total_value="100000", unit_nav="1.0", high_water_mark="1.0", positions=()):
    return PortfolioState(
        total_value=Decimal(total_value),
        cash=Decimal(cash),
        unit_nav=Decimal(unit_nav),
        high_water_mark=Decimal(high_water_mark),
        positions=tuple(positions),
        as_of_date=DAY,
    )


def instruments(*rows):
    return pd.DataFrame(list(rows))


def propose(signals, portfolio=None, prices=None, rows=(CORE,), policy=None, snapshot=7):
    return propose_orders(
        list(signals),
        portfolio or state(),
        prices or {},
        instruments(*rows),
        policy or RiskPolicy(),
        snapshot,
    )


class IncreaseBaselineTest(unittest.TestCase):
    """A clean, fully-evidenced increase still has to respect the locked caps."""

    def test_a_clean_increase_is_capped_by_the_single_fund_limit(self):
        orders = propose([signal(target_weight="0.25")])
        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual((order.symbol, order.action), ("F1", Action.BUY))
        # 25% of 100k requested, trimmed to the 15% single-fund limit.
        self.assertEqual(order.amount, Decimal("15000.00"))
        self.assertIn("单只基金上限为15%，建议金额已缩减", order.reasons)

    def test_target_weight_is_clamped_to_full_capital(self):
        orders = propose([signal(target_weight="5")])
        self.assertEqual(len(orders), 1)
        self.assertLessEqual(orders[0].target_weight, Decimal("1"))

    def test_a_target_below_current_holding_produces_no_order(self):
        portfolio = state(
            cash="50000", positions=(position("F1", AssetType.FUND, "50000", "50000"),)
        )
        # Already at 60% but only 25% is wanted: nothing to add.
        orders = propose([signal(target_weight="0.25")], portfolio)
        self.assertEqual(orders, [])

    def test_orders_carry_the_keyed_dates_and_snapshot(self):
        order = propose([signal()], snapshot=42)[0]
        self.assertEqual(order.data_snapshot_id, 42)
        self.assertEqual(order.as_of_date, DAY)
        self.assertEqual(order.valid_until, FUTURE)
        self.assertEqual(order.strategy_version, "v1")


class FailClosedIncreaseTest(unittest.TestCase):
    """Untrusted inputs must never add risk, and must fail loudly, not silently."""

    def test_an_expired_signal_adds_nothing(self):
        self.assertEqual(propose([signal(valid_until=PAST)]), [])

    def test_blocked_actions_add_nothing(self):
        for action in (Action.HOLD, Action.NO_TRADE):
            self.assertEqual(propose([signal(action=action)]), [], action)

    def test_unusable_drawdown_adds_nothing(self):
        # A zero high-water mark makes drawdown unknowable.
        portfolio = state(high_water_mark="0")
        self.assertEqual(propose([signal()], portfolio), [])

    def test_stop_drawdown_adds_nothing(self):
        portfolio = state(unit_nav="0.85", high_water_mark="1.0")  # 15% > 10% stop
        self.assertEqual(propose([signal()], portfolio), [])

    def test_incomplete_positions_add_nothing(self):
        # A stock whose value and quantity disagree cannot be trusted.
        broken = position("600001.SH", AssetType.STOCK, "0", "1000")
        portfolio = state(cash="99000", positions=(broken,))
        self.assertEqual(propose([signal()], portfolio), [])

    def test_unknown_fund_bucket_blocks_every_fund_increase(self):
        holdings = (position("R9", AssetType.FUND, "1000", "1000"),)
        portfolio = state(cash="99000", positions=holdings)
        # R9 has no bucket metadata anywhere, so no fund may be increased.
        rows = ({"symbol": "R9"}, CORE)
        self.assertEqual(propose([signal()], portfolio, rows=rows), [])

    def test_unknown_stock_industry_blocks_every_stock_increase(self):
        holdings = (position("600001.SH", AssetType.STOCK, "1000", "9000"),)
        portfolio = state(cash="91000", positions=holdings)
        rows = ({"symbol": "600001.SH", "asset_type": "stock"}, STOCK)
        orders = propose(
            [signal("600000.SH", AssetType.STOCK, target_weight="0.05")],
            portfolio,
            prices={"600000.SH": Decimal("10")},
            rows=rows,
        )
        self.assertEqual(orders, [])

    def test_a_duplicate_instrument_row_blocks_that_symbol(self):
        rows = (CORE, dict(CORE, fund_bucket="rotation"))
        self.assertEqual(propose([signal()], rows=rows), [])

    def test_snapshot_id_must_be_a_positive_integer(self):
        for bad in (0, -1, True, "7", 7.0, None):
            self.assertEqual(propose([signal()], snapshot=bad), [], repr(bad))

    def test_a_non_positive_target_weight_adds_nothing(self):
        for target in ("0", "-0.2"):
            self.assertEqual(propose([signal(target_weight=target)]), [], target)

    def test_accounted_value_must_reconcile_with_total(self):
        # cash + positions != total_value, by more than a cent.
        portfolio = state(cash="50000", total_value="100000")
        self.assertEqual(propose([signal()], portfolio), [])

    def test_a_position_dated_after_the_state_blocks_increases(self):
        ahead = position("F1", AssetType.FUND, "1000", "1000", as_of=date(2026, 10, 9))
        portfolio = state(cash="99000", positions=(ahead,))
        self.assertEqual(propose([signal()], portfolio), [])


class CapReasonTest(unittest.TestCase):
    """Every ceiling that trims an amount must say so in the order reasons."""

    def test_bucket_cap_reason(self):
        holdings = (position("F1", AssetType.FUND, "29000", "29000"),)
        portfolio = state(cash="71000", positions=holdings)
        orders = propose([signal("F2", target_weight="0.15")], portfolio, rows=(CORE, CORE2))
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].amount, Decimal("1000.00"))
        self.assertIn("核心基金资产桶上限为30%，建议金额已缩减", orders[0].reasons)

    def test_industry_cap_reason(self):
        holdings = (position("600000.SH", AssetType.STOCK, "1900", "19000"),)
        portfolio = state(cash="81000", positions=holdings)
        rows = (STOCK, {"symbol": "600001.SH", "asset_type": "stock", "industry": "银行"})
        orders = propose(
            [signal("600001.SH", AssetType.STOCK, target_weight="0.15")],
            portfolio,
            prices={"600001.SH": Decimal("10")},
            rows=rows,
        )
        self.assertEqual(len(orders), 1)
        self.assertIn("银行行业上限为20%，建议金额已缩减", orders[0].reasons)

    def test_cash_floor_reason(self):
        holdings = (
            position("F1", AssetType.FUND, "28000", "28000"),
            position("R1", AssetType.FUND, "21000", "21000"),
            position("600000.SH", AssetType.STOCK, "2000", "20000"),
        )
        portfolio = state(cash="31000", positions=holdings)
        orders = propose(
            [signal("F2", target_weight="0.15")], portfolio, rows=(CORE, CORE2, ROTATION, STOCK)
        )
        self.assertEqual(len(orders), 1)
        self.assertIn("至少保留30%现金，建议金额已缩减", orders[0].reasons)

    def test_turnover_cap_reason(self):
        policy = RiskPolicy(max_daily_turnover=Decimal("0.02"))
        orders = propose([signal("F2", target_weight="0.15")], rows=(CORE, CORE2), policy=policy)
        self.assertEqual(orders[0].amount, Decimal("2000.00"))
        self.assertIn("单日换手上限为2%，建议金额已缩减", orders[0].reasons)

    def test_a_share_lot_rounding_reason(self):
        orders = propose(
            [signal("600000.SH", AssetType.STOCK, target_weight="0.05")],
            prices={"600000.SH": Decimal("33")},
            rows=(STOCK,),
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].quantity, Decimal("100"))
        self.assertEqual(orders[0].amount, Decimal("3300.00"))
        self.assertIn("A股按100股一手向下取整", orders[0].reasons)

    def test_a_lot_smaller_than_100_shares_produces_no_order(self):
        orders = propose(
            [signal("600000.SH", AssetType.STOCK, target_weight="0.001")],
            prices={"600000.SH": Decimal("1000")},
            rows=(STOCK,),
        )
        self.assertEqual(orders, [])


class WarningDrawdownTest(unittest.TestCase):
    def test_warning_drawdown_halves_the_target_budget(self):
        calm = propose([signal()])
        warned = propose([signal()], state(unit_nav="0.93", high_water_mark="1.0"))  # 7%
        self.assertEqual(calm[0].amount, Decimal("15000.00"))
        # 25% halved to 12.5% of 100k, under the 15% single cap.
        self.assertEqual(warned[0].amount, Decimal("12500.00"))
        self.assertIn("组合回撤达到5%警戒线，新增目标仓位减半", warned[0].reasons)


class ReductionPriorityTest(unittest.TestCase):
    """De-risking must be uncontrollable by any ceiling, and must come first."""

    def test_a_sell_is_proposed_before_a_buy(self):
        holdings = (position("600000.SH", AssetType.STOCK, "100", "1000"),)
        portfolio = state(cash="99000", positions=holdings)
        orders = propose(
            [
                signal("F1", target_weight="0.25"),
                signal("600000.SH", AssetType.STOCK, Action.SELL, target_weight="0"),
            ],
            portfolio,
            rows=(CORE, STOCK),
        )
        self.assertGreaterEqual(len(orders), 2)
        self.assertEqual(orders[0].action, Action.SELL)
        self.assertEqual(orders[0].symbol, "600000.SH")

    def test_reduction_survives_an_incomplete_state(self):
        holdings = (
            position("600000.SH", AssetType.STOCK, "100", "1000"),
            position("600001.SH", AssetType.STOCK, "0", "1500"),  # broken row
        )
        portfolio = state(cash="97500", positions=holdings)
        orders = propose(
            [
                signal("600000.SH", AssetType.STOCK, Action.SELL, target_weight="0"),
                signal("F1", target_weight="0.25"),
            ],
            portfolio,
            rows=(CORE, STOCK),
        )
        self.assertEqual([order.action for order in orders], [Action.SELL])

    def test_reduction_bypasses_the_turnover_ceiling(self):
        holdings = (position("600000.SH", AssetType.STOCK, "100", "1000"),)
        portfolio = state(cash="99000", positions=holdings)
        policy = RiskPolicy(max_daily_turnover=Decimal("0"))
        orders = propose(
            [signal("600000.SH", AssetType.STOCK, Action.SELL, target_weight="0")],
            portfolio,
            rows=(STOCK,),
            policy=policy,
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].amount, Decimal("1000.00"))
        self.assertIn("降低风险的订单优先，豁免单日换手上限", orders[0].reasons)

    def test_selling_odd_lots_is_allowed(self):
        holdings = (position("600000.SH", AssetType.STOCK, "150", "1500"),)
        portfolio = state(cash="98500", positions=holdings)
        orders = propose(
            [signal("600000.SH", AssetType.STOCK, Action.SELL, target_weight="0")],
            portfolio,
            rows=(STOCK,),
        )
        self.assertEqual(orders[0].quantity, Decimal("150"))
        self.assertIn("清仓允许卖出持仓中的零股", orders[0].reasons)

    def test_reducing_to_a_target_at_or_above_the_holding_is_a_no_op(self):
        holdings = (position("F1", AssetType.FUND, "50000", "50000"),)
        portfolio = state(cash="50000", positions=holdings)
        orders = propose([signal("F1", action=Action.REDUCE, target_weight="0.6")], portfolio)
        self.assertEqual(orders, [])

    def test_sell_proceeds_do_not_finance_buys(self):
        """A proposal is not a fill: unreceived cash must not be spent.

        Cash sits just below the 30% floor, so the buy is refused purely on the
        cash floor. Crediting the unsettled sale proceeds would clear that floor
        and let the buy through, so this isolates the rule from the turnover
        ceiling that the sell also consumes.
        """
        holdings = (
            position("600000.SH", AssetType.STOCK, "100", "1000"),
            position("R1", AssetType.FUND, "69100", "69100"),
        )
        portfolio = state(cash="29900", positions=holdings)
        orders = propose(
            [
                signal("600000.SH", AssetType.STOCK, Action.SELL, target_weight="0"),
                signal("F1", target_weight="0.01"),
            ],
            portfolio,
            rows=(CORE, ROTATION, STOCK),
        )
        self.assertEqual(
            [(order.action, order.symbol) for order in orders],
            [(Action.SELL, "600000.SH")],
            "an unsettled sale must not fund a buy",
        )


class DeterminismTest(unittest.TestCase):
    def test_output_is_independent_of_caller_order(self):
        low = signal("F1", score=0.5, target_weight="0.10")
        high = signal("F2", score=0.9, target_weight="0.10")
        rows = (CORE, CORE2)
        forward = propose([low, high], rows=rows)
        backward = propose([high, low], rows=rows)
        self.assertEqual(
            [(order.symbol, order.amount) for order in forward],
            [(order.symbol, order.amount) for order in backward],
        )
        # Highest score is considered first, so it takes the earlier capacity.
        self.assertEqual(forward[0].symbol, "F2")

    def test_identical_input_yields_identical_output(self):
        first = propose([signal()])
        second = propose([signal()])
        self.assertEqual(first, second)

    def test_disagreeing_signals_resolve_to_one_order_per_symbol(self):
        """Sell priority must win over a same-symbol buy."""
        holdings = (position("600000.SH", AssetType.STOCK, "100", "1000"),)
        portfolio = state(cash="99000", positions=holdings)
        orders = propose(
            [
                signal("600000.SH", AssetType.STOCK, Action.SELL, target_weight="0"),
                signal("600000.SH", AssetType.STOCK, Action.BUY, target_weight="0.05"),
            ],
            portfolio,
            prices={"600000.SH": Decimal("10")},
            rows=(STOCK,),
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].action, Action.SELL)


if __name__ == "__main__":
    unittest.main()
