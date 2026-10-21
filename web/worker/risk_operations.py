"""Portfolio risk metrics and historical stress tests.

Ops: ``risk.metrics``, ``risk.stress_test``.

Contract (batch3): ``risk.metrics`` payload ``{portfolio_id, period_days}`` or
``{nav_series: [..]}`` →
``{var95, max_drawdown, ann_volatility, sharpe, calmar, period_days, method,
computed_at}``; ``risk.stress_test`` payload ``{portfolio_id}`` or
``{nav_series, weights}`` →
``{scenarios: [{name, index_drop_pct, est_loss_pct, est_loss_amount}], disclaimer}``.

Methodology (documented assumptions):
- ``nav_series`` accepts plain numbers, ``{date, nav}`` mappings or
  ``[date, nav]`` pairs; entries are treated as one point per trading day
  and sorted by date when dates are present.
- Returns are simple per-point returns ``nav[i]/nav[i-1] - 1``.
- VaR(95%) uses historical simulation: ``-P5(returns)`` (linear interpolation,
  same convention as ``numpy.percentile``), floored at 0. It answers "the loss
  not exceeded on 95% of days", expressed as a positive fraction.
- Volatility is the sample std of daily returns annualised with sqrt(252).
- Sharpe = (annualised mean return - ``RISK_FREE_RATE``) / annualised volatility.
  ``RISK_FREE_RATE = 0.02`` is a module constant; change it in one place if
  policy changes. Sharpe is ``None`` when volatility is zero.
- Calmar = annualised return / max drawdown, where the annualised return is
  ``(last/first)**(252/n) - 1``. ``None`` when there was no drawdown.
- ``max_drawdown`` is reported as a positive fraction (peak-to-trough loss)
  together with the peak (start) and trough (end) dates.
- Fewer than ``MIN_POINTS`` (30) points → all metrics ``None`` + warning;
  the worker never fabricates risk numbers from thin data.
- ``risk.stress_test`` replays three hard-coded historical broad-index
  drawdowns (``STRESS_SCENARIOS``) against the current portfolio assuming
  ``beta ~= 1.0`` (documented coarse estimate). ``est_loss_amount`` needs a
  ``portfolio_value`` (or is read from the latest ``portfolio_valuations``
  row in the ``portfolio_id`` path); otherwise it is ``None`` + warning.
- ``portfolio_id`` is the ledger ``user_id``; the NAV series is read from
  ``portfolio_valuations`` (``as_of_date``, ``unit_nav``) and requires the
  caller to pass ``snapshot`` for the DB context, like other DB-backed ops.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from statistics import mean, stdev

RISK_CAPABILITIES = ("risk.metrics", "risk.stress_test")

#: Annual risk-free rate used by the Sharpe ratio. One place to change.
RISK_FREE_RATE = 0.02
#: Trading days per year for annualisation.
TRADING_DAYS = 252
#: Minimum NAV points before metrics are computed; below this we return
#: nulls + warning instead of fabricating precision.
MIN_POINTS = 30

STRESS_DISCLAIMER = (
    "本结果为基于宽基指数历史极端区间的假想测算（组合 beta 按 1.0 粗估），"
    "不构成投资建议；实际组合跌幅受持仓结构、债基/货基配比与再平衡影响，可能显著偏离。"
)

#: Hard-coded historical stress scenarios. ``index_drop_pct`` is the
#: peak-to-trough close-to-close fall of the stated broad index over the
#: stated interval (approximate reference constants from public exchange
#:行情 data, see ``source_note``).
STRESS_SCENARIOS = (
    {
        "id": "crash_2015",
        "name": "2015年股灾",
        "index": "上证综指",
        "interval": "2015-06-12 收盘 → 2016-01-28 收盘",
        "index_drop_pct": -0.486,
        "source_note": "上交所公开行情数据：2015-06-12 收 5166.35，2016-01-28 收 2655.66，区间跌幅约 -48.6%",
    },
    {
        "id": "covid_2020_03",
        "name": "2020年3月疫情冲击",
        "index": "上证综指",
        "interval": "2020-01-14 收盘 → 2020-03-19 收盘",
        "index_drop_pct": -0.156,
        "source_note": "上交所公开行情数据：2020-01-14 收 3127.57，2020-03-19 收 2641.26，区间跌幅约 -15.6%",
    },
    {
        "id": "bear_2022",
        "name": "2022年熊市",
        "index": "上证综指",
        "interval": "2021-12-31 收盘 → 2022-10-31 收盘",
        "index_drop_pct": -0.205,
        "source_note": "上交所公开行情数据：2021-12-31 收 3640.44，2022-10-31 收 2893.48，区间跌幅约 -20.5%",
    },
)


def _to_float(value: object, field: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _to_date(value: object, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date") from exc
    raise ValueError(f"{field} must be an ISO date string")


def normalize_nav_series(raw: object) -> list[tuple[date | None, float]]:
    """Normalise accepted ``nav_series`` shapes to ``[(date|None, nav)]``.

    Shapes: numbers, ``{"date","nav"}`` mappings, ``[date, nav]`` pairs.
    Sorted by date when every entry carries one. Raises ``ValueError``
    on malformed input.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("nav_series must be a non-empty list")
    points: list[tuple[date | None, float]] = []
    for position, entry in enumerate(raw):
        field = f"nav_series[{position}]"
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            day, nav = None, _to_float(entry, field)
        elif isinstance(entry, dict):
            day = _to_date(entry.get("date"), f"{field}.date") if "date" in entry else None
            if "nav" not in entry:
                raise ValueError(f"{field} must carry 'nav'")
            nav = _to_float(entry["nav"], f"{field}.nav")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            raw_day = entry[0]
            day = None if raw_day is None else _to_date(raw_day, f"{field}[0]")
            nav = _to_float(entry[1], f"{field}[1]")
        else:
            raise ValueError(f"{field} has an unsupported shape")
        if nav <= 0:
            raise ValueError(f"{field}: nav must be positive")
        points.append((day, nav))
    if all(day is not None for day, _ in points):
        points.sort(key=lambda item: item[0])  # type: ignore[index]
    return points


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (``numpy.percentile`` default method)."""
    if not sorted_values:
        raise ValueError("empty sample")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (pct / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[lower]
    fraction = rank - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _null_metrics(warnings: list[str], extra: dict | None = None) -> dict:
    result = {
        "var95": None,
        "max_drawdown": None,
        "max_drawdown_peak_date": None,
        "max_drawdown_trough_date": None,
        "ann_volatility": None,
        "sharpe": None,
        "calmar": None,
        "period_days": 0,
        "n_points": 0,
        "method": None,
        "computed_at": datetime.now().astimezone().isoformat(),
        "warnings": warnings,
    }
    if extra:
        result.update(extra)
    return result


def risk_metrics(
    nav_series: object,
    period_days: int | None = None,
    *,
    as_of: date | None = None,
) -> dict:
    """Core computation; pure apart from the ``computed_at`` timestamp."""
    if period_days is not None and (
        isinstance(period_days, bool) or not isinstance(period_days, int) or period_days <= 0
    ):
        raise ValueError("period_days must be a positive integer")
    points = normalize_nav_series(nav_series)
    if period_days is not None:
        if all(day is not None for day, _ in points):
            # "recent period_days calendar days" -> strict filter keeps
            # exactly period_days points for daily series.
            cutoff = points[-1][0] - timedelta(days=period_days)  # type: ignore[operator]
            points = [point for point in points if point[0] > cutoff]  # type: ignore[operator]
        else:
            points = points[-period_days:]
    n = len(points)
    warnings: list[str] = []
    if n < MIN_POINTS:
        warnings.append(f"净值序列仅 {n} 个点（需 ≥{MIN_POINTS}），风险指标不予计算")
        return _null_metrics(warnings, {"period_days": period_days})

    navs = [nav for _, nav in points]
    returns = [navs[i] / navs[i - 1] - 1.0 for i in range(1, n)]
    dates = [day for day, _ in points]

    # Historical-simulation VaR: the loss not exceeded on 95% of days.
    var95 = max(0.0, -_percentile(sorted(returns), 5.0))

    # Max drawdown with peak/trough dates.
    peak = navs[0]
    peak_idx = 0
    max_dd = 0.0
    trough_idx = 0
    dd_peak_idx = 0
    for i in range(1, n):
        if navs[i] > peak:
            peak = navs[i]
            peak_idx = i
        dd = (peak - navs[i]) / peak
        if dd > max_dd:
            max_dd = dd
            trough_idx = i
            dd_peak_idx = peak_idx

    sigma = stdev(returns)
    mu = mean(returns)
    ann_vol = sigma * math.sqrt(TRADING_DAYS)
    sharpe = (mu * TRADING_DAYS - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else None
    ann_return = (navs[-1] / navs[0]) ** (TRADING_DAYS / len(returns)) - 1.0
    calmar = ann_return / max_dd if max_dd > 0 else None

    def iso(day: date | None) -> str | None:
        return day.isoformat() if day is not None else None

    if dates[0] is not None and dates[-1] is not None:
        span_days = (dates[-1] - dates[0]).days
        start, end = iso(dates[0]), iso(dates[-1])
    else:
        span_days, start, end = n, None, None

    return {
        "var95": round(var95, 6),
        "max_drawdown": round(max_dd, 6),
        "max_drawdown_peak_date": iso(dates[dd_peak_idx]),
        "max_drawdown_trough_date": iso(dates[trough_idx]),
        "ann_volatility": round(ann_vol, 6),
        "sharpe": round(sharpe, 6) if sharpe is not None else None,
        "calmar": round(calmar, 6) if calmar is not None else None,
        "period_days": span_days,
        "n_points": n,
        "series_start": start,
        "series_end": end,
        "method": (
            "VaR(95%) 历史模拟法（日收益 5% 分位数取反）；"
            f"年化波动率/夏普按 252 交易日年化，无风险利率 {RISK_FREE_RATE}；"
            "最大回撤为净值峰值到谷底的最大跌幅（正值表示亏损幅度）；"
            "卡玛比率 = 年化收益 / 最大回撤"
        ),
        "computed_at": (as_of.isoformat() if as_of else datetime.now().astimezone().isoformat()),
        "warnings": warnings,
    }


def stress_test(
    nav_series: object | None = None,
    portfolio_value: float | None = None,
    beta: float = 1.0,
) -> dict:
    """Core computation; pure. ``beta`` is the documented coarse estimate (~1)."""
    if isinstance(beta, bool) or not isinstance(beta, (int, float)) or not math.isfinite(beta) or beta < 0:
        raise ValueError("beta must be a non-negative finite number")
    value = None
    if portfolio_value is not None:
        value = _to_float(portfolio_value, "portfolio_value")
        if value < 0:
            raise ValueError("portfolio_value must be non-negative")
    warnings: list[str] = []
    if value is None and nav_series is not None:
        points = normalize_nav_series(nav_series)
        value = points[-1][1]
        warnings.append("est_loss_amount 以净值序列末点为组合市值参考估算")
    if value is None:
        warnings.append("未提供 portfolio_value，est_loss_amount 为空")
    scenarios = []
    for scenario in STRESS_SCENARIOS:
        est_loss_pct = scenario["index_drop_pct"] * float(beta)
        scenarios.append(
            {
                "id": scenario["id"],
                "name": scenario["name"],
                "index": scenario["index"],
                "interval": scenario["interval"],
                "index_drop_pct": scenario["index_drop_pct"],
                "beta": float(beta),
                "est_loss_pct": round(est_loss_pct, 6),
                "est_loss_amount": round(value * est_loss_pct, 2) if value is not None else None,
                "source_note": scenario["source_note"],
            }
        )
    return {
        "scenarios": scenarios,
        "beta_assumption": "组合 beta 按 1.0 粗估（可传入 beta 覆盖）；未区分债基/货基等低波动资产",
        "disclaimer": STRESS_DISCLAIMER,
        "warnings": warnings,
    }


def _portfolio_series_from_db(database, user_id: str) -> tuple[list[tuple[date, float]], float | None]:
    """Read (as_of_date, unit_nav) from portfolio_valuations + latest total_value."""
    with database.connection() as connection:
        rows = connection.execute(
            "SELECT as_of_date, unit_nav, total_value FROM portfolio_valuations "
            "WHERE user_id = ? ORDER BY as_of_date",
            (user_id,),
        ).fetchall()
    series = [(_to_date(row[0], "as_of_date"), float(row[1])) for row in rows]
    latest_value = float(rows[-1][2]) if rows else None
    return series, latest_value


def execute_risk(kind: str, payload: dict) -> dict:
    if kind not in RISK_CAPABILITIES:
        raise ValueError("unsupported_operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    series = payload.get("nav_series")
    portfolio_id = payload.get("portfolio_id")
    db_value: float | None = None
    if portfolio_id is not None and series is None:
        if not isinstance(portfolio_id, str) or not portfolio_id.strip():
            raise ValueError("portfolio_id must be a non-empty string")
        if "snapshot" not in payload:
            raise ValueError("portfolio_id path requires payload['snapshot'] for DB access")
        from analysis_context import analytical_context

        with analytical_context(payload["snapshot"]) as (database, _queries, _directory):
            series, db_value = _portfolio_series_from_db(database, portfolio_id.strip())
    if kind == "risk.metrics":
        if not series:
            if portfolio_id is not None:
                return _null_metrics(["该组合无历史净值（portfolio_valuations 为空）"])
            raise ValueError("risk.metrics requires nav_series or portfolio_id")
        return risk_metrics(series, payload.get("period_days"))
    # risk.stress_test: the NAV series is optional (only used to infer a
    # portfolio value reference); portfolio_value alone is a valid payload.
    beta = payload.get("beta", 1.0)
    portfolio_value = payload.get("portfolio_value", db_value)
    weights = payload.get("weights")
    result = stress_test(series or None, portfolio_value, beta)
    if portfolio_id is not None and not series:
        result["warnings"].append("该组合无历史净值（portfolio_valuations 为空）")
    if weights is not None:
        result["warnings"].append("weights 暂仅用于文档记录：当前按组合整体 beta 粗估，未做逐资产 beta 加权")
        result["weights"] = weights
    return result
