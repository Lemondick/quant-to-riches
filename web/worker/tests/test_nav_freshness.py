"""Historic official NAV remains evidence and never becomes an intraday estimate."""

import json
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.data_quality import metadata
from quant_assistant.providers.fund_valuation import AkshareFundNavProvider, LocalFundNavProvider
from quant_assistant.providers.nav_freshness import policy_for_fund
from quant_assistant.services.api_queries.local_portfolio import _PortfolioMixin

NOW = datetime(2026, 10, 18, 10, tzinfo=SHANGHAI_TZ)


class NavFreshnessTests(unittest.TestCase):
    def test_shared_publication_cases_match_native_runtime(self):
        fixture = json.loads(
            (
                Path(__file__).resolve().parents[3] / "tests/fixtures/public-quality-cases.json"
            ).read_text(encoding="utf-8")
        )
        for case in fixture["navs"]:
            with self.subTest(case=case):
                result = metadata(
                    "official_nav",
                    "synthetic",
                    case["date"],
                    datetime.fromisoformat(case["now"]),
                    nav_policy=case["policy"],
                    nav_valuation_weekday=case["weekday"],
                )
                self.assertEqual(case["quality"], result["quality_status"])
                self.assertEqual(case["age"], result["nav_age_sessions"])
                self.assertEqual(case["stale"], result["is_stale"])
                self.assertEqual(case["date"], result["updated_at"])
                self.assertIsNone(result["source_time"])

    def test_policy_uses_verified_type_or_frequency_and_never_guesses_from_name(self):
        for kind, frequency, expected in (
            ("股票型", None, "daily_t1"),
            ("债券型-长债", None, "daily_t1"),
            ("QDII-混合型", None, "qdii_t3"),
            ("货币型", None, "unknown"),
            ("指数型", None, "unknown"),
            ("某某股票基金", None, "unknown"),
            (None, "weekly", "weekly"),
        ):
            with self.subTest(kind=kind, frequency=frequency):
                self.assertEqual(expected, policy_for_fund(kind, frequency))

    def test_query_view_keeps_publication_policy_and_calendar_evidence(self):
        record = {
            "000001": {
                "observed_at": NOW.isoformat(),
                "fund_type": "股票型",
                "points": [
                    {"nav_date": "2026-09-01", "unit_nav": "1", "source": "eastmoney"},
                    {"nav_date": "2026-09-02", "unit_nav": "1.01", "source": "eastmoney"},
                ],
            }
        }
        query = _PortfolioMixin()
        query._fund_valuation_provider = LocalFundNavProvider(record, now=lambda: NOW)
        result = query.fund_valuations(["000001"]).items[0]
        self.assertEqual("daily_t1", result.nav_freshness_policy)
        self.assertIsNotNone(result.calendar_revision)
        self.assertTrue(result.is_stale)

    def test_unknown_publication_policy_does_not_advertise_old_nav_as_current(self):
        result = metadata("official_nav", "synthetic", "2026-10-14", NOW)
        self.assertTrue(result["is_stale"])
        self.assertEqual("unknown_time", result["quality_status"])

    def assert_old_evidence(self, provider):
        try:
            value = provider.estimate("000001")
        except (ValueError, RuntimeError) as error:
            self.fail(
                "A genuine dated old NAV should remain available as stale evidence: "
                + type(error).__name__
            )
        self.assertEqual("2026-09-02", value.nav_date)
        self.assertEqual(Decimal("1.01"), value.official_nav)
        self.assertIsNone(value.estimate_nav)
        self.assertIsNone(value.change_rate)
        self.assertTrue(value.is_stale)

    def test_local_old_official_nav_is_retained_with_original_date_and_without_estimate(self):
        record = {
            "000001": {
                "observed_at": NOW.isoformat(),
                "points": [
                    {"nav_date": "2026-09-01", "unit_nav": "1", "source": "eastmoney"},
                    {"nav_date": "2026-09-02", "unit_nav": "1.01", "source": "eastmoney"},
                ],
            }
        }
        self.assert_old_evidence(LocalFundNavProvider(record, now=lambda: NOW))

    def test_legacy_public_provider_also_retains_old_official_evidence(self):
        frame = pd.DataFrame(
            {"净值日期": ["2026-09-01", "2026-09-02"], "单位净值": [1, 1.01], "日增长率": [None, 1]}
        )
        provider = AkshareFundNavProvider(
            akshare_module=SimpleNamespace(fund_open_fund_info_em=lambda **kwargs: frame),
            now=lambda: NOW,
        )
        self.assert_old_evidence(provider)


if __name__ == "__main__":
    unittest.main()
