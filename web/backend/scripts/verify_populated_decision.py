"""Read populated decision facts from disposable storage, including blocked runs."""

import unittest
from pathlib import Path

from quant_assistant.models.entities import Action
from quant_assistant.models.repositories.database import Database
from quant_assistant.reporting.queries import DecisionQueries


class PopulatedDecisionTest(unittest.TestCase):
    def setUp(self):
        self.database = Database(Path(":memory:"))
        self.database.initialize()
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO strategy_runs(run_id,as_of_date,generated_at,strategy_version,"
                "data_status,readiness,status) VALUES"
                "('synthetic','2026-09-30',current_timestamp,'synthetic-v1','valid','paper','completed')"
            )
            for symbol, action in (("600000.SH", "hold"), ("600001.SH", "buy")):
                connection.execute(
                    "INSERT INTO signals(run_id,symbol,asset_type,action,score,target_weight,"
                    "reasons_json,as_of_date,valid_until,strategy_version) VALUES"
                    "('synthetic',?,'stock',?,50,0.1,'[\"合成验收证据\"]','2026-09-30',"
                    "current_timestamp,'synthetic-v1')",
                    [symbol, action],
                )
                connection.execute(
                    "INSERT INTO proposed_orders(run_id,symbol,asset_type,action,amount,quantity,"
                    "target_weight,reasons_json,as_of_date,valid_until,data_snapshot_id,strategy_version)"
                    "VALUES('synthetic',?,'stock',?,100,10,0.1,'[\"合成验收证据\"]','2026-09-30',"
                    "current_timestamp,1,'synthetic-v1')",
                    [symbol, action],
                )

    def tearDown(self):
        self.database.close()

    def test_exact_and_latest_reads_reconstruct_nonempty_signals_and_orders(self):
        queries = DecisionQueries(self.database)
        for result in (queries.decision_by_id("synthetic"), queries.latest_decision()):
            self.assertEqual(len(result.signals), 2)
            self.assertEqual(len(result.orders), 2)
            self.assertEqual(result.signals[0].reasons, ("合成验收证据",))
        self.assertIsNone(queries.decision_by_id("unrelated"))

    def test_blocked_run_still_removes_risk_increasing_facts(self):
        with self.database.connection() as connection:
            connection.execute("UPDATE strategy_runs SET data_status='blocked'")
        result = DecisionQueries(self.database).decision_by_id("synthetic")
        self.assertEqual([signal.action for signal in result.signals], [Action.HOLD])
        self.assertEqual([order.action for order in result.orders], [Action.HOLD])


if __name__ == "__main__":
    unittest.main()
