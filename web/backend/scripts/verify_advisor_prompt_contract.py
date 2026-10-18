"""Keep role prompts aligned with the actual nested evidence request."""

import unittest

from quant_assistant.analysis.advisor_contract import role_instructions, validate_role_report
from quant_assistant.providers.advisor_models import AdvisorModelError


class AdvisorPromptContractTest(unittest.TestCase):
    def test_roles_name_the_actual_evidence_and_candidate_fields(self):
        for kind in ("morning", "afternoon"):
            for role in ("news", "strategy", "reviewer"):
                with self.subTest(kind=kind, role=role):
                    text = role_instructions(role, kind)
                    self.assertIn("input.sources", text)
                    self.assertIn("input.symbols", text)
                    self.assertIn("原样", text)

    def test_unknown_citations_remain_rejected(self):
        state = {"kind": "afternoon", "symbols": ["000001"], "sources": [{"id": "news:cls:123"}]}
        report = {
            "summary": "资料复核",
            "findings": [{"text": "合成验收事实", "source_ids": ["source_1"]}],
        }
        with self.assertRaises(AdvisorModelError) as caught:
            validate_role_report(report, state)
        self.assertEqual(caught.exception.validation_reason, "citation_mismatch")
        report["findings"][0]["source_ids"] = ["news:cls:123"]
        self.assertEqual(validate_role_report(report, state)["findings"], report["findings"])


if __name__ == "__main__":
    unittest.main()
