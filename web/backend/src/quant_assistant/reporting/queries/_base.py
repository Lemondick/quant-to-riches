"""Shared base class for the read projection mixins.

Holds only the error type and the shared ``__init__`` + ``_query`` plumbing.
Every mixin inherits from ``_DecisionQueriesBase`` so ``self._query(...)``,
``self.database`` and the ``DatabaseUnavailableError`` re-export are always
available without circular imports between mixins.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb
import pandas as pd

from quant_assistant.models.repositories.database import Database

__all__ = ["DatabaseUnavailableError", "_DecisionQueriesBase"]


class DatabaseUnavailableError(ConnectionError):
    """The local DuckDB file cannot currently be opened or read."""


class _DecisionQueriesBase:
    """``DecisionQueries`` shared plumbing (constructor + low-level query).

    Mixins that implement the public read projections compose with this base
    via multiple inheritance.  ``DecisionQueries`` itself is assembled in
    ``reporting/queries/__init__.py``.
    """

    database_unavailable_error = DatabaseUnavailableError

    def __init__(self, database: Database | Path):
        self.database = database if isinstance(database, Database) else Database(database)

    def _query(self, sql: str, parameters: Sequence[object] = ()) -> pd.DataFrame:
        try:
            return self.database.query_df(sql, parameters)
        except (
            duckdb.ConnectionException,
            duckdb.IOException,
            duckdb.PermissionException,
        ) as exc:
            raise DatabaseUnavailableError("local database unavailable") from exc
