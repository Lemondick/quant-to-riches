"""Minimal HTTP health probe used by containers and service managers."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765/healthz")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    request = Request(args.url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status = int(response.status)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError):
        return 1
    if status != 200 or not isinstance(payload, dict) or payload.get("status") != "ok":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
