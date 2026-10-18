#!/usr/bin/env bash
# Use the native archive checks; PostgreSQL cases require QUANT_TEST_PG_DSN.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${QUANT_PYTHON:-python3}"
exec "$PYTHON" -m unittest discover -s "$SCRIPT_DIR/../tools/tests" -v
