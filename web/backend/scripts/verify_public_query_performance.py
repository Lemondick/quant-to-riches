"""Deterministic concurrent-source and lightweight public-worker contracts."""

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime
from importlib import import_module
from pathlib import Path
from threading import Barrier, Lock
from unittest.mock import patch

import requests

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.fund_detail import PublicFundDetailProvider

WORKER = Path(__file__).resolve().parents[2] / "worker"
sys.path.insert(0, str(WORKER))
child_public_operation = import_module("public_operations").child_public_operation


class Response:
    def __init__(self, data=None, content=None):
        self.data = data
        self.content = json.dumps(data).encode() if content is None else content

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class PublicQueryTests(unittest.TestCase):
    def provider(self, rules_fail=False):
        # Prove overlap without requiring five requests to violate the four-slot limit.
        barrier, lock, calls = Barrier(2), Lock(), []

        def get(url, **kwargs):
            with lock:
                calls.append(url)
                position = len(calls)
            if position <= 2:
                barrier.wait(timeout=3)
            if "pingzhongdata" in url:
                points = [{"x": 1735660800000, "y": 1.0}, {"x": 1735747200000, "y": 1.1}]
                return Response(
                    content=(
                        'var fS_code = "004243"; var Data_netWorthTrend = '
                        + json.dumps(points)
                        + ";"
                    ).encode()
                )
            if "jjfl_" in url:
                self.assertEqual(kwargs["timeout"], (2, 5))
                if rules_fail:
                    raise requests.exceptions.ReadTimeout("synthetic optional source unavailable")
                return Response(
                    content=(
                        "<html><title>004243</title><h4 class='t'>交易状态</h4>"
                        "<table><tr><td>申购状态</td><td>开放申购</td></tr></table></html>"
                    ).encode()
                )
            if "JJGG" in url:
                self.assertEqual(kwargs["timeout"], (2, 5))
                return Response({"Data": []})
            if "/detail/" in url:
                return Response({"data": {}})
            return Response({"data": {"fd_code": "004243", "fd_name": "Synthetic public fund"}})

        provider = PublicFundDetailProvider(
            http_get=get, now=lambda: datetime(2026, 10, 12, tzinfo=SHANGHAI_TZ)
        )
        return provider, calls

    def test_independent_sources_overlap_and_cache_does_not_refetch(self):
        provider, calls = self.provider()
        detail = provider.detail("004243")
        self.assertEqual(detail.name, "Synthetic public fund")
        self.assertEqual(len(detail.nav_history), 2)
        self.assertIs(provider.detail("004243"), detail)
        self.assertEqual(len(calls), 5)

    def test_worker_calls_keep_provider_cache_and_explicit_refresh_reloads_sources(self):
        public = import_module("public_operations")
        public._detail_provider.cache_clear()
        self.addCleanup(public._detail_provider.cache_clear)
        first_provider, first_calls = self.provider()
        with patch(
            "quant_assistant.providers.fund_detail.PublicFundDetailProvider",
            return_value=first_provider,
        ) as constructor:
            first = public.execute_public("fund.detail", {"symbol": "004243"})
            cached = public.execute_public("fund.detail", {"symbol": "004243"})
            self.assertEqual(first, cached)
            self.assertEqual(len(first_calls), 5)
            self.assertEqual(constructor.call_count, 1)
            refreshed = public.execute_public("fund.detail", {"symbol": "004243", "_refresh": True})
            self.assertEqual(refreshed["symbol"], "004243")
            self.assertEqual(len(first_calls), 10)
            self.assertEqual(constructor.call_count, 1)

    def test_optional_timeout_keeps_verified_nav_and_unknown_rules(self):
        provider, _ = self.provider(rules_fail=True)
        detail = provider.detail("004243")
        self.assertEqual(len(detail.nav_history), 2)
        self.assertIsNone(detail.trading_rules)
        rules = next(item for item in detail.source_health if item.name == "eastmoney_rules")
        self.assertFalse(rules.available)
        self.assertEqual(rules.last_error, "PublicHttpError")

    def test_public_entry_does_not_import_financial_ai_execution(self):
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import public_operations; "
                "from quant_assistant.providers.fund_detail import PublicFundDetailProvider; "
                "from quant_assistant.controllers.schemas import FundDetailResponse; "
                "assert not any(name in sys.modules for name in "
                '("operations", "paid_operations", "research_operations"))',
            ],
            env={**os.environ, "PYTHONPATH": str(WORKER)},
            capture_output=True,
            check=True,
            timeout=30,
        )

    def test_public_child_redacts_errors_and_rejects_nonfinite_outputs(self):
        with patch(
            "public_operations.execute_public",
            side_effect=RuntimeError("private diagnostic detail"),
        ):
            self.assertEqual(
                child_public_operation("fund.detail", {}),
                {"result": None, "error_code": "worker_computation_failed"},
            )
        with patch("public_operations.execute_public", return_value={"value": float("nan")}):
            self.assertEqual(
                child_public_operation("fund.detail", {}),
                {"result": None, "error_code": "invalid_request"},
            )


if __name__ == "__main__":
    unittest.main()
