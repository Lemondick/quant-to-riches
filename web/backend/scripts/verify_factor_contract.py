"""Verify the published worker factor identifiers.

The former Uni-App panel was removed when the supplied Vue UI replaced it.
That UI currently uses demo data, so it has no live screener request contract.
Keep the backend identifier check without reading a deleted frontend file.
"""

import re
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
WORKER = BACKEND / "scripts/calc_factors.py"

# ``FACTORS`` entries look like ("MOM", "20日动量", "momentum", 1).
FACTOR_ENTRY = re.compile(r'\(\s*"([A-Z0-9_]+)"\s*,\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*-?1\s*\)')


def _authoritative_codes() -> set[str]:
    source = WORKER.read_text(encoding="utf-8")
    start = source.index("FACTORS")
    end = source.index("\n)", start)
    codes = {match.group(1) for match in FACTOR_ENTRY.finditer(source[start:end])}
    if not codes:
        raise AssertionError(f"no factor codes parsed from {WORKER.name}; FACTORS layout changed?")
    return codes


class FactorContractAcceptance(unittest.TestCase):
    def test_worker_publishes_momentum_and_volatility(self):
        self.assertEqual(_authoritative_codes(), {"MOM", "VOL"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
