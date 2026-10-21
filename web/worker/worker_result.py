"""Shared child-operation result envelope for the worker boundary.

Cross-stream contract (section 4): the worker returns
``{"result", "error_code"}`` dicts. ``child_operation`` (run_worker) and
``child_public_operation`` (public_operations) shared this mapping logic;
it lives here now.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

# Raised for malformed caller input (missing keys, bad shapes, bad values).
INVALID_REQUEST_ERRORS = (ValueError, TypeError, KeyError, ValidationError)


def child_envelope(compute, pre_handlers=()):
    """Run ``compute()`` and map exceptions to the contract error codes.

    ``pre_handlers`` is a sequence of ``(exception types, handler)`` pairs
    tried before the default mapping, for operation-specific codes.
    ``json.dumps`` stays inside the ``try``: an unserializable result was an
    invalid request in the old mapping and stays one.
    """
    try:
        result = compute()
        json.dumps(result, allow_nan=False)
        return {"result": result, "error_code": None}
    except Exception as error:  # noqa: BLE001 - details stay redacted at this boundary
        for exc_types, handler in pre_handlers:
            if isinstance(error, exc_types):
                return handler(error)
        if isinstance(error, INVALID_REQUEST_ERRORS):
            return {"result": None, "error_code": "invalid_request"}
        return {"result": None, "error_code": "worker_computation_failed"}
