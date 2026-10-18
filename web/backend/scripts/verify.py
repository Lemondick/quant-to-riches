"""Check maintained web source and report operational admission without test code."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from contextlib import chdir
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-production", action="store_true")
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    workspace = backend.parent.parent
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ.setdefault("QUANT_DATA_DIR", str(workspace / "data"))
    os.environ.setdefault(
        "QUANT_ADVISOR_CONFIG", str(workspace / "deploy/research-secrets/advisor.json")
    )
    commands = [
        ["uv", "run", "python", "-B", "scripts/check_source_size.py"],
        ["uv", "run", "ruff", "check", "--no-cache", "src", "scripts"],
        ["uv", "run", "ruff", "format", "--check", "--no-cache", "src", "scripts"],
        [
            "uv",
            "run",
            "mypy",
            "--cache-dir",
            str(Path(tempfile.gettempdir()) / "quant-web-mypy-cache"),
            "src/quant_assistant/models/entities.py",
            "src/quant_assistant/models/__init__.py",
            "src/quant_assistant/common",
            "src/quant_assistant/analysis/models.py",
        ],
        ["uv", "run", "python", "-B", "scripts/check_no_secrets.py"],
    ]
    with chdir(backend):
        for command in commands:
            if subprocess.run(command, check=False).returncode:
                return 1
        code = subprocess.run(
            ["uv", "run", "python", "-B", "scripts/production_readiness.py"], check=False
        ).returncode
    if code == 2 and not args.strict_production:
        print("Source checks passed; operational admission is still blocked.")
        return 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
