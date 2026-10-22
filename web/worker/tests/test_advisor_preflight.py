"""Preflight uses report evidence rules but never calls paid providers."""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import advisor_preflight
from analysis_context import FACTS, build_context


class AdvisorPreflightTests(unittest.TestCase):
    def test_missing_public_and_local_facts_have_links_without_model_calls(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, QUANT_SITE_ROOT=directory),
        ):
            snapshot = {
                "version": 1,
                "user_id": "a" * 32,
                "role": "user",
                "tables": {t: [] for t in FACTS},
            }
            database, queries, root = build_context(snapshot)
            queries.market_news = lambda *_: SimpleNamespace(items=[], stale=True, unavailable=True)
            queries.market_quotes = lambda **_: SimpleNamespace(
                items=[], stale=True, unavailable=True
            )

            # analytical_context is a context manager since the E17
            # try/finally consolidation; the fake yields the real triple.
            @contextmanager
            def fake_context(_snapshot):
                yield (database, queries, root)

            with (
                patch.object(advisor_preflight, "analytical_context", side_effect=fake_context),
                patch("paid_operations.public_post") as paid,
            ):
                result = advisor_preflight.execute_preflight(
                    {
                        "snapshot": snapshot,
                        "report_kind": "morning",
                        "strategy": {"parameters": {}},
                        "preferences": {"watchlist": []},
                    }
                )
            paid.assert_not_called()
            self.assertEqual(result["model_calls"], 0)
            self.assertFalse(result["decision_ready"])
            self.assertGreaterEqual(len(result["issues"]), 3)
            self.assertTrue(all(i["url"].startswith("#/pages/") for i in result["issues"]))
            self.assertNotIn("sources", result)
            self.assertFalse((root / "research-evidence.json").exists())

    def test_unknown_kind_cannot_build_an_account_context(self):
        with (
            patch.object(advisor_preflight, "analytical_context") as build,
            self.assertRaises(ValueError),
        ):
            advisor_preflight.execute_preflight({"report_kind": "unknown"})
        build.assert_not_called()
