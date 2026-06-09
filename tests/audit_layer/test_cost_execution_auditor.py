import json
import os
import sys
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


class TestCostExecutionAuditor(unittest.TestCase):
    def test_llm_called_with_recent_trades_and_account(self):
        from agents.audit_layer import run_cost_execution_auditor
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
        result = run_cost_execution_auditor(
            proposal=proposal,
            recent_trades=[{"date": "2026-05-12", "ticker": "601088"}],
            current_account={"net_value": 101167},
            llm_client=mock_llm,
        )
        self.assertEqual(result["verdict"], "APPROVE")
        prompt = mock_llm.call.call_args.kwargs.get("user") or mock_llm.call.call_args.args[-1]
        self.assertIn("601088", prompt)
        self.assertIn("101167", prompt)


if __name__ == "__main__":
    unittest.main()
