"""Command-line entry point for local daily operations."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.data.history import initialize_market_history
from quant_assistant.data.pipeline import run_daily_data_pipeline
from quant_assistant.data.point_in_time import (
    audit_point_in_time_coverage,
    import_point_in_time_prices,
    import_point_in_time_universe,
    load_point_in_time_prices,
)
from quant_assistant.data.research import sync_research_evidence
from quant_assistant.models.entities import DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.base import ProviderError
from quant_assistant.providers.fund_detail import PublicFundDetailProvider
from quant_assistant.services.composition import build_market_data_provider
from quant_assistant.services.fund_lifecycle import sync_fund_lifecycle
from quant_assistant.services.fund_style_drift import sync_fund_style

from .cli_commands import _print_research_sync

MARKET_DATA_READY_AT = time(16, 0)


def _public_market_provider():
    """Build the bounded provider wrapper used by all live-data commands."""
    return build_market_data_provider()


def _update_data(args: argparse.Namespace) -> int:
    as_of_date = args.date or latest_completed_market_day()
    try:
        result = run_daily_data_pipeline(Settings.from_env(), _public_market_provider(), as_of_date)
    except ProviderError as error:
        if args.debug:
            raise
        print(f"数据源错误：{error}", file=sys.stderr)
        return 1

    for dataset, item in result.datasets.items():
        issue_text = "; ".join(issue.message for issue in item.issues) or "ok"
        print(
            f"{dataset}: status={item.status.value} rows={item.row_count} "
            f"snapshot={item.snapshot_id} {issue_text}"
        )
    _print_research_sync(getattr(result, "research", None))
    if result.active_source:
        print(f"market_source: {result.active_source}")
    if not result.tradable:
        print("数据未通过交易门槛：本次只可查看，不得生成新增风险建议。", file=sys.stderr)
        return 2
    print(f"{as_of_date.isoformat()} 数据更新完成，交易数据门槛已通过。")
    return 0


def _import_point_in_time(args: argparse.Namespace) -> int:
    try:
        result = import_point_in_time_prices(Settings.from_env(), args.input)
    except FileNotFoundError:
        print(f"点时行情文件不存在：{args.input}", file=sys.stderr)
        return 1
    except (OSError, TypeError, ValueError) as error:
        print(f"点时行情导入失败：{error}", file=sys.stderr)
        return 2
    for issue in result.issues:
        print(f"限制：{issue.code} {issue.message}", file=sys.stderr)
    print(
        f"点时行情导入：status={result.status.value} rows={result.row_count} "
        f"as_of={result.as_of_date.isoformat()} snapshot={result.snapshot_id}"
    )
    return 0 if result.status is DataStatus.VALID else 2


def _import_point_in_time_universe(args: argparse.Namespace) -> int:
    try:
        result = import_point_in_time_universe(Settings.from_env(), args.input)
    except FileNotFoundError:
        print(f"点时股票池文件不存在：{args.input}", file=sys.stderr)
        return 1
    except (OSError, TypeError, ValueError) as error:
        print(f"点时股票池导入失败：{error}", file=sys.stderr)
        return 2
    for issue in result.issues:
        print(f"限制：{issue.code} {issue.message}", file=sys.stderr)
    print(
        f"点时股票池导入：status={result.status.value} rows={result.row_count} "
        f"as_of={result.as_of_date.isoformat()} snapshot={result.snapshot_id}"
    )
    return 0 if result.status is DataStatus.VALID else 2


def _audit_point_in_time(args: argparse.Namespace) -> int:
    try:
        frame = load_point_in_time_prices(args.input)
        report = audit_point_in_time_coverage(frame, expected_dates=tuple(args.expected_date))
    except FileNotFoundError:
        print(f"点时行情文件不存在：{args.input}", file=sys.stderr)
        return 1
    except (OSError, TypeError, ValueError) as error:
        print(f"点时行情审计失败：{error}", file=sys.stderr)
        return 2
    print(
        f"点时行情审计：status={report.status.value} rows={report.row_count} "
        f"symbols={report.symbol_count} dates={report.date_count} "
        f"missing_suspended={report.missing_suspension_rows} "
        f"invalid_limits={report.invalid_limit_rows}"
    )
    for issue in report.issues:
        print(f"限制：{issue}", file=sys.stderr)
    return 0 if report.status is DataStatus.VALID else 2


def _sync_research(args: argparse.Namespace) -> int:
    end_date = args.end or latest_completed_market_day()
    start_date = args.start or (end_date - timedelta(days=365))
    settings = Settings.from_env()
    fund_symbols = tuple(args.fund_symbol)
    if not fund_symbols:
        database = Database(settings.database_path)
        database.initialize()
        tracked = database.query_df(
            """
            SELECT symbol FROM positions WHERE asset_type = 'fund'
            UNION
            SELECT symbol FROM fund_share_families
            ORDER BY symbol
            """
        )
        if not tracked.empty:
            fund_symbols = tuple(tracked["symbol"].astype(str).tolist())
    try:
        result = sync_research_evidence(
            settings,
            _public_market_provider(),
            start_date,
            end_date,
            symbols=tuple(args.symbol),
            fund_symbols=fund_symbols,
        )
    except (ProviderError, OSError, TypeError, ValueError) as error:
        if args.debug:
            raise
        print(f"研究证据同步失败：{error}", file=sys.stderr)
        return 1
    for item in result.datasets:
        issue_text = (
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
            f"snapshot={snapshot} {issue_text}"
        )
    if not result.ready:
        print("研究证据未全部通过门禁；Smart DCA/多因子将继续失败关闭。", file=sys.stderr)
        return 2
    print(f"{end_date.isoformat()} 研究证据同步完成。")
    return 0


def _sync_fund_lifecycle(args: argparse.Namespace) -> int:
    """Fetch tracked public fund profiles and persist lifecycle changes."""

    as_of_date = args.date or latest_completed_market_day()
    settings = Settings.from_env()
    symbols = tuple(args.fund_symbol)
    if not symbols:
        database = Database(settings.database_path)
        database.initialize()
        tracked = database.query_df(
            """
            SELECT symbol FROM positions WHERE asset_type = 'fund'
            UNION
            SELECT symbol FROM fund_share_families
            ORDER BY symbol
            """
        )
        if not tracked.empty:
            symbols = tuple(tracked["symbol"].astype(str).tolist())
    if not symbols:
        print("未找到要同步的基金持仓或基金池代码。", file=sys.stderr)
        return 2
    try:
        result = sync_fund_lifecycle(
            settings,
            PublicFundDetailProvider(),
            as_of_date,
            symbols,
        )
    except (ProviderError, OSError, TypeError, ValueError) as error:
        if args.debug:
            raise
        print(f"基金生命周期同步失败：{error}", file=sys.stderr)
        return 1
    for item in result.items:
        detail = item.error or f"新增事件={item.event_count}"
        print(f"{item.symbol}: status={item.status.value} {detail}")
    if not result.ready:
        print("基金生命周期证据未全部同步；页面仅显示已落盘事件。", file=sys.stderr)
        return 2
    print(f"{as_of_date.isoformat()} 基金生命周期证据同步完成。")
    return 0


def _sync_fund_style(args: argparse.Namespace) -> int:
    """Fetch disclosed holdings and persist report-period style drift evidence."""

    as_of_date = args.date or latest_completed_market_day()
    settings = Settings.from_env()
    symbols = tuple(args.fund_symbol)
    if not symbols:
        database = Database(settings.database_path)
        database.initialize()
        tracked = database.query_df(
            """
            SELECT symbol FROM positions WHERE asset_type = 'fund'
            UNION
            SELECT symbol FROM fund_share_families
            ORDER BY symbol
            """
        )
        if not tracked.empty:
            symbols = tuple(tracked["symbol"].astype(str).tolist())
    if not symbols:
        print("未找到要同步的基金持仓或基金池代码。", file=sys.stderr)
        return 2
    try:
        result = sync_fund_style(
            settings,
            PublicFundDetailProvider(),
            as_of_date,
            symbols,
        )
    except (ProviderError, OSError, TypeError, ValueError) as error:
        if args.debug:
            raise
        print(f"基金持仓风格同步失败：{error}", file=sys.stderr)
        return 1
    for item in result.items:
        detail = item.error or f"新增事件={item.event_count}"
        print(f"{item.symbol}: status={item.status.value} {detail}")
    if not result.ready:
        print("基金持仓风格证据未全部同步；结果仅供人工复核。", file=sys.stderr)
        return 2
    print(f"{as_of_date.isoformat()} 基金持仓风格漂移同步完成。")
    return 0


def _init_history(args: argparse.Namespace) -> int:
    end_date = args.end or latest_completed_market_day()
    start_date = args.start or end_date - timedelta(days=550)

    def report_progress(item, completed: int, total: int) -> None:
        if completed == 1 or completed == total or completed % 25 == 0 or item.issues:
            mode = "skip" if item.skipped else item.status.value
            print(
                f"history-progress: {completed}/{total} {item.symbol} rows={item.row_count} {mode}",
                flush=True,
            )

    try:
        result = initialize_market_history(
            Settings.from_env(),
            _public_market_provider(),
            start_date,
            end_date,
            symbols=args.symbol,
            fund_symbols=args.fund_symbol,
            progress=report_progress,
        )
    except (OSError, ProviderError, RuntimeError, TypeError, ValueError) as error:
        if args.debug:
            raise
        print(f"历史数据初始化失败：{error}", file=sys.stderr)
        return 1
    displayed_items = result.items if len(result.items) <= 20 else result.failed
    for item in displayed_items:
        issues = "; ".join(issue.message for issue in item.issues) or "ok"
        mode = "已跳过" if item.skipped else "已抓取"
        print(
            f"{item.dataset}/{item.symbol}: status={item.status.value} "
            f"rows={item.row_count} snapshot={item.snapshot_id} {mode} {issues}"
        )
    if len(result.items) > 20:
        print(
            f"history-summary: total={len(result.items)} "
            f"valid={len(result.items) - len(result.failed)} failed={len(result.failed)}"
        )
    if not result.ready:
        print("历史数据门槛未通过；无效切片未写入标准化行情表。", file=sys.stderr)
        return 2
    print(
        f"历史数据初始化完成：{start_date.isoformat()} 至 {end_date.isoformat()}，可继续执行回测。"
    )
    return 0


def latest_completed_market_day(now: datetime | None = None) -> date:
    """Return a conservative weekday candidate; provider data confirms freshness."""

    current = now.astimezone(SHANGHAI_TZ) if now else datetime.now(SHANGHAI_TZ)
    candidate = current.date()
    if current.time() < MARKET_DATA_READY_AT:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate
