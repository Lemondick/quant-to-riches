"""History and onboarding endpoints dispatched within the session owner's app."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from quant_assistant.services.history import history


def build_history_router(queries):
    router = APIRouter(prefix="/api/v1")
    database = queries._queries.database

    @router.get("/history/{section}/export")
    def export_records(
        section: Literal["ledger", "actions", "corrections"],
        symbol: str = Query("", max_length=32),
        start: date | None = None,
        end: date | None = None,
    ):
        with database.connection() as conn:
            result = history(conn, section, limit=500, symbol=symbol, start=start, end=end)
            if result["total"] > 100000:
                raise HTTPException(413, "单次导出最多10万条，请缩小日期范围")
            items = list(result["items"])
            for page in range(2, (result["total"] + 499) // 500 + 1):
                items.extend(
                    history(
                        conn, section, page=page, limit=500, symbol=symbol, start=start, end=end
                    )["items"]
                )
        return {"items": items, "total": len(items)}

    @router.get("/history/{section}")
    def records(
        section: Literal["ledger", "actions", "corrections"],
        page: int = Query(1, ge=1, le=100000),
        limit: int = Query(50, ge=1, le=500),
        symbol: str = Query("", max_length=32),
        start: date | None = None,
        end: date | None = None,
        export: bool = False,
    ):
        with database.connection() as conn:
            return history(
                conn,
                section,
                page=page,
                limit=limit,
                symbol=symbol,
                start=start,
                end=end,
                export=export,
            )

    @router.get("/onboarding")
    def onboarding():
        with database.connection() as conn:
            positions = conn.execute("SELECT count(*) FROM positions").fetchone()[0]
            cash = conn.execute(
                "SELECT count(*) FROM positions WHERE asset_type='cash'"
            ).fetchone()[0]
            valuation = conn.execute("SELECT count(*) FROM portfolio_valuations").fetchone()[0]
            reports = conn.execute(
                "SELECT count(*) FROM market_advisor_reports WHERE status='completed'"
            ).fetchone()[0]
        model = queries.market_advisor.public_config()["configured"]
        return {
            "steps": [
                {
                    "key": "portfolio",
                    "label": "建立账户、持仓和现金",
                    "done": bool(positions and cash),
                    "url": "/pages/import/index",
                },
                {
                    "key": "baseline",
                    "label": "完成首个有效净值基线",
                    "done": bool(valuation),
                    "url": "/pages/home/index",
                },
                {
                    "key": "models",
                    "label": "配置自己的模型服务",
                    "done": bool(model),
                    "url": "/pages/settings/index",
                },
                {
                    "key": "report",
                    "label": "生成第一份研究报告",
                    "done": bool(reports),
                    "url": "/pages/home/index",
                },
            ]
        }

    return router
