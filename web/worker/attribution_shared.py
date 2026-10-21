"""Shared attribution primitives.

Both ``attribution_operations`` and ``ledger_attribution`` need these helpers;
they live in this third module so neither module imports the other at module
level (the old function-level ``from ledger_attribution import ...`` dodge in
``execute_attribution`` is gone).
"""

from datetime import date
from decimal import Decimal, InvalidOperation


class EvidenceMissing(ValueError):
    pass


def _day(value):
    return date.fromisoformat(str(value)[:10])


def _finite_decimal(value):
    """Decimal(str(value)) that must be finite; raises ValueError otherwise.

    Shared by _amount and public_charts._number so the parse+finite check
    lives in one place. Callers map ValueError to their domain error.
    """
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as failure:
        raise ValueError(f"invalid decimal: {value!r}") from failure
    if not result.is_finite():
        raise ValueError(f"non-finite decimal: {value!r}")
    return result


def _amount(value):
    try:
        return _finite_decimal(value)
    except ValueError as failure:
        raise EvidenceMissing("标的金额或价格证据不是有效十进制数。") from failure


def _text(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
