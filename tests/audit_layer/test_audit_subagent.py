import json
import unittest
from unittest.mock import MagicMock, patch
import os, sys
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


class TestAuditSubagent(unittest.TestCase):
    def test_valid_json_response_parsed(self):
        from agents.audit_subagent import call_auditor
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "verdict": "APPROVE",
            "devil_advocate_points": ["a", "b", "c"],
            "hard_reject_hits": [],
            "specific_evidence": [],
            "in_scope": True,
        })
        result = call_auditor("overfitting_auditor", "test prompt", llm_client=mock_llm, model="deepseek-v4-pro")
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(len(result["devil_advocate_points"]), 3)

    def test_malformed_json_retried_once_then_infra_error(self):
        from agents.audit_subagent import call_auditor
        mock_llm = MagicMock()
        mock_llm.call.return_value = "not valid json {{"  # malformed both times
        result = call_auditor("overfitting_auditor", "test prompt", llm_client=mock_llm, model="deepseek-v4-pro")
        self.assertEqual(result["verdict"], "INFRA_ERROR")
        self.assertEqual(result["error_type"], "malformed_json")
        self.assertEqual(result["attempt_count"], 2)
        self.assertEqual(mock_llm.call.call_count, 2)

    def test_malformed_then_valid_succeeds(self):
        from agents.audit_subagent import call_auditor
        mock_llm = MagicMock()
        mock_llm.call.side_effect = [
            "garbage",  # first attempt fails
            json.dumps({"verdict": "REJECT", "devil_advocate_points": ["x"]*3,
                       "hard_reject_hits": ["triggering_events <= 2"],
                       "specific_evidence": [], "in_scope": True}),
        ]
        result = call_auditor("overfitting_auditor", "test prompt", llm_client=mock_llm, model="deepseek-v4-pro")
        self.assertEqual(result["verdict"], "REJECT")
        self.assertEqual(mock_llm.call.call_count, 2)

    def test_exception_caught_as_infra_error(self):
        from agents.audit_subagent import call_auditor
        mock_llm = MagicMock()
        mock_llm.call.side_effect = TimeoutError("simulated")
        result = call_auditor("overfitting_auditor", "test prompt", llm_client=mock_llm, model="deepseek-v4-pro")
        self.assertEqual(result["verdict"], "INFRA_ERROR")
        # exception type is captured for diagnosis
        self.assertIn("timeout", result["error_type"])


if __name__ == "__main__":
    unittest.main()
