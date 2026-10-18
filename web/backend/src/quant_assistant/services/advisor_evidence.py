"""Freeze bounded local/public facts before calling any model."""

import re
import time
from concurrent.futures import Future
from dataclasses import asdict
from datetime import datetime, timedelta
from threading import Thread

from quant_assistant.services.fund_fee_evidence import FundFeeEvidenceService
from quant_assistant.services.fund_lots import FundLotService
from quant_assistant.services.strategy_settings import StrategyParameters


def _public_request(call):
    result = Future()

    def run():
        try:
            result.set_result(call())
        except Exception as error:
            result.set_exception(error)

    Thread(target=run, name="advisor-public-source", daemon=True).start()
    return result


class AdvisorEvidence:
    def __init__(self, database, queries, strategies, preferences):
        self.database, self.queries = database, queries
        self.strategies, self.preferences = strategies, preferences

    def build(self, kind, now):
        deadline = time.monotonic() + 80
        sources = []
        blockers = []
        symbol_gates = {}
        symbols = []
        held_symbols = []
        news_request = _public_request(lambda: self.queries.market_news(20))
        quote_request = _public_request(
            lambda: self.queries.market_quotes(index_symbols=("000001", "000300"))
        )
        try:
            feed = news_request.result(timeout=40)
            recent = [n for n in feed.items if now - timedelta(days=3) <= n.published_at <= now]
            for item in recent[:12]:
                sources.append(
                    {
                        "id": "news:" + item.id,
                        "title": item.title[:400],
                        "url": item.url,
                        "published_at": item.published_at.isoformat(),
                        "summary": item.summary[:600],
                        "source": item.source,
                    }
                )
            if feed.stale or feed.unavailable or not recent:
                blockers.append("资讯缺失、过期或当前公开来源不可用")
        except Exception:
            blockers.append("资讯来源读取失败")
        live = False
        try:
            market = quote_request.result(timeout=20)
            sources.append(
                {
                    "id": "market",
                    "title": "主要指数行情",
                    "data": [asdict(q) for q in market.items],
                    "stale": market.stale,
                }
            )
            live = (
                bool(market.items)
                and not market.stale
                and not market.unavailable
                and all(q.trade_time_verified for q in market.items)
                and all(
                    timedelta(0) <= now - q.observed_at <= timedelta(minutes=15)
                    for q in market.items
                )
            )
            if not live:
                blockers.append("指数行情非当前交易时段数据，休市或数据过期时不生成买卖建议")
        except Exception:
            blockers.append("指数行情读取失败")
        if kind == "morning":
            preview = self.strategies.preview(
                StrategyParameters.model_validate(self.strategies.load()["parameters"])
            )
            sources.append({"id": "screening", "title": "本地量化股票筛选", "data": preview})
            symbols = [item["symbol"] for item in preview.get("items", [])[:8]]
            if not preview.get("research_eligible"):
                blockers.append("股票历史行情与来源证据尚未通过AI复核准入")
            if not symbols:
                blockers.append("没有通过本地筛选的股票候选")
            symbol_gates = {symbol: list(blockers) for symbol in symbols}
        else:
            positions = self.database.query_df(
                """SELECT symbol, quantity, average_cost, market_value, as_of_date FROM positions
                   WHERE asset_type='fund' AND quantity>0"""
            )
            quantities = {str(row["symbol"]): row for row in positions.to_dict("records")}
            screenshots = self.database.query_df(
                """SELECT p.symbol,p.quantity,p.average_cost,p.market_value,p.as_of_date
                   FROM account_positions p JOIN portfolio_accounts a USING(account_id)
                   WHERE p.account_id='fund-photo' AND a.platform='photo_import'
                   AND a.status='active' AND p.asset_type='fund' AND p.quantity>0"""
            )
            photo_rows = {str(row["symbol"]): row for row in screenshots.to_dict("records")}
            watchlist = self.preferences.load().watchlist
            requested = {
                *quantities,
                *photo_rows,
                *(w.symbol for w in watchlist if w.asset_type == "fund"),
            }
            if any(not re.fullmatch(r"\d{6}", symbol) for symbol in requested):
                blockers.append("基金代码或份额类别需核对，非规范六位代码未参与模型决策")
            # Prioritize owned records; a watchlist must not crowd out personal observations.
            ordered = sorted(quantities) + sorted(set(photo_rows) - set(quantities))
            ordered += sorted(requested - set(ordered))
            valid = [symbol for symbol in ordered if re.fullmatch(r"\d{6}", symbol)]
            symbols = valid[:6]
            if len(valid) > 6:
                blockers.append("基金复核单次最多6只，部分基金本轮未覆盖")
            held_symbols = [symbol for symbol in symbols if symbol in quantities]
            sources.append(
                {
                    "id": "portfolio",
                    "title": "本地基金持仓快照（仍需对账确认）",
                    "data": [quantities[symbol] for symbol in held_symbols],
                }
            )
            if photo_rows:
                sources.append(
                    {
                        "id": "photo_holdings",
                        "title": "用户确认的截图基金（非完整账户基线）",
                        "data": [
                            photo_rows[s]
                            for s in symbols
                            if s in photo_rows and s not in quantities
                        ],
                        "complete_account": False,
                    }
                )
            if not symbols:
                blockers.append("尚无确认的基金持仓或基金研究池，不能给出个人买卖建议")
            baseline = self.database.query_df(
                "SELECT run_id FROM portfolio_valuations WHERE is_baseline=true LIMIT 1"
            )
            for symbol in symbols:
                gates = list(blockers)
                if baseline.empty:
                    gates.append("账户净值基线尚未确认")
                if symbol in photo_rows and symbol not in quantities:
                    gates.append("截图持仓仅供观察，现金及完整账户、持有批次尚未核对")
                    day = photo_rows[symbol]["as_of_date"]
                    day = day.date() if isinstance(day, datetime) else day
                    if not 0 <= (now.date() - day).days <= 7:
                        gates.append("截图持仓过期，需要更新截图")
                if symbol in quantities:
                    as_of = quantities[symbol]["as_of_date"]
                    as_of = as_of.date() if isinstance(as_of, datetime) else as_of
                    if not 0 <= (now.date() - as_of).days <= 7:
                        gates.append("持仓快照过期，需要对账")
                try:
                    remaining = max(0, deadline - time.monotonic())
                    if remaining == 0:
                        raise TimeoutError
                    detail = _public_request(lambda s=symbol: self.queries.fund_detail(s)).result(
                        timeout=min(20, remaining)
                    )
                    public = asdict(detail)
                    public["nav_history"] = public["nav_history"][-60:]
                    public["announcements"] = public["announcements"][:5]
                    public["managers"] = [
                        {"name": m.name, "tenure_start": m.tenure_start} for m in detail.managers
                    ]
                    sources.append({"id": "fund:" + symbol, "title": detail.name, "data": public})
                    if (
                        detail.stale
                        or not detail.nav_history
                        or not 0 <= (now.date() - detail.nav_history[-1].nav_date).days <= 7
                    ):
                        gates.append("基金净值或规则数据缺失/过期")
                    if (
                        not detail.trading_rules
                        or not detail.trading_rules.fields
                        or not detail.purchase_status
                        or not detail.redemption_status
                    ):
                        gates.append("申赎规则未核验")
                except Exception:
                    gates.append("基金公开资料读取失败")
                fees = FundFeeEvidenceService(self.database, now=lambda: now).list(symbol)
                current = next((f for f in fees["items"] if f["usable_today"]), None)
                sources.append(
                    {"id": "fees:" + symbol, "title": "支付宝实际费用证据", "data": current}
                )
                if current is None:
                    gates.append("尚无有效的支付宝实际费用证据")
                if symbol in quantities:
                    lots = FundLotService(self.database, now=lambda: now).list(symbol)
                    # Freeze only decision facts; private evidence references stay in the ledger.
                    sources.append(
                        {
                            "id": "lots:" + symbol,
                            "title": "已核对基金批次",
                            "data": {
                                key: lots[key]
                                for key in ("status", "verified_on", "items", "blockers")
                            },
                        }
                    )
                    if lots["status"] != "reconciled":
                        gates.append("基金剩余批次未核对或已失效")
                    # Reconciled lots do not establish which ones a partial sale will redeem.
                    gates.append("部分赎回需明确所选批次和确认日期")
                # Stock backtests and execution fixtures cannot qualify an OTC fund strategy.
                gates.append("场外基金完整策略回放尚未验收")
                symbol_gates[symbol] = gates
        if not sources:
            sources.append(
                {
                    "id": "availability",
                    "title": "数据可用性",
                    "data": "当前未取得可核验的行情或资讯",
                }
            )
        return {
            "kind": kind,
            "observed_at": now.isoformat(),
            "symbols": symbols,
            "held_symbols": held_symbols,
            "sources": sources,
            "blockers": blockers,
            "symbol_gates": symbol_gates,
            "market_live": live,
            "research_only": True,
        }
