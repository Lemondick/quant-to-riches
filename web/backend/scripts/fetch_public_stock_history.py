"""Archive explicit BaoStock calls and independent Sina history for a research pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
from datetime import date
from pathlib import Path

import akshare as ak

from quant_assistant.common.time import now_from_clock
from quant_assistant.providers.baostock import (
    INDEX_FIELDS,
    STOCK_FIELDS,
    compare_sina_history,
    normalize_history,
    read_result,
    sdk_symbol,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--universe-date", type=date.fromisoformat, action="append", default=[])
    parser.add_argument(
        "--skip-catalogue", action="store_true", help="retry selected bars without full catalogues"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--start must precede --end")
    if any(day < args.start or day > args.end for day in args.universe_date):
        parser.error("--universe-date must be inside the requested history range")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a new or empty archive directory")
    import baostock as bs  # Optional public-data extra; never loaded by the API itself.

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "fetched_at": now_from_clock().isoformat(),
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "research_only": True,
        "symbols": list(dict.fromkeys(args.symbol)),
        "adjustflag": "3",
        "universe_dates": [day.isoformat() for day in dict.fromkeys(args.universe_date)],
        "catalogue_requested": not args.skip_catalogue,
        "status": "running",
        "files": [],
    }

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
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(15)
    logged_in = False
    try:
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {login.error_code}")
        logged_in = True
        if not args.skip_catalogue:
            archive("baostock-basic-all.csv", read_result(bs.query_stock_basic()))
            archive(
                "baostock-universe.csv", read_result(bs.query_all_stock(day=args.end.isoformat()))
            )
        for day in dict.fromkeys(args.universe_date):
            archive(
                f"baostock-universe-{day.isoformat()}.csv",
                read_result(bs.query_all_stock(day=day.isoformat())),
            )
        archive(
            "baostock-calendar.csv",
            read_result(
                bs.query_trade_dates(
                    start_date=args.start.isoformat(), end_date=args.end.isoformat()
                )
            ),
        )
        checks = []
        for symbol in dict.fromkeys([*args.symbol, "000300.SH"]):
            code = sdk_symbol(symbol)
            is_index = symbol == "000300.SH"
            raw = read_result(
                bs.query_history_k_data_plus(
                    code,
                    INDEX_FIELDS if is_index else STOCK_FIELDS,
                    start_date=args.start.isoformat(),
                    end_date=args.end.isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
            )
            normalize_history(raw, symbol, is_index=is_index)
            archive(f"baostock-full-{code}.csv", raw)
            if not is_index:
                sina_code = code.replace(".", "")
                independent = ak.stock_zh_a_daily(
                    symbol=sina_code,
                    start_date=args.start.strftime("%Y%m%d"),
                    end_date=args.end.strftime("%Y%m%d"),
                    adjust="",
                )
                archive(f"sina-full-{sina_code}.csv", independent)
                check = {"symbol": symbol, **compare_sina_history(raw, independent)}
                checks.append(check)
                (args.output / "crosscheck.json").write_text(
                    json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(json.dumps(check, ensure_ascii=False), flush=True)
        manifest["status"] = "completed" if all(item["verified"] for item in checks) else "blocked"
        return 0 if manifest["status"] == "completed" else 2
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        manifest["finished_at"] = now_from_clock().isoformat()
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if logged_in:
            bs.logout()
        socket.setdefaulttimeout(previous_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
