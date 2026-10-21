#!/usr/bin/env bash
# 加密异地备份同步模板（F2）。
#
# 启用步骤（详见 ubuntu-runbook.md §6 / web/docs/维护说明.md §7）：
#   1. 安装 rclone 并配置远端：rclone config  （如 S3/OSS/网盘/WebDAV）
#   2. 生成 age 密钥：age-keygen -o /root/.age-backup-key.txt
#      （私钥离线保管，加密只需公钥）
#   3. 复制本文件为可执行脚本（建议 /usr/local/lib/quant/offsite.sh），
#      填好下面的 REMOTE 与 AGE_RECIPIENT。
#   4. 在 /etc/quant/environment 追加：
#        QUANT_BACKUP_OFFSITE_ENABLED=true
#   5. 用 systemd timer 或 cron 定期执行（如每周一次）：
#        0 6 * * 0 root /usr/local/lib/quant/offsite.sh
#   6. 定期做一次异地恢复演练：下载归档 → age 解密 → restore 到隔离环境。
#
# 安全原则：只上传加密副本，绝不上传明文 quant-site-*.zip。
set -euo pipefail

# 默认禁用：用户填好目标并显式启用后才执行。
if [ "${QUANT_BACKUP_OFFSITE_ENABLED:-false}" != "true" ]; then
  echo "offsite sync disabled (set QUANT_BACKUP_OFFSITE_ENABLED=true to enable)"
  exit 0
fi

BACKUP_DIR="${QUANT_BACKUP_DIR:?Set QUANT_BACKUP_DIR}"

# ===== 用户填写区 =====
REMOTE="${QUANT_OFFSITE_REMOTE:?Set QUANT_OFFSITE_REMOTE, e.g. myremote:quant-backups}"
AGE_RECIPIENT="${QUANT_BACKUP_AGE_RECIPIENT:?Set QUANT_BACKUP_AGE_RECIPIENT (age public key)}"
# 若用 gpg 而不用 age，改走下面的 GPG 分支并设置 QUANT_BACKUP_GPG_RECIPIENT。
# =====================

command -v rclone >/dev/null 2>&1 || { echo "offsite: rclone 未安装" >&2; exit 1; }
command -v age >/dev/null 2>&1 || { echo "offsite: age 未安装" >&2; exit 1; }

LATEST="$(ls -t "$BACKUP_DIR"/quant-site-*.zip 2>/dev/null | head -n 1 || true)"
[ -n "$LATEST" ] || { echo "offsite: 归档目录无备份" >&2; exit 1; }

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
ENCRYPTED="$TMPDIR/$(basename "$LATEST").age"
age -r "$AGE_RECIPIENT" -o "$ENCRYPTED" "$LATEST"
chmod 600 "$ENCRYPTED"

# 上传加密副本；rclone 返回非零则 trap 清理临时目录并报错退出。
rclone copyto "$ENCRYPTED" "$REMOTE/$(basename "$ENCRYPTED")"
# 校验远端副本存在且大小一致。
rclone lsf "$REMOTE" | grep -qx "$(basename "$ENCRYPTED")" \
  || { echo "offsite: 远端校验失败" >&2; exit 1; }
echo "offsite: 已同步 $(basename "$ENCRYPTED") -> $REMOTE"
