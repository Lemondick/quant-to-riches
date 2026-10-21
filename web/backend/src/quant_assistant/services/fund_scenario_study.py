"""Read a saved research comparison; never qualifies production evidence."""

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock, Thread
from uuid import uuid4

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.services.fund_study_curves import read_curves
from quant_assistant.services.fund_study_refresh import run_refresh


class FundScenarioStudy:
    def __init__(self, directory: Path, *, now=None, runner=run_refresh):
        self.directory = directory.resolve()
        self.now = now or (lambda: datetime.now(SHANGHAI_TZ))
        self.runner = runner
        self.lock = RLock()
        self.update = {"status": "idle", "message": "尚未发起研究更新。"}
        status = self.directory / "update-status.json"
        if status.is_file():
            try:
                self.update = json.loads(status.read_bytes())
                if self.update["status"] == "running":
                    self.update = {
                        "status": "failed",
                        "message": "服务重启，更新已中断；旧版保留，请手动重试。",
                    }
            except (OSError, ValueError, KeyError):
                self.update = {"status": "failed", "message": "更新记录不可读取，旧版存档未改动。"}

    def update_status(self):
        with self.lock:
            return {**self.update, "model_calls": 0}

    def _bundle(self):
        root = self.directory
        pointer = root / "current.json"
        expected = None
        version = None
        if pointer.is_file():
            current = json.loads(pointer.read_bytes())
            version, expected = current["version"], current["manifest_sha256"]
            if not re.fullmatch(r"[a-f0-9]{32}", version):
                raise ValueError("invalid study version")
            root = root / "versions" / version / "study"
        if not root.resolve().is_relative_to(self.directory):
            raise ValueError("study path outside workspace")
        if not (root / "manifest.json").is_file():
            raise ValueError("missing study manifest")
        manifest_path = root / "manifest.json"
        if not manifest_path.resolve().is_relative_to(root.resolve()):
            raise ValueError("manifest outside study")
        raw_manifest = manifest_path.read_bytes()
        digest = hashlib.sha256(raw_manifest).hexdigest()
        if expected is not None and digest != expected:
            raise ValueError("published manifest mismatch")
        manifest = json.loads(raw_manifest)
        entries = manifest["files"]
        if {entry["name"] for entry in entries} != {
            "report.json",
            "curves.csv",
            "trades.csv",
        } or len(entries) != 3:
            raise ValueError("invalid study manifest")
        files = {}
        for entry in entries:
            file = (root / entry["name"]).resolve()
            if not file.is_relative_to(root.resolve()) or file.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("invalid study file")
            payload = file.read_bytes()
            if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError("study hash mismatch")
            files[entry["name"]] = payload
        report = json.loads(files["report.json"])
        if (
            report["version"] != "fund-nav-cost-study-v1"
            or report["research_only"] is not True
            or report["qualifies_strategy"] is not False
            or report["ready_for_trading"] is not False
        ):
            raise ValueError("unsupported study scope")
        return report, files, version or f"initial-{digest[:16]}"

    def read(self):
        empty = {"available": False, "research_only": True, "ready_for_trading": False, "funds": []}
        if (
            not (self.directory / "manifest.json").is_file()
            and not (self.directory / "current.json").is_file()
        ):
            return {**empty, "message": "尚无基金成本情景对比。"}
        try:
            report, _files, version = self._bundle()
            freshness = "unknown"
            try:
                finished = datetime.fromisoformat(report["evidence_finished_at"])
                if finished.tzinfo is not None and finished <= self.now():
                    freshness = "stale" if self.now() - finished > timedelta(hours=24) else "recent"
            except (KeyError, TypeError, ValueError):
                pass
            return {
                "available": True,
                **report,
                "study_version": version,
                "source_freshness": freshness,
                "update": self.update_status(),
            }
        except (KeyError, OSError, TypeError, ValueError):
            return {**empty, "message": "对比证据缺失、损坏或校验失败，结果不可用。"}

    def curves(self, symbol, period, purchase_rate, version):
        empty = {"available": False, "research_only": True, "series": []}
        try:
            report, files, current = self._bundle()
            if version != current:
                return {**empty, "message": "研究版本已更新，请重新读取表格后查看曲线。"}
            fund = next(fund for fund in report["funds"] if fund["symbol"] == symbol)
            series = read_curves(files["curves.csv"], fund, period, purchase_rate)
            return {
                "available": True,
                "research_only": True,
                "study_version": current,
                "series": series,
            }
        except (ArithmeticError, KeyError, OSError, StopIteration, TypeError, ValueError):
            return {**empty, "message": "曲线证据缺失、损坏或与表格不一致，暂不绘图。"}

    def _record(self, values):
        with self.lock:
            self.update = values
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / "update-status.tmp"
            temporary.write_text(json.dumps(values, ensure_ascii=False), "utf-8")
            os.replace(temporary, self.directory / "update-status.json")

    def start_update(self):
        with self.lock:
            if self.update["status"] == "running":
                return self.update_status()
            previous = self.read()
            if not previous["available"] or not previous["funds"]:
                return {
                    "status": "failed",
                    "message": "缺少有效研究范围，未发起采集。",
                    "model_calls": 0,
                }
            job = uuid4().hex
            started = self.now().isoformat()
            self._record(
                {
                    "status": "running",
                    "stage": "queued",
                    "job_id": job,
                    "started_at": started,
                    "message": "更新已开始；完成校验前继续展示旧版。",
                }
            )
            Thread(
                target=self._run,
                args=(job, previous, started),
                daemon=True,
                name="fund-study-update",
            ).start()
            return self.update_status()

    def _run(self, job, previous, started):
        def progress(stage, message):
            self._record(
                {
                    "status": "running",
                    "job_id": job,
                    "started_at": started,
                    "stage": stage,
                    "message": message,
                }
            )

        root = self.directory / "versions" / job
        published = False
        try:
            root.mkdir(parents=True, exist_ok=False)
            self.runner(root, previous, self.now(), progress)
            candidate = FundScenarioStudy(root / "study", now=self.now)
            report = candidate.read()
            if not report["available"]:
                raise ValueError("invalid updated report")
            if {fund["symbol"] for fund in report["funds"]} != {
                fund["symbol"] for fund in previous["funds"]
            }:
                raise ValueError("updated study scope changed")
            # Rules are study-wide, but compare per fund by symbol (see worker E20).
            previous_rules = {fund["symbol"]: fund["rules"] for fund in previous["funds"]}
            if any(fund["rules"] != previous_rules[fund["symbol"]] for fund in report["funds"]):
                raise ValueError("updated study rules changed")
            # Validate all 27 comparisons per fund before atomically publishing.
            for fund in report["funds"]:
                for period in ("full", "early", "late"):
                    for fee in ("0", "0.0012", "0.012"):
                        if not candidate.curves(
                            fund["symbol"], period, fee, report["study_version"]
                        )["available"]:
                            raise ValueError("invalid updated curves")
            pointer = {
                "version": job,
                "manifest_sha256": hashlib.sha256(
                    (root / "study/manifest.json").read_bytes()
                ).hexdigest(),
            }
            temporary = self.directory / f"current-{job}.tmp"
            temporary.write_text(json.dumps(pointer), "utf-8")
            os.replace(temporary, self.directory / "current.json")
            published = True
            self._record(
                {
                    "status": "completed",
                    "job_id": job,
                    "started_at": started,
                    "finished_at": self.now().isoformat(),
                    "message": "研究已更新并另存新版本；请按数据截止日期查看。",
                }
            )
        except Exception:
            message = (
                "新版已发布，但更新记录保存失败；请重新读取研究。"
                if published
                else "资料采集或计算校验失败，旧版保留；请检查公开来源或分红、折算记录后重试。"
            )
            self._record(
                {
                    "status": "failed",
                    "job_id": job,
                    "started_at": started,
                    "finished_at": self.now().isoformat(),
                    "message": message,
                }
            )
