import json
import os
import sys
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


def _proposal(change_type="strategy", triggering_events=None):
    return {
        "proposal_id": "test-id",
        "proposed_at": "2026-05-14T02:00:00Z",
        "proposer": "strategy_maintainer",
        "account": "main",
        "change_type": change_type,
        "current_version": "1.0.5",
        "proposed_version": "1.0.6",
        "diff": [],
        "triggering_events": triggering_events if triggering_events is not None else [{"event": "1"}],
        "rationale": "",
    }


class TestOverfittingAuditorScope(unittest.TestCase):
    """change_type != strategy 应当短路 APPROVE，不调 LLM"""

    def test_risk_type_short_circuits_approve(self):
        from agents.audit_layer import run_overfitting_auditor
        mock_llm = MagicMock()
        result = run_overfitting_auditor(
            proposal=_proposal(change_type="risk"),
            changelog=[],
            oos_backtest={"current": {"sharpe": 0.5, "max_dd": 0.01}, "proposed": {"sharpe": 0.5, "max_dd": 0.01}},
            llm_client=mock_llm,
        )
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(result.get("in_scope"), False)
        # LLM should not have been called for non-strategy types
        mock_llm.call.assert_not_called()

    def test_execution_type_short_circuits(self):
        from agents.audit_layer import run_overfitting_auditor
        mock_llm = MagicMock()
        result = run_overfitting_auditor(
            proposal=_proposal(change_type="execution"),
            changelog=[],
            oos_backtest={"current": {}, "proposed": {}},
            llm_client=mock_llm,
        )
        self.assertEqual(result["verdict"], "APPROVE")
        mock_llm.call.assert_not_called()


class TestOverfittingAuditorStrategy(unittest.TestCase):
    """change_type == strategy 时应调 LLM 并解析其返回"""

    def test_llm_called_for_strategy_type(self):
        from agents.audit_layer import run_overfitting_auditor
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "verdict": "REJECT",
            "in_scope": True,
            "devil_advocate_points": ["a", "b", "c"],
            "hard_reject_hits": ["triggering_events <= 2"],
            "specific_evidence": [],
        })
        result = run_overfitting_auditor(
            proposal=_proposal(change_type="strategy", triggering_events=[{"e": 1}]),
            changelog=[],
            oos_backtest={"current": {"sharpe": 0.5, "max_dd": 0.01}, "proposed": {"sharpe": 0.4, "max_dd": 0.02}},
            llm_client=mock_llm,
        )
        self.assertEqual(result["verdict"], "REJECT")
        mock_llm.call.assert_called_once()
        # prompt should mention triggering_events and OOS sharpe values
        prompt_arg = mock_llm.call.call_args.kwargs.get("user") or mock_llm.call.call_args.args[-1]
        self.assertIn("triggering_events", prompt_arg)


if __name__ == "__main__":
    unittest.main()
