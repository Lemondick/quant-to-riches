"""Intraday failure must never trigger a full-history NAV request."""

import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.providers.base import ProviderUnavailable
from quant_assistant.providers.fund_valuation import (
    FallbackFundValuationProvider,
    FundIntradayEstimate,
    FundValuationError,
    LocalFundNavProvider,
)
from quant_assistant.services.api_queries.local_portfolio import _PortfolioMixin


class ValuationFallbackTests(unittest.TestCase):
    def local(
        self,
        *,
        source="eastmoney",
        latest="2026-10-16",
        previous="2026-10-15",
        amount="1.25",
        daily_return=None,
    ):
        return LocalFundNavProvider(
            {
                "004243": {
                    "observed_at": "2026-10-16T18:30:00+08:00",
                    "points": [
                        {"nav_date": previous, "unit_nav": "1.20", "source": source},
                        {
                            "nav_date": latest,
                            "unit_nav": amount,
                            "source": source,
                            "daily_return": daily_return,
                        },
                    ],
                }
            },
            now=lambda: datetime(2026, 10, 17, 10, tzinfo=SHANGHAI_TZ),
        )

    def test_native_local_nav_preserves_provenance_and_decimal_values_without_network(self):
        with patch(
            "requests.get",
            side_effect=AssertionError("network forbidden"),
        ):
            result = self.local().estimate("004243")
        self.assertIsNone(result.estimate_nav)
        self.assertEqual(result.official_nav, Decimal("1.25"))
        self.assertEqual(result.previous_nav, Decimal("1.20"))
        self.assertEqual(result.valuation_type, "latest_nav")
        self.assertEqual(result.nav_date, "2026-10-16")
        self.assertIsNone(result.observed_at)
        self.assertEqual(result.fetched_at.isoformat(), "2026-10-16T18:30:00+08:00")
        self.assertEqual(result.updated_at, "2026-10-16")
        self.assertEqual(result.source, "local:eastmoney")
        self.assertIsNone(result.change_rate)

    def test_native_local_nav_rejects_untrusted_future_duplicate_and_nonfinite_points(self):
        for changes in (
            {"source": "synthetic"},
            {"latest": "2027-01-01"},
            {"previous": "2026-10-16"},
            {"amount": "NaN"},
            {"amount": "0"},
        ):
            with self.subTest(changes=changes), self.assertRaises((ValueError, FundValuationError)):
                self.local(**changes).estimate("004243")

    def test_native_local_nav_missing_symbol_stays_unavailable(self):
        with self.assertRaises(ProviderUnavailable):
            self.local().estimate("000001")

    def test_native_local_nav_preserves_official_return_on_distribution_days(self):
        result = self.local(amount="1.10", daily_return="0.50").estimate("004243")
        self.assertEqual(result.official_change_rate, Decimal("0.50"))
        self.assertEqual(result.official_nav, Decimal("1.10"))
        with self.assertRaises(FundValuationError):
            self.local(daily_return="NaN").estimate("004243")

    def test_native_local_nav_is_used_after_intraday_source_failure(self):
        provider = FallbackFundValuationProvider(
            primary=Mock(estimate=Mock(side_effect=ProviderUnavailable("synthetic"))),
            fallback=self.local(),
        )
        self.assertEqual(provider.estimate("004243").valuation_type, "latest_nav")

    def test_default_failure_does_not_construct_or_call_akshare_fallback(self):
        for error in (ProviderUnavailable("synthetic"), FundValuationError("synthetic")):
            with (
                self.subTest(error=type(error).__name__),
                patch("quant_assistant.providers.fund_valuation.AkshareFundNavProvider") as nav,
            ):
                provider = FallbackFundValuationProvider(
                    primary=Mock(estimate=Mock(side_effect=error))
                )
                with self.assertRaises(ProviderUnavailable):
                    provider.estimate("004243")
                nav.assert_not_called()

    def test_available_intraday_estimate_does_not_read_fallback(self):
        expected = object()
        primary, local = Mock(estimate=Mock(return_value=expected)), Mock()
        self.assertIs(FallbackFundValuationProvider(primary, local).estimate("004243"), expected)
        local.estimate.assert_not_called()

    def test_injected_local_nav_keeps_its_date_source_and_latest_nav_type(self):
        official = FundIntradayEstimate(
            symbol="004243",
            estimate_nav=Decimal("1.25"),
            previous_nav=Decimal("1.20"),
            change_rate=Decimal("4.1667"),
            observed_at=datetime(2026, 10, 17, tzinfo=SHANGHAI_TZ),
            source="verified_local_nav",
            nav_date="2026-10-16",
            valuation_type="latest_nav",
        )
        provider = FallbackFundValuationProvider(
            SimpleNamespace(estimate=Mock(side_effect=ProviderUnavailable("synthetic"))),
            SimpleNamespace(estimate=Mock(return_value=official)),
        )
        result = provider.estimate("004243")
        self.assertEqual(result.official_nav, official.official_nav)
        self.assertIsNone(result.estimate_nav)
        self.assertTrue(result.fallback_used)

    def test_query_marks_failed_symbols_unavailable_without_fabricating_navs(self):
        query = _PortfolioMixin()
        query._fund_valuation_provider = FallbackFundValuationProvider(
            primary=SimpleNamespace(estimate=Mock(side_effect=ProviderUnavailable("synthetic")))
        )
        result = query.fund_valuations(["004243", "000001"])
        self.assertEqual(result.items, ())
        self.assertEqual(result.unavailable, ("000001", "004243"))


if __name__ == "__main__":
    unittest.main()
