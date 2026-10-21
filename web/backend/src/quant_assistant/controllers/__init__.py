"""Versioned, local-only read API.

The FastAPI application factory is imported lazily (PEP 562) so that
pure-computation consumers -- the Python worker only needs
``quant_assistant.controllers.schemas`` -- can import this package without
pulling the FastAPI/starlette/uvicorn stack into the process. Importing a
submodule (``from quant_assistant.controllers import schemas`` or
``from quant_assistant.controllers.schemas import X``) never triggers the
lazy attribute loader; only ``create_app`` / ``serve_local_api`` do.
"""

from __future__ import annotations

__all__ = ["create_app", "serve_local_api"]


def __getattr__(name):
    if name == "create_app":
        from quant_assistant.controllers.app import create_app

        return create_app
    if name == "serve_local_api":
        from quant_assistant.controllers.server import serve_local_api

        return serve_local_api
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
