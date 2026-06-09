"""P1: LLM missing → audit BLOCKED, active.json/changelog unchanged."""
import json
import os
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
import sys
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

import audit_layer


class TestLLMMissingBlocked(unittest.TestCase):
    """When llm_client is None, audit_layer.review() must return BLOCKED
    and never write to active.json/changelog."""

    def _make_proposal(self):
        return {
            "proposal_id": "test-llm-missing-001",
            "proposed_at": "2026-05-15T12:00:00",
            "proposer": "test",
            "account": "main",
            "change_type": "strategy",
            "current_version": "v1.0.5",
            "proposed_version": "v1.0.6",
            "diff": [{"field": "take_profit_pct", "old": 15, "new": 12}],
            "triggering_events": ["test"],
            "rationale": "test",
        }

    def test_review_returns_blocked_when_llm_none(self):
        """audit_layer.review() with llm_client=None → BLOCKED"""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            log_path = f.name
        try:
            result = audit_layer.review(
                proposal=self._make_proposal(),
                changelog=[],
                oos_backtest={},
                risk_rules="",
                current_portfolio={},
                recent_trades=[],
                current_account={},
                llm_client=None,
                audit_log_path=log_path,
            )
            self.assertEqual(result["decision"], "BLOCKED")
            self.assertIn("LLM client", result["reason"])
            self.assertEqual(result["verdicts"], {})
        finally:
            os.unlink(log_path)

    def test_blocked_logged_to_audit_log(self):
        """BLOCKED decision must be appended to audit_log.json"""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            log_path = f.name
        try:
            audit_layer.review(
                proposal=self._make_proposal(),
                changelog=[],
                oos_backtest={},
                risk_rules="",
                current_portfolio={},
                recent_trades=[],
                current_account={},
                llm_client=None,
                audit_log_path=log_path,
            )
            with open(log_path) as f:
                log = json.load(f)
            self.assertEqual(len(log), 1)
            self.assertEqual(log[0]["decision"], "BLOCKED")
        finally:
            os.unlink(log_path)

    def test_blocked_does_not_call_any_reviewer(self):
        """When llm_client is None, no reviewer should be called.
        We verify by checking verdicts is empty dict."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            log_path = f.name
        try:
            result = audit_layer.review(
                proposal=self._make_proposal(),
                changelog=[],
                oos_backtest={},
                risk_rules="",
                current_portfolio={},
                recent_trades=[],
                current_account={},
                llm_client=None,
                audit_log_path=log_path,
            )
            self.assertEqual(result["verdicts"], {})
        finally:
            os.unlink(log_path)


class TestCoordinatorLLMBlocked(unittest.TestCase):
    """coordinator._audit_strategy_adjustments returns BLOCKED when llm is None."""

    def test_blocked_when_llm_none(self):
        """Simulate maintainer with llm=None → adjustments blocked."""
        from coordinator import MultiAgentCoordinator

        class FakeMaintainer:
            llm = None
            changelog = []

        coordinator = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        coordinator.data_dir = tempfile.mkdtemp()

        adjustments = [{"parameter": "take_profit_pct", "new_value": 12, "reason": "test"}]
        review_report = {"accounts": {}}

        result = coordinator._audit_strategy_adjustments(
            FakeMaintainer(), adjustments, review_report
        )
        self.assertEqual(result["audit_decision"], "BLOCKED")
        self.assertEqual(result["applied_adjustments"], [])
        self.assertEqual(result["failed_adjustments"], adjustments)


if __name__ == "__main__":
    unittest.main()
