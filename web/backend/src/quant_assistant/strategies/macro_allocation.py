"""Evidence-gated macro allocation across Nasdaq, gold, bonds, and cash."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite

import numpy as np
import pandas as pd

from quant_assistant.common.statistics import annualized_volatility, period_return

DEFAULT_MACRO_ASSETS = ("纳指", "黄金", "纯债")
_ASSET_ALIASES: Mapping[str, str] = {
    "纳指": "纳指",
    "纳斯达克": "纳指",
    "NASDAQ": "纳指",
    "NASDAQ100": "纳指",
    "纳指100": "纳指",
    "黄金": "黄金",
    "GOLD": "黄金",
    "黄金ETF": "黄金",
    "纯债": "纯债",
    "债券": "纯债",
    "中短债": "纯债",
    "BOND": "纯债",
}


@dataclass(frozen=True, slots=True)
class MacroAllocationConfig:
    version: str = "macro-allocation-v1"
    assets: tuple[str, ...] = DEFAULT_MACRO_ASSETS
    min_observations: int = 200
    cash_floor: Decimal = Decimal("0.10")
    max_asset_weight: Decimal = Decimal("0.55")
    ret20_weight: float = 0.30
    ret60_weight: float = 0.35
    ret120_weight: float = 0.35
    volatility_penalty: float = 0.30
    max_price_age_days: int = 7

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_price_age_days, bool)
            or not isinstance(self.max_price_age_days, int)
            or self.max_price_age_days < 0
        ):
            raise ValueError("max_price_age_days must be a non-negative integer")
        if not self.version.strip():
            raise ValueError("version must not be empty")
        normalized = tuple(normalize_macro_asset(item) for item in self.assets)
        if not normalized or any(not item for item in normalized):
            raise ValueError("assets must contain non-empty labels")
        if len(set(normalized)) != len(normalized):
            raise ValueError("assets must be unique after normalization")
        object.__setattr__(self, "assets", normalized)
        if isinstance(self.min_observations, bool) or self.min_observations < 200:
            raise ValueError("min_observations must be at least 200 for MA200")
        for name in ("cash_floor", "max_asset_weight"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a Decimal between 0 and 1")
        if self.cash_floor >= 1:
            raise ValueError("cash_floor must be below 1")
        if self.max_asset_weight <= 0:
            raise ValueError("max_asset_weight must be positive")
        weights = (
            self.ret20_weight,
            self.ret60_weight,
            self.ret120_weight,
            self.volatility_penalty,
        )
        if any(not isfinite(float(value)) or float(value) < 0 for value in weights):
            raise ValueError("macro weights must be finite and non-negative")
        if self.ret20_weight + self.ret60_weight + self.ret120_weight <= 0:
            raise ValueError("return weights must sum to a positive value")


@dataclass(frozen=True, slots=True)
class MacroAllocationResult:
    as_of_date: date
    metrics: pd.DataFrame
    allocations: pd.DataFrame
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers and not self.metrics.empty


def normalize_macro_asset(value: object) -> str:
    label = str(value or "").strip()
    return _ASSET_ALIASES.get(label.upper(), _ASSET_ALIASES.get(label, label))


def calculate_macro_allocation(
    prices: pd.DataFrame,
    as_of_date: date,
    *,
    config: MacroAllocationConfig | None = None,
) -> MacroAllocationResult:
    """Score point-in-time macro assets and reserve missing risk budget as cash."""

    if not isinstance(prices, pd.DataFrame):
        raise TypeError("prices must be a pandas DataFrame")
    if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
        # datetime subclasses date: a timestamp would slip through and later
        # date arithmetic (as_of_date - price_date) would raise an opaque
        # "unsupported operand type(s) for -: 'datetime.datetime' and
        # 'datetime.date'".
        raise TypeError("as_of_date must be a date")
    selected = config or MacroAllocationConfig()
    asset_column = (
        "asset" if "asset" in prices.columns else "symbol" if "symbol" in prices else None
    )
    date_column = (
        "trade_date" if "trade_date" in prices.columns else "date" if "date" in prices else None
    )
    missing = [
        name
        for name, present in (
            ("asset", asset_column is not None),
            ("trade_date", date_column is not None),
            ("close", "close" in prices.columns),
        )
        if not present
    ]
    if missing:
        return MacroAllocationResult(
            as_of_date,
            pd.DataFrame(),
            _cash_allocation(Decimal("1"), "宏观行情缺少字段：" + ", ".join(missing)),
            ("宏观行情缺少字段：" + ", ".join(missing),),
        )
    frame = prices[[asset_column, date_column, "close"]].copy(deep=True)
    frame = frame.rename(columns={asset_column: "asset", date_column: "trade_date"})
    frame["asset"] = frame["asset"].map(normalize_macro_asset)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.loc[
        frame["asset"].ne("")
        & frame["trade_date"].notna()
        & frame["trade_date"].le(as_of_date)
        & frame["close"].notna()
        & np.isfinite(frame["close"])
        & frame["close"].gt(0)
    ].drop_duplicates(["asset", "trade_date"], keep="last")

    blockers: list[str] = []
    rows: list[dict[str, object]] = []
    for asset in selected.assets:
        history = frame.loc[frame["asset"].eq(asset)].sort_values("trade_date", kind="mergesort")
        values = history["close"].to_numpy(dtype=float)
        if len(values) < selected.min_observations:
            blockers.append(f"{asset}缺少至少{selected.min_observations}个目标日前观测")
            rows.append(_blocked_row(asset, as_of_date))
            continue
        latest_price_date = history["trade_date"].iloc[-1]
        price_age_days = (as_of_date - latest_price_date).days
        if price_age_days > selected.max_price_age_days:
            blockers.append(
                f"{asset}行情过期：最新日期{latest_price_date}，"
                f"距目标日{price_age_days}天，超过{selected.max_price_age_days}天上限"
            )
            rows.append(
                {
                    **_blocked_row(asset, as_of_date),
                    "observations": len(values),
                    "latest_price_date": latest_price_date,
                    "price_age_days": price_age_days,
                    "status": "stale",
                }
            )
            continue
        ret20 = _return(values, 20)
        ret60 = _return(values, 60)
        ret120 = _return(values, 120)
        volatility = _volatility(values)
        ma120 = float(np.mean(values[-120:]))
        ma200 = float(np.mean(values[-200:]))
        close = float(values[-1])
        trend_ok = close > ma120 > ma200
        score = (
            selected.ret20_weight * ret20
            + selected.ret60_weight * ret60
            + selected.ret120_weight * ret120
            - selected.volatility_penalty * volatility
        )
        rows.append(
            {
                "asset": asset,
                "as_of_date": as_of_date,
                "observations": len(values),
                "latest_price_date": latest_price_date,
                "price_age_days": price_age_days,
                "ret_20": ret20,
                "ret_60": ret60,
                "ret_120": ret120,
                "volatility_20": volatility,
                "close": close,
                "ma_120": ma120,
                "ma_200": ma200,
                "trend_ok": trend_ok,
                "score": score,
                "status": "eligible" if trend_ok and score > 0 else "risk_off",
            }
        )
    metrics = pd.DataFrame(rows)
    allocations = (
        _cash_allocation(Decimal("1"), "有宏观资产证据缺口，风险预算保留现金")
        if blockers
        else _build_allocations(metrics, selected)
    )
    return MacroAllocationResult(as_of_date, metrics, allocations, tuple(dict.fromkeys(blockers)))


macro_resilient_allocation = calculate_macro_allocation


_return = period_return
_volatility = annualized_volatility


def _blocked_row(asset: str, as_of_date: date) -> dict[str, object]:
    return {
        "asset": asset,
        "as_of_date": as_of_date,
        "observations": 0,
        "latest_price_date": None,
        "price_age_days": None,
        "ret_20": np.nan,
        "ret_60": np.nan,
        "ret_120": np.nan,
        "volatility_20": np.nan,
        "close": np.nan,
        "ma_120": np.nan,
        "ma_200": np.nan,
        "trend_ok": False,
        "score": np.nan,
        "status": "history_insufficient",
    }


def _build_allocations(metrics: pd.DataFrame, config: MacroAllocationConfig) -> pd.DataFrame:
    columns = ["asset", "target_weight", "reason"]
    if metrics.empty:
        return _cash_allocation(Decimal("1"))
    eligible = metrics.loc[
        metrics["status"].eq("eligible") & pd.to_numeric(metrics["score"], errors="coerce").gt(0)
    ].copy()
    if eligible.empty:
        return _cash_allocation(Decimal("1"), "趋势或动量未通过，进入宏观风险防守")
    positive = pd.to_numeric(eligible["score"], errors="coerce").clip(lower=0)
    budget = Decimal("1") - config.cash_floor
    raw = positive / float(positive.sum()) * float(budget)
    weights = {
        str(asset): Decimal(str(value)) for asset, value in zip(eligible["asset"], raw, strict=True)
    }
    capped = {asset: min(weight, config.max_asset_weight) for asset, weight in weights.items()}
    invested = sum(capped.values(), Decimal("0"))
    rows = [
        {
            "asset": asset,
            "target_weight": weight,
            "reason": "动量得分为正且价格站上MA120/MA200",
        }
        for asset, weight in sorted(capped.items())
        if weight > 0
    ]
    rows.append(
        {
            "asset": "现金",
            "target_weight": Decimal("1") - invested,
            "reason": "现金底仓与超出单资产上限的风险预算",
        }
    )
    return pd.DataFrame(rows, columns=columns)


def _cash_allocation(weight: Decimal, reason: str = "证据不足，风险预算保留现金") -> pd.DataFrame:
    return pd.DataFrame(
        [{"asset": "现金", "target_weight": weight, "reason": reason}],
        columns=["asset", "target_weight", "reason"],
    )


__all__ = [
    "DEFAULT_MACRO_ASSETS",
    "MacroAllocationConfig",
    "MacroAllocationResult",
    "calculate_macro_allocation",
    "normalize_macro_asset",
    "macro_resilient_allocation",
]
