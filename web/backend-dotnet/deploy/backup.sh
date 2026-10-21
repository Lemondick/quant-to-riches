#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${QUANT_PYTHON:-python3}"
ARCHIVE_DIR="${1:?Usage: backup.sh <archive-directory>}"
NEW_ARCHIVE="$("$PYTHON" "$SCRIPT_DIR/../tools/site_archive.py" backup \
  --site-root "${QUANT_SITE_ROOT:?Set QUANT_SITE_ROOT}" --output "$ARCHIVE_DIR")"
echo "$NEW_ARCHIVE"

# 可选加密（F2）：设置 QUANT_BACKUP_AGE_RECIPIENT（age 公钥）或
# QUANT_BACKUP_GPG_RECIPIENT 后，为新归档在归档目录内生成加密副本
# （*.age / *.gpg），供异地同步脚本使用。本地 *.zip 保留不变，
# 管理页备份证据（NativeBackupEvidence）只读取 *.zip。
if [ -n "${QUANT_BACKUP_AGE_RECIPIENT:-}" ]; then
  command -v age >/dev/null 2>&1 || { echo "backup.sh: age 未安装，跳过加密" >&2; exit 1; }
  age -r "$QUANT_BACKUP_AGE_RECIPIENT" -o "${NEW_ARCHIVE}.age" "$NEW_ARCHIVE"
  chmod 600 "${NEW_ARCHIVE}.age"
  echo "encrypted: ${NEW_ARCHIVE}.age"
elif [ -n "${QUANT_BACKUP_GPG_RECIPIENT:-}" ]; then
  command -v gpg >/dev/null 2>&1 || { echo "backup.sh: gpg 未安装，跳过加密" >&2; exit 1; }
  gpg --batch --yes --trust-model always --recipient "$QUANT_BACKUP_GPG_RECIPIENT" \
    --output "${NEW_ARCHIVE}.gpg" --encrypt "$NEW_ARCHIVE"
  chmod 600 "${NEW_ARCHIVE}.gpg"
  echo "encrypted: ${NEW_ARCHIVE}.gpg"
fi
