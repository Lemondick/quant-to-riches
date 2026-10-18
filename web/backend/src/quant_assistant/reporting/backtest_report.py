"""Deterministic Markdown and strict-JSON walk-forward reports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any

from quant_assistant.backtest.metrics import PerformanceMetrics
from quant_assistant.backtest.walk_forward import WalkForwardResult

DISCLAIMER = "历史回测结果不代表未来收益，也不构成任何收益保证或自动交易指令。"


@dataclass(frozen=True, slots=True)
class BacktestReportArtifacts:
    markdown: str
    json_payload: Mapping[str, object]
    json_text: str


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_json_safe(item) for item in value]
    return value


def _metrics_payload(metrics: PerformanceMetrics) -> dict[str, object]:
    return _json_safe(asdict(metrics))


def _all_limitations(result: WalkForwardResult) -> tuple[str, ...]:
    limitations = [*result.limitations]
    for fold in result.folds:
        limitations.extend(fold.limitations)
    return tuple(dict.fromkeys(item for item in limitations if item))


def backtest_report_payload(result: WalkForwardResult) -> dict[str, object]:
    """Build a JSON-safe report payload with all mandatory disclosures."""

    if not isinstance(result, WalkForwardResult):
        raise TypeError("result must be WalkForwardResult")
    if not result.folds:
        raise ValueError("walk-forward result must contain at least one fold")

    limitations = _all_limitations(result)
    folds: list[dict[str, object]] = []
    for index, fold in enumerate(result.folds, start=1):
        folds.append(
            {
                "fold": index,
                "periods": {
                    "train": {"start": fold.train_start, "end": fold.train_end},
                    "validation": {
                        "start": fold.validation_start,
                        "end": fold.validation_end,
                    },
                    "test": {"start": fold.test_start, "end": fold.test_end},
                },
                "selected_parameters": dict(fold.selected_parameters),
                "train_metrics": (
                    _metrics_payload(fold.train_metrics) if fold.train_metrics is not None else None
                ),
                "validation_metrics": (
                    _metrics_payload(fold.validation_metrics)
                    if fold.validation_metrics is not None
                    else None
                ),
                "test_metrics": _metrics_payload(fold.test_metrics),
                "snapshot_ids": list(fold.snapshot_ids),
                "limitations": list(fold.limitations),
            }
        )

    payload = {
        "schema_version": "backtest-report-v1",
        "label": "research_only" if result.research_only else "validated_backtest",
        "research_only": result.research_only,
        "strategy_version": result.strategy_version,
        "selection_metric": result.selection_metric,
        "benchmark": result.benchmark_name,
        "sample_period": {
            "start": min(fold.train_start for fold in result.folds),
            "end": max(fold.test_end for fold in result.folds),
        },
        "snapshot_ids": list(result.snapshot_ids),
        "aggregate_metrics": _metrics_payload(result.aggregate_metrics),
        "folds": folds,
        "limitations": list(limitations),
        "disclaimer": DISCLAIMER,
    }
    return _json_safe(payload)


def _format_ratio(value: float) -> str:
    return "N/A" if not isfinite(value) else f"{value:.2%}"


def _format_number(value: float) -> str:
    return "N/A" if not isfinite(value) else f"{value:.4f}"


def _parameter_text(parameters: Mapping[str, object]) -> str:
    return json.dumps(_json_safe(dict(parameters)), ensure_ascii=False, sort_keys=True)


def render_backtest_markdown(result: WalkForwardResult) -> str:
    """Render a concise report that never presents history as a guarantee."""

    payload = backtest_report_payload(result)
    metrics = result.aggregate_metrics
    limitations = _all_limitations(result)
    snapshot_text = ", ".join(str(item) for item in result.snapshot_ids) or "未提供"
    lines = [
        "# 滚动样本外回测报告",
        "",
        f"- 状态标签：`{'research_only' if result.research_only else 'validated_backtest'}`",
        f"- 策略版本：`{result.strategy_version or '未提供'}`",
        f"- 参数选择指标：`{result.selection_metric}`",
        f"- 基准：`{result.benchmark_name}`",
        f"- 样本期间：{payload['sample_period']['start']} 至 {payload['sample_period']['end']}",
        f"- 数据快照编号：{snapshot_text}",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 总收益 | {_format_ratio(metrics.total_return)} |",
        f"| 年化收益 | {_format_ratio(metrics.annualized_return)} |",
        f"| 年化波动率 | {_format_ratio(metrics.annualized_volatility)} |",
        f"| 最大回撤 | {_format_ratio(metrics.max_drawdown)} |",
        f"| 零利率夏普 | {_format_number(metrics.sharpe_zero_rate)} |",
        f"| 双边换手率 | {_format_ratio(metrics.turnover)} |",
        f"| 总费用 | {metrics.total_fees:.2f} |",
        f"| 基准收益 | {_format_ratio(metrics.benchmark_return)} |",
        f"| 超额收益 | {_format_ratio(metrics.excess_return)} |",
        "",
        "## 样本外折叠",
        "",
    ]
    for index, fold in enumerate(result.folds, start=1):
        fold_snapshots = ", ".join(str(item) for item in fold.snapshot_ids) or "未提供"
        lines.extend(
            [
                f"### Fold {index}",
                "",
                f"- 训练期：{fold.train_start.isoformat()} 至 {fold.train_end.isoformat()}",
                (
                    f"- 验证期：{fold.validation_start.isoformat()} "
                    f"至 {fold.validation_end.isoformat()}"
                ),
                f"- 测试期：{fold.test_start.isoformat()} 至 {fold.test_end.isoformat()}",
                f"- 选定参数：`{_parameter_text(fold.selected_parameters)}`",
                f"- 数据快照编号：{fold_snapshots}",
                f"- 测试总收益：{_format_ratio(fold.test_metrics.total_return)}",
                f"- 测试基准收益：{_format_ratio(fold.test_metrics.benchmark_return)}",
                f"- 测试最大回撤：{_format_ratio(fold.test_metrics.max_drawdown)}",
                f"- 测试双边换手率：{_format_ratio(fold.test_metrics.turnover)}",
                f"- 测试费用：{fold.test_metrics.total_fees:.2f}",
                "",
            ]
        )

    lines.extend(["## 数据与方法限制", ""])
    if limitations:
        lines.extend(f"- `{limitation}`" for limitation in limitations)
    else:
        lines.append("- 无已登记限制。")
    lines.extend(["", "## 重要声明", "", DISCLAIMER, ""])
    return "\n".join(lines)


def build_backtest_report(result: WalkForwardResult) -> BacktestReportArtifacts:
    payload = backtest_report_payload(result)
    json_text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    return BacktestReportArtifacts(
        markdown=render_backtest_markdown(result),
        json_payload=payload,
        json_text=json_text,
    )


def generate_backtest_report(result: WalkForwardResult) -> BacktestReportArtifacts:
    """Compatibility alias with an action-oriented name."""

    return build_backtest_report(result)


def write_backtest_report(
    result: WalkForwardResult,
    markdown_path: str | Path,
    json_path: str | Path,
) -> BacktestReportArtifacts:
    """Write both artifacts only to the caller-provided paths."""

    artifacts = build_backtest_report(result)
    markdown_target = Path(markdown_path)
    json_target = Path(json_path)
    markdown_target.write_text(artifacts.markdown, encoding="utf-8")
    json_target.write_text(artifacts.json_text, encoding="utf-8")
    return artifacts


__all__ = [
    "BacktestReportArtifacts",
    "DISCLAIMER",
    "backtest_report_payload",
    "build_backtest_report",
    "generate_backtest_report",
    "render_backtest_markdown",
    "write_backtest_report",
]
