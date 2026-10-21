"""Worker op announcements.fetch: announcement aggregation + kind mapping."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from announcement_operations import (
    ANNOUNCEMENT_CAPABILITIES,
    classify_kind,
    execute_announcements,
    fetch_announcements,
)
from operations import CAPABILITIES, execute
from public_operations import IN_PROCESS_CAPABILITIES
from worker_result import child_envelope


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_http(rows=None, explode=False):
    def get(url, **kwargs):
        assert "api.fund.eastmoney.com/f10/JJGG" in url
        assert kwargs["params"]["fundcode"] == "000001"
        if explode:
            raise ConnectionError("boom")
        return _FakeResponse({"Data": rows if rows is not None else []})

    return get


_ROWS = [
    {"ID": "1", "TITLE": "关于旗下基金分红的公告", "PUBLISHDATE": "2026-10-15 00:00:00"},
    {"ID": "2", "TITLE": "增聘基金经理的公告", "PUBLISHDATE": "2026-10-10 00:00:00"},
    {"ID": "3", "TITLE": "提示基金可能触发清盘情形的公告", "PUBLISHDATE": "2026-09-01 00:00:00"},
    {"ID": "4", "TITLE": "2026年第三季度报告", "PUBLISHDATE": "2026-10-20 00:00:00"},
    {"ID": "bad", "TITLE": "", "PUBLISHDATE": "2026-10-20"},
]


class AnnouncementTests(unittest.TestCase):
    def test_op_is_registered_in_process(self):
        self.assertIn("announcements.fetch", CAPABILITIES)
        self.assertIn("announcements.fetch", IN_PROCESS_CAPABILITIES)
        self.assertIn("announcements.fetch", ANNOUNCEMENT_CAPABILITIES)

    def test_classify_kind(self):
        self.assertEqual(classify_kind("关于分红的公告"), "dividend")
        self.assertEqual(classify_kind("基金收益分配公告"), "dividend")
        self.assertEqual(classify_kind("基金经理变更公告"), "manager_change")
        self.assertEqual(classify_kind("增聘基金经理的公告"), "manager_change")
        self.assertEqual(classify_kind("基金合同终止及清算的公告"), "liquidation_risk")
        self.assertEqual(classify_kind("连续60个工作日基金资产净值低于5000万元的提示"), "liquidation_risk")
        self.assertEqual(classify_kind("2026年第三季度报告"), "other")

    def test_fetch_and_mapping(self):
        result = fetch_announcements(["000001"], http_get=_fake_http(_ROWS))
        items = result["announcements"]
        self.assertEqual(len(items), 4)  # the empty-title row is dropped
        kinds = {item["title"]: item["kind"] for item in items}
        self.assertEqual(kinds["关于旗下基金分红的公告"], "dividend")
        self.assertEqual(kinds["增聘基金经理的公告"], "manager_change")
        self.assertEqual(kinds["提示基金可能触发清盘情形的公告"], "liquidation_risk")
        self.assertEqual(kinds["2026年第三季度报告"], "other")
        # newest first
        self.assertEqual(items[0]["published_at"], "2026-10-20")
        self.assertTrue(items[0]["source_url"].startswith("https://fund.eastmoney.com/gonggao/000001,"))
        self.assertEqual(items[0]["fund_code"], "000001")
        self.assertEqual(result["warnings"], [])

    def test_since_filter(self):
        result = fetch_announcements(
            ["000001"], since=date(2026, 10, 12), http_get=_fake_http(_ROWS)
        )
        dates = [item["published_at"] for item in result["announcements"]]
        self.assertEqual(dates, ["2026-10-20", "2026-10-15"])

    def test_source_failure_degrades(self):
        result = fetch_announcements(["000001"], http_get=_fake_http(explode=True))
        self.assertEqual(result["announcements"], [])
        self.assertTrue(result["warnings"])

    def test_empty_result_warns(self):
        result = fetch_announcements(["000001"], http_get=_fake_http([]))
        self.assertEqual(result["announcements"], [])
        self.assertTrue(result["warnings"])

    def test_invalid_payloads(self):
        with self.assertRaises(ValueError):
            fetch_announcements([])
        with self.assertRaises(ValueError):
            fetch_announcements(["000001"], since="yesterday")
        with self.assertRaises(ValueError):
            fetch_announcements(["000001"], source="bogus")
        with self.assertRaises(ValueError):
            execute_announcements("announcements.fetch", {"fund_codes": [""]})
        outcome = child_envelope(
            lambda: execute_announcements("announcements.fetch", {"fund_codes": []})
        )
        self.assertEqual(outcome["error_code"], "invalid_request")

    def test_execute_dispatch(self):
        import announcement_operations

        original = announcement_operations.requests.get
        announcement_operations.requests.get = _fake_http(_ROWS)  # noqa: E731
        try:
            result = execute("announcements.fetch", {"fund_codes": ["000001"]})
        finally:
            announcement_operations.requests.get = original
        self.assertEqual(len(result["announcements"]), 4)


if __name__ == "__main__":
    unittest.main()
