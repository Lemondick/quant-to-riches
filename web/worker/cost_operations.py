"""Per-fund holding-cost analysis (``cost.positions``).

Contract: payload ``{funds: [{fund_code, current_nav, lots: [{acquired_at,
shares, cost_amount, redemption_tiers?}]}]}`` or ``{portfolio_id}`` (DB path)
→ per-fund ``{shares, cost_amount, current_value, floating_pnl,
floating_pnl_pct, est_redemption_fee, annual_return_xirr, breakeven_nav}``
+ portfolio summary + warnings.

Methodology (documented assumptions):
- ``cost_amount`` = 买入支出（含已付申购费）的总成本；``current_value`` =
  持有份额 × 当前净值；浮动盈亏 = 市值 − 成本（未扣赎回费）。
- 预估赎回费口径与 ``FundFeeSchedule``（``funds/fees.py``）一致：
  每笔 lot 按持有天数匹配 ``redemption_tiers``，赎回金额 = 份额 × 当前净值，
  与 ``fund.redemption_windows`` 相同的分级费率口径（申购/管理费腿置零，
  只用赎回档位）。
- 年化收益率（XIRR 简化版）：把每笔买入视为一次性现金流出、期末市值视为
  一次性流入，复用 ``backtest_shared.xirr`` 二分法求解。忽略期间分红、
  再投入与部分赎回的现金流细节；不足两个不同日期或无解时为 ``None``。
- 回本净值线 = (成本 + 预估赎回费) / 持有份额；无费率数据时按成本/份额
  并注明未含赎回费。
- DB 路径（``portfolio_id`` = 账本 ``user_id``）：从 ``transactions`` 按
  时间顺序 FIFO 轧差买卖重建 lots（lot 成本 = price × quantity + fees），
  以 ``positions`` 表的 quantity/market_value 倒推当前净值；无逐笔交易时
  退化为按 ``average_cost`` 的持仓级估算并打 warning。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

COST_CAPABILITIES = ("cost.positions",)


def _to_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _to_date(value: object, field: str) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date") from exc
    raise ValueError(f"{field} must be an ISO date string")


def _to_tiers(raw: object, field: str) -> tuple:
    from quant_assistant.funds.fees import RedemptionTier

    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError(f"{field} must be a non-empty list")
    tiers = []
    for position, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"{field}[{position}] must be a mapping")
        min_days = item.get("min_days")
        if isinstance(min_days, bool) or not isinstance(min_days, int) or min_days < 0:
            raise ValueError(f"{field}[{position}].min_days must be a non-negative integer")
        max_days = item.get("max_days")
        if max_days is not None and (
            isinstance(max_days, bool) or not isinstance(max_days, int) or max_days < min_days
        ):
            raise ValueError(f"{field}[{position}].max_days must be an integer >= min_days")
        tiers.append(
            RedemptionTier(
                min_days=min_days,
                max_days=max_days,
                rate=_to_decimal(item.get("rate"), f"{field}[{position}].rate"),
            )
        )
    return tuple(tiers)


def _redemption_fee_estimate(
    fund_code: str,
    lots: Sequence[Mapping],
    current_nav: Decimal,
    today: date,
) -> tuple[Decimal | None, list[str]]:
    """Sum per-lot redemption fees using the FundFeeSchedule caliber.

    Returns (fee_total, warnings); fee_total is None when any lot lacks tiers
    (documented: we never invent a fee schedule).
    """
    from quant_assistant.funds.fees import (
        FundFeeSchedule,
        FundRedemptionLot,
        estimate_redemption_lots,
    )

    total = Decimal("0")
    warnings: list[str] = []
    for position, lot in enumerate(lots):
        tiers_raw = lot.get("redemption_tiers")
        if tiers_raw is None:
            warnings.append(f"{fund_code}: lot#{position} 无赎回费率档位，预估赎回费不含该笔")
            return None, warnings
        schedule = FundFeeSchedule(
            symbol=fund_code,
            share_class="A",
            purchase_rate=Decimal("0"),
            annual_service_rate=Decimal("0"),
            redemption_tiers=_to_tiers(tiers_raw, f"lot#{position}.redemption_tiers"),
            verified_on=today,
        )
        shares = lot["_shares"]
        evaluated = estimate_redemption_lots(
            schedule,
            (FundRedemptionLot(lot["_acquired_at"], shares * current_nav),),
            today,
        )[0]
        total += evaluated["fee"]
    return total, warnings


def _normalise_lots(raw_lots: object, fund_code: str) -> list[dict]:
    if not isinstance(raw_lots, (list, tuple)) or not raw_lots:
        raise ValueError(f"{fund_code}: lots must be a non-empty list")
    lots = []
    for position, lot in enumerate(raw_lots):
        if not isinstance(lot, Mapping):
            raise ValueError(f"{fund_code}: lots[{position}] must be a mapping")
        acquired_at = _to_date(lot.get("acquired_at"), f"lots[{position}].acquired_at")
        shares = _to_decimal(lot.get("shares"), f"lots[{position}].shares")
        cost_amount = _to_decimal(lot.get("cost_amount"), f"lots[{position}].cost_amount")
        if shares <= 0:
            raise ValueError(f"{fund_code}: lots[{position}].shares must be positive")
        if cost_amount < 0:
            raise ValueError(f"{fund_code}: lots[{position}].cost_amount must be non-negative")
        lots.append(
            {
                "_acquired_at": acquired_at,
                "_shares": shares,
                "_cost": cost_amount,
                "_no_txn_detail": bool(lot.get("_no_txn_detail")),
                "redemption_tiers": lot.get("redemption_tiers"),
            }
        )
    return lots


def cost_positions(funds: object, *, as_of: date | None = None) -> dict:
    """Core computation; pure apart from the default ``as_of`` date."""
    if not isinstance(funds, (list, tuple)) or not funds:
        raise ValueError("funds must be a non-empty list")
    from backtest_shared import xirr

    today = as_of or date.today()
    results: list[dict] = []
    warnings: list[str] = []
    total_cost = Decimal("0")
    total_value = Decimal("0")
    total_fee = Decimal("0")
    fee_unknown = False
    for position, fund in enumerate(funds):
        if not isinstance(fund, Mapping):
            raise ValueError(f"funds[{position}] must be a mapping")
        fund_code = fund.get("fund_code")
        if not isinstance(fund_code, str) or not fund_code.strip():
            raise ValueError(f"funds[{position}].fund_code must be a non-empty string")
        fund_code = fund_code.strip()
        current_nav = _to_decimal(fund.get("current_nav"), f"{fund_code}.current_nav")
        if current_nav <= 0:
            raise ValueError(f"{fund_code}: current_nav must be positive")
        lots = _normalise_lots(fund.get("lots"), fund_code)

        shares = sum(lot["_shares"] for lot in lots)
        cost = sum(lot["_cost"] for lot in lots)
        value = shares * current_nav
        pnl = value - cost

        fee, fee_warnings = _redemption_fee_estimate(fund_code, lots, current_nav, today)
        warnings.extend(fee_warnings)
        fee_known = fee is not None
        if not fee_known:
            fee_unknown = True

        if any(lot.get("_no_txn_detail") for lot in lots):
            # Position-level fallback from average_cost: we know cost but not the
            # real purchase dates, so an XIRR would be fabricated — leave null.
            annual_return = None
            warnings.append(
                f"{fund_code}: 无逐笔交易明细，年化收益率无法计算（按平均成本估算成本）"
            )
        else:
            cashflows = [(lot["_acquired_at"], -lot["_cost"]) for lot in lots]
            cashflows.append((today, value))
            annual_return = xirr(sorted(cashflows, key=lambda item: item[0]))

        breakeven = (cost + (fee or Decimal("0"))) / shares

        total_cost += cost
        total_value += value
        if fee_known:
            total_fee += fee  # type: ignore[operator]

        results.append(
            {
                "fund_code": fund_code,
                "shares": str(shares),
                "cost_amount": str(cost),
                "current_nav": str(current_nav),
                "current_value": str(value),
                "floating_pnl": str(pnl),
                "floating_pnl_pct": round(float(pnl / cost), 6) if cost > 0 else None,
                "est_redemption_fee": str(fee) if fee_known else None,
                "annual_return_xirr": round(annual_return, 6)
                if annual_return is not None
                else None,
                "breakeven_nav": str(breakeven),
                "breakeven_note": ("含预估赎回费" if fee_known else "无费率数据，未含赎回费"),
                "as_of": today.isoformat(),
            }
        )
    results.sort(key=lambda item: item["fund_code"])
    portfolio_pnl = total_value - total_cost
    return {
        "funds": results,
        "summary": {
            "n_funds": len(results),
            "total_cost": str(total_cost),
            "total_value": str(total_value),
            "total_floating_pnl": str(portfolio_pnl),
            "total_floating_pnl_pct": (
                round(float(portfolio_pnl / total_cost), 6) if total_cost > 0 else None
            ),
            "total_est_redemption_fee": str(total_fee) if not fee_unknown else None,
        },
        "method": (
            "浮动盈亏 = 份额×当前净值 − 买入总成本；赎回费按 FundFeeSchedule 赎回档位"
            "（持有天数匹配）估算；年化收益率为 XIRR 简化版（每笔买入为流出、期末市值为流入，"
            "复用 backtest_shared.xirr 二分求解，忽略期间分红）；"
            "回本净值线 = (成本+预估赎回费)/份额"
        ),
        "as_of": today.isoformat(),
        "warnings": warnings,
    }


def _funds_from_db(database) -> list[dict]:
    """Rebuild FIFO lots from the authenticated, account-isolated snapshot."""
    with database.connection() as connection:
        position_rows = connection.execute(
            "SELECT symbol, quantity, average_cost, market_value, as_of_date FROM positions",
        ).fetchall()
        txn_rows = connection.execute(
            "SELECT symbol, action, quantity, price, fees, traded_at "
            "FROM transactions ORDER BY traded_at",
        ).fetchall()

    txns: dict[str, list[dict]] = {}
    for symbol, action, quantity, price, fees, traded_at in txn_rows:
        txns.setdefault(str(symbol), []).append(
            {
                "action": str(action),
                "quantity": Decimal(str(quantity)),
                "price": Decimal(str(price)),
                "fees": Decimal(str(fees or 0)),
                "traded_at": traded_at,
            }
        )

    funds: list[dict] = []
    for symbol, quantity, average_cost, market_value, _as_of in position_rows:
        symbol = str(symbol)
        quantity_d = Decimal(str(quantity))
        if quantity_d <= 0:
            continue
        current_nav = Decimal(str(market_value)) / quantity_d
        lots: list[dict] = []
        open_lots: list[dict] = []
        for txn in txns.get(symbol, []):
            traded = _to_date(str(txn["traded_at"]), "traded_at")
            qty = txn["quantity"]
            if txn["action"] in ("buy", "add"):
                open_lots.append(
                    {"acquired_at": traded, "shares": qty, "cost": txn["price"] * qty + txn["fees"]}
                )
            elif txn["action"] in ("reduce", "sell"):
                remaining = qty
                while remaining > 0 and open_lots:
                    head = open_lots[0]
                    take = min(head["shares"], remaining)
                    head["shares"] -= take
                    head["cost"] -= head["cost"] * take / (head["shares"] + take)
                    remaining -= take
                    if head["shares"] <= 0:
                        open_lots.pop(0)
        if txns.get(symbol):
            lots = [
                {
                    "acquired_at": lot["acquired_at"].isoformat(),
                    "shares": str(lot["shares"]),
                    "cost_amount": str(lot["cost"]),
                    "redemption_tiers": None,
                }
                for lot in open_lots
                if lot["shares"] > 0
            ]
        if not lots:
            # No transaction detail: position-level fallback, XIRR/fee unknown.
            avg_cost = Decimal(str(average_cost))
            lots = [
                {
                    "acquired_at": date.today().isoformat(),
                    "shares": str(quantity_d),
                    "cost_amount": str(avg_cost * quantity_d),
                    "redemption_tiers": None,
                    "_no_txn_detail": True,
                }
            ]
        funds.append({"fund_code": symbol, "current_nav": str(current_nav), "lots": lots})
    return funds


def execute_cost(kind: str, payload: dict) -> dict:
    if kind not in COST_CAPABILITIES:
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    funds = payload.get("funds")
    if funds is None:
        portfolio_id = payload.get("portfolio_id")
        if not isinstance(portfolio_id, str) or not portfolio_id.strip():
            raise ValueError("payload must carry funds or portfolio_id")
        if "snapshot" not in payload:
            raise ValueError("portfolio_id path requires payload['snapshot'] for DB access")
        from analysis_context import analytical_context

        with analytical_context(payload["snapshot"]) as (database, _queries, _directory):
            funds = _funds_from_db(database)
        if not funds:
            return {
                "funds": [],
                "summary": None,
                "method": None,
                "as_of": datetime.now().astimezone().isoformat(),
                "warnings": ["该组合无持仓数据"],
            }
    result = cost_positions(funds)
    return result
