"""Historical portfolio drawdown control-line validation (facade).

The implementation lives in :mod:`quant_assistant.portfolio.drawdown_control`
so the backtest layer can depend on it without importing services.
"""

from quant_assistant.portfolio.drawdown_control import (
    DrawdownControlService,
    DrawdownValidation,
    validate_drawdown,
)

__all__ = ["DrawdownControlService", "DrawdownValidation", "validate_drawdown"]
