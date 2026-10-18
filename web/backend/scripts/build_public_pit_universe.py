"""Build dated metadata for a selected research pool from archived actual daily states.

This does not reconstruct the full historical market. Quarterly industry observations
are carried forward only after their query date, never backfilled from today's catalogue.
"""

import argparse
import hashlib
import json
import socket
from datetime import date
from pathlib import Path

import pandas as pd

from quant_assistant.common.time import now_from_clock
from quant_assistant.data.point_in_time import (
    POINT_IN_TIME_UNIVERSE_COLUMNS,
    load_point_in_time_universe,
)
from quant_assistant.providers.baostock import normalize_history, read_result, sdk_symbol
from quant_assistant.providers.free_limits import quote_symbol


def build_selected_metadata(raw, symbol, basic, observations):
    """Combine actual dated state with metadata known by that day."""
    quote_symbol(symbol)
    normalize_history(raw, symbol)  # Reject guessed flags and adjusted or partial bars.
    rows = basic.loc[basic["code"].eq(sdk_symbol(symbol))]
    if len(rows) != 1 or str(rows.iloc[0]["type"]) != "1":
        raise ValueError(f"{symbol}: no unique stock lifecycle evidence")
    listed_on = date.fromisoformat(str(rows.iloc[0]["ipoDate"]))
    delisted = str(rows.iloc[0]["outDate"]).strip()
    state = raw.loc[:, ["date", "tradestatus", "isST"]].copy()
    state["date"] = pd.to_datetime(state["date"], format="%Y-%m-%d")
    if state["date"].lt(pd.Timestamp(listed_on)).any():
        raise ValueError(f"{symbol}: daily evidence precedes listing")
    if delisted and state["date"].ge(pd.Timestamp(date.fromisoformat(delisted))).any():
        raise ValueError(f"{symbol}: daily evidence reaches delisting")
    required = {"code", "code_name", "industry", "updateDate", "queried_on"}
    if not required.issubset(observations.columns) or observations.empty:
        raise ValueError(f"{symbol}: missing historical industry evidence")
    industry = observations.copy()
    if not industry["code"].eq(sdk_symbol(symbol)).all():
        raise ValueError(f"{symbol}: wrong industry stock code")
    for column in ("updateDate", "queried_on"):
        industry[column] = pd.to_datetime(industry[column], format="%Y-%m-%d", errors="raise")
    if (
        industry["updateDate"].isna().any()
        or industry["queried_on"].isna().any()
        or industry["updateDate"].gt(industry["queried_on"]).any()
        or industry["queried_on"].duplicated().any()
    ):
        raise ValueError(f"{symbol}: industry observation is future-dated or duplicated")
    for column in ("industry", "code_name"):
        if industry[column].isna().any() or industry[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"{symbol}: empty industry/name evidence")
    joined = pd.merge_asof(
        state.sort_values("date"),
        industry.sort_values("queried_on"),
        left_on="date",
        right_on="queried_on",
        direction="backward",
    )
    if joined["industry"].isna().any():
        raise ValueError(f"{symbol}: industry evidence does not cover the first trading date")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "asset_type": "stock",
            "name": joined["code_name"],
            "exchange": symbol[-2:],
            "industry": joined["industry"],
            "board": "star"
            if symbol.startswith("688")
            else "gem"
            if symbol.startswith("30")
            else "main",
            "listed_on": listed_on,
            # A current lifecycle file must not expose a future delisting date.
            "delisted_on": None,
            "is_st": joined["isST"].eq("1"),
            "is_suspended": joined["tradestatus"].eq("0"),
            "status": "active",
            "as_of_date": joined["date"].dt.date,
        },
        columns=POINT_IN_TIME_UNIVERSE_COLUMNS,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--basic-file", type=Path, required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    symbols = tuple(dict.fromkeys(args.symbol))
    if len(symbols) > 30:
        parser.error("selected research pools are limited to 30 stocks")
    for symbol in symbols:
        quote_symbol(symbol)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be new or empty")
    args.output.mkdir(parents=True, exist_ok=True)
    basic = pd.read_csv(args.basic_file, dtype=str, keep_default_na=False)
    manifest = {
        "fetched_at": now_from_clock().isoformat(),
        "research_only": True,
        "scope": "selected_stock_research_universe",
        "membership_evidence": "actual dated BaoStock bars and stock lifecycle dates",
        "industry_sampling": "first supplied trading date in each calendar quarter",
        "symbols": symbols,
        "verified": False,
        "files": [],
        "inputs": [
            {
                "path": str(args.basic_file.resolve()),
                "sha256": hashlib.sha256(args.basic_file.read_bytes()).hexdigest(),
            }
        ],
    }

    def save_manifest():
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def archive(name, frame):
        path = args.output / name
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        manifest["files"].append(
            {
                "name": name,
                "rows": len(frame),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        save_manifest()

    import baostock as bs

    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(15)
    logged_in = False
    save_manifest()
    try:
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {login.error_code}")
        logged_in = True
        frames = []
        for symbol in symbols:
            path = args.raw_dir / f"baostock-full-{sdk_symbol(symbol)}.csv"
            raw = pd.read_csv(path, dtype=str, keep_default_na=False)
            normalize_history(raw, symbol)
            manifest["inputs"].append(
                {
                    "path": str(path.resolve()),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
            days = sorted(set(pd.to_datetime(raw["date"]).dt.date))
            queries = {}
            for day in days:
                queries.setdefault((day.year, (day.month - 1) // 3), day)
            observations = []
            for day in queries.values():
                frame = read_result(
                    bs.query_stock_industry(code=sdk_symbol(symbol), date=day.isoformat())
                )
                frame["queried_on"] = day.isoformat()
                archive(f"industry-{symbol}-{day}.csv", frame)
                observations.append(frame)
            selected = build_selected_metadata(raw, symbol, basic, pd.concat(observations))
            frames.append(selected)
            print(
                json.dumps({"symbol": symbol, "dated_rows": len(selected)}, ensure_ascii=False),
                flush=True,
            )
        archive("point-in-time-universe.csv", pd.concat(frames, ignore_index=True))
        validated = load_point_in_time_universe(args.output / "point-in-time-universe.csv")
        manifest.update(
            verified=True, rows=len(validated), dates=int(validated["as_of_date"].nunique())
        )
        save_manifest()
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        manifest["error_type"] = type(exc).__name__
        save_manifest()
        raise
    finally:
        if logged_in:
            bs.logout()
        socket.setdefaulttimeout(previous_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
