"""P2: 将 DuckDB schema.sql 翻译为 PostgreSQL 业务域 DDL（加 user_id 用户隔离维度）。

规则（对应计划书 §4「目标键必须包含用户维度」）：
- 每个业务表新增 user_id TEXT NOT NULL 作为首列；
- 主键改为复合 (user_id, 原主键列)；UNIQUE 约束加 user_id 维度；
- 自增 id 保持全局序列 nextval（导入后校准序列，见计划书 §4）；
- 序列在全部建表之前创建；
- DOUBLE -> DOUBLE PRECISION；
- 纯市场参考数据表与回测表保留在 DuckDB/Parquet（计划书表1「分析存储」），见 EXCLUDE_TABLES。

只读脚本：不改产品代码，输出 migrations/0002_business.sql。
"""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[3] / "web" / "backend" / "src" / "quant_assistant" / "models" / "repositories" / "schema.sql"
OUT = Path(__file__).resolve().parents[1] / "migrations" / "0002_business.sql"

EXCLUDE_TABLES = {
    "instrument_history", "daily_prices",
    "fund_nav", "fund_valuation_history", "fund_manager_history", "fund_profile_history",
    "fund_lifecycle_events", "fund_holding_history", "fund_style_drift_events",
    "backtest_runs", "backtest_nav", "stock_rankings",
}


def _balanced_body(text: str, start: int) -> tuple[str, int]:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i
    raise ValueError("unbalanced parens")


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _translate_statement(name: str, body: str) -> str:
    cols: list[str] = []
    pk_cols: list[str] = []
    uniques: list[list[str]] = []
    for part in _split_top_level(body):
        upper = part.lstrip().upper()
        if upper.startswith("PRIMARY KEY"):
            inner = part[part.index("(") + 1 : part.rindex(")")]
            pk_cols = [c.strip() for c in inner.split(",")]
        elif upper.startswith("UNIQUE"):
            inner = part[part.index("(") + 1 : part.rindex(")")]
            uniques.append([c.strip() for c in inner.split(",")])
        elif re.search(r"\bPRIMARY\s+KEY\b", part, re.IGNORECASE):
            cleaned = re.sub(r"\bPRIMARY\s+KEY\b", "", part, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            pk_cols.append(cleaned.split()[0])
            cols.append(cleaned)
        elif re.search(r"\bUNIQUE\b", part, re.IGNORECASE):
            cleaned = re.sub(r"\bUNIQUE\b", "", part, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            uniques.append([cleaned.split()[0]])
            cols.append(cleaned)
        else:
            cols.append(part)
    pk = ", ".join(["user_id"] + pk_cols) if pk_cols else "user_id"
    body_lines = [re.sub(r"\bDOUBLE\b", "DOUBLE PRECISION", c) for c in cols]
    body_lines.append(f"PRIMARY KEY ({pk})")
    for u in uniques:
        body_lines.append("UNIQUE (" + ", ".join(["user_id"] + u) + ")")
    body_joined = ",\n    ".join(body_lines)
    return (
        f"CREATE TABLE {name} (\n"
        f"    user_id TEXT NOT NULL REFERENCES users (id),\n"
        f"    {body_joined}\n"
        f");\n"
    )


def main() -> None:
    text = SCHEMA.read_text(encoding="utf-8")

    header = (
        "-- 0002_business.sql — 业务域（来自旧每用户 DuckDB schema.sql，合并为单 PG 库，加 user_id 隔离维度）\n"
        "-- 规则：user_id 首列 + 复合主键/唯一约束含 user_id；DOUBLE -> DOUBLE PRECISION；全局序列 nextval。\n"
        "-- 排除表（保留在 DuckDB/Parquet，Python 分析域，计划书表1）：\n"
        f"--   {', '.join(sorted(EXCLUDE_TABLES))}\n"
        "-- 本文件由 tools/translate_business_schema.py 生成，只读分析，不改产品代码。\n\n"
    )

    out_parts: list[str] = [header]
    included: list[str] = []
    excluded: list[str] = []
    table_statements: list[str] = []

    head_pattern = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)\s*")
    for m in head_pattern.finditer(text):
        name = m.group(1)
        open_paren = text.find("(", m.end())
        body, close_idx = _balanced_body(text, open_paren)
        if name in EXCLUDE_TABLES:
            excluded.append(name)
            continue
        table_statements.append(_translate_statement(name, body))
        included.append(name)

    # 序列必须在建表之前创建（表 DEFAULT nextval('...') 引用）。
    tables_sql = "\n".join(table_statements)
    seq_pattern = re.compile(r"nextval\('([^']+)'\)")
    used_sequences = sorted(set(seq_pattern.findall(tables_sql)))
    if used_sequences:
        out_parts.append("-- 全局自增序列（跨用户共享；导入后校准到 max(id)+1，见计划书 §4）。\n")
        for seq in used_sequences:
            out_parts.append(f"CREATE SEQUENCE IF NOT EXISTS {seq} START 1;\n")
        out_parts.append("\n")

    out_parts.extend(table_statements)

    # 增量迁移列（schema.sql 中 ALTER TABLE ... ADD COLUMN IF NOT EXISTS 添加的列）——
    # 这些列不在 CREATE TABLE 里，必须单独追加（对 PG 有效），否则丢列。
    alter_pattern = re.compile(r"ALTER TABLE (\w+)\s+ADD COLUMN IF NOT EXISTS [^;]+;")
    alter_stmts: list[str] = []
    for m in alter_pattern.finditer(text):
        if m.group(1) in EXCLUDE_TABLES:
            continue
        alter_stmts.append(m.group(0))
    if alter_stmts:
        out_parts.append("\n-- 增量迁移列（ALTER TABLE ... ADD COLUMN，来自旧 DuckDB 的增量迁移）。\n")
        out_parts.extend(stmt + "\n" for stmt in alter_stmts)

    out_parts.append(
        "\n-- 用户隔离索引：每张业务表按 user_id 建索引（串账户防护，计划书 §7「跨账号ID访问被拒绝」）。\n"
    )
    for name in included:
        out_parts.append(f"CREATE INDEX IF NOT EXISTS idx_{name}_user ON {name} (user_id);\n")

    OUT.write_text("".join(out_parts), encoding="utf-8")
    print(f"迁移表: {len(included)}")
    print(f"序列: {len(used_sequences)}")
    print(f"排除表: {len(excluded)}")
    print(f"输出: {OUT}")


if __name__ == "__main__":
    main()
