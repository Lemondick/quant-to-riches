"""Native account reads must not eagerly import the full public feed package."""

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quant_assistant.providers._lazy_akshare import _LazyModule


class LazyFeedRuntimeTest(unittest.TestCase):
    def test_fresh_native_worker_does_not_load_akshare(self):
        worker = Path(__file__).resolve().parents[2] / "worker"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys;sys.path.insert(0,sys.argv[1]);import run_worker;"
                "assert 'akshare' not in sys.modules",
                str(worker),
            ],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_feed_attribute_uses_the_real_import_boundary(self):
        endpoint = object()
        with patch(
            "quant_assistant.providers._lazy_akshare.import_module",
            return_value=SimpleNamespace(endpoint=endpoint),
        ) as load:
            module = _LazyModule("akshare")
            load.assert_not_called()
            self.assertIs(module.endpoint, endpoint)
            load.assert_called_once_with("akshare")


if __name__ == "__main__":
    unittest.main()
