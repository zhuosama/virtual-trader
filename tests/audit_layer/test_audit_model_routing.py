"""Verify audit reviewers route to deepseek-v4-pro, not flash fallback."""
import unittest

import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "agents"))

from llm_client import AGENT_MODEL_MAP


class TestAuditModelRouting(unittest.TestCase):
    """All 3 audit reviewers must map to deepseek-v4-pro."""

    def test_overfitting_auditor_uses_pro(self):
        self.assertEqual(AGENT_MODEL_MAP["overfitting_auditor"], "deepseek-v4-pro")

    def test_risk_auditor_uses_pro(self):
        self.assertEqual(AGENT_MODEL_MAP["risk_auditor"], "deepseek-v4-pro")

    def test_cost_execution_auditor_uses_pro(self):
        self.assertEqual(AGENT_MODEL_MAP["cost_execution_auditor"], "deepseek-v4-pro")

    def test_no_auditor_uses_flash(self):
        """No auditor name should fall back to flash."""
        auditors = {"overfitting_auditor", "risk_auditor", "cost_execution_auditor"}
        for name in auditors:
            self.assertIn(name, AGENT_MODEL_MAP,
                          f"{name} missing from AGENT_MODEL_MAP — will fall back to flash")
            self.assertNotEqual(AGENT_MODEL_MAP[name], "deepseek-v4-flash",
                                f"{name} should use pro, not flash")


if __name__ == "__main__":
    unittest.main()
