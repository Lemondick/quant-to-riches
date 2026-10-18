"""Versioned, local-only read API."""

from quant_assistant.controllers.app import create_app
from quant_assistant.controllers.server import serve_local_api

__all__ = ["create_app", "serve_local_api"]
