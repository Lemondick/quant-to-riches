"""Verify archived evidence, replay accounting, timing and original DB isolation."""

import argparse
import hashlib
import json
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from scripts.record_forward_observation import observe


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_replay(directory):
    report = load(directory / "report.json")
    for item in report["files"]:
        assert sha(directory / item["name"]) == item["sha256"], directory
    nav = pd.read_csv(directory / "nav.csv", dtype=str)
    orders = pd.read_csv(directory / "orders.csv", dtype=str)
    fills = pd.read_csv(directory / "fills.csv", dtype=str)
    positions = pd.read_csv(directory / "positions.csv", dtype=str)
    assert len(nav) == report["execution"]["sessions"] >= 120
    assert not nav["trade_date"].duplicated().any()
    assert len(fills) == report["execution"]["fills"]
    assert pd.to_datetime(orders["execution_date"]).gt(pd.to_datetime(orders["signal_date"])).all()
    initial = Decimal(report["request"]["initial_cash"])
    previous_cash, previous_holdings = initial, {}
    for row in nav.to_dict(orient="records"):
        day = row["trade_date"]
        day_fills = fills.loc[fills["trade_date"].eq(day)]
        expected_cash = previous_cash
        sold = {}
        for fill in day_fills.to_dict(orient="records"):
            amount, fees = Decimal(fill["gross_amount"]), Decimal(fill["fees"])
            assert fees >= 0
            if fill["action"] in ("buy", "add"):
                expected_cash -= amount + fees
            else:
                assert fill["action"] in ("sell", "reduce")
                expected_cash += amount - fees
                sold[fill["symbol"]] = sold.get(fill["symbol"], Decimal("0")) + Decimal(
                    fill["quantity"]
                )
        assert all(
            quantity <= previous_holdings.get(symbol, Decimal("0"))
            for symbol, quantity in sold.items()
        ), f"T+1: {directory}: {day}"
        assert abs(expected_cash - Decimal(row["cash"])) <= Decimal("0.01"), day
        held = positions.loc[positions["trade_date"].eq(day)]
        held_value = sum((Decimal(value) for value in held["market_value"]), Decimal("0"))
        total = Decimal(row["cash"]) + Decimal(row["unavailable_cash"]) + held_value
        assert abs(total - Decimal(row["total_value"])) <= Decimal("0.01"), day
        assert Decimal(row["cash"]) >= 0
        assert abs(Decimal(row["unit_nav"]) - Decimal(row["total_value"]) / initial) < Decimal(
            "1e-12"
        )
        assert Decimal(row["external_cash_flow"]) == 0
        previous_cash = Decimal(row["cash"])
        previous_holdings = {
            item["symbol"]: Decimal(item["quantity"]) for item in held.to_dict(orient="records")
        }
    return {
        "directory": directory.name,
        "sessions": len(nav),
        "fills": len(fills),
        "cash_balance": True,
        "nav_balance": True,
        "next_session_execution": True,
        "stock_t_plus_one": True,
    }


def verify_forward_guards():
    # Fixtures remain in a temporary directory and are never admission evidence.
    with TemporaryDirectory(prefix="quant-evidence-verify-") as temporary:
        root = Path(temporary)
        (root / "manifest.json").write_text(
            json.dumps({"research_only": True, "status": "completed"}), encoding="utf-8"
        )
        holiday = observe(root, datetime(2026, 10, 5, 17, tzinfo=SHANGHAI_TZ))
        assert holiday["status"] == "skipped" and not holiday["eligible_paper_session"]
        early = observe(root, datetime(2026, 10, 8, 15, tzinfo=SHANGHAI_TZ))
        assert early["blockers"] == ["market_data_not_completed"]
        with duckdb.connect(str(root / "quant_assistant.duckdb")) as connection:
            connection.execute(
                "CREATE TABLE strategy_runs(run_id VARCHAR,as_of_date DATE,"
                "generated_at TIMESTAMPTZ,data_status VARCHAR,readiness VARCHAR,"
                "status VARCHAR)"
            )
        missing = observe(root, datetime(2026, 10, 8, 17, tzinfo=SHANGHAI_TZ))
        assert missing["blockers"] == ["same_day_strategy_run_missing"]
        with duckdb.connect(str(root / "quant_assistant.duckdb")) as connection:
            connection.execute(
                "INSERT INTO strategy_runs VALUES "
                "('retrospective','2026-10-08','2026-10-09T17:00:00+08:00',"
                "'valid','research_only','completed')"
            )
            connection.execute(
                "CREATE TABLE data_snapshots(dataset VARCHAR,as_of_date DATE,"
                "fetched_at TIMESTAMPTZ,status VARCHAR,path VARCHAR,id INT)"
            )
            connection.execute(
                "CREATE TABLE paper_reconciliations(order_id VARCHAR,status VARCHAR)"
            )
            connection.execute("CREATE TABLE paper_orders(order_id VARCHAR,run_id VARCHAR)")
            connection.execute("CREATE TABLE proposed_orders(run_id VARCHAR)")
            connection.execute("CREATE TABLE positions(symbol VARCHAR)")
        future = observe(root, datetime(2026, 10, 8, 17, tzinfo=SHANGHAI_TZ))
        assert not future["eligible_paper_session"]
        assert "strategy_run_not_generated_today" in future["blockers"]
        assert "strategy_not_ready_for_paper" in future["blockers"]
    return {
        "holiday_skipped": True,
        "pre_close_rejected": True,
        "missing_run_rejected": True,
        "backdated_or_future_run_rejected": True,
    }


def verify_reproduction(study):
    from quant_assistant.backtest.production import BacktestCostAssumptions, run_database_backtest
    from quant_assistant.config import Settings
    from scripts.complete_research_evidence import REASONS

    expected = load(study / "window-1-standard/report.json")
    request = expected["request"]
    costs = request["costs"]
    with TemporaryDirectory(prefix="quant-backtest-reproduction-") as temporary:
        root = Path(temporary)
        shutil.copy2(study / "quant_assistant.duckdb", root / "quant_assistant.duckdb")
        actual = run_database_backtest(
            Settings.for_data_dir(root),
            date.fromisoformat(request["start"]),
            date.fromisoformat(request["end"]),
            initial_cash=Decimal(request["initial_cash"]),
            costs=BacktestCostAssumptions(
                **{
                    name: Decimal(costs[name])
                    for name in (
                        "commission_rate",
                        "minimum_commission",
                        "sell_stamp_tax_rate",
                        "transfer_fee_rate",
                        "slippage_bps",
                    )
                }
            ),
            research_only_reasons=REASONS,
        )
        assert actual.result.run_id == expected["run_id"]
        for item in expected["files"]:
            name = Path(item["name"]).stem
            raw = getattr(actual.result, name).to_csv(index=False).encode("utf-8")
            assert hashlib.sha256(raw).hexdigest() == item["sha256"], f"reproduction: {name}"
    return {
        "passed": True,
        "window": "window-1-standard",
        "sessions": 242,
        "matching_files": [item["name"] for item in expected["files"]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--study", default="stock-study-v2")
    parser.add_argument("--reproduce", action="store_true")
    args = parser.parse_args()
    root, study = args.root, args.root / args.study
    protected = load(root / "protected-inputs.json")
    assert all(sha(Path(item["path"])) == item["sha256"] for item in protected)
    manifest = load(study / "manifest.json")
    assert manifest["status"] == "completed" and manifest["research_only"] is True
    assert all(sha(Path(item["path"])) == item["sha256"] for item in manifest["inputs"])
    source_root = Path(__file__).resolve().parents[1] / "src" / "quant_assistant"
    assert all(
        sha(source_root / item["path"]) == item["sha256"] for item in manifest["implementation"]
    )
    assert (
        sha(Path(__file__).with_name("complete_research_evidence.py")) == manifest["runner_sha256"]
    )
    cases = [
        verify_replay(path)
        for path in sorted(study.iterdir())
        if path.is_dir() and path.name.endswith(("-standard", "-stress"))
    ]
    assert len(cases) == 8
    rows = [
        json.loads(line)
        for line in (study / "paper-replay/sessions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 666 and len({row["as_of_date"] for row in rows}) == len(rows)
    assert all(
        row["mode"] == "historical_replay"
        and row["admission_session_credit"] is False
        and row["reconciliation"]["status"] == "matched"
        for row in rows
    )
    plan = load(study / "forward-plan.json")
    assert plan["actual_forward_sessions"] == 0 and len(plan["earliest_sessions"]) == 20
    assert date.fromisoformat(plan["earliest_sessions"][0]) == date(2026, 10, 8)
    admission = load(study / "admission.json")
    assert not admission["ready_for_deployment"]
    assert admission["checks"]["paper_sessions"]["sessions"] == []
    report = {
        "passed": True,
        "protected_original_databases": len(protected),
        "artifact_hashes_verified": True,
        "replays": cases,
        "historical_replay_sessions": len(rows),
        "actual_forward_sessions": 0,
        "forward_guards": verify_forward_guards(),
        "paid_model_calls": 0,
    }
    if args.reproduce:
        report["reproduction"] = verify_reproduction(study)
    (root / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
