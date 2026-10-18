"""Acceptance for transparent multi-factor public-fund selection.

``strategies/fund_factors.py`` is the scored fund universe behind the fund
screening feature and had no acceptance coverage. Its defining promise is
fail-closed: missing manager, size, liquidity or benchmark evidence must surface
as ``NaN`` plus a blocker, and must **never** be substituted by a population
average. A single unavailable factor has to block eligibility, not quietly
shrink the average.

Everything here is pure in-memory maths on frames. No database, network or
credentials are involved.
"""

import sys
import unittest
import warnings
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

import pandas as pd  # noqa: E402

from quant_assistant.strategies.fund_factors import (  # noqa: E402
    FundFactorConfig,
    annualized_return,
    calmar_ratio,
    information_ratio,
    manager_factor_scores,
    max_drawdown,
    max_drawdown_recovery_days,
    score_fund_universe,
    sortino_ratio,
)

FACTOR_COLUMNS = [
    "return_score",
    "calmar_score",
    "sortino_score",
    "ir_score",
    "drawdown_score",
    "manager_factor",
    "size_factor",
    "liquidity_factor",
]


def nav_frame(values, symbol="F1", start="2026-01-01"):
    days = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({"symbol": [symbol] * len(values), "nav_date": days, "unit_nav": values})


def benchmark_frame(values, symbol="B", start="2026-01-01"):
    return nav_frame(values, symbol=symbol, start=start)


def complete_metadata(**overrides):
    record = {
        "symbol": "F1",
        "fund_size": 1e9,
        "avg_daily_volume": 1e6,
        "manager_tenure_years": 5,
        "manager_win_rate": 60,
        "alpha_stability": 0.5,
        "max_drawdown_recovery_days": 30,
    }
    record.update(overrides)
    return pd.DataFrame([record])


RISING = [1.0, 1.05, 1.10, 1.15]
BENCH = [1.0, 1.01, 1.02, 1.03]
CFG = FundFactorConfig(minimum_observations=2)


class ConfigTest(unittest.TestCase):
    def test_defaults_are_usable(self):
        config = FundFactorConfig()
        self.assertEqual(config.minimum_observations, 60)
        self.assertGreater(config.large_size_threshold, config.minimum_size)

    def test_invalid_values_are_rejected(self):
        cases = (
            {"minimum_observations": 1},
            {"minimum_observations": 2.0},
            {"minimum_size": -1.0},
            {"large_size_threshold": 0.0},
            {"large_size_threshold": 1.0},  # below minimum_size
            {"large_size_penalty": -1.0},
            {"large_size_penalty": 101.0},
            {"annualization_days": 0},
            {"annualization_days": 252.0},
        )
        for overrides in cases:
            with self.assertRaises(ValueError, msg=overrides):
                FundFactorConfig(**overrides)


class NavValidationTest(unittest.TestCase):
    """Bad NAV input must raise, not be silently coerced into a score."""

    def test_missing_columns_are_named(self):
        for dropped, message in (("nav_date", "date"), ("unit_nav", "nav"), ("symbol", "symbol")):
            frame = nav_frame(RISING).drop(columns=[dropped])
            with self.assertRaisesRegex(ValueError, f"nav_history missing {message}"):
                score_fund_universe(frame, config=CFG)

    def test_invalid_dates_are_rejected(self):
        frame = nav_frame(RISING)
        frame["nav_date"] = ["x", "y", "z", "w"]
        with warnings.catch_warnings():
            # pandas warns while failing to infer the deliberately broken dates.
            warnings.simplefilter("ignore", UserWarning)
            with self.assertRaisesRegex(ValueError, "invalid dates"):
                score_fund_universe(frame, config=CFG)

    def test_non_positive_nav_is_rejected(self):
        for bad in ([-1.0], [0.0]):
            frame = nav_frame([1.0, 1.1, 1.2, *bad])
            with self.assertRaisesRegex(ValueError, "finite positive NAV"):
                score_fund_universe(frame, config=CFG)

    def test_duplicate_symbol_date_is_rejected(self):
        frame = nav_frame(RISING)
        frame.loc[1, "nav_date"] = frame.loc[0, "nav_date"]
        with self.assertRaisesRegex(ValueError, "duplicate symbol/date"):
            score_fund_universe(frame, config=CFG)

    def test_non_frame_input_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "pandas DataFrame"):
            score_fund_universe([1.0, 2.0], config=CFG)

    def test_empty_universe_returns_the_documented_empty_shape(self):
        empty = nav_frame([]).astype({"nav_date": "datetime64[ns]"})
        result = score_fund_universe(empty, config=CFG)
        self.assertEqual(list(result.columns), ["symbol", "eligible", "blockers", "factor_score"])
        self.assertTrue(result.empty)

    def test_benchmark_must_be_a_single_series(self):
        two = pd.concat(
            [benchmark_frame(BENCH, symbol="B1"), benchmark_frame(BENCH, symbol="B2")],
            ignore_index=True,
        )
        with self.assertRaisesRegex(ValueError, "exactly one benchmark"):
            score_fund_universe(nav_frame(RISING), two, config=CFG)


class FailClosedTest(unittest.TestCase):
    """The core promise: absent evidence blocks, it is never averaged in."""

    def test_nav_only_fund_is_blocked_and_unscored(self):
        result = score_fund_universe(nav_frame(RISING), config=CFG)
        row = result.iloc[0]
        self.assertFalse(row["eligible"])
        self.assertEqual(row["status"], "blocked")
        self.assertTrue(pd.isna(row["factor_score"]))
        for blocker in ("基准收益历史缺失，无法计算IR", "基金规模证据缺失", "流动性证据缺失"):
            self.assertIn(blocker, row["blockers"])

    def test_one_missing_factor_blocks_the_whole_score(self):
        """With 7 of 8 factors present the score must be NaN, not the mean of 7.

        An 87.5 here would mean missing evidence was quietly averaged away.
        """
        metadata = complete_metadata().drop(columns=["fund_size"])
        result = score_fund_universe(
            nav_frame(RISING), benchmark_frame(BENCH), metadata, config=CFG
        )
        row = result.iloc[0]
        present = row[FACTOR_COLUMNS].dropna()
        self.assertEqual(
            len(present), len(FACTOR_COLUMNS) - 1, "expected exactly one missing factor"
        )
        self.assertTrue(pd.isna(row["size_factor"]))
        self.assertTrue(
            pd.isna(row["factor_score"]),
            f"factor_score must stay NaN, got {row['factor_score']!r}",
        )
        self.assertFalse(row["eligible"])
        self.assertIn("基金规模证据缺失", row["blockers"])

    def test_a_complete_fund_is_eligible(self):
        result = score_fund_universe(
            nav_frame(RISING), benchmark_frame(BENCH), complete_metadata(), config=CFG
        )
        row = result.iloc[0]
        self.assertTrue(row["eligible"])
        self.assertEqual(row["status"], "eligible")
        self.assertEqual(row["blockers"], ())
        self.assertFalse(pd.isna(row["factor_score"]))

    def test_missing_evidence_is_not_borrowed_from_a_complete_neighbour(self):
        """A scored peer must not leak its values into an unscored fund."""
        nav = pd.concat([nav_frame(RISING, symbol="OK"), nav_frame(RISING, symbol="NAVONLY")])
        metadata = complete_metadata().assign(symbol="OK")
        result = score_fund_universe(nav, benchmark_frame(BENCH), metadata, config=CFG)
        by_symbol = result.set_index("symbol")
        self.assertTrue(by_symbol.loc["OK", "eligible"])
        self.assertFalse(by_symbol.loc["NAVONLY", "eligible"])
        self.assertTrue(pd.isna(by_symbol.loc["NAVONLY", "factor_score"]))
        self.assertTrue(pd.isna(by_symbol.loc["NAVONLY", "size_factor"]))
        self.assertNotEqual(
            by_symbol.loc["OK", "factor_score"], by_symbol.loc["NAVONLY", "factor_score"]
        )

    def test_advisory_blocker_still_disqualifies_a_fully_scored_fund(self):
        """A blocker must block even when every factor computes.

        A small fund has a finite size, so all eight factors are available and
        ``factor_score`` is a real number. Eligibility must still be withheld:
        it is the blocker, not a missing value, that disqualifies it. Deriving
        ``eligible`` from ``factor_score.notna()`` alone would leak this fund
        into the ranked list.
        """
        result = score_fund_universe(
            nav_frame(RISING),
            benchmark_frame(BENCH),
            complete_metadata(fund_size=1e6),
            config=CFG,
        )
        row = result.iloc[0]
        self.assertTrue(row[FACTOR_COLUMNS].notna().all(), "expected all factors computable")
        self.assertGreaterEqual(row["factor_score"], 0.0)
        self.assertTrue(row["blockers"], "expected an advisory blocker")
        self.assertFalse(row["eligible"], "a blocker must block despite a valid score")
        self.assertEqual(row["status"], "blocked")

    def test_blockers_are_deduplicated_and_ordered(self):
        result = score_fund_universe(nav_frame(RISING), config=CFG)
        blockers = result.iloc[0]["blockers"]
        self.assertEqual(len(blockers), len(set(blockers)), "blockers must be deduplicated")


class PointInTimeTest(unittest.TestCase):
    """Scores must reflect only what was knowable on ``as_of_date``."""

    def test_as_of_date_truncates_the_nav_history(self):
        frame = nav_frame([1.0, 1.1, 1.2, 1.3])
        full = score_fund_universe(frame, config=CFG)
        # Only one return remains on 2026-01-02, below the 2-observation floor.
        truncated = score_fund_universe(frame, as_of_date=date(2026, 1, 2), config=CFG)
        self.assertNotIn("净值历史不足2个观测", full.iloc[0]["blockers"])
        self.assertIn("净值历史不足2个观测", truncated.iloc[0]["blockers"])

    def test_future_nav_rows_are_excluded(self):
        """A crash dated after ``as_of_date`` must not appear in the score."""
        frame = nav_frame([1.0, 1.1, 1.2, 1.3, 0.5])  # the crash lands on 2026-01-07
        survivor = score_fund_universe(frame, as_of_date=date(2026, 1, 6), config=CFG)
        everything = score_fund_universe(frame, config=CFG)
        self.assertEqual(survivor.iloc[0]["max_drawdown"], 0.0)
        self.assertLess(everything.iloc[0]["max_drawdown"], 0.0)

    def test_metadata_without_a_pit_column_is_flagged(self):
        metadata = complete_metadata()
        result = score_fund_universe(
            nav_frame(RISING), fund_metadata=metadata, as_of_date=date(2026, 1, 4), config=CFG
        )
        self.assertIn("基金元数据时点缺失，无法验证当时可知", result.iloc[0]["blockers"])

    def test_metadata_dated_after_as_of_is_dropped(self):
        metadata = complete_metadata().assign(as_of_date=pd.Timestamp("2026-06-01"))
        result = score_fund_universe(
            nav_frame(RISING), fund_metadata=metadata, as_of_date=date(2026, 1, 4), config=CFG
        )
        self.assertIn("基金规模证据缺失", result.iloc[0]["blockers"])


class GateTest(unittest.TestCase):
    def test_small_fund_is_blocked_by_the_liquidation_threshold(self):
        metadata = complete_metadata(fund_size=1e6)
        result = score_fund_universe(
            nav_frame(RISING), benchmark_frame(BENCH), metadata, config=CFG
        )
        row = result.iloc[0]
        self.assertTrue(
            any("清盘风险阈值" in blocker for blocker in row["blockers"]),
            row["blockers"],
        )
        self.assertEqual(row["status"], "blocked")

    def test_negative_liquidity_is_treated_as_missing(self):
        metadata = complete_metadata(avg_daily_volume=-1.0)
        result = score_fund_universe(nav_frame(RISING), fund_metadata=metadata, config=CFG)
        self.assertIn("流动性证据缺失", result.iloc[0]["blockers"])
        self.assertTrue(pd.isna(result.iloc[0]["liquidity"]))

    def test_boolean_liquidity_is_treated_as_missing(self):
        metadata = complete_metadata(avg_daily_volume=True)
        result = score_fund_universe(nav_frame(RISING), fund_metadata=metadata, config=CFG)
        self.assertIn("流动性证据缺失", result.iloc[0]["blockers"])

    def test_oversized_fund_is_penalised_and_clipped(self):
        metadata = complete_metadata(fund_size=2e11)
        result = score_fund_universe(
            nav_frame(RISING), benchmark_frame(BENCH), metadata, config=CFG
        )
        row = result.iloc[0]
        self.assertEqual(row["large_fund_penalty"], FundFactorConfig().large_size_penalty)
        self.assertLessEqual(row["factor_score"], 100.0)
        self.assertGreaterEqual(row["factor_score"], 0.0)

    def test_penalty_never_drives_the_score_negative(self):
        config = FundFactorConfig(minimum_observations=2, large_size_penalty=100.0)
        metadata = complete_metadata(fund_size=2e11)
        result = score_fund_universe(
            nav_frame(RISING), benchmark_frame(BENCH), metadata, config=config
        )
        self.assertEqual(result.iloc[0]["factor_score"], 0.0)


class OrderingTest(unittest.TestCase):
    def test_eligible_funds_rank_before_blocked_ones(self):
        nav = pd.concat([nav_frame(RISING, symbol="OK"), nav_frame(RISING, symbol="NAVONLY")])
        result = score_fund_universe(
            nav, benchmark_frame(BENCH), complete_metadata().assign(symbol="OK"), config=CFG
        )
        self.assertEqual(result.iloc[0]["symbol"], "OK")
        self.assertEqual(list(result["eligible"]), [True, False])

    def test_result_is_reset_to_a_clean_index(self):
        nav = pd.concat([nav_frame(RISING, symbol="B"), nav_frame(RISING, symbol="A")])
        result = score_fund_universe(nav, config=CFG)
        self.assertEqual(list(result.index), list(range(len(result))))


class MetricTest(unittest.TestCase):
    def test_annualized_return_compounds_and_keeps_sign(self):
        self.assertAlmostEqual(annualized_return([0.01] * 252, 252), (1.01**252) - 1, places=10)
        self.assertLess(annualized_return([-0.01] * 252, 252), 0)

    def test_annualized_return_refuses_ruinous_and_missing_input(self):
        self.assertTrue(pd.isna(annualized_return([])))
        self.assertTrue(pd.isna(annualized_return([-1.5, 0.1])))
        self.assertTrue(pd.isna(annualized_return([0.01, float("nan")])))

    def test_max_drawdown_counts_the_initial_capital_as_a_peak(self):
        # A loss in the very first period is still a drawdown from the outset.
        self.assertAlmostEqual(max_drawdown([-0.5, 0.0]), -0.5)
        self.assertEqual(max_drawdown([0.1, 0.1]), 0.0)

    def test_max_drawdown_rejects_impossible_returns(self):
        self.assertTrue(pd.isna(max_drawdown([-1.5])))
        self.assertTrue(pd.isna(max_drawdown([float("nan")])))

    def test_calmar_edge_cases_are_explicit(self):
        self.assertEqual(calmar_ratio([0.0] * 10), 0.0)  # no drawdown, no return
        self.assertEqual(calmar_ratio([0.01] * 10), float("inf"))  # no drawdown, positive
        self.assertTrue(pd.isna(calmar_ratio([])))

    def test_sortino_edge_cases_are_explicit(self):
        self.assertEqual(sortino_ratio([0.0] * 10), 0.0)
        self.assertEqual(sortino_ratio([0.01] * 10), float("inf"))
        self.assertTrue(pd.isna(sortino_ratio([])))

    def test_sortino_only_penalises_downside(self):
        mixed = sortino_ratio([0.02, -0.01, 0.03, -0.005])
        upside_only = sortino_ratio([0.02, 0.01, 0.03, 0.005])
        self.assertGreater(upside_only, mixed)

    def test_information_ratio_requires_matched_lengths_for_plain_sequences(self):
        self.assertTrue(pd.isna(information_ratio([0.01, 0.02], [0.01])))

    def test_information_ratio_is_zero_when_active_return_is_flat(self):
        self.assertEqual(information_ratio([0.01] * 5, [0.01] * 5), 0.0)

    def test_information_ratio_sign_follows_outperformance(self):
        better = information_ratio([0.02] * 6, [0.01] * 6)
        worse = information_ratio([0.01] * 6, [0.02] * 6)
        self.assertGreater(better, 0)
        self.assertLess(worse, 0)

    def test_recovery_days_counts_an_ongoing_drawdown(self):
        self.assertEqual(max_drawdown_recovery_days([-0.1, -0.1, 0.5]), 2.0)
        # Never recovers: the run counts to the final observation.
        self.assertEqual(max_drawdown_recovery_days([-0.1, -0.1, -0.1]), 3.0)
        self.assertEqual(max_drawdown_recovery_days([0.1, 0.1]), 0.0)


class ManagerScoreTest(unittest.TestCase):
    def test_percent_win_rate_is_normalised_to_a_ratio(self):
        frame = pd.DataFrame(
            {"symbol": ["A", "B"], "manager_win_rate": [60, 0.8], "manager_tenure_years": [5, 3]}
        )
        result = manager_factor_scores(frame).set_index("symbol")
        self.assertAlmostEqual(result.loc["A", "win_rate"], 0.6)
        self.assertAlmostEqual(result.loc["B", "win_rate"], 0.8)

    def test_unavailable_manager_evidence_becomes_a_blocker(self):
        frame = pd.DataFrame(
            {"symbol": ["A"], "manager_win_rate": [True], "manager_tenure_years": [5]}
        )
        row = manager_factor_scores(frame).iloc[0]
        self.assertIn("经理win_rate证据缺失", row["manager_blockers"])
        self.assertIn("经理alpha_stability证据缺失", row["manager_blockers"])
        self.assertTrue(pd.isna(row["win_rate"]))

    def test_documented_aliases_are_accepted(self):
        frame = pd.DataFrame({"fund_symbol": ["A"], "经理任职年限": [7], "经理胜率": [0.9]})
        result = manager_factor_scores(frame)
        self.assertEqual(result.iloc[0]["symbol"], "A")
        self.assertAlmostEqual(result.iloc[0]["tenure"], 7)
        self.assertAlmostEqual(result.iloc[0]["win_rate"], 0.9)

    def test_recovery_days_ranks_lower_as_better(self):
        frame = pd.DataFrame(
            {
                "symbol": ["fast", "slow"],
                "max_drawdown_recovery_days": [10, 100],
                "manager_tenure_years": [5, 5],
            }
        )
        result = manager_factor_scores(frame).set_index("symbol")
        self.assertGreater(
            result.loc["fast", "manager_recovery_days_score"],
            result.loc["slow", "manager_recovery_days_score"],
        )

    def test_tenure_ranks_higher_as_better(self):
        frame = pd.DataFrame({"symbol": ["long", "short"], "manager_tenure_years": [10, 1]})
        result = manager_factor_scores(frame).set_index("symbol")
        self.assertGreater(
            result.loc["long", "manager_tenure_score"], result.loc["short", "manager_tenure_score"]
        )

    def test_empty_metadata_yields_the_documented_empty_shape(self):
        result = manager_factor_scores(pd.DataFrame())
        self.assertEqual(list(result.columns), ["symbol", "manager_score", "manager_blockers"])

    def test_non_frame_input_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "pandas DataFrame"):
            manager_factor_scores({"symbol": "A"})


if __name__ == "__main__":
    unittest.main()
