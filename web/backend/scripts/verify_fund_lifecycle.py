"""Acceptance for fund lifecycle evidence and change detection.

``services/fund_lifecycle.py`` records public fund-profile observations and
emits explainable lifecycle events. It had no acceptance coverage, and its
contract is deliberately conservative:

* a profile is validated and canonicalised before it reaches DuckDB;
* the first observation for a fund emits **no** events (there is nothing to
  compare against);
* each observation is compared with the *immediately preceding* one only, and
  re-recording a date is a no-op so history stays immutable;
* a change only fires when the previous side actually carried the evidence, so
  a first-time disclosure never looks like a change;
* a scale threshold is a review heuristic, never a liquidation conclusion.

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
from quant_assistant.providers.fund_detail import FundFee  # noqa: E402
from quant_assistant.services.fund_lifecycle import (  # noqa: E402
    FundLifecycleMonitor,
    FundLifecycleSnapshot,
    FundLifecycleSyncItem,
    FundLifecycleSyncResult,
    detect_lifecycle_events,
    normalize_snapshot,
    parse_scale,
)

OBSERVED = datetime(2026, 7, 1, 10, 0, tzinfo=SHANGHAI_TZ)
Q1 = date(2026, 3, 31)
Q2 = date(2026, 6, 30)
Q3 = date(2026, 9, 30)


def snapshot(as_of=Q2, symbol="000001", **overrides):
    values = {"source": "eastmoney", "observed_at": OBSERVED}
    values.update(overrides)
    return FundLifecycleSnapshot(symbol=symbol, as_of_date=as_of, **values)


def events(previous, current):
    detected = detect_lifecycle_events(snapshot(Q1, **previous), snapshot(Q2, **current))
    return [(item["event_type"], item["severity"]) for item in detected]


def fee(rate, fee_type="申购费", condition="小于100万"):
    return FundFee(fee_type, condition, None if rate is None else Decimal(str(rate)))


class ParseScaleTest(unittest.TestCase):
    def test_chinese_magnitudes_are_expanded(self):
        cases = (
            ("50亿元", Decimal("5000000000"), "元"),
            ("1,234.56万份", Decimal("12345600.00"), "份"),
            ("2兆元", Decimal("2000000000000"), "元"),
            ("3千元", Decimal("3000"), "元"),
        )
        for text, value, unit in cases:
            self.assertEqual(parse_scale(text), (value, unit), text)

    def test_a_unitless_number_is_still_parsed(self):
        self.assertEqual(parse_scale("12345"), (Decimal("12345"), None))

    def test_unusable_text_yields_nothing_rather_than_a_guess(self):
        for text in ("", "   ", "暂无数据", None):
            self.assertEqual(parse_scale(text), (None, None), repr(text))

    def test_non_positive_scales_are_refused(self):
        for text in ("0", "0亿元", "-5亿"):
            self.assertEqual(parse_scale(text), (None, None), text)


class NormalizeSnapshotTest(unittest.TestCase):
    """Only a validated snapshot may reach storage."""

    def test_the_snapshot_type_is_checked(self):
        with self.assertRaisesRegex(TypeError, "FundLifecycleSnapshot"):
            normalize_snapshot({"symbol": "000001"})

    def test_a_datetime_as_of_date_is_rejected(self):
        """A datetime would be compared against a DATE column and blow up later.

        Left unguarded, the second observation raises an opaque
        "can't compare datetime.datetime to datetime.date" *after* the
        surrounding transaction has already been rolled back.
        """
        for bad in (datetime(2026, 6, 30, 15, 0, tzinfo=SHANGHAI_TZ), 20260630):
            with self.assertRaisesRegex(TypeError, "as_of_date must be a date"):
                normalize_snapshot(snapshot(as_of=bad))

    def test_a_naive_observed_at_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            normalize_snapshot(snapshot(observed_at=datetime(2026, 7, 1, 10, 0)))

    def test_observed_at_is_converted_to_shanghai_time(self):
        result = normalize_snapshot(snapshot())
        self.assertEqual(result.observed_at.utcoffset(), timedelta(hours=8))

    def test_a_blank_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "source must be non-empty"):
            normalize_snapshot(snapshot(source="   "))

    def test_manager_names_are_deduplicated_sorted_and_filtered(self):
        result = normalize_snapshot(snapshot(manager_names=("李四", "张三", "李四", "  ", "")))
        self.assertEqual(result.manager_names, ("张三", "李四"))

    def test_scale_value_is_derived_from_the_display_text(self):
        result = normalize_snapshot(snapshot(scale_text="50亿元"))
        self.assertEqual(result.scale_value, Decimal("5000000000"))
        self.assertEqual(result.scale_unit, "元")

    def test_an_explicit_scale_value_wins_over_the_parsed_one(self):
        result = normalize_snapshot(
            snapshot(scale_text="50亿元", scale_value=Decimal("7"), scale_unit="份")
        )
        self.assertEqual(result.scale_value, Decimal("7"))
        self.assertEqual(result.scale_unit, "份")

    def test_a_non_positive_scale_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be positive"):
            normalize_snapshot(snapshot(scale_value=Decimal("0")))

    def test_blank_optional_statuses_become_none(self):
        result = normalize_snapshot(snapshot(purchase_status="  ", redemption_status=""))
        self.assertIsNone(result.purchase_status)
        self.assertIsNone(result.redemption_status)

    def test_source_snapshot_id_is_optional_but_typed(self):
        self.assertIsNone(normalize_snapshot(snapshot()).source_snapshot_id)
        self.assertEqual(normalize_snapshot(snapshot(source_snapshot_id=0)).source_snapshot_id, 0)
        for bad, error in ((True, TypeError), (-1, ValueError), (1.5, TypeError)):
            with self.assertRaises(error):
                normalize_snapshot(snapshot(source_snapshot_id=bad))

    def test_fee_values_must_be_fund_fee_objects(self):
        with self.assertRaisesRegex(TypeError, "FundFee"):
            normalize_snapshot(snapshot(fees=({"fee_type": "x"},)))


class ManagerChangeTest(unittest.TestCase):
    def test_a_changed_roster_is_reported(self):
        self.assertEqual(
            events({"manager_names": ("张三",)}, {"manager_names": ("李四",)}),
            [("manager_change", "warning")],
        )

    def test_a_first_time_roster_is_not_a_change(self):
        """Without a previous roster there is nothing to have changed from."""
        self.assertEqual(events({}, {"manager_names": ("李四",)}), [])

    def test_an_unchanged_roster_is_silent(self):
        self.assertEqual(
            events({"manager_names": ("张三", "李四")}, {"manager_names": ("李四", "张三")}), []
        )


class ScaleDropTest(unittest.TestCase):
    def test_thresholds_are_separated_from_the_background_ratio(self):
        cases = (
            ("100亿元", "90亿元", []),  # 10% drop is below the 20% warning ratio
            ("100亿元", "75亿元", [("scale_drop", "warning")]),
            ("100亿元", "70亿元", [("scale_drop", "warning")]),  # exactly 30%
            ("100亿元", "40亿元", [("scale_drop", "critical")]),  # 60%
        )
        for before, after, expected in cases:
            self.assertEqual(
                events({"scale_text": before}, {"scale_text": after}),
                expected,
                f"{before}->{after}",
            )

    def test_the_warning_ratio_boundary_is_inclusive(self):
        # Exactly 20% decline meets the threshold; 19% does not.
        self.assertEqual(
            events({"scale_value": Decimal("100")}, {"scale_value": Decimal("80")}),
            [("scale_drop", "warning")],
        )
        self.assertEqual(
            events({"scale_value": Decimal("100")}, {"scale_value": Decimal("81")}), []
        )

    def test_a_drop_needs_both_scales_to_be_known(self):
        self.assertEqual(events({}, {"scale_text": "40亿元"}), [])

    def test_a_rise_is_not_a_drop(self):
        self.assertEqual(events({"scale_text": "50亿元"}, {"scale_text": "100亿元"}), [])


class LiquidationRiskTest(unittest.TestCase):
    def test_crossing_below_the_threshold_is_flagged_as_a_review(self):
        detected = detect_lifecycle_events(
            snapshot(Q1, scale_text="60亿元"), snapshot(Q2, scale_text="3000万元")
        )
        kinds = [(item["event_type"], item["severity"]) for item in detected]
        self.assertIn(("liquidation_risk", "critical"), kinds)
        risk = next(item for item in detected if item["event_type"] == "liquidation_risk")
        self.assertIn("不能据此认定清盘", risk["message"])
        self.assertEqual(risk["details"]["heuristic"], "absolute_scale_review_only")

    def test_an_already_small_fund_is_not_re_alerted(self):
        """Re-alerting every period would be noise, not evidence."""
        detected = detect_lifecycle_events(
            snapshot(Q1, scale_text="3000万元"), snapshot(Q2, scale_text="2000万元")
        )
        kinds = [item["event_type"] for item in detected]
        self.assertNotIn("liquidation_risk", kinds)

    def test_a_scale_without_a_recognised_unit_is_not_flagged(self):
        # A bare number could be a ratio or a count; it cannot be compared to a
        # yuan threshold, so the monitor stays silent.
        detected = detect_lifecycle_events(
            snapshot(Q1, scale_text="60亿元"),
            snapshot(Q2, scale_value=Decimal("1000000"), scale_unit=None),
        )
        self.assertNotIn("liquidation_risk", [item["event_type"] for item in detected])


class StatusSuspensionTest(unittest.TestCase):
    def test_a_new_purchase_restriction_is_reported_for_both_legs(self):
        self.assertEqual(
            events({}, {"purchase_status": "暂停申购"}), [("purchase_suspension", "warning")]
        )
        self.assertEqual(
            events({}, {"redemption_status": "暂停赎回"}),
            [("redemption_suspension", "warning")],
        )

    def test_an_unrestricted_status_change_is_not_reported(self):
        self.assertEqual(events({"purchase_status": "开放申购"}, {"purchase_status": "正常"}), [])

    def test_an_unchanged_restriction_is_not_reported_twice(self):
        self.assertEqual(
            events({"purchase_status": "暂停申购"}, {"purchase_status": "暂停申购"}), []
        )

    def test_a_newly_cleared_restriction_is_not_reported(self):
        """Returning to tradeable is not itself a risk event."""
        self.assertEqual(
            events({"purchase_status": "暂停申购"}, {"purchase_status": "开放申购"}), []
        )


class FeeChangeTest(unittest.TestCase):
    def test_a_changed_fee_schedule_is_reported(self):
        self.assertEqual(
            events({"fees": (fee("0.5"),)}, {"fees": (fee("0.8"),)}),
            [("fee_change", "warning")],
        )

    def test_an_unchanged_schedule_is_silent(self):
        self.assertEqual(events({"fees": (fee("0.5"),)}, {"fees": (fee("0.5"),)}), [])

    def test_a_first_time_fee_disclosure_is_not_a_change(self):
        self.assertEqual(events({}, {"fees": (fee("0.8"),)}), [])


class EventCompositionTest(unittest.TestCase):
    def test_independent_changes_are_all_reported(self):
        detected = events(
            {"manager_names": ("张三",), "scale_text": "100亿元", "purchase_status": "开放"},
            {"manager_names": ("李四",), "scale_text": "40亿元", "purchase_status": "暂停申购"},
        )
        self.assertEqual(
            detected,
            [
                ("manager_change", "warning"),
                ("scale_drop", "critical"),
                ("purchase_suspension", "warning"),
            ],
        )

    def test_events_are_reported_in_a_stable_order(self):
        first = events(
            {"manager_names": ("A",), "scale_text": "100亿元"},
            {"manager_names": ("B",), "scale_text": "40亿元"},
        )
        second = events(
            {"manager_names": ("A",), "scale_text": "100亿元"},
            {"manager_names": ("B",), "scale_text": "40亿元"},
        )
        self.assertEqual(first, second)

    def test_an_unchanged_profile_produces_nothing(self):
        self.assertEqual(events({"scale_text": "50亿元"}, {"scale_text": "50亿元"}), [])


class DetectionGuardTest(unittest.TestCase):
    def test_snapshots_must_share_a_symbol(self):
        with self.assertRaisesRegex(ValueError, "same fund symbol"):
            detect_lifecycle_events(snapshot(Q1), snapshot(Q2, symbol="000002"))

    def test_the_current_observation_must_be_strictly_later(self):
        for as_of in (Q1, date(2026, 3, 30)):
            with self.assertRaisesRegex(ValueError, "must be later than previous"):
                detect_lifecycle_events(snapshot(Q1), snapshot(as_of))

    def test_the_drop_ratio_must_be_a_proportion(self):
        for bad in (Decimal("0"), Decimal("1"), Decimal("1.5")):
            with self.assertRaises(ValueError):
                detect_lifecycle_events(snapshot(Q1), snapshot(Q2), scale_drop_ratio=bad)

    def test_the_liquidation_threshold_must_be_positive(self):
        for bad in (Decimal("0"), Decimal("-1")):
            with self.assertRaises(ValueError):
                detect_lifecycle_events(snapshot(Q1), snapshot(Q2), liquidation_threshold=bad)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quant-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.monitor = FundLifecycleMonitor(Path(self.temp.name) / "quant_assistant.duckdb")

    def stored_dates(self):
        with self.monitor.database.connection() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT as_of_date FROM fund_profile_history ORDER BY as_of_date"
                ).fetchall()
            ]

    def test_the_first_observation_emits_no_event(self):
        self.assertEqual(self.monitor.record_snapshot(snapshot(Q1, scale_text="100亿元")), ())

    def test_the_second_observation_compares_with_its_predecessor(self):
        self.monitor.record_snapshot(snapshot(Q1, scale_text="100亿元"))
        detected = self.monitor.record_snapshot(snapshot(Q2, scale_text="40亿元"))
        self.assertEqual([item.event_type for item in detected], ["scale_drop"])
        self.assertEqual(detected[0].severity, "critical")
        self.assertEqual(detected[0].observed_on, Q2)
        self.assertEqual(detected[0].previous_value, "100亿元")
        self.assertEqual(detected[0].current_value, "40亿元")

    def test_only_the_immediately_preceding_observation_is_compared(self):
        self.monitor.record_snapshot(snapshot(Q1, scale_text="100亿元"))
        self.monitor.record_snapshot(snapshot(Q2, scale_text="40亿元"))
        detected = self.monitor.record_snapshot(snapshot(Q3, scale_text="40亿元"))
        # Compared with Q2 (unchanged) this is quiet; compared with Q1 it would not be.
        self.assertEqual(detected, ())

    def test_re_recording_a_date_is_a_no_op(self):
        self.monitor.record_snapshot(snapshot(Q1, scale_text="100亿元"))
        self.monitor.record_snapshot(snapshot(Q2, scale_text="40亿元"))
        before = self.stored_dates()
        self.assertEqual(self.monitor.record_snapshot(snapshot(Q2, scale_text="3000万元")), ())
        self.assertEqual(self.stored_dates(), before)

    def test_an_invalid_snapshot_is_rejected_before_it_is_stored(self):
        for bad in (
            snapshot(datetime(2026, 6, 30, 15, 0, tzinfo=SHANGHAI_TZ)),
            snapshot(Q2, observed_at=datetime(2026, 7, 1, 10, 0)),
        ):
            with self.assertRaises((TypeError, ValueError)):
                self.monitor.record_snapshot(bad)
        self.assertEqual(self.stored_dates(), [])


class MonitorConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quant-lifecycle-cfg-")
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "quant_assistant.duckdb"

    def test_the_drop_ratio_must_be_strictly_inside_zero_and_one(self):
        for bad in (Decimal("0"), Decimal("1"), Decimal("2")):
            with self.assertRaisesRegex(ValueError, "between 0 and 1"):
                FundLifecycleMonitor(self.database, scale_drop_ratio=bad)

    def test_the_liquidation_threshold_must_be_positive(self):
        # Zero reaches the positivity check; a negative value is stopped earlier.
        with self.assertRaisesRegex(ValueError, "must be positive"):
            FundLifecycleMonitor(self.database, liquidation_threshold=Decimal("0"))
        with self.assertRaisesRegex(ValueError, "non-negative"):
            FundLifecycleMonitor(self.database, liquidation_threshold=Decimal("-1"))

    def test_ready_requires_at_least_one_valid_item(self):
        self.assertFalse(FundLifecycleSyncResult(Q2, ()).ready)
        blocked = FundLifecycleSyncItem("000001", DataStatus.BLOCKED, error="boom")
        self.assertFalse(FundLifecycleSyncResult(Q2, (blocked,)).ready)
        valid = FundLifecycleSyncItem("000001", DataStatus.VALID, event_count=2)
        self.assertTrue(FundLifecycleSyncResult(Q2, (valid,)).ready)


if __name__ == "__main__":
    unittest.main()
