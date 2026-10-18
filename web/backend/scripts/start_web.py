"""Build the H5 client and start/verify its local API without requesting an AI report."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from manage_users import initialize


def main():
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    root = Path(__file__).resolve().parents[1]
    workspace = root.parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=workspace / "data")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在1至65535之间")
    try:
        initialize(args.data_dir.resolve())
    except (ValueError, EOFError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, ValueError) else "管理者初始化已取消。")
        return 2
    if not args.skip_build:
        node = shutil.which("node")
        if not node:
            print(json.dumps({"status": "failed", "reason": "node_runtime_missing"}))
            return 2
        build = subprocess.run(
            [node, str(root.parent / "frontend/scripts/run-web.mjs"), "build"],
            cwd=root,
            check=False,
        )
        if build.returncode:
            return build.returncode
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/run_market_advisor.py"),
            "--prepare-only",
            "--api-url",
            f"http://127.0.0.1:{args.port}",
            "--data-dir",
            str(args.data_dir.resolve()),
        ],
        cwd=workspace,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
