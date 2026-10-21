FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_SYNC=1 \
    UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
COPY backend/src ./src
COPY backend/scripts ./scripts
COPY deploy /app/deploy
COPY Dockerfile .dockerignore start.ps1 /app/

RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin quant \
    && mkdir -p /var/lib/quant-assistant \
    && chown -R quant:quant /app /var/lib/quant-assistant

USER quant
ENV QUANT_DATA_DIR=/var/lib/quant-assistant \
    QUANT_ADVISOR_CONFIG=/var/lib/quant-assistant/private/advisor.json \
    QUANT_API_HOST=0.0.0.0 \
    QUANT_API_PORT=8765 \
    QUANT_REQUIRE_AUTH=true

EXPOSE 8765

CMD ["uv", "run", "quant-assistant", "serve-production"]
