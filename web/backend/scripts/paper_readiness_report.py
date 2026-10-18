"""Report real paper-session progress and unresolved readiness blockers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant_assistant.config import Settings
from quant_assistant.models.repositories.database import Database
from quant_assistant.services.forward_sessions import paper_session_summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-sessions", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(Database(Settings.from_env().database_path), args.minimum_sessions)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ready_for_reference"] else 2


def build_report(database: Database, minimum_sessions: int = 20) -> dict[str, object]:
    return paper_session_summary(database, minimum_sessions)


if __name__ == "__main__":
    raise SystemExit(main())
