"""Shared, fail-closed admission for explicitly observed forward paper sessions."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path

import duckdb
import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.data.snapshots import _frame_sha256
from quant_assistant.portfolio.models import RiskPolicy
from quant_assistant.services.trading_calendar import TradingCalendar
from quant_assistant.strategies.funds import _DEFAULT_CONFIG as FUND_CONFIG
from quant_assistant.strategies.stocks import _DEFAULT_CONFIG as STOCK_CONFIG

FORMAT = 1
FREEZE_NAME = "forward-freeze.json"
REQUIRED_SOURCES = ("daily_prices", "instruments", "index_history")
TERMINAL_ORDER_STATUSES = {"filled", "cancelled", "rejected", "expired"}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def strategy_fingerprint(versions: Sequence[str], configs=None, *, policy=None) -> str:
    """Hash strategy configuration and its implementation, excluding admission/UI code."""
    configs = configs or (STOCK_CONFIG, FUND_CONFIG)
    selected = {config.version: asdict(config) for config in configs if config.version in versions}
    if set(selected) != set(versions) or not selected:
        return ""
    package = Path(__file__).resolve().parents[1]
    modules = {
        "portfolio/orders.py",
        "portfolio/risk.py",
        "portfolio/aggregation.py",
        "strategies/market_regime.py",
    }
    if STOCK_CONFIG.version in versions:
        modules.update(("strategies/stocks.py", "features/technical.py"))
    if FUND_CONFIG.version in versions:
        modules.update(("strategies/funds.py", "features/funds.py", "funds/fees.py"))
    from quant_assistant.services.daily_decision import DefaultStrategyRunner

    return _digest(
        {
            "configs": selected,
            "risk_policy": asdict(policy or RiskPolicy()),
            "runner": hashlib.sha256(
                inspect.getsource(DefaultStrategyRunner.run_with_artifacts).encode("utf-8")
            ).hexdigest(),
            "implementation": {
                name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in modules
            },
        }
    )


def _timestamp(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evidence timestamp must include a timezone")
    return parsed.astimezone(SHANGHAI_TZ)


def site_root(data_dir: Path) -> Path:
    data_dir = Path(data_dir).resolve()
    if data_dir.parent.name == "users":
        return data_dir.parent.parent
    return data_dir


def freeze_forward_strategy(data_dir: Path, versions: Sequence[str], now: datetime) -> dict:
    """Explicitly start a new evidence period before generating any credited decision."""
    data_dir = Path(data_dir).resolve()
    manifest = data_dir / "manifest.json"
    if manifest.is_file() and json.loads(manifest.read_text("utf-8")).get("research_only") is True:
        raise ValueError("archived research workspaces cannot become admission evidence")
    versions = sorted(set(versions))
    fingerprint = strategy_fingerprint(versions)
    if not fingerprint:
        raise ValueError("only the configured strategy versions can be frozen")
    payload = {
        "format": FORMAT,
        "mode": "forward_observation",
        "frozen_at": _timestamp(now).isoformat(),
        "strategy_versions": versions,
        "strategy_fingerprint": fingerprint,
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    # Never silently replace the freeze and mix two observation periods.
    with (data_dir / FREEZE_NAME).open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    return payload


def _load_freeze(data_dir):
    value = json.loads((data_dir / FREEZE_NAME).read_text("utf-8"))
    if (
        value.get("format") != FORMAT
        or value.get("mode") != "forward_observation"
        or not isinstance(value.get("strategy_versions"), list)
        or not value["strategy_versions"]
        or not value.get("strategy_fingerprint")
    ):
        raise ValueError("invalid forward strategy freeze")
    _timestamp(value["frozen_at"])
    return value


def evaluate_observation(connection, data_dir: Path, now: datetime, *, run_id=None) -> dict:
    """Inspect a decision at observation time without writing or assigning implicit credit."""
    data_dir, now = Path(data_dir).resolve(), _timestamp(now)
    day = now.date()
    report = {
        "format": FORMAT,
        "observed_at": now.isoformat(),
        "as_of_date": str(day),
        "mode": "forward_observation",
        "research_only": False,
        "eligible_paper_session": False,
        "blockers": [],
        "decision": None,
    }
    calendar = TradingCalendar(site_root(data_dir) / "calendars")
    trading = calendar.trading_day(day)
    if trading is not True:
        report.update(
            status="skipped",
            blockers=["market_closed" if trading is False else "calendar_unavailable"],
        )
        return report
    if now.time() < time(16):
        report.update(status="skipped", blockers=["market_data_not_completed"])
        return report
    clause = " AND run_id=?" if run_id is not None else ""
    frame = connection.execute(
        "SELECT * FROM strategy_runs WHERE as_of_date=?"
        + clause
        + " ORDER BY generated_at DESC LIMIT 1",
        [day, run_id] if run_id is not None else [day],
    ).fetchdf()
    if frame.empty:
        report.update(status="blocked", blockers=["same_day_strategy_run_missing"])
        return report
    row = frame.iloc[0].to_dict()
    run_id = str(row["run_id"])
    generated = _timestamp(row["generated_at"])
    blockers = report["blockers"]
    if generated.date() != day or generated > now or generated.time() < time(16):
        blockers.append("strategy_run_not_generated_today")
    if (
        row["data_status"] != "valid"
        or row["status"] != "completed"
        or row["readiness"] not in {"paper", "reference"}
    ):
        blockers.append("strategy_not_ready_for_paper")
    parameters = json.loads(row.get("parameters_json", "{}"))
    versions = sorted(set(str(row.get("strategy_version", "")).split("+")))
    report["decision"] = {
        "run_id": run_id,
        "generated_at": generated.isoformat(),
        "data_status": row["data_status"],
        "readiness": row["readiness"],
        "status": row["status"],
        "strategy_versions": versions,
        "run_fingerprint": _digest(row),
    }
    try:
        freeze = _load_freeze(data_dir)
        report["freeze_sha256"] = _digest(freeze)
        if _timestamp(freeze["frozen_at"]) > generated:
            blockers.append("strategy_frozen_after_run")
        if (
            versions != freeze["strategy_versions"]
            or parameters.get("strategy_fingerprint") != freeze["strategy_fingerprint"]
            or freeze["strategy_fingerprint"] != strategy_fingerprint(versions)
        ):
            blockers.append("frozen_strategy_mismatch")
    except (OSError, ValueError, KeyError, TypeError):
        blockers.append("forward_strategy_freeze_missing_or_invalid")
    manifest = data_dir / "manifest.json"
    if manifest.is_file():
        try:
            if json.loads(manifest.read_text("utf-8")).get("research_only") is True:
                report["research_only"] = True
                blockers.append("research_workspace_not_admission_evidence")
        except (OSError, ValueError, AttributeError):
            blockers.append("workspace_manifest_invalid")
    snapshots = connection.execute(
        "SELECT * FROM data_snapshots WHERE as_of_date=? "
        "QUALIFY row_number() OVER(PARTITION BY dataset ORDER BY id DESC)=1",
        [day],
    ).fetchdf()
    sources = {str(item["dataset"]): item for item in snapshots.to_dict("records")}
    report["sources"] = []
    required = REQUIRED_SOURCES + (("fund_daily",) if FUND_CONFIG.version in versions else ())
    for name in required:
        source = sources.get(name)
        if source is None:
            blockers.append(f"same_day_valid_snapshot_missing:{name}")
            continue
        fetched = _timestamp(source["fetched_at"])
        if source["status"] != "valid" or fetched.date() != day or fetched > generated:
            blockers.append(f"same_day_valid_snapshot_missing:{name}")
            continue
        if json.loads(source.get("issues_json", "[]")) != []:
            blockers.append(f"snapshot_validation_issues:{name}")
            continue
        if int(source["id"]) > int(row.get("data_snapshot_id") or 0):
            blockers.append(f"snapshot_not_consumed_by_run:{name}")
            continue
        path = Path(source["path"]).resolve()
        if not path.is_relative_to(data_dir) or not path.is_file():
            blockers.append(f"snapshot_file_missing:{name}")
            continue
        try:
            snapshot_frame = pd.read_parquet(path)
            if _frame_sha256(snapshot_frame) != source["sha256"] or len(snapshot_frame) != int(
                source["row_count"]
            ):
                blockers.append(f"snapshot_content_mismatch:{name}")
                continue
        except (OSError, ValueError, TypeError, KeyError):
            blockers.append(f"snapshot_content_unreadable:{name}")
            continue
        report["sources"].append(
            {
                "dataset": name,
                "snapshot_id": int(source["id"]),
                "fetched_at": fetched.isoformat(),
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "snapshot_fingerprint": _digest(source),
            }
        )
    if blockers:
        report.update(status="blocked", blockers=list(dict.fromkeys(blockers)))
        return report
    orders = connection.execute(
        "SELECT * FROM paper_orders WHERE run_id=? ORDER BY order_id", [run_id]
    ).fetchdf()
    proposals = connection.execute(
        "SELECT * FROM proposed_orders WHERE run_id=? ORDER BY id", [run_id]
    ).fetchdf()
    proposal_ids = {int(item["id"]) for item in proposals.to_dict("records")}
    covered_ids = {int(item["proposal_id"]) for item in orders.to_dict("records")}
    if proposal_ids != covered_ids:
        blockers.append("paper_proposal_not_reconciled")
    report["proposal_count"] = len(proposals)
    report["proposals_sha256"] = _digest(proposals.to_dict("records"))
    report["order_count"] = len(orders)
    report["orders_sha256"] = _digest(orders.to_dict("records"))
    reconciliations = []
    for order in orders.to_dict("records"):
        if order.get("status") not in TERMINAL_ORDER_STATUSES:
            blockers.append("paper_orders_unresolved")
        latest = connection.execute(
            "SELECT * FROM paper_reconciliations WHERE order_id=? "
            "ORDER BY checked_at DESC,reconciliation_id DESC LIMIT 1",
            [order["order_id"]],
        ).fetchdf()
        if latest.empty:
            blockers.append("paper_reconciliation_missing")
            continue
        reconciliation = latest.iloc[0].to_dict()
        checked = _timestamp(reconciliation["checked_at"])
        if reconciliation["status"] != "matched":
            blockers.append("paper_reconciliation_mismatch")
        if checked > now or checked < _timestamp(order["updated_at"]):
            blockers.append("paper_reconciliation_not_current")
        reconciliations.append(reconciliation)
    report["reconciliation_sha256"] = _digest(reconciliations)
    if not blockers:
        # A confirmed historical replay revises the account trajectory captured
        # by an earlier observation, so affected old evidence must lose credit.
        replay_events = connection.execute(
            "SELECT after_json FROM audit_log WHERE entity_type='ledger_replay' AND changed_at>?",
            [now],
        ).fetchall()
        for (payload,) in replay_events:
            try:
                effective = date.fromisoformat(json.loads(payload)["effective_date"])
            except (ValueError, KeyError, TypeError):
                blockers.append("ledger_replay_provenance_invalid")
                break
            if effective <= day:
                blockers.append("account_history_replayed_after_observation")
                break
    report["blockers"] = list(dict.fromkeys(blockers))
    report["eligible_paper_session"] = not report["blockers"]
    report["status"] = "recorded" if report["eligible_paper_session"] else "blocked"
    return report


def valid_forward_sessions(
    database, *, before_date: date | None = None, now=None
) -> tuple[dict, ...]:
    """Revalidate persisted observations; strategy_runs alone never earn admission credit."""
    data_dir = database.path.resolve().parent
    now = _timestamp(now or datetime.now(SHANGHAI_TZ))
    sessions = {}
    with database.connection() as connection:
        for path in sorted((data_dir / "forward-observations").glob("*/*.json")):
            try:
                observed = json.loads(path.read_text("utf-8"))
                if (
                    not isinstance(observed, Mapping)
                    or observed.get("eligible_paper_session") is not True
                ):
                    continue
                timestamp = _timestamp(observed["observed_at"])
                day = timestamp.date()
                if timestamp > now or (before_date is not None and day >= before_date):
                    continue
                if observed.get("as_of_date") != str(day) or path.parent.name != str(day):
                    continue
                run_id = observed["decision"]["run_id"]
                verified = evaluate_observation(connection, data_dir, timestamp, run_id=run_id)
                if not verified["eligible_paper_session"] or verified != observed:
                    continue
                for version in verified["decision"]["strategy_versions"]:
                    sessions[(version, day)] = {
                        "strategy_version": version,
                        "as_of_date": day,
                        "run_id": run_id,
                    }
            except (OSError, ValueError, KeyError, TypeError, AttributeError, duckdb.Error):
                continue
    return tuple(sessions[key] for key in sorted(sessions))


def paper_session_summary(database, minimum_sessions: int = 20) -> dict:
    if minimum_sessions <= 0:
        raise ValueError("minimum_sessions must be positive")
    accepted = valid_forward_sessions(database)
    by_version = {}
    for row in accepted:
        by_version.setdefault(row["strategy_version"], set()).add(row["as_of_date"])
    rows = [
        {
            "strategy_version": version,
            "sessions": len(days),
            "first_date": min(days),
            "latest_date": max(days),
        }
        for version, days in sorted(by_version.items())
    ]
    failures = []
    if not rows:
        failures.append("no_valid_paper_sessions")
    else:
        freeze = _load_freeze(database.path.resolve().parent)
        if any(
            len(by_version.get(version, ())) < minimum_sessions
            for version in freeze["strategy_versions"]
        ):
            failures.append(f"paper_sessions_below_{minimum_sessions}")
    open_orders = int(
        database.query_df(
            "SELECT count(*) AS count FROM paper_orders "
            "WHERE status NOT IN ('filled','cancelled','rejected','expired')"
        ).iloc[0]["count"]
    )
    mismatches = int(
        database.query_df(
            "SELECT count(*) AS count FROM (SELECT status FROM paper_reconciliations "
            "QUALIFY row_number() OVER(PARTITION BY order_id "
            "ORDER BY checked_at DESC,reconciliation_id DESC)=1) "
            "WHERE status<>'matched'"
        ).iloc[0]["count"]
    )
    if open_orders:
        failures.append("paper_orders_unresolved")
    if mismatches:
        failures.append("paper_reconciliation_mismatch")
    return {
        "minimum_sessions": minimum_sessions,
        "sessions": rows,
        "open_paper_orders": open_orders,
        "reconciliation_mismatches": mismatches,
        "ready_for_reference": not failures,
        "failures": failures,
    }
