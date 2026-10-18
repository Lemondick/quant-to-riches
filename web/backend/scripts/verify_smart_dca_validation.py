"""Acceptance for walk-forward validation of Smart DCA parameters.

``analysis/smart_dca_validation.py`` is the research-only evaluator that picks
Smart DCA parameters. It had no acceptance coverage. The properties that make
it trustworthy:

* **The test window never influences selection.** Candidates are ranked on
  validation (then training) performance; the test window is scored once with
  the already-chosen parameters. A fixture where the ranking flips with the
  validation data pins that order.
* **Doing nothing scores exactly 0.0.** A candidate whose rule always pauses
  therefore beats a rule whose historical mean return is negative — the
  evaluator refuses to prefer losing money.
* Structural problems surface as blockers or hard errors, never as a
  quietly degraded ranking, and every result is stamped ``research_only``.

Pure in-memory frames. No database, network or credentials.
"""

import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

import pandas as pd  # noqa: E402

from quant_assistant.analysis.smart_dca_validation import (  # noqa: E402
    SmartDcaValidationWindow,
    run_smart_dca_walk_forward,
    scan_smart_dca_parameters,
)

START = date(2024, 1, 1)
TRAIN = (date(2024, 1, 1), date(2024, 3, 31))
VALIDATION = (date(2024, 4, 1), date(2024, 6, 30))
TEST = (date(2024, 7, 1), date(2024, 9, 30))
WINDOW = SmartDcaValidationWindow(*TRAIN, *VALIDATION, *TEST)

INVEST_FREELY = {"minimum_investment": Decimal("0.01")}
NEVER_INVEST = {"minimum_investment": Decimal("100000")}


def observations(mean_return="mixed", days=270):
    """Build one observation per day.

    ``mixed`` alternates +1% / -2% (net negative mean), ``positive`` repeats
    +1%, ``negative`` repeats -2%.
    """
    rows = []
    for index in range(days):
        if mean_return == "positive":
            forward = 0.01
        elif mean_return == "negative":
            forward = -0.02
        else:
            forward = 0.01 if index % 2 == 0 else -0.02
        rows.append(
            {
                "as_of_date": START + timedelta(days=index),
                "symbol": "000001",
                "current_price": 1.0 + index * 0.001,
                "ma120": 1.0,
                "ma200": 0.9,
                "pe": 10.0 + (index % 20),
                "pb": 1.0,
                "forward_return": forward,
            }
        )
    return pd.DataFrame(rows)


def run(observations_frame, windows=None, candidates=None):
    return run_smart_dca_walk_forward(
        observations_frame,
        windows if windows is not None else [WINDOW],
        candidates if candidates is not None else [INVEST_FREELY, NEVER_INVEST],
    )


class WindowValidationTest(unittest.TestCase):
    def test_a_chronological_window_is_accepted(self):
        window = SmartDcaValidationWindow(*TRAIN, *VALIDATION, *TEST)
        self.assertEqual(window.train_start, date(2024, 1, 1))

    def test_periods_must_be_ordered_and_disjoint(self):
        cases = (
            (TRAIN[1], TRAIN[0], *VALIDATION, *TEST),  # train start after train end
            (date(2024, 1, 1), date(2024, 4, 1), *VALIDATION, *TEST),  # train touches validation
            (*TRAIN, date(2024, 5, 15), date(2024, 5, 1), *TEST),  # validation window reversed
            (*TRAIN, *VALIDATION, date(2024, 9, 30), date(2024, 7, 1)),  # test window reversed
        )
        for values in cases:
            with self.assertRaisesRegex(ValueError, "chronological and disjoint"):
                SmartDcaValidationWindow(*values)

    def test_a_datetime_window_date_is_rejected(self):
        """A timestamp cannot be ordered against plain dates."""
        with self.assertRaises(TypeError):
            SmartDcaValidationWindow(*TRAIN, *VALIDATION, datetime(2024, 10, 1), TEST[1])

    def test_a_non_date_window_date_is_rejected(self):
        with self.assertRaises(TypeError):
            SmartDcaValidationWindow(*TRAIN, *VALIDATION, 20241001, TEST[1])


class PrepareTest(unittest.TestCase):
    def test_missing_columns_are_named(self):
        frame = pd.DataFrame({"as_of_date": [START], "symbol": ["000001"]})
        with self.assertRaisesRegex(ValueError, "missing columns"):
            run(frame, [WINDOW], [INVEST_FREELY])

    def test_duplicate_date_symbol_rows_are_rejected(self):
        rows = observations()
        duplicated = pd.concat([rows, rows.head(1)], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate date/symbol"):
            run(duplicated)

    def test_invalid_rows_are_dropped(self):
        rows = observations()
        rows["forward_return"] = rows["forward_return"].astype(object)
        rows.loc[0, "current_price"] = None
        rows.loc[1, "forward_return"] = "n/a"
        result = run(rows)
        self.assertEqual(len(result.folds), 1)

    def test_an_empty_frame_still_reports_a_fold_with_blockers(self):
        result = run(pd.DataFrame(columns=list(observations().columns)))
        self.assertTrue(any("验证数据为空" in blocker for blocker in result.blockers))


class WalkForwardTest(unittest.TestCase):
    def test_selection_follows_validation_performance(self):
        """A rule whose validation mean is negative loses to doing nothing."""
        losing = run(observations("mixed"))
        self.assertEqual(losing.folds[0].selected_parameters, NEVER_INVEST)
        self.assertEqual(losing.folds[0].test_investments, 0)

        winning = run(observations("positive"))
        self.assertEqual(winning.folds[0].selected_parameters, INVEST_FREELY)
        self.assertGreater(winning.folds[0].test_investments, 0)

    def test_selection_follows_validation_even_when_training_disagrees(self):
        """The validation window outranks the training window in the tie-break.

        Training data is net negative (so a do-nothing rule wins there) while
        validation data is net positive (so investing wins). Only a
        validation-driven selection can pick the investing rule.
        """
        rows = observations("mixed")  # net negative mean
        in_validation = rows["as_of_date"].between(*VALIDATION)
        rows.loc[in_validation, "forward_return"] = 0.01
        result = run(rows)
        self.assertEqual(result.folds[0].selected_parameters, INVEST_FREELY)

    def test_the_test_window_is_scored_but_never_chooses(self):
        """Identical test data, different validation data -> different choice."""
        losing = run(observations("mixed"))
        winning = run(observations("positive"))
        self.assertNotEqual(
            losing.folds[0].selected_parameters,
            winning.folds[0].selected_parameters,
            "selection must follow the validation window, not the test window",
        )

    def test_every_result_is_marked_research_only(self):
        result = run(observations("positive"))
        self.assertTrue(result.research_only)

    def test_candidate_scores_cover_every_candidate_and_window(self):
        result = run(observations("positive"))
        self.assertEqual(len(result.candidate_scores), 2)
        for column in (
            "train_start",
            "validation_start",
            "parameters",
            "train_mean_return",
            "validation_mean_return",
        ):
            self.assertIn(column, result.candidate_scores.columns)

    def test_multiple_windows_produce_multiple_folds(self):
        later = SmartDcaValidationWindow(
            date(2024, 4, 1),
            date(2024, 6, 30),
            date(2024, 7, 1),
            date(2024, 9, 30),
            date(2024, 10, 1),
            date(2024, 12, 31),
        )
        result = run(observations(days=360), windows=[WINDOW, later])
        self.assertEqual(len(result.folds), 2)

    def test_overlapping_test_windows_are_rejected(self):
        """Each window is internally valid, but the two test ranges overlap."""
        second = SmartDcaValidationWindow(
            date(2024, 7, 1),
            date(2024, 7, 31),
            date(2024, 8, 1),
            date(2024, 8, 31),
            date(2024, 9, 1),
            date(2024, 11, 30),  # overlaps the first test window
        )
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            run(observations(), windows=[WINDOW, second])

    def test_missing_windows_or_candidates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one Smart DCA validation window"):
            run(observations(), windows=[])
        with self.assertRaisesRegex(ValueError, "at least one Smart DCA parameter candidate"):
            run(observations(), candidates=[])

    def test_an_invalid_candidate_becomes_a_blocker_not_a_crash(self):
        result = run(observations(), candidates=[INVEST_FREELY, {"low_valuation_percentile": 5.0}])
        self.assertTrue(any("参数候选无效" in blocker for blocker in result.blockers))
        self.assertEqual(len(result.folds), 1)

    def test_all_invalid_candidates_yield_no_folds(self):
        result = run(observations(), candidates=[{"nope": 1}])
        self.assertEqual(result.folds, ())
        self.assertTrue(result.research_only)
        self.assertTrue(result.blockers)

    def test_identical_input_yields_identical_selections(self):
        first = run(observations("positive"))
        second = run(observations("positive"))
        self.assertEqual(
            [fold.selected_parameters for fold in first.folds],
            [fold.selected_parameters for fold in second.folds],
        )
        self.assertEqual(
            [fold.test_mean_return for fold in first.folds],
            [fold.test_mean_return for fold in second.folds],
        )


class SensitivityScanTest(unittest.TestCase):
    def test_the_best_candidate_maximises_the_realized_mean(self):
        result = scan_smart_dca_parameters(
            observations("positive"),
            [INVEST_FREELY, NEVER_INVEST],
            start=date(2024, 1, 1),
            end=date(2024, 9, 30),
        )
        self.assertEqual(result.best_parameters, INVEST_FREELY)

    def test_doing_nothing_scores_exactly_zero(self):
        result = scan_smart_dca_parameters(
            observations("positive"),
            [NEVER_INVEST],
            start=date(2024, 1, 1),
            end=date(2024, 9, 30),
        )
        self.assertEqual(result.results["score"].tolist(), [0.0])

    def test_a_failing_candidate_withholds_the_best_choice(self):
        """A partial scan is not evidence for a production parameter."""
        result = scan_smart_dca_parameters(
            observations("positive"),
            [INVEST_FREELY, {"low_valuation_percentile": 5.0}],
            start=date(2024, 1, 1),
            end=date(2024, 9, 30),
        )
        self.assertIsNone(result.best_parameters)
        self.assertTrue(len(result.blockers) > 0)

    def test_reversed_dates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "start must not exceed end"):
            scan_smart_dca_parameters(
                observations(), [INVEST_FREELY], start=date(2024, 9, 30), end=date(2024, 1, 1)
            )

    def test_a_datetime_boundary_is_rejected(self):
        for bad in (datetime(2024, 1, 1, tzinfo=None), 20240101):
            with self.assertRaises(TypeError):
                scan_smart_dca_parameters(
                    observations(), [INVEST_FREELY], start=bad, end=date(2024, 9, 30)
                )


if __name__ == "__main__":
    unittest.main()
