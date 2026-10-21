"""fund.catalog payload validation (``fund.catalog_validate``).

Task 15: worker-side replacement for the per-element check that E8 moved
out of the 0024 trigger (``web/backend-dotnet/migrations/
0024_public_fund_catalog.sql``). Stream A calls this op before writing
``fund.catalog``.

Contract (batch2 §2): payload ``{items: [...]}`` →
``{ok: bool, errors: [{index, message}]}``.

The predicate below is a line-by-line transcription of the trigger's
``fund.catalog`` branch::

    jsonb_array_length(items) BETWEEN 1 AND 50000
    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(items) item
        WHERE jsonb_typeof(item)<>'object'
           OR item - ARRAY['symbol','name','fund_type']<>'{}'::jsonb
           OR COALESCE(item->>'symbol','') !~ '^[0-9]{6}$'
           OR COALESCE(item->>'name','')='')

Notes on the transcription:
- ``item - ARRAY[...]`` removes keys: the check rejects *extra* keys only;
  a missing ``fund_type`` passes the trigger (only ``symbol``/``name`` are
  re-checked via the COALESCE clauses), so this port allows it too.
- ``item->>'symbol'`` renders any JSON scalar as text (numbers included);
  the port applies the regex to ``str(value)`` (``None`` → ``''``), which
  is the same predicate. The POSIX ``^[0-9]{6}$`` is written ``\\A[0-9]{6}\\Z``
  to avoid Python ``$`` matching before a trailing newline.
- The trigger's header clauses (schema_version/source/quality_status/
  source_time/fetched_at) belong to the snapshot envelope, not the catalog
  content; this op validates ``payload["items"]`` only, so both
  ``{items: [...]}`` and the full result_json shape are accepted.
- Top-level errors use ``index: -1``; element errors use the element index.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

CATALOG_VALIDATE_CAPABILITIES = ("fund.catalog_validate",)

# Verbatim from 0024 (POSIX spelling); see module docstring for the \A..\Z note.
_SYMBOL_RE = re.compile(r"\A[0-9]{6}\Z")
_ALLOWED_KEYS = frozenset({"symbol", "name", "fund_type"})
_MAX_ITEMS = 50000


def _element_error(index: int, item: object) -> str | None:
    """Transcription of the trigger's ``WHERE`` disjunction; None = valid."""
    # jsonb_typeof(item) <> 'object'
    if not isinstance(item, Mapping):
        return "item must be an object"
    # item - ARRAY['symbol','name','fund_type'] <> '{}'::jsonb  (extra keys)
    extra = set(item.keys()) - _ALLOWED_KEYS
    if extra:
        return f"unexpected keys: {sorted(extra)}"
    # COALESCE(item->>'symbol','') !~ '^[0-9]{6}$'
    symbol = item.get("symbol")
    if _SYMBOL_RE.fullmatch("" if symbol is None else str(symbol)) is None:
        return "symbol must match ^[0-9]{6}$"
    # COALESCE(item->>'name','') = ''
    name = item.get("name")
    if ("" if name is None else str(name)) == "":
        return "name must be non-empty"
    return None


def catalog_validate(payload: Mapping) -> dict:
    """Validate ``payload["items"]`` per the 0024 trigger predicate."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    items = payload.get("items")
    errors: list[dict] = []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        errors.append({"index": -1, "message": "items must be a list"})
    elif not 1 <= len(items) <= _MAX_ITEMS:
        errors.append(
            {"index": -1, "message": f"items length must be between 1 and {_MAX_ITEMS}"}
        )
    else:
        for index, item in enumerate(items):
            message = _element_error(index, item)
            if message is not None:
                errors.append({"index": index, "message": message})
    return {"ok": not errors, "errors": errors}


def execute_catalog_validate(kind: str, payload: dict) -> dict:
    if kind != "fund.catalog_validate":
        raise ValueError("unsupported_operation")
    return catalog_validate(payload)
