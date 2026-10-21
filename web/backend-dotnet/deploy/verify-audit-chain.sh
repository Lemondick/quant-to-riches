#!/usr/bin/env bash
# verify-audit-chain.sh — 独立验证 admin_audit 哈希链完整性（任务12）。
#
# 口径（与 0036_audit_hash_chain.sql 文件头一致，A 流写入端必须逐字遵守）：
#   preimage = prev_hash || actor_id || action || target_id || detail || created_at_utc
#   NULL 一律视为空字符串；无分隔符直接拼接；
#   created_at_utc = to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')；
#   hash = 小写 hex(SHA256(UTF-8(preimage)))。创世行（id 最小）prev_hash 为 ''。
#
# 实现要点：preimage 的拼接与 base64 在 SQL 侧完成（字节精确，避免 shell 解析
# 换行/特殊字符破坏哈希输入）；shell 只做 base64 解码 + sha256sum 重算比对，
# 并检查 row[i].prev_hash == row[i-1].hash 的链连续性。
#
# 连接：优先解析 QUANT_PG_CONNECTION（Npgsql 风格），否则用 PG* 环境变量。
# 退出码：0=链完整；1=断裂或哈希不匹配（--strict 下遗留未入链行亦视为失败）；
#         2=用法/连接/查询错误。
set -euo pipefail

STRICT=0
usage() {
    cat <<'EOF'
用法：verify-audit-chain.sh [--strict]
  --strict  hash 为 NULL 的遗留行（0036 之前写入）也视为失败；默认仅警告。
环境变量：QUANT_PG_CONNECTION 或 PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD。
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --strict) STRICT=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "verify-audit-chain: 未知参数 '$1'" >&2; usage >&2; exit 2 ;;
    esac
done

PSQL_BIN="${QUANT_PSQL_BIN:-psql}"

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
    echo "verify-audit-chain: 找不到 psql（QUANT_PSQL_BIN='$PSQL_BIN'）" >&2
    exit 2
}

PSQL=( "$PSQL_BIN" -X -q -A -t -v ON_ERROR_STOP=1 )

# 遗留行：0036 之前写入、尚未入链（hash IS NULL）。默认警告，--strict 下失败。
if ! LEGACY="$("${PSQL[@]}" -c "SELECT count(*) FROM public.admin_audit WHERE hash IS NULL;")"; then
    echo "verify-audit-chain: 查询失败（admin_audit 表不存在或 0036 未应用？）" >&2
    exit 2
fi
LEGACY="$(printf '%s' "$LEGACY" | tr -d '[:space:]')"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

"${PSQL[@]}" -F $'\x1f' -c "
SELECT id,
       COALESCE(prev_hash, ''),
       -- 注意：PG 的 encode(x,'base64') 每 76 字符插入换行，须去掉，否则破坏行解析
       replace(encode(convert_to(
           COALESCE(prev_hash, '') || COALESCE(actor_id, '') || COALESCE(action, '')
           || COALESCE(target_id, '') || COALESCE(detail, '')
           || to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'),
           'UTF8'), 'base64'), chr(10), ''),
       hash
FROM public.admin_audit
WHERE hash IS NOT NULL
ORDER BY id;" > "$TMP" || {
    echo "verify-audit-chain: 读取审计链失败" >&2
    exit 2
}

expected_prev=""
total=0
broken=0
while IFS=$'\x1f' read -r id prev_hash preimage_b64 hash; do
    if [ -z "$id" ]; then
        continue
    fi
    total=$((total + 1))
    if [ "$prev_hash" != "$expected_prev" ]; then
        echo "verify-audit-chain: 链断裂 id=${id}（prev_hash 与上一条 hash 不一致）" >&2
        broken=1
    fi
    recomputed="$(printf '%s' "$preimage_b64" | base64 -d | sha256sum | cut -d' ' -f1)"
    if [ "$recomputed" != "$hash" ]; then
        echo "verify-audit-chain: 哈希不匹配 id=${id}" >&2
        broken=1
    fi
    expected_prev="$hash"
done < "$TMP"

if [ "$LEGACY" -gt 0 ]; then
    if [ "$STRICT" -eq 1 ]; then
        echo "verify-audit-chain: 存在 ${LEGACY} 条未入链遗留行（--strict 视为失败）" >&2
        broken=1
    else
        echo "verify-audit-chain: 警告：${LEGACY} 条遗留行未入链（hash IS NULL），已跳过验算" >&2
    fi
fi

if [ "$broken" -ne 0 ]; then
    echo "verify-audit-chain: FAIL（已验 ${total} 条）" >&2
    exit 1
fi
echo "verify-audit-chain: OK（已验 ${total} 条，遗留未入链 ${LEGACY} 条）"
