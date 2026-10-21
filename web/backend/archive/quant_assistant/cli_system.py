"""Command-line entry point for local daily operations."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import time

from quant_assistant.config import Settings
from quant_assistant.controllers.server import serve_local_api
from quant_assistant.deployment import DeploymentConfig, serve_production
from quant_assistant.models.repositories.database import Database
from quant_assistant.services.production_readiness import build_report as build_production_report
from quant_assistant.services.roadmap_features import (
    SchedulerWatchdogService,
)

MARKET_DATA_READY_AT = time(16, 0)


def _serve_api(args: argparse.Namespace) -> int:
    try:
        serve_local_api(port=args.port)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"本地 API 启动失败：{error}", file=sys.stderr)
        return 1
    return 0


def _serve_production(args: argparse.Namespace) -> int:
    try:
        config = DeploymentConfig.from_env()
        if args.check_config:
            print(
                f"部署配置有效：host={config.host} port={config.port} "
                f"auth={'enabled' if config.require_auth else 'loopback-only'} "
                f"rate_limit={config.rate_limit_per_minute}/min data_dir={config.data_dir}"
            )
            return 0
        serve_production(config)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"生产 API 启动失败：{error}", file=sys.stderr)
        return 1
    return 0


def _production_check(args: argparse.Namespace) -> int:
    try:
        settings = Settings.from_env()
        database = Database(settings.database_path)
        database.initialize()
        report = build_production_report(
            database,
            minimum_paper_sessions=args.minimum_paper_sessions,
            backup_dir=args.backup_dir or settings.data_dir / "backups",
            minimum_backups=args.minimum_backups,
        )
        text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"生产就绪检查失败：{error}", file=sys.stderr)
        return 1
    return 0 if report["ready_for_deployment"] else 2


def _watchdog_scan(args: argparse.Namespace) -> int:
    del args
    try:
        settings = Settings.from_env()
        database = Database(settings.database_path)
        database.initialize()
        rows = SchedulerWatchdogService(database).scan()
        print(json.dumps(list(rows), ensure_ascii=False, indent=2, default=str))
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"任务看门狗失败：{error}", file=sys.stderr)
        return 1
    return 0 if all(bool(row["healthy"]) for row in rows) else 2


def _print_research_sync(result: object | None) -> None:
    """Print optional research evidence collected by the live data pipeline."""

    if result is None:
        return
    for item in getattr(result, "datasets", ()):
        issues = (
            "; ".join(
                (
                    *tuple(issue.message for issue in item.issues),
                    *tuple(getattr(item, "coverage_blockers", ()) or ()),
                )
            )
            or "ok"
        )
        snapshot = item.snapshot_id if item.snapshot_id is not None else "—"
        print(
            f"{item.dataset}: status={item.status.value} rows={item.row_count} "
            f"snapshot={snapshot} {issues}"
        )
    if not bool(getattr(result, "ready", False)):
        print("研究证据未全部通过门禁；相关 Smart DCA/多因子结果继续失败关闭。", file=sys.stderr)
