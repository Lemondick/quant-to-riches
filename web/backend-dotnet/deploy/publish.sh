#!/usr/bin/env bash
# Publish a new immutable release. This does not switch or restart a live site.
set -euo pipefail
REPO_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
[[ $# == 1 ]] || { printf 'Usage: publish.sh NEW_RELEASE_DIRECTORY\n' >&2; exit 2; }
RELEASE_PATH="$(realpath -m -- "$1")"
case "$RELEASE_PATH" in "$REPO_PATH"|"$REPO_PATH"/*) printf 'Publish outside the checkout.\n' >&2; exit 2;; esac
[[ ! -e "$RELEASE_PATH" ]] || { printf 'A new release directory is required.\n' >&2; exit 2; }
umask 077
mkdir -p -- "$RELEASE_PATH"
dotnet publish "$REPO_PATH/web/backend-dotnet/src/QuantAssistant.Api" -c Release -p:UseAppHost=false -p:ExportOpenApi=true -o "$RELEASE_PATH/api"
# E13：把构建期导出的版本化 OpenAPI 产物一并带入发布包（D 的 API 控制台 / C 的 CI 校验入口）。
cp -a -- "$REPO_PATH/web/backend-dotnet/artifacts" "$RELEASE_PATH/api/artifacts"
(
  cd -- "$REPO_PATH/web/frontend"
  npm ci
  npm test
  npm run typecheck
  VITE_API_BASE_URL="${VITE_API_BASE_URL:-/api/v1}" QUANT_WEB_OUTPUT_DIR="$RELEASE_PATH/frontend" npm run build
)
cp -a -- "$RELEASE_PATH/frontend/build" "$RELEASE_PATH/api/wwwroot"
for part in migrations tools deploy; do cp -a -- "$REPO_PATH/web/backend-dotnet/$part" "$RELEASE_PATH/$part"; done
cp -a -- "$REPO_PATH/web/worker" "$RELEASE_PATH/worker"
mkdir -- "$RELEASE_PATH/backend"
for part in pyproject.toml uv.lock src; do cp -a -- "$REPO_PATH/web/backend/$part" "$RELEASE_PATH/backend/$part"; done
uv sync --project "$RELEASE_PATH/backend" --frozen --no-dev --python 3.12
printf 'Published release: %s\n' "$RELEASE_PATH"
