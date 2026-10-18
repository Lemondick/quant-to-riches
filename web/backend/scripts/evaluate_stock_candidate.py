"""Apply frozen multi-window and cost-stress gates to stock research artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import date
from math import isfinite
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard", type=Path, action="append", required=True)
    parser.add_argument("--stress", type=Path, action="append", default=[])
    parser.add_argument("--min-sessions", type=int, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate(args.standard, args.stress, args.min_sessions)
    except (ValueError, OSError) as exc:
        report = {"passed": False, "research_only": True, "failures": [str(exc)]}
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 2


def evaluate(
    standard_paths: list[Path], stress_paths: list[Path], min_sessions: int = 120
) -> dict[str, object]:
    if not standard_paths:
        raise ValueError("at least one standard artifact is required")
    if isinstance(min_sessions, bool) or not isinstance(min_sessions, int) or min_sessions < 120:
        raise ValueError("min_sessions must be an integer of at least 120")
    standard = [_load(path) for path in standard_paths]
    stress = [_load(path) for path in stress_paths]
    failures: list[str] = []
    if len(standard) < 3:
        failures.append("standard:fewer_than_three_windows")
    standard_windows = [_window(item) for item in standard]
    stress_windows = [_window(item) for item in stress]
    for label, windows in (("standard", standard_windows), ("stress", stress_windows)):
        ordered = sorted(windows)
        if any(right[0] <= left[1] for left, right in zip(ordered, ordered[1:], strict=False)):
            failures.append(f"{label}:overlapping_windows")
    if sorted(standard_windows) != sorted(stress_windows):
        failures.append("stress:windows_do_not_match_standard")
    frozen_config = _config(standard[0])
    for label, item in [("standard", value) for value in standard] + [
        ("stress", value) for value in stress
    ]:
        metrics = item["metrics"]
        sessions = item["execution"]["sessions"]
        if _config(item) != frozen_config:
            failures.append(f"{label}:strategy_config_mismatch")
        expected_cost = 1 if label == "standard" else 2
        if float(item["request"]["cost_multiplier"]) != expected_cost:
            failures.append(f"{label}:expected_cost_multiplier_{expected_cost}")
        if label == "stress":
            for baseline in standard:
                if (
                    _window(baseline) == _window(item)
                    and sessions != baseline["execution"]["sessions"]
                ):
                    failures.append("stress:session_count_mismatch")
        if sessions < min_sessions:
            failures.append(f"{label}:sessions_below_{min_sessions}")
        if float(metrics["max_drawdown"]) < -0.30:
            failures.append(f"{label}:drawdown_below_minus_30pct")
        if float(metrics["total_return"]) <= 0:
            failures.append(f"{label}:non_positive_total_return")
        if label == "standard" and float(metrics["excess_return"]) <= 0:
            failures.append(f"{label}:non_positive_excess_return")
    return {
        "passed": not failures,
        "research_only": True,
        "minimum_windows": 3,
        "min_sessions": min_sessions,
        "standard": [
            _summary(path, item) for path, item in zip(standard_paths, standard, strict=True)
        ],
        "stress": [_summary(path, item) for path, item in zip(stress_paths, stress, strict=True)],
        "failures": list(dict.fromkeys(failures)),
    }


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), dict) for key in ("metrics", "execution", "request")
    ):
        raise ValueError(f"invalid research artifact: {path}")
    sessions = payload["execution"].get("sessions")
    if isinstance(sessions, bool) or not isinstance(sessions, int) or sessions <= 0:
        raise ValueError(f"invalid sessions: {path}")
    for name in ("total_return", "excess_return", "max_drawdown", "turnover"):
        _finite_number(payload["metrics"].get(name), f"{path}:metrics.{name}")
    drawdown = float(payload["metrics"]["max_drawdown"])
    if not -1 <= drawdown <= 0 or float(payload["metrics"]["turnover"]) < 0:
        raise ValueError(f"invalid drawdown or turnover: {path}")
    _finite_number(payload["request"].get("cost_multiplier"), f"{path}:cost_multiplier")
    start, end = _window(payload)
    if sessions > (end - start).days + 1:
        raise ValueError(f"sessions exceed window calendar days: {path}")
    _config(payload)
    return payload


def _finite_number(value: object, label: str) -> None:
    try:
        valid = not isinstance(value, bool) and isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise ValueError(f"{label} must be a finite number")


def _window(payload: dict[str, object]) -> tuple[date, date]:
    request = payload["request"]
    try:
        start = date.fromisoformat(request["start"])
        end = date.fromisoformat(request["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("request must contain ISO start/end dates") from exc
    if start >= end:
        raise ValueError("window start must precede end")
    return start, end


def _config(payload: dict[str, object]) -> dict[str, object]:
    config = payload["request"].get("config")
    required = {
        "version",
        "top_candidate_count",
        "min_listing_days",
        "min_amount_20",
        "exit_percentile",
        "max_names",
        "target_weight",
        "stop_loss_pct",
    }
    if not isinstance(config, dict) or not required.issubset(config):
        raise ValueError("request must contain the complete frozen stock config")
    # The runner adds the cost multiplier to the display version for stress runs.
    return {key: value for key, value in config.items() if key != "version"}


def _summary(path: Path, payload: dict[str, object]) -> dict[str, object]:
    return {
        "path": str(path),
        "start": payload["request"]["start"],
        "end": payload["request"]["end"],
        "cost_multiplier": payload["request"]["cost_multiplier"],
        "sessions": payload["execution"]["sessions"],
        "total_return": payload["metrics"]["total_return"],
        "excess_return": payload["metrics"].get("excess_return"),
        "max_drawdown": payload["metrics"]["max_drawdown"],
        "turnover": payload["metrics"]["turnover"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
