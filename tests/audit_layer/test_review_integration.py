import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


class TestReviewIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.audit_log_path = Path(self.tmpdir) / "audit_log.json"
        self.audit_log_path.write_text("[]")

    def _proposal(self, change_type="strategy"):
        return {
            "proposal_id": "test-id-001",
            "proposed_at": "2026-05-14T02:00:00Z",
            "proposer": "strategy_maintainer",
            "account": "main",
            "change_type": change_type,
            "current_version": "1.0.5",
            "proposed_version": "1.0.6",
            "diff": [],
            "triggering_events": [{"e": 1}, {"e": 2}, {"e": 3}],
            "rationale": "",
        }

    def _make_mock_llm(self, verdicts):
        """Returns a mock_llm whose call() returns each verdict in sequence."""
        mock = MagicMock()
        responses = [
            json.dumps({"verdict": v, "in_scope": True,
                       "devil_advocate_points": ["a", "b", "c"],
                       "hard_reject_hits": [],
                       "specific_evidence": []})
            for v in verdicts
        ]
        mock.call.side_effect = responses
        return mock

    def test_3_approve_results_in_auto_merge(self):
        from agents.audit_layer import review
        mock_llm = self._make_mock_llm(["APPROVE", "APPROVE", "APPROVE"])
        result = review(
            proposal=self._proposal(),
            changelog=[],
            oos_backtest={"current": {"sharpe": 0.5, "max_dd": 0.01}, "proposed": {"sharpe": 0.5, "max_dd": 0.01}},
            risk_rules="",
            current_portfolio={},
            recent_trades=[],
            current_account={},
            llm_client=mock_llm,
            audit_log_path=str(self.audit_log_path),
        )
        self.assertEqual(result["decision"], "AUTO_MERGE")
        # audit_log written
        log = json.loads(self.audit_log_path.read_text())
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["decision"], "AUTO_MERGE")

    def test_2_reject_results_in_auto_reject_with_feedback(self):
        from agents.audit_layer import review
        mock_llm = self._make_mock_llm(["APPROVE", "REJECT", "REJECT"])
        result = review(
            proposal=self._proposal(),
            changelog=[], oos_backtest={"current":{},"proposed":{}},
            risk_rules="", current_portfolio={}, recent_trades=[], current_account={},
            llm_client=mock_llm,
            audit_log_path=str(self.audit_log_path),
        )
        self.assertEqual(result["decision"], "AUTO_REJECT")
        self.assertTrue(result["feed_back_to_review_agent"])

    def test_risk_type_skips_overfitting_via_short_circuit(self):
        """risk 类 proposal 的 Overfitting Auditor 短路 APPROVE，不消耗 LLM"""
        from agents.audit_layer import review
        # 只准备 2 个 LLM responses（risk + cost auditor），overfitting 不消耗
        mock = MagicMock()
        mock.call.side_effect = [
            json.dumps({"verdict": "APPROVE", "devil_advocate_points": ["a","b","c"], "hard_reject_hits": [], "specific_evidence": []}),
            json.dumps({"verdict": "APPROVE", "devil_advocate_points": ["a","b","c"], "hard_reject_hits": [], "specific_evidence": []}),
        ]
        result = review(
            proposal=self._proposal(change_type="risk"),
            changelog=[], oos_backtest={"current":{},"proposed":{}},
            risk_rules="", current_portfolio={}, recent_trades=[], current_account={},
            llm_client=mock,
            audit_log_path=str(self.audit_log_path),
        )
        self.assertEqual(result["decision"], "AUTO_MERGE")
        self.assertEqual(mock.call.call_count, 2)  # only 2 LLM calls (risk + cost), overfitting short-circuited

    def test_infra_error_results_in_pending_retry_no_feedback(self):
        from agents.audit_layer import review
        # mock returns: malformed both attempts for overfitting → INFRA_ERROR
        mock = MagicMock()
        mock.call.side_effect = ["garbage", "garbage", "garbage", "garbage", "garbage", "garbage"]
        result = review(
            proposal=self._proposal(),  # change_type=strategy
            changelog=[], oos_backtest={"current":{},"proposed":{}},
            risk_rules="", current_portfolio={}, recent_trades=[], current_account={},
            llm_client=mock,
            audit_log_path=str(self.audit_log_path),
        )
        self.assertEqual(result["decision"], "PENDING_RETRY")
        self.assertFalse(result["feed_back_to_review_agent"])


if __name__ == "__main__":
    unittest.main()
