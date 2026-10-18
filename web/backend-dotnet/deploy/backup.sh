#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${QUANT_PYTHON:-python3}"
exec "$PYTHON" "$SCRIPT_DIR/../tools/site_archive.py" backup \
  --site-root "${QUANT_SITE_ROOT:?Set QUANT_SITE_ROOT}" --output "${1:?Usage: backup.sh <archive-directory>}"
