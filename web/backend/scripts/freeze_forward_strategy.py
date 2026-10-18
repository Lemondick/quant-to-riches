"""Explicitly freeze configured strategies before collecting forward paper evidence."""

import argparse
import json
from pathlib import Path

from quant_assistant.common.time import now_from_clock
from quant_assistant.services.forward_sessions import freeze_forward_strategy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--strategy-version", action="append", required=True)
    args = parser.parse_args()
    result = freeze_forward_strategy(args.data_dir, args.strategy_version, now_from_clock())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
