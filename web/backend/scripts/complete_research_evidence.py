"""Build isolated, reproducible stock evidence from reviewed public archives."""

import argparse
import hashlib
import json
import platform
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from math import isfinite
from pathlib import Path

import pandas as pd

from quant_assistant.backtest.engine import BacktestEngine, BacktestRequest
from quant_assistant.backtest.production import (
    BacktestCostAssumptions,
    ProductionStockBacktestStrategy,
    _database_frames,
    run_database_backtest,
)
from quant_assistant.backtest.stock_execution import StockExecutionModel
from quant_assistant.common.time import now_from_clock
from quant_assistant.config import Settings
from quant_assistant.data.point_in_time import (
    import_point_in_time_prices,
    load_point_in_time_universe,
)
from quant_assistant.data.snapshots import SnapshotWriter
from quant_assistant.data.validation import validate_dataset
from quant_assistant.models.entities import AssetType, DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.baostock import compare_sina_history, normalize_history, sdk_symbol
from quant_assistant.services.daily_decision import DailyDecisionService
from quant_assistant.services.portfolio_commands import PortfolioCommandService, PositionInput
from quant_assistant.services.production_readiness import build_report
from quant_assistant.services.trading_calendar import TradingCalendar
from quant_assistant.strategies.stocks import _DEFAULT_CONFIG

REASONS = ("simulated_account", "selected_stock_research_universe", "corporate_actions_not_applied")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(
        json.dumps(value, default=str, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def records(frame):
    return frame.astype(object).where(frame.notna(), None).to_dict(orient="records")


def checked_archive(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") not in (None, "completed") or (
        "verified" in manifest and manifest["verified"] is not True
    ):
        raise ValueError(f"archive not completed/verified: {directory}")
    for item in manifest["files"]:
        path = (directory / item["name"]).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path) != item["sha256"]:
            raise ValueError(f"archive hash mismatch: {directory}")
    return manifest


def prepare(args, settings):
    history = checked_archive(args.history)
    checked_archive(args.universe)
    checked_archive(args.limits)
    symbols = tuple(history["symbols"])
    database = Database(settings.database_path)
    database.initialize()
    writer = SnapshotWriter(settings.raw_snapshot_dir, "reviewed_public_archive")
    end = date.fromisoformat(history["end"])
    checks = []
    for symbol in (*symbols, "000300.SH"):
        code = sdk_symbol(symbol)
        raw = pd.read_csv(args.history / f"baostock-full-{code}.csv", dtype=str)
        is_index = symbol == "000300.SH"
        if not is_index:
            independent = pd.read_csv(args.history / f"sina-full-{code.replace('.', '')}.csv")
            check = {"symbol": symbol, **compare_sina_history(raw, independent)}
            if not check["verified"]:
                raise ValueError(f"independent verification failed: {symbol}")
            checks.append(check)
        frame = normalize_history(raw, symbol, is_index=is_index)
        dataset = "index_history" if is_index else "stock_history"
        validation = validate_dataset(dataset, frame, end)
        if validation.status is not DataStatus.VALID:
            raise ValueError(f"history invalid: {symbol}: {validation.issues}")
        snapshot = writer.write(dataset, end, frame)
        frame["source_snapshot_id"] = database.record_snapshot(snapshot, validation)
        database.upsert_frame("daily_prices", frame)
    metadata = load_point_in_time_universe(args.universe / "point-in-time-universe.csv")
    index = pd.DataFrame(
        [
            {
                "symbol": "000300.SH",
                "asset_type": "index",
                "name": "沪深300",
                "exchange": "SH",
                "is_st": False,
                "is_suspended": False,
                "status": "active",
                "as_of_date": min(metadata["as_of_date"]),
            }
        ]
    )
    database.upsert_frame("instrument_history", pd.concat([metadata, index], ignore_index=True))
    latest = metadata.sort_values("as_of_date").groupby("symbol", as_index=False).tail(1)
    index["as_of_date"] = end
    latest = pd.concat([latest, index], ignore_index=True)
    database.upsert_frame("instruments", latest)
    prices = database.query_df("SELECT * FROM daily_prices ORDER BY trade_date,symbol")
    for dataset, frame in [("instruments", latest), ("daily_prices", prices)]:
        validation = validate_dataset(dataset, frame, end)
        if validation.status is not DataStatus.VALID:
            raise ValueError(f"invalid {dataset}: {validation.issues}")
        database.record_snapshot(writer.write(dataset, end, frame), validation)
    database.close()
    imported = import_point_in_time_prices(settings, args.limits / "point-in-time-prices.csv")
    if imported.status is not DataStatus.VALID:
        raise ValueError("latest verified limit import failed")
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_frame(
        "instruments",
        pd.DataFrame(
            [
                {
                    "symbol": "CASH.CNY",
                    "asset_type": "cash",
                    "name": "研究模拟现金",
                    "as_of_date": end,
                }
            ]
        ),
    )
    commands = PortfolioCommandService(database, now_from_clock)
    commands.import_initial_account(
        "research-simulation-baseline",
        end,
        (
            PositionInput(
                "CASH.CNY", AssetType.CASH, Decimal("100000"), Decimal("1"), Decimal("100000"), end
            ),
        ),
        reason="独立研究模拟账户假设本金，非真实账户导入或持仓证据",
    )
    database.close()
    return checks


def export_result(directory, result, request, metrics, qualification):
    directory.mkdir()
    for name in ("nav", "orders", "fills", "positions"):
        getattr(result, name).to_csv(directory / f"{name}.csv", index=False)
    metric_values = {
        key: None if isinstance(value, float) and not isfinite(value) else value
        for key, value in asdict(metrics).items()
    }
    report = {
        "run_id": result.run_id,
        "generated_at": now_from_clock().isoformat(),
        "mode": "historical_replay",
        "research_only": True,
        "admission_session_credit": False,
        "request": request,
        "metrics": metric_values,
        "execution": {
            "sessions": len(result.trading_dates),
            "fills": len(result.fills),
            "orders": len(result.orders),
        },
        "qualification_passed": qualification.qualification_passed,
        "qualification_failures": qualification.qualification_failures,
        "limitations": result.limitations,
        "files": [
            {"name": f"{name}.csv", "sha256": digest(directory / f"{name}.csv")}
            for name in ("nav", "orders", "fills", "positions")
        ],
    }
    write_json(directory / "report.json", report)
    return report


def replay_records(output, result):
    output.mkdir()
    generated_at = now_from_clock().isoformat()
    rows = []
    for nav in records(result.nav):
        day = nav["trade_date"]
        positions = result.positions.loc[result.positions["trade_date"].eq(day)]
        holdings_value = sum(positions["market_value"], Decimal("0"))
        difference = Decimal(str(nav["total_value"])) - (
            Decimal(str(nav["cash"])) + Decimal(str(nav["unavailable_cash"])) + holdings_value
        )
        if abs(difference) > Decimal("0.01"):
            raise ValueError(f"paper replay balance mismatch: {day}")
        rows.append(
            {
                "generated_at": generated_at,
                "as_of_date": str(day),
                "mode": "historical_replay",
                "admission_session_credit": False,
                "research_only": True,
                "backtest_run_id": result.run_id,
                "nav": nav,
                "positions": records(positions),
                "orders": records(result.orders.loc[result.orders["execution_date"].eq(day)]),
                "fills": records(result.fills.loc[result.fills["trade_date"].eq(day)]),
                "reconciliation": {"status": "matched", "difference": str(difference)},
            }
        )
    (output / "sessions.jsonl").write_text(
        "".join(
            json.dumps(row, default=str, ensure_ascii=False, allow_nan=False) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    write_json(
        output / "summary.json",
        {
            "mode": "historical_replay",
            "research_only": True,
            "replay_sessions": len(rows),
            "actual_forward_sessions": 0,
            "admission_session_credit": False,
            "reconciliation_mismatches": 0,
            "first_date": rows[0]["as_of_date"],
            "latest_date": rows[-1]["as_of_date"],
            "sessions_sha256": digest(output / "sessions.jsonl"),
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--limits", type=Path, required=True)
    parser.add_argument("--window", action="append", required=True, help="ISO_START:ISO_END")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    windows = [
        tuple(date.fromisoformat(value) for value in item.split(":")) for item in args.window
    ]
    if any(len(pair) != 2 or pair[0] >= pair[1] for pair in windows):
        parser.error("invalid windows")
    if len(windows) < 3 or any(
        right[0] <= left[1]
        for left, right in zip(sorted(windows), sorted(windows)[1:], strict=False)
    ):
        parser.error("require at least three nonoverlapping windows")
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        return run(args, windows)
    except Exception as exc:
        path = args.output / "manifest.json"
        if path.is_file():
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest.update(
                status="failed",
                error_type=type(exc).__name__,
                finished_at=now_from_clock().isoformat(),
            )
            write_json(path, manifest)
        raise


def run(args, windows):
    settings = Settings.for_data_dir(args.output)
    source_root = Path(__file__).resolve().parents[1] / "src" / "quant_assistant"
    inputs = [
        {"path": str(path.resolve()), "sha256": digest(path)}
        for directory in (args.history, args.universe, args.limits)
        for path in sorted(directory.iterdir())
        if path.is_file()
    ]
    implementation = [
        {"path": str(path.relative_to(source_root)), "sha256": digest(path)}
        for path in sorted(source_root.rglob("*.py"))
    ]
    manifest = {
        "started_at": now_from_clock().isoformat(),
        "status": "running",
        "research_only": True,
        "inputs": inputs,
        "implementation": implementation,
        "runner_sha256": digest(__file__),
        "python": platform.python_version(),
        "stock_config": asdict(_DEFAULT_CONFIG),
        "risk_rules": "unchanged",
    }
    write_json(args.output / "manifest.json", manifest)
    checks = prepare(args, settings)
    write_json(args.output / "crosscheck.json", checks)
    costs = BacktestCostAssumptions(
        Decimal("0.0003"), Decimal("5"), Decimal("0.0005"), Decimal("0.00001"), Decimal("5")
    )
    for label, (start, end) in [
        *((f"window-{i + 1}", pair) for i, pair in enumerate(windows)),
        ("continuous", (min(pair[0] for pair in windows), max(pair[1] for pair in windows))),
    ]:
        qualified = run_database_backtest(
            settings,
            start,
            end,
            initial_cash=Decimal("100000"),
            costs=costs,
            research_only_reasons=REASONS,
        )
        base_request = {
            "start": str(start),
            "end": str(end),
            "initial_cash": "100000",
            "config": asdict(_DEFAULT_CONFIG),
            "costs": asdict(costs),
        }
        standard = export_result(
            args.output / f"{label}-standard",
            qualified.result,
            {**base_request, "cost_multiplier": 1},
            qualified.standard_metrics,
            qualified,
        )
        database = Database(settings.database_path)
        loader, _ = _database_frames(database, start, end)
        database.close()
        request = BacktestRequest(
            start,
            end,
            Decimal("100000"),
            _DEFAULT_CONFIG.version,
            {
                "strategy": "locked_stock_base_v1",
                "costs": {key: str(value) for key, value in asdict(costs).items()},
                "cost_stress": "2x",
            },
            ("unverified_broker_fee_assumptions", *REASONS),
        )
        stress = BacktestEngine(
            loader,
            stock_execution=StockExecutionModel(
                costs.schedule(start, stress_multiplier=Decimal("2"))
            ),
            strategy=ProductionStockBacktestStrategy(),
        ).run(request)
        from quant_assistant.backtest.metrics import calculate_performance

        stress_metrics = calculate_performance(
            stress.nav,
            loader.prices.loc[
                loader.prices["symbol"].eq("000300.SH") & loader.prices["trade_date"].ge(start)
            ],
        )
        if json.dumps(asdict(stress_metrics), default=str, sort_keys=True) != json.dumps(
            asdict(qualified.stress_metrics), default=str, sort_keys=True
        ):
            raise ValueError("repeated stress result differs from production runner")
        export_result(
            args.output / f"{label}-stress",
            stress,
            {**base_request, "cost_multiplier": 2},
            stress_metrics,
            qualified,
        )
        if label == "continuous":
            replay_records(args.output / "paper-replay", qualified.result)
        print(
            json.dumps(
                {
                    "window": label,
                    "sessions": standard["execution"]["sessions"],
                    "qualification_passed": qualified.qualification_passed,
                    "failures": qualified.qualification_failures,
                }
            ),
            flush=True,
        )
    database = Database(settings.database_path)
    decision = DailyDecisionService(database).run(max(pair[1] for pair in windows))
    write_json(
        args.output / "latest-research-decision.json",
        {
            "run_id": decision.run_id,
            "as_of_date": str(decision.as_of_date),
            "generated_at": now_from_clock().isoformat(),
            "data_status": decision.status.value,
            "readiness": decision.readiness.value,
            "blockers": decision.blockers,
            "mode": "retrospective_research_observation",
            "admission_session_credit": False,
        },
    )
    write_json(
        args.output / "admission.json",
        {"scope": "isolated_simulated_account", "research_only": True, **build_report(database)},
    )
    database.close()
    calendar = TradingCalendar()
    day = now_from_clock().date()
    future = []
    while len(future) < 20:
        day += timedelta(days=1)
        if calendar.trading_day(day) is None:
            break
        if calendar.trading_day(day):
            future.append(str(day))
    write_json(
        args.output / "forward-plan.json",
        {
            "source": calendar.load(now_from_clock().year)["source"],
            "earliest_sessions": future,
            "conditional_on_prerequisites": True,
            "actual_forward_sessions": 0,
            "scheduler_installed": False,
            "minimum_sessions": 20,
        },
    )
    if any(digest(item["path"]) != item["sha256"] for item in inputs):
        raise ValueError("source changed during replay")
    manifest.update(status="completed", finished_at=now_from_clock().isoformat())
    write_json(args.output / "manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
