"""Evidence-driven offline fund order replay using the real settlement model."""

from datetime import datetime, time
from decimal import Decimal

from quant_assistant.backtest.fund_execution import (
    FundExecutionModel,
    FundOrderRejected,
    FundSettlementRule,
    FundTradingStatus,
)
from quant_assistant.backtest.models import ExecutionState
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.funds.fees import FundFeeSchedule, PurchaseTier, RedemptionTier
from quant_assistant.models.entities import Action, AssetType, ProposedOrder
from quant_assistant.providers.fund_valuation import normalize_fund_symbol
from quant_assistant.reporting.fund_execution_schema import Bundle
from quant_assistant.services.fund_research_store import day, encode, fingerprint, positive

VERSION = "fund-execution-evidence-v1"
KINDS = {
    "calendar",
    "settlement_rules",
    "actual_fees",
    "nav_publication",
    "corporate_actions",
    "trading_status",
}


def timestamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return stamp.astimezone(SHANGHAI_TZ)


def fee(symbol, value):
    if value["channel"] != "alipay" or not value["evidence_reference"].strip():
        raise ValueError("actual fee evidence missing")
    if value.get("service_refund_rule"):
        raise ValueError("service refund needs a separate replay implementation")
    return FundFeeSchedule(
        symbol=symbol,
        share_class=value["share_class"],
        purchase_rate=Decimal(value["purchase_rate"]),
        annual_service_rate=Decimal(value["annual_service_rate"]),
        redemption_tiers=tuple(
            RedemptionTier(t["min_days"], t["max_days"], Decimal(t["rate"]))
            for t in value["redemption_tiers"]
        ),
        verified_on=day(value["verified_on"]),
        effective_from=day(value["effective_from"]),
        effective_to=day(value["effective_to"]) if value.get("effective_to") else None,
        purchase_tiers=tuple(
            PurchaseTier(
                Decimal(t["min_amount"]),
                Decimal(t["max_amount"]) if t["max_amount"] is not None else None,
                Decimal(t["rate"]) if t.get("rate") is not None else None,
                Decimal(t["fixed_amount"]) if t.get("fixed_amount") is not None else None,
            )
            for t in value.get("purchase_tiers", [])
        ),
        channel="alipay",
        evidence_reference=value["evidence_reference"],
    )


def replay(bundle, *, now=None):
    result = {
        "version": VERSION,
        "research_only": True,
        "qualifies_strategy": False,
        "qualifies_paper_days": False,
        "ready_for_trading": False,
        "status": "blocked",
        "blockers": [],
        "curve": [],
        "fills": [],
        "rejections": [],
        "net_return": None,
        "input_sha256": fingerprint(encode(bundle)),
    }
    try:
        prepared = prepare(bundle, now or datetime.now(SHANGHAI_TZ))
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        result["blockers"] = ["完整回放证据缺失或无效：" + str(error)]
        return result
    symbol, calendar, navs, model, orders, capital = prepared
    state = ExecutionState(cash=capital)
    pending = []
    peak, max_drawdown = capital, Decimal(0)

    def settle_known(cutoff):
        nonlocal state, pending
        trading_day = cutoff.date()
        outstanding = []
        for item in pending:
            point = navs[item.nav_date]
            available = timestamp(point["published_at"]) <= cutoff
            if trading_day < item.confirm_on or not available:
                outstanding.append(item)
                continue
            outcome = model.settle(item, positive(point["unit_nav"]), trading_day, state)
            state = outcome.state
            if outcome.fill:
                fill = outcome.fill
                result["fills"].append(
                    {
                        "application_date": str(fill.trade_date),
                        "confirmed_on": str(trading_day),
                        "processed_at": cutoff.isoformat(),
                        "action": fill.order.action.value,
                        "units": str(fill.quantity),
                        "nav": str(fill.nav),
                        "fees": str(fill.fees),
                        "gross_amount": str(fill.gross_amount),
                    }
                )
            else:
                result["rejections"].append(
                    {"date": str(trading_day), "code": outcome.rejection.code}
                )
        pending = outstanding

    for trading_day in calendar:
        state = model.release_cash(state, trading_day)
        for order, submitted in orders:
            if submitted.date() != trading_day:
                continue
            settle_known(submitted)
            try:
                application = model.submit(order, trading_day, state)
                state = model.reserve(application, state)
                pending.append(application)
            except FundOrderRejected as error:
                result["rejections"].append({"date": str(trading_day), "code": error.code})
        settle_known(datetime.combine(trading_day, datetime.max.time(), SHANGHAI_TZ))
        state = model.release_cash(state, trading_day)
        nav = positive(navs[trading_day]["unit_nav"])
        assets = (
            state.cash + state.unavailable_cash + state.fund_units.get(symbol, Decimal(0)) * nav
        )
        peak = max(peak, assets)
        max_drawdown = max(max_drawdown, 1 - assets / peak)
        result["curve"].append(
            {
                "date": str(trading_day),
                "cash": str(state.cash),
                "unavailable_cash": str(state.unavailable_cash),
                "units": str(state.fund_units.get(symbol, Decimal(0))),
                "assets": str(assets),
            }
        )
    result.update(
        status="completed",
        max_drawdown=float(max_drawdown),
        pending_orders=len(pending),
        unavailable_cash=str(state.unavailable_cash),
        units=str(state.fund_units.get(symbol, Decimal(0))),
    )
    if pending or state.unavailable_cash or state.fund_units.get(symbol, Decimal(0)):
        result["blockers"].append("期末仍有持仓或在途资金，未计算完全结清后的扣费收益")
    elif result["rejections"]:
        result["blockers"].append("存在拒单，不能视为原策略完整成交后的收益")
    else:
        result["net_return"] = float(state.cash / capital - 1)
    result["method"] = (
        "按申请日未知净值、公布时间、确认日和到账日逐笔回放；不是实盘或策略准入证据。"
    )
    return result


def prepare(bundle, now):
    bundle = Bundle.model_validate(bundle).model_dump(mode="json")
    if bundle["version"] != VERSION:
        raise ValueError("unsupported bundle version")
    symbol = normalize_fund_symbol(bundle["symbol"])
    evidence = bundle["evidence"]
    if {e["kind"] for e in evidence} != KINDS or len(evidence) != len(KINDS):
        raise ValueError("calendar/rules/fees/publication/actions/status evidence required")
    if any(e["confirmed"] is not True or not e["reference"].strip() for e in evidence):
        raise ValueError("evidence must be explicitly reviewed")
    if bundle["corporate_actions_review"] != "no_actions":
        raise ValueError("distribution/split periods require adjusted replay")
    calendar = tuple(day(d) for d in bundle["calendar"])
    if not 2 <= len(calendar) <= 10000 or list(calendar) != sorted(set(calendar)):
        raise ValueError("calendar must be complete, unique and ordered")
    if calendar[-1] > now.date():
        raise ValueError("replay cannot include future dates")
    capital = positive(bundle["capital"])
    if capital > Decimal("100000000"):
        raise ValueError("capital exceeds replay bound")
    navs = {day(n["nav_date"]): n for n in bundle["navs"]}
    if len(navs) != len(bundle["navs"]) or not set(calendar).issubset(navs):
        raise ValueError("missing or duplicate NAV dates")
    for nav_day, value in navs.items():
        positive(value["unit_nav"])
        positive(value["accumulated_nav"])
        if timestamp(value["published_at"]).date() < nav_day:
            raise ValueError("NAV publication predates NAV date")
        if timestamp(value["published_at"]) > now:
            raise ValueError("NAV has not yet been published")
    gaps = [positive(n["accumulated_nav"]) - positive(n["unit_nav"]) for n in navs.values()]
    if max(gaps) - min(gaps) > Decimal("0.000001"):
        raise ValueError("NAV shows unreviewed corporate actions")
    settlement = bundle["settlement"]
    cutoff = time.fromisoformat(settlement["application_cutoff"])
    if cutoff.tzinfo is not None:
        raise ValueError("cutoff must use local Shanghai clock")
    rule = FundSettlementRule(
        settlement["subscription_confirm_days"],
        settlement["redemption_cash_days"],
        settlement["redemption_confirm_days"],
        trading_dates=calendar,
    )
    fees = tuple(fee(symbol, value) for value in bundle["fees"])
    if not fees or len({f.share_class for f in fees}) != 1:
        raise ValueError("consistent actual share-class fees required")
    statuses = []
    status_days = set()
    for value in bundle["statuses"]:
        status_day = day(value["status_date"])
        if status_day in status_days:
            raise ValueError("duplicate trading status")
        status_days.add(status_day)
        if value["purchase_limit"] is not None or value["redemption_limit"] is not None:
            raise ValueError("amount-limited periods require a limit-aware execution model")
        if timestamp(value["known_at"]).date() > status_day:
            raise ValueError("trading status was not known in time")
        latest = day(value["latest_nav_date"])
        if latest not in navs or timestamp(navs[latest]["published_at"]) > timestamp(
            value["known_at"]
        ):
            raise ValueError("latest NAV was not known at status observation")
        statuses.append(
            FundTradingStatus(
                symbol,
                status_day,
                day(value["latest_nav_date"]),
                value["purchase_open"],
                value["redemption_open"],
            )
        )
    if not set(calendar).issubset(status_days):
        raise ValueError("historical trading status coverage incomplete")
    orders = []
    for value in bundle["orders"]:
        submitted = timestamp(value["submitted_at"])
        signal_day = day(value["signal_nav_date"])
        if (
            submitted.date() not in calendar
            or signal_day >= submitted.date()
            or submitted.time() >= cutoff
        ):
            raise ValueError("order date/cutoff invalid")
        if signal_day not in navs or timestamp(navs[signal_day]["published_at"]) > submitted:
            raise ValueError("future NAV used as signal")
        if any(timestamp(e["known_at"]) > submitted for e in evidence):
            raise ValueError("evidence was not known at order time")
        if any(
            timestamp(s["known_at"]) > submitted
            for s in bundle["statuses"]
            if day(s["status_date"]) == submitted.date()
        ):
            raise ValueError("trading status published after decision")
        action = Action(value["action"])
        if action not in (Action.BUY, Action.ADD, Action.SELL, Action.REDUCE):
            raise ValueError("unsupported fund action")
        order = ProposedOrder(
            symbol,
            AssetType.FUND,
            action,
            Decimal(value["amount"]),
            Decimal(value["quantity"]) if value["quantity"] is not None else None,
            Decimal(0),
            ("frozen offline order",),
            signal_day,
            submitted,
            1,
            VERSION,
        )
        orders.append((order, submitted))
    if not 1 <= len(orders) <= 1000 or orders != sorted(orders, key=lambda item: item[1]):
        raise ValueError("orders must be nonempty and ordered")
    model = FundExecutionModel(rule, {symbol: fees}, {symbol: tuple(statuses)})
    return symbol, calendar, navs, model, orders, capital
