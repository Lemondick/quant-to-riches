"""Command-line entry point for local daily operations."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

from .cli_commands import (
    _audit_account,
    _audit_point_in_time,
    _backup,
    _benchmark,
    _cash_flow_plan,
    _drawdown_validate,
    _factor_monitor,
    _factor_validation,
    _fund_overlap,
    _import_account,
    _import_point_in_time,
    _import_point_in_time_universe,
    _init_history,
    _macro_allocation,
    _macro_observation,
    _notify_daily,
    _production_check,
    _restore,
    _run_backtest,
    _run_daily,
    _sector_rotation,
    _serve_api,
    _serve_production,
    _sync_fund_lifecycle,
    _sync_fund_style,
    _sync_research,
    _tail_alert,
    _update_data,
    _validate_account,
    _watchdog_scan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quant-assistant",
        description="本地量化投资决策助手（仅生成建议，不连接交易账户）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    update = subparsers.add_parser(
        "update-data", help="抓取、快照、校验并保存一个已完成交易日的数据"
    )
    update.add_argument("--date", type=date.fromisoformat, help="目标日期（YYYY-MM-DD）")
    update.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    history = subparsers.add_parser(
        "init-history", help="断点续传初始化股票、指数和可选基金历史数据"
    )
    history.add_argument("--start", type=date.fromisoformat, help="起始日期（默认目标日前550天）")
    history.add_argument("--end", type=date.fromisoformat, help="截止日期（YYYY-MM-DD）")
    history.add_argument(
        "--symbol", action="append", default=[], help="仅初始化指定A股，可重复提供"
    )
    history.add_argument(
        "--fund-symbol", action="append", default=[], help="同时初始化指定基金，可重复提供"
    )
    history.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    point_in_time = subparsers.add_parser(
        "import-point-in-time", help="导入带停牌与涨跌停证据的历史行情CSV"
    )
    point_in_time.add_argument("--input", type=Path, required=True, help="标准化点时行情CSV")

    point_in_time_universe = subparsers.add_parser(
        "import-point-in-time-universe", help="导入按日期保存的历史证券元数据CSV"
    )
    point_in_time_universe.add_argument(
        "--input", type=Path, required=True, help="标准化点时股票池CSV"
    )

    point_in_time_audit = subparsers.add_parser(
        "audit-point-in-time", help="只读审计停牌与涨跌停历史证据覆盖"
    )
    point_in_time_audit.add_argument("--input", type=Path, required=True, help="标准化点时行情CSV")
    point_in_time_audit.add_argument(
        "--expected-date",
        action="append",
        type=date.fromisoformat,
        default=[],
        help="期望覆盖的交易日（可重复）",
    )

    research = subparsers.add_parser(
        "sync-research",
        help="自动抓取并保存 PE/PB 与基金经理证据（只读外部数据）",
    )
    research.add_argument("--start", type=date.fromisoformat, help="估值历史起始日期")
    research.add_argument("--end", type=date.fromisoformat, help="目标日期（默认最近完成交易日）")
    research.add_argument(
        "--symbol", action="append", default=[], help="指数代码，可重复提供（默认沪深300）"
    )
    research.add_argument(
        "--fund-symbol",
        action="append",
        default=[],
        help="基金代码，可重复提供；默认读取本地持仓/基金池",
    )
    research.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    lifecycle = subparsers.add_parser(
        "sync-fund-lifecycle",
        help="抓取并保存基金经理、规模、申赎状态和费率变化证据",
    )
    lifecycle.add_argument("--date", type=date.fromisoformat, help="观察日期（默认最近完成交易日）")
    lifecycle.add_argument(
        "--fund-symbol",
        action="append",
        default=[],
        help="基金代码，可重复提供；默认读取本地持仓/基金池",
    )
    lifecycle.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    style = subparsers.add_parser(
        "sync-fund-style",
        help="抓取并保存基金披露持仓重叠度与风格漂移证据",
    )
    style.add_argument("--date", type=date.fromisoformat, help="观察日期（默认最近完成交易日）")
    style.add_argument(
        "--fund-symbol",
        action="append",
        default=[],
        help="基金代码，可重复提供；默认读取本地持仓/基金池",
    )
    style.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    backtest = subparsers.add_parser(
        "run-backtest", aliases=["backtest"], help="使用本地数据库运行锁定策略和双成本回测"
    )
    backtest.add_argument("--start", type=date.fromisoformat, help="评估起始日期")
    backtest.add_argument("--end", type=date.fromisoformat, help="评估截止日期")
    backtest.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    backtest.add_argument("--commission-rate", type=Decimal, default=Decimal("0.0003"))
    backtest.add_argument("--minimum-commission", type=Decimal, default=Decimal("5"))
    backtest.add_argument("--stamp-tax-rate", type=Decimal, default=Decimal("0.0005"))
    backtest.add_argument("--transfer-fee-rate", type=Decimal, default=Decimal("0.00001"))
    backtest.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    backtest.add_argument(
        "--fee-verified-on",
        type=date.fromisoformat,
        help="账户费率人工核验日期；未提供则回测保持研究模式",
    )
    backtest.add_argument(
        "--fee-schedule",
        type=Path,
        help="包含券商名称、证据编号、来源文件和费率的JSON；优先于单项费率参数",
    )
    backtest.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    daily = subparsers.add_parser(
        "run-daily",
        aliases=["daily-run"],
        help="更新数据并生成一份需要人工确认的每日建议",
    )
    daily.add_argument("--date", type=date.fromisoformat, help="目标日期（YYYY-MM-DD）")
    daily.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    tail_alert = subparsers.add_parser(
        "tail-alert",
        help="扫描当前基金持仓的14:30盘中估值异动并可选推送通知通道",
    )
    tail_alert.add_argument(
        "--warning-threshold",
        type=Decimal,
        default=None,
        help="预警阈值（估值接口百分比点，默认1.50）",
    )
    tail_alert.add_argument(
        "--critical-threshold",
        type=Decimal,
        default=None,
        help="严重阈值（估值接口百分比点，默认3.00）",
    )
    tail_alert.add_argument(
        "--info-threshold",
        type=Decimal,
        default=None,
        help="提示阈值（可选；留空则保持历史两级告警）",
    )
    tail_alert.add_argument(
        "--silence-minutes",
        type=int,
        default=None,
        help="相同告警的通知静默窗口（分钟，默认30；0表示关闭）",
    )
    tail_alert.add_argument("--no-notify", action="store_true", help="只写入本地预警，不发送通知")
    tail_alert.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    notify_daily = subparsers.add_parser(
        "notify-daily",
        help="将最新每日决策清单推送到已配置的通知通道",
    )
    notify_daily.add_argument("--run-id", help="指定已持久化的决策运行ID；默认读取最新运行")
    notify_daily.add_argument("--debug", action="store_true", help="保留完整异常堆栈")

    sector_rotation = subparsers.add_parser(
        "sector-rotation",
        help="从带sector列的历史行情计算六赛道动量轮动（只读）",
    )
    sector_rotation.add_argument("--input", type=Path, required=True, help="历史行情CSV")
    sector_rotation.add_argument("--as-of", type=date.fromisoformat, required=True)
    sector_rotation.add_argument(
        "--sector", action="append", default=[], help="指定赛道标签，可重复；默认六大赛道"
    )
    sector_rotation.add_argument("--min-observations", type=int, default=200)
    sector_rotation.add_argument("--top-n", type=int, default=2)
    sector_rotation.add_argument("--output", type=Path, help="可选JSON输出路径")

    macro_allocation = subparsers.add_parser(
        "macro-allocation",
        help="从纳指/黄金/纯债历史行情计算宏观抗震配置（只读）",
    )
    macro_allocation.add_argument("--input", type=Path, required=True, help="历史行情CSV")
    macro_allocation.add_argument("--as-of", type=date.fromisoformat, required=True)
    macro_allocation.add_argument(
        "--asset", action="append", default=[], help="指定资产标签，可重复；默认纳指/黄金/纯债"
    )
    macro_allocation.add_argument("--min-observations", type=int, default=200)
    macro_allocation.add_argument("--output", type=Path, help="可选JSON输出路径")

    fund_overlap = subparsers.add_parser(
        "fund-overlap",
        help="按目标日分析基金底层重仓与行业重叠（只读）",
    )
    fund_overlap.add_argument("--input", type=Path, required=True, help="基金持仓CSV")
    fund_overlap.add_argument("--as-of", type=date.fromisoformat, required=True)
    fund_overlap.add_argument(
        "--portfolio-weight",
        action="append",
        default=[],
        metavar="FUND=WEIGHT",
        help="可选组合基金权重（十进制比例），可重复",
    )
    fund_overlap.add_argument("--output", type=Path, help="可选JSON输出路径")

    factor_validation = subparsers.add_parser(
        "factor-validation",
        help="计算横截面因子IC/IR和分层多空回测（只读）",
    )
    factor_validation.add_argument("--input", type=Path, required=True, help="因子观测CSV")
    factor_validation.add_argument("--layers", type=int, default=5, help="分层数量（默认5）")
    factor_validation.add_argument(
        "--min-observations", type=int, default=5, help="每期最少截面观测数"
    )
    factor_validation.add_argument("--output", type=Path, help="可选JSON输出路径")

    drawdown = subparsers.add_parser(
        "validate-drawdown", help="用已保存或导入的NAV序列验证独立回撤控制线（研究模式）"
    )
    drawdown.add_argument("--run-id", required=True)
    drawdown.add_argument(
        "--input", type=Path, required=True, help="包含unit_nav或total_value列的CSV"
    )
    drawdown.add_argument("--threshold", type=float, default=0.20)

    cash_flow = subparsers.add_parser("cash-flow-plan", help="创建或列出未来定期投入/提取计划")
    cash_flow.add_argument("action", choices=("create", "list"))
    cash_flow.add_argument("--name", help="计划名称")
    cash_flow.add_argument("--direction", choices=("deposit", "withdrawal"))
    cash_flow.add_argument("--amount", type=Decimal)
    cash_flow.add_argument(
        "--frequency", choices=("once", "weekly", "monthly", "quarterly", "annual")
    )
    cash_flow.add_argument("--start", type=date.fromisoformat)
    cash_flow.add_argument("--end", type=date.fromisoformat)
    cash_flow.add_argument("--note")
    cash_flow.add_argument("--status", choices=("active", "paused", "completed"))
    cash_flow.add_argument("--limit", type=int, default=100)

    factor_monitor = subparsers.add_parser(
        "factor-monitor", help="导入并查看因子IC/IR连续监控与衰减告警"
    )
    factor_monitor.add_argument("--factor", required=True)
    factor_monitor.add_argument("--input", type=Path, required=True)
    factor_monitor.add_argument("--source", default="csv")
    factor_monitor.add_argument("--limit", type=int, default=60)

    benchmark = subparsers.add_parser(
        "benchmark", help="保存自定义组合基准或对历史行情进行基准对比"
    )
    benchmark.add_argument("--name")
    benchmark.add_argument("--component", action="append", default=[], metavar="CODE=WEIGHT")
    benchmark.add_argument("--benchmark-id")
    benchmark.add_argument("--default", action="store_true")
    benchmark.add_argument("--compare")
    benchmark.add_argument("--start", type=date.fromisoformat)
    benchmark.add_argument("--end", type=date.fromisoformat)

    macro_observation = subparsers.add_parser(
        "macro-observation", help="保存利率、PMI或社融的带来源观测"
    )
    macro_observation.add_argument(
        "--indicator", required=True, choices=("interest_rate", "pmi", "social_financing")
    )
    macro_observation.add_argument("--date", type=date.fromisoformat, required=True)
    macro_observation.add_argument("--value", type=float, required=True)
    macro_observation.add_argument("--unit", required=True)
    macro_observation.add_argument("--source", default="manual")

    subparsers.add_parser("watchdog-scan", help="扫描定时任务漏跑和失败状态")

    backup = subparsers.add_parser("backup", help="校验并备份本地数据库和配置")
    backup.add_argument("--output", type=Path, required=True, help="输出 ZIP 文件")

    restore = subparsers.add_parser("restore", help="校验备份并恢复到显式目录")
    restore.add_argument("--input", type=Path, required=True, help="输入 ZIP 文件")
    restore.add_argument("--target", type=Path, required=True, help="恢复目标目录")
    restore.add_argument("--force", action="store_true", help="允许写入非空目标目录")

    account = subparsers.add_parser("validate-account", help="只读校验完整账户CSV，不写入数据库")
    account.add_argument("--input", type=Path, required=True, help="完整账户CSV文件")
    account.add_argument("--as-of", type=date.fromisoformat, required=True, help="账户日期")

    account_import = subparsers.add_parser(
        "import-account",
        help="在明确确认后，将完整券商CSV原子导入并建立初始NAV基线",
    )
    account_import.add_argument("--input", type=Path, required=True, help="完整账户CSV文件")
    account_import.add_argument("--as-of", type=date.fromisoformat, required=True, help="账户日期")
    account_import.add_argument("--reason", required=True, help="账户快照核对原因")
    account_import.add_argument("--event-id", help="幂等导入编号（默认按券商和日期生成）")
    account_import.add_argument("--broker-name", default="broker_csv", help="券商名称标签")
    account_import.add_argument(
        "--account-id",
        default="file-export",
        help="账户标签（不会写入日志）",
    )
    account_import.add_argument(
        "--confirm",
        action="store_true",
        help="确认将快照写入本地账本并建立基线",
    )

    account_audit = subparsers.add_parser("audit-account", help="只读核对本地持仓、现金和NAV基线")
    account_audit.add_argument("--as-of", type=date.fromisoformat, help="核对截止日期")

    api = subparsers.add_parser("serve-api", help="仅在 127.0.0.1 启动本地单用户 API")
    api.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")

    production_api = subparsers.add_parser(
        "serve-production",
        help="按环境变量启动经过认证和限流的生产 API",
    )
    production_api.add_argument(
        "--check-config",
        action="store_true",
        help="仅校验部署环境变量，不启动服务",
    )

    readiness = subparsers.add_parser(
        "production-check",
        help="汇总账户、数据、策略、纸面、备份和部署门禁",
    )
    readiness.add_argument("--minimum-paper-sessions", type=int, default=20)
    readiness.add_argument("--backup-dir", type=Path)
    readiness.add_argument("--minimum-backups", type=int, default=1)
    readiness.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "update-data":
        return _update_data(args)
    if args.command == "init-history":
        return _init_history(args)
    if args.command == "import-point-in-time":
        return _import_point_in_time(args)
    if args.command == "import-point-in-time-universe":
        return _import_point_in_time_universe(args)
    if args.command == "audit-point-in-time":
        return _audit_point_in_time(args)
    if args.command == "sync-research":
        return _sync_research(args)
    if args.command == "sync-fund-lifecycle":
        return _sync_fund_lifecycle(args)
    if args.command == "sync-fund-style":
        return _sync_fund_style(args)
    if args.command in {"run-backtest", "backtest"}:
        return _run_backtest(args)
    if args.command in {"run-daily", "daily-run"}:
        return _run_daily(args)
    if args.command == "tail-alert":
        return _tail_alert(args)
    if args.command == "notify-daily":
        return _notify_daily(args)
    if args.command == "sector-rotation":
        return _sector_rotation(args)
    if args.command == "macro-allocation":
        return _macro_allocation(args)
    if args.command == "fund-overlap":
        return _fund_overlap(args)
    if args.command == "factor-validation":
        return _factor_validation(args)
    if args.command == "validate-drawdown":
        return _drawdown_validate(args)
    if args.command == "cash-flow-plan":
        return _cash_flow_plan(args)
    if args.command == "factor-monitor":
        return _factor_monitor(args)
    if args.command == "benchmark":
        return _benchmark(args)
    if args.command == "macro-observation":
        return _macro_observation(args)
    if args.command == "watchdog-scan":
        return _watchdog_scan(args)
    if args.command == "backup":
        return _backup(args)
    if args.command == "restore":
        return _restore(args)
    if args.command == "validate-account":
        return _validate_account(args)
    if args.command == "import-account":
        return _import_account(args)
    if args.command == "audit-account":
        return _audit_account(args)
    if args.command == "serve-api":
        return _serve_api(args)
    if args.command == "serve-production":
        return _serve_production(args)
    if args.command == "production-check":
        return _production_check(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
