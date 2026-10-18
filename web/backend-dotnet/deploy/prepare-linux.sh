#!/usr/bin/env bash
# Check an unpacked release; --sync prepares its locked Python environment.
set -euo pipefail
MODE="${1:---check}"
[[ $# -le 1 && ( "$MODE" == --check || "$MODE" == --sync ) ]] || {
  printf 'Usage: prepare-linux.sh [--check|--sync]\n' >&2; exit 2;
}
[[ "$(uname -s)" == Linux ]] || { printf 'Run this check on the target Linux host.\n' >&2; exit 2; }
case "$(uname -m)" in
  x86_64|aarch64) ;;
  *) printf 'Unsupported host architecture; validate its dependencies before deployment.\n' >&2; exit 2;;
esac
RELEASE_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
for part in api/QuantAssistant.Api.dll backend/pyproject.toml backend/uv.lock worker/run_worker.py migrations/0019_model_bill_evidence.sql; do
  [[ -f "$RELEASE_PATH/$part" ]] || { printf 'Incomplete release: %s\n' "$part" >&2; exit 2; }
done
if [[ -n "${QUANT_PG_BIN:-}" ]]; then export PATH="$QUANT_PG_BIN:$PATH"; fi
for command in dotnet uv pg_dump pg_restore psql curl systemctl caddy; do
  command -v "$command" >/dev/null || { printf 'Missing prerequisite: %s\n' "$command" >&2; exit 2; }
done
RUNTIMES="$(dotnet --list-runtimes)"
for runtime in Microsoft.NETCore.App Microsoft.AspNetCore.App; do
  [[ "$RUNTIMES" =~ $runtime\ 10\. ]] || { printf 'Missing .NET 10 runtime: %s\n' "$runtime" >&2; exit 2; }
done
for command in pg_dump pg_restore psql; do
  VERSION="$($command --version)"
  [[ "$VERSION" =~ PostgreSQL\)\ ([0-9]+) && "${BASH_REMATCH[1]}" -ge 18 ]] || {
    printf 'PostgreSQL client 18 or newer required: %s\n' "$command" >&2; exit 2;
  }
done
printf 'Host: %s / %s\nRelease: %s\n' "$(uname -s)" "$(uname -m)" "$RELEASE_PATH"
if [[ -r /etc/os-release ]]; then
  awk -F= '/^(NAME|VERSION_ID)=/ { print $0 }' /etc/os-release
fi
printf 'Prerequisites present; service, database, DNS and certificate checks remain.\n'
if [[ "$MODE" == --sync ]]; then
  [[ "$(id -u)" != 0 ]] || { printf 'Prepare Python as the release owner, not root.\n' >&2; exit 2; }
  [[ -w "$RELEASE_PATH/backend" ]] || { printf 'The release owner needs write access to backend.\n' >&2; exit 2; }
  umask 077
  uv sync --project "$RELEASE_PATH/backend" --frozen --no-dev --python 3.12
  "$RELEASE_PATH/backend/.venv/bin/python" -c 'import sys, psycopg2, duckdb; assert sys.version_info[:2] == (3, 12); print("Locked Python 3.12 environment ready")'
fi
