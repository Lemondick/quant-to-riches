"""Archive public dividends and adjustment factors; do not invent ledger events."""

import argparse
import hashlib
import json
import socket
from datetime import date
from pathlib import Path

from quant_assistant.common.time import now_from_clock
from quant_assistant.providers.baostock import read_result, sdk_symbol


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    symbols = tuple(dict.fromkeys(args.symbol))
    if args.start >= args.end or len(symbols) > 30:
        parser.error("require an increasing date range and at most 30 stocks")
    args.output.mkdir(parents=True, exist_ok=False)
    import baostock as bs

    manifest = {
        "started_at": now_from_clock().isoformat(),
        "source": "BaoStock",
        "research_only": True,
        "applied_to_backtest": False,
        "start": str(args.start),
        "end": str(args.end),
        "status": "running",
        "files": [],
    }

    def save():
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def archive(name, frame, query):
        path = args.output / name
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        manifest["files"].append(
            {
                "name": name,
                "rows": len(frame),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "query": query,
            }
        )
        save()

    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(15)
    logged_in = False
    try:
        if bs.login().error_code != "0":
            raise RuntimeError("BaoStock login failed")
        logged_in = True
        for symbol in symbols:
            code = sdk_symbol(symbol)
            # Previous report year can pay dividends during the requested range.
            for year in range(args.start.year - 1, args.end.year + 1):
                frame = read_result(bs.query_dividend_data(code, str(year), "report"))
                archive(
                    f"dividends-{symbol}-{year}.csv",
                    frame,
                    {
                        "method": "query_dividend_data",
                        "code": code,
                        "year": year,
                        "yearType": "report",
                    },
                )
            frame = read_result(bs.query_adjust_factor(code, str(args.start), str(args.end)))
            archive(
                f"adjustment-factors-{symbol}.csv",
                frame,
                {
                    "method": "query_adjust_factor",
                    "code": code,
                    "start_date": str(args.start),
                    "end_date": str(args.end),
                },
            )
            print(json.dumps({"symbol": symbol, "status": "archived"}), flush=True)
        manifest["status"] = "completed"
        return 0
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        manifest["finished_at"] = now_from_clock().isoformat()
        save()
        if logged_in:
            bs.logout()
        socket.setdefaulttimeout(previous_timeout)


if __name__ == "__main__":
    raise SystemExit(main())
