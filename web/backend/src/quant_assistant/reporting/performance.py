"""Performance calendar and deterministic daily decision-report exports."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from io import BytesIO

import numpy as np
import pandas as pd


def build_performance_calendar(valuations: pd.DataFrame) -> pd.DataFrame:
    """Build daily P/L and unit-NAV returns from persisted valuation rows.

    The input is expected to be the read-only ``portfolio_valuations`` query.
    External deposits/withdrawals are removed from cash P/L when
    ``net_external_flow`` is present.
    """

    if not isinstance(valuations, pd.DataFrame):
        raise TypeError("valuations must be a pandas DataFrame")
    if valuations.empty:
        return pd.DataFrame(
            columns=["as_of_date", "total_value", "unit_nav", "daily_profit", "daily_return"]
        )
    required = {"as_of_date", "total_value", "unit_nav"}
    missing = required - set(valuations.columns)
    if missing:
        raise ValueError(f"valuations missing columns: {', '.join(sorted(missing))}")
    frame = valuations.copy(deep=True)
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"], errors="coerce").dt.date
    frame["total_value"] = pd.to_numeric(frame["total_value"], errors="coerce")
    frame["unit_nav"] = pd.to_numeric(frame["unit_nav"], errors="coerce")
    flow_values = (
        frame["net_external_flow"]
        if "net_external_flow" in frame
        else pd.Series(0, index=frame.index)
    )
    frame["net_external_flow"] = pd.to_numeric(flow_values, errors="coerce").fillna(0)
    frame = frame.loc[
        frame["as_of_date"].notna() & frame["total_value"].gt(0) & frame["unit_nav"].gt(0)
    ].copy()
    frame = frame.sort_values("as_of_date", kind="mergesort").drop_duplicates(
        "as_of_date", keep="last"
    )
    frame["previous_total_value"] = frame["total_value"].shift(1)
    frame["previous_unit_nav"] = frame["unit_nav"].shift(1)
    frame["daily_profit"] = (
        frame["total_value"] - frame["previous_total_value"] - frame["net_external_flow"]
    )
    frame["daily_return"] = frame["unit_nav"] / frame["previous_unit_nav"] - 1
    frame.loc[frame["previous_total_value"].isna(), "daily_profit"] = np.nan
    frame.loc[frame["previous_unit_nav"].isna(), "daily_return"] = np.nan
    frame["year"] = pd.to_datetime(frame["as_of_date"]).dt.year
    frame["week"] = pd.to_datetime(frame["as_of_date"]).dt.isocalendar().week.astype(int)
    frame["weekday"] = pd.to_datetime(frame["as_of_date"]).dt.weekday
    frame["heatmap_value"] = frame["daily_profit"].fillna(frame["daily_return"] * 100)
    return frame.reset_index(drop=True)


def calendar_heatmap_frame(valuations: pd.DataFrame) -> pd.DataFrame:
    """Return a compact GitHub-style calendar dataset (Mon=0 … Sun=6)."""

    frame = build_performance_calendar(valuations)
    if frame.empty:
        return frame
    return frame[
        ["as_of_date", "year", "week", "weekday", "daily_profit", "daily_return", "heatmap_value"]
    ].copy()


def _money(value: object) -> str:
    try:
        return f"¥{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def render_daily_report_markdown(
    *,
    as_of_date: date,
    status: str,
    valuations: pd.DataFrame,
    notes: tuple[str, ...] = (),
    blockers: tuple[str, ...] = (),
    operational_summary: Mapping[str, object] | None = None,
    source_health: pd.DataFrame | None = None,
) -> str:
    """Render a Markdown report suitable for download or archival."""

    calendar = build_performance_calendar(valuations)
    latest = calendar.iloc[-1] if not calendar.empty else None
    daily_profit = (
        _money(latest["daily_profit"])
        if latest is not None and pd.notna(latest["daily_profit"])
        else "—"
    )
    daily_return = (
        f"{float(latest['daily_return']):.2%}"
        if latest is not None and pd.notna(latest["daily_return"])
        else "—"
    )
    lines = [
        f"# 量化投资决策简报 · {as_of_date.isoformat()}",
        "",
        f"- 数据状态：**{status}**",
        f"- 组合市值：**{_money(latest['total_value']) if latest is not None else '—'}**",
        f"- 单位净值：**{float(latest['unit_nav']):.6f}**"
        if latest is not None
        else "- 单位净值：**—**",
        f"- 当日盈亏：**{daily_profit}**",
        f"- 当日收益率：**{daily_return}**",
        "",
        "## 风险与门禁",
        "",
    ]
    if blockers:
        lines.extend(f"- ⛔ {item}" for item in blockers)
    else:
        lines.append("- 未发现新增阻断项（仍需人工核对渠道和费用）。")
    if notes:
        lines.extend(("", "## 研究备注", "", *[f"- {item}" for item in notes]))
    if operational_summary:
        lines.extend(("", "## 运行摘要", ""))
        summary_labels = (
            ("decision_runs", "决策运行次数", "int"),
            ("valid_decisions", "通过决策次数", "int"),
            ("decision_pass_rate", "决策通过率", "ratio"),
            ("snapshot_count", "快照数量", "int"),
            ("source_count", "数据来源数", "int"),
            ("fetch_lag_days", "抓取日期滞后", "days"),
        )
        for key, label, value_type in summary_labels:
            if key not in operational_summary or operational_summary[key] is None:
                continue
            raw = operational_summary[key]
            try:
                if value_type == "ratio":
                    value = f"{float(raw):.2%}"
                elif value_type == "days":
                    value = f"{int(raw)} 天"
                else:
                    value = str(int(raw))
            except (TypeError, ValueError, OverflowError):
                value = "—"
            lines.append(f"- {label}：**{value}**")
    if isinstance(source_health, pd.DataFrame) and not source_health.empty:
        required_source_columns = {
            "source",
            "snapshot_count",
            "valid_snapshot_rate",
            "latest_fetched_at",
        }
        lines.extend(("", "## 来源健康", ""))
        if required_source_columns.issubset(source_health.columns):
            lines.extend(
                (
                    "| 来源 | 快照数 | 有效率 | 最近抓取 |",
                    "| --- | ---: | ---: | --- |",
                )
            )
            for row in source_health.itertuples(index=False):
                rate = (
                    f"{float(row.valid_snapshot_rate):.2%}"
                    if pd.notna(row.valid_snapshot_rate)
                    else "—"
                )
                lines.append(
                    f"| {row.source} | {int(row.snapshot_count)} | {rate} | "
                    f"{row.latest_fetched_at} |"
                )
        else:
            lines.append("- 来源健康字段不完整，未生成汇总。")
    lines.extend(
        ("", "## 收益日历数据", "", "| 日期 | 当日盈亏 | 当日收益率 |", "| --- | ---: | ---: |")
    )
    if calendar.empty:
        lines.append("| — | — | — |")
    else:
        for row in calendar.itertuples(index=False):
            profit = _money(row.daily_profit) if pd.notna(row.daily_profit) else "—"
            daily_return = f"{float(row.daily_return):.2%}" if pd.notna(row.daily_return) else "—"
            lines.append(f"| {row.as_of_date} | {profit} | {daily_return} |")
    lines.extend(("", "> 本报告只读展示研究结果，不构成投资承诺，也不会自动下单。"))
    return "\n".join(lines) + "\n"


def render_daily_report_pdf(markdown: str) -> bytes:
    """Render a valid one-page PDF without a heavyweight runtime dependency.

    The standard Adobe ``STSong-Light`` CID font keeps Chinese report text
    selectable in viewers that provide the built-in GB character collection.
    Markdown remains the canonical lossless export and is always offered
    alongside this PDF in the UI.
    """

    if not isinstance(markdown, str):
        raise TypeError("markdown must be a string")
    text = "\n".join(line.strip() for line in markdown.splitlines() if line.strip())
    lines = [text[index : index + 48] for index in range(0, len(text), 48)][:45] or ["Quant report"]
    content_lines = ["BT", "/F1 9 Tf", "50 760 Td"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("0 -15 Td")
        content_lines.append(f"<{line.encode('utf-16-be').hex().upper()}> Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("ascii")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 6 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
        b"/Encoding /UniGB-UCS2-H /DescendantFonts [5 0 R] >> endobj\n",
        b"5 0 obj << /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
        b"/DW 1000 >> endobj\n",
        b"6 0 obj << /Length "
        + str(len(stream)).encode()
        + b" >> stream\n"
        + stream
        + b"\nendstream endobj\n",
    ]
    output = BytesIO()
    output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj in objects:
        offsets.append(output.tell())
        output.write(obj)
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode())
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return output.getvalue()


__all__ = [
    "build_performance_calendar",
    "calendar_heatmap_frame",
    "render_daily_report_markdown",
    "render_daily_report_pdf",
]
