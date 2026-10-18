"""Diagnose the locked stock strategy without changing its parameters."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quant_assistant.backtest.production import _database_frames
from quant_assistant.config import Settings
from quant_assistant.features.technical import calculate_stock_feature_history
from quant_assistant.models.repositories.database import Database
from quant_assistant.strategies.market_regime import classify_market_regime
from quant_assistant.strategies.stocks import StockStrategyConfig, rank_stocks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--start must precede --end")

    settings = Settings.from_env()
    database = Database(settings.database_path)
    loader, _ = _database_frames(database, args.start, args.end)
    features = calculate_stock_feature_history(loader.prices)
    features = _with_forward_returns(features)
    stock_features = features.loc[features["symbol"].astype(str).ne("000300.SH")].copy()
    stock_features["trade_date"] = pd.to_datetime(stock_features["trade_date"])

    benchmark = loader.prices.loc[loader.prices["symbol"].astype(str).eq("000300.SH")].copy()
    benchmark["trade_date"] = pd.to_datetime(benchmark["trade_date"])
    benchmark = benchmark.sort_values("trade_date", kind="mergesort")
    for horizon in (5, 20):
        benchmark[f"forward_{horizon}"] = (
            benchmark["close"].shift(-horizon) / benchmark["close"] - 1
        )
    benchmark_by_date = benchmark.set_index("trade_date")

    evaluation_dates = _weekly_evaluation_dates(benchmark, args.start, args.end)
    weekly_rows: list[dict[str, object]] = []
    candidate_frequency: Counter[str] = Counter()
    defensive_candidate_frequency: Counter[str] = Counter()
    previous_candidates: set[str] | None = None
    previous_defensive_candidates: set[str] | None = None
    config = StockStrategyConfig()

    for evaluation_date in evaluation_dates:
        timestamp = pd.Timestamp(evaluation_date)
        point_in_time = stock_features.loc[stock_features["trade_date"].eq(timestamp)].copy()
        ranked = rank_stocks(
            point_in_time,
            loader.instruments_as_of(evaluation_date),
            evaluation_date,
            config,
        )
        top = ranked.head(config.max_names)
        defensive_top = ranked.sort_values(
            ["low_volatility_z", "symbol"],
            ascending=[False, True],
            kind="mergesort",
        ).head(config.max_names)
        candidates = set(top["symbol"].astype(str))
        defensive_candidates = set(defensive_top["symbol"].astype(str))
        candidate_frequency.update(candidates)
        defensive_candidate_frequency.update(defensive_candidates)
        turnover = (
            np.nan
            if previous_candidates is None
            else 1 - len(candidates.intersection(previous_candidates)) / config.max_names
        )
        defensive_turnover = (
            np.nan
            if previous_defensive_candidates is None
            else 1
            - len(defensive_candidates.intersection(previous_defensive_candidates))
            / config.max_names
        )
        previous_candidates = candidates
        previous_defensive_candidates = defensive_candidates
        regime = classify_market_regime(benchmark, evaluation_date)
        benchmark_row = benchmark_by_date.loc[timestamp]
        weekly_rows.append(
            {
                "evaluation_date": evaluation_date,
                "regime": regime.value,
                "eligible_count": len(ranked),
                "candidate_count": len(top),
                "candidate_turnover": turnover,
                "defensive_candidate_turnover": defensive_turnover,
                "top5_forward_5": _finite_mean(top.get("forward_5")),
                "defensive_top5_forward_5": _finite_mean(defensive_top.get("forward_5")),
                "universe_forward_5": _finite_mean(ranked.get("forward_5")),
                "benchmark_forward_5": _finite_number(benchmark_row.get("forward_5")),
                "top5_forward_20": _finite_mean(top.get("forward_20")),
                "defensive_top5_forward_20": _finite_mean(defensive_top.get("forward_20")),
                "universe_forward_20": _finite_mean(ranked.get("forward_20")),
                "benchmark_forward_20": _finite_number(benchmark_row.get("forward_20")),
                **{
                    f"{column}_ic_5": _spearman(ranked.get(column), ranked.get("forward_5"))
                    for column in (
                        "score",
                        "momentum_z",
                        "trend_z",
                        "low_volatility_z",
                        "liquidity_z",
                    )
                },
                **{
                    f"{column}_ic_20": _spearman(ranked.get(column), ranked.get("forward_20"))
                    for column in (
                        "score",
                        "momentum_z",
                        "trend_z",
                        "low_volatility_z",
                        "liquidity_z",
                    )
                },
            }
        )

    weekly = pd.DataFrame(weekly_rows)
    trading_dates = tuple(
        value.date() for value in benchmark["trade_date"] if args.start <= value.date() <= args.end
    )
    regime_counts = Counter(
        classify_market_regime(benchmark, value).value for value in trading_dates
    )
    latest_backtest = database.query_df(
        """
        SELECT run_id, start_date, end_date, readiness, metrics_json, limitations_json
        FROM backtest_runs
        WHERE start_date = ? AND end_date = ?
        ORDER BY completed_at DESC LIMIT 1
        """,
        [args.start, args.end],
    )
    persisted_backtest: dict[str, object] | None = None
    if not latest_backtest.empty:
        row = latest_backtest.iloc[0]
        persisted_backtest = {
            "run_id": str(row["run_id"]),
            "readiness": str(row["readiness"]),
            "metrics": json.loads(row["metrics_json"]),
            "limitations": json.loads(row["limitations_json"]),
        }
    payload = {
        "generated_for": {
            "start": args.start.isoformat(),
            "end": args.end.isoformat(),
            "database": str(settings.database_path),
            "strategy_version": config.version,
        },
        "source_quality": {
            "price_rows": len(loader.prices),
            "symbols": int(loader.prices["symbol"].nunique()),
            "duplicate_symbol_dates": int(loader.prices.duplicated(["symbol", "trade_date"]).sum()),
            "limitations": list(loader.limitations),
        },
        "persisted_backtest": persisted_backtest,
        "diagnostic_summary": _summarize(
            weekly,
            regime_counts,
            candidate_frequency,
            defensive_candidate_frequency,
        ),
        "weekly_observations": _json_records(weekly),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    weekly.to_csv(args.output.with_suffix(".weekly.csv"), index=False, encoding="utf-8-sig")
    print(json.dumps(payload["diagnostic_summary"], ensure_ascii=False, indent=2))
    return 0


def _with_forward_returns(features: pd.DataFrame) -> pd.DataFrame:
    result = features.sort_values(["symbol", "trade_date"], kind="mergesort").copy()
    grouped = result.groupby("symbol", sort=False)["close"]
    for horizon in (5, 20):
        result[f"forward_{horizon}"] = grouped.shift(-horizon) / result["close"] - 1
    return result


def _weekly_evaluation_dates(benchmark: pd.DataFrame, start: date, end: date) -> tuple[date, ...]:
    dates = benchmark.loc[
        benchmark["trade_date"].between(pd.Timestamp(start), pd.Timestamp(end)),
        "trade_date",
    ].drop_duplicates()
    calendar = pd.DataFrame({"trade_date": dates})
    iso = calendar["trade_date"].dt.isocalendar()
    calendar["iso_year"] = iso["year"]
    calendar["iso_week"] = iso["week"]
    return tuple(
        value.date()
        for value in calendar.groupby(["iso_year", "iso_week"], sort=True)["trade_date"].max()
    )


def _finite_mean(values: pd.Series | None) -> float | None:
    if values is None:
        return None
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric.loc[np.isfinite(numeric)]
    return float(numeric.mean()) if not numeric.empty else None


def _finite_number(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _spearman(left: pd.Series | None, right: pd.Series | None) -> float | None:
    if left is None or right is None:
        return None
    pairs = pd.DataFrame(
        {
            "left": pd.to_numeric(left, errors="coerce"),
            "right": pd.to_numeric(right, errors="coerce"),
        }
    ).replace([np.inf, -np.inf], np.nan)
    pairs = pairs.dropna()
    if len(pairs) < 30:
        return None
    return _finite_number(pairs["left"].rank().corr(pairs["right"].rank()))


def _summarize(
    weekly: pd.DataFrame,
    regime_counts: Counter[str],
    candidate_frequency: Counter[str],
    defensive_candidate_frequency: Counter[str],
) -> dict[str, object]:
    summary: dict[str, object] = {
        "weekly_evaluations": len(weekly),
        "market_regime_sessions": dict(sorted(regime_counts.items())),
        "mean_candidate_turnover": _finite_mean(weekly.get("candidate_turnover")),
        "mean_defensive_candidate_turnover": _finite_mean(
            weekly.get("defensive_candidate_turnover")
        ),
        "most_frequent_candidates": [
            {"symbol": symbol, "weeks": count}
            for symbol, count in candidate_frequency.most_common(10)
        ],
        "most_frequent_defensive_candidates": [
            {"symbol": symbol, "weeks": count}
            for symbol, count in defensive_candidate_frequency.most_common(10)
        ],
    }
    for horizon in (5, 20):
        top = _finite_mean(weekly.get(f"top5_forward_{horizon}"))
        universe = _finite_mean(weekly.get(f"universe_forward_{horizon}"))
        benchmark = _finite_mean(weekly.get(f"benchmark_forward_{horizon}"))
        defensive = _finite_mean(weekly.get(f"defensive_top5_forward_{horizon}"))
        summary[f"forward_{horizon}"] = {
            "top5_mean": top,
            "defensive_top5_mean": defensive,
            "eligible_universe_mean": universe,
            "benchmark_mean": benchmark,
            "top5_minus_universe": _difference(top, universe),
            "top5_minus_benchmark": _difference(top, benchmark),
            "defensive_top5_minus_benchmark": _difference(defensive, benchmark),
        }
        summary[f"factor_ic_{horizon}"] = {
            column: _finite_mean(weekly.get(f"{column}_ic_{horizon}"))
            for column in (
                "score",
                "momentum_z",
                "trend_z",
                "low_volatility_z",
                "liquidity_z",
            )
        }
    regime_performance: dict[str, dict[str, float | None]] = {}
    if not weekly.empty:
        for regime, frame in weekly.groupby("regime", sort=True):
            regime_performance[str(regime)] = {
                "evaluations": len(frame),
                "top5_forward_5": _finite_mean(frame.get("top5_forward_5")),
                "top5_forward_20": _finite_mean(frame.get("top5_forward_20")),
            }
    summary["performance_by_regime"] = regime_performance
    return summary


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _json_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for row in frame.to_dict("records"):
        records.append(
            {
                key: (
                    value.isoformat()
                    if isinstance(value, date)
                    else None
                    if value is None or (isinstance(value, float) and not np.isfinite(value))
                    else value
                )
                for key, value in row.items()
            }
        )
    return records


if __name__ == "__main__":
    raise SystemExit(main())
