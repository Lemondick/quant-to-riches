"""Command-line entry point for local daily operations."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, time, timedelta
from decimal import Decimal

from quant_assistant.backup import create_backup, restore_backup
from quant_assistant.brokers.csv import CsvBrokerAdapter
from quant_assistant.config import Settings
from quant_assistant.models.entities import AssetType
from quant_assistant.models.repositories.database import Database
from quant_assistant.services.account_audit import audit_account
from quant_assistant.services.account_snapshot import parse_account_snapshot, snapshot_total
from quant_assistant.services.composition import build_local_services
from quant_assistant.services.notifications import (
    NotificationStatus,
    format_daily_order_digest,
    notifier_from_env,
    send_optional_notification,
)
from quant_assistant.services.portfolio_commands import PositionInput
from quant_assistant.services.roadmap_features import (
    CashFlowPlanService,
)

MARKET_DATA_READY_AT = time(16, 0)


def _notify_daily(args: argparse.Namespace) -> int:
    """Push a persisted decision digest; never invent a decision when absent."""

    settings = Settings.from_env()
    try:
        services = build_local_services(settings)
        decision = (
            services.decision_queries.decision_by_id(args.run_id)
            if args.run_id
            else services.decision_queries.latest_decision()
        )
        if decision is None:
            print("没有可推送的已持久化每日决策。", file=sys.stderr)
            return 2
        notifier = notifier_from_env()
        result = send_optional_notification(
            notifier,
            format_daily_order_digest(decision),
            title="量化助手每日清单",
        )
    except (
        AttributeError,
        ConnectionError,
        FileNotFoundError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        if args.debug:
            raise
        print(f"每日清单推送失败：{error}", file=sys.stderr)
        return 1
    print(
        f"notification: channel={result.channel} status={result.status.value} "
        f"message={result.message}"
    )
    notification_key = "notification:operator:daily"
    operational_alerts = getattr(services, "operational_alerts", None)
    if operational_alerts is not None:
        if result.status is NotificationStatus.FAILED:
            operational_alerts.raise_alert(
                notification_key,
                "warning",
                "每日决策通知失败",
                {"channel": result.channel},
            )
        else:
            operational_alerts.resolve(notification_key)
    return 1 if result.status is NotificationStatus.FAILED else 0


def _backup(args: argparse.Namespace) -> int:
    try:
        manifest = create_backup(Settings.from_env(), args.output)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"备份失败：{error}", file=sys.stderr)
        return 1
    print(f"备份完成：{args.output}，文件数={len(manifest.files)}")
    return 0


def _restore(args: argparse.Namespace) -> int:
    try:
        manifest = restore_backup(args.input, args.target, force=bool(args.force))
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"恢复失败：{error}", file=sys.stderr)
        return 1
    print(f"恢复完成：{args.target}，文件数={len(manifest.files)}")
    return 0


def _validate_account(args: argparse.Namespace) -> int:
    try:
        rows = parse_account_snapshot(args.input.read_text(encoding="utf-8"), args.as_of)
        total = snapshot_total(rows)
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(f"账户快照校验失败：{error}", file=sys.stderr)
        return 2
    print(f"账户快照有效：日期={args.as_of.isoformat()} 持仓数={len(rows)} 总市值={total}")
    print("校验不会写入数据库；请在仪表盘‘首次建档’或‘完整账户对账’中人工确认后保存。")
    return 0


def _import_account(args: argparse.Namespace) -> int:
    try:
        adapter = CsvBrokerAdapter(
            args.input,
            broker_name=args.broker_name,
            account_id=args.account_id,
            as_of_date=args.as_of,
        )
        snapshot = adapter.fetch_account_snapshot()
        positions = tuple(
            PositionInput(
                symbol=str(row["symbol"]),
                asset_type=AssetType(str(row["asset_type"])),
                quantity=Decimal(str(row["quantity"])),
                average_cost=Decimal(str(row["average_cost"])),
                market_value=Decimal(str(row["market_value"])),
                as_of_date=args.as_of,
            )
            for row in snapshot.positions
        )
        event_id = args.event_id or f"account-import:{args.broker_name}:{args.as_of.isoformat()}"
        if not args.confirm:
            total_value = sum((item.market_value for item in positions), Decimal("0"))
            print(
                f"账户快照有效：券商={args.broker_name} 日期={args.as_of.isoformat()} "
                f"持仓数={len(positions)} 总市值={total_value}"
            )
            print("写入被阻断：请再次提供 --confirm；当前未修改数据库。", file=sys.stderr)
            return 2
        services = build_local_services(Settings.from_env())
        services.portfolio_commands.import_initial_account(
            event_id,
            args.as_of,
            positions,
            reason=args.reason,
        )
    except FileNotFoundError:
        print(f"账户快照文件不存在：{args.input}", file=sys.stderr)
        return 1
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"账户快照导入失败：{error}", file=sys.stderr)
        return 2
    print(
        f"账户快照已导入：券商={args.broker_name} 日期={args.as_of.isoformat()} "
        f"持仓数={len(positions)} event_id={event_id}；下一步请运行 run-daily 写入首个NAV。"
    )
    return 0


def _audit_account(args: argparse.Namespace) -> int:
    try:
        database = Database(Settings.from_env().database_path)
        database.initialize()
        report = audit_account(database, args.as_of)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"账户对账审计失败：{error}", file=sys.stderr)
        return 1
    print(
        f"账户审计：status={'ready' if report.ready else 'blocked'} "
        f"date={report.as_of_date.isoformat() if report.as_of_date else '—'} "
        f"positions={report.position_count} total={report.total_value} cash={report.cash_value}"
    )
    for blocker in report.blockers:
        print(f"阻断：{blocker}", file=sys.stderr)
    return 0 if report.ready else 2


def _cash_flow_plan(args: argparse.Namespace) -> int:
    try:
        settings = Settings.from_env()
        database = Database(settings.database_path)
        database.initialize()
        service = CashFlowPlanService(database)
        if args.action == "list":
            rows = service.list(status=args.status, limit=args.limit)
            print(
                json.dumps(
                    [
                        {
                            "plan_id": item.plan_id,
                            "name": item.name,
                            "direction": item.direction,
                            "amount": str(item.amount),
                            "frequency": item.frequency,
                            "start_date": item.start_date.isoformat(),
                            "end_date": None
                            if item.end_date is None
                            else item.end_date.isoformat(),
                            "status": item.status,
                            "next_dates": [
                                value.isoformat()
                                for value in item.schedule(
                                    until=date.today() + timedelta(days=365)
                                )[:12]
                            ],
                        }
                        for item in rows
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        item = service.create(
            args.name,
            args.direction,
            args.amount,
            args.frequency,
            args.start,
            end_date=args.end,
            note=args.note,
        )
        print(f"现金流计划已保存：{item.plan_id} {item.name} {item.direction} {item.amount}")
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"现金流计划失败：{error}", file=sys.stderr)
        return 1
    return 0
