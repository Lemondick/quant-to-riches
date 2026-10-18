#!/usr/bin/env sh
set -eu

DEPLOY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$DEPLOY_DIR/.." && pwd)
ENV_FILE=${ENV_FILE:-"$DEPLOY_DIR/.env"}
MODE=apply

for argument in "$@"; do
  case "$argument" in
    --check) MODE=check ;;
    --apply) MODE=apply ;;
    *) printf 'Unknown argument: %s\n' "$argument" >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker is required. Install Docker Engine with the Compose plugin first.\n' >&2
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  printf 'Missing %s. Copy deploy/.env.example and set QUANT_DOMAIN first.\n' "$ENV_FILE" >&2
  exit 2
fi

domain=$(sed -n 's/^QUANT_DOMAIN=//p' "$ENV_FILE" | tail -n 1 | tr -d '\r')
case "$domain" in
  ''|quant.example.com|*' '*|http://*|https://*|*/*)
    printf 'QUANT_DOMAIN must be a real DNS hostname without scheme or path.\n' >&2
    exit 2
    ;;
esac

docker compose --env-file "$ENV_FILE" -f "$DEPLOY_DIR/docker-compose.yml" config --quiet
printf 'Compose configuration is valid for %s.\n' "$domain"
if [ "$MODE" = check ]; then
  exit 0
fi

docker compose --env-file "$ENV_FILE" -f "$DEPLOY_DIR/docker-compose.yml" up -d api caddy
docker compose --env-file "$ENV_FILE" -f "$DEPLOY_DIR/docker-compose.yml" ps
printf 'HTTPS deployment started. Check https://%s/healthz after DNS and ports 80/443 are ready.\n' "$domain"
printf 'Initialize the administrator interactively: docker compose --env-file %s -f %s exec api uv run python scripts/manage_users.py init-admin\n' "$ENV_FILE" "$DEPLOY_DIR/docker-compose.yml"
