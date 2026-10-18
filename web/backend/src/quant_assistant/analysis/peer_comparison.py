"""Point-in-time peer comparison and five-dimension fund radar data."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class PeerComparisonConfig:
    """Column aliases and score weights for peer comparison."""

    min_peers: int = 2
    returns_weight: float = 0.25
    risk_weight: float = 0.25
    cost_weight: float = 0.20
    tracking_weight: float = 0.15
    liquidity_weight: float = 0.15

    def __post_init__(self) -> None:
        if isinstance(self.min_peers, bool) or self.min_peers < 2:
            raise ValueError("min_peers must be at least 2")
        weights = (
            self.returns_weight,
            self.risk_weight,
            self.cost_weight,
            self.tracking_weight,
            self.liquidity_weight,
        )
        if any(not np.isfinite(value) or value < 0 for value in weights) or sum(weights) <= 0:
            raise ValueError("peer weights must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class PeerComparisonResult:
    """Result wrapper used by the UI and API adapters."""

    frame: pd.DataFrame
    radar: pd.DataFrame
    blockers: tuple[str, ...]


_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "fund_symbol", "code"),
    "name": ("name", "fund_name", "名称"),
    "returns": ("annualized_return", "return_1y", "return", "收益率", "超额收益"),
    "drawdown": ("max_drawdown", "drawdown", "最大回撤"),
    "volatility": ("volatility", "volatility_20", "波动率"),
    "calmar": ("calmar", "calmar_ratio", "卡玛比率"),
    "sortino": ("sortino", "sortino_ratio", "索提诺比率"),
    "fee": ("total_fee_rate", "fee_rate", "expense_ratio", "综合费率"),
    "tracking_error": ("tracking_error", "tracking_difference", "td", "跟踪误差"),
    "excess_return": ("excess_return", "alpha", "超额收益"),
    "liquidity": ("avg_daily_volume", "liquidity", "turnover", "日均成交额", "流动性"),
    "size": ("fund_size", "aum", "scale", "asset_size", "规模"),
}


def _column(frame: pd.DataFrame, key: str) -> str | None:
    return next((name for name in _ALIASES[key] if name in frame.columns), None)


def _numeric(frame: pd.DataFrame, column: str | None) -> pd.Series:
    if column is None:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    values = pd.to_numeric(frame[column], errors="coerce")
    return values.replace([np.inf, -np.inf], np.nan).astype(float)


def _rank_score(values: pd.Series, *, higher_is_better: bool) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return (numeric.rank(pct=True, ascending=higher_is_better) * 100).round(2)


def _dimension(frame: pd.DataFrame, key: str, *, higher_is_better: bool) -> pd.Series:
    column = _column(frame, key)
    return _rank_score(_numeric(frame, column), higher_is_better=higher_is_better)


def compare_peers(
    peers: pd.DataFrame,
    *,
    config: PeerComparisonConfig | None = None,
    required_dimensions: Sequence[str] = ("returns", "risk", "cost", "tracking", "liquidity"),
) -> PeerComparisonResult:
    """Create comparable 0–100 scores without imputing missing metrics."""

    if not isinstance(peers, pd.DataFrame):
        raise TypeError("peers must be a pandas DataFrame")
    config = config or PeerComparisonConfig()
    if peers.empty:
        return PeerComparisonResult(pd.DataFrame(), pd.DataFrame(), ("同类基金数据为空",))
    frame = peers.copy(deep=True)
    symbol_column = _column(frame, "symbol")
    if symbol_column is None:
        return PeerComparisonResult(pd.DataFrame(), pd.DataFrame(), ("同类数据缺少基金代码",))
    frame["symbol"] = frame[symbol_column].astype(str).str.strip()
    frame = (
        frame.loc[frame["symbol"].ne("")]
        .drop_duplicates("symbol", keep="last")
        .reset_index(drop=True)
    )
    if len(frame) < config.min_peers:
        return PeerComparisonResult(
            frame, pd.DataFrame(), (f"同类基金至少需要{config.min_peers}只",)
        )

    frame["returns_score"] = _dimension(frame, "returns", higher_is_better=True)
    # A less negative drawdown and lower volatility are both safer.  Average
    # only the supplied columns; an absent column remains unavailable.
    risk_parts = []
    if _column(frame, "drawdown") is not None:
        risk_parts.append(_dimension(frame, "drawdown", higher_is_better=True))
    if _column(frame, "volatility") is not None:
        risk_parts.append(_dimension(frame, "volatility", higher_is_better=False))
    if _column(frame, "calmar") is not None:
        risk_parts.append(_dimension(frame, "calmar", higher_is_better=True))
    if _column(frame, "sortino") is not None:
        risk_parts.append(_dimension(frame, "sortino", higher_is_better=True))
    frame["risk_score"] = (
        pd.concat(risk_parts, axis=1).mean(axis=1, skipna=False) if risk_parts else np.nan
    )
    frame["cost_score"] = _dimension(frame, "fee", higher_is_better=False)
    tracking_parts = []
    if _column(frame, "tracking_error") is not None:
        tracking_parts.append(_dimension(frame, "tracking_error", higher_is_better=False))
    if _column(frame, "excess_return") is not None:
        tracking_parts.append(_dimension(frame, "excess_return", higher_is_better=True))
    frame["tracking_score"] = (
        pd.concat(tracking_parts, axis=1).mean(axis=1, skipna=False) if tracking_parts else np.nan
    )
    frame["liquidity_score"] = _dimension(frame, "liquidity", higher_is_better=True)

    dimension_columns = {
        "returns": "returns_score",
        "risk": "risk_score",
        "cost": "cost_score",
        "tracking": "tracking_score",
        "liquidity": "liquidity_score",
    }
    blockers: list[str] = []
    frame["blockers"] = [() for _ in range(len(frame))]
    for dimension in required_dimensions:
        column = dimension_columns.get(dimension)
        if column is None:
            blockers.append(f"不支持的雷达维度：{dimension}")
            continue
        missing = frame[column].isna()
        if missing.any():
            frame.loc[missing, "blockers"] = frame.loc[missing, "blockers"].map(
                lambda current, d=dimension: tuple((*current, f"{d}证据缺失"))
            )
    weights = {
        "returns_score": config.returns_weight,
        "risk_score": config.risk_weight,
        "cost_score": config.cost_weight,
        "tracking_score": config.tracking_weight,
        "liquidity_score": config.liquidity_weight,
    }
    weighted = pd.DataFrame({column: frame[column] * weight for column, weight in weights.items()})
    frame["composite_score"] = weighted.sum(axis=1, min_count=len(weights)) / sum(weights.values())
    frame["eligible"] = frame["blockers"].map(len).eq(0) & frame["composite_score"].notna()
    frame["status"] = np.where(frame["eligible"], "eligible", "blocked")
    frame = frame.sort_values(
        ["eligible", "composite_score", "symbol"], ascending=[False, False, True], kind="mergesort"
    ).reset_index(drop=True)
    radar = frame[
        [
            "symbol",
            "returns_score",
            "risk_score",
            "cost_score",
            "tracking_score",
            "liquidity_score",
            "eligible",
        ]
    ].copy()
    return PeerComparisonResult(frame=frame, radar=radar, blockers=tuple(dict.fromkeys(blockers)))


def build_fund_radar(
    peers: pd.DataFrame, *, config: PeerComparisonConfig | None = None
) -> pd.DataFrame:
    """Return one radar-ready row per peer."""

    return compare_peers(peers, config=config).radar


peer_comparison = compare_peers


__all__ = [
    "PeerComparisonConfig",
    "PeerComparisonResult",
    "build_fund_radar",
    "compare_peers",
    "peer_comparison",
]
