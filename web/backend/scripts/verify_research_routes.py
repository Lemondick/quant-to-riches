"""Acceptance for the research HTTP surface: status codes, payloads and errors.

The route layer is thin but its contract is specific and easy to break silently:
a recreated run must answer 202 while a cache hit answers 200, unknown fields
must be rejected rather than ignored, identifiers are constrained by pattern,
and every :class:`ResearchError` must surface as its declared status with a
stable ``error_code``.

Driven with a stub service, so this file exercises the routing, validation and
error mapping only. No database, model endpoint or credentials are involved.
"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from quant_assistant.analysis.research_models import ResearchError  # noqa: E402
from quant_assistant.controllers.research_routes import build_research_router  # noqa: E402

PREFIX = "/api/v1/research/agent-runs"
SNAPSHOT = "a" * 32
SYMBOL = "600000.SH"
JOB = "b" * 32


class StubService:
    """Records calls and replays configured results onto the route layer."""

    def __init__(self):
        self.calls = []
        self.created = ({"job_id": JOB, "status": "queued"}, False)
        self.retried = ({"job_id": JOB, "status": "queued"}, False)
        self.existing = {"job_id": JOB, "status": "succeeded"}
        self.cancelled = {"job_id": JOB, "status": "cancelled"}
        self.failure = None

    def _maybe_fail(self):
        if self.failure is not None:
            raise self.failure

    def create(self, snapshot_id, symbol, *, retry_id=None):
        self.calls.append(("create", snapshot_id, symbol))
        self._maybe_fail()
        return self.created

    def retry(self, job_id, snapshot_id):
        self.calls.append(("retry", job_id, snapshot_id))
        self._maybe_fail()
        return self.retried

    def get(self, job_id):
        self.calls.append(("get", job_id))
        self._maybe_fail()
        return self.existing

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))
        self._maybe_fail()
        return self.cancelled


class RouteCase(unittest.TestCase):
    def setUp(self):
        self.service = StubService()
        app = FastAPI()

        @app.exception_handler(ResearchError)
        async def handle(request, error):
            return JSONResponse(
                status_code=error.status,
                content={"error_code": error.code, "message": str(error)},
            )

        app.include_router(build_research_router(self.service))
        self.client = TestClient(app, raise_server_exceptions=False)


class CreateRouteTest(RouteCase):
    def test_a_new_run_answers_202(self):
        response = self.client.post(
            PREFIX, json={"preview_snapshot_id": SNAPSHOT, "symbol": SYMBOL}
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["job_id"], JOB)
        self.assertEqual(self.service.calls[-1], ("create", SNAPSHOT, SYMBOL))

    def test_a_reused_run_answers_200(self):
        """A cache hit must not look like fresh work; the plan distinguishes them."""
        self.service.created = ({"job_id": JOB, "cache_hit": True}, True)
        response = self.client.post(
            PREFIX, json={"preview_snapshot_id": SNAPSHOT, "symbol": SYMBOL}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cache_hit"])

    def test_unknown_fields_are_rejected_not_ignored(self):
        response = self.client.post(
            PREFIX,
            json={
                "preview_snapshot_id": SNAPSHOT,
                "symbol": SYMBOL,
                "score": 99,
                "facts": [],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.service.calls, [], "an invalid body must not reach the service")

    def test_snapshot_id_must_be_32_lowercase_hex(self):
        for bad in ("", "A" * 32, "a" * 31, "a" * 33, "z" * 32, "a-b"):
            response = self.client.post(PREFIX, json={"preview_snapshot_id": bad, "symbol": SYMBOL})
            self.assertEqual(response.status_code, 422, bad)
        self.assertEqual(self.service.calls, [])

    def test_symbol_must_be_a_prefixed_stock_code(self):
        for bad in ("600000", "600000.sh", "60000.SH", "6000000.SH", "000001.BJ", "AAPL"):
            response = self.client.post(
                PREFIX, json={"preview_snapshot_id": SNAPSHOT, "symbol": bad}
            )
            self.assertEqual(response.status_code, 422, bad)
        self.assertEqual(self.service.calls, [])


class GetRouteTest(RouteCase):
    def test_get_returns_the_current_job(self):
        response = self.client.get(f"{PREFIX}/{JOB}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "succeeded")
        self.assertEqual(self.service.calls[-1], ("get", JOB))

    def test_get_is_read_only(self):
        self.client.get(f"{PREFIX}/{JOB}")
        self.assertEqual([name for name, *_ in self.service.calls], ["get"])


class RetryRouteTest(RouteCase):
    def test_a_retry_takes_a_snapshot_and_answers_202(self):
        response = self.client.post(f"{PREFIX}/{JOB}/retry", json={"preview_snapshot_id": SNAPSHOT})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.service.calls[-1], ("retry", JOB, SNAPSHOT))

    def test_a_reused_retry_answers_200(self):
        self.service.retried = ({"job_id": JOB, "cache_hit": True}, True)
        response = self.client.post(f"{PREFIX}/{JOB}/retry", json={"preview_snapshot_id": SNAPSHOT})
        self.assertEqual(response.status_code, 200)

    def test_retry_rejects_unknown_fields_and_bad_snapshot(self):
        bodies = ({"preview_snapshot_id": SNAPSHOT, "symbol": SYMBOL}, {"preview_snapshot_id": "x"})
        for body in bodies:
            response = self.client.post(f"{PREFIX}/{JOB}/retry", json=body)
            self.assertEqual(response.status_code, 422, body)
        self.assertEqual(self.service.calls, [])


class CancelRouteTest(RouteCase):
    def test_cancel_returns_the_actual_status(self):
        response = self.client.post(f"{PREFIX}/{JOB}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertEqual(self.service.calls[-1], ("cancel", JOB))


class ErrorMappingTest(RouteCase):
    def test_each_research_error_keeps_its_declared_status(self):
        """The route must not collapse distinct failures into one generic status."""
        for code, status in (
            ("research_disabled", 503),
            ("worker_unavailable", 503),
            ("model_not_configured", 503),
            ("preview_expired", 409),
            ("preview_changed", 409),
            ("job_active", 409),
            ("job_not_found", 404),
            ("quota_exceeded", 429),
            ("not_candidate", 422),
            ("ineligible_evidence", 422),
        ):
            self.service.failure = ResearchError(code, status)
            response = self.client.post(
                PREFIX, json={"preview_snapshot_id": SNAPSHOT, "symbol": SYMBOL}
            )
            self.assertEqual(response.status_code, status, code)
            self.assertEqual(response.json()["error_code"], code, code)

    def test_error_bodies_carry_a_message_not_a_traceback(self):
        self.service.failure = ResearchError("preview_expired")
        response = self.client.get(f"{PREFIX}/{JOB}")
        self.assertEqual(set(response.json()), {"error_code", "message"})
        self.assertNotIn("Traceback", response.text)
        self.assertNotIn("research_models", response.text)


if __name__ == "__main__":
    unittest.main()
