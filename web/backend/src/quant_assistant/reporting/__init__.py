"""Human- and machine-readable research reports."""

from quant_assistant.reporting.backtest_report import (
    backtest_report_payload,
    build_backtest_report,
    render_backtest_markdown,
)
from quant_assistant.reporting.performance import (
    build_performance_calendar,
    calendar_heatmap_frame,
    render_daily_report_markdown,
    render_daily_report_pdf,
)

__all__ = [
    "backtest_report_payload",
    "build_backtest_report",
    "render_backtest_markdown",
    "build_performance_calendar",
    "calendar_heatmap_frame",
    "render_daily_report_markdown",
    "render_daily_report_pdf",
]
