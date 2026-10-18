"""P0 inventory: extract storage schema (SQLite + DuckDB) and file storage references.

Read-only: parses DDL and code references; never opens real databases or user data.
Output: storage-inventory.json next to this file (raw material for storage-map.md).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[4] / "web" / "backend" / "src" / "quant_assistant"
REPOS = BASE / "models" / "repositories"
DATA_DIR = Path(__file__).resolve().parents[4] / "data"
BASELINE_COMMIT = "29568a8a0a8aa9def7241d5d199bdf5d8a111adc"


def parse_ddl(text: str) -> list[dict]:
    """Parse CREATE TABLE ... statements (SQLite + DuckDB dialects)."""
    tables = []
    # 去掉行注释便于解析
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\s*\)", text, re.S):
        name = m.group(1)
        body = m.group(2)
        columns = []
        constraints = []
        depth = 0
        current = []
        parts: list[str] = []
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current).strip())
        for part in parts:
            head = part.split()[0].upper()
            if head in {"PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK"}:
                constraints.append(" ".join(part.split()))
            else:
                toks = part.split()
                columns.append({"name": toks[0], "type": " ".join(toks[1:3]) if len(toks) > 1 else ""})
        tables.append({"table": name, "columns": columns, "constraints": constraints})
    return tables


def scan_file_references() -> list[dict]:
    """Scan source for file storage references (parquet/json/sqlite/duckdb paths)."""
    patterns = {
        "parquet": re.compile(r"""["']([^"']*\.parquet)["']"""),
        "json_config": re.compile(r"""["']([^"']*(?:preferences|watchlist|settings|strategy)[^"']*\.json)["']"""),
        "duckdb": re.compile(r"""["']([^"']*\.duckdb)["']"""),
        "sqlite": re.compile(r"""["']([^"']*\.sqlite3?)["']"""),
    }
    refs: list[dict] = []
    for py in BASE.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for kind, pat in patterns.items():
            for m in pat.finditer(text):
                refs.append({
                    "kind": kind,
                    "file": str(py.relative_to(BASE)),
                    "literal": m.group(1),
                })
    # 去重（file+literal）
    seen = set()
    out = []
    for r in refs:
        key = (r["file"], r["literal"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def scan_data_dir() -> dict:
    """Catalog data/ directory structure (names and file counts/sizes only, no contents)."""
    if not DATA_DIR.is_dir():
        return {"exists": False}
    tree = {}
    for child in sorted(DATA_DIR.iterdir()):
        if child.is_dir():
            files = [f for f in child.rglob("*") if f.is_file()]
            total = sum(f.stat().st_size for f in files if not f.name.startswith("."))
            tree[child.name + "/"] = {"files": len(files), "bytes": total}
        else:
            tree[child.name] = {"bytes": child.stat().st_size}
    return {"exists": True, "entries": tree}


def main() -> None:
    auth_src = (REPOS / "auth.py").read_text(encoding="utf-8")
    sqlite_tables = parse_ddl(auth_src)
    duckdb_tables = parse_ddl((REPOS / "schema.sql").read_text(encoding="utf-8"))

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": BASELINE_COMMIT,
        "sqlite_auth": {
            "source": "models/repositories/auth.py",
            "table_count": len(sqlite_tables),
            "tables": sqlite_tables,
        },
        "duckdb_user": {
            "source": "models/repositories/schema.sql",
            "table_count": len(duckdb_tables),
            "tables": duckdb_tables,
        },
        "file_references": scan_file_references(),
        "data_dir_structure": scan_data_dir(),
    }
    out = Path(__file__).resolve().parent.parent / "storage-inventory.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"sqlite tables: {len(sqlite_tables)}, duckdb tables: {len(duckdb_tables)}")
    print(f"file references: {len(result['file_references'])}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
