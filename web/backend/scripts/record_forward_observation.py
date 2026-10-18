"""Record a real-time paper observation; never backdate runs or update admission."""

import argparse
import json
from pathlib import Path

import duckdb

from quant_assistant.common.time import SHANGHAI_TZ, now_from_clock
from quant_assistant.services.forward_sessions import evaluate_observation


def observe(data_dir, now, *, run_id=None):
    data_dir = Path(data_dir).resolve()
    # Closed markets and early observations need no database access.
    preliminary = (
        evaluate_observation(None, data_dir, now)
        if (now.astimezone(SHANGHAI_TZ).time().hour < 16 or _closed_market(data_dir, now))
        else None
    )
    if preliminary is not None:
        return preliminary
    with duckdb.connect(str(data_dir / "quant_assistant.duckdb"), read_only=True) as connection:
        connection.execute("SET TimeZone='Asia/Shanghai'")
        return evaluate_observation(connection, data_dir, now, run_id=run_id)


def _closed_market(data_dir, now):
    from quant_assistant.services.forward_sessions import site_root
    from quant_assistant.services.trading_calendar import TradingCalendar

    calendar = TradingCalendar(site_root(data_dir) / "calendars")
    return calendar.trading_day(now.astimezone(SHANGHAI_TZ).date()) is not True


def record(data_dir, now, *, run_id=None):
    manifest = Path(data_dir) / "manifest.json"
    if manifest.is_file() and json.loads(manifest.read_text("utf-8")).get("research_only") is True:
        raise ValueError(
            "archived research workspaces are read-only and cannot earn admission credit"
        )
    report = observe(data_dir, now, run_id=run_id)
    output = Path(data_dir) / "forward-observations" / report["as_of_date"]
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{now.strftime('%H%M%S-%f')}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, default=str, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return report, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run-id", help="observe a specific same-day strategy decision")
    args = parser.parse_args()
    now = now_from_clock()
    report, path = record(args.data_dir.resolve(), now, run_id=args.run_id)
    print(
        json.dumps(
            {
                "status": report["status"],
                "blockers": report["blockers"],
                "eligible_paper_session": report["eligible_paper_session"],
                "output": str(path.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] in ("recorded", "skipped") else 2


if __name__ == "__main__":
    raise SystemExit(main())
