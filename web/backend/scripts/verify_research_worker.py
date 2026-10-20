"""Verify the isolated three-role research worker contract and failure codes.

Covers the parts of the research subsystem that can be exercised without a real
model endpoint: the strict JSON contract in ``contract.py``, the standard
library adapter in ``main.py`` running as a real isolated subprocess, and the
stable fact identifiers published by the evidence freezer.
No network and no credentials are involved; the "real endpoint" acceptance is a
separate, credentialed step documented in the MVP execution plan.

The fixtures below deliberately avoid embedding anything that looks like a
credential, so this file stays clean under ``check_no_secrets.py``.
"""

import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
WORKER_DIR = BACKEND / "src" / "quant_assistant" / "integrations" / "tradingagents_worker"
EVIDENCE = BACKEND / "src" / "quant_assistant" / "analysis" / "research_evidence.py"
sys.path.insert(0, str(BACKEND / "src"))

from quant_assistant.integrations.tradingagents_worker import main as adapter  # noqa: E402
from quant_assistant.integrations.tradingagents_worker.contract import (  # noqa: E402
    ROLES,
    validate_report,
    validate_role,
)

FACTS = (
    {"fact_id": "rank", "label": "候选名次", "value": 1},
    {"fact_id": "volatility_20", "label": "20日波动率", "value": 0.31},
)
COMMON = {"symbol": "600000.SH", "data_date": "2026-10-05"}
REQUEST = {**COMMON, "facts": FACTS, "warnings": []}


def bull_points(*texts, refs=("rank",)):
    return {"points": [{"text": text, "fact_ids": list(refs)} for text in texts]}


def reviewer(**overrides):
    value = {
        **COMMON,
        "summary": "复核总结：引用仅说明事实存在，不构成收益结论。",
        "disagreements": [{"text": "双方对波动率含义存在分歧。", "fact_ids": ["volatility_20"]}],
        "questions": ["是否存在尚未纳入数据的除权事件？"],
    }
    value.update(overrides)
    return value


class ContractAcceptanceTest(unittest.TestCase):
    """The happy path must stay accepted, or the worker can never succeed."""

    def test_each_role_accepts_a_well_formed_value(self):
        for role in ("bull", "bear"):
            validate_role({**COMMON, **bull_points("依据候选名次。")}, role, REQUEST)
        validate_role(reviewer(), "reviewer", REQUEST)

    def test_report_requires_exactly_the_three_roles(self):
        report = {
            "bull": {**COMMON, **bull_points("多头依据。")},
            "bear": {**COMMON, **bull_points("空头反证。", refs=("volatility_20",))},
            "reviewer": reviewer(),
        }
        self.assertIs(validate_report(report, REQUEST), report)
        for broken in (
            {k: v for k, v in report.items() if k != "bear"},
            {**report, "extra": report["bull"]},
        ):
            with self.assertRaises(ValueError):
                validate_report(broken, REQUEST)


class RoleRejectionTest(unittest.TestCase):
    """A drifting model must fail loudly instead of producing a neutral rating."""

    def test_wrong_symbol_or_date_is_rejected(self):
        for wrong in ({"symbol": "000001.SZ"}, {"data_date": "2026-10-04"}):
            with self.assertRaises(ValueError):
                validate_role({**COMMON, **wrong, **bull_points("依据。")}, "bull", REQUEST)

    def test_unknown_fact_id_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_role(
                {**COMMON, **bull_points("依据。", refs=("no_such_fact",))}, "bull", REQUEST
            )

    def test_point_and_reference_counts_are_bounded(self):
        cases = (
            {**COMMON, "points": []},
            {**COMMON, **bull_points(*("依据。" for _ in range(4)))},
            {**COMMON, **bull_points("依据。", refs=())},
            {**COMMON, **bull_points("依据。", refs=tuple(["rank"] * 16))},
        )
        for value in cases:
            with self.assertRaises(ValueError):
                validate_role(value, "bull", REQUEST)

    def test_text_and_summary_limits_are_enforced(self):
        cases = (
            ("bull", {**COMMON, **bull_points("")}),
            ("bull", {**COMMON, **bull_points("x" * 501)}),
            ("reviewer", reviewer(summary="x" * 801)),
            ("reviewer", reviewer(questions=["x" * 301])),
        )
        for role, value in cases:
            with self.assertRaises(ValueError):
                validate_role(value, role, REQUEST)

    def test_reviewer_question_count_is_bounded(self):
        for questions in ([], ["q"] * 6):
            with self.assertRaises(ValueError):
                validate_role(reviewer(questions=questions), "reviewer", REQUEST)

    def test_role_field_sets_do_not_cross_over(self):
        with self.assertRaises(ValueError):
            validate_role(reviewer(), "bull", REQUEST)
        with self.assertRaises(ValueError):
            validate_role({**COMMON, **bull_points("依据。")}, "reviewer", REQUEST)

    def test_non_mapping_is_rejected(self):
        for value in ([], "text", None, 3):
            with self.assertRaises(ValueError):
                validate_role(value, "bull", REQUEST)


class WorkerSubprocessTest(unittest.TestCase):
    """The adapter must degrade to a stable error code, never to a fake report."""

    def run_worker(self, task, env=None):
        environment = {
            **os.environ,
            "QUANT_AGENT_PARENT_PID": str(os.getpid()),
            **(env or {}),
        }
        process = subprocess.run(
            [sys.executable, "-I", str(WORKER_DIR / "main.py")],
            input=json.dumps(task, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=60,
            env=environment,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        return json.loads(process.stdout.decode("utf-8"))

    def mock_task(self, **config):
        return {
            "request": REQUEST,
            "config": {"provider": "mock", "model": "mock-v1", "endpoint": "", **config},
            "timeout_seconds": 30,
        }

    def test_mock_provider_returns_a_report_that_passes_the_contract(self):
        result = self.run_worker(self.mock_task())
        self.assertEqual(set(result["report"]), set(ROLES))
        validate_report(result["report"], REQUEST)
        self.assertEqual(result["usage"], [None, None, None])

    def test_unknown_provider_is_not_configured(self):
        result = self.run_worker(self.mock_task(provider="other"))
        self.assertEqual(result, {"error_code": "model_not_configured"})

    def test_compatible_provider_without_a_key_file_is_not_configured(self):
        # No QUANT_AGENT_KEY_FILE: the adapter must refuse before any dial-out.
        result = self.run_worker(
            self.mock_task(
                provider="compatible", model="gpt-x", endpoint="https://example.invalid/v1"
            ),
            env={"QUANT_AGENT_KEY_FILE": ""},
        )
        self.assertEqual(result, {"error_code": "model_not_configured"})

    def test_non_https_or_credentialed_endpoint_is_rejected(self):
        for endpoint in (
            "http://example.invalid/v1",
            "https://user@example.invalid/v1",
            "https://example.invalid/v1?key=1",
        ):
            result = self.run_worker(
                self.mock_task(provider="compatible", model="gpt-x", endpoint=endpoint)
            )
            self.assertEqual(result, {"error_code": "model_not_configured"}, endpoint)

    def test_missing_key_never_resolves_or_opens_a_connection(self):
        with (
            patch.dict(os.environ, {"QUANT_AGENT_KEY_FILE": ""}),
            patch("socket.getaddrinfo", side_effect=AssertionError("unexpected DNS")),
            patch.object(
                adapter,
                "PinnedHTTPSConnection",
                side_effect=AssertionError("unexpected connection"),
            ),
            self.assertRaisesRegex(adapter.WorkerError, "^model_not_configured$"),
        ):
            adapter.call_model(
                {"endpoint": "https://example.invalid/v1", "model": "synthetic"},
                "bull",
                REQUEST,
                {},
                time.monotonic() + 3,
                [1],
            )

    def test_oversized_stdin_is_invalid_output(self):
        task = self.mock_task()
        task["request"] = {**REQUEST, "warnings": ["x" * 70000]}
        self.assertEqual(self.run_worker(task), {"error_code": "invalid_output"})

    def test_malformed_stdin_is_invalid_output(self):
        process = subprocess.run(
            [sys.executable, "-I", str(WORKER_DIR / "main.py")],
            input=b"{not json",
            capture_output=True,
            timeout=60,
            env={**os.environ, "QUANT_AGENT_PARENT_PID": str(os.getpid())},
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout), {"error_code": "invalid_output"})

    @unittest.skipUnless(sys.platform == "linux", "Linux parent-death protection")
    def test_missing_or_wrong_parent_pid_is_rejected_before_reading_a_task(self):
        for parent in (None, str(os.getpid() + 1)):
            with self.subTest(parent="missing" if parent is None else "wrong"):
                environment = dict(os.environ)
                environment.pop("QUANT_AGENT_PARENT_PID", None)
                if parent is not None:
                    environment["QUANT_AGENT_PARENT_PID"] = parent
                process = subprocess.run(
                    [sys.executable, "-I", str(WORKER_DIR / "main.py")],
                    input=json.dumps(self.mock_task()).encode("utf-8"),
                    capture_output=True,
                    timeout=15,
                    env=environment,
                )
                self.assertEqual(process.returncode, 1)
                self.assertEqual(process.stdout, b"")


class FactLabelContractTest(unittest.TestCase):
    """Keep backend fact IDs stable while the new Vue UI awaits API wiring."""

    def test_published_contribution_fact_identifiers_remain_stable(self):
        evidence = EVIDENCE.read_text(encoding="utf-8")
        weights = re.search(r"WEIGHTS = \{(.+?)\}", evidence)
        self.assertIsNotNone(weights, f"WEIGHTS not found in {EVIDENCE.name}")
        keys = re.findall(r'"([a-z_0-9]+)":', weights.group(1))
        emitted = {f"{key}_contribution" for key in keys}
        self.assertEqual(len(emitted), 4, f"unexpected WEIGHTS layout: {sorted(emitted)}")

        self.assertEqual(
            emitted,
            {
                "momentum_z_contribution",
                "trend_z_contribution",
                "low_volatility_z_contribution",
                "liquidity_z_contribution",
            },
        )


if __name__ == "__main__":
    unittest.main()
