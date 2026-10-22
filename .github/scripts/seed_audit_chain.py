#!/usr/bin/env python3
"""CI 辅助：按 0036_audit_hash_chain.sql 文件头的标准口径，向 admin_audit
写入 3 条链式行，供 deploy/verify-audit-chain.sh 做正/反向验证。

标准口径：preimage = prev_hash||actor_id||action||target_id||detail||created_at_utc
（NULL 视为空串），created_at_utc = to_char(UTC,'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')，
hash = 小写 hex(SHA256(UTF-8(preimage)))；创世行 prev_hash=''。

连接沿用 PG* 环境变量（psql 默认读取）。
"""

import hashlib
import os
import subprocess

# (actor_id, action, target_id, detail, created_at 字面量, created_at_utc 标准形式)
CANON = [
    (
        "admin",
        "user.create",
        "u001",
        "创建用户",
        "2026-10-21 12:00:00+00",
        "2026-10-21T12:00:00.000000Z",
    ),
    (
        "admin",
        "backup.run",
        None,
        "手动备份",
        "2026-10-21 12:05:30.5+00",
        "2026-10-21T12:05:30.500000Z",
    ),
    (
        "admin",
        "config.update",
        "site",
        "调整保留期",
        "2026-10-21 12:09:00+00",
        "2026-10-21T12:09:00.000000Z",
    ),
]


def lit(value):
    if value is None:
        return "NULL"
    return "$q$" + value + "$q$"


def main():
    stmts = []
    prev = ""
    for actor, action, target, detail, ts, ts_utc in CANON:
        preimage = prev + actor + action + (target or "") + detail + ts_utc
        digest = hashlib.sha256(preimage.encode("utf-8")).hexdigest()
        stmts.append(
            "INSERT INTO public.admin_audit "
            "(actor_id, action, target_id, detail, created_at, prev_hash, hash) VALUES ("
            + ", ".join(
                [
                    lit(actor),
                    lit(action),
                    lit(target),
                    lit(detail),
                    "timestamptz " + lit(ts),
                    lit(prev),
                    lit(digest),
                ]
            )
            + ");"
        )
        prev = digest
    sql = "\n".join(stmts)
    subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-q", "-c", sql], check=True, env=os.environ)
    print(f"seeded {len(stmts)} chained admin_audit rows")


if __name__ == "__main__":
    main()
