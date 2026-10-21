#!/usr/bin/env bash
set -euo pipefail

# Install the full upstream graph into a dedicated environment. Do not install
# these dependencies into quant-to-riches' existing uv environment: mootdx and
# other upstream dependencies can have conflicting constraints.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMIT="e3981af1e4c94656cdc663f1466d54fd49253dd5"
PREFIX="${QUANT_TRADINGAGENTS_HOME:-/opt/quant-tradingagents}"
VENV="${QUANT_TRADINGAGENTS_VENV:-${PREFIX}/venv}"

if [[ ! -f "${ROOT}/tradingagents-worker/runner.py" ]]; then
  echo "TradingAgents runner.py not found under ${ROOT}" >&2
  exit 1
fi

python3 -m venv "${VENV}"
"${VENV}/bin/python" -m pip install --upgrade pip
"${VENV}/bin/pip" install "git+https://github.com/simonlin1212/TradingAgents-astock.git@${COMMIT}"
install -d -m 0750 "${PREFIX}"
install -m 0640 "${ROOT}/tradingagents-worker/runner.py" "${PREFIX}/runner.py"

cat <<EOF
Installed TradingAgents-Astock commit ${COMMIT}
Set these variables for the native worker:
  QUANT_TRADINGAGENTS_PYTHON=${VENV}/bin/python
  QUANT_TRADINGAGENTS_RUNNER=${PREFIX}/runner.py
Enable only after validating costs and data access:
  QUANT_TRADINGAGENTS_ENGINE=full
EOF
