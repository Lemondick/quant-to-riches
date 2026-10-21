#!/usr/bin/env bash
# rotate-backup-keys.sh — 备份加密密钥轮换（batch4 任务 #10，age 方案）。
#
# 背景：backup.sh / offsite.sh 用 age 公钥加密备份归档（*.age），私钥离线保管。
# 密钥长期不换会放大"私钥泄露影响期"；本脚本做一次轮换：
#   1. 生成新 age 密钥对；
#   2. 用旧私钥解密最新全量备份的 *.age；
#   3. 用新公钥重加密，明文 sha256 必须与旧解密明文一致（校验）；
#   4. 旧私钥移入归档目录（不删除，600 权限）；
#   5. 新 *.age 就位（旧 *.age 保留为 *.age.prev-<时间戳>，供回滚）；
#   6. 输出轮换报告（stdout + 备份目录下的 key-rotation-<时间戳>.report.txt）。
#
# 注意：
# - 只轮换"最新"全量备份的加密副本；历史归档仍用旧密钥加密——这正是旧密钥
#   要归档保留（而非删除）的原因。
# - 轮换后必须：① 更新 /etc/quant/environment 的 QUANT_BACKUP_AGE_RECIPIENT
#   为新公钥；② 把新私钥离线保管（参考 offsite.example.sh 的安全原则）；
#   ③ 重跑一次 offsite 同步，把新加密副本送上远端。
# - gpg 方案：本脚本只处理 age；gpg 密钥轮换见 web/docs/维护说明.md §7（手工步骤）。
#
# 用法：rotate-backup-keys.sh [--dry-run] [--key-file PATH] [--archive-dir PATH] [--backup-dir PATH]
# 环境：QUANT_BACKUP_DIR（必填，也可用 --backup-dir）
#      QUANT_BACKUP_KEY_FILE（默认 /root/.age-backup-key.txt，当前私钥）
#      QUANT_BACKUP_KEY_ARCHIVE_DIR（默认 /root/backup-keys-archive，旧密钥归档目录）
# 退出码：0=成功；1=轮换失败；2=用法/环境错误。
set -euo pipefail

DRY_RUN=0
BACKUP_DIR="${QUANT_BACKUP_DIR:-}"
KEY_FILE="${QUANT_BACKUP_KEY_FILE:-/root/.age-backup-key.txt}"
ARCHIVE_DIR="${QUANT_BACKUP_KEY_ARCHIVE_DIR:-/root/backup-keys-archive}"

usage() {
    cat <<'EOF'
用法：rotate-backup-keys.sh [--dry-run] [--key-file PATH] [--archive-dir PATH] [--backup-dir PATH]
  --dry-run      只做解密→重加密→校验并输出报告，不改动任何文件
  --key-file     当前 age 私钥路径（默认 $QUANT_BACKUP_KEY_FILE 或 /root/.age-backup-key.txt）
  --archive-dir  旧密钥归档目录（默认 $QUANT_BACKUP_KEY_ARCHIVE_DIR 或 /root/backup-keys-archive）
  --backup-dir   备份归档目录（默认 $QUANT_BACKUP_DIR）
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --key-file) KEY_FILE="${2:?--key-file 需要参数}"; shift 2 ;;
        --archive-dir) ARCHIVE_DIR="${2:?--archive-dir 需要参数}"; shift 2 ;;
        --backup-dir) BACKUP_DIR="${2:?--backup-dir 需要参数}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "rotate-backup-keys: 未知参数 '$1'" >&2; usage >&2; exit 2 ;;
    esac
done

[ -n "$BACKUP_DIR" ] || { echo "rotate-backup-keys: 未设置备份目录（QUANT_BACKUP_DIR 或 --backup-dir）" >&2; exit 2; }
[ -d "$BACKUP_DIR" ] || { echo "rotate-backup-keys: 备份目录不存在：$BACKUP_DIR" >&2; exit 2; }
[ -f "$KEY_FILE" ] || { echo "rotate-backup-keys: 当前私钥不存在：$KEY_FILE" >&2; exit 2; }
[ -r "$KEY_FILE" ] || { echo "rotate-backup-keys: 当前私钥不可读：$KEY_FILE" >&2; exit 2; }
for tool in age age-keygen unzip sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || { echo "rotate-backup-keys: 缺少工具：$tool" >&2; exit 2; }
done

# 最新全量备份（按 mtime）。
LATEST_ZIP="$(ls -t "$BACKUP_DIR"/quant-site-*.zip 2>/dev/null | head -n 1 || true)"
[ -n "$LATEST_ZIP" ] || { echo "rotate-backup-keys: 备份目录无 quant-site-*.zip 全量备份" >&2; exit 1; }
if [ ! -f "${LATEST_ZIP}.age" ]; then
    if [ -f "${LATEST_ZIP}.gpg" ]; then
        echo "rotate-backup-keys: 最新备份只有 .gpg 加密副本；gpg 密钥轮换请按维护说明.md §7 手工执行，本脚本只处理 age。" >&2
        exit 2
    fi
    echo "rotate-backup-keys: 最新备份 ${LATEST_ZIP} 没有加密副本（.age）；请先用 backup.sh（QUANT_BACKUP_AGE_RECIPIENT）生成加密副本再轮换。" >&2
    exit 1
fi
LATEST_AGE="${LATEST_ZIP}.age"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
NEW_KEY_TMP="$TMPDIR/new-key.txt"
OLD_PLAIN="$TMPDIR/old-plain.zip"
NEW_ENC="$TMPDIR/new.age"
NEW_PLAIN="$TMPDIR/new-plain.zip"

echo "rotate-backup-keys: 目标 ${LATEST_AGE}（dry-run=${DRY_RUN}）"

# 1. 生成新密钥对。
age-keygen -o "$NEW_KEY_TMP" 2>/dev/null
chmod 600 "$NEW_KEY_TMP"
NEW_PUB="$(awk '/# public key:/{print $4}' "$NEW_KEY_TMP")"
[ -n "$NEW_PUB" ] || { echo "rotate-backup-keys: 新密钥公钥提取失败" >&2; exit 1; }
OLD_PUB="$(age-keygen -y "$KEY_FILE" 2>/dev/null || true)"
echo "rotate-backup-keys: 旧公钥 ${OLD_PUB:-<未知>} → 新公钥 ${NEW_PUB}"

# 2. 用旧私钥解密。
age -d -i "$KEY_FILE" -o "$OLD_PLAIN" "$LATEST_AGE"
# 3. 校验旧明文（zip 完整性）。
unzip -t "$OLD_PLAIN" >/dev/null || { echo "rotate-backup-keys: 旧密钥解密出的明文 zip 校验失败，停止轮换" >&2; exit 1; }
OLD_SHA="$(sha256sum "$OLD_PLAIN" | cut -d' ' -f1)"

# 4. 用新公钥重加密。
age -r "$NEW_PUB" -o "$NEW_ENC" "$OLD_PLAIN"
chmod 600 "$NEW_ENC"

# 5. 校验新加密副本：用新私钥解密，明文 sha256 必须一致 + zip 完整。
age -d -i "$NEW_KEY_TMP" -o "$NEW_PLAIN" "$NEW_ENC"
NEW_SHA="$(sha256sum "$NEW_PLAIN" | cut -d' ' -f1)"
[ "$OLD_SHA" = "$NEW_SHA" ] || { echo "rotate-backup-keys: 新旧明文 sha256 不一致（旧=${OLD_SHA} 新=${NEW_SHA}），停止轮换" >&2; exit 1; }
unzip -t "$NEW_PLAIN" >/dev/null || { echo "rotate-backup-keys: 新加密副本解密后 zip 校验失败，停止轮换" >&2; exit 1; }
echo "rotate-backup-keys: 校验通过（明文 sha256=${OLD_SHA:0:16}…一致）"

REPORT="$BACKUP_DIR/key-rotation-${TS}.report.txt"
write_report() {
    {
        echo "备份密钥轮换报告（age）"
        echo "时间（UTC）：${TS}"
        echo "操作者：$(whoami)"
        echo "模式：$([ "$DRY_RUN" = 1 ] && echo "dry-run（未做任何修改）" || echo "正式轮换")"
        echo "备份归档：${LATEST_ZIP}"
        echo "旧加密副本：${LATEST_AGE}"
        echo "旧公钥：${OLD_PUB:-<未知>}"
        echo "新公钥：${NEW_PUB}"
        echo "明文 sha256：${OLD_SHA}"
        echo "校验：旧解密 unzip -t 通过；新加密→新解密 sha256 一致；新解密 unzip -t 通过"
        if [ "$DRY_RUN" = 1 ]; then
            echo "dry-run 结论：全部检查通过，可执行正式轮换。"
        else
            echo "旧 *.age 保留为：${LATEST_AGE}.prev-${TS}"
            echo "旧私钥归档为：${ARCHIVE_DIR}/$(basename "$KEY_FILE").${TS}（未删除，600 权限）"
            echo "当前私钥：${KEY_FILE}（已更新为新密钥，600 权限）"
            echo "后续必须做："
            echo "  1. 更新 /etc/quant/environment 的 QUANT_BACKUP_AGE_RECIPIENT=${NEW_PUB}"
            echo "  2. 把新私钥 ${KEY_FILE} 离线保管（参考 offsite.example.sh 安全原则）"
            echo "  3. 重跑 offsite 同步，把新加密副本送上远端"
            echo "  4. 做一次异地恢复演练验证新密钥可用"
        fi
    } | tee "$1"
}

if [ "$DRY_RUN" = 1 ]; then
    write_report "$REPORT"
    echo "rotate-backup-keys: dry-run 通过，未修改任何文件。"
    exit 0
fi

# 6. 正式切换（先换钥后换文件：任何一步失败，旧 .age.prev + 归档旧钥仍可回滚）。
mkdir -p "$ARCHIVE_DIR"
chmod 700 "$ARCHIVE_DIR"
ARCHIVED_KEY="${ARCHIVE_DIR}/$(basename "$KEY_FILE").${TS}"
mv "$KEY_FILE" "$ARCHIVED_KEY"
chmod 600 "$ARCHIVED_KEY"
install -m 600 "$NEW_KEY_TMP" "$KEY_FILE"
mv "$LATEST_AGE" "${LATEST_AGE}.prev-${TS}"
install -m 600 "$NEW_ENC" "$LATEST_AGE"

write_report "$REPORT"
echo "rotate-backup-keys: 轮换完成，报告：${REPORT}"
