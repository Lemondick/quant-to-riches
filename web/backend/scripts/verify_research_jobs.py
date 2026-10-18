"""Acceptance for the durable research job lifecycle, quota and cancel races.

``ResearchRepository`` enforces the invariants the MVP plan calls out but that no
existing script covered: atomic cache-key deduplication, a pre-call quota
reservation that a crash cannot silently refund, a launch-date quota re-check,
late-attempt isolation, cancel-before-start versus cancel-while-running, and the
restart recovery sweep.

Everything runs against a real temporary DuckDB store. No model endpoint, no
credentials and no network are involved.
"""

import sys
import tempfile
import unittest
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from quant_assistant.analysis.research_config import AgentResearchSettings  # noqa: E402
from quant_assistant.analysis.research_models import ResearchError  # noqa: E402
from quant_assistant.common.time import SHANGHAI_TZ  # noqa: E402
from quant_assistant.models.repositories.agent_research import (  # noqa: E402
    ResearchRepository,
)
from quant_assistant.models.repositories.database import Database  # noqa: E402

DAY = date(2026, 10, 5)


class FakeClock:
    """Advanceable Shanghai-time clock; the repository reads only ``date()``."""

    def __init__(self, moment=datetime(2026, 10, 5, 9, 30, tzinfo=SHANGHAI_TZ)):
        self.moment = moment

    def __call__(self):
        return self.moment

    def set_day(self, day):
        self.moment = datetime(day.year, day.month, day.day, 9, 30, tzinfo=SHANGHAI_TZ)

    def tick(self, seconds=1):
        """Advance within the day.

        ``next_job`` orders by ``created_at, job_id``. Jobs enqueued in the same
        tick therefore fall back to a random UUID tie-break, so any test that
        depends on launch order must advance the clock between enqueues.
        """
        self.moment += timedelta(seconds=seconds)


def next_day(day):
    return day + timedelta(days=1) if day.day < monthrange(day.year, day.month)[1] else None


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quant-research-jobs-")
        self.addCleanup(self.temp.cleanup)
        self.database = Database(Path(self.temp.name) / "quant_assistant.duckdb")
        self.database.initialize()
        self.clock = FakeClock()
        self.repository = ResearchRepository(self.database, self.clock)
        self.settings = AgentResearchSettings(provider="mock", model="mock-v1")

    def enqueue(self, key="cache-a", symbol="600000.SH", snapshot="snap-1", **kwargs):
        return self.repository.enqueue(key, symbol, snapshot, self.settings, **kwargs)

    def attempt_rows(self):
        with self.database.connection() as conn:
            return conn.execute(
                "SELECT job_id,attempt_no,quota_date,execution_status,report_json "
                "FROM agent_research_attempts ORDER BY job_id,attempt_no"
            ).fetchall()

    def status(self, job_id):
        return self.repository.get(job_id)["status"]


class DeduplicationTest(RepositoryCase):
    def test_same_cache_key_returns_the_same_job_without_a_second_attempt(self):
        first, reused = self.enqueue()
        second, reused_again = self.enqueue()
        self.assertFalse(reused)
        self.assertTrue(reused_again)
        self.assertEqual(first, second)
        self.assertEqual(len(self.attempt_rows()), 1)

    def test_distinct_cache_keys_create_distinct_jobs(self):
        a, _ = self.enqueue(key="cache-a")
        b, _ = self.enqueue(key="cache-b")
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.attempt_rows()), 2)


class RetryTest(RepositoryCase):
    def test_retry_bumps_the_attempt_and_records_a_second_quota_row(self):
        job_id, _ = self.enqueue()
        self.repository.next_job(self.clock() + timedelta(seconds=180))
        self.assertTrue(self.repository.finish(job_id, 1, "failed", error="model_error"))
        retried, reused = self.enqueue(retry_id=job_id)
        self.assertEqual(retried, job_id)
        self.assertFalse(reused)
        self.assertEqual(self.repository.get(job_id)["attempt_no"], 2)
        self.assertEqual(len(self.attempt_rows()), 2)

    def test_retrying_an_active_job_is_rejected(self):
        job_id, _ = self.enqueue()
        with self.assertRaises(ResearchError) as caught:
            self.enqueue(retry_id=job_id)
        self.assertEqual(caught.exception.code, "job_active")

    def test_retrying_an_unknown_job_is_a_404(self):
        with self.assertRaises(ResearchError) as caught:
            self.enqueue(retry_id="deadbeef")
        self.assertEqual((caught.exception.code, caught.exception.status), ("job_not_found", 404))


class QuotaTest(RepositoryCase):
    def test_daily_limit_blocks_the_extra_enqueue(self):
        limited = AgentResearchSettings(provider="mock", model="mock-v1", daily_limit=2)
        for index in range(2):
            self.repository.enqueue(f"cache-{index}", "600000.SH", "snap", limited)
        with self.assertRaises(ResearchError) as caught:
            self.repository.enqueue("cache-2", "600000.SH", "snap", limited)
        self.assertEqual((caught.exception.code, caught.exception.status), ("quota_exceeded", 429))

    def test_a_failed_attempt_still_consumes_quota(self):
        limited = AgentResearchSettings(provider="mock", model="mock-v1", daily_limit=1)
        job_id, _ = self.repository.enqueue("cache-a", "600000.SH", "snap", limited)
        self.repository.next_job(self.clock() + timedelta(seconds=180), daily_limit=1)
        self.repository.finish(job_id, 1, "failed", error="model_error")
        with self.assertRaises(ResearchError) as caught:
            self.repository.enqueue("cache-b", "600000.SH", "snap", limited)
        self.assertEqual(caught.exception.code, "quota_exceeded")

    def test_quota_is_per_shanghai_day(self):
        limited = AgentResearchSettings(provider="mock", model="mock-v1", daily_limit=1)
        job_id, _ = self.repository.enqueue("cache-a", "600000.SH", "snap", limited)
        self.repository.next_job(self.clock() + timedelta(seconds=180), daily_limit=1)
        self.repository.finish(job_id, 1, "succeeded", report={"r": 1})
        tomorrow = next_day(DAY)
        self.clock.set_day(tomorrow)
        _, reused = self.repository.enqueue("cache-b", "600000.SH", "snap", limited)
        self.assertFalse(reused)

    def test_a_stale_reservation_is_rechecked_against_the_launch_day(self):
        """A job enqueued yesterday must not launch today when today is exhausted."""
        limited = AgentResearchSettings(provider="mock", model="mock-v1", daily_limit=1)
        stale, _ = self.repository.enqueue("cache-stale", "600000.SH", "snap", limited)
        tomorrow = next_day(DAY)
        self.clock.set_day(tomorrow)
        fresh, _ = self.repository.enqueue("cache-fresh", "600000.SH", "snap", limited)
        deadline = self.clock() + timedelta(seconds=180)
        # The stale job is drained first: it is failed, and the call reports nothing.
        self.assertIsNone(self.repository.next_job(deadline, daily_limit=1))
        self.assertEqual(self.status(stale), "failed")
        self.assertEqual(self.repository.get(stale)["error_code"], "quota_exceeded")
        # Only the next call picks up the job actually enqueued today.
        launched = self.repository.next_job(deadline, daily_limit=1)
        self.assertEqual(launched["job_id"], fresh)
        self.assertEqual(self.status(fresh), "running")

    def test_an_unstarted_reservation_moves_to_the_launch_day(self):
        """Otherwise a job carried overnight would silently free yesterday's quota."""
        limited = AgentResearchSettings(provider="mock", model="mock-v1", daily_limit=5)
        job_id, _ = self.repository.enqueue("cache-a", "600000.SH", "snap", limited)
        tomorrow = next_day(DAY)
        self.clock.set_day(tomorrow)
        self.repository.next_job(self.clock() + timedelta(seconds=180), daily_limit=5)
        with self.database.connection() as conn:
            quota_date = conn.execute(
                "SELECT quota_date FROM agent_research_attempts WHERE job_id=?", [job_id]
            ).fetchone()[0]
        self.assertEqual(quota_date, tomorrow)


class CancelRaceTest(RepositoryCase):
    def test_cancelling_a_queued_job_completes_immediately(self):
        job_id, _ = self.enqueue()
        self.repository.cancel(job_id)
        self.assertEqual(self.status(job_id), "cancelled")
        self.assertEqual(self.repository.get(job_id)["error_code"], "cancelled")
        row = [r for r in self.attempt_rows() if r[0] == job_id][0]
        self.assertEqual(row[3], "cancelled")

    def test_cancelling_a_running_job_only_requests_it(self):
        job_id, _ = self.enqueue()
        self.repository.next_job(self.clock() + timedelta(seconds=180))
        self.repository.cancel(job_id)
        self.assertEqual(self.status(job_id), "cancel_requested")

    def test_a_completion_racing_a_cancel_is_recorded_as_cancelled_with_no_report(self):
        job_id, _ = self.enqueue()
        self.repository.next_job(self.clock() + timedelta(seconds=180))
        self.repository.cancel(job_id)
        # The worker believes it succeeded; the repository must overrule it.
        self.assertTrue(self.repository.finish(job_id, 1, "succeeded", report={"bull": {}}))
        self.assertEqual(self.status(job_id), "cancelled")
        row = [r for r in self.attempt_rows() if r[0] == job_id][0]
        self.assertEqual(row[3], "cancelled")
        self.assertIsNone(row[4], "a cancelled attempt must not keep a report")

    def test_cancelling_an_unknown_job_is_a_404(self):
        with self.assertRaises(ResearchError) as caught:
            self.repository.cancel("deadbeef")
        self.assertEqual(caught.exception.status, 404)


class LateAttemptTest(RepositoryCase):
    def test_a_late_result_from_an_old_attempt_cannot_overwrite_the_new_one(self):
        job_id, _ = self.enqueue()
        self.repository.next_job(self.clock() + timedelta(seconds=180))
        self.repository.finish(job_id, 1, "failed", error="model_error")
        self.enqueue(retry_id=job_id)
        # attempt 1's result arrives after attempt 2 was reserved.
        self.assertFalse(self.repository.finish(job_id, 1, "succeeded", report={"stale": True}))
        self.assertEqual(self.repository.get(job_id)["attempt_no"], 2)
        self.assertEqual(self.status(job_id), "queued")

    def test_finish_on_a_terminal_job_is_a_no_op(self):
        job_id, _ = self.enqueue()
        self.repository.next_job(self.clock() + timedelta(seconds=180))
        self.assertTrue(self.repository.finish(job_id, 1, "succeeded", report={"r": 1}))
        self.assertFalse(self.repository.finish(job_id, 1, "failed", error="model_error"))
        self.assertEqual(self.status(job_id), "succeeded")


class RecoveryTest(RepositoryCase):
    def test_recover_marks_every_in_flight_state_interrupted(self):
        """queued, running and cancel_requested must all become interrupted.

        ``cancel_requested`` is the subtle one: the worker was told to stop but
        the API died before it confirmed, so the job must not stay pending.
        """
        queued, _ = self.enqueue(key="cache-q")
        self.clock.tick()
        running, _ = self.enqueue(key="cache-r")
        self.clock.tick()
        cancelling, _ = self.enqueue(key="cache-c")
        deadline = self.clock() + timedelta(seconds=180)
        self.repository.next_job(deadline)  # cache-q -> running
        self.repository.next_job(deadline)  # cache-r -> running
        self.repository.cancel(cancelling)  # queued -> cancelled (terminal)
        self.repository.cancel(running)  # running -> cancel_requested
        self.assertEqual(self.status(running), "cancel_requested")
        self.repository.recover()
        self.assertEqual(self.status(queued), "interrupted")
        self.assertEqual(self.status(running), "interrupted")
        # A job cancelled while it was still queued is already terminal.
        self.assertEqual(self.status(cancelling), "cancelled")
        attempt_status = {row[0]: row[3] for row in self.attempt_rows()}
        self.assertEqual(attempt_status[queued], "interrupted")
        self.assertEqual(attempt_status[running], "interrupted")
        self.assertEqual(attempt_status[cancelling], "cancelled")

    def test_recover_does_not_touch_finished_jobs(self):
        job_id, _ = self.enqueue()
        self.repository.next_job(self.clock() + timedelta(seconds=180))
        self.repository.finish(job_id, 1, "succeeded", report={"r": 1})
        self.repository.recover()
        self.assertEqual(self.status(job_id), "succeeded")


class DecodedTest(RepositoryCase):
    def test_decoded_round_trips_report_and_usage_and_drops_the_raw_columns(self):
        job_id, _ = self.enqueue()
        self.repository.next_job(self.clock() + timedelta(seconds=180))
        report = {"bull": {"points": [{"text": "中文观点", "fact_ids": ["rank"]}]}}
        usage = [{"total_tokens": 12}]
        self.repository.finish(job_id, 1, "succeeded", report=report, usage=usage)
        decoded = self.repository.decoded(job_id)
        self.assertEqual(decoded["report"], report)
        self.assertEqual(decoded["usage"], usage)
        self.assertNotIn("report_json", decoded)
        self.assertNotIn("usage_json", decoded)

    def test_decoded_yields_none_for_a_job_that_never_finished(self):
        job_id, _ = self.enqueue()
        decoded = self.repository.decoded(job_id)
        self.assertIsNone(decoded["report"])
        self.assertIsNone(decoded["usage"])


if __name__ == "__main__":
    unittest.main()
