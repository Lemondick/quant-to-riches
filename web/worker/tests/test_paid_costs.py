"""Provider usage is recorded exactly, with no guessed or conflicting token counts."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from paid_operations import CostClient, MemoryKey

from quant_assistant.providers.advisor_models import AdvisorModelError, AdvisorModels
from quant_assistant.services.model_costs import tokens


class PaidCostTests(unittest.TestCase):
    def test_real_transport_failure_records_role_and_stage_without_repeating_post(self):
        for error, expected in [
            (requests.ReadTimeout(), "read_timeout"),
            (requests.ConnectTimeout(), "connect_timeout"),
        ]:
            with self.subTest(stage=expected):
                session = SimpleNamespace(
                    headers={},
                    post=Mock(
                        return_value=Mock(status_code=200, json=lambda: {"call_id": "synthetic"})
                    ),
                )
                with patch("paid_operations.requests.Session", return_value=session):
                    client = CostClient(
                        {"api": "http://127.0.0.1/internal", "lease": {}, "secret": "synthetic"}
                    )
                external = Mock(side_effect=error)
                settings = SimpleNamespace(
                    key_file=MemoryKey("synthetic-test-key"),
                    jev_key_file=MemoryKey("synthetic-jev-key"),
                    endpoint="https://example.org/v1",
                )
                models = AdvisorModels(settings, post=external)
                models.costs, models.call_role = client, "news"
                with self.assertRaises(AdvisorModelError):
                    models.chat("synthetic-model", "instructions", {})
                self.assertEqual(external.call_count, 1)
                reserve, finish = [call.kwargs["json"] for call in session.post.call_args_list]
                self.assertEqual(reserve["role"], "news")
                self.assertEqual(reserve["provider"], "compatible_api")
                self.assertEqual(finish["stage"], expected)
                self.assertEqual(finish["unknown_reason"], "no_usage")

    def test_native_finish_accepts_both_provider_field_names_without_guessing(self):
        for usage, expected in [
            ({"input_tokens": 123, "output_tokens": 45}, (123, 45)),
            ({"prompt_tokens": 123, "completion_tokens": 45}, (123, 45)),
            ({"input_tokens": 0, "output_tokens": 0}, (0, 0)),
            ({"input_tokens": 123, "output_tokens": 45, "prompt_tokens": 124}, (None, None)),
            ({"input_tokens": True, "output_tokens": 45}, (None, None)),
            ({"input_tokens": -1, "output_tokens": 45}, (None, None)),
            ({"input_tokens": 2_000_000_001, "output_tokens": 45}, (None, None)),
            ({"input_tokens": 123}, (None, None)),
            ({}, (None, None)),
        ]:
            with self.subTest(usage=usage):
                session = SimpleNamespace(headers={}, post=Mock(return_value=Mock()))
                with patch("paid_operations.requests.Session", return_value=session):
                    client = CostClient(
                        {"api": "http://127.0.0.1/internal", "lease": {}, "secret": "synthetic"}
                    )
                    client.finish("synthetic-call", usage)
                body = session.post.call_args.kwargs["json"]
                self.assertEqual((body["input_tokens"], body["output_tokens"]), expected)

    def test_shared_usage_rejects_conflicting_aliases_and_invalid_totals(self):
        self.assertEqual(
            tokens(
                {"prompt_tokens": 7, "completion_tokens": 3, "input_tokens": 7, "output_tokens": 3}
            ),
            (7, 3),
        )
        for usage in [
            {"prompt_tokens": 7, "completion_tokens": 3, "input_tokens": 8},
            {"input_tokens": 7, "output_tokens": 3, "total_tokens": 11},
            {"input_tokens": 7, "output_tokens": 3, "total_tokens": True},
            {"input_tokens": 2_000_000_001, "output_tokens": 3},
        ]:
            self.assertIsNone(tokens(usage))


if __name__ == "__main__":
    unittest.main()
