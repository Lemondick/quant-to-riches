"""Worker op fund.health_check: four体检 dimensions + degradation."""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from health_operations import HEALTH_CAPABILITIES, execute_health, health_check
from operations import CAPABILITIES, execute
from public_operations import IN_PROCESS_CAPABILITIES
from worker_result import child_envelope

TODAY = date(2026, 10, 21)


def _view(**overrides):
    view = {
        "latest_scale": "12.50亿元",
        "established_on": date(2018, 3, 1),
        "fund_type": "混合型",
        "managers": [{"name": "张三", "tenure_start": date(2020, 5, 1)}],
        "holdings": [
            {"name": "贵州茅台", "weight": Decimal("9.5")},
            {"name": "招商银行", "weight": Decimal("8.0")},
            {"name": "中国平安", "weight": Decimal("7.0")},
        ],
        "fees": [
            {"fee_type": "管理费", "rate": Decimal("0.015")},
            {"fee_type": "托管费", "rate": Decimal("0.0025")},
        ],
    }
    view.update(overrides)
    return view


def _fetcher(views):
    def fetch(code):
        if code not in views:
            raise RuntimeError("provider exploded")
        return views[code]

    return fetch


def _finding(report, kind):
    return next(item for item in report["findings"] if item["kind"] == kind)


class HealthCheckTests(unittest.TestCase):
    def test_op_is_registered_in_process(self):
        self.assertIn("fund.health_check", CAPABILITIES)
        self.assertIn("fund.health_check", IN_PROCESS_CAPABILITIES)
        self.assertIn("fund.health_check", HEALTH_CAPABILITIES)

    def test_all_info_when_healthy(self):
        result = health_check(
            [{"code": "000001", "name": "稳健混合", "declared_style": "大盘价值"}],
            fetcher=_fetcher({"000001": _view()}),
            as_of=TODAY,
        )
        report = result["reports"][0]
        self.assertEqual(report["code"], "000001")
        self.assertEqual(report["name"], "稳健混合")
        self.assertEqual(len(report["findings"]), 4)
        self.assertEqual(_finding(report, "scale_change")["severity"], "info")
        self.assertIn("数据不足", _finding(report, "scale_change")["detail"])
        self.assertEqual(_finding(report, "manager_change")["severity"], "info")
        self.assertEqual(_finding(report, "style_drift")["severity"], "info")
        self.assertEqual(_finding(report, "fee_level")["severity"], "info")

    def test_scale_surge_warns(self):
        view = _view(
            scale_history=[
                {"label": "2026Q2", "scale": Decimal("10")},
                {"label": "2026Q3", "scale": Decimal("15")},
            ]
        )
        result = health_check([{"code": "000001"}], fetcher=_fetcher({"000001": view}), as_of=TODAY)
        finding = _finding(result["reports"][0], "scale_change")
        self.assertEqual(finding["severity"], "warn")
        self.assertIn("激增", finding["detail"])
        self.assertEqual(finding["evidence"]["change_ratio"], 0.5)

    def test_scale_stable_is_info(self):
        view = _view(
            scale_history=[
                {"label": "2026Q2", "scale": Decimal("10")},
                {"label": "2026Q3", "scale": Decimal("11")},
            ]
        )
        result = health_check([{"code": "000001"}], fetcher=_fetcher({"000001": view}), as_of=TODAY)
        self.assertEqual(_finding(result["reports"][0], "scale_change")["severity"], "info")

    def test_manager_change_is_critical(self):
        view = _view(managers=[{"name": "李四", "tenure_start": date(2026, 9, 1)}])
        result = health_check([{"code": "000001"}], fetcher=_fetcher({"000001": view}), as_of=TODAY)
        finding = _finding(result["reports"][0], "manager_change")
        self.assertEqual(finding["severity"], "critical")
        self.assertIn("李四", finding["detail"])

    def test_new_fund_manager_not_flagged(self):
        view = _view(
            established_on=date(2026, 8, 1),
            managers=[{"name": "李四", "tenure_start": date(2026, 8, 1)}],
        )
        result = health_check([{"code": "000001"}], fetcher=_fetcher({"000001": view}), as_of=TODAY)
        self.assertEqual(_finding(result["reports"][0], "manager_change")["severity"], "info")

    def test_previous_managers_comparison(self):
        result = health_check(
            [{"code": "000001", "previous_managers": ["张三", "王五"]}],
            fetcher=_fetcher({"000001": _view()}),
            as_of=TODAY,
        )
        finding = _finding(result["reports"][0], "manager_change")
        self.assertEqual(finding["severity"], "critical")
        self.assertIn("王五", finding["detail"])

    def test_style_drift_warns(self):
        view = _view(
            holdings=[
                {"name": "亿纬锂能", "weight": Decimal("9.0")},
                {"name": "澜起科技", "weight": Decimal("8.0")},
                {"name": "中微半导体", "weight": Decimal("7.5")},
            ]
        )
        result = health_check(
            [{"code": "000001", "declared_style": "大盘价值"}],
            fetcher=_fetcher({"000001": view}),
            as_of=TODAY,
        )
        finding = _finding(result["reports"][0], "style_drift")
        self.assertEqual(finding["severity"], "warn")
        self.assertIn("风格漂移", finding["detail"])

    def test_style_drift_needs_declared_style(self):
        result = health_check(
            [{"code": "000001"}], fetcher=_fetcher({"000001": _view()}), as_of=TODAY
        )
        finding = _finding(result["reports"][0], "style_drift")
        self.assertEqual(finding["severity"], "info")
        self.assertIn("数据不足", finding["detail"])

    def test_fee_level_warns(self):
        view = _view(
            fees=[
                {"fee_type": "管理费", "rate": Decimal("0.015")},
                {"fee_type": "托管费", "rate": Decimal("0.01")},
            ]
        )
        result = health_check([{"code": "000001"}], fetcher=_fetcher({"000001": view}), as_of=TODAY)
        finding = _finding(result["reports"][0], "fee_level")
        self.assertEqual(finding["severity"], "warn")

    def test_fee_level_currency_fund(self):
        view = _view(
            fund_type="货币型",
            fees=[
                {"fee_type": "管理费", "rate": Decimal("0.0033")},
                {"fee_type": "托管费", "rate": Decimal("0.001")},
            ],
        )
        result = health_check([{"code": "000001"}], fetcher=_fetcher({"000001": view}), as_of=TODAY)
        self.assertEqual(_finding(result["reports"][0], "fee_level")["severity"], "info")

    def test_fetcher_failure_degrades_per_fund(self):
        result = health_check(
            [{"code": "NOPE"}, {"code": "000001", "declared_style": "大盘价值"}],
            fetcher=_fetcher({"000001": _view()}),
            as_of=TODAY,
        )
        self.assertEqual(len(result["reports"]), 2)
        failed = result["reports"][0]
        self.assertTrue(all(item["severity"] == "info" for item in failed["findings"]))
        self.assertTrue(all("数据不足" in item["detail"] for item in failed["findings"]))
        # the other fund is unaffected
        self.assertEqual(_finding(result["reports"][1], "style_drift")["severity"], "info")

    def test_invalid_payloads(self):
        with self.assertRaises(ValueError):
            health_check([], as_of=TODAY)
        with self.assertRaises(ValueError):
            health_check([{"code": ""}], as_of=TODAY)
        outcome = child_envelope(lambda: execute_health("fund.health_check", {"funds": []}))
        self.assertEqual(outcome["error_code"], "invalid_request")

    def test_execute_dispatch(self):
        import health_operations

        original = health_operations._production_fetcher
        health_operations._production_fetcher = lambda code: _view()  # noqa: E731
        try:
            result = execute("fund.health_check", {"funds": [{"code": "000001"}]})
        finally:
            health_operations._production_fetcher = original
        self.assertIn("reports", result)


if __name__ == "__main__":
    unittest.main()
