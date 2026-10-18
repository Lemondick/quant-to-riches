"""Check CI result accounting using actual unittest and TRX inputs."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("test_report.py")


class TestReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = self.root / "result.json"

    def run_report(self, *args):
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--report",
                str(self.report),
                "--title",
                "Synthetic",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return process, json.loads(self.report.read_text(encoding="utf-8"))

    def test_failed_and_skipped_tests_are_not_counted_as_passed(self):
        (self.root / "test_synthetic.py").write_text(
            "import unittest\n"
            "class Example(unittest.TestCase):\n"
            "    def test_pass(self): pass\n"
            "    def test_fail(self): self.fail('synthetic')\n"
            "    @unittest.skip('synthetic missing dependency')\n"
            "    def test_skip(self): pass\n",
            encoding="utf-8",
        )
        process, report = self.run_report("--start", str(self.root))
        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            (report["tests"], report["passed"], report["failed"], report["skipped"]), (3, 1, 1, 1)
        )
        self.assertEqual(report["skip_reasons"][0]["reason"], "synthetic missing dependency")

    def test_empty_discovery_does_not_pass(self):
        process, report = self.run_report("--start", str(self.root))
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["tests"], 0)
        self.assertFalse(report["successful"])

    def test_all_skipped_discovery_does_not_pass(self):
        (self.root / "test_synthetic.py").write_text(
            "import unittest\n@unittest.skip('synthetic unavailable')\n"
            "class Example(unittest.TestCase):\n    def test_skip(self): pass\n",
            encoding="utf-8",
        )
        process, report = self.run_report("--start", str(self.root))
        self.assertEqual(process.returncode, 1)
        self.assertEqual((report["passed"], report["skipped"]), (0, 1))

    def test_postgres_gated_trx_is_explicitly_failed_when_database_is_required(self):
        trx = self.root / "synthetic.trx"
        trx.write_text(
            '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
            '<ResultSummary outcome="Completed">'
            '<Counters total="2" passed="1" failed="0" notExecuted="1"/>'
            '</ResultSummary><Results><UnitTestResult testName="SyntheticPg" outcome="NotExecuted">'
            "<Output><ErrorInfo><Message>QUANT_TEST_PG_CONNECTION is required."
            "</Message></ErrorInfo>"
            "</Output></UnitTestResult></Results></TestRun>",
            encoding="utf-8",
        )
        process, report = self.run_report("--trx", str(trx))
        self.assertEqual(process.returncode, 0)
        self.assertEqual(report["executed"], 1)
        process, report = self.run_report("--trx", str(trx), "--require-database")
        self.assertEqual(process.returncode, 1)
        self.assertEqual((report["tests"], report["passed"], report["skipped"]), (2, 1, 1))
        self.assertIn("QUANT_TEST_PG_CONNECTION", report["skip_reasons"][0]["reason"])
        trx.write_text(
            trx.read_text(encoding="utf-8").replace('outcome="Completed"', 'outcome="Failed"'),
            encoding="utf-8",
        )
        process, report = self.run_report("--trx", str(trx))
        self.assertEqual(process.returncode, 1)
        self.assertFalse(report["successful"])


if __name__ == "__main__":
    unittest.main()
