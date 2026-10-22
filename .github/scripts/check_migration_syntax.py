#!/usr/bin/env python3
"""CI：用 pglast 解析全部 SQL 迁移（含 .down.sql），语法错误即阻断。

用法：python3 .github/scripts/check_migration_syntax.py [migrations-dir]
默认目录：web/backend-dotnet/migrations（相对仓库根目录）。
"""

import glob
import os
import sys

import pglast


def main() -> int:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(script_dir))  # <root>/.github/scripts/xxx.py
    migrations_dir = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(repo_root, "web", "backend-dotnet", "migrations")
    )
    files = sorted(glob.glob(os.path.join(migrations_dir, "*.sql")))
    if not files:
        print(f"未找到迁移文件：{migrations_dir}")
        return 1
    failures = 0
    for path in files:
        with open(path, encoding="utf-8-sig") as f:
            sql = f.read()
        try:
            pglast.parse_sql(sql)
            print(f"OK   {os.path.basename(path)}")
        except Exception as e:  # noqa: BLE001 - 解析失败即阻断，需打印原因
            print(f"FAIL {os.path.basename(path)}: {e}")
            failures += 1
    print(f"共 {len(files)} 个文件，{failures} 个失败（pglast {pglast.__version__}）")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
