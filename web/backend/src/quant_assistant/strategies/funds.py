"""Fee-aware, benchmark-driven signals for public funds."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import numpy as np
import pandas as pd

from quant_assistant.common.tabular import first_column
from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.features.funds import calculate_fund_features
from quant_assistant.funds.fees import (
    FeeScheduleError,
    FundFeeSchedule,
    choose_share_class,
)
from quant_assistant.models.entities import Action, AssetType, Signal

_BENCHMARK_FEATURE_COLUMNS = {
    "ret_20",
    "ret_60",
    "ret_120",
    "close",
    "ma_120",
    "ma_200",
    "volatility_20",
}
_POOL_SYMBOL_ALIASES = ("symbol", "fund_symbol")
_FAMILY_ALIASES = ("family_id", "fund_family_id", "family", "pair_id")
_CLASS_ALIASES = ("share_class", "class")
_BENCHMARK_ALIASES = ("benchmark_symbol", "benchmark", "index_symbol")
_BUCKET_ALIASES = (
    "fund_bucket",
    "bucket",
    "strategy_type",
    "strategy",
    "strategy_bucket",
    "allocation_bucket",
    "allocation",
    "evaluation_frequency",
    "rebalance_frequency",
    "mode",
)
_LAST_EVALUATION_ALIASES = (
    "last_evaluated_on",
    "last_evaluation_date",
    "last_signal_date",
    "last_rebalance_date",
)


@dataclass(frozen=True, slots=True)
class FundStrategyConfig:
    """Deterministic public-fund strategy parameters."""

    version: str = "fund-index-v1"
    core_holding_days: int = 365
    rotation_holding_days: int = 90
    minimum_rotation_days: int = 30
    fee_comparison_amount: Decimal = Decimal("5000")
    core_target_weight: Decimal = Decimal("0.15")
    rotation_target_weight: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("version must not be empty")
        for field_name in (
            "core_holding_days",
            "rotation_holding_days",
            "minimum_rotation_days",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.rotation_holding_days < self.minimum_rotation_days:
            raise ValueError("rotation_holding_days must meet minimum_rotation_days")
        if not isinstance(self.fee_comparison_amount, Decimal):
            raise TypeError("fee_comparison_amount must be Decimal")
        if self.fee_comparison_amount <= 0:
            raise ValueError("fee_comparison_amount must be positive")
        for field_name in ("core_target_weight", "rotation_target_weight"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{field_name} must be Decimal")
            if value < 0 or value > 1:
                raise ValueError(f"{field_name} must be between 0 and 1")


_DEFAULT_CONFIG = FundStrategyConfig()


def _row_value(row: Any, name: str) -> float:
    if isinstance(row, Mapping):
        return float(row[name])
    try:
        return float(row[name])
    except (KeyError, TypeError):
        return float(getattr(row, name))


def benchmark_score(row: Any) -> tuple[float, bool]:
    """Return the locked momentum/volatility score and long-trend gate."""

    momentum = (
        0.25 * _row_value(row, "ret_20")
        + 0.35 * _row_value(row, "ret_60")
        + 0.40 * _row_value(row, "ret_120")
    )
    score = momentum - 0.25 * _row_value(row, "volatility_20")
    trend_ok = _row_value(row, "close") > _row_value(row, "ma_120") and _row_value(
        row, "ma_120"
    ) > _row_value(row, "ma_200")
    return score, trend_ok


_first_column = first_column


def _normalize_share_class(value: Any) -> str:
    normalized = str(value).strip().upper().replace("类", "")
    if normalized in {"A", "CLASS A", "A CLASS"}:
        return "A"
    if normalized in {"C", "CLASS C", "C CLASS"}:
        return "C"
    return normalized


def _canonical_fund_pool(fund_pool: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(fund_pool, pd.DataFrame):
        raise TypeError("fund_pool must be a pandas DataFrame")
    if fund_pool.empty:
        return pd.DataFrame(columns=["family_id", "symbol", "share_class", "benchmark_symbol"])

    pool = fund_pool.copy(deep=True)
    if {"a_symbol", "c_symbol"}.issubset(pool.columns):
        expanded: list[dict[str, Any]] = []
        for index, values in enumerate(pool.to_dict("records")):
            family = next(
                (
                    values[name]
                    for name in _FAMILY_ALIASES
                    if name in values and pd.notna(values[name])
                ),
                f"family-{index}",
            )
            for share_class, symbol_column in (("A", "a_symbol"), ("C", "c_symbol")):
                item = values.copy()
                item["family_id"] = family
                item["symbol"] = item[symbol_column]
                item["share_class"] = share_class
                expanded.append(item)
        pool = pd.DataFrame(expanded)

    symbol_column = _first_column(pool, _POOL_SYMBOL_ALIASES)
    if symbol_column is None:
        raise ValueError("fund_pool missing symbol or A/C symbol columns")
    class_column = _first_column(pool, _CLASS_ALIASES)
    benchmark_column = _first_column(pool, _BENCHMARK_ALIASES)
    if benchmark_column is None:
        raise ValueError("fund_pool missing benchmark_symbol")

    pool["symbol"] = pool[symbol_column].astype("string").str.strip()
    if class_column is None:
        pool["share_class"] = pool["symbol"].str[-1].str.upper()
    else:
        pool["share_class"] = pool[class_column].map(_normalize_share_class)
    pool["benchmark_symbol"] = pool[benchmark_column].astype("string").str.strip()

    family_column = _first_column(pool, _FAMILY_ALIASES)
    if family_column is not None:
        pool["family_id"] = pool[family_column].astype("string").str.strip()
    elif len(pool) == 2 and set(pool["share_class"].dropna()) == {"A", "C"}:
        pool["family_id"] = "family-0"
    else:
        pool["family_id"] = pool["symbol"].str.replace(r"[AC]$", "", regex=True)
    return pool


def _filter_point_in_time_rows(
    frame: pd.DataFrame, as_of_date: date, date_columns: tuple[str, ...]
) -> pd.DataFrame:
    result = frame.copy()
    for column in date_columns:
        if column in result.columns:
            parsed = pd.to_datetime(result[column], errors="coerce")
            result = result.loc[parsed.notna() & parsed.le(pd.Timestamp(as_of_date))].copy()
    return result


def _benchmark_features(benchmark_prices: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    if not isinstance(benchmark_prices, pd.DataFrame):
        raise TypeError("benchmark_prices must be a pandas DataFrame")
    if _BENCHMARK_FEATURE_COLUMNS.issubset(benchmark_prices.columns):
        features = _filter_point_in_time_rows(
            benchmark_prices, as_of_date, ("trade_date", "as_of_date")
        )
        if "symbol" not in features.columns:
            raise ValueError("benchmark features missing symbol")
        sort_column = next(
            (name for name in ("trade_date", "as_of_date") if name in features.columns),
            None,
        )
        if sort_column:
            features = features.sort_values(["symbol", sort_column], kind="mergesort")
            features = features.groupby("symbol", sort=False, as_index=False).tail(1)
        return features.reset_index(drop=True)
    return calculate_fund_features(benchmark_prices, as_of_date)


def _normalize_bucket(group: pd.DataFrame, expected_holding_days: int | None) -> str:
    for column in _BUCKET_ALIASES:
        if column not in group.columns:
            continue
        values = group[column].dropna().astype(str).str.strip().str.lower()
        if values.empty:
            continue
        value = values.iloc[-1]
        if any(marker in value for marker in ("rotation", "rotate", "weekly", "轮动", "周")):
            return "rotation"
        if any(marker in value for marker in ("core", "monthly", "核心", "月")):
            return "core"
    if "is_rotation" in group.columns:
        values = group["is_rotation"].dropna()
        if not values.empty:
            return "rotation" if bool(values.iloc[-1]) else "core"
    return (
        "core" if expected_holding_days is not None and expected_holding_days >= 365 else "rotation"
    )


def _as_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _evaluation_due(group: pd.DataFrame, bucket: str, as_of_date: date) -> bool:
    if "evaluation_due" in group.columns:
        values = group["evaluation_due"].dropna()
        if not values.empty:
            return bool(values.iloc[-1])
    for column in _LAST_EVALUATION_ALIASES:
        if column not in group.columns:
            continue
        dates = [_as_date(value) for value in group[column].tolist()]
        completed = [value for value in dates if value is not None and value <= as_of_date]
        if not completed:
            return True
        last_evaluation = max(completed)
        if bucket == "rotation":
            return (as_of_date - last_evaluation).days >= 7
        return (last_evaluation.year, last_evaluation.month) != (
            as_of_date.year,
            as_of_date.month,
        )
    return True


def _next_submission_deadline(as_of_date: date) -> datetime:
    next_date = as_of_date + timedelta(days=1)
    while next_date.weekday() >= 5:
        next_date += timedelta(days=1)
    return datetime.combine(next_date, time(15, 0), tzinfo=SHANGHAI_TZ)


def _blocking_signal(
    symbol: str,
    reason: str,
    as_of_date: date,
    config: FundStrategyConfig,
    *,
    score: float = 0.0,
) -> Signal:
    return Signal(
        symbol=symbol,
        asset_type=AssetType.FUND,
        action=Action.NO_TRADE,
        score=score if np.isfinite(score) else 0.0,
        target_weight=Decimal("0"),
        reasons=(reason,),
        as_of_date=as_of_date,
        valid_until=_next_submission_deadline(as_of_date),
        strategy_version=config.version,
    )


def _representative_symbol(group: pd.DataFrame) -> str:
    values = sorted(str(value) for value in group["symbol"].dropna() if str(value))
    return values[0] if values else str(group.name)


def _share_schedules(
    group: pd.DataFrame, fee_schedules: Mapping[str, FundFeeSchedule]
) -> tuple[FundFeeSchedule, FundFeeSchedule] | None:
    a_symbols = sorted(
        group.loc[group["share_class"].eq("A"), "symbol"].dropna().astype(str).unique()
    )
    c_symbols = sorted(
        group.loc[group["share_class"].eq("C"), "symbol"].dropna().astype(str).unique()
    )
    if len(a_symbols) != 1 or len(c_symbols) != 1:
        return None
    a_schedule = fee_schedules.get(a_symbols[0])
    c_schedule = fee_schedules.get(c_symbols[0])
    if not isinstance(a_schedule, FundFeeSchedule) or not isinstance(c_schedule, FundFeeSchedule):
        return None
    if a_schedule.symbol != a_symbols[0] or c_schedule.symbol != c_symbols[0]:
        return None
    return a_schedule, c_schedule


def _status_row(
    fund_status: pd.DataFrame,
    symbol: str,
    family_id: str,
    as_of_date: date,
) -> pd.Series | None:
    if fund_status.empty:
        return None
    status = fund_status.copy(deep=True)
    if "symbol" in status.columns:
        matches = status.loc[status["symbol"].astype(str).eq(symbol)].copy()
    elif "family_id" in status.columns:
        matches = status.loc[status["family_id"].astype(str).eq(family_id)].copy()
    else:
        return None
    matches = _filter_point_in_time_rows(matches, as_of_date, ("as_of_date",))
    if matches.empty:
        return None
    nav_column = next(
        (name for name in ("nav_date", "latest_nav_date", "valuation_date") if name in matches),
        None,
    )
    if nav_column:
        matches["_nav_sort"] = pd.to_datetime(matches[nav_column], errors="coerce")
        matches = matches.sort_values("_nav_sort", kind="mergesort", na_position="first")
    return matches.iloc[-1]


def _nav_date(status: pd.Series) -> date | None:
    for column in ("nav_date", "latest_nav_date", "valuation_date"):
        if column in status.index:
            return _as_date(status[column])
    return None


def _parse_open_flag(value: Any) -> bool | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {
        "true",
        "1",
        "yes",
        "open",
        "available",
        "开放",
        "开放申购",
        "开放赎回",
        "可申购",
        "可赎回",
    }:
        return True
    if normalized in {
        "false",
        "0",
        "no",
        "closed",
        "suspended",
        "暂停",
        "暂停申购",
        "暂停赎回",
        "不可申购",
        "不可赎回",
    }:
        return False
    return None


def _operation_open(status: pd.Series, operation: str) -> bool | None:
    aliases = (
        (
            "is_purchasable",
            "open_subscription",
            "subscription_open",
            "purchase_open",
            "is_open_for_purchase",
            "can_purchase",
            "purchase_status",
            "subscription_status",
        )
        if operation == "purchase"
        else (
            "is_redeemable",
            "open_redemption",
            "redemption_open",
            "redeem_open",
            "is_open_for_redemption",
            "can_redeem",
            "redemption_status",
        )
    )
    for column in aliases:
        if column in status.index:
            flag = _parse_open_flag(status[column])
            if flag is not None:
                return flag
    for column in ("is_open", "open_status", "status"):
        if column in status.index:
            flag = _parse_open_flag(status[column])
            if flag is not None:
                return flag
    return None


def _purchase_limit(status: pd.Series) -> Decimal | None:
    for column in ("purchase_limit", "subscription_limit", "max_purchase_amount"):
        if column not in status.index or pd.isna(status[column]):
            continue
        try:
            return Decimal(str(status[column]))
        except InvalidOperation:
            return Decimal("0")
    return None


def _signal_for_family(
    family_id: str,
    group: pd.DataFrame,
    features: pd.DataFrame,
    fund_status: pd.DataFrame,
    fee_schedules: Mapping[str, FundFeeSchedule],
    as_of_date: date,
    expected_holding_days: int | None,
    config: FundStrategyConfig,
) -> Signal:
    representative = _representative_symbol(group)
    benchmark_values = group["benchmark_symbol"].dropna().astype(str).unique()
    if len(benchmark_values) != 1 or not benchmark_values[0]:
        return _blocking_signal(
            representative, "基准映射缺失或不唯一，禁止生成基金交易建议", as_of_date, config
        )
    benchmark_symbol = benchmark_values[0]
    benchmark_rows = features.loc[features["symbol"].astype(str).eq(benchmark_symbol)]
    if benchmark_rows.empty:
        return _blocking_signal(
            representative,
            f"基准{benchmark_symbol}在目标日之前的技术数据不足",
            as_of_date,
            config,
        )
    feature_row = benchmark_rows.iloc[-1]
    feature_date = next(
        (
            _as_date(feature_row[column])
            for column in ("trade_date", "as_of_date")
            if column in feature_row.index
        ),
        None,
    )
    if feature_date is not None and feature_date != as_of_date:
        return _blocking_signal(
            representative,
            f"基准{benchmark_symbol}行情未更新到策略日期",
            as_of_date,
            config,
        )
    score, trend_ok = benchmark_score(feature_row)
    if not np.isfinite(score):
        return _blocking_signal(
            representative, "基准技术指标无效，禁止生成基金交易建议", as_of_date, config
        )

    schedules = _share_schedules(group, fee_schedules)
    if schedules is None:
        return _blocking_signal(
            representative,
            "A/C份额映射或产品具体费率不完整，禁止推荐具体份额",
            as_of_date,
            config,
            score=score,
        )
    bucket = _normalize_bucket(group, expected_holding_days)
    holding_days = expected_holding_days
    if holding_days is None:
        holding_days = (
            config.core_holding_days if bucket == "core" else config.rotation_holding_days
        )
    if bucket == "rotation" and holding_days < config.minimum_rotation_days:
        return _blocking_signal(
            representative,
            f"预计持有{holding_days}日，少于轮动基金最低{config.minimum_rotation_days}日",
            as_of_date,
            config,
            score=score,
        )

    try:
        decision = choose_share_class(
            schedules[0],
            schedules[1],
            config.fee_comparison_amount,
            holding_days,
            as_of_date,
        )
    except FeeScheduleError as exc:
        return _blocking_signal(
            representative,
            f"产品具体费率未验证、已过期或不适用：{exc}",
            as_of_date,
            config,
            score=score,
        )

    status = _status_row(fund_status, decision.selected_symbol, family_id, as_of_date)
    if status is None:
        return _blocking_signal(
            decision.selected_symbol,
            "缺少目标份额的最新净值与开放状态",
            as_of_date,
            config,
            score=score,
        )
    if _nav_date(status) != as_of_date:
        return _blocking_signal(
            decision.selected_symbol,
            "目标份额净值尚未更新到策略日期，禁止交易",
            as_of_date,
            config,
            score=score,
        )
    broken_120_trend = _row_value(feature_row, "close") < _row_value(feature_row, "ma_120")
    buy_candidate = score > 0 and trend_ok
    sell_candidate = score < 0 and broken_120_trend
    if buy_candidate:
        if _operation_open(status, "purchase") is not True:
            return _blocking_signal(
                decision.selected_symbol,
                "目标份额暂停申购或申购状态未确认",
                as_of_date,
                config,
                score=score,
            )
        limit = _purchase_limit(status)
        if limit is not None and limit < config.fee_comparison_amount:
            return _blocking_signal(
                decision.selected_symbol,
                f"申购限额{limit:.2f}元低于计划比较金额{config.fee_comparison_amount:.2f}元",
                as_of_date,
                config,
                score=score,
            )
    elif sell_candidate:
        if _operation_open(status, "redemption") is not True:
            return _blocking_signal(
                decision.selected_symbol,
                "目标份额暂停赎回或赎回状态未确认",
                as_of_date,
                config,
                score=score,
            )

    if not _evaluation_due(group, bucket, as_of_date):
        cadence = "每月" if bucket == "core" else "每周"
        return Signal(
            symbol=decision.selected_symbol,
            asset_type=AssetType.FUND,
            action=Action.HOLD,
            score=score,
            target_weight=Decimal("0"),
            reasons=(f"{cadence}评估周期尚未到期", *decision.reasons),
            as_of_date=as_of_date,
            valid_until=_next_submission_deadline(as_of_date),
            strategy_version=config.version,
        )

    if buy_candidate:
        action = Action.BUY
        target_weight = (
            config.core_target_weight if bucket == "core" else config.rotation_target_weight
        )
        signal_reasons = (
            f"基准{benchmark_symbol}得分{score:.4f}为正且满足120/200日上升趋势",
            f"基金策略桶：{'核心' if bucket == 'core' else '轮动'}",
            *decision.reasons,
        )
    elif sell_candidate:
        action = Action.SELL
        target_weight = Decimal("0")
        signal_reasons = (
            f"基准{benchmark_symbol}得分{score:.4f}为负且跌破120日均线",
            f"基金策略桶：{'核心' if bucket == 'core' else '轮动'}",
            *decision.reasons,
        )
    else:
        action = Action.HOLD
        target_weight = Decimal("0")
        signal_reasons = (
            f"基准{benchmark_symbol}未同时满足买入或卖出规则",
            f"基金策略桶：{'核心' if bucket == 'core' else '轮动'}",
            *decision.reasons,
        )

    return Signal(
        symbol=decision.selected_symbol,
        asset_type=AssetType.FUND,
        action=action,
        score=score,
        target_weight=target_weight,
        reasons=signal_reasons,
        as_of_date=as_of_date,
        valid_until=_next_submission_deadline(as_of_date),
        strategy_version=config.version,
    )


def generate_fund_signals(
    fund_pool: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
    fund_status: pd.DataFrame,
    fee_schedules: Mapping[str, FundFeeSchedule],
    as_of_date: date,
    expected_holding_days: int | None,
    config: FundStrategyConfig = _DEFAULT_CONFIG,
) -> list[Signal]:
    """Generate one fail-closed, fee-aware signal per fund family."""

    if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
        # datetime subclasses date: without the explicit exclusion a timestamp
        # slips through and every later comparison against a calendar date
        # raises an opaque "can't compare datetime.datetime to datetime.date".
        raise TypeError("as_of_date must be a date")
    if expected_holding_days is not None and (
        isinstance(expected_holding_days, bool)
        or not isinstance(expected_holding_days, int)
        or expected_holding_days < 0
    ):
        raise ValueError("expected_holding_days must be a non-negative integer or None")
    if not isinstance(fund_status, pd.DataFrame):
        raise TypeError("fund_status must be a pandas DataFrame")
    if not isinstance(fee_schedules, Mapping):
        raise TypeError("fee_schedules must be a mapping")

    pool = _canonical_fund_pool(fund_pool)
    pool = _filter_point_in_time_rows(pool, as_of_date, ("as_of_date",))
    if pool.empty:
        return []
    features = _benchmark_features(benchmark_prices, as_of_date)

    signals: list[Signal] = []
    grouped = pool.sort_values(["family_id", "share_class", "symbol"], kind="mergesort").groupby(
        "family_id", sort=True, dropna=False
    )
    for family_id, group in grouped:
        signals.append(
            _signal_for_family(
                str(family_id),
                group,
                features,
                fund_status,
                fee_schedules,
                as_of_date,
                expected_holding_days,
                config,
            )
        )
    return signals
