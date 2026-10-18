"""Replay an explicitly reviewed evidence bundle offline; never opens the account DB."""

import argparse
import hashlib
import json
from pathlib import Path

from quant_assistant.reporting.fund_execution_study import replay
from quant_assistant.services.fund_research_store import encode, fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.bundle.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("bundle too large")
    source = args.bundle.read_bytes()
    if len(source) > 10 * 1024 * 1024:
        raise ValueError("bundle too large")
    report = replay(json.loads(source))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "bundle.json").write_bytes(source)
    raw = encode(report)
    (args.output / "report.json").write_text(raw, encoding="utf-8")
    (args.output / "manifest.json").write_text(
        encode(
            {
                "report_sha256": fingerprint(raw),
                "bundle_sha256": hashlib.sha256(source).hexdigest(),
                "research_only": True,
            }
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "blockers": report["blockers"],
                "fills": len(report["fills"]),
                "ready_for_trading": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
