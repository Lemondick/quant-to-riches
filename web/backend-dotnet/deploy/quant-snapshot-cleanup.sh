#!/usr/bin/env bash
# quant-snapshot-cleanup.sh — 每日快照分区维护（任务9）。
# 调用 DB 函数 public.quant_snapshot_maintenance()：预建未来 3 个月分区 +
# 删除超出保留期的月份分区。保留期由环境变量 QUANT_SNAPSHOT_RETENTION_TICK_DAYS
# 控制（默认 90 天），经 GUC quant.snapshot_retention_tick_days 传入函数。
#
# 连接：优先解析 QUANT_PG_CONNECTION（Npgsql 风格 Host=..;Port=..;Database=..;
# Username=..;Password=..），否则沿用标准 PG* 环境变量（PGHOST/PGPORT/PGUSER/
# PGDATABASE/PGPASSWORD）。通常由 quant-snapshot-cleanup.service 每日触发，
# 也可手工执行。输出摘要行供 journald 记录。
set -euo pipefail

RETENTION_DAYS="${QUANT_SNAPSHOT_RETENTION_TICK_DAYS:-90}"
PSQL_BIN="${QUANT_PSQL_BIN:-psql}"

# fail-closed：保留期必须为正整数，拒绝注入与非法值。
if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || [ "$RETENTION_DAYS" -lt 1 ]; then
    echo "quant-snapshot-cleanup: 非法保留期 QUANT_SNAPSHOT_RETENTION_TICK_DAYS='$RETENTION_DAYS'（需为 >=1 的整数）" >&2
    exit 2
fi

# 解析 Npgsql 风格连接串为 PG* 环境变量（大小写不敏感的键名）。
if [ -n "${QUANT_PG_CONNECTION:-}" ]; then
    # 键不存在时 grep 返回 1：加 || true，避免 pipefail + set -e 误杀。
    pick() { printf '%s' "$QUANT_PG_CONNECTION" | tr ';' '\n' | grep -i -m1 "^$1=" | cut -d= -f2- || true; }
    for mapping in "Host:PGHOST" "Port:PGPORT" "Database:PGDATABASE" "Username:PGUSER" "Password:PGPASSWORD"; do
        key="${mapping%%:*}"
        var="${mapping##*:}"
        val="$(pick "$key")"
        if [ -n "$val" ]; then
            export "$var=$val"
        fi
    done
fi

command -v "$PSQL_BIN" >/dev/null 2>&1 || {
    echo "quant-snapshot-cleanup: 找不到 psql（QUANT_PSQL_BIN='$PSQL_BIN'）" >&2
    exit 2
}

RESULT="$("$PSQL_BIN" -X -q -t -A -v ON_ERROR_STOP=1 \
    -c "SET quant.snapshot_retention_tick_days = '${RETENTION_DAYS}';" \
    -c "SELECT public.quant_snapshot_maintenance();")"
echo "quant-snapshot-cleanup: ${RESULT}"
