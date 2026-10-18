"""Point-in-time cross-sector momentum rotation.

This module is intentionally a pure calculator.  It accepts an operator- or
provider-supplied price panel and returns an auditable ranking/allocation; it
does not place orders and it fails closed when a track lacks enough history.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite

import numpy as np
import pandas as pd

from quant_assistant.common.statistics import annualized_volatility, period_return

DEFAULT_SECTORS = ("银行", "医疗", "有色", "恒生科技", "消费", "红利")
_SECTOR_ALIASES: Mapping[str, str] = {
    "银行": "银行",
    "银行业": "银行",
    "金融": "银行",
    "医疗": "医疗",
    "医药": "医疗",
    "医疗保健": "医疗",
    "有色": "有色",
    "有色金属": "有色",
    "恒科": "恒生科技",
    "恒生科技": "恒生科技",
    "恒生科技指数": "恒生科技",
    "消费": "消费",
    "大消费": "消费",
    "红利": "红利",
    "股息": "红利",
}


@dataclass(frozen=True, slots=True)
class SectorRotationConfig:
    """Locked defaults for the six-track momentum rotation."""

    version: str = "sector-rotation-v1"
    sectors: tuple[str, ...] = DEFAULT_SECTORS
    min_observations: int = 200
    top_n: int = 2
    max_weight: Decimal = Decimal("0.35")
    ret20_weight: float = 0.25
    ret60_weight: float = 0.35
    ret120_weight: float = 0.40
    volatility_penalty: float = 0.25

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("version must not be empty")
        if not self.sectors or any(not str(item).strip() for item in self.sectors):
            raise ValueError("sectors must contain non-empty labels")
        normalized = tuple(normalize_sector(item) for item in self.sectors)
        if len(set(normalized)) != len(normalized):
            raise ValueError("sectors must be unique after normalization")
        object.__setattr__(self, "sectors", normalized)
        if isinstance(self.min_observations, bool) or self.min_observations < 200:
            raise ValueError("min_observations must be at least 200 for MA200")
        if isinstance(self.top_n, bool) or self.top_n < 1:
            raise ValueError("top_n must be positive")
        if not isinstance(self.max_weight, Decimal) or not 0 < self.max_weight <= 1:
            raise ValueError("max_weight must be a Decimal between 0 and 1")
        weights = (
            self.ret20_weight,
            self.ret60_weight,
            self.ret120_weight,
            self.volatility_penalty,
        )
        if any(not isfinite(float(value)) or float(value) < 0 for value in weights):
            raise ValueError("rotation weights must be finite and non-negative")
        if self.ret20_weight + self.ret60_weight + self.ret120_weight <= 0:
            raise ValueError("return weights must sum to a positive value")


@dataclass(frozen=True, slots=True)
class SectorRotationResult:
    """Ranking, capped target weights, and explicit evidence blockers."""

    as_of_date: date
    ranking: pd.DataFrame
    allocations: pd.DataFrame
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers and not self.ranking.empty


def normalize_sector(value: object) -> str:
    """Normalize common Chinese labels without guessing unknown sectors."""

    label = str(value or "").strip()
    return _SECTOR_ALIASES.get(label, label)


def calculate_sector_rotation(
    prices: pd.DataFrame,
    as_of_date: date,
    *,
    config: SectorRotationConfig | None = None,
) -> SectorRotationResult:
    """Calculate cross-sector momentum and a capped top-``N`` allocation.

    ``prices`` must contain ``symbol``, ``sector`` (or ``track``),
    ``trade_date`` (or ``date``), and positive ``close``.  All observations
    after ``as_of_date`` are ignored before any return or moving average is
    computed.
    """

    if not isinstance(prices, pd.DataFrame):
        raise TypeError("prices must be a pandas DataFrame")
    if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
        # datetime subclasses date: the price dates are converted to plain
        # ``date`` objects, so a timestamp would make the point-in-time filter
        # raise an opaque "can't compare datetime.datetime to datetime.date".
        raise TypeError("as_of_date must be a date")
    selected = config or SectorRotationConfig()
    sector_column = (
        "sector" if "sector" in prices.columns else "track" if "track" in prices else None
    )
    date_column = (
        "trade_date" if "trade_date" in prices.columns else "date" if "date" in prices else None
    )
    missing = [
        name
        for name, present in (
            ("symbol", "symbol" in prices.columns),
            ("sector", sector_column is not None),
            ("trade_date", date_column is not None),
            ("close", "close" in prices.columns),
        )
        if not present
    ]
    columns = _result_columns()
    if missing:
        return SectorRotationResult(
            as_of_date,
            pd.DataFrame(columns=columns),
            _cash_allocation(),
            ("赛道行情缺少字段：" + ", ".join(missing),),
        )

    frame = prices[["symbol", sector_column, date_column, "close"]].copy(deep=True)
    frame = frame.rename(columns={sector_column: "sector", date_column: "trade_date"})
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["sector"] = frame["sector"].map(normalize_sector)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.loc[
        frame["symbol"].ne("")
        & frame["sector"].ne("")
        & frame["trade_date"].notna()
        & frame["trade_date"].le(as_of_date)
        & frame["close"].notna()
        & np.isfinite(frame["close"])
        & frame["close"].gt(0)
    ].copy()
    frame = frame.drop_duplicates(["symbol", "trade_date"], keep="last")

    blockers: list[str] = []
    symbol_metrics: list[dict[str, object]] = []
    for symbol, group in frame.groupby("symbol", sort=True):
        history = group.sort_values("trade_date", kind="mergesort")
        if history.empty:
            continue
        sector = str(history.iloc[-1]["sector"])
        values = history["close"].to_numpy(dtype=float)
        if len(values) < selected.min_observations:
            continue
        symbol_metrics.append(
            {
                "symbol": str(symbol),
                "sector": sector,
                "ret_20": _return(values, 20),
                "ret_60": _return(values, 60),
                "ret_120": _return(values, 120),
                "volatility_20": _volatility(values),
                "close": float(values[-1]),
                "ma_120": float(np.mean(values[-120:])),
                "ma_200": float(np.mean(values[-200:])),
                "observations": len(values),
            }
        )

    metrics = pd.DataFrame(symbol_metrics)
    rows: list[dict[str, object]] = []
    for sector in selected.sectors:
        subset = metrics.loc[metrics["sector"].eq(sector)] if not metrics.empty else metrics
        if subset.empty:
            blockers.append(f"{sector}缺少至少{selected.min_observations}个目标日前观测")
            rows.append(_blocked_sector_row(sector, as_of_date, "history_insufficient"))
            continue
        row = {
            "sector": sector,
            "as_of_date": as_of_date,
            "symbol_count": int(len(subset)),
            "observations": int(subset["observations"].min()),
            "ret_20": float(subset["ret_20"].mean()),
            "ret_60": float(subset["ret_60"].mean()),
            "ret_120": float(subset["ret_120"].mean()),
            "volatility_20": float(subset["volatility_20"].mean()),
            "close": float(subset["close"].mean()),
            "ma_120": float(subset["ma_120"].mean()),
            "ma_200": float(subset["ma_200"].mean()),
        }
        row["trend_ok"] = bool(row["close"] > row["ma_120"] > row["ma_200"])
        row["score"] = (
            selected.ret20_weight * row["ret_20"]
            + selected.ret60_weight * row["ret_60"]
            + selected.ret120_weight * row["ret_120"]
            - selected.volatility_penalty * row["volatility_20"]
        )
        row["status"] = "eligible" if row["trend_ok"] else "trend_blocked"
        rows.append(row)

    ranking = pd.DataFrame(rows, columns=columns)
    if not ranking.empty:
        eligible = ranking[ranking["status"].eq("eligible")].sort_values(
            ["score", "sector"], ascending=[False, True], kind="mergesort"
        )
        ranking.loc[eligible.index, "rank"] = np.arange(1, len(eligible) + 1, dtype=int)
        ranking = ranking.sort_values(
            ["status", "score", "sector"],
            key=lambda values: (
                values.map({"eligible": 0, "trend_blocked": 1}).fillna(2)
                if values.name == "status"
                else values
            ),
            kind="mergesort",
        ).reset_index(drop=True)
    allocations = (
        _cash_allocation("有赛道证据缺口，风险预算保留现金")
        if blockers
        else _build_allocations(ranking, selected)
    )
    return SectorRotationResult(as_of_date, ranking, allocations, tuple(dict.fromkeys(blockers)))


sector_momentum_rotation = calculate_sector_rotation


_return = period_return
_volatility = annualized_volatility


def _blocked_sector_row(sector: str, as_of_date: date, status: str) -> dict[str, object]:
    return {
        "sector": sector,
        "as_of_date": as_of_date,
        "symbol_count": 0,
        "observations": 0,
        "ret_20": np.nan,
        "ret_60": np.nan,
        "ret_120": np.nan,
        "volatility_20": np.nan,
        "close": np.nan,
        "ma_120": np.nan,
        "ma_200": np.nan,
        "trend_ok": False,
        "score": np.nan,
        "status": status,
        "rank": np.nan,
    }


def _result_columns() -> list[str]:
    return [
        "sector",
        "as_of_date",
        "symbol_count",
        "observations",
        "ret_20",
        "ret_60",
        "ret_120",
        "volatility_20",
        "close",
        "ma_120",
        "ma_200",
        "trend_ok",
        "score",
        "status",
        "rank",
    ]


def _build_allocations(ranking: pd.DataFrame, config: SectorRotationConfig) -> pd.DataFrame:
    columns = ["sector", "target_weight", "rank", "reason"]
    if ranking.empty:
        return _cash_allocation()
    eligible = ranking.loc[
        ranking["status"].eq("eligible") & pd.to_numeric(ranking["score"], errors="coerce").gt(0)
    ].sort_values("score", ascending=False, kind="mergesort")
    selected = eligible.head(config.top_n)
    if selected.empty:
        return _cash_allocation("无正分且趋势通过的赛道，资金保留现金")
    per_weight = min(config.max_weight, Decimal("1") / Decimal(str(len(selected))))
    rows = [
        {
            "sector": str(row.sector),
            "target_weight": per_weight,
            "rank": int(row.rank),
            "reason": "动量得分为正且价格站上MA120/MA200",
        }
        for row in selected.itertuples(index=False)
    ]
    invested = per_weight * len(rows)
    rows.append(
        {
            "sector": "现金",
            "target_weight": Decimal("1") - invested,
            "rank": None,
            "reason": "未分配风险预算留存现金",
        }
    )
    return pd.DataFrame(rows, columns=columns)


def _cash_allocation(reason: str = "证据不足，风险预算保留现金") -> pd.DataFrame:
    return pd.DataFrame(
        [{"sector": "现金", "target_weight": Decimal("1"), "rank": None, "reason": reason}],
        columns=["sector", "target_weight", "rank", "reason"],
    )


__all__ = [
    "DEFAULT_SECTORS",
    "SectorRotationConfig",
    "SectorRotationResult",
    "calculate_sector_rotation",
    "normalize_sector",
    "sector_momentum_rotation",
]
