"""Archive cross-checked public latest price limits; never relabel them as past dates."""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from quant_assistant.common.time import now_from_clock
from quant_assistant.data.point_in_time import POINT_IN_TIME_COLUMNS
from quant_assistant.data.validation import validate_dataset
from quant_assistant.models.entities import DataStatus
from quant_assistant.providers.baostock import compare_sina_history, normalize_history, sdk_symbol
from quant_assistant.providers.free_limits import (
    normalize_eastmoney_limits,
    normalize_eastmoney_snapshot,
    normalize_tencent_limits,
    quote_symbol,
    verify_quote_limits,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--history-dir", type=Path, help="optional archived BaoStock/Sina pilot bars"
    )
    args = parser.parse_args()
    symbols = tuple(dict.fromkeys(args.symbol))
    codes = [quote_symbol(symbol) for symbol in symbols]
    if len(symbols) > 30:
        parser.error("this research collector supports at most 30 selected stocks")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a new or empty directory")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "fetched_at": now_from_clock().isoformat(),
        "expected_date": args.date.isoformat(),
        "source_type": "latest_quote_snapshot",
        "supports_historical_query": False,
        "price_unit": "CNY",
        "verified": False,
        "files": [],
    }

    def save_manifest():
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def archive(name, payload):
        (args.output / name).write_bytes(payload)
        manifest["files"].append({"name": name, "sha256": hashlib.sha256(payload).hexdigest()})
        save_manifest()

    save_manifest()
    try:
        response = requests.get(f"https://qt.gtimg.cn/q={','.join(codes)}", timeout=(5, 12))
        response.raise_for_status()
        archive("tencent-quotes.raw", response.content)
        tencent = normalize_tencent_limits(response.content.decode("gbk"), symbols, args.date)
        eastmoney_rows = []
        for symbol in symbols:
            try:
                response = requests.get(
                    "https://push2.eastmoney.com/api/qt/stock/get",
                    params={
                        "secid": f"{1 if symbol.endswith('.SH') else 0}.{symbol[:6]}",
                        "fields": "f43,f44,f45,f46,f47,f48,f51,f52,f57,f58,f59,f60,f86",
                        "fltt": "2",
                        "invt": "2",
                    },
                    timeout=(5, 12),
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                manifest.setdefault("source_failures", []).append(
                    {
                        "symbol": symbol,
                        "source": "eastmoney-quote",
                        "error_type": type(exc).__name__,
                    }
                )
                save_manifest()
                response = requests.get(
                    "https://hsmarketwg.eastmoney.com/api/SHSZQuoteSnapshot",
                    params={"id": symbol[:6], "callback": "quoteSnapshot"},
                    timeout=(5, 12),
                )
                response.raise_for_status()
                archive(f"eastmoney-snapshot-{symbol}.jsonp", response.content)
                text = response.text
                payload = json.loads(text[text.find("{") : text.rfind("}") + 1])
                observed = normalize_eastmoney_snapshot(payload, symbol, args.date)
            else:
                archive(f"eastmoney-quote-{symbol}.json", response.content)
                observed = normalize_eastmoney_limits(response.json(), symbol, args.date)
            eastmoney_rows.append(observed)
        verified = verify_quote_limits(tencent, pd.DataFrame(eastmoney_rows))
        if args.history_dir:
            bars = []
            for symbol in symbols:
                code = sdk_symbol(symbol)
                raw = pd.read_csv(args.history_dir / f"baostock-full-{code}.csv", dtype=str)
                sina = pd.read_csv(args.history_dir / f"sina-full-{code.replace('.', '')}.csv")
                crosscheck = compare_sina_history(raw, sina)
                if not crosscheck["verified"]:
                    raise ValueError(f"{symbol}: archived prices and units are not cross-verified")
                manifest.setdefault("history_evidence", []).append(
                    {
                        "symbol": symbol,
                        "crosschecked_dates": crosscheck["matched_dates"],
                        "baostock_csv_sha256": hashlib.sha256(
                            (args.history_dir / f"baostock-full-{code}.csv").read_bytes()
                        ).hexdigest(),
                        "sina_csv_sha256": hashlib.sha256(
                            (
                                args.history_dir / f"sina-full-{code.replace('.', '')}.csv"
                            ).read_bytes()
                        ).hexdigest(),
                    }
                )
                normalized = normalize_history(raw, symbol)
                selected = normalized.loc[normalized["trade_date"].eq(args.date)].copy()
                if len(selected) != 1:
                    raise ValueError(f"{symbol}: no unique archived bar for {args.date}")
                bars.append(selected.drop(columns=["upper_limit", "lower_limit"]))
            combined = (
                pd.concat(bars, ignore_index=True)
                .merge(verified, on=["symbol", "trade_date"], validate="one_to_one")
                .loc[:, list(POINT_IN_TIME_COLUMNS)]
            )
            validation = validate_dataset("point_in_time_prices", combined, args.date)
            if validation.status is not DataStatus.VALID:
                raise ValueError(f"dated bar/limit validation failed: {validation.issues}")
            archive("point-in-time-prices.csv", combined.to_csv(index=False).encode("utf-8"))
        archive("verified-limits.csv", verified.to_csv(index=False).encode("utf-8"))
        manifest.update(verified=True, verified_rows=len(verified))
        save_manifest()
        print(
            json.dumps(
                {
                    "verified": True,
                    "trade_date": str(args.date),
                    "rows": len(verified),
                    "historical_query": False,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        manifest["error_type"] = type(exc).__name__
        save_manifest()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
