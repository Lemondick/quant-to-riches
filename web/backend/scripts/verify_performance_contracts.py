"""Guard the public-read performance contracts against silent regressions."""

import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quant_assistant.providers.market_feed import AkshareMarketFeedProvider, _Section

BACKEND = Path(__file__).resolve().parents[1]
WEB = BACKEND.parent
WORKER = WEB / "worker"
DOTNET = WEB / "backend-dotnet" / "src"


def _read(path: Path) -> str:
    """Read a contract source, failing loudly when the layout moves."""

    if not path.is_file():
        raise AssertionError(f"contract source is missing: {path}")
    return path.read_text(encoding="utf-8")


def _quoted_kinds(text: str, label: str) -> set[str]:
    """Extract the quoted kinds from a single tuple/array literal declaration."""

    match = re.search(r"=\s*[\[(]([^\])]*?)[\])]", text)
    if match is None:
        raise AssertionError(f"kind declaration not found: {label}")
    kinds = set(re.findall(r'"([^"]+)"', match.group(1)))
    if not kinds:
        raise AssertionError(f"kind declaration parsed empty: {label}")
    return kinds


# Legacy repositories still block the caller through the synchronous wrappers;
# at the current single-user load there is no starving path, so they stay as
# frozen stock. New code must use ReadAsync/WriteAsync: shrink, never grow.
SYNC_ACCOUNT_WRAPPER_BASELINE = 114


class PerformanceContractAcceptance(unittest.TestCase):
    def test_valuation_fallback_never_defaults_to_the_full_history_scraper(self):
        source = _read(BACKEND / "src/quant_assistant/providers/fund_valuation.py")
        # The request-path fallback must stay opt-in; a default Akshare provider
        # would reintroduce the multi-second full NAV-history scrape per request.
        self.assertIn("self._fallback = fallback", source)
        self.assertNotIn("fallback or AkshareFundNavProvider", source)
        operations = _read(WORKER / "public_operations.py")
        self.assertIn("fallback=LocalFundNavProvider", operations)

    def test_resident_worker_pool_covers_every_independent_public_kind(self):
        dotnet = _read(DOTNET / "QuantAssistant.Infrastructure/Jobs/ComputeJobRepository.cs")
        dotnet_kinds = _quoted_kinds(
            dotnet[dotnet.index("IndependentPublicKinds") :], "IndependentPublicKinds"
        )
        operations = _read(WORKER / "public_operations.py")
        public_kinds = _quoted_kinds(
            operations[operations.index("PUBLIC_CAPABILITIES") :], "PUBLIC_CAPABILITIES"
        )
        self.assertIn(
            'IN_PROCESS_CAPABILITIES = (*PUBLIC_CAPABILITIES, "fund.valuations")',
            operations,
        )
        # Both ends of the queue must agree on the exact resident kind set.
        self.assertEqual(public_kinds | {"fund.valuations"}, dotnet_kinds)
        worker = _read(WORKER / "run_worker.py")
        self.assertIn('RESIDENT_CAPABILITIES = (*IN_PROCESS_CAPABILITIES, "strategy.view")', worker)
        # Resident jobs must run on the bounded pool, not on the claim loop:
        # one slow snapshot may never serialize lighter public queries behind it.
        self.assertIn("self.resident.submit(self.run_public_job", worker)

    def test_gateway_serves_public_reads_from_memory_before_touching_the_queue(self):
        service = _read(DOTNET / "QuantAssistant.Api/Services/ComputeService.cs")
        self.assertIn("cache.TryGetValue", service)
        self.assertIn("signal.WaitAsync", service)
        self.assertNotIn("Task.Delay(200", service)
        refresher = _read(DOTNET / "QuantAssistant.Api/Services/PublicSnapshotRefresher.cs")
        self.assertIn("BackgroundService", refresher)
        # Actual core-first scheduling and deadlines are exercised by the native
        # PostgreSQL PublicRefreshSchedulingTests, independent of helper names.

    def test_sync_account_database_call_sites_do_not_grow(self):
        call_sites = 0
        for path in DOTNET.rglob("*.cs"):
            text = path.read_text(encoding="utf-8")
            call_sites += len(re.findall(r"\bdatabase\.(?:Read|Write)\(", text))
            call_sites += len(
                re.findall(r"GetRequiredService<AccountDatabase>\(\)\.(?:Read|Write)\(", text)
            )
        self.assertLessEqual(
            call_sites,
            SYNC_ACCOUNT_WRAPPER_BASELINE,
            f"sync AccountDatabase call sites grew to {call_sites}; "
            "new code must use ReadAsync/WriteAsync",
        )

    def test_full_market_fetches_stay_behind_the_market_ttl_cache(self):
        # Exercise the cache rather than pinning an obsolete upstream function name.
        for section, loader in (
            ("funds", "_load_fund_rankings"),
            ("sectors", "_load_sector_rankings"),
        ):
            with self.subTest(section=section):
                clock = [0.0]
                provider = AkshareMarketFeedProvider(monotonic_clock=lambda clock=clock: clock[0])
                fetch = Mock(return_value=_Section((), "eastmoney"))
                with patch.object(provider, loader, fetch):
                    first = provider.market_snapshot(section)
                    second = provider.market_snapshot(section)
                    self.assertEqual(second.fund_rankings, first.fund_rankings)
                    self.assertEqual(second.sector_rankings, first.sector_rankings)
                    self.assertEqual(fetch.call_count, 1)
                    clock[0] = 3601.0
                    provider.market_snapshot(section)
                    self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
