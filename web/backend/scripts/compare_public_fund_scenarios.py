"""Create a reproducible, read-only public NAV cost study. No database or network."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.reporting import fund_scenarios
from quant_assistant.reporting.fund_scenarios import compare_scenarios
from scripts.replay_public_fund_nav import replay_archive


def study(evidence_dir, *, symbols, start, end):
    source, references = replay_archive(evidence_dir, symbols=symbols, start=start, end=end)
    if len(references) != len(symbols):
        raise ValueError("all funds require valid, action-reviewed archived NAV references")
    report = {
        "version": "fund-nav-cost-study-v1",
        "requested_start": start,
        "requested_end": end,
        "generated_at": datetime.now(SHANGHAI_TZ).isoformat(),
        "research_only": True,
        "qualifies_strategy": False,
        "ready_for_trading": False,
        "source_manifest_sha256": source["manifest_sha256"],
        "evidence_finished_at": source["evidence_finished_at"],
        "implementation_sha256": hashlib.sha256(
            Path(fund_scenarios.__file__).read_bytes()
        ).hexdigest(),
        "limitations": [
            "费率为统一假设情景，不是支付宝已核对费率；不使用今日费率冒充历史费用。",
            "信号延迟2条净值记录，申购确认延迟1条、赎回到账延迟3条；均为情景假设。",
            "净值记录用作交易日期代理，历史开放/限购状态、净值发布时间尚未核验。",
            "未扣第二遍管理/托管/销售服务费；已公布净值包含基金运作费用。",
            "全部方案起始预算相同；分批投入从这笔现金支出，不追加外部入金，现金收益按0。",
            "期末资产已扣估计赎回费，包含在途资金，不等于当日可到账现金。",
            "前后段独立重新投入同等预算，后段仍是历史保留样本；未用于参数寻优，不能当作实盘。",
            "仅含当前选择的基金，不代表当时可选全基金池，也不代表未来收益。",
            *source["limitations"],
        ],
        "funds": [],
    }
    curves, trades = [], []
    for symbol, frame in references.items():
        comparison = compare_scenarios(frame)
        for row in comparison["results"]:
            identity = {key: row[key] for key in ("strategy", "purchase_rate", "period")}
            for kind, target in (("curve", curves), ("trades", trades)):
                target.extend({"symbol": symbol, **identity, **item} for item in row.pop(kind))
        detail_path = evidence_dir / f"{symbol}-existing-detail.json"
        name = symbol
        if detail_path.is_file():
            name = json.loads(detail_path.read_text(encoding="utf-8")).get("name", symbol)
        report["funds"].append({"symbol": symbol, "name": name, **comparison})
    return report, pd.DataFrame(curves), pd.DataFrame(trades)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must be a new directory")
    try:
        report, curves, trades = study(
            args.evidence_dir, symbols=tuple(args.symbol), start=args.start, end=args.end
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True, exist_ok=False)
    curves.to_csv(args.output / "curves.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(args.output / "trades.csv", index=False, encoding="utf-8-sig")
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    manifest = {
        "files": [
            {"name": name, "sha256": hashlib.sha256((args.output / name).read_bytes()).hexdigest()}
            for name in ("report.json", "curves.csv", "trades.csv")
        ]
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "research_only": True,
                "fund_count": len(report["funds"]),
                "comparison_count": sum(len(f["results"]) for f in report["funds"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
