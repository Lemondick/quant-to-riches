"""Index valuation percentiles, ERP and market temperature (``valuation.percentiles``).

Contract (batch2 §2): payload ``{indices: [{symbol, market}], years?: 10}``,
returns ``{as_of, items: [{symbol, pe, pb, pe_pct, pb_pct, erp}],
temperature: {score, label}}``. ``pe_pct``/``pb_pct`` are the current value's
percentile rank inside the trailing ``years``-year history window
(0 = cheapest ever, 100 = most expensive ever).

Data note (documented assumption): none of the existing public market
providers (``AkshareMarketFeedProvider``, ``index_history`` OHLC) publish
index PE/PB *history*, so the history source is a pluggable
``ValuationSource``. The default source is intentionally unwired and the op
degrades to ``items: []`` + a warning instead of fabricating a series, per
the contract's no-data behaviour. Wire a real feed (e.g. akshare
``stock_zh_index_value_csindex``) by passing a source that implements
``history(symbol, market, years)``.

ERP = 1/PE − 10Y sovereign yield. There is no live bond-yield feed in the
worker, so yields are documented assumptions overridable via environment:
``QUANT_VALUATION_BOND_YIELD_CN/US/HK/JP`` (defaults: CN 1.75%, US 4.05%,
HK 4.05% via the USD peg, JP 1.60% — Oct-2026 area levels).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Protocol

VALUATION_CAPABILITIES = ("valuation.percentiles",)

_MIN_OBSERVATIONS = 60  # same bar as SmartDcaConfig.min_valuation_observations
_MIN_YEARS, _MAX_YEARS = 1, 30
_MARKETS = ("CN", "US", "HK", "JP")

_BOND_YIELD_DEFAULTS = {
    "CN": 0.0175,  # 10Y CGB, ~Oct-2026 level
    "US": 0.0405,  # 10Y UST, ~Oct-2026 level
    "HK": 0.0405,  # HKD pegged to USD: use the US 10Y as proxy
    "JP": 0.0160,  # 10Y JGB, ~Oct-2026 level
}


def bond_yield(market: str) -> float:
    """10Y sovereign yield assumption for ``market`` (env-overridable)."""
    try:
        return float(os.environ[f"QUANT_VALUATION_BOND_YIELD_{market}"])
    except (KeyError, TypeError, ValueError):
        return _BOND_YIELD_DEFAULTS[market]


class ValuationSource(Protocol):
    """History of ``(date, pe, pb)``; ``None`` values mark missing points."""

    def history(
        self, symbol: str, market: str, years: int
    ) -> Sequence[tuple[date, float | None, float | None]] | None: ...


class _UnwiredValuationSource:
    """Default source: no verified PE/PB history feed is wired yet."""

    def history(self, symbol, market, years):
        return None


def _default_source() -> ValuationSource:
    return _UnwiredValuationSource()


def percentile_of(history: Sequence[float], current: float) -> float:
    """Percentile rank of ``current`` in ``history`` (0..100, inclusive).

    ``100 * #{obs <= current} / n``: the median of a symmetric series scores
    ~50, the all-time high scores 100.
    """
    if not history:
        raise ValueError("percentile requires a non-empty history")
    return 100.0 * sum(1 for value in history if value <= current) / len(history)


def trailing_percentile(
    series: Sequence[tuple[date, float]], at: date, years: int
) -> float | None:
    """Percentile of the value at ``at`` inside its trailing ``years`` window."""
    window = [value for day, value in series if at - _years_ago(years) <= day <= at]
    current = next((value for day, value in series if day == at), None)
    if current is None or len(window) < _MIN_OBSERVATIONS:
        return None
    return percentile_of(window, current)


def _years_ago(years: int) -> timedelta:
    return timedelta(days=int(years * 365.25))


def _clean(values: Sequence[tuple[date, float | None, float | None]], field: int):
    """``field``: 1 = PE, 2 = PB (position in the ``(date, pe, pb)`` tuple)."""
    points = [(day, row[field - 1]) for day, *row in values if row[field - 1] is not None]
    points.sort(key=lambda item: item[0])
    return points


def _erp(pe: float | None, market: str) -> float | None:
    if pe is None or pe <= 0:
        return None
    return 1.0 / pe - bond_yield(market)


def _erp_heat(erp: float | None) -> float | None:
    """Map ERP to a 0..100 heat contribution: ERP ≤ −2% → 100 (boiling),
    ERP ≥ 6% → 0 (ice cold), linear in between. Documented heuristic."""
    if erp is None:
        return None
    return max(0.0, min(100.0, (0.06 - erp) / 0.08 * 100.0))


def _temperature_label(score: float | None) -> str:
    if score is None:
        return "数据不足"
    if score < 20:
        return "冰点"
    if score < 40:
        return "偏冷"
    if score < 60:
        return "适中"
    if score < 80:
        return "偏热"
    return "过热"


def _temperature(items: Sequence[Mapping]) -> dict:
    """score = 0.4·PE分位 + 0.4·PB分位 + 0.2·ERP热度, averaged over indices."""
    scores = []
    for item in items:
        heat = _erp_heat(item["erp"])
        if heat is None:
            scores.append(0.5 * item["pe_pct"] + 0.5 * item["pb_pct"])
        else:
            scores.append(
                0.4 * item["pe_pct"] + 0.4 * item["pb_pct"] + 0.2 * heat
            )
    if not scores:
        return {"score": None, "label": "数据不足"}
    score = round(sum(scores) / len(scores), 1)
    return {"score": score, "label": _temperature_label(score)}


def valuation_percentiles(
    indices: Sequence[Mapping],
    years: int = 10,
    *,
    source: ValuationSource | None = None,
    as_of: date | None = None,
) -> dict:
    """Core computation; ``source`` is injectable for deterministic tests."""
    if not isinstance(indices, Sequence) or isinstance(indices, str) or not indices:
        raise ValueError("indices must be a non-empty list")
    if not isinstance(years, int) or isinstance(years, bool) or not _MIN_YEARS <= years <= _MAX_YEARS:
        raise ValueError("years must be an integer between 1 and 30")
    provider = source or _default_source()
    items: list[dict] = []
    warnings: list[str] = []
    for entry in indices:
        if not isinstance(entry, Mapping):
            raise ValueError("each index entry must be a mapping")
        symbol = entry.get("symbol")
        market = entry.get("market")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("index symbol must be a non-empty string")
        if market not in _MARKETS:
            raise ValueError(f"unsupported market: {market!r}")
        symbol = symbol.strip()
        history = provider.history(symbol, market, years)
        if not history:
            warnings.append(f"{symbol}: 指数 PE/PB 历史数据不可用，已跳过")
            continue
        pe_points = _clean(history, 1)
        pb_points = _clean(history, 2)
        if len(pe_points) < _MIN_OBSERVATIONS or len(pb_points) < _MIN_OBSERVATIONS:
            warnings.append(f"{symbol}: 有效历史不足{_MIN_OBSERVATIONS}个观测，已跳过")
            continue
        pe = pe_points[-1][1]
        pb = pb_points[-1][1]
        pe_pct = round(percentile_of([value for _, value in pe_points], pe), 1)
        pb_pct = round(percentile_of([value for _, value in pb_points], pb), 1)
        erp = _erp(pe, market)
        items.append(
            {
                "symbol": symbol,
                "pe": round(pe, 2),
                "pb": round(pb, 2),
                "pe_pct": pe_pct,
                "pb_pct": pb_pct,
                "erp": round(erp, 4) if erp is not None else None,
            }
        )
    if not items:
        warnings.append("所有指数均无可用估值历史：PE/PB 历史数据源未接入")
    return {
        "as_of": (as_of or date.today()).isoformat(),
        "items": items,
        "temperature": _temperature(items),
        "warnings": warnings,
    }


def execute_valuation(kind: str, payload: dict) -> dict:
    if kind != "valuation.percentiles":
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    return valuation_percentiles(
        payload.get("indices", ()),
        payload.get("years", 10),
    )
