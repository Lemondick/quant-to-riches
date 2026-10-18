"""Isolated public-source study rebuild; retain every published version."""

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from quant_assistant.reporting.fund_scenarios import ScenarioRules


def run_refresh(root, previous, now, progress):
    symbols = [fund["symbol"] for fund in previous["funds"]]
    if not 1 <= len(symbols) <= 5 or len(set(symbols)) != len(symbols):
        raise ValueError("invalid study scope")
    if any(fund["rules"] != asdict(ScenarioRules()) for fund in previous["funds"]):
        raise ValueError("custom rules require an explicit offline rebuild")
    repository = Path(__file__).resolve().parents[3]
    source, destination = root / "evidence", root / "study"
    progress("fetching", "正在采集公开净值、分红与折算记录。")
    common = [sys.executable, "-X", "utf8", "-m"]
    codes = [item for symbol in symbols for item in ("--symbol", symbol)]
    subprocess.run(
        [*common, "scripts.probe_public_fund_data", *codes, "--nav-only", "--output", str(source)],
        cwd=repository,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        timeout=120,
        check=True,
    )
    progress("calculating", "正在校验资料并计算固定规则，旧版结果仍可查看。")
    subprocess.run(
        [
            *common,
            "scripts.compare_public_fund_scenarios",
            "--evidence-dir",
            str(source),
            *codes,
            "--start",
            previous.get("requested_start", "2024-01-01"),
            "--end",
            now.date().isoformat(),
            "--output",
            str(destination),
        ],
        cwd=repository,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        timeout=120,
        check=True,
    )
    # Names come from the frozen previous study, not unverified remote labels.
    report_path = destination / "report.json"
    report = json.loads(report_path.read_bytes())
    names = {fund["symbol"]: fund["name"] for fund in previous["funds"]}
    for fund in report["funds"]:
        fund["name"] = names[fund["symbol"]]
    report_path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), "utf-8")
    manifest = json.loads((destination / "manifest.json").read_bytes())
    for entry in manifest["files"]:
        if entry["name"] == "report.json":
            entry["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    (destination / "manifest.json").write_text(json.dumps(manifest), "utf-8")
