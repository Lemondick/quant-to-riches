"""Acceptance for fail-closed fund style-drift monitoring.

``services/fund_style_drift.py`` compares disclosed report-period holdings and
raises a review event when a fund's style drifts. It had no acceptance coverage,
yet its rules are all fail-closed and easy to break silently:

* a stale or un-dated fund detail is blocked, never partially recorded;
* snapshots are validated and canonicalised before persistence;
* the first report for a fund emits **no** event (it must not be flagged as
  total drift just because there is nothing to compare against);
* re-recording the same report period is a no-op, so history stays immutable;
* each report is compared with the *immediately preceding* report only;
* weighted overlap and its severity thresholds are computed conservatively.

Pure logic runs in memory; persistence runs against a real temporary DuckDB.
No network, provider, or credentials are involved.
"""

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from quant_assistant.common.time import SHANGHAI_TZ  # noqa: E402
from quant_assistant.models.entities import DataStatus  # noqa: E402
from quant_assistant.providers.fund_detail import FundDetail, FundHolding  # noqa: E402
from quant_assistant.services.fund_style_drift import (  # noqa: E402
    MAX_DIFF_HOLDINGS,
    FundHoldingsSnapshot,
    FundStyleDriftMonitor,
    FundStyleSyncItem,
    FundStyleSyncResult,
    compare_holdings,
    normalize_holdings_snapshot,
    snapshot_from_detail,
)

OBSERVED = datetime(2026, 7, 1, 10, 0, tzinfo=SHANGHAI_TZ)
Q1 = date(2026, 3, 31)
Q2 = date(2026, 6, 30)
Q3 = date(2026, 9, 30)


def holding(symbol, weight, asset_type="stock", name="披露名称", report=Q2):
    """Build a holding.

    ``compare_holdings`` reads ``report_date`` off the *previous* holdings to
    label the comparison, so raw fixtures must carry a real date.
    """
    return FundHolding(symbol, name, asset_type, Decimal(str(weight)), report)


def snapshot(holdings, report=Q2, symbol="000001", source="eastmoney", sid=None):
    return FundHoldingsSnapshot(
        symbol=symbol,
        report_date=report,
        source=source,
        observed_at=OBSERVED,
        holdings=tuple(holdings),
        source_snapshot_id=sid,
    )


def normalized(holdings, report=Q2, sid=None):
    return normalize_holdings_snapshot(snapshot(holdings, report, sid=sid))


def detail(holdings=(), report=Q2, *, stale=False, symbol="000001"):
    return FundDetail(
        symbol=symbol,
        name="测试基金",
        full_name=None,
        fund_type="混合型",
        company=None,
        established_on=None,
        latest_scale=None,
        benchmark=None,
        investment_target=None,
        nav_history=(),
        period_returns=(),
        holdings=tuple(holdings),
        managers=(),
        fees=(),
        announcements=(),
        holdings_report_date=report,
        observed_at=OBSERVED,
        stale=stale,
    )


class SnapshotNormalizationTest(unittest.TestCase):
    """Holdings must be validated and canonicalised before they are stored."""

    def test_the_snapshot_type_is_checked(self):
        with self.assertRaisesRegex(TypeError, "FundHoldingsSnapshot"):
            normalize_holdings_snapshot({"symbol": "000001"})

    def test_holdings_are_canonicalised(self):
        result = normalized([holding("sh600000", 12.5, "Stock")])
        item = result.holdings[0]
        self.assertEqual(item.symbol, "SH600000")
        self.assertEqual(item.asset_type, "stock")
        self.assertEqual(item.report_date, Q2)

    def test_holdings_are_ranked_by_weight_then_identity(self):
        result = normalized([holding("A", 5), holding("B", 20, "bond"), holding("C", 20, "stock")])
        self.assertEqual(
            [(item.asset_type, item.symbol) for item in result.holdings],
            [("stock", "C"), ("bond", "B"), ("stock", "A")],
        )

    def test_an_empty_disclosure_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no disclosed holdings"):
            normalized([])

    def test_a_datetime_report_date_is_rejected(self):
        bad = snapshot([holding("A", 10)])
        object.__setattr__(bad, "report_date", datetime(2026, 6, 30, tzinfo=SHANGHAI_TZ))
        with self.assertRaisesRegex(TypeError, "report_date must be a date"):
            normalize_holdings_snapshot(bad)

    def test_a_naive_observed_at_is_rejected(self):
        bad = snapshot([holding("A", 10)])
        object.__setattr__(bad, "observed_at", datetime(2026, 7, 1, 10, 0))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            normalize_holdings_snapshot(bad)

    def test_observed_at_is_converted_to_shanghai_time(self):
        result = normalized([holding("A", 10)])
        self.assertEqual(result.observed_at.utcoffset(), timedelta(hours=8))

    def test_duplicate_holdings_are_rejected(self):
        for rows in (
            [holding("A", 10), holding("A", 20)],
            [holding("A", 10, "stock"), holding("a", 20, "stock")],
        ):
            with self.assertRaisesRegex(ValueError, "duplicate disclosed holding"):
                normalized(rows)

    def test_the_same_symbol_in_different_asset_types_is_allowed(self):
        result = normalized([holding("A", 10, "stock"), holding("A", 20, "bond")])
        self.assertEqual(len(result.holdings), 2)

    def test_an_unknown_asset_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "stock or bond"):
            normalized([holding("A", 10, "cash")])

    def test_weights_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "must not exceed 100"):
            normalized([holding("A", 100.5)])
        # Boundaries themselves are accepted.
        self.assertEqual(normalized([holding("A", 0)]).holdings[0].weight, Decimal("0"))
        self.assertEqual(normalized([holding("A", 100)]).holdings[0].weight, Decimal("100"))

    def test_a_negative_weight_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            normalized([holding("A", -1)])

    def test_a_string_holding_is_not_a_sequence_of_holdings(self):
        bad = snapshot(["not-a-holding"])
        with self.assertRaisesRegex(TypeError, "FundHolding"):
            normalize_holdings_snapshot(bad)

    def test_source_snapshot_id_is_optional_but_typed(self):
        self.assertIsNone(normalized([holding("A", 10)]).source_snapshot_id)
        self.assertEqual(normalized([holding("A", 10)], sid=0).source_snapshot_id, 0)
        for bad, error in ((True, TypeError), (-1, ValueError), (1.5, TypeError)):
            with self.assertRaises(error):
                normalized([holding("A", 10)], sid=bad)


class SnapshotFromDetailTest(unittest.TestCase):
    def test_a_stale_detail_is_blocked(self):
        """Stale evidence must not be recorded as if it were current."""
        with self.assertRaisesRegex(ValueError, "stale"):
            snapshot_from_detail(detail([holding("A", 10)], stale=True))

    def test_a_detail_without_a_report_date_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "no holdings report date"):
            snapshot_from_detail(detail([holding("A", 10)], report=None))

    def test_a_foreign_object_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "FundDetail"):
            snapshot_from_detail(object())

    def test_a_clean_detail_is_converted(self):
        result = snapshot_from_detail(detail([holding("A", 10)]))
        self.assertEqual(result.symbol, "000001")
        self.assertEqual(result.report_date, Q2)


class OverlapComparisonTest(unittest.TestCase):
    """Overlap is weighted on both sides, so a swap of equal weights still drifts."""

    def test_identical_holdings_never_drift(self):
        current = normalized([holding("A", 60), holding("B", 40)])
        comparison = compare_holdings(current.holdings, current)
        self.assertEqual(comparison.overlap_ratio, Decimal("1"))
        self.assertEqual(comparison.severity, "info")
        self.assertFalse(comparison.drift_detected)

    def test_swapping_a_holding_is_measured_by_the_union(self):
        previous = [holding("A", 60), holding("B", 40)]
        current = normalized([holding("A", 60), holding("C", 40)])
        comparison = compare_holdings(previous, current)
        # min-sum 60 over max-sum 140: the retained name is weighted, not counted.
        self.assertAlmostEqual(comparison.overlap_ratio, Decimal(60) / Decimal(140))
        self.assertEqual(comparison.severity, "critical")
        self.assertTrue(comparison.drift_detected)
        self.assertEqual(comparison.added_holdings, ("stock:C (40.00%)",))
        self.assertEqual(comparison.removed_holdings, ("stock:B (40.00%)",))

    def test_complete_replacement_is_total_drift(self):
        current = normalized([holding("Z", 100)])
        comparison = compare_holdings([holding("A", 100)], current)
        self.assertEqual(comparison.overlap_ratio, Decimal("0"))
        self.assertEqual(comparison.severity, "critical")
        self.assertTrue(comparison.drift_detected)

    def test_severity_thresholds_are_exclusive_lower_bounds(self):
        """Exactly at a threshold falls into the milder band, by design."""
        cases = (
            ("0.5", "warning", True, [holding("A", 100)], [holding("A", 50)]),
            ("0.7", "info", True, [holding("A", 70)], [holding("A", 49)]),
            ("0.85", "info", False, [holding("A", 100)], [holding("A", 85)]),
        )
        for expected_overlap, severity, drift, previous, rows in cases:
            comparison = compare_holdings(previous, normalized(rows))
            self.assertEqual(comparison.overlap_ratio, Decimal(expected_overlap))
            self.assertEqual(comparison.severity, severity, expected_overlap)
            self.assertEqual(comparison.drift_detected, drift, expected_overlap)

    def test_the_previous_report_date_comes_from_the_previous_holdings(self):
        comparison = compare_holdings(
            [holding("A", 100)], normalized([holding("A", 50)], report=Q2)
        )
        self.assertEqual(comparison.previous_report_date, Q2)
        self.assertEqual(comparison.report_date, Q2)

    def test_comparing_against_nothing_is_treated_as_total_drift(self):
        """Documented direct-call behaviour; the monitor never reaches it."""
        comparison = compare_holdings([], normalized([holding("A", 100)]))
        self.assertEqual(comparison.overlap_ratio, Decimal("0"))
        self.assertEqual(comparison.severity, "critical")
        self.assertEqual(comparison.previous_report_date, Q2)


class DiffListingTest(unittest.TestCase):
    def test_added_and_removed_lists_are_capped_and_weight_ranked(self):
        previous = [holding(f"P{index}", index + 1) for index in range(8)]
        current = normalized([holding(f"N{index}", index + 1) for index in range(8)])
        comparison = compare_holdings(previous, current)
        self.assertEqual(len(comparison.added_holdings), MAX_DIFF_HOLDINGS)
        self.assertEqual(len(comparison.removed_holdings), MAX_DIFF_HOLDINGS)
        # Heaviest first, for both sides.
        self.assertTrue(comparison.added_holdings[0].startswith("stock:N7"))
        self.assertTrue(comparison.removed_holdings[0].startswith("stock:P7"))

    def test_unchanged_holdings_are_not_listed(self):
        previous = [holding("A", 60), holding("B", 40)]
        comparison = compare_holdings(previous, normalized([holding("A", 60), holding("C", 40)]))
        self.assertNotIn(
            "stock:A", " ".join(comparison.added_holdings + comparison.removed_holdings)
        )


class MonitorTest(unittest.TestCase):
    """Persistence: first report is silent, history is immutable, order is adjacent."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quant-style-drift-")
        self.addCleanup(self.temp.cleanup)
        self.monitor = FundStyleDriftMonitor(Path(self.temp.name) / "quant_assistant.duckdb")

    def history_rows(self, report):
        with self.monitor.database.connection() as conn:
            return conn.execute(
                "SELECT holding_symbol, weight FROM fund_holding_history "
                "WHERE symbol = '000001' AND report_date = ? ORDER BY holding_symbol",
                [report],
            ).fetchall()

    def test_the_first_report_emits_no_event(self):
        """Otherwise every fund would look critically drifted on first sight."""
        self.assertEqual(self.monitor.record_snapshot(snapshot([holding("A", 60)])), ())

    def test_the_second_report_compares_with_the_first(self):
        self.monitor.record_snapshot(snapshot([holding("A", 60), holding("B", 40)], Q1))
        events = self.monitor.record_snapshot(snapshot([holding("A", 60), holding("C", 40)], Q2))
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.previous_report_date, Q1)
        self.assertEqual(event.report_date, Q2)
        self.assertEqual(event.severity, "critical")
        self.assertTrue(event.drift_detected)
        self.assertIn("风格漂移", event.message)
        self.assertEqual(event.details["previous_report_date"], Q1.isoformat())

    def test_re_recording_a_period_is_a_no_op(self):
        self.monitor.record_snapshot(snapshot([holding("A", 60), holding("B", 40)], Q1))
        self.monitor.record_snapshot(snapshot([holding("A", 60), holding("C", 40)], Q2))
        before = self.history_rows(Q2)
        # A later, different disclosure for the same period must not overwrite it.
        self.assertEqual(
            self.monitor.record_snapshot(snapshot([holding("Z", 90)], Q2)),
            (),
        )
        self.assertEqual(self.history_rows(Q2), before)

    def test_only_the_immediately_preceding_report_is_compared(self):
        self.monitor.record_snapshot(snapshot([holding("A", 60), holding("B", 40)], Q1))
        self.monitor.record_snapshot(snapshot([holding("A", 60), holding("C", 40)], Q2))
        events = self.monitor.record_snapshot(snapshot([holding("A", 60), holding("C", 40)], Q3))
        self.assertEqual(len(events), 1)
        # Comparing Q3 with Q2 (identical) must report no drift, not with Q1.
        self.assertEqual(events[0].previous_report_date, Q2)
        self.assertEqual(events[0].overlap_ratio, Decimal("1"))
        self.assertFalse(events[0].drift_detected)

    def test_a_stale_detail_never_reaches_storage(self):
        with self.assertRaises(ValueError):
            self.monitor.record_snapshot(
                FundHoldingsSnapshot(
                    symbol="000001",
                    report_date=Q2,
                    source="eastmoney",
                    observed_at=datetime(2026, 7, 1, 10, 0),
                    holdings=(holding("A", 10),),
                )
            )
        self.assertEqual(self.history_rows(Q2), [])


class MonitorConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quant-style-drift-cfg-")
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "quant_assistant.duckdb"

    def test_thresholds_must_be_ordered(self):
        cases = (
            {
                "critical_overlap_threshold": Decimal("0.9"),
                "warning_overlap_threshold": Decimal("0.7"),
            },
            {
                "warning_overlap_threshold": Decimal("0.9"),
                "info_overlap_threshold": Decimal("0.85"),
            },
        )
        for overrides in cases:
            with self.assertRaisesRegex(ValueError, "critical < warning < info"):
                FundStyleDriftMonitor(self.database, **overrides)

    def test_a_threshold_must_be_a_positive_ratio(self):
        for bad in (Decimal("0"), Decimal("1.5")):
            with self.assertRaises(ValueError):
                FundStyleDriftMonitor(self.database, info_overlap_threshold=bad)

    def test_ready_requires_at_least_one_valid_item(self):
        self.assertFalse(FundStyleSyncResult(Q2, ()).ready)
        blocked = FundStyleSyncItem("000001", DataStatus.BLOCKED, error="boom")
        self.assertFalse(FundStyleSyncResult(Q2, (blocked,)).ready)
        valid = FundStyleSyncItem("000001", DataStatus.VALID, event_count=1)
        self.assertTrue(FundStyleSyncResult(Q2, (valid,)).ready)


if __name__ == "__main__":
    unittest.main()
