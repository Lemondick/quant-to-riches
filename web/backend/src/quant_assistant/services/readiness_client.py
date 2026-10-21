"""Read a live loopback service's report when it owns the DuckDB file lock."""

from __future__ import annotations

import getpass
import hashlib
import os
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from quant_assistant.config import Settings


def path_identity(path: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(path.resolve())).encode("utf-8")).hexdigest()


def live_readiness(
    settings: Settings,
    *,
    api_url: str | None,
    backup_dir: Path,
    minimum_paper_sessions: int,
    minimum_backups: int,
    username: str | None = None,
) -> dict[str, object]:
    import httpx  # Lazy: only the live-HTTP path needs it.

    base = (api_url or f"http://127.0.0.1:{settings.deployment.port}").rstrip("/")
    address = urlsplit(base)
    if (
        address.scheme != "http"
        or address.hostname not in {"127.0.0.1", "localhost", "::1"}
        or address.username
        or address.password
        or address.path
        or address.query
        or address.fragment
    ):
        raise ValueError("readiness API must be a loopback HTTP origin")
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        if not username:
            raise ValueError("live readiness requires --username and an interactive account login")
        login = client.post(
            base + "/api/v1/auth/login",
            json={"username": username, "password": getpass.getpass("账号密码: ")},
            headers={"Origin": base},
        )
        login.raise_for_status()
        headers = {"Origin": base, "X-CSRF-Token": login.json()["csrf_token"]}
        try:
            response = client.get(
                base + "/api/v1/operations/readiness",
                params={
                    "minimum_paper_sessions": minimum_paper_sessions,
                    "minimum_backups": minimum_backups,
                },
            )
            response.raise_for_status()
            report = response.json()
        finally:
            with suppress(httpx.HTTPError):
                client.post(base + "/api/v1/auth/logout", headers=headers)
    if not isinstance(report, dict):
        raise ValueError("invalid readiness report")
    if report.get("database_identity") != path_identity(settings.database_path):
        raise ValueError("readiness API belongs to a different database")
    if report.get("backup_identity") != path_identity(backup_dir):
        raise ValueError("readiness API uses a different backup directory")
    checks = report.get("checks")
    expected = {
        "account_baseline",
        "broker_fee_evidence",
        "point_in_time_data",
        "strategy_acceptance",
        "drawdown_control",
        "paper_sessions",
        "operations_backup_restore",
        "broker_safety_boundary",
        "deployment_security",
    }
    if (
        not isinstance(checks, dict)
        or set(checks) != expected
        or any(
            not isinstance(item, dict) or type(item.get("passed")) is not bool
            for item in checks.values()
        )
        or type(report.get("ready_for_deployment")) is not bool
        or report["ready_for_deployment"] != all(item["passed"] for item in checks.values())
    ):
        raise ValueError("incomplete or inconsistent readiness report")
    return report
