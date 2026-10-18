#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${QUANT_PYTHON:-python3}"
exec "$PYTHON" "$SCRIPT_DIR/../tools/site_archive.py" restore \
  "${1:?Usage: restore.sh <archive.zip> <new-site-directory>}" --destination "${2:?Set a new site directory}"
