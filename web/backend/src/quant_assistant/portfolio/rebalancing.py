"""Read-only rebalancing sandbox with fund fee safety gates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

import pandas as pd

from quant_assistant.funds.fees import FundFeeSchedule


class RebalanceAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    BLOCKED = "blocked"


def _money(value: object, name: str) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    return number


@dataclass(frozen=True, slots=True)
class RebalanceConfig:
    tolerance: Decimal = Decimal("0.005")
    minimum_order_amount: Decimal = Decimal("1")
    penalty_holding_days: int = 7
    penalty_rate: Decimal = Decimal("0.015")

    def __post_init__(self) -> None:
        for name in ("tolerance", "minimum_order_amount", "penalty_rate"):
            value = _money(getattr(self, name), name)
            if name == "tolerance" and value > 1:
                raise ValueError("tolerance must be between 0 and 1")
            if name == "penalty_rate" and value > 1:
                raise ValueError("penalty_rate must be between 0 and 1")
            if name == "minimum_order_amount" and value <= 0:
                raise ValueError("minimum_order_amount must be positive")
            object.__setattr__(self, name, value)
        if isinstance(self.penalty_holding_days, bool) or self.penalty_holding_days < 0:
            raise ValueError("penalty_holding_days must be non-negative")


@dataclass(frozen=True, slots=True)
class RebalancePlan:
    as_of_date: date
    total_value: Decimal
    target_weights: Mapping[str, Decimal]
    orders: pd.DataFrame
    blockers: tuple[str, ...] = ()

    @property
    def executable_orders(self) -> pd.DataFrame:
        if self.orders.empty:
            return self.orders.copy()
        return self.orders.loc[self.orders["executable"].astype(bool)].copy()

    @property
    def has_blockers(self) -> bool:
        return bool(self.blockers) or bool(
            not self.orders.empty and self.orders["action"].eq(RebalanceAction.BLOCKED.value).any()
        )


def _tier_rate(schedule: FundFeeSchedule, holding_days: int) -> Decimal | None:
    for tier in schedule.redemption_tiers:
        if tier.includes(holding_days):
            return tier.rate
    return None


def _normalise_targets(target_weights: Mapping[str, object]) -> dict[str, Decimal]:
    if not isinstance(target_weights, Mapping):
        raise TypeError("target_weights must be a mapping")
    result: dict[str, Decimal] = {}
    for symbol, raw in target_weights.items():
        name = str(symbol).strip()
        if not name:
            raise ValueError("target symbol must not be empty")
        value = _money(raw, f"target weight for {name}")
        if value > 1:
            raise ValueError("target weight must be between 0 and 1")
        result[name] = value
    if sum(result.values(), Decimal("0")) > Decimal("1.0000001"):
        raise ValueError("target weights cannot exceed 100%")
    return result


def build_rebalance_plan(
    current_positions: pd.DataFrame,
    target_weights: Mapping[str, object],
    *,
    total_value: Decimal | None = None,
    as_of_date: date | None = None,
    fee_schedules: Mapping[str, FundFeeSchedule] | None = None,
    config: RebalanceConfig | None = None,
) -> RebalancePlan:
    """Calculate a manual buy/sell list; no broker side effect is possible."""

    if not isinstance(current_positions, pd.DataFrame):
        raise TypeError("current_positions must be a pandas DataFrame")
    config = config or RebalanceConfig()
    targets = _normalise_targets(target_weights)
    if as_of_date is not None and not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date or None")
    if current_positions.empty:
        current = pd.DataFrame(columns=["symbol", "market_value", "asset_type", "held_days"])
    else:
        current = current_positions.copy(deep=True)
        if "symbol" not in current.columns or "market_value" not in current.columns:
            raise ValueError("current_positions requires symbol and market_value")
        current["symbol"] = current["symbol"].astype(str).str.strip()
        current["market_value"] = pd.to_numeric(current["market_value"], errors="coerce")
        current = current.loc[current["symbol"].ne("") & current["market_value"].notna()].copy()
        asset_values = (
            current["asset_type"]
            if "asset_type" in current
            else pd.Series("fund", index=current.index)
        )
        held_values = (
            current["held_days"] if "held_days" in current else pd.Series(0, index=current.index)
        )
        current["asset_type"] = asset_values.astype(str).str.lower()
        current["held_days"] = pd.to_numeric(held_values, errors="coerce").fillna(0).astype(int)
        current = current.groupby(["symbol", "asset_type"], as_index=False, sort=True).agg(
            market_value=("market_value", "sum"), held_days=("held_days", "min")
        )
    inferred_total = float(current["market_value"].sum()) if not current.empty else 0.0
    total = _money(total_value if total_value is not None else inferred_total, "total_value")
    if total <= 0:
        raise ValueError("total_value must be positive")
    present = {
        (str(row.symbol), str(row.asset_type)): row for row in current.itertuples(index=False)
    }
    symbols = sorted(set(targets) | {symbol for symbol, _ in present})
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    schedules = fee_schedules or {}
    for symbol in symbols:
        current_row = next((row for (key, _), row in present.items() if key == symbol), None)
        current_value = (
            Decimal(str(float(current_row.market_value)))
            if current_row is not None
            else Decimal("0")
        )
        asset_type = str(current_row.asset_type) if current_row is not None else "fund"
        held_days = int(current_row.held_days) if current_row is not None else 0
        target_weight = targets.get(symbol, Decimal("0"))
        target_value = (total * target_weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        delta = target_value - current_value
        current_weight = current_value / total
        if (
            abs(delta) < config.minimum_order_amount
            or abs(current_weight - target_weight) <= config.tolerance
        ):
            action = RebalanceAction.HOLD
            amount = Decimal("0")
        elif delta > 0:
            action = RebalanceAction.BUY
            amount = delta
        else:
            action = RebalanceAction.SELL
            amount = abs(delta)
        rate: Decimal | None = Decimal("0")
        reasons: list[str] = []
        order_blockers: list[str] = []
        schedule = schedules.get(symbol)
        if action in {RebalanceAction.BUY, RebalanceAction.SELL} and asset_type == "fund":
            if not isinstance(schedule, FundFeeSchedule):
                order_blockers.append("基金费率证据缺失，禁止生成可执行调仓单")
            elif action is RebalanceAction.BUY:
                rate = schedule.purchase_rate
            else:
                rate = _tier_rate(schedule, held_days)
                if rate is None:
                    order_blockers.append("持有期没有唯一赎回费率阶梯")
                elif held_days < config.penalty_holding_days and rate >= config.penalty_rate:
                    order_blockers.append(
                        f"持有{held_days}天，触发{rate:.2%}惩罚性赎回费，系统拦截"
                    )
                    reasons.append("建议等待满7天后再评估")
        fee = (amount * (rate or Decimal("0"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        executable = action in {RebalanceAction.BUY, RebalanceAction.SELL} and not order_blockers
        if order_blockers:
            action = RebalanceAction.BLOCKED
            blockers.extend(order_blockers)
        rows.append(
            {
                "symbol": symbol,
                "asset_type": asset_type,
                "current_value": current_value,
                "current_weight": current_weight,
                "target_weight": target_weight,
                "target_value": target_value,
                "delta": delta,
                "held_days": held_days,
                "fee_rate": rate,
                "estimated_fee": fee,
                "net_amount": amount - fee if action is RebalanceAction.SELL else amount + fee,
                "amount": amount,
                "action": action.value,
                "executable": executable,
                "reasons": tuple(dict.fromkeys(reasons + order_blockers)),
            }
        )
    orders = pd.DataFrame(rows)
    return RebalancePlan(
        as_of_date=as_of_date or date.today(),
        total_value=total,
        target_weights=targets,
        orders=orders,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def manual_order_markdown(plan: RebalancePlan) -> str:
    """Render a copyable manual execution list with the safety status visible."""

    if not isinstance(plan, RebalancePlan):
        raise TypeError("plan must be a RebalancePlan")
    lines = [
        f"# 调仓操作清单（{plan.as_of_date.isoformat()}）",
        "",
        f"组合市值：¥{plan.total_value:,.2f}",
        "",
        "| 代码 | 动作 | 金额 | 预计费用 | 状态 | 说明 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    if plan.orders.empty:
        lines.append("| — | 无需调仓 | 0.00 | 0.00 | 观察 | 当前权重在容忍区间内 |")
    else:
        for row in plan.orders.itertuples(index=False):
            reasons = "；".join(row.reasons) if row.reasons else "—"
            status = "可人工执行" if row.executable else "已拦截"
            lines.append(
                f"| {row.symbol} | {row.action} | {row.amount:.2f} | "
                f"{row.estimated_fee:.2f} | {status} | {reasons} |"
            )
    lines.extend(("", "> 此清单仅用于人工复核，不会登录支付宝/券商，也不会自动下单。"))
    return "\n".join(lines) + "\n"


# A descriptive alias for callers that prefer a verb.
calculate_rebalance = build_rebalance_plan
simulate_rebalancing = build_rebalance_plan


__all__ = [
    "RebalanceAction",
    "RebalanceConfig",
    "RebalancePlan",
    "build_rebalance_plan",
    "calculate_rebalance",
    "manual_order_markdown",
    "simulate_rebalancing",
]
