"""Package-level production readiness checks usable from the installed CLI."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from quant_assistant.brokers import BrokerUnavailable, DisabledBrokerAdapter
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.deployment import DeploymentConfig
from quant_assistant.models.repositories.database import Database
from quant_assistant.portfolio.drawdown_control import validate_drawdown
from quant_assistant.security_scan import scan_tracked_files_for_secrets
from quant_assistant.services.account_audit import audit_account
from quant_assistant.services.forward_sessions import paper_session_summary, site_root
from quant_assistant.services.readiness_client import live_readiness
from quant_assistant.services.site_backup_evidence import verify_site_archive

_PLACEHOLDER_PREFIXES = ("replace_", "placeholder", "your_")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-paper-sessions", type=int, default=20)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--minimum-backups", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--api-url", help="loopback API used when the local database is locked")
    parser.add_argument(
        "--username", help="account for interactive live API login when the database is locked"
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    database = Database(settings.database_path)
    backup_dir = args.backup_dir or settings.data_dir / "backups"
    try:
        database.initialize()
    except duckdb.IOException as exc:
        if not any(
            marker in str(exc)
            for marker in ("File is already open", "Conflicting lock", "Could not set lock")
        ):
            raise
        try:
            report = live_readiness(
                settings,
                api_url=args.api_url,
                backup_dir=backup_dir,
                minimum_paper_sessions=args.minimum_paper_sessions,
                minimum_backups=args.minimum_backups,
                username=args.username,
            )
        except Exception:
            report = {
                "generated_at": datetime.now(SHANGHAI_TZ).isoformat(),
                "ready_for_deployment": False,
                "checks": {
                    "database_access": {
                        "passed": False,
                        "status": "unavailable",
                        "blockers": ["database_in_use_and_live_readiness_unavailable"],
                    }
                },
            }
    else:
        try:
            report = build_report(
                database,
                minimum_paper_sessions=args.minimum_paper_sessions,
                backup_dir=backup_dir,
                minimum_backups=args.minimum_backups,
            )
        finally:
            database.close()
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ready_for_deployment"] else 2


def build_report(
    database: Database,
    *,
    minimum_paper_sessions: int = 20,
    backup_dir: Path | None = None,
    minimum_backups: int = 1,
) -> dict[str, object]:
    if minimum_paper_sessions <= 0:
        raise ValueError("minimum_paper_sessions must be positive")
    if minimum_backups <= 0:
        raise ValueError("minimum_backups must be positive")
    checks = {
        "account_baseline": _account_check(database),
        "broker_fee_evidence": _fee_evidence_check(database),
        "point_in_time_data": _point_in_time_check(database),
        "strategy_acceptance": _backtest_check(database),
        "drawdown_control": _drawdown_check(database),
        "paper_sessions": _paper_check(database, minimum_paper_sessions),
        "operations_backup_restore": _operations_check(
            backup_dir, minimum_backups, site_directory=site_root(database.path.parent)
        ),
        "broker_safety_boundary": _broker_boundary_check(),
        "deployment_security": _deployment_check(),
    }
    return {
        "generated_at": datetime.now(SHANGHAI_TZ).isoformat(),
        "ready_for_deployment": all(item["passed"] for item in checks.values()),
        "checks": checks,
    }


def _account_check(database: Database) -> dict[str, object]:
    try:
        report = audit_account(database)
    except Exception as exc:
        return {"passed": False, "status": "error", "blockers": [type(exc).__name__]}
    return {
        "passed": report.ready,
        "status": "passed" if report.ready else "blocked",
        "as_of_date": report.as_of_date,
        "position_count": report.position_count,
        "total_value": str(report.total_value),
        "cash_value": str(report.cash_value),
        "blockers": list(report.blockers),
    }


def _fee_evidence_check(database: Database) -> dict[str, object]:
    try:
        frame = database.query_df(
            """
            SELECT run_id, parameters_json, limitations_json
            FROM backtest_runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC NULLS LAST, run_id DESC LIMIT 1
            """
        )
        if frame.empty:
            return {
                "passed": False,
                "status": "blocked",
                "blockers": ["no_completed_backtest_for_fee_evidence"],
            }
        row = frame.iloc[0]
        parameters = _json_object(row.get("parameters_json"))
        costs = parameters.get("costs")
        limitations = _json_list(row.get("limitations_json"))
        blockers: list[str] = []
        required = ("broker_name", "evidence_id", "source_document", "verified_on")
        if not isinstance(costs, Mapping):
            blockers.append("fee_costs_missing")
            evidence: Mapping[str, object] = {}
        else:
            evidence = costs
        for field in required:
            value = str(evidence.get(field, "")).strip()
            if (
                not value
                or value.lower() == "none"
                or value.lower().startswith(_PLACEHOLDER_PREFIXES)
            ):
                blockers.append(f"fee_{field}_missing")
        if "unverified_broker_fee_assumptions" in limitations:
            blockers.append("unverified_broker_fee_assumptions")
        return {
            "passed": not blockers,
            "status": "passed" if not blockers else "blocked",
            "run_id": str(row.get("run_id")),
            "broker_name": str(evidence.get("broker_name", "")),
            "evidence_id": str(evidence.get("evidence_id", "")),
            "verified_on": str(evidence.get("verified_on", "")),
            "blockers": list(dict.fromkeys(blockers)),
        }
    except Exception as exc:
        return {"passed": False, "status": "error", "blockers": [type(exc).__name__]}


def _point_in_time_check(database: Database) -> dict[str, object]:
    try:
        latest = database.query_df(
            "SELECT max(as_of_date) AS latest_date FROM data_snapshots"
        ).iloc[0]["latest_date"]
        if latest is None or bool(pd.isna(latest)):
            raise ValueError("database contains no data snapshots")
        cutoff = latest.date() if hasattr(latest, "date") else latest
        snapshots = database.query_df(
            """
            SELECT dataset, sum(CASE WHEN status = 'valid' THEN 1 ELSE 0 END) AS valid_count
            FROM data_snapshots WHERE as_of_date <= ? GROUP BY dataset
            """,
            [cutoff],
        )
        required = {"instruments", "daily_prices", "index_history"}
        available = set(snapshots["dataset"].astype(str))
        blockers: list[str] = [
            "missing_required_snapshots:" + ",".join(sorted(required - available))
        ]
        blockers = [item for item in blockers if not item.endswith(":")]
        invalid = snapshots.loc[
            snapshots["dataset"].astype(str).isin(required)
            & snapshots["valid_count"].fillna(0).astype(int).eq(0),
            "dataset",
        ]
        if not invalid.empty:
            blockers.append("required_snapshots_not_valid:" + ",".join(sorted(invalid.astype(str))))
        history = database.query_df(
            """
            SELECT count(DISTINCT as_of_date) AS dates
            FROM instrument_history WHERE as_of_date <= ?
            """,
            [cutoff],
        ).iloc[0]["dates"]
        if int(history or 0) < 2:
            blockers.append("instrument_history_has_fewer_than_two_dates")
        coverage = database.query_df(
            """
            SELECT count(*) AS missing
            FROM (
                SELECT DISTINCT p.trade_date
                FROM daily_prices p
                LEFT JOIN (
                    SELECT DISTINCT as_of_date FROM instrument_history WHERE as_of_date <= ?
                ) h ON h.as_of_date = p.trade_date
                WHERE p.trade_date <= ? AND h.as_of_date IS NULL
            )
            """,
            [cutoff, cutoff],
        ).iloc[0]["missing"]
        prices = database.query_df(
            """
            SELECT count(*) AS rows, count(suspended) AS suspended,
                   count(upper_limit) AS upper_limit, count(lower_limit) AS lower_limit
            FROM daily_prices p LEFT JOIN instruments i ON i.symbol = p.symbol
            WHERE trade_date <= ? AND (i.asset_type = 'stock' OR i.asset_type IS NULL)
            """,
            [cutoff],
        ).iloc[0]
        if int(coverage or 0) > 0:
            blockers.append("historical_universe_reconstructed_from_current_snapshot")
        rows = int(prices["rows"] or 0)
        if int(prices["suspended"] or 0) < rows:
            blockers.append("missing_historical_suspension_flags")
        if int(prices["upper_limit"] or 0) < rows or int(prices["lower_limit"] or 0) < rows:
            blockers.append("estimated_historical_price_limits")
    except Exception as exc:
        return {"passed": False, "status": "error", "blockers": [type(exc).__name__]}
    blockers = list(dict.fromkeys(blockers))
    return {
        "passed": not blockers,
        "status": "passed" if not blockers else "blocked",
        "as_of": cutoff,
        "blockers": blockers,
    }


def _backtest_check(database: Database) -> dict[str, object]:
    try:
        frame = database.query_df(
            """
            SELECT run_id, status, readiness, metrics_json, limitations_json
            FROM backtest_runs
            ORDER BY completed_at DESC NULLS LAST, run_id DESC LIMIT 1
            """
        )
        if frame.empty:
            return {"passed": False, "status": "blocked", "blockers": ["no_backtest_run"]}
        row = frame.iloc[0]
        limitations = _json_list(row.get("limitations_json"))
        metrics = _json_object(row.get("metrics_json"))
        qualification_failures = [
            str(item).removeprefix("qualification:")
            for item in limitations
            if str(item).startswith("qualification:")
        ]
        recorded_failures = metrics.get("qualification_failures", [])
        if isinstance(recorded_failures, list):
            qualification_failures.extend(str(item) for item in recorded_failures)
        blockers: list[str] = []
        if str(row.get("status")) != "completed":
            blockers.append("latest_backtest_not_completed")
        if str(row.get("readiness")) == "research_only":
            blockers.append("backtest_research_only")
        if metrics.get("qualification_passed") is not True:
            blockers.append("strategy_qualification_not_passed")
        blockers.extend(f"qualification:{item}" for item in qualification_failures)
        return {
            "passed": not blockers,
            "status": "passed" if not blockers else "blocked",
            "run_id": str(row.get("run_id")),
            "readiness": str(row.get("readiness")),
            "blockers": list(dict.fromkeys(blockers)),
            "qualification_failures": list(dict.fromkeys(qualification_failures)),
        }
    except Exception as exc:
        return {"passed": False, "status": "error", "blockers": [type(exc).__name__]}


def _paper_check(database: Database, minimum_sessions: int) -> dict[str, object]:
    try:
        summary = paper_session_summary(database, minimum_sessions)
        return {
            "passed": summary["ready_for_reference"],
            "status": "passed" if summary["ready_for_reference"] else "blocked",
            "sessions": summary["sessions"],
            "open_paper_orders": summary["open_paper_orders"],
            "reconciliation_mismatches": summary["reconciliation_mismatches"],
            "blockers": summary["failures"],
        }
    except Exception as exc:
        return {"passed": False, "status": "error", "blockers": [type(exc).__name__]}


def _drawdown_check(database: Database) -> dict[str, object]:
    """Require a persisted NAV drawdown validation before production readiness."""

    try:
        frame = database.query_df(
            """
            SELECT run_id, threshold, max_drawdown, observed_sessions, breach_count,
                   passed, research_only, reasons_json, validated_at
            FROM drawdown_validations
            ORDER BY validated_at DESC, run_id DESC LIMIT 1
            """
        )
        if frame.empty:
            return {
                "passed": False,
                "status": "blocked",
                "blockers": ["drawdown_validation_missing"],
            }
        row = frame.iloc[0]
        reasons = _json_list(row.get("reasons_json"))
        blockers: list[str] = []
        if int(row.get("observed_sessions", 0) or 0) < 120:
            blockers.append("drawdown_nav_sessions_insufficient")
        acceptance = _backtest_check(database)
        if not acceptance["passed"] or acceptance.get("run_id") != str(row["run_id"]):
            blockers.append("drawdown_qualified_backtest_mismatch")
        nav = database.query_df(
            "SELECT as_of_date, unit_nav FROM backtest_nav WHERE run_id = ? ORDER BY as_of_date",
            [str(row["run_id"])],
        )
        # Recompute from the immutable backtest series; API-submitted summaries
        # cannot serve as production evidence by themselves.
        verified = validate_drawdown(str(row["run_id"]), nav, threshold=0.20)
        if len(nav) < 120 or not verified.passed:
            blockers.append("drawdown_persisted_nav_not_qualified")
        if (
            float(row["threshold"]) != 0.20
            or verified.observed_sessions != int(row["observed_sessions"])
            or abs(verified.max_drawdown - float(row["max_drawdown"])) > 1e-10
            or verified.breach_count != int(row["breach_count"])
        ):
            blockers.append("drawdown_evidence_mismatch")
        if not bool(row.get("passed")):
            blockers.extend(str(item) for item in reasons or ["drawdown_control_line_breached"])
        # The report is intentionally marked research_only.  A separate human
        # approval is still required; readiness only records evidence quality.
        return {
            "passed": not blockers,
            "status": "passed" if not blockers else "blocked",
            "run_id": str(row.get("run_id")),
            "threshold": float(row.get("threshold", 0)),
            "max_drawdown": float(row.get("max_drawdown", 0)),
            "observed_sessions": int(row.get("observed_sessions", 0)),
            "breach_count": int(row.get("breach_count", 0)),
            "research_only": bool(row.get("research_only", True)),
            "blockers": list(dict.fromkeys(blockers)),
        }
    except Exception as exc:
        return {"passed": False, "status": "error", "blockers": [type(exc).__name__]}


def _operations_check(
    backup_dir: Path | None, minimum_backups: int, *, site_directory: Path | None = None
) -> dict[str, object]:
    directory = None if backup_dir is None else Path(backup_dir).expanduser().resolve()
    archives = (
        ()
        if directory is None or not directory.is_dir()
        else tuple(sorted(directory.glob("*.zip"), key=lambda path: path.stat().st_mtime))
    )
    metadata = [verify_site_archive(path, site_root=site_directory) for path in archives]
    count = sum(bool(item["verified"]) for item in metadata)
    blockers = [] if count >= minimum_backups else [f"backups_below_{minimum_backups}"]
    latest = archives[-1] if archives else None
    restore_verified = bool(metadata and metadata[-1]["verified"])
    if latest is not None and not restore_verified:
        blockers.append("latest_full_site_backup_restore_failed")
    project_root, deployment_root = _code_roots()
    required_scripts = (
        project_root / "scripts" / "start_web.py",
        project_root / "scripts" / "serve_advisor_web.py",
        project_root / "scripts" / "run_market_advisor.py",
        project_root / "scripts" / "healthcheck.py",
        project_root / "scripts" / "production_readiness.py",
        project_root / "src" / "quant_assistant" / "site_backup.py",
        deployment_root / "start.ps1",
        deployment_root / "deploy" / "cloud_up.sh",
    )
    missing_scripts = [path.name for path in required_scripts if not path.is_file()]
    if missing_scripts:
        blockers.append("operations_scripts_missing:" + ",".join(sorted(missing_scripts)))
    return {
        "passed": not blockers,
        "status": "passed" if not blockers else "blocked",
        "directory": directory,
        "archive_count": len(archives),
        "verified_site_archive_count": count,
        "coverage": metadata[-1] if metadata else None,
        "latest_backup": None if latest is None else latest.name,
        "restore_verified": restore_verified,
        "blockers": list(dict.fromkeys(blockers)),
    }


def _broker_boundary_check() -> dict[str, object]:
    adapter = DisabledBrokerAdapter()
    blockers: list[str] = []
    try:
        adapter.fetch_account_snapshot()
    except BrokerUnavailable:
        pass
    else:
        blockers.append("disabled_broker_account_fetch_did_not_fail")
    try:
        adapter.submit_order({"symbol": "CHECK", "action": "buy"})
    except BrokerUnavailable:
        pass
    else:
        blockers.append("disabled_broker_order_submission_did_not_fail")
    return {
        "passed": not blockers,
        "status": "passed" if not blockers else "blocked",
        "account_source": "validated_csv_export",
        "automatic_order_submission": False,
        "manual_confirmation_required": True,
        "blockers": blockers,
    }


def _deployment_check() -> dict[str, object]:
    blockers: list[str] = []
    try:
        config = DeploymentConfig.from_env()
    except Exception as exc:
        config = None
        blockers.append(type(exc).__name__)
    _, project_root = _code_roots()
    required_files = (
        project_root / "Dockerfile",
        project_root / ".dockerignore",
        project_root / "deploy" / "docker-compose.yml",
        project_root / "deploy" / "Caddyfile",
        project_root / "deploy" / "README.md",
    )
    missing = [
        path.relative_to(project_root).as_posix() for path in required_files if not path.is_file()
    ]
    if missing:
        blockers.append("deployment_files_missing:" + ",".join(missing))
    findings = scan_tracked_files_for_secrets(project_root)
    if findings:
        blockers.append("potential_secret_detected")
    return {
        "passed": not blockers,
        "status": "passed" if not blockers else "blocked",
        "host": None if config is None else config.host,
        "port": None if config is None else config.port,
        "authentication": None if config is None else config.require_auth,
        "rate_limit_per_minute": (None if config is None else config.rate_limit_per_minute),
        "findings": findings,
        "blockers": blockers,
    }


def _code_roots() -> tuple[Path, Path]:
    """Locate source/deployment files independently of the retained data directory."""
    current = Path.cwd().resolve()
    backend = current / "web" / "backend"
    if (backend / "pyproject.toml").is_file():
        return backend, current / "web"
    if current.name == "backend" and (
        (current.parent / "frontend").is_dir() or (current.parent / "Dockerfile").is_file()
    ):
        return current, current.parent
    return current, current


def _json_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return ["invalid_limitations_json"]
    else:
        parsed = value
    return list(parsed) if isinstance(parsed, list) else ["invalid_limitations_json"]


def _json_object(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
    else:
        parsed = value
    return dict(parsed) if isinstance(parsed, Mapping) else {}


__all__ = ["build_report", "main"]
