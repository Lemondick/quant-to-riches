"""Fail-closed validation for normalized market datasets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from quant_assistant.models.entities import DataStatus


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class ValidationResult:
    dataset: str
    status: DataStatus
    issues: tuple[ValidationIssue, ...]


Validator = Callable[[str, pd.DataFrame, date], list[ValidationIssue]]


def validate_dataset(dataset: str, frame: pd.DataFrame, expected_date: date) -> ValidationResult:
    validator = VALIDATORS.get(dataset)
    if validator is None:
        issue = ValidationIssue(
            code="unknown_dataset",
            message=f"no validator is registered for dataset: {dataset}",
            blocking=True,
        )
        return ValidationResult(dataset, DataStatus.BLOCKED, (issue,))

    issues = tuple(validator(dataset, frame, expected_date))
    if any(issue.blocking for issue in issues):
        status = DataStatus.BLOCKED
    elif issues:
        status = DataStatus.STALE
    else:
        status = DataStatus.VALID
    return ValidationResult(dataset, status, issues)


def validate_daily_prices(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    required = {"symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"}
    issues = _base_issues(frame, required)
    if issues:
        return issues

    issues.extend(_duplicate_issues(frame, ["symbol", "trade_date"]))
    issues.extend(_latest_date_issues(frame, "trade_date", expected_date))

    issues.extend(_ohlc_issues(frame))
    return issues


def validate_fund_daily(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    required = {
        "symbol",
        "nav_date",
        "unit_nav",
        "purchase_status",
        "redemption_status",
    }
    issues = _base_issues(frame, required)
    if issues:
        return issues
    issues.extend(_duplicate_issues(frame, ["symbol", "nav_date"]))
    issues.extend(_latest_date_issues(frame, "nav_date", expected_date))
    issues.extend(_positive_numeric_issues(frame, ["unit_nav"], "invalid_nav"))
    if frame[["purchase_status", "redemption_status"]].isna().any(axis=None):
        issues.append(
            _blocking("missing_fund_status", "fund purchase/redemption status is missing")
        )
    return issues


def validate_fund_nav(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    required = {"symbol", "nav_date", "unit_nav"}
    issues = _base_issues(frame, required)
    if issues:
        return issues
    issues.extend(_duplicate_issues(frame, ["symbol", "nav_date"]))
    issues.extend(_latest_date_issues(frame, "nav_date", expected_date))
    issues.extend(_positive_numeric_issues(frame, ["unit_nav"], "invalid_nav"))
    return issues


def validate_instruments(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    required = {"symbol", "asset_type", "name", "as_of_date"}
    issues = _base_issues(frame, required)
    if issues:
        return issues
    issues.extend(_duplicate_issues(frame, ["symbol"]))
    issues.extend(_latest_date_issues(frame, "as_of_date", expected_date))

    stocks = frame.loc[frame["asset_type"].astype(str).eq("stock")]
    if not stocks.empty:
        required_stock_fields = ("exchange", "board", "listed_on", "industry")
        missing_fields = [column for column in required_stock_fields if column not in stocks]
        if missing_fields:
            issues.append(
                _blocking(
                    "missing_stock_metadata",
                    f"stock metadata columns are missing: {', '.join(missing_fields)}",
                )
            )
        if "listed_on" in stocks:
            listed_on = pd.to_datetime(stocks["listed_on"], errors="coerce")
            if listed_on.isna().any() or listed_on.gt(pd.Timestamp(expected_date)).any():
                issues.append(
                    _blocking(
                        "invalid_listing_date",
                        "listed_on must be present and no later than the snapshot date",
                    )
                )
        # Industry is an eligibility field, not a catalogue-wide invariant.  Public
        # exchange catalogues are authoritative for listings but do not always expose
        # an industry for every security (notably some SH listings).  Those rows stay
        # in the instrument master for auditability and are excluded by the strategy
        # selector below.  Fail closed only when no main-board stock can be classified.
        missing_industry_column = "industry" not in stocks
        strategy_candidates = stocks
        if "board" in stocks:
            strategy_candidates = stocks.loc[
                stocks["board"].astype("string").str.lower().isin({"main", "main_board", "主板"})
            ]
        has_classified_candidate = False
        if not missing_industry_column and not strategy_candidates.empty:
            industry = strategy_candidates["industry"].astype("string").str.strip()
            has_classified_candidate = bool((industry.notna() & industry.ne("")).any())
        if missing_industry_column or (
            not strategy_candidates.empty and not has_classified_candidate
        ):
            issues.append(
                _blocking(
                    "missing_industry",
                    "at least one main-board stock must have a valid industry",
                )
            )
    return issues


def _base_issues(frame: pd.DataFrame, required: set[str]) -> list[ValidationIssue]:
    if frame.empty:
        return [_blocking("empty_dataset", "dataset contains no rows")]
    missing = sorted(required.difference(frame.columns))
    if missing:
        return [
            _blocking(
                "missing_columns",
                f"required columns are missing: {', '.join(missing)}",
            )
        ]
    return []


def _duplicate_issues(frame: pd.DataFrame, keys: list[str]) -> list[ValidationIssue]:
    if frame.duplicated(keys, keep=False).any():
        return [_blocking("duplicate_key", f"duplicate rows found for key: {', '.join(keys)}")]
    return []


def _latest_date_issues(
    frame: pd.DataFrame, column: str, expected_date: date
) -> list[ValidationIssue]:
    values = pd.to_datetime(frame[column], errors="coerce")
    if values.isna().any():
        return [_blocking("invalid_date", f"{column} contains invalid dates")]
    latest = values.max().date()
    if latest != expected_date:
        return [
            _blocking(
                "unexpected_date",
                f"latest {column} {latest.isoformat()} does not match expected date "
                f"{expected_date.isoformat()}",
            )
        ]
    return []


def _history_date_issues(
    frame: pd.DataFrame, column: str, expected_date: date
) -> list[ValidationIssue]:
    values = pd.to_datetime(frame[column], errors="coerce")
    if values.isna().any():
        return [_blocking("invalid_date", f"{column} contains invalid dates")]
    if values.gt(pd.Timestamp(expected_date)).any():
        return [
            _blocking(
                "future_date",
                f"{column} contains observations after {expected_date.isoformat()}",
            )
        ]
    return []


def _ohlc_issues(frame: pd.DataFrame) -> list[ValidationIssue]:
    """Return the shared numeric and high/low consistency checks for OHLC data."""

    numeric = frame.loc[:, ["open", "high", "low", "close", "volume", "amount"]].apply(
        pd.to_numeric, errors="coerce"
    )
    prices = numeric.loc[:, ["open", "high", "low", "close"]].astype("float64")
    invalid_prices = ~np.isfinite(prices.to_numpy()) | (prices.to_numpy() <= 0)
    issues: list[ValidationIssue] = []
    if invalid_prices.any():
        issues.append(_blocking("invalid_price", "OHLC values must be finite and positive"))

    activity = numeric[["volume", "amount"]].astype("float64").to_numpy()
    invalid_activity = ~np.isfinite(activity) | (activity < 0)
    if invalid_activity.any():
        issues.append(
            _blocking("invalid_activity", "volume and amount must be finite and non-negative")
        )

    if not invalid_prices.any():
        invalid_high = numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)
        invalid_low = numeric["low"] > numeric[["open", "close", "high"]].min(axis=1)
        if invalid_high.any() or invalid_low.any():
            issues.append(_blocking("invalid_ohlc", "OHLC high/low relationships are invalid"))
    return issues


def validate_price_history(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    """Validate a historical price slice without requiring a holiday observation."""

    required = {"symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"}
    issues = _base_issues(frame, required)
    if issues:
        return issues
    issues.extend(_duplicate_issues(frame, ["symbol", "trade_date"]))
    issues.extend(_history_date_issues(frame, "trade_date", expected_date))
    issues.extend(_ohlc_issues(frame))
    return issues


def validate_point_in_time_prices(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    """Validate historical bars with explicit suspension and limit evidence."""

    required = {
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "suspended",
        "upper_limit",
        "lower_limit",
    }
    issues = _base_issues(frame, required)
    if issues:
        return issues
    issues.extend(_duplicate_issues(frame, ["symbol", "trade_date"]))
    issues.extend(_history_date_issues(frame, "trade_date", expected_date))
    ohlc_issues = _ohlc_issues(frame)
    issues.extend(ohlc_issues)
    if frame["suspended"].isna().any():
        issues.append(
            _blocking("missing_suspension_flag", "suspended must be present for every bar")
        )
    limits = frame.loc[:, ["upper_limit", "lower_limit"]].apply(pd.to_numeric, errors="coerce")
    limit_values = limits.astype("float64").to_numpy()
    invalid_limits = ~np.isfinite(limit_values) | (limit_values <= 0)
    if invalid_limits.any():
        issues.append(
            _blocking(
                "invalid_price_limits",
                "upper_limit and lower_limit must be finite and positive",
            )
        )
    if not invalid_limits.any() and (limits["lower_limit"] > limits["upper_limit"]).any():
        issues.append(_blocking("invalid_price_limits", "lower_limit must not exceed upper_limit"))
    return issues


def validate_fund_history(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    required = {"symbol", "nav_date", "unit_nav"}
    issues = _base_issues(frame, required)
    if issues:
        return issues
    issues.extend(_duplicate_issues(frame, ["symbol", "nav_date"]))
    issues.extend(_history_date_issues(frame, "nav_date", expected_date))
    issues.extend(_positive_numeric_issues(frame, ["unit_nav"], "invalid_nav"))
    return issues


def validate_fund_valuation_history(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    """Validate persisted PE/PB observations without filling missing metrics."""

    required = {"symbol", "as_of_date", "pe", "pb", "source"}
    issues = _base_issues(frame, required)
    if issues:
        return issues
    issues.extend(_duplicate_issues(frame, ["symbol", "as_of_date"]))
    issues.extend(_history_date_issues(frame, "as_of_date", expected_date))
    values = frame.loc[:, ["pe", "pb"]].apply(pd.to_numeric, errors="coerce")
    present = values.notna().any(axis=1)
    if (~present).any():
        issues.append(_blocking("missing_valuation", "每行至少需要一个有效的 PE 或 PB 观测"))
    invalid = values.notna() & (~np.isfinite(values) | values.le(0))
    if invalid.any().any():
        issues.append(_blocking("invalid_valuation", "PE/PB 必须是有限正数"))
    if frame["source"].astype(str).str.strip().eq("").any():
        issues.append(_blocking("missing_source", "估值证据必须记录来源"))
    return issues


def validate_fund_manager_history(
    dataset: str, frame: pd.DataFrame, expected_date: date
) -> list[ValidationIssue]:
    """Validate manager archive rows while retaining unavailable factor fields."""

    required = {"symbol", "as_of_date", "source"}
    issues = _base_issues(frame, required)
    if issues:
        return issues
    issues.extend(_duplicate_issues(frame, ["symbol", "as_of_date"]))
    issues.extend(_history_date_issues(frame, "as_of_date", expected_date))
    if frame["source"].astype(str).str.strip().eq("").any():
        issues.append(_blocking("missing_source", "经理证据必须记录来源"))
    for column in ("manager_tenure_years", "drawdown_recovery_days"):
        if column not in frame:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if ((values.notna()) & (~np.isfinite(values) | values.lt(0))).any():
            issues.append(_blocking("invalid_manager_metric", f"{column}必须是有限非负数"))
    if "manager_win_rate" in frame:
        values = pd.to_numeric(frame["manager_win_rate"], errors="coerce")
        if ((values.notna()) & (~np.isfinite(values) | values.lt(0) | values.gt(1))).any():
            issues.append(_blocking("invalid_manager_win_rate", "manager_win_rate必须在0到1之间"))
    if "alpha_stability" in frame:
        values = pd.to_numeric(frame["alpha_stability"], errors="coerce")
        if ((values.notna()) & (~np.isfinite(values) | values.lt(0) | values.gt(1))).any():
            issues.append(_blocking("invalid_alpha_stability", "alpha_stability必须在0到1之间"))
    return issues


def _positive_numeric_issues(
    frame: pd.DataFrame, columns: list[str], code: str
) -> list[ValidationIssue]:
    numeric = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce").to_numpy()
    if (~np.isfinite(numeric) | (numeric <= 0)).any():
        return [_blocking(code, f"{', '.join(columns)} must be finite and positive")]
    return []


def _blocking(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, blocking=True)


VALIDATORS: dict[str, Validator] = {
    "daily_prices": validate_daily_prices,
    "index_history": validate_daily_prices,
    "fund_daily": validate_fund_daily,
    "fund_nav": validate_fund_nav,
    "stock_history": validate_price_history,
    "point_in_time_prices": validate_point_in_time_prices,
    "fund_history": validate_fund_history,
    "fund_valuation_history": validate_fund_valuation_history,
    "fund_manager_history": validate_fund_manager_history,
    "instruments": validate_instruments,
}
