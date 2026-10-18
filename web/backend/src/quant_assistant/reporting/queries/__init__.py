"""Read-only projections over persisted daily decisions.

Public surface kept on a single ``DecisionQueries`` class so existing imports
(``from quant_assistant.reporting.queries import DecisionQueries``) continue
to work.  Internally the implementation is split into small mixins grouped by
domain: latest decision / alerts (``_decisions``), positions and ledger
(``_portfolio``), operational health (``_operations``), fund evidence
(``_funds``), research coverage (``_research``).  Helper coercion functions
and Row→domain serializers live in ``_helpers`` / ``_serializers``.
"""

from __future__ import annotations

from ._base import DatabaseUnavailableError
from ._decisions import _DecisionMixin
from ._funds import _FundsMixin
from ._operations import _OperationsMixin
from ._portfolio import _PortfolioMixin
from ._research import _ResearchMixin

__all__ = ["DatabaseUnavailableError", "DecisionQueries"]


class DecisionQueries(
    _DecisionMixin,
    _PortfolioMixin,
    _OperationsMixin,
    _FundsMixin,
    _ResearchMixin,
):
    """The only DuckDB-aware read interface consumed by the local dashboard."""
