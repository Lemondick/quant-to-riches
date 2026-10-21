"""F3: persistent public-source consecutive-failure counting."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from public_source_failures import (
    PublicSourceFailureTracker,
    alert_rounds_from_env,
)

from quant_assistant.providers.public_http import PublicHttp


class FailureTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quant-failures-")
        self.state_path = Path(self.temporary.name) / ".public-source-failures.json"
        self.tracker = PublicSourceFailureTracker(self.state_path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_trips_at_threshold_only_once(self):
        self.assertFalse(self.tracker.record("example.com", ok=False, error="http_500"))
        self.assertFalse(self.tracker.record("example.com", ok=False, error="http_500"))
        self.assertTrue(self.tracker.record("example.com", ok=False, error="http_500"))
        # Already alerting: no repeated trip signal.
        self.assertFalse(self.tracker.record("example.com", ok=False, error="http_500"))
        self.assertEqual(
            self.tracker.alerting_sources()["example.com"]["consecutive_failures"], 4
        )

    def test_success_resets(self):
        self.tracker.record("example.com", ok=False, error="x")
        self.tracker.record("example.com", ok=False, error="x")
        self.assertFalse(self.tracker.record("example.com", ok=True))
        self.assertEqual(self.tracker.snapshot()["example.com"]["consecutive_failures"], 0)
        self.assertEqual(self.tracker.alerting_sources(), {})

    def test_success_without_failures_is_noop(self):
        self.assertFalse(self.tracker.record("example.com", ok=True))
        self.assertFalse(self.state_path.exists())

    def test_counts_are_per_source(self):
        for _ in range(3):
            self.tracker.record("a.example", ok=False, error="x")
        self.tracker.record("b.example", ok=False, error="x")
        self.assertEqual(set(self.tracker.alerting_sources()), {"a.example"})

    def test_persistence_round_trip(self):
        self.tracker.record("example.com", ok=False, error="http_503")
        self.tracker.record("example.com", ok=False, error="http_503")
        reread = PublicSourceFailureTracker(self.state_path)
        entry = reread.snapshot()["example.com"]
        self.assertEqual(entry["consecutive_failures"], 2)
        self.assertEqual(entry["last_error"], "http_503")
        self.assertFalse(entry["alerting"])
        self.assertTrue(reread.record("example.com", ok=False, error="http_503"))
        # Raw file shape for stream A's consumer.
        raw = json.loads(self.state_path.read_text("utf-8"))
        self.assertEqual(raw["version"], 1)
        self.assertTrue(raw["sources"]["example.com"]["alerting"])

    def test_custom_threshold(self):
        tracker = PublicSourceFailureTracker(self.state_path, alert_rounds=1)
        self.assertTrue(tracker.record("example.com", ok=False, error="x"))

    def test_alert_rounds_from_env(self):
        self.assertEqual(alert_rounds_from_env({}), 3)
        self.assertEqual(
            alert_rounds_from_env({"QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS": "5"}), 5
        )
        self.assertEqual(
            alert_rounds_from_env({"QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS": "nope"}), 3
        )
        self.assertEqual(
            alert_rounds_from_env({"QUANT_PUBLIC_SOURCE_FAIL_ALERT_ROUNDS": "0"}), 1
        )


class PublicHttpTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quant-failures-")
        self.tracker = PublicSourceFailureTracker(
            Path(self.temporary.name) / ".public-source-failures.json"
        )
        PublicHttp.set_default_failure_tracker(self.tracker)
        self.addCleanup(PublicHttp.set_default_failure_tracker, None)

    def tearDown(self):
        self.temporary.cleanup()

    def _http(self, fail_times):
        calls = {"n": 0}

        class FakeResponse:
            status_code = 200
            content = b'{"ok": true}'
            headers = {"Content-Type": "application/json"}

            def iter_content(self, size):
                yield self.content

            def close(self):
                pass

        def fake_get(url, **kwargs):
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise ConnectionError("down")
            return FakeResponse()

        return PublicHttp(fake_get, sleep=lambda *_: None), calls

    def test_terminal_failure_recorded_once_per_call(self):
        # fail_times=2: first attempt + 1 retry, both fail -> single record.
        http, _ = self._http(fail_times=2)
        with self.assertRaises(Exception):
            http.get("https://example.com/data")
        entry = self.tracker.snapshot()["example.com"]
        self.assertEqual(entry["consecutive_failures"], 1)
        self.assertIn("ConnectionError", entry["last_error"])

    def test_success_resets_counter(self):
        http, _ = self._http(fail_times=2)
        with self.assertRaises(Exception):
            http.get("https://example.com/data")
        http2, _ = self._http(fail_times=0)
        http2.get("https://example.com/data")
        self.assertEqual(
            self.tracker.snapshot()["example.com"]["consecutive_failures"], 0
        )

    def test_no_tracker_no_behavior_change(self):
        PublicHttp.set_default_failure_tracker(None)
        http, _ = self._http(fail_times=2)
        with self.assertRaises(Exception):
            http.get("https://example.com/data")


if __name__ == "__main__":
    unittest.main()
