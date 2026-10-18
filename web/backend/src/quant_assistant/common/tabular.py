"""Small dataframe helpers shared by provider and strategy adapters."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


def first_column(frame: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    """Return the first candidate present in ``frame`` or ``None``.

    Provider schemas are allowed to use aliases; keeping this lookup in one
    place prevents each adapter from subtly diverging on empty/duplicate names.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    return next((name for name in candidates if name in frame.columns), None)


__all__ = ["first_column"]
