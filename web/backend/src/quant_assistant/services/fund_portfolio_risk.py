"""Concentration and disclosed overlap of one fund snapshot, without account merging."""

from datetime import datetime
from decimal import Decimal
from itertools import combinations

from quant_assistant.services.fund_research_store import day, positive


class FundPortfolioRisk:
    def __init__(self, database, store, now):
        self.database, self.store, self.now = database, store, now

    def positions(self):
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT symbol,market_value,as_of_date FROM positions "
                "WHERE asset_type='fund' AND quantity>0 ORDER BY symbol"
            ).fetchall()
            if rows:
                return rows, "main"
            rows = connection.execute(
                "SELECT p.symbol,p.market_value,p.as_of_date FROM account_positions p "
                "JOIN portfolio_accounts a USING(account_id) WHERE p.account_id='fund-photo' "
                "AND a.platform='photo_import' AND a.status='active' "
                "AND p.asset_type='fund' AND p.quantity>0 ORDER BY p.symbol"
            ).fetchall()
        return rows, "photo" if rows else "none"

    def read(self):
        rows, scope = self.positions()
        result = {
            "scope": scope,
            "items": [],
            "pairs": [],
            "concentration": None,
            "research_only": True,
            "ready_for_trading": False,
            "denominator": "已知基金市值合计，不含未知现金、其他资产或未上传的基金",
            "messages": [],
        }
        if not rows:
            result["messages"] = ["暂无基金持仓快照"]
            return result
        if scope == "photo":
            result["messages"].append("截图观察账户，不代表完整账户")
        total = sum((row[1] for row in rows), Decimal(0))
        usable = total > 0 and all(row[1] > 0 for row in rows)
        dates = {row[2] for row in rows}
        stale = any(not 0 <= (self.now().date() - d).days <= 7 for d in dates)
        if len(dates) != 1 or stale:
            usable = False
            result["messages"].append("快照日期不一致或已过期，集中度暂不可核对")
        holdings = {}
        for symbol, value, as_of in rows:
            item = {
                "symbol": symbol,
                "name": symbol,
                "market_value": str(value),
                "as_of_date": str(as_of),
                "weight": float(value / total) if usable else None,
                "disclosed_coverage": None,
                "holdings_report_date": None,
                "latest_nav_date": None,
                "public_observed_at": None,
                "disclosure_message": "未取得有效持仓披露",
            }
            try:
                snapshots = self.store.snapshots(symbol)
                if snapshots:
                    public = snapshots[0]
                    item["name"] = public["name"] or symbol
                    item["public_observed_at"] = datetime.fromisoformat(
                        public["observed_at"]
                    ).isoformat()
                    item["latest_nav_date"] = max(
                        (p["nav_date"] for p in public["nav_history"]), default=None
                    )
                    report_day = public["holdings_report_date"]
                    if report_day:
                        item["disclosure_message"] = "披露或公开资料已过期，需更新核对"
                    if (
                        report_day
                        and 0 <= (self.now().date() - day(report_day)).days <= 180
                        and 0
                        <= (
                            self.now() - datetime.fromisoformat(public["observed_at"])
                        ).total_seconds()
                        <= 7 * 86400
                    ):
                        item["disclosure_message"] = "披露持仓无法核对"
                        selected = [h for h in public["holdings"] if h["report_date"] == report_day]
                        weights = {}
                        for h in selected:
                            key = (h["asset_type"], h["symbol"])
                            if key in weights or not h["symbol"]:
                                raise ValueError("ambiguous disclosed holding")
                            # FundDetail preserves provider percentages, e.g. 10 means 10%.
                            weights[key] = positive(h["weight"]) / 100
                        coverage = sum(weights.values(), Decimal(0))
                        if weights and coverage <= 1:
                            item.update(
                                disclosed_coverage=float(coverage),
                                holdings_report_date=report_day,
                                disclosure_message="仅已披露持仓，未披露部分未知",
                            )
                            holdings[symbol] = weights
            except (KeyError, TypeError, ValueError, ArithmeticError):
                item.update(
                    disclosed_coverage=None,
                    holdings_report_date=None,
                    latest_nav_date=None,
                    public_observed_at=None,
                    disclosure_message="公开资料校验失败，需重新核对",
                )
            result["items"].append(item)
        if usable:
            weights = [Decimal(str(item["weight"])) for item in result["items"]]
            hhi = sum((w * w for w in weights), Decimal(0))
            result["concentration"] = {
                "largest_fund_weight": float(max(weights)),
                "top3_weight": float(sum(sorted(weights, reverse=True)[:3])),
                "effective_fund_count": float(1 / hhi),
                "fund_value_total": str(total),
            }
        for left, right in combinations(result["items"], 2):
            pair = {
                "left": left["symbol"],
                "right": right["symbol"],
                "overlap_lower_bound": None,
                "message": "披露持仓不足、过期或报告期不同，不能视为无重叠",
            }
            a, b = holdings.get(left["symbol"]), holdings.get(right["symbol"])
            if a and b and left["holdings_report_date"] == right["holdings_report_date"]:
                shared = a.keys() & b.keys()
                pair.update(
                    overlap_lower_bound=float(sum((min(a[k], b[k]) for k in shared), Decimal(0))),
                    report_date=left["holdings_report_date"],
                    message="仅已披露部分的重叠下限，未披露部分未知",
                )
            result["pairs"].append(pair)
        return result
