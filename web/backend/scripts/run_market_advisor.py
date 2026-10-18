"""Trigger the shared API, optionally starting its local server without a window."""

import argparse
import getpass
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from quant_assistant.services.advisor_scheduler import trading_day


def main():
    root = Path(__file__).resolve().parents[1]
    workspace = root.parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("morning", "afternoon"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--data-dir", type=Path, default=workspace / "data")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.prepare_only and args.kind is None:
        parser.error("报告生成需要 --kind；只启动网页可使用 --prepare-only")
    if args.scheduled and args.force:
        print(json.dumps({"status": "blocked", "reason": "scheduled_force_forbidden"}))
        return 2
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if args.scheduled and trading_day(now.date()) is not True:
        print(json.dumps({"status": "skipped", "reason": "non_trading_day_or_calendar_unknown"}))
        return 0
    hour, minute = (9, 30) if args.kind == "morning" else (14, 30)
    if args.scheduled and not (now.hour == hour and minute <= now.minute < minute + 15):
        print(json.dumps({"status": "skipped", "reason": "outside_scheduled_window"}))
        return 0
    try:
        url = urlsplit(args.api_url)
        valid_origin = (
            url.scheme == "http"
            and url.hostname == "127.0.0.1"
            and url.username is None
            and url.password is None
            and url.path in ("", "/")
            and not url.query
            and not url.fragment
            and (url.port is None or 1 <= url.port <= 65535)
        )
    except ValueError:
        valid_origin = False
    if not valid_origin:
        print(json.dumps({"status": "blocked", "reason": "local_api_required"}))
        return 2
    api = args.api_url.rstrip("/")
    expected = hashlib.sha256(
        str((args.data_dir / "quant_assistant.duckdb").resolve()).encode()
    ).hexdigest()[:16]
    client = requests.Session()
    client.trust_env = False
    try:
        try:
            response = client.get(api + "/api/v1/auth/status", timeout=5, allow_redirects=False)
        except requests.ConnectionError:
            logs = args.data_dir.resolve() / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / "advisor-web.log").open("ab") as output:
                subprocess.Popen(
                    [
                        sys.executable,
                        str(root / "scripts/serve_advisor_web.py"),
                        "--port",
                        str(url.port or 80),
                        "--data-dir",
                        str(args.data_dir.resolve()),
                    ],
                    cwd=workspace,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            for _ in range(30):
                time.sleep(1)
                try:
                    response = client.get(
                        api + "/api/v1/auth/status", timeout=3, allow_redirects=False
                    )
                    break
                except requests.ConnectionError:
                    continue
            else:
                raise RuntimeError
        response.raise_for_status()
        if response.status_code >= 300:
            raise RuntimeError
        payload = response.json()
        if payload["instance_id"] != expected or payload.get("authentication") != "session":
            print(json.dumps({"status": "blocked", "reason": "different_data_directory"}))
            return 2
        if args.prepare_only:
            print(json.dumps({"status": "ready", "url": api, "model_calls": 0}))
            return 0
        username = input("报告所属用户账号: ")
        response = client.post(
            api + "/api/v1/auth/login",
            json={"username": username, "password": getpass.getpass("密码: ")},
            headers={"Origin": api},
            timeout=15,
            allow_redirects=False,
        )
        response.raise_for_status()
        client.headers.update({"Origin": api, "X-CSRF-Token": response.json()["csrf_token"]})
        if args.scheduled:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            if not (now.hour == hour and minute <= now.minute < minute + 15):
                print(json.dumps({"status": "skipped", "reason": "outside_scheduled_window"}))
                return 0
        response = client.post(
            api + "/api/v1/advisor/reports",
            json={"kind": args.kind, "force": args.force},
            timeout=15,
            allow_redirects=False,
        )
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + 360
        while job["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(2)
            response = client.get(
                api + "/api/v1/advisor/reports/" + job["report_id"],
                timeout=10,
                allow_redirects=False,
            )
            response.raise_for_status()
            job = response.json()
        print(
            json.dumps(
                {
                    "report_id": job["report_id"],
                    "kind": args.kind,
                    "status": job["status"],
                    "error_code": job.get("error_code"),
                    "wechat_sent": False,
                },
                ensure_ascii=False,
            )
        )
        return 1 if job["status"] in ("failed", "queued", "running") else 0
    except (requests.RequestException, ValueError, KeyError, RuntimeError, OSError, EOFError):
        print(json.dumps({"status": "blocked", "reason": "local_advisor_unavailable"}))
        return 2
    finally:
        if client.headers.get("X-CSRF-Token"):
            with suppress(requests.RequestException):
                client.post(api + "/api/v1/auth/logout", timeout=5, allow_redirects=False)
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
