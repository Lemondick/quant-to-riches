"""Command-line entry point for local daily operations."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import pandas as pd

from quant_assistant.analysis.factor_validation import cross_sectional_ic, layer_backtest
from quant_assistant.analysis.fund_holdings import analyze_fund_holdings
from quant_assistant.backtest.fee_audit import load_fee_schedule
from quant_assistant.backtest.production import (
    BacktestCostAssumptions,
    run_database_backtest,
)
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.config import Settings
from quant_assistant.data.pipeline import run_daily_data_pipeline
from quant_assistant.models.entities import AssetType, DataStatus
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.base import ProviderError
from quant_assistant.services.composition import build_local_services
from quant_assistant.services.notifications import (
    NotificationStatus,
    notifier_from_env,
)
from quant_assistant.services.portfolio_commands import PositionInput
from quant_assistant.services.roadmap_features import (
    BenchmarkService,
    DrawdownControlService,
    FactorMonitoringService,
    MacroDataService,
)
from quant_assistant.services.valuation_alerts import (
    FundValuationAlertService,
    ValuationAlertConfig,
)
from quant_assistant.strategies.macro_allocation import (
    MacroAllocationConfig,
    calculate_macro_allocation,
)
from quant_assistant.strategies.sector_rotation import (
    SectorRotationConfig,
    calculate_sector_rotation,
)

from .cli_commands import _print_research_sync, _public_market_provider, latest_completed_market_day

MARKET_DATA_READY_AT = time(16, 0)


def _run_backtest(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    try:
        start_date, end_date = _default_backtest_dates(
            Database(settings.database_path), args.start, args.end
        )
        costs = (
            load_fee_schedule(args.fee_schedule)
            if args.fee_schedule is not None
            else BacktestCostAssumptions(
                commission_rate=args.commission_rate,
                minimum_commission=args.minimum_commission,
                sell_stamp_tax_rate=args.stamp_tax_rate,
                transfer_fee_rate=args.transfer_fee_rate,
                slippage_bps=args.slippage_bps,
                verified_on=args.fee_verified_on,
            )
        )
        result = run_database_backtest(
            settings,
            start_date,
            end_date,
            initial_cash=args.initial_cash,
            costs=costs,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        if args.debug:
            raise
        print(f"回测失败：{error}", file=sys.stderr)
        return 1
    metrics = result.standard_metrics
    stress = result.stress_metrics
    print(
        f"backtest: run={result.result.run_id} sessions={len(result.result.trading_dates)} "
        f"return={metrics.total_return:.2%} excess={metrics.excess_return:.2%} "
        f"sharpe={metrics.sharpe_zero_rate:.3f} drawdown={metrics.max_drawdown:.2%}"
    )
    print(
        f"cost-stress-2x: return={stress.total_return:.2%} "
        f"drawdown={stress.max_drawdown:.2%} fees={stress.total_fees}"
    )
    for limitation in result.result.limitations:
        print(f"限制：{limitation}", file=sys.stderr)
    if result.result.research_only:
        print("回测已保存，但仍处于 research_only，不得作为收益承诺。", file=sys.stderr)
        return 2
    print("量化回测门槛通过；后续仍只允许纸面观察和人工确认。")
    return 0


def _default_backtest_dates(
    database: Database, requested_start: date | None, requested_end: date | None
) -> tuple[date, date]:
    database.initialize()
    dates = database.query_df(
        """
        SELECT DISTINCT trade_date FROM daily_prices
        WHERE symbol = '000300.SH' ORDER BY trade_date
        """
    )
    available = [value.date() for value in dates["trade_date"]]
    if not available:
        raise ValueError("缺少沪深300历史，请先运行 init-history")
    end_date = requested_end or available[-1]
    eligible = [value for value in available if value <= end_date]
    if not eligible:
        raise ValueError("回测截止日期早于本地历史")
    start_date = requested_start or eligible[min(200, len(eligible) - 1)]
    return start_date, end_date


def _run_daily(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    as_of_date = args.date or latest_completed_market_day()
    services = None
    alert_key = f"daily-run:{as_of_date.isoformat()}"
    try:
        services = build_local_services(settings)
        services.roadmap.watchdog.heartbeat(
            "run-daily", expected_interval_minutes=36 * 60, status="running"
        )
        provider = _public_market_provider()
        with services.operation_coordinator.hold(alert_key):
            pipeline = run_daily_data_pipeline(settings, provider, as_of_date)
            decision = services.daily_decision.run(as_of_date)
            services.roadmap.watchdog.heartbeat(
                "run-daily",
                expected_interval_minutes=36 * 60,
                status="completed" if decision.status is DataStatus.VALID else "blocked",
                completed_at=datetime.now(SHANGHAI_TZ),
            )
    except (
        AttributeError,
        ConnectionError,
        FileNotFoundError,
        OSError,
        ProviderError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        if services is not None:
            services.roadmap.watchdog.heartbeat(
                "run-daily",
                expected_interval_minutes=36 * 60,
                status="failed",
                error=type(error).__name__,
            )
            services.operational_alerts.raise_alert(
                alert_key,
                "critical",
                f"每日运行失败：{type(error).__name__}",
                {"date": as_of_date.isoformat()},
            )
        if args.debug:
            raise
        print(f"每日运行失败：{error}", file=sys.stderr)
        return 1

    for dataset, item in pipeline.datasets.items():
        issues = "; ".join(issue.message for issue in item.issues) or "ok"
        print(
            f"{dataset}: status={item.status.value} rows={item.row_count} "
            f"snapshot={item.snapshot_id} {issues}"
        )
    _print_research_sync(getattr(pipeline, "research", None))
    if pipeline.active_source:
        print(f"market_source: {pipeline.active_source}")
    print(
        f"decision: run={decision.run_id} date={decision.as_of_date.isoformat()} "
        f"status={decision.status.value} readiness={decision.readiness.value} "
        f"snapshot={decision.data_snapshot_id} regime={decision.market_regime.value} "
        f"orders={len(decision.orders)}"
    )
    for blocker in decision.blockers:
        print(f"阻断：{blocker}", file=sys.stderr)
    if decision.status is DataStatus.VALID:
        services.operational_alerts.resolve(alert_key)
    else:
        services.operational_alerts.raise_alert(
            alert_key,
            "warning",
            "每日决策被数据或组合门禁阻断",
            {"blockers": list(decision.blockers)},
        )
    print("所有建议均需在支付宝或券商官方客户端人工核对并确认。")
    return 0 if decision.status is DataStatus.VALID else 2


def _tail_alert(args: argparse.Namespace) -> int:
    """Run the read-only 14:30 fund estimate scan for local positions."""

    settings = Settings.from_env()
    try:
        services = build_local_services(settings)
        frame = services.database.query_df(
            """
            SELECT symbol, asset_type, quantity, average_cost, market_value, as_of_date
            FROM positions
            WHERE asset_type = 'fund' AND quantity > 0
            ORDER BY symbol
            """
        )
        positions = _position_inputs_from_frame(frame)
        notifier = None if args.no_notify else notifier_from_env()
        scanner = FundValuationAlertService(
            services.operational_alerts,
            notifier=notifier,
        )
        alert_settings = settings.valuation_alerts
        result = scanner.scan(
            positions,
            config=ValuationAlertConfig(
                warning_change_rate_percent=(
                    args.warning_threshold
                    if args.warning_threshold is not None
                    else alert_settings.warning_change_rate_percent
                ),
                critical_change_rate_percent=(
                    args.critical_threshold
                    if args.critical_threshold is not None
                    else alert_settings.critical_change_rate_percent
                ),
                info_change_rate_percent=(
                    args.info_threshold
                    if args.info_threshold is not None
                    else alert_settings.info_change_rate_percent
                ),
                silence_minutes=(
                    args.silence_minutes
                    if args.silence_minutes is not None
                    else alert_settings.silence_minutes
                ),
            ),
            notify=not args.no_notify,
        )
    except (
        AttributeError,
        ConnectionError,
        FileNotFoundError,
        OSError,
        ProviderError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        if args.debug:
            raise
        print(f"尾盘估值预警失败：{error}", file=sys.stderr)
        return 1

    if not positions:
        print("当前没有正数基金持仓；未调用外部估值接口。")
    for alert in result.alerts:
        print(
            f"预警：{alert.symbol} severity={alert.severity.value} "
            f"change={alert.change_rate:+.2f}% observed={alert.observed_at.isoformat()}"
        )
    if result.suppressed:
        print("静默去重：" + ",".join(result.suppressed))
    if result.unavailable:
        print("估值不可用：" + ",".join(result.unavailable), file=sys.stderr)
    notification = result.notification
    print(
        f"notification: channel={notification.channel} status={notification.status.value} "
        f"message={notification.message}"
    )
    if result.unavailable or notification.status is NotificationStatus.FAILED:
        return 1
    print("基金估值/最新净值仅用于提醒，不会覆盖本地净值或生成交易订单。")
    return 0


def _sector_rotation(args: argparse.Namespace) -> int:
    try:
        frame = pd.read_csv(args.input)
        config = SectorRotationConfig(
            sectors=tuple(args.sector) if args.sector else SectorRotationConfig().sectors,
            min_observations=args.min_observations,
            top_n=args.top_n,
        )
        result = calculate_sector_rotation(frame, args.as_of, config=config)
        payload = {
            "as_of_date": args.as_of.isoformat(),
            "ready": result.ready,
            "blockers": list(result.blockers),
            "ranking": _frame_records(result.ranking),
            "allocations": _frame_records(result.allocations),
        }
        _write_optional_json(args.output, payload)
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(f"赛道轮动计算失败：{error}", file=sys.stderr)
        return 1
    print(f"sector-rotation: as_of={args.as_of.isoformat()} ready={result.ready}")
    for row in result.allocations.itertuples(index=False):
        print(f"配置：{row.sector} weight={row.target_weight} reason={row.reason}")
    for blocker in result.blockers:
        print(f"阻断：{blocker}", file=sys.stderr)
    return 0 if result.ready else 2


def _macro_allocation(args: argparse.Namespace) -> int:
    try:
        frame = pd.read_csv(args.input)
        config = MacroAllocationConfig(
            assets=tuple(args.asset) if args.asset else MacroAllocationConfig().assets,
            min_observations=args.min_observations,
        )
        result = calculate_macro_allocation(frame, args.as_of, config=config)
        payload = {
            "as_of_date": args.as_of.isoformat(),
            "ready": result.ready,
            "blockers": list(result.blockers),
            "metrics": _frame_records(result.metrics),
            "allocations": _frame_records(result.allocations),
        }
        _write_optional_json(args.output, payload)
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(f"宏观配置计算失败：{error}", file=sys.stderr)
        return 1
    print(f"macro-allocation: as_of={args.as_of.isoformat()} ready={result.ready}")
    for row in result.allocations.itertuples(index=False):
        print(f"配置：{row.asset} weight={row.target_weight} reason={row.reason}")
    for blocker in result.blockers:
        print(f"阻断：{blocker}", file=sys.stderr)
    return 0 if result.ready else 2


def _fund_overlap(args: argparse.Namespace) -> int:
    try:
        frame = pd.read_csv(args.input)
        weights = _parse_portfolio_weights(args.portfolio_weight)
        result = analyze_fund_holdings(frame, args.as_of, portfolio_weights=weights or None)
        payload = {
            "as_of_date": args.as_of.isoformat(),
            "ready": result.ready,
            "blockers": list(result.blockers),
            "holdings": _frame_records(result.holdings),
            "fund_industry": _frame_records(result.fund_industry),
            "industry_overlap": _frame_records(result.industry_overlap.reset_index()),
            "stock_overlap": _frame_records(result.stock_overlap.reset_index()),
            "portfolio_industry_exposure": _frame_records(result.portfolio_industry_exposure),
        }
        _write_optional_json(args.output, payload)
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(f"基金持仓穿透失败：{error}", file=sys.stderr)
        return 1
    print(
        f"fund-overlap: as_of={args.as_of.isoformat()} ready={result.ready} "
        f"funds={result.holdings['fund_symbol'].nunique() if not result.holdings.empty else 0}"
    )
    if not result.industry_overlap.empty:
        print(result.industry_overlap.to_string())
    for blocker in result.blockers:
        print(f"阻断：{blocker}", file=sys.stderr)
    return 0 if result.ready else 2


def _factor_validation(args: argparse.Namespace) -> int:
    try:
        observations = pd.read_csv(args.input)
        ic = cross_sectional_ic(
            observations,
            minimum_observations=args.min_observations,
        )
        layers = layer_backtest(
            observations,
            layers=args.layers,
            minimum_observations=args.min_observations,
        )
        payload = {
            "ic": {
                "observations": ic.observations,
                "dates": ic.dates,
                "mean_ic": ic.mean_ic,
                "ic_std": ic.ic_std,
                "ic_ir": ic.ic_ir,
                "positive_ic_ratio": ic.positive_ic_ratio,
                "blockers": list(ic.blockers),
                "by_date": _frame_records(ic.by_date),
            },
            "layers": {
                "layers": layers.layers,
                "observations": layers.observations,
                "long_short_mean": layers.long_short_mean,
                "long_short_ir": layers.long_short_ir,
                "blockers": list(layers.blockers),
                "by_date": _frame_records(layers.by_date),
                "summary": _frame_records(layers.summary),
            },
        }
        _write_optional_json(args.output, payload)
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(f"因子有效性验证失败：{error}", file=sys.stderr)
        return 1
    print(
        f"factor-validation: dates={ic.dates} mean_ic={ic.mean_ic:.4f} "
        f"ic_ir={ic.ic_ir:.4f} long_short={layers.long_short_mean:.4%}"
    )
    for blocker in (*ic.blockers, *layers.blockers):
        print(f"阻断：{blocker}", file=sys.stderr)
    return 0 if not ic.blockers and not layers.blockers else 2


def _drawdown_validate(args: argparse.Namespace) -> int:
    try:
        frame = pd.read_csv(args.input)
        settings = Settings.from_env()
        database = Database(settings.database_path)
        database.initialize()
        report = DrawdownControlService(database).validate_and_store(
            args.run_id, frame, threshold=args.threshold
        )
        print(
            json.dumps(
                {
                    "run_id": report.run_id,
                    "threshold": report.threshold,
                    "max_drawdown": report.max_drawdown,
                    "observed_sessions": report.observed_sessions,
                    "breach_count": report.breach_count,
                    "passed": report.passed,
                    "research_only": report.research_only,
                    "reasons": list(report.reasons),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (FileNotFoundError, OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"回撤控制线验证失败：{error}", file=sys.stderr)
        return 1
    return 0 if report.passed else 2


def _factor_monitor(args: argparse.Namespace) -> int:
    try:
        frame = pd.read_csv(args.input)
        required = {"as_of_date", "ic", "ir"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"因子CSV缺少列：{','.join(sorted(missing))}")
        settings = Settings.from_env()
        database = Database(settings.database_path)
        database.initialize()
        service = FactorMonitoringService(database)
        for row in frame.to_dict("records"):
            service.record(
                args.factor,
                date.fromisoformat(str(row["as_of_date"])),
                ic=None if pd.isna(row["ic"]) else float(row["ic"]),
                ir=None if pd.isna(row["ir"]) else float(row["ir"]),
                sample_count=int(row.get("sample_count", 0) or 0),
                source=args.source,
            )
        report = service.report(args.factor, limit=args.limit)
        print(
            json.dumps(
                {
                    "factor": report.factor,
                    "mean_ic": report.mean_ic,
                    "mean_ir": report.mean_ir,
                    "ic_slope": report.ic_slope,
                    "ir_slope": report.ir_slope,
                    "decay": report.decay,
                    "alerts": list(report.alerts),
                    "research_only": report.research_only,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (FileNotFoundError, OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"因子监控失败：{error}", file=sys.stderr)
        return 1
    return 0


def _benchmark(args: argparse.Namespace) -> int:
    try:
        settings = Settings.from_env()
        database = Database(settings.database_path)
        database.initialize()
        service = BenchmarkService(database)
        if args.compare:
            print(
                json.dumps(
                    service.compare(args.compare, start_date=args.start, end_date=args.end),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0
        if not args.name or not args.component:
            raise ValueError("创建基准需要 --name 和至少一个 --component CODE=WEIGHT")
        components = {}
        for value in args.component:
            symbol, separator, weight = value.partition("=")
            if not separator:
                raise ValueError("component must use CODE=WEIGHT")
            components[symbol.strip()] = float(weight)
        item = service.create(
            args.name, components, benchmark_id=args.benchmark_id, is_default=args.default
        )
        print(
            json.dumps(
                {
                    "benchmark_id": item.benchmark_id,
                    "name": item.name,
                    "components": dict(item.components),
                    "is_default": item.is_default,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"组合基准失败：{error}", file=sys.stderr)
        return 1
    return 0


def _macro_observation(args: argparse.Namespace) -> int:
    try:
        settings = Settings.from_env()
        database = Database(settings.database_path)
        database.initialize()
        item = MacroDataService(database).record(
            args.indicator, args.date, args.value, args.unit, args.source
        )
        print(f"宏观观测已保存：{item.indicator} {item.observed_on} {item.value} {item.unit}")
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"宏观观测失败：{error}", file=sys.stderr)
        return 1
    return 0


def _parse_portfolio_weights(values: Sequence[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("组合基金权重必须使用FUND=WEIGHT格式")
        symbol, weight = value.split("=", 1)
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("组合基金代码不能为空")
        result[symbol] = float(weight)
    return result


def _frame_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    cleaned = frame.astype(object).where(pd.notna(frame), None)
    return cleaned.to_dict(orient="records")


def _write_optional_json(path: Path | None, payload: object) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _json_safe(payload),
            ensure_ascii=False,
            indent=2,
            default=str,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def _json_safe(value: object) -> object:
    """Convert non-finite numeric diagnostics to JSON ``null`` explicitly."""

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _position_inputs_from_frame(frame: object) -> tuple[PositionInput, ...]:
    """Convert only validated local ledger rows into the alert input contract."""

    if not hasattr(frame, "to_dict"):
        raise TypeError("持仓查询结果不是表格")
    items: list[PositionInput] = []
    for row in frame.to_dict("records"):
        asset_type = AssetType(str(row["asset_type"]))
        as_of_value = row["as_of_date"]
        as_of_date = as_of_value.date() if hasattr(as_of_value, "date") else as_of_value
        items.append(
            PositionInput(
                symbol=str(row["symbol"]),
                asset_type=asset_type,
                quantity=Decimal(str(row["quantity"])),
                average_cost=Decimal(str(row["average_cost"])),
                market_value=Decimal(str(row["market_value"])),
                as_of_date=as_of_date,
            )
        )
    return tuple(items)
