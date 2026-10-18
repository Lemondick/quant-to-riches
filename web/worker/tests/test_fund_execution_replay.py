"""Exact execution replay and defensive evidence boundaries."""

import copy
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.reporting.fund_execution_study import KINDS, VERSION, replay


def bundle():
    calendar = [
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
        "2026-01-12",
        "2026-01-13",
    ]
    dates = ["2026-01-01", *calendar]
    return {
        "version": VERSION,
        "symbol": "000001",
        "capital": "1000",
        "calendar": calendar,
        "corporate_actions_review": "no_actions",
        "evidence": [
            {
                "kind": kind,
                "confirmed": True,
                "reference": "synthetic evidence",
                "known_at": "2026-01-01T08:00:00+08:00",
            }
            for kind in sorted(KINDS)
        ],
        "navs": [
            {
                "nav_date": d,
                "unit_nav": "1" if d < "2026-01-08" else "1.2",
                "accumulated_nav": "1" if d < "2026-01-08" else "1.2",
                "published_at": d + "T20:00:00+08:00",
            }
            for d in dates
        ],
        "statuses": [
            {
                "status_date": d,
                "latest_nav_date": dates[i],
                "known_at": d + "T09:00:00+08:00",
                "purchase_open": True,
                "redemption_open": True,
                "purchase_limit": None,
                "redemption_limit": None,
            }
            for i, d in enumerate(calendar)
        ],
        "settlement": {
            "application_cutoff": "15:00",
            "subscription_confirm_days": 1,
            "redemption_confirm_days": 1,
            "redemption_cash_days": 2,
        },
        "fees": [
            {
                "channel": "alipay",
                "share_class": "A",
                "purchase_rate": "0.01",
                "annual_service_rate": "0",
                "verified_on": "2026-01-01",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "evidence_reference": "synthetic fee",
                "redemption_tiers": [{"min_days": 0, "max_days": None, "rate": "0.015"}],
            }
        ],
        "orders": [
            {
                "submitted_at": "2026-01-05T14:00:00+08:00",
                "signal_nav_date": "2026-01-02",
                "action": "buy",
                "amount": "1000",
                "quantity": None,
            },
            {
                "submitted_at": "2026-01-08T14:00:00+08:00",
                "signal_nav_date": "2026-01-07",
                "action": "sell",
                "amount": "0",
                "quantity": "990.10000000",
            },
        ],
    }


class FundExecutionReplayTests(unittest.TestCase):
    def replay(self, source):
        return replay(source, now=datetime(2026, 2, 1, tzinfo=SHANGHAI_TZ))

    def test_settled_replay_charges_fees_and_waits_for_cash(self):
        result = self.replay(bundle())
        self.assertEqual(result["status"], "completed", result["blockers"])
        self.assertEqual([f["fees"] for f in result["fills"]], ["9.90", "17.82"])
        self.assertEqual(result["curve"][-1]["cash"], "1170.30")
        self.assertAlmostEqual(result["net_return"], 0.1703)
        self.assertEqual(result["curve"][4]["units"], "990.10000000")
        self.assertEqual(result["curve"][5]["cash"], "0")
        self.assertFalse(result["qualifies_strategy"])

    def test_unsettled_or_rejected_orders_never_claim_net_return(self):
        source = bundle()
        source["orders"].pop()
        self.assertIsNone(self.replay(source)["net_return"])
        source = bundle()
        source["statuses"][4]["redemption_open"] = False
        self.assertIsNone(self.replay(source)["net_return"])

    def test_future_signal_or_unreviewed_actions_are_blocked(self):
        for mutate in (
            lambda s: s["orders"][0].update(signal_nav_date="2026-01-05"),
            lambda s: s.update(corporate_actions_review="unknown"),
            lambda s: s["evidence"][0].update(confirmed=False),
        ):
            source = bundle()
            mutate(source)
            self.assertEqual(self.replay(source)["status"], "blocked")

    def test_unknown_fields_and_oversized_lists_are_blocked_before_execution(self):
        source = bundle()
        source["extra"] = "not part of the reviewed schema"
        self.assertEqual(self.replay(source)["status"], "blocked")
        source = bundle()
        source["orders"] = [copy.deepcopy(source["orders"][0]) for _ in range(1001)]
        self.assertEqual(self.replay(source)["status"], "blocked")
