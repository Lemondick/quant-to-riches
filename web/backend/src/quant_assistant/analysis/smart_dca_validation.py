"""Walk-forward validation for Smart DCA parameters.

This is a research-only evaluator.  It deliberately uses only valuation rows
dated no later than each decision date and never turns a validation result into
an executable order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from math import isfinite

import numpy as np
import pandas as pd

from quant_assistant.analysis.research_statistics import (
    ParameterSensitivityResult,
    scan_parameter_sensitivity,
)
from quant_assistant.common.serialization import canonical_json
from quant_assistant.strategies.smart_dca import (
    SmartDcaAction,
    SmartDcaConfig,
    SmartDcaInput,
    calculate_smart_dca,
)


@dataclass(frozen=True, slots=True)
class SmartDcaValidationWindow:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date

    def __post_init__(self) -> None:
        values = (
            self.train_start,
            self.train_end,
            self.validation_start,
            self.validation_end,
            self.test_start,
            self.test_end,
        )
        if any(not isinstance(value, date) for value in values):
            raise TypeError("walk-forward window dates must be dates")
        if not (
            self.train_start
            <= self.train_end
            < self.validation_start
            <= self.validation_end
            < self.test_start
            <= self.test_end
        ):
            raise ValueError("Smart DCA walk-forward periods must be chronological and disjoint")


@dataclass(frozen=True, slots=True)
class SmartDcaValidationFold:
    window: SmartDcaValidationWindow
    selected_parameters: Mapping[str, object]
    train_mean_return: float
    validation_mean_return: float
    test_mean_return: float
    test_observations: int
    test_investments: int


@dataclass(frozen=True, slots=True)
class SmartDcaValidationResult:
    folds: tuple[SmartDcaValidationFold, ...]
    candidate_scores: pd.DataFrame
    research_only: bool
    blockers: tuple[str, ...] = ()


def _prepare(observations: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(observations, pd.DataFrame):
        raise TypeError("Smart DCA observations must be a pandas DataFrame")
    required = {
        "as_of_date",
        "symbol",
        "current_price",
        "ma120",
        "ma200",
        "pe",
        "pb",
        "forward_return",
    }
    missing = sorted(required.difference(observations.columns))
    if missing:
        raise ValueError(f"Smart DCA observations missing columns: {', '.join(missing)}")
    frame = observations.loc[:, sorted(required)].copy(deep=True)
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"], errors="coerce").dt.date
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    for column in ("current_price", "ma120", "ma200", "pe", "pb", "forward_return"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.loc[
        frame["as_of_date"].notna()
        & frame["symbol"].notna()
        & frame["symbol"].ne("")
        & np.isfinite(frame["current_price"])
        & np.isfinite(frame["ma120"])
        & np.isfinite(frame["ma200"])
        & np.isfinite(frame["forward_return"])
    ]
    if frame.duplicated(["as_of_date", "symbol"]).any():
        raise ValueError("Smart DCA observations contain duplicate date/symbol rows")
    return frame.sort_values(["as_of_date", "symbol"], kind="mergesort").reset_index(drop=True)


def _score_period(
    frame: pd.DataFrame,
    start: date,
    end: date,
    config: SmartDcaConfig,
    base_amount: Decimal,
) -> tuple[float, int, int]:
    period = frame.loc[frame["as_of_date"].between(start, end)]
    if period.empty:
        return float("nan"), 0, 0
    realized: list[float] = []
    investments = 0
    for row in period.to_dict("records"):
        history = frame.loc[frame["as_of_date"].le(row["as_of_date"]), ["as_of_date", "pe", "pb"]]
        pe = None if pd.isna(row["pe"]) else float(row["pe"])
        pb = None if pd.isna(row["pb"]) else float(row["pb"])
        decision = calculate_smart_dca(
            SmartDcaInput(
                symbol=str(row["symbol"]),
                as_of_date=row["as_of_date"],
                base_amount=base_amount,
                current_price=float(row["current_price"]),
                ma120=float(row["ma120"]),
                ma200=float(row["ma200"]),
                valuation_history=history,
                pe=pe,
                pb=pb,
            ),
            config,
        )
        if decision.action is SmartDcaAction.INVEST:
            realized.append(float(row["forward_return"]))
            investments += 1
        else:
            realized.append(0.0)
    mean_return = float(np.mean(realized)) if realized else float("nan")
    return mean_return, len(realized), investments


def run_smart_dca_walk_forward(
    observations: pd.DataFrame,
    windows: Sequence[SmartDcaValidationWindow],
    parameter_candidates: Sequence[Mapping[str, object]],
    *,
    base_config: SmartDcaConfig | None = None,
    base_amount: Decimal = Decimal("1000"),
) -> SmartDcaValidationResult:
    """Select parameters on validation windows and report untouched test windows."""

    if not windows:
        raise ValueError("at least one Smart DCA validation window is required")
    if not parameter_candidates:
        raise ValueError("at least one Smart DCA parameter candidate is required")
    frame = _prepare(observations)
    config = base_config or SmartDcaConfig()
    if not isinstance(config, SmartDcaConfig):
        raise TypeError("base_config must be SmartDcaConfig")
    if not isinstance(base_amount, Decimal) or not base_amount.is_finite() or base_amount < 0:
        raise ValueError("base_amount must be a finite non-negative Decimal")
    candidates: list[tuple[dict[str, object], SmartDcaConfig]] = []
    blockers: list[str] = []
    for raw in parameter_candidates:
        if not isinstance(raw, Mapping):
            raise TypeError("Smart DCA parameter candidates must be mappings")
        parameters = dict(raw)
        try:
            candidates.append((parameters, replace(config, **parameters)))
        except (TypeError, ValueError) as exc:
            blockers.append(f"参数候选无效:{type(exc).__name__}")
    if not candidates:
        return SmartDcaValidationResult((), pd.DataFrame(), True, tuple(dict.fromkeys(blockers)))

    score_rows: list[dict[str, object]] = []
    folds: list[SmartDcaValidationFold] = []
    previous_test_end: date | None = None
    for window in windows:
        if previous_test_end is not None and window.test_start <= previous_test_end:
            raise ValueError("Smart DCA test windows must not overlap")
        previous_test_end = window.test_end
        evaluations: list[tuple[dict[str, object], SmartDcaConfig, float, float, int]] = []
        for parameters, candidate in candidates:
            train_return, train_count, _ = _score_period(
                frame, window.train_start, window.train_end, candidate, base_amount
            )
            validation_return, validation_count, _ = _score_period(
                frame, window.validation_start, window.validation_end, candidate, base_amount
            )
            score_rows.append(
                {
                    "train_start": window.train_start,
                    "validation_start": window.validation_start,
                    "parameters": canonical_json(parameters),
                    "train_mean_return": train_return,
                    "validation_mean_return": validation_return,
                    "train_observations": train_count,
                    "validation_observations": validation_count,
                }
            )
            evaluations.append(
                (parameters, candidate, train_return, validation_return, validation_count)
            )
        selected = max(
            evaluations,
            key=lambda item: (
                -float("inf") if not isfinite(item[3]) else item[3],
                -float("inf") if not isfinite(item[2]) else item[2],
                canonical_json(item[0]),
            ),
        )
        test_return, test_count, test_investments = _score_period(
            frame, window.test_start, window.test_end, selected[1], base_amount
        )
        folds.append(
            SmartDcaValidationFold(
                window=window,
                selected_parameters=dict(selected[0]),
                train_mean_return=selected[2],
                validation_mean_return=selected[3],
                test_mean_return=test_return,
                test_observations=test_count,
                test_investments=test_investments,
            )
        )
    if not folds:
        blockers.append("没有可用的Smart DCA验证窗口")
    if frame.empty:
        blockers.append("Smart DCA验证数据为空")
    return SmartDcaValidationResult(
        folds=tuple(folds),
        candidate_scores=pd.DataFrame(score_rows),
        research_only=True,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def scan_smart_dca_parameters(
    observations: pd.DataFrame,
    parameter_candidates: Sequence[Mapping[str, object]],
    *,
    start: date,
    end: date,
    base_config: SmartDcaConfig | None = None,
    base_amount: Decimal = Decimal("1000"),
) -> ParameterSensitivityResult:
    """Scan Smart DCA candidates over one untouched research period.

    The score is the mean supplied ``forward_return`` after applying the
    candidate's decision rule.  A candidate that cannot be evaluated is kept
    as a blocked row; the generic scanner therefore withholds a best parameter
    from a partial scan.  This function is for sensitivity analysis only and
    never mutates ``base_config`` or turns a score into an order.
    """

    if not isinstance(start, date) or not isinstance(end, date):
        raise TypeError("Smart DCA sensitivity dates must be dates")
    if start > end:
        raise ValueError("Smart DCA sensitivity start must not exceed end")
    frame = _prepare(observations)
    config = base_config or SmartDcaConfig()
    if not isinstance(config, SmartDcaConfig):
        raise TypeError("base_config must be SmartDcaConfig")
    if not isinstance(base_amount, Decimal) or not base_amount.is_finite() or base_amount < 0:
        raise ValueError("base_amount must be a finite non-negative Decimal")

    def evaluate(parameters: Mapping[str, object]) -> Mapping[str, object]:
        candidate = replace(config, **dict(parameters))
        mean_return, observations_count, investments = _score_period(
            frame, start, end, candidate, base_amount
        )
        if not isfinite(mean_return) or observations_count <= 0:
            raise ValueError("敏感性区间没有可用观测")
        return {
            "score": mean_return,
            "observations": observations_count,
            "investments": investments,
            "investment_rate": investments / observations_count,
        }

    return scan_parameter_sensitivity(parameter_candidates, evaluate)


__all__ = [
    "SmartDcaValidationFold",
    "SmartDcaValidationResult",
    "SmartDcaValidationWindow",
    "scan_smart_dca_parameters",
    "run_smart_dca_walk_forward",
]
