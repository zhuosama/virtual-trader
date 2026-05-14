import json
import os
import sys
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


class TestRiskAuditor(unittest.TestCase):
    def test_llm_called_with_risk_rules_and_portfolio(self):
        from agents.audit_layer import run_risk_auditor
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "verdict": "APPROVE",
            "devil_advocate_points": ["a", "b", "c"],
            "hard_reject_hits": [],
            "specific_evidence": [],
        })
        proposal = {
            "proposal_id": "x", "proposed_at": "y", "proposer": "z",
            "account": "main", "change_type": "strategy",
            "current_version": "1.0", "proposed_version": "1.1",
            "diff": [], "triggering_events": [], "rationale": "",
        }
        result = run_risk_auditor(
            proposal=proposal,
            risk_rules="## 主账户\n单笔最大仓位 | 总资产 8%",
            current_portfolio={"positions": {}, "cash": 100000},
            llm_client=mock_llm,
        )
        self.assertEqual(result["verdict"], "APPROVE")
        mock_llm.call.assert_called_once()
        prompt = mock_llm.call.call_args.kwargs.get("user") or mock_llm.call.call_args.args[-1]
        self.assertIn("risk-rules", prompt.lower())
        self.assertIn("100000", prompt)

    def test_handles_all_change_types(self):
        """Risk auditor 对所有 change_type 都运行（不像 Overfitting 只跑 strategy）"""
        from agents.audit_layer import run_risk_auditor
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "verdict": "REJECT",
            "devil_advocate_points": ["a", "b", "c"],
            "hard_reject_hits": ["weakens_stop_loss"],
            "specific_evidence": [],
        })
        proposal = {
            "proposal_id": "x", "proposed_at": "y", "proposer": "z",
            "account": "main", "change_type": "execution",
            "current_version": "1.0", "proposed_version": "1.1",
            "diff": [], "triggering_events": [], "rationale": "",
        }
        result = run_risk_auditor(
            proposal=proposal,
            risk_rules="",
            current_portfolio={},
            llm_client=mock_llm,
        )
        self.assertEqual(result["verdict"], "REJECT")
        mock_llm.call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
