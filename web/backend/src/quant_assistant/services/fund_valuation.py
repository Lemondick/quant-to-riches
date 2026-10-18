"""Display-only fund portfolio valuation helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from quant_assistant.models.entities import AssetType
from quant_assistant.providers.fund_valuation import (
    FallbackFundValuationProvider,
    FundIntradayEstimate,
    FundValuationProvider,
)
from quant_assistant.services.portfolio_commands import PositionInput


@dataclass(frozen=True, slots=True)
class FundPositionEstimate:
    symbol: str
    quantity: Decimal
    estimated_market_value: Decimal | None
    estimate: FundIntradayEstimate | None
    status: str


class FundIntradayValuationService:
    """Apply external estimates to positions without persisting the result."""

    def __init__(self, provider: FundValuationProvider | None = None) -> None:
        self._provider = provider or FallbackFundValuationProvider()

    def estimate_positions(
        self,
        positions: Iterable[PositionInput],
    ) -> tuple[FundPositionEstimate, ...]:
        result: list[FundPositionEstimate] = []
        for position in positions:
            if not isinstance(position, PositionInput):
                raise TypeError("估值持仓必须使用PositionInput")
            if position.asset_type is not AssetType.FUND:
                continue
            try:
                estimate = self._provider.estimate(position.symbol)
                if (
                    estimate.valuation_type != "intraday_estimate"
                    or estimate.estimate_nav is None
                    or estimate.quality_status != "valid"
                ):
                    raise ValueError("unavailable_intraday_estimate")
            except Exception:
                result.append(
                    FundPositionEstimate(
                        position.symbol, position.quantity, None, None, "unavailable"
                    )
                )
                continue
            value = (position.quantity * estimate.estimate_nav).quantize(Decimal("0.01"))
            result.append(
                FundPositionEstimate(position.symbol, position.quantity, value, estimate, "ok")
            )
        return tuple(result)


__all__ = ["FundIntradayValuationService", "FundPositionEstimate"]
