"""Cash-flow-adjusted portfolio performance metrics."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from math import sqrt

import numpy as np
import pandas as pd

from quant_assistant.common.tabular import first_column

TRADING_DAYS_PER_YEAR = 252
MIN_ANNUALIZATION_OBSERVATIONS = 20
CENT = Decimal("0.01")
_DATE_COLUMNS = ("trade_date", "date", "as_of_date", "nav_date")
_TOTAL_VALUE_COLUMNS = (
    "total_value",
    "portfolio_value",
    "ending_value",
    "closing_value",
    "equity",
    "nav",
)
_DIRECT_FLOW_COLUMNS = (
    "external_cash_flow",
    "external_flow",
    "net_external_flow",
    "net_cash_flow",
    "cash_flow",
)


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Serializable summary of a completed portfolio return series."""

    total_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    sharpe_zero_rate: float
    turnover: float
    total_fees: Decimal
    benchmark_return: float
    excess_return: float


@dataclass(frozen=True, slots=True)
class _PreparedNav:
    unit_nav: pd.Series
    total_value: pd.Series | None
    frame: pd.DataFrame


_first_column = first_column


def _numeric(frame: pd.DataFrame, column: str, label: str) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce").astype(float)
    if values.isna().any() or not np.isfinite(values.to_numpy()).all():
        raise ValueError(f"{label} contains non-finite values")
    return values


def _normalise_frame(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{label} must be a pandas DataFrame")
    if frame.empty:
        raise ValueError(f"{label} must not be empty")

    result = frame.copy(deep=True)
    date_column = _first_column(result, _DATE_COLUMNS)
    if date_column is not None:
        keys = pd.to_datetime(result[date_column], errors="coerce")
        if keys.isna().any():
            raise ValueError(f"{label} contains invalid dates")
        with suppress(TypeError):
            keys = keys.dt.tz_localize(None)
        result["_metric_key"] = keys.dt.normalize()
    elif isinstance(result.index, pd.DatetimeIndex):
        keys = pd.to_datetime(result.index, errors="coerce")
        if keys.isna().any():
            raise ValueError(f"{label} contains invalid index dates")
        if keys.tz is not None:
            keys = keys.tz_localize(None)
        result["_metric_key"] = keys.normalize()
    else:
        result["_metric_key"] = result.index

    result = result.drop_duplicates("_metric_key", keep="last")
    return result.sort_values("_metric_key", kind="mergesort").reset_index(drop=True)


def _net_external_flow(frame: pd.DataFrame) -> pd.Series:
    direct = _first_column(frame, _DIRECT_FLOW_COLUMNS)
    if direct is not None:
        return _numeric(frame, direct, "external cash flow")

    flow = pd.Series(0.0, index=frame.index, dtype=float)
    for column in ("deposit", "deposits", "external_deposit", "external_deposits"):
        if column in frame.columns:
            flow = flow + _numeric(frame, column, "external deposits")
    for column in ("withdrawal", "withdrawals", "external_withdrawal", "external_withdrawals"):
        if column in frame.columns:
            flow = flow - _numeric(frame, column, "external withdrawals").abs()
    return flow


def _has_explicit_flow(frame: pd.DataFrame) -> bool:
    flow_columns = set(_DIRECT_FLOW_COLUMNS) | {
        "deposit",
        "deposits",
        "external_deposit",
        "external_deposits",
        "withdrawal",
        "withdrawals",
        "external_withdrawal",
        "external_withdrawals",
    }
    return bool(flow_columns.intersection(frame.columns))


def _unit_nav_from_values(values: pd.Series, flows: pd.Series) -> pd.Series:
    if (values <= 0).any():
        raise ValueError("portfolio values must be positive")
    units = np.ones(len(values), dtype=float)
    for index in range(1, len(values)):
        previous_value = float(values.iloc[index - 1])
        adjusted_ending_value = float(values.iloc[index] - flows.iloc[index])
        if previous_value <= 0 or adjusted_ending_value < 0:
            raise ValueError("cash-flow-adjusted portfolio value is invalid")
        units[index] = units[index - 1] * adjusted_ending_value / previous_value
    return pd.Series(units, index=values.index, dtype=float)


def _prepare_nav(nav: pd.DataFrame) -> _PreparedNav:
    frame = _normalise_frame(nav, "nav")
    total_column = _first_column(frame, _TOTAL_VALUE_COLUMNS)
    total_value = (
        _numeric(frame, total_column, "portfolio values") if total_column is not None else None
    )

    if total_value is not None and (_has_explicit_flow(frame) or "unit_nav" not in frame.columns):
        unit_nav = _unit_nav_from_values(total_value, _net_external_flow(frame))
    elif "unit_nav" in frame.columns:
        unit_nav = _numeric(frame, "unit_nav", "unit NAV")
        if (unit_nav <= 0).any():
            raise ValueError("unit NAV must be positive")
    elif total_value is not None:
        unit_nav = _unit_nav_from_values(total_value, _net_external_flow(frame))
    else:
        raise ValueError("nav must contain unit_nav or a portfolio-value column")

    unit_nav.index = frame["_metric_key"]
    if total_value is not None:
        total_value.index = frame["_metric_key"]
    return _PreparedNav(unit_nav=unit_nav, total_value=total_value, frame=frame)


def cash_flow_adjusted_unit_nav(nav: pd.DataFrame) -> pd.Series:
    """Return the deterministic time-weighted unit-NAV series used by metrics."""

    return _prepare_nav(nav).unit_nav.copy()


def _decimal_sum(frame: pd.DataFrame) -> Decimal:
    direct_column = _first_column(
        frame,
        ("fees", "fee", "total_fees", "transaction_fees", "trading_fees"),
    )
    columns = (
        [direct_column]
        if direct_column is not None
        else [
            name
            for name in (
                "commission",
                "stamp_tax",
                "transfer_fee",
                "purchase_fee",
                "redemption_fee",
                "service_fee",
            )
            if name in frame.columns
        ]
    )
    total = Decimal("0")
    for column in columns:
        for raw in frame[column].tolist():
            if raw is None or pd.isna(raw):
                continue
            try:
                value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("fees contain an invalid value") from exc
            if not value.is_finite():
                raise ValueError("fees contain a non-finite value")
            total += value
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


def _turnover(frame: pd.DataFrame, total_value: pd.Series | None) -> float:
    if "turnover_ratio" in frame.columns:
        return float(_numeric(frame, "turnover_ratio", "turnover ratio").abs().sum())

    buy_column = _first_column(frame, ("buy_amount", "purchase_amount", "gross_buys"))
    sell_column = _first_column(frame, ("sell_amount", "redemption_amount", "gross_sells"))
    if buy_column is not None or sell_column is not None:
        traded = pd.Series(0.0, index=frame.index, dtype=float)
        if buy_column is not None:
            traded += _numeric(frame, buy_column, "buy amount").abs()
        if sell_column is not None:
            traded += _numeric(frame, sell_column, "sell amount").abs()
        numerator = float(traded.sum())
    else:
        amount_column = _first_column(
            frame,
            ("turnover_amount", "traded_amount", "trade_amount", "gross_trade_amount", "turnover"),
        )
        numerator = (
            float(_numeric(frame, amount_column, "turnover amount").abs().sum())
            if amount_column is not None
            else 0.0
        )
    if numerator == 0:
        return 0.0
    if total_value is None:
        return float("nan")
    average_nav = float(total_value.mean())
    return numerator / average_nav if average_nav > 0 else float("nan")


def _benchmark_comparison(
    unit_nav: pd.Series,
    benchmark: pd.DataFrame,
) -> tuple[float, float]:
    benchmark_frame = _normalise_frame(benchmark, "benchmark")
    close_column = _first_column(
        benchmark_frame,
        ("close", "benchmark_close", "unit_nav", "value"),
    )
    if close_column is None:
        raise ValueError("benchmark must contain a close column")
    close = _numeric(benchmark_frame, close_column, "benchmark close")
    if (close <= 0).any():
        raise ValueError("benchmark close must be positive")
    close.index = benchmark_frame["_metric_key"]

    common = unit_nav.index.intersection(close.index).sort_values()
    if len(common) < 2:
        return float("nan"), float("nan")
    aligned_portfolio = unit_nav.loc[common]
    aligned_benchmark = close.loc[common]
    portfolio_return = float(aligned_portfolio.iloc[-1] / aligned_portfolio.iloc[0] - 1)
    benchmark_return = float(aligned_benchmark.iloc[-1] / aligned_benchmark.iloc[0] - 1)
    return benchmark_return, portfolio_return - benchmark_return


def calculate_performance(nav: pd.DataFrame, benchmark: pd.DataFrame) -> PerformanceMetrics:
    """Calculate point-in-time-safe portfolio and aligned benchmark performance."""

    prepared = _prepare_nav(nav)
    unit_nav = prepared.unit_nav
    observations = len(unit_nav)
    total_return = float(unit_nav.iloc[-1] / unit_nav.iloc[0] - 1) if observations > 1 else 0.0
    drawdowns = unit_nav / unit_nav.cummax() - 1
    max_drawdown = float(drawdowns.min())
    daily_returns = unit_nav.pct_change().dropna()

    annualized_return = float("nan")
    annualized_volatility = float("nan")
    sharpe = float("nan")
    if observations >= MIN_ANNUALIZATION_OBSERVATIONS and not daily_returns.empty:
        growth = float(unit_nav.iloc[-1] / unit_nav.iloc[0])
        if growth > 0:
            annualized_return = growth ** (TRADING_DAYS_PER_YEAR / len(daily_returns)) - 1
        daily_std = float(daily_returns.std(ddof=1))
        if np.isfinite(daily_std):
            annualized_volatility = daily_std * sqrt(TRADING_DAYS_PER_YEAR)
            if daily_std > 0:
                sharpe = float(daily_returns.mean()) / daily_std * sqrt(TRADING_DAYS_PER_YEAR)

    benchmark_return, excess_return = _benchmark_comparison(unit_nav, benchmark)
    return PerformanceMetrics(
        total_return=total_return,
        annualized_return=float(annualized_return),
        annualized_volatility=float(annualized_volatility),
        max_drawdown=max_drawdown,
        sharpe_zero_rate=float(sharpe),
        turnover=_turnover(prepared.frame, prepared.total_value),
        total_fees=_decimal_sum(prepared.frame),
        benchmark_return=benchmark_return,
        excess_return=excess_return,
    )


__all__ = [
    "MIN_ANNUALIZATION_OBSERVATIONS",
    "PerformanceMetrics",
    "calculate_performance",
    "cash_flow_adjusted_unit_nav",
]
