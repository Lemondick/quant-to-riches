"""Replay archived public fund NAVs as risk references, never as account or strategy evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.portfolio.drawdown_control import validate_drawdown

MINIMUM_OBSERVATIONS = 120
CONTROL_LINE = 0.20


def replay_archive(
    evidence_dir: Path, *, symbols: tuple[str, ...], start: str, end: str
) -> tuple[dict[str, object], dict[str, pd.DataFrame]]:
    """Read a completed probe; validate hashes and dates before computing risk.

    This reference starts at one, holds a fixed number of units, and excludes
    entry/exit fees. It is not an executable strategy or a total-return series
    for a fund with distributions or splits. No database or network is opened.
    """
    today = datetime.now(SHANGHAI_TZ).date()
    start_date = datetime.fromisoformat(start).date()
    end_date = datetime.fromisoformat(end).date()
    if start_date > end_date or end_date > today:
        raise ValueError("replay dates must be ordered and not in the future")
    if (
        not 1 <= len(symbols) <= 5
        or len(set(symbols)) != len(symbols)
        or any(not re.fullmatch(r"[0-9]{6}", symbol) for symbol in symbols)
    ):
        raise ValueError("provide one to five distinct six-digit fund codes")
    root = evidence_dir.resolve()
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    finished = datetime.fromisoformat(manifest["finished_at"])
    if finished.tzinfo is None or end_date > finished.astimezone(SHANGHAI_TZ).date():
        raise ValueError("the evidence snapshot predates the replay end")
    if not set(symbols).issubset(manifest["symbols"]):
        raise ValueError("the evidence snapshot does not cover the requested funds")
    files: dict[str, Path] = {}
    for entry in manifest["files"]:
        name = entry["name"]
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or ":" in name
            or "/" in name
            or "\\" in name
            or name in files
        ):
            raise ValueError("invalid or duplicate evidence filename")
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("evidence file escapes the snapshot directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"evidence hash mismatch: {name}")
        files[name] = path
    sources = {item["name"]: item for item in manifest["results"]}
    report: dict[str, object] = {
        "generated_at": datetime.now(SHANGHAI_TZ).isoformat(),
        "scope": "public_fund_nav_reference",
        "readiness": "research_only",
        "qualifies_strategy": False,
        "requested_start": start_date.isoformat(),
        "requested_end": end_date.isoformat(),
        "evidence_finished_at": finished.isoformat(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "verified_file_count": len(files),
        "method": "fixed_units_unit_nav_index_excluding_entry_and_exit_fees",
        "limitations": [
            "public_source_not_independently_verified",
            "empty_corporate_action_tables_require_source_review",
            "alipay_actual_fees_not_verified",
            "historical_subscription_redemption_rules_not_verified",
            "nav_publication_timestamps_not_verified",
            "not_a_strategy_backtest_or_live_paper_session",
        ],
        "funds": [],
    }
    references: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        item: dict[str, object] = {"symbol": symbol, "status": "unavailable"}
        report["funds"].append(item)
        for label in ("unit-nav", "accumulated-nav", "dividends", "splits"):
            name = f"{symbol}-{label}"
            source = sources.get(name, {})
            if source.get("available") is not True or f"{name}.csv" not in files:
                item["blockers"] = [f"missing_verified_source:{label}"]
                break
        else:
            # A raw unit-NAV drop around a distribution/split is not a loss.
            # Do not manufacture an adjusted series from incomplete actions.
            if any(
                sources[f"{symbol}-{kind}"].get("rows") != 0 for kind in ("dividends", "splits")
            ):
                item["blockers"] = ["corporate_actions_need_adjusted_total_return_replay"]
                continue
            unit = pd.read_csv(files[f"{symbol}-unit-nav.csv"])
            accumulated = pd.read_csv(files[f"{symbol}-accumulated-nav.csv"])
            reference = _reference_frame(unit, accumulated, start_date, end_date)
            if len(reference) < MINIMUM_OBSERVATIONS:
                item["blockers"] = ["fewer_than_120_nav_observations"]
                item["observations"] = len(reference)
                continue
            validation = validate_drawdown(
                f"public-nav-{symbol}", reference, threshold=CONTROL_LINE
            )
            item.update(
                status="computed",
                first_nav_date=reference["as_of_date"].iloc[0].isoformat(),
                last_nav_date=reference["as_of_date"].iloc[-1].isoformat(),
                observations=len(reference),
                nav_change=float(reference["unit_nav"].iloc[-1] - 1),
                drawdown=asdict(validation),
            )
            references[symbol] = reference
    return report, references


def _reference_frame(unit: pd.DataFrame, accumulated: pd.DataFrame, start, end) -> pd.DataFrame:
    for frame, column in ((unit, "单位净值"), (accumulated, "累计净值")):
        if not {"净值日期", column}.issubset(frame.columns):
            raise ValueError("missing NAV columns")
        frame["净值日期"] = pd.to_datetime(frame["净值日期"], errors="raise").dt.date
        if frame["净值日期"].isna().any() or frame["净值日期"].duplicated().any():
            raise ValueError("invalid or duplicate NAV dates")
        if not frame["净值日期"].is_monotonic_increasing:
            raise ValueError("NAV dates must be ordered")
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    selected = unit.loc[unit["净值日期"].between(start, end), ["净值日期", "单位净值"]]
    if selected.empty:
        return pd.DataFrame(columns=["as_of_date", "source_unit_nav", "unit_nav", "drawdown"])
    joined = selected.merge(accumulated, on="净值日期", how="left", validate="one_to_one")
    for column in ("单位净值", "累计净值"):
        if not all(math.isfinite(float(value)) and value > 0 for value in joined[column]):
            raise ValueError("missing, nonfinite or nonpositive NAV values in the replay window")
    if not all(
        math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
        for a, b in zip(joined["单位净值"], joined["累计净值"], strict=True)
    ):
        raise ValueError("unit and accumulated NAV differ; corporate action review required")
    reference = joined.rename(columns={"净值日期": "as_of_date", "单位净值": "source_unit_nav"})
    reference["unit_nav"] = reference["source_unit_nav"] / reference["source_unit_nav"].iloc[0]
    reference["drawdown"] = 1 - reference["unit_nav"] / reference["unit_nav"].cummax()
    return reference[["as_of_date", "source_unit_nav", "unit_nav", "drawdown"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must be a new directory")
    try:
        report, references = replay_archive(
            args.evidence_dir, symbols=tuple(args.symbol), start=args.start, end=args.end
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True, exist_ok=False)
    for symbol, frame in references.items():
        frame.to_csv(args.output / f"{symbol}-nav-reference.csv", index=False, encoding="utf-8-sig")
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    (args.output / "nav-risk-report.json").write_text(payload + "\n", encoding="utf-8")
    print(payload)
    # A successful reference calculation does not imply risk/strategy acceptance.
    return 0 if len(references) == len(args.symbol) else 2


if __name__ == "__main__":
    raise SystemExit(main())
