FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/tradingagents

# Install the pinned upstream into a separate virtual environment. This avoids
# dependency conflicts with quant-to-riches' own Python environment.
ARG TRADINGAGENTS_COMMIT=e3981af1e4c94656cdc663f1466d54fd49253dd5
RUN python -m venv /opt/tradingagents-venv \
    && /opt/tradingagents-venv/bin/pip install --no-cache-dir \
       "git+https://github.com/simonlin1212/TradingAgents-astock.git@${TRADINGAGENTS_COMMIT}"

COPY runner.py /opt/tradingagents/runner.py
RUN useradd --uid 10002 --create-home --shell /usr/sbin/nologin ta-runner \
    && mkdir -p /var/lib/tradingagents \
    && chown -R ta-runner:ta-runner /var/lib/tradingagents /opt/tradingagents

USER ta-runner
ENV HOME=/var/lib/tradingagents \
    TRADINGAGENTS_CACHE_DIR=/var/lib/tradingagents/cache \
    TRADINGAGENTS_RESULTS_DIR=/var/lib/tradingagents/results \
    TRADINGAGENTS_MEMORY_LOG_PATH=/var/lib/tradingagents/memory.md

ENTRYPOINT ["/opt/tradingagents-venv/bin/python", "-I", "/opt/tradingagents/runner.py"]
