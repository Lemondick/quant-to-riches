"""Explicit research/read projections over a C# approved account snapshot."""

from datetime import date

from analysis_context import build_context

from quant_assistant.controllers import schemas
from quant_assistant.services.benchmarks import BenchmarkService

READ_CAPABILITIES = (
    "analysis.data_status",
    "analysis.decision",
    "analysis.stocks",
    "analysis.funds",
    "analysis.backtest",
    "analysis.lifecycle",
    "analysis.style_drift",
    "analysis.valuation_alerts",
    "analysis.profit",
    "analysis.benchmark",
    "analysis.price_dates",
)


def execute_read(kind, payload):
    database, queries, _directory = build_context(payload["snapshot"])
    try:
        if kind == "analysis.price_dates":
            result = {}
            with database.connection() as connection:
                for asset, table, column in (
                    ("fund", "fund_nav", "nav_date"),
                    ("stock", "daily_prices", "trade_date"),
                    ("etf", "daily_prices", "trade_date"),
                ):
                    for symbol, day in connection.execute(
                        f"SELECT symbol,max({column}) FROM {table} GROUP BY symbol"
                    ).fetchall():
                        result[f"{asset}:{symbol}"] = str(day) if day is not None else None
            return result
        if kind == "analysis.data_status":
            result, schema = queries.data_status(), schemas.DataStatusResponse
        elif kind == "analysis.decision":
            result, schema = queries.latest_decision(), schemas.DecisionResponse
        elif kind == "analysis.stocks":
            result, schema = (
                queries.stock_candidates(payload["run_id"]),
                schemas.CandidateListResponse,
            )
        elif kind == "analysis.funds":
            result, schema = (
                queries.fund_candidates(payload["run_id"]),
                schemas.CandidateListResponse,
            )
        elif kind == "analysis.backtest":
            result, schema = queries.latest_backtest(), schemas.BacktestResponse
        elif kind == "analysis.lifecycle":
            result, schema = (
                {"items": queries.fund_lifecycle_events(payload.get("symbol"), payload["limit"])},
                schemas.FundLifecycleEventsResponse,
            )
        elif kind == "analysis.style_drift":
            result, schema = (
                {"items": queries.fund_style_drift_events(payload.get("symbol"), payload["limit"])},
                schemas.FundStyleDriftEventsResponse,
            )
        elif kind == "analysis.valuation_alerts":
            result, schema = (
                {"items": queries.valuation_alerts(payload.get("symbol"), payload["limit"])},
                schemas.FundValuationAlertsResponse,
            )
        elif kind == "analysis.profit":
            result, schema = (
                queries.profit_attribution(),
                schemas.ProfitAttributionResponse,
            )
        elif kind == "analysis.benchmark":
            result = BenchmarkService(database).compare(
                payload["benchmark_id"],
                start_date=date.fromisoformat(payload["start_date"])
                if payload.get("start_date")
                else None,
                end_date=date.fromisoformat(payload["end_date"])
                if payload.get("end_date")
                else None,
            )
            schema = schemas.BenchmarkCompareResponse
        else:
            raise ValueError("unsupported_operation")
        return (
            None
            if result is None
            else schema.model_validate(result).model_dump(mode="json", by_alias=True)
        )
    finally:
        database.close()
