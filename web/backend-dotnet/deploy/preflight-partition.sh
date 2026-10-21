#!/usr/bin/env bash
# preflight-partition.sh — 0035 分区重建迁移（native_monitor_samples 重建为按月
# RANGE 分区表）生产执行前的预检（batch4 任务 #4）。
#
# 检查项（任一不满足即 exit 非零并打印原因）：
#   1. PG 版本：server_version_num >= 140000（分区 DDL 语义稳定，项目要求 PG18，14 为硬下限）
#   2. 目标表存在且尚未分区（已分区→提示跳过，exit 0 明确输出）
#   3. 磁盘空间：表所在数据目录磁盘的可用空间 >= 表总大小 × 2
#      （0035 重建为 CREATE 新分区表 + 全量回填，旧表保留至行数校验通过）
#   4. 长事务：无运行超过 LONG_TXN_SECONDS（默认 300 秒）的事务
#      （长事务持有锁会阻塞迁移的 ACCESS EXCLUSIVE 锁，造成全库排队）
#   5. 复制槽：无 inactive（断连）复制槽；active 槽仅警告提示
#      （inactive 槽会在回填期间保留大量 WAL，有撑爆磁盘的风险）
#
# 连接：优先解析 QUANT_PG_CONNECTION（Npgsql 风格），否则用标准 PG* 环境变量。
# 退出码：0=全部通过；1=任一检查失败；2=用法/连接/查询错误。
set -euo pipefail

TABLE="${QUANT_PARTITION_TABLE:-native_monitor_samples}"
LONG_TXN_SECONDS="${QUANT_PARTITION_MAX_TXN_SECONDS:-300}"
PSQL_BIN="${QUANT_PSQL_BIN:-psql}"

usage() {
    cat <<'EOF'
用法：preflight-partition.sh [--table <表名>]
  对 0035 分区重建迁移做生产前预检。环境变量：
    QUANT_PARTITION_TABLE           目标表（默认 native_monitor_samples）
    QUANT_PARTITION_MAX_TXN_SECONDS 长事务阈值秒数（默认 300）
    QUANT_PSQL_BIN                  psql 路径（默认 psql）
    QUANT_PG_CONNECTION             Npgsql 风格连接串（优先）或 PG* 环境变量
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --table) TABLE="${2:?--table 需要参数}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "preflight-partition: 未知参数 '$1'" >&2; usage >&2; exit 2 ;;
    esac
done

# fail-closed：表名/阈值必须合法，拒绝注入。
if ! [[ "$TABLE" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo "preflight-partition: 非法表名 '$TABLE'" >&2
    exit 2
fi
if ! [[ "$LONG_TXN_SECONDS" =~ ^[0-9]+$ ]] || [ "$LONG_TXN_SECONDS" -lt 1 ]; then
    echo "preflight-partition: 非法长事务阈值 '$LONG_TXN_SECONDS'（需为 >=1 的整数）" >&2
    exit 2
fi

# 解析 Npgsql 风格连接串为 PG* 环境变量（大小写不敏感的键名）。
if [ -n "${QUANT_PG_CONNECTION:-}" ]; then
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
    echo "preflight-partition: 找不到 psql（QUANT_PSQL_BIN='$PSQL_BIN'）" >&2
    exit 2
}

# 单点查询失败（连接串错、权限不足等）直接报错退出（exit 2），不误判为检查失败。
q() {
    local out
    out="$("$PSQL_BIN" -X -q -t -A -v ON_ERROR_STOP=1 -c "$1" 2>&1)" || {
        echo "preflight-partition: 查询失败（连接/权限？）: $1" >&2
        echo "  详情: $(printf '%s\n' "$out" | head -3)" >&2
        exit 2
    }
    printf '%s' "$out"
}

FAIL=0
fail() { echo "FAIL: $1" >&2; FAIL=1; }
pass() { echo "OK:   $1"; }

echo "preflight-partition: 目标表 public.${TABLE}，检查开始…"

# ---- 1. PG 版本 ----
VERSION_NUM="$(q "SELECT current_setting('server_version_num')::bigint;")"
if [ "$VERSION_NUM" -lt 140000 ]; then
    fail "PG 版本过低（server_version_num=${VERSION_NUM}，要求 >= 140000）"
else
    pass "PG 版本满足（server_version_num=${VERSION_NUM}）"
fi

# ---- 2. 表存在性与分区状态 ----
EXISTS="$(q "SELECT to_regclass('public.${TABLE}') IS NOT NULL;")"
if [ "$EXISTS" != "t" ]; then
    fail "表 public.${TABLE} 不存在，无法评估重建风险（请先确认数据库与连接串）"
    echo "preflight-partition: 预检未通过，禁止执行 0035 迁移。" >&2
    exit 1
fi
IS_PART="$(q "SELECT EXISTS (SELECT 1 FROM pg_partitioned_table pt JOIN pg_class c ON c.oid = pt.partrelid WHERE c.relname = '${TABLE}' AND c.relnamespace = 'public'::regnamespace);")"
if [ "$IS_PART" = "t" ]; then
    echo "SKIP:  public.${TABLE} 已是分区表，0035 无需执行，预检不再继续。"
    exit 0
fi
pass "表存在且尚未分区"

# ---- 3. 磁盘空间 >= 表大小 × 2 ----
TABLE_BYTES="$(q "SELECT pg_total_relation_size('public.${TABLE}');")"
DATA_DIR="$(q "SHOW data_directory;")"
FREE_KB="$(df -k --output=avail "$DATA_DIR" 2>/dev/null | tail -1 | tr -d ' ')"
NEED_KB=$(( (TABLE_BYTES / 1024) * 2 ))
if [ -z "$FREE_KB" ] || ! [[ "$FREE_KB" =~ ^[0-9]+$ ]]; then
    fail "无法读取数据目录磁盘可用空间（data_directory=${DATA_DIR}）"
elif [ "$FREE_KB" -lt "$NEED_KB" ]; then
    fail "磁盘空间不足：表总大小 ${TABLE_BYTES} 字节，要求可用 >= $(( NEED_KB * 1024 )) 字节（2 倍），实际可用 ${FREE_KB} KiB"
else
    pass "磁盘空间满足（可用 ${FREE_KB} KiB，需求 >= ${NEED_KB} KiB）"
fi

# ---- 4. 长事务 ----
LONG_TXN="$(q "SELECT count(*) FROM pg_stat_activity WHERE xact_start IS NOT NULL AND now() - xact_start > make_interval(secs => ${LONG_TXN_SECONDS}) AND pid <> pg_backend_pid();")"
if [ "$LONG_TXN" != "0" ]; then
    fail "存在 ${LONG_TXN} 个运行超过 ${LONG_TXN_SECONDS} 秒的事务（会阻塞迁移的 ACCESS EXCLUSIVE 锁）：$(q "SELECT string_agg(pid::text, ',') FROM pg_stat_activity WHERE xact_start IS NOT NULL AND now() - xact_start > make_interval(secs => ${LONG_TXN_SECONDS}) AND pid <> pg_backend_pid();")"
else
    pass "无超过 ${LONG_TXN_SECONDS} 秒的长事务"
fi

# ---- 5. 复制槽 ----
INACTIVE_SLOTS="$(q "SELECT coalesce(string_agg(slot_name, ', '), '') FROM pg_replication_slots WHERE NOT active;")"
if [ -n "$INACTIVE_SLOTS" ]; then
    fail "存在 inactive 复制槽（${INACTIVE_SLOTS}）：回填期间的 WAL 会被长期保留，有撑爆磁盘风险；请先修复或删除后再执行"
else
    ACTIVE_COUNT="$(q "SELECT count(*) FROM pg_replication_slots;")"
    pass "无 inactive 复制槽（active 槽 ${ACTIVE_COUNT} 个，回填期请监控 WAL 增长）"
fi

if [ "$FAIL" -ne 0 ]; then
    echo "preflight-partition: 预检未通过，禁止执行 0035 迁移。" >&2
    exit 1
fi
echo "preflight-partition: 全部检查通过，可以进入停机窗口执行 0035。"
