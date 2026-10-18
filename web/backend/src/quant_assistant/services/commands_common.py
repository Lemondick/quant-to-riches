"""Shared validation primitives for command services."""

from __future__ import annotations

from decimal import Decimal


class LedgerStateError(ValueError):
    """A rejected ledger operation with a safe, fixed API error code."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def require_text(
    value: object,
    label: str,
    *,
    limit: int | None = None,
    coerce: bool = False,
) -> str:
    normalized = str(value or "").strip() if coerce else value
    if not isinstance(normalized, str) or not normalized:
        raise ValueError(f"{label}不能为空")
    if limit is not None and len(normalized) > limit:
        raise ValueError(f"{label}不能超过{limit}个字符")
    return normalized


def require_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label}必须使用Decimal")
    if not value.is_finite():
        raise ValueError(f"{label}必须是有限数")
    # DuckDB 1.5.x binds Decimal('1E+2') as 1. Re-express positive
    # exponents exactly before any command binds a quantity or money value.
    if value.as_tuple().exponent > 0:
        return Decimal(format(value, "f"))
    return value


__all__ = ["require_decimal", "require_text"]
