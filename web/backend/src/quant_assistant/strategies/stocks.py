"""Explainable A-share cross-sectional ranking and signal rules."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from quant_assistant.features.technical import robust_zscore
from quant_assistant.models.entities import Action, AssetType, MarketRegime, Position, Signal

_MAIN_BOARD_SYMBOL = re.compile(r"^(?:60[0135]\d{3}\.SH|00[0123]\d{3}\.SZ)$")
_RANKING_FEATURES = {
    "symbol",
    "ret_20",
    "ret_60",
    "ret_120",
    "trend_120",
    "trend_200",
    "volatility_20",
    "amount_20",
}
_INSTRUMENT_FIELDS = {
    "symbol",
    "board",
    "listed_on",
    "industry",
    "is_st",
    "is_suspended",
}


@dataclass(frozen=True, slots=True)
class StockStrategyConfig:
    """Locked parameters for the price-and-volume stock strategy."""

    version: str = "stock-base-v1"
    top_candidate_count: int = 10
    min_listing_days: int = 250
    min_amount_20: float = 50_000_000.0
    exit_percentile: float = 0.20
    max_names: int = 5
    target_weight: Decimal = Decimal("0.06")
    stop_loss_pct: Decimal | None = None


_DEFAULT_CONFIG = StockStrategyConfig()


def rank_stocks(
    features: pd.DataFrame,
    instruments: pd.DataFrame,
    as_of_date: date,
    config: StockStrategyConfig = _DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Filter the point-in-time universe and apply the locked factor weights."""

    missing_features = _RANKING_FEATURES.difference(features.columns)
    missing_instruments = _INSTRUMENT_FIELDS.difference(instruments.columns)
    if missing_features:
        formatted = ", ".join(sorted(missing_features))
        raise ValueError(f"features missing required columns: {formatted}")
    if missing_instruments:
        formatted = ", ".join(sorted(missing_instruments))
        raise ValueError(f"instruments missing required columns: {formatted}")

    eligible = _merge_and_filter_main_board(features, instruments, as_of_date, config)
    score_columns = [
        "momentum_z",
        "trend_z",
        "low_volatility_z",
        "liquidity_z",
        "score",
        "rank",
        "rank_percentile",
    ]
    if eligible.empty:
        return eligible.assign(**{column: pd.Series(dtype=float) for column in score_columns})

    momentum = 0.25 * eligible["ret_20"] + 0.35 * eligible["ret_60"] + 0.40 * eligible["ret_120"]
    trend = 0.60 * eligible["trend_120"] + 0.40 * eligible["trend_200"]
    eligible["momentum_z"] = robust_zscore(momentum, higher_is_better=True)
    eligible["trend_z"] = robust_zscore(trend, higher_is_better=True)
    eligible["low_volatility_z"] = robust_zscore(eligible["volatility_20"], higher_is_better=False)
    eligible["liquidity_z"] = robust_zscore(np.log1p(eligible["amount_20"]), higher_is_better=True)
    eligible["score"] = (
        0.40 * eligible["momentum_z"]
        + 0.25 * eligible["trend_z"]
        + 0.20 * eligible["low_volatility_z"]
        + 0.15 * eligible["liquidity_z"]
    )
    eligible = eligible.sort_values(
        ["score", "symbol"], ascending=[False, True], kind="mergesort"
    ).reset_index(drop=True)
    eligible["rank"] = np.arange(1, len(eligible) + 1, dtype=int)
    eligible["rank_percentile"] = eligible["rank"] / len(eligible)
    return eligible


def generate_stock_signals(
    ranked: pd.DataFrame,
    price_history: pd.DataFrame,
    rank_history: pd.DataFrame,
    positions: Sequence[Position],
    regime: MarketRegime,
    as_of_date: date,
    valid_until: datetime,
    config: StockStrategyConfig = _DEFAULT_CONFIG,
) -> list[Signal]:
    """Emit deterministic stock signals from ranks, exits, and market risk."""

    if valid_until.tzinfo is None or valid_until.utcoffset() is None:
        raise ValueError("valid_until must be timezone-aware")
    if "symbol" not in ranked.columns:
        raise ValueError("ranked missing required column: symbol")

    current_ranking = _ranking_as_of(ranked, as_of_date)
    held = {
        item.symbol: item
        for item in positions
        if item.asset_type == AssetType.STOCK and item.quantity > 0
    }
    ranked_by_symbol = current_ranking.set_index("symbol", drop=False)
    held_signals: list[Signal] = []
    retained_names = 0
    target_weight = _regime_target_weight(config.target_weight, regime)

    for symbol in sorted(held):
        current_row = ranked_by_symbol.loc[symbol] if symbol in ranked_by_symbol.index else None
        if isinstance(current_row, pd.DataFrame):
            current_row = current_row.iloc[0]
        exit_reasons: list[str] = []
        if current_row is None:
            exit_reasons.append("标的已不在当期可交易股票池")
        else:
            if _has_cost_stop_loss(held[symbol], price_history, as_of_date, config.stop_loss_pct):
                exit_reasons.append(f"收盘价相对持仓成本回撤达到{config.stop_loss_pct:.0%}止损线")
            if _has_two_consecutive_weak_ranks(
                symbol, current_row, rank_history, as_of_date, config.exit_percentile
            ):
                exit_reasons.append("连续两次周评估跌出可交易股票池前20%")
            if _has_three_session_ma120_break(symbol, price_history, as_of_date):
                exit_reasons.append("连续3个已完成交易日收盘价低于120日均线")

        score = _safe_score(current_row)
        if exit_reasons:
            held_signals.append(
                _signal(
                    symbol,
                    Action.SELL,
                    score,
                    Decimal("0"),
                    exit_reasons,
                    as_of_date,
                    valid_until,
                    config,
                )
            )
        elif regime is MarketRegime.RED:
            held_signals.append(
                _signal(
                    symbol,
                    Action.REDUCE,
                    score,
                    Decimal("0"),
                    ["市场风险状态为红色，禁止新增风险并逐步降低股票仓位"],
                    as_of_date,
                    valid_until,
                    config,
                )
            )
        else:
            retained_names += 1
            regime_reason = (
                "市场风险状态为黄色，股票目标权重减半"
                if regime is MarketRegime.YELLOW
                else "市场风险状态为绿色，可使用完整股票风险预算"
            )
            held_signals.append(
                _signal(
                    symbol,
                    Action.HOLD,
                    score,
                    target_weight,
                    ["当期排名与120日趋势均未触发退出规则", regime_reason],
                    as_of_date,
                    valid_until,
                    config,
                )
            )

    buy_signals: list[Signal] = []
    if regime is not MarketRegime.RED:
        candidate_limit = max(config.top_candidate_count, 0)
        slots = max(config.max_names - retained_names, 0)
        candidates = current_ranking.head(candidate_limit)
        candidates = candidates.loc[~candidates["symbol"].isin(held)]
        if "score" not in candidates.columns:
            candidates = candidates.iloc[0:0]
        else:
            candidate_scores = pd.to_numeric(candidates["score"], errors="coerce")
            candidates = candidates.loc[candidate_scores.notna() & np.isfinite(candidate_scores)]
        for _, row in candidates.head(slots).iterrows():
            rank = _display_rank(row, current_ranking)
            regime_reason = (
                "市场风险状态为黄色，股票目标权重减半"
                if regime is MarketRegime.YELLOW
                else "市场风险状态为绿色，允许新增股票风险"
            )
            buy_signals.append(
                _signal(
                    str(row["symbol"]),
                    Action.BUY,
                    _safe_score(row),
                    target_weight,
                    [f"综合评分排名第{rank}，进入前{candidate_limit}候选", regime_reason],
                    as_of_date,
                    valid_until,
                    config,
                )
            )

    action_priority = {Action.SELL: 0, Action.REDUCE: 1, Action.HOLD: 2}
    held_signals.sort(key=lambda signal: (action_priority[signal.action], signal.symbol))
    return held_signals + buy_signals


def _merge_and_filter_main_board(
    features: pd.DataFrame,
    instruments: pd.DataFrame,
    as_of_date: date,
    config: StockStrategyConfig,
) -> pd.DataFrame:
    feature_rows = features.copy(deep=True)
    if "trade_date" in feature_rows.columns:
        feature_rows["trade_date"] = pd.to_datetime(feature_rows["trade_date"], errors="coerce")
        feature_rows = feature_rows.loc[
            feature_rows["trade_date"].notna()
            & feature_rows["trade_date"].le(pd.Timestamp(as_of_date))
        ]
        feature_rows = feature_rows.sort_values(
            ["symbol", "trade_date"], kind="mergesort"
        ).drop_duplicates("symbol", keep="last")
    else:
        feature_rows = feature_rows.drop_duplicates("symbol", keep="last")

    metadata = instruments.copy(deep=True)
    if "as_of_date" in metadata.columns:
        metadata["as_of_date"] = pd.to_datetime(metadata["as_of_date"], errors="coerce")
        metadata = metadata.loc[
            metadata["as_of_date"].notna() & metadata["as_of_date"].le(pd.Timestamp(as_of_date))
        ]
        metadata = metadata.sort_values(["symbol", "as_of_date"], kind="mergesort").drop_duplicates(
            "symbol", keep="last"
        )
    else:
        metadata = metadata.drop_duplicates("symbol", keep="last")

    metadata["listed_on"] = pd.to_datetime(metadata["listed_on"], errors="coerce")
    if "delisted_on" in metadata.columns:
        metadata["delisted_on"] = pd.to_datetime(metadata["delisted_on"], errors="coerce")

    eligible = feature_rows.merge(metadata, on="symbol", how="inner", suffixes=("", "_instrument"))
    for column in _RANKING_FEATURES.difference({"symbol"}):
        eligible[column] = pd.to_numeric(eligible[column], errors="coerce")

    finite_features = np.isfinite(eligible[list(_RANKING_FEATURES.difference({"symbol"}))]).all(
        axis=1
    )
    listing_age = (pd.Timestamp(as_of_date) - eligible["listed_on"]).dt.days
    industry_present = eligible["industry"].notna() & eligible["industry"].astype(
        str
    ).str.strip().ne("")
    main_board = eligible["board"].astype("string").str.lower().isin({"main", "main_board", "主板"})
    ordinary_a_share = eligible["symbol"].astype(str).str.upper().str.match(_MAIN_BOARD_SYMBOL)
    active = ~eligible["is_st"].fillna(True).astype(bool) & ~eligible["is_suspended"].fillna(
        True
    ).astype(bool)
    if "name" in eligible.columns:
        active &= ~eligible["name"].astype("string").str.upper().str.contains(
            "ST", regex=False, na=True
        )
    if "asset_type" in eligible.columns:
        active &= eligible["asset_type"].astype("string").str.lower().eq(AssetType.STOCK.value)
    if "delisted_on" in eligible.columns:
        active &= eligible["delisted_on"].isna() | eligible["delisted_on"].gt(
            pd.Timestamp(as_of_date)
        )

    valid_prices = eligible["volatility_20"].ge(0) & eligible["amount_20"].ge(config.min_amount_20)
    if "close" in eligible.columns:
        eligible["close"] = pd.to_numeric(eligible["close"], errors="coerce")
        valid_prices &= eligible["close"].gt(0) & np.isfinite(eligible["close"])

    mask = (
        finite_features
        & eligible["listed_on"].notna()
        & listing_age.ge(config.min_listing_days)
        & industry_present
        & main_board
        & ordinary_a_share
        & active
        & valid_prices
    )
    return eligible.loc[mask].copy().reset_index(drop=True)


def _ranking_as_of(ranked: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    current = ranked.copy(deep=True)
    date_column = next(
        (column for column in ("as_of_date", "trade_date") if column in current.columns), None
    )
    if date_column is not None:
        current[date_column] = pd.to_datetime(current[date_column], errors="coerce")
        current = current.loc[
            current[date_column].notna() & current[date_column].le(pd.Timestamp(as_of_date))
        ]
        current = current.sort_values(["symbol", date_column], kind="mergesort").drop_duplicates(
            "symbol", keep="last"
        )
    else:
        current = current.drop_duplicates("symbol", keep="first")

    if "rank" in current.columns:
        current["rank"] = pd.to_numeric(current["rank"], errors="coerce")
        current = current.sort_values(["rank", "symbol"], kind="mergesort", na_position="last")
    elif "score" in current.columns:
        current["score"] = pd.to_numeric(current["score"], errors="coerce")
        current = current.sort_values(
            ["score", "symbol"], ascending=[False, True], kind="mergesort", na_position="last"
        )
    else:
        current = current.sort_values("symbol", kind="mergesort")
    return current.reset_index(drop=True)


def _has_two_consecutive_weak_ranks(
    symbol: str,
    current_row: pd.Series,
    rank_history: pd.DataFrame,
    as_of_date: date,
    exit_percentile: float,
) -> bool:
    observations: list[tuple[pd.Timestamp | None, float]] = []
    if not rank_history.empty and "symbol" in rank_history.columns:
        history = rank_history.loc[rank_history["symbol"].astype(str).eq(symbol)].copy()
        percentile_column = next(
            (
                column
                for column in ("rank_percentile", "rank_pct", "percentile")
                if column in history.columns
            ),
            None,
        )
        if percentile_column is None and {"rank", "universe_size"}.issubset(history.columns):
            history["_rank_percentile"] = pd.to_numeric(
                history["rank"], errors="coerce"
            ) / pd.to_numeric(history["universe_size"], errors="coerce")
            percentile_column = "_rank_percentile"
        date_column = next(
            (
                column
                for column in ("as_of_date", "trade_date", "evaluation_date", "rank_date")
                if column in history.columns
            ),
            None,
        )
        if date_column is not None:
            history[date_column] = pd.to_datetime(history[date_column], errors="coerce")
            history = history.loc[
                history[date_column].notna() & history[date_column].le(pd.Timestamp(as_of_date))
            ].sort_values(date_column, kind="mergesort")
        if percentile_column is not None:
            for row in history.to_dict("records"):
                value = pd.to_numeric(pd.Series([row[percentile_column]]), errors="coerce").iloc[0]
                if pd.notna(value):
                    observed_on = row[date_column] if date_column is not None else None
                    observations.append((observed_on, float(value)))

    current_percentile = _row_rank_percentile(current_row)
    if current_percentile is not None:
        current_date = pd.Timestamp(as_of_date)
        observations = [item for item in observations if item[0] != current_date]
        observations.append((current_date, current_percentile))

    return len(observations) >= 2 and all(
        percentile > exit_percentile for _, percentile in observations[-2:]
    )


def _row_rank_percentile(row: pd.Series) -> float | None:
    for column in ("rank_percentile", "rank_pct", "percentile"):
        if column in row.index:
            value = pd.to_numeric(pd.Series([row[column]]), errors="coerce").iloc[0]
            if pd.notna(value):
                return float(value)
    return None


def _has_three_session_ma120_break(
    symbol: str, price_history: pd.DataFrame, as_of_date: date
) -> bool:
    required = {"symbol", "trade_date", "close"}
    if price_history.empty or not required.issubset(price_history.columns):
        return False
    history = price_history.loc[price_history["symbol"].astype(str).eq(symbol)].copy()
    history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce")
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    history = history.loc[
        history["trade_date"].notna()
        & history["trade_date"].le(pd.Timestamp(as_of_date))
        & history["close"].gt(0)
        & np.isfinite(history["close"])
    ].sort_values("trade_date", kind="mergesort")
    history = history.drop_duplicates("trade_date", keep="last")
    if "ma_120" in history.columns:
        history["_ma_120"] = pd.to_numeric(history["ma_120"], errors="coerce")
    else:
        history["_ma_120"] = history["close"].rolling(120, min_periods=120).mean()
    completed = history.dropna(subset=["_ma_120"]).tail(3)
    return len(completed) == 3 and bool((completed["close"] < completed["_ma_120"]).all())


def _has_cost_stop_loss(
    position: Position,
    price_history: pd.DataFrame,
    as_of_date: date,
    stop_loss_pct: Decimal | None,
) -> bool:
    if stop_loss_pct is None:
        return False
    if stop_loss_pct <= 0 or stop_loss_pct >= 1:
        raise ValueError("stop_loss_pct must be between 0 and 1")
    required = {"symbol", "trade_date", "close"}
    if price_history.empty or not required.issubset(price_history.columns):
        return False
    history = price_history.loc[price_history["symbol"].astype(str).eq(position.symbol)].copy()
    history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce")
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    history = history.loc[
        history["trade_date"].notna()
        & history["trade_date"].le(pd.Timestamp(as_of_date))
        & history["close"].gt(0)
        & np.isfinite(history["close"])
    ].sort_values("trade_date", kind="mergesort")
    if history.empty:
        return False
    latest_close = Decimal(str(history.iloc[-1]["close"]))
    stop_price = position.average_cost * (Decimal("1") - stop_loss_pct)
    return latest_close <= stop_price


def _regime_target_weight(base: Decimal, regime: MarketRegime) -> Decimal:
    if regime is MarketRegime.RED:
        return Decimal("0")
    if regime is MarketRegime.YELLOW:
        return base / Decimal("2")
    return base


def _safe_score(row: pd.Series | None) -> float:
    if row is None or "score" not in row.index:
        return 0.0
    score = pd.to_numeric(pd.Series([row["score"]]), errors="coerce").iloc[0]
    return float(score) if pd.notna(score) and np.isfinite(score) else 0.0


def _display_rank(row: pd.Series, ranking: pd.DataFrame) -> int:
    if "rank" in row.index:
        rank = pd.to_numeric(pd.Series([row["rank"]]), errors="coerce").iloc[0]
        if pd.notna(rank):
            return int(rank)
    matches = ranking.index[ranking["symbol"].eq(row["symbol"])]
    return int(matches[0]) + 1 if not matches.empty else 0


def _signal(
    symbol: str,
    action: Action,
    score: float,
    target_weight: Decimal,
    reasons: Sequence[str],
    as_of_date: date,
    valid_until: datetime,
    config: StockStrategyConfig,
) -> Signal:
    return Signal(
        symbol=symbol,
        asset_type=AssetType.STOCK,
        action=action,
        score=score,
        target_weight=target_weight,
        reasons=tuple(reasons),
        as_of_date=as_of_date,
        valid_until=valid_until,
        strategy_version=config.version,
    )
