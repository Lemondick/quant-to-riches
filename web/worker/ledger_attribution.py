"""Audited ledger attribution with explicitly disclosed closing-value cash-flow convention.

Four phases:
1. ``collect_events`` -- gather and audit-check ledger events, then sort them.
2. ``rebuild_positions`` -- replay events up to the period start.
3. ``compute_period`` -- value one period, apply its events, reconcile.
4. ``assemble_period`` -- build the portfolio/benchmark output rows.
"""

import json
from collections import defaultdict
from decimal import Decimal

from attribution_shared import EvidenceMissing, _amount, _day, _text


def _initial_state(original, start, end):
    """Shared prologue: validate the range and build the baseline book."""
    if start >= end:
        raise EvidenceMissing("账本分期需要开始日期早于结束日期。")
    positions = {row["symbol"]: _amount(row["quantity"]) for row in original}
    assets = {row["symbol"]: row["asset_type"] for row in original}
    cash = next(row["symbol"] for row in original if row["asset_type"] == "cash")
    if any(asset not in {"stock", "fund", "cash"} for asset in assets.values()):
        raise EvidenceMissing("账本归因需要股票、基金与单一现金持仓。")
    return positions, assets, cash


def collect_events(tables, baseline, end):
    """Phase 1 事件收集: gather, audit-check and sort ledger events."""
    events = []
    for table, kind, field, category in (
        ("transactions", "trade", "traded_at", "transaction"),
        ("dividend_events", "dividend", "paid_at", "dividend"),
        ("portfolio_events", "flow", "effective_date", "external_cash_flow"),
        ("corporate_action_events", "action", "effective_at", "corporate_action"),
        ("ledger_corrections", "correction", "effective_at", "ledger_correction"),
    ):
        for row in tables.get(table, []):
            day = _day(row[field])
            if kind == "flow" and (row["event_type"] == "baseline" or row["status"] == "void"):
                continue
            if not _day(baseline["effective_date"]) <= day <= end:
                continue
            if day == _day(baseline["effective_date"]):
                raise EvidenceMissing("基线日还有账本事件，缺少初始快照与事件的日内边界。")
            identity = row["event_id"] if kind == "flow" else str(row["id"])
            audits = [
                audit
                for audit in tables["audit_log"]
                if audit["entity_type"] == category and str(audit["entity_id"]) == identity
            ]
            if len(audits) != 1:
                raise EvidenceMissing("账本事件缺少唯一原始审计顺序，不能自动归因。")
            if kind in {"trade", "dividend", "action"} and row.get("asset_type") not in {
                "stock",
                "fund",
            }:
                raise EvidenceMissing("账本事件包含其他资产，需先核对该资产的归因模型。")
            if kind == "flow" and (
                row["status"] != "confirmed" or row["event_type"] not in {"deposit", "withdrawal"}
            ):
                raise EvidenceMissing("资金事件尚未确认或包含校准，不能自动归因。")
            events.append((day, str(row[field]), int(audits[0]["id"]), kind, row))
    if len(events) > 20000:
        raise EvidenceMissing("单次账本归因最多2万条事件，请缩小区间。")
    events.sort(key=lambda event: event[:3])
    return events


def _correction_targets(tables):
    """Index reversible targets for correction events."""
    targets = {}
    for table, category in (
        ("transactions", "transaction"),
        ("dividend_events", "dividend"),
    ):
        for row in tables[table]:
            key = (category, str(row["id"]))
            if key in targets:
                raise EvidenceMissing("冲正目标编号重复。")
            targets[key] = row
    return targets


class _PositionRebuilder:
    """Replayable position book shared by phases 2 and 3.

    The old 162-line ``apply`` closure captured this state implicitly; it is
    now an explicit method so the phases stay decoupled. The body is unchanged.
    """

    def __init__(self, tables, positions, assets, cash, targets):
        self.tables = tables
        self.positions = positions
        self.assets = assets
        self.cash = cash
        self.targets = targets
        self.applied = set()

    def apply(self, kind, row, initial=None, gains=None, close=None):
        if row.get("cash_symbol") not in {None, self.cash}:
            raise EvidenceMissing("账本现金标识与原始账户不一致。")
        if kind == "flow":
            details = row.get("details_json", {})
            if isinstance(details, str):
                details = json.loads(details)
            if details.get("cash_symbol") not in {None, self.cash}:
                raise EvidenceMissing("资金事件现金标识与原始账户不一致。")
            amount = _amount(row["amount"])
            if (row["event_type"] == "deposit" and amount <= 0) or (
                row["event_type"] == "withdrawal" and amount >= 0
            ):
                raise EvidenceMissing("资金事件方向与金额不一致。")
            self.positions[self.cash] += amount
            effect = Decimal(0)
        elif kind in {"trade", "correction"}:
            sign = 1
            if kind == "correction":
                key = (row["target_type"], str(row["target_id"]))
                target = self.targets.get(key)
                field = "traded_at" if key[0] == "transaction" else "paid_at"
                if (
                    target is None
                    or key[0] not in {"transaction", "dividend"}
                    or key in self.applied
                    or str(target.get("reversed_by_correction_id")) != str(row["id"])
                ):
                    raise EvidenceMissing("冲正目标缺失、重复或与原始事件关联不一致。")
                if (
                    str(target[field]) > str(row["effective_at"])
                    or len(
                        [
                            a
                            for a in self.tables["audit_log"]
                            if a["entity_type"] == key[0] and str(a["entity_id"]) == key[1]
                        ]
                    )
                    != 1
                ):
                    raise EvidenceMissing("冲正早于原始事件或缺少原始审计。")
                row, sign = target, -1
                self.applied.add(key)
                if row.get("cash_symbol") not in {None, self.cash} or row.get("asset_type") not in {
                    "stock",
                    "fund",
                }:
                    raise EvidenceMissing("冲正资产或现金标识不一致。")
                if key[0] == "dividend":
                    net = _amount(row["net_amount"])
                    gross, fees = _amount(row["gross_amount"]), _amount(row["fees"])
                    if (
                        gross <= 0
                        or not 0 <= fees <= gross
                        or abs(net - gross + fees) > Decimal("0.00000001")
                    ):
                        raise EvidenceMissing("冲正分红金额不一致。")
                    self.positions[self.cash] -= net
                    effect = -net
                    if self.positions[self.cash] < 0:
                        raise EvidenceMissing("冲正产生负现金。")
                    if gains is not None:
                        carrier = row["symbol"] if initial.get(row["symbol"], 0) else self.cash
                        if not initial.get(carrier, 0):
                            raise EvidenceMissing("冲正收益缺少期初资金基数。")
                        gains[carrier] += effect
                    return
            symbol, action = row["symbol"], row["action"]
            quantity, price, fees = (_amount(row[key]) for key in ("quantity", "price", "fees"))
            if (
                quantity <= 0
                or price <= 0
                or fees < 0
                or action not in {"buy", "add", "sell", "reduce"}
            ):
                raise EvidenceMissing("成交方向、数量、价格或费用无效。")
            self.assets[symbol] = row["asset_type"]
            buying = action in {"buy", "add"}
            self.positions[symbol] = self.positions.get(symbol, Decimal(0)) + (
                sign * (quantity if buying else -quantity)
            )
            expected_cash = (-quantity * price if buying else quantity * price) - fees
            delta = _amount(row.get("cash_delta", expected_cash))
            if abs(delta - expected_cash) > Decimal("0.00000001"):
                raise EvidenceMissing("成交金额与账本现金变化不一致。")
            self.positions[self.cash] += sign * delta
            effect = (
                Decimal(0)
                if close is None
                else sign * (delta + quantity * close(symbol) * (1 if buying else -1))
            )
        elif kind == "action":
            symbol, action = row["symbol"], row["action_type"]
            quantity = _amount(row.get("quantity") or 0)
            target_quantity = _amount(row.get("target_quantity") or 0)
            fees, delta = _amount(row["fees"]), _amount(row["cash_delta"])
            if (
                quantity <= 0
                or fees < 0
                or self.positions.get(symbol, 0) <= 0
                or (action != "dividend_reinvestment" and self.positions[symbol] < quantity)
                or self.assets.get(symbol) != row["asset_type"]
            ):
                raise EvidenceMissing("公司行动数量、费用或资产类型与重建持仓不一致。")
            if action == "split":
                ratio = _amount(row.get("ratio") or 0)
                if ratio <= 0 or target_quantity != quantity * ratio or delta != 0:
                    raise EvidenceMissing("拆分份额、比例或现金变化不一致。")
                self.positions[symbol] += target_quantity - quantity
                effect = (
                    Decimal(0) if close is None else (target_quantity - quantity) * close(symbol)
                )
            elif action == "dividend_reinvestment":
                price, gross = _amount(row.get("price") or 0), _amount(row["gross_amount"])
                if price <= 0 or gross <= 0 or abs(delta + gross + fees) > Decimal("0.00000001"):
                    raise EvidenceMissing("再投资价格、金额或现金变化不一致。")
                self.positions[symbol] += quantity
                self.positions[self.cash] += delta
                effect = Decimal(0) if close is None else delta + quantity * close(symbol)
            elif action == "fund_conversion":
                target = row.get("target_symbol")
                if (
                    row["asset_type"] != "fund"
                    or not target
                    or target == symbol
                    or target_quantity <= 0
                    or delta != -fees
                ):
                    raise EvidenceMissing("基金转换目标份额或费用不一致。")
                self.assets[target] = "fund"
                self.positions[symbol] -= quantity
                self.positions[target] = self.positions.get(target, Decimal(0)) + target_quantity
                self.positions[self.cash] += delta
                effect = (
                    Decimal(0)
                    if close is None
                    else delta + target_quantity * close(target) - quantity * close(symbol)
                )
            elif action == "custody_transfer":
                if delta != 0 or not row.get("from_account") or not row.get("to_account"):
                    raise EvidenceMissing("转托管账户或现金变化不一致。")
                effect = Decimal(0)
            else:
                raise EvidenceMissing("未知公司行动类型。")
        else:
            symbol = row["symbol"]
            gross, fees = _amount(row["gross_amount"]), _amount(row["fees"])
            if self.positions.get(symbol, 0) <= 0 or gross <= 0 or not 0 <= fees <= gross:
                raise EvidenceMissing("现金分红缺少对应持仓或金额不一致。")
            effect = _amount(row.get("net_amount", gross - fees))
            if abs(effect - gross + fees) > Decimal("0.00000001"):
                raise EvidenceMissing("现金分红净额与总额及费用不一致。")
            self.positions[self.cash] += effect
        if any(value < 0 or value >= Decimal("1e20") for value in self.positions.values()):
            raise EvidenceMissing("账本重建产生负持仓、负现金或超范围金额，请先核对事件。")
        if gains is not None and effect:
            carrier = row.get("symbol")
            if initial.get(carrier, 0) == 0:
                carrier = self.cash
            if initial.get(carrier, 0) == 0:
                raise EvidenceMissing("事件收益缺少期初持仓或现金资金基数，需要更细的日内估值。")
            gains[carrier] += effect


def rebuild_positions(rebuilder, events, start):
    """Phase 2 持仓重建: replay events on/before the period start."""
    for day, _, _, kind, row in events:
        if day <= start:
            rebuilder.apply(kind, row)


def _make_close(marks, assets, day):
    """Per-period close-price accessor with a per-period cache."""
    prices = {}

    def close(symbol):
        if symbol not in prices:
            prices[symbol] = (
                marks.price(symbol, day, asset="fund")
                if assets[symbol] == "fund"
                else marks.price(symbol, day)
            )[0]
        return prices[symbol]

    return close


def _prefetch_period_prices(marks, original, events, dates):
    """Warm price evidence for every (symbol, day) the periods may request.

    One batched lookup per asset table (see Marks.prefetch_prices); warming
    is best-effort and never changes price() validation semantics.
    """
    symbols = {row["symbol"] for row in original}
    for _event_day, _timestamp, _audit_id, _kind, row in events:
        if row.get("symbol"):
            symbols.add(row["symbol"])
        if row.get("target_symbol"):
            symbols.add(row["target_symbol"])
    marks.prefetch_prices(
        (symbol, day, asset) for symbol in symbols for day in dates for asset in ("stock", "fund")
    )


def compute_period(rebuilder, events, marks, left, right):
    """Phase 3 收益计算: value one period, apply its events, reconcile."""
    positions, assets = rebuilder.positions, rebuilder.assets
    initial, gains, sectors = {}, defaultdict(Decimal), {}
    close = _make_close(marks, assets, right)
    for symbol, quantity in positions.items():
        if quantity == 0:
            continue
        if assets[symbol] == "cash":
            initial[symbol], sectors[symbol] = quantity, "现金"
        else:
            initial[symbol] = (
                quantity
                * (
                    marks.price(symbol, left, asset="fund")
                    if assets[symbol] == "fund"
                    else marks.price(symbol, left)
                )[0]
            )
            sectors[symbol] = (
                "基金" if assets[symbol] == "fund" else marks.classification(symbol, left)[0]
            )
            gains[symbol] = quantity * close(symbol) - initial[symbol]
    total = sum(initial.values(), Decimal(0))
    if total <= 0:
        raise EvidenceMissing("分期期初资产必须大于零。")
    flow, audit_ids = Decimal(0), []
    for day, _, audit_id, kind, row in events:
        if left < day <= right:
            rebuilder.apply(kind, row, initial, gains, close)
            if kind == "flow":
                flow += _amount(row["amount"])
            audit_ids.append(audit_id)
    ending = sum(
        (
            quantity if assets[symbol] == "cash" else quantity * close(symbol)
            for symbol, quantity in positions.items()
            if quantity
        ),
        Decimal(0),
    )
    profit = sum(gains.values(), Decimal(0))
    if abs(ending - flow - total - profit) > Decimal("0.00000001"):
        raise EvidenceMissing("分期收益与持仓、成交及现金流不能对账。")
    return {
        "initial": initial,
        "gains": gains,
        "sectors": sectors,
        "total": total,
        "flow": flow,
        "ending": ending,
        "profit": profit,
        "audit_ids": audit_ids,
    }


def assemble_period(computed, rebuilder, components, marks, left, right):
    """Phase 4 输出组装: build portfolio/benchmark rows for one period."""
    assets = rebuilder.assets
    initial, gains, sectors = computed["initial"], computed["gains"], computed["sectors"]
    total, flow, ending, profit = (computed[key] for key in ("total", "flow", "ending", "profit"))
    audit_ids = computed["audit_ids"]
    portfolio = []
    for symbol, amount in initial.items():
        returns = gains[symbol] / amount
        if not -1 <= returns <= Decimal("1e6"):
            raise EvidenceMissing("分期行业资金贡献超出支持范围，需要更细估值。")
        holding = {
            "symbol": symbol,
            "sector_name": sectors[symbol],
            "initial_value": _text(amount),
            "period_return": _text(returns),
        }
        if assets[symbol] == "fund":
            portfolio.extend(marks.fund_breakdown(holding, left))
        else:
            portfolio.append(holding)
    if len(portfolio) > 121:
        raise EvidenceMissing("归因行业拆分超过121项，请缩小组合范围。")
    benchmark = [
        marks.holding(
            symbol,
            "cash" if symbol in {"CASH", "CASH.CNY"} else "stock",
            weight,
            left,
            right,
            benchmark=True,
        )
        for symbol, weight in components.items()
    ]
    return {
        "start_date": str(left),
        "end_date": str(right),
        "portfolio": portfolio,
        "benchmark": benchmark,
        "initial_value": _text(total),
        "ending_value": _text(ending),
        "net_external_flow": _text(flow),
        "profit": _text(profit),
        "portfolio_return": _text(profit / total),
        "audit_ids": audit_ids,
    }


def calculate_periods(tables, baseline, original, components, start, end, marks):
    """Run the four attribution phases; signature and results unchanged."""
    positions, assets, cash = _initial_state(original, start, end)
    events = collect_events(tables, baseline, end)  # Phase 1 事件收集
    rebuilder = _PositionRebuilder(tables, positions, assets, cash, _correction_targets(tables))
    rebuild_positions(rebuilder, events, start)  # Phase 2 持仓重建
    dates = sorted({start, end, *(event[0] for event in events if event[0] > start)})
    if len(dates) > 367:
        raise EvidenceMissing("单次账本归因最多366个事件日期区间，请缩小区间。")
    _prefetch_period_prices(marks, original, events, dates)
    periods = []
    for left, right in zip(dates, dates[1:], strict=False):
        computed = compute_period(rebuilder, events, marks, left, right)  # Phase 3 收益计算
        periods.append(
            assemble_period(computed, rebuilder, components, marks, left, right)
        )  # Phase 4 输出组装
    return periods
