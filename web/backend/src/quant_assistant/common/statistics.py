"""Numerical helpers shared by lightweight strategy feature builders."""

from __future__ import annotations

import numpy as np


def period_return(values: np.ndarray, sessions: int) -> float:
    """Return the simple return over ``sessions`` observations."""

    if sessions <= 0 or len(values) <= sessions:
        raise ValueError("sessions must be positive and available in values")
    return float(values[-1] / values[-sessions - 1] - 1.0)


def annualized_volatility(values: np.ndarray, *, lookback: int = 21) -> float:
    """Annualize sample volatility of simple returns over the trailing window."""

    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    window = values[-lookback:]
    returns = np.diff(window) / window[:-1]
    return float(np.std(returns, ddof=1) * np.sqrt(252)) if len(returns) > 1 else 0.0


__all__ = ["annualized_volatility", "period_return"]
