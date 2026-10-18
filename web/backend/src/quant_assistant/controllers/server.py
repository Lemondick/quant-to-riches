"""Loopback-only Uvicorn launcher."""

from __future__ import annotations

import uvicorn

from quant_assistant.config import Settings
from quant_assistant.controllers.auth_app import create_authenticated_app


def serve_local_api(port: int = 8765) -> None:
    """Serve the API on loopback; callers cannot override the host."""

    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port_out_of_range")
    uvicorn.run(create_authenticated_app(Settings.from_env()), host="127.0.0.1", port=port)
