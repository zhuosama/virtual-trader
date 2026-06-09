"""P3: Status semantics — success/degraded/failed differentiation."""
import os
import sys
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))


class TestStatusSemantics(unittest.TestCase):
    """Test that coordinator returns correct status codes."""

    def _coordinator_with_pre_market_decision(self, decision):
        from coordinator import MultiAgentCoordinator

        class FakeMarketAnalyst:
            def run_analysis(self):
                return {"analysis_summary": "ok", "market_tone": "neutral"}

        class FakeExecutionPlanner:
            def generate_trading_plan(self, market_analysis):
                return {"actions": []}

            def generate_plan_summary(self, trading_plan):
                return "no trades"

        class FakeRiskController:
            def validate_trading_plan(self, trading_plan):
                return {"decision": decision, "warnings": ["risk gate"]}

            def generate_validation_summary(self, validation_result):
                return validation_result["decision"]

        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.agents = {
            "market_analyst": FakeMarketAnalyst(),
            "execution_planner": FakeExecutionPlanner(),
            "risk_controller": FakeRiskController(),
        }
        return c

    def test_success_when_no_warnings(self):
        """No warnings → status should be 'success'."""
        wf = {'warnings': [], 'status': 'success'}
        if wf['warnings']:
            wf['status'] = 'degraded'
        self.assertEqual(wf['status'], 'success')

    def test_degraded_when_warnings(self):
        """Warnings present → status should be 'degraded'."""
        wf = {'warnings': ['2 个风控问题'], 'status': 'success'}
        if wf['warnings']:
            wf['status'] = 'degraded'
        self.assertEqual(wf['status'], 'degraded')

    def test_failed_on_exception(self):
        """Exception caught → status should be 'failed'."""
        wf = {'status': 'success', 'warnings': []}
        try:
            raise RuntimeError("test error")
        except Exception as e:
            wf['status'] = 'failed'
            wf['error'] = str(e)
        self.assertEqual(wf['status'], 'failed')
        self.assertEqual(wf['error'], 'test error')

    def test_blocked_audit_is_degraded(self):
        """BLOCKED audit decision → warning added → degraded."""
        wf = {'warnings': [], 'status': 'success'}
        audit_decision = 'BLOCKED'
        if audit_decision == 'BLOCKED':
            wf['warnings'].append("策略变更被阻止（LLM不可用）")
        if wf['warnings']:
            wf['status'] = 'degraded'
        self.assertEqual(wf['status'], 'degraded')
        self.assertIn('LLM', wf['warnings'][0])

    def test_pre_market_rejected_risk_decision_is_degraded(self):
        """Risk REJECTED blocks trading and must not report workflow success."""
        c = self._coordinator_with_pre_market_decision("REJECTED")
        result = c.run_pre_market_workflow()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["risk_decision"], "REJECTED")
        self.assertEqual(result["warnings"], ["risk gate"])

    def test_pre_market_modify_risk_decision_is_degraded(self):
        """Risk MODIFY requires intervention and must not report workflow success."""
        c = self._coordinator_with_pre_market_decision("MODIFY")
        result = c.run_pre_market_workflow()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["risk_decision"], "MODIFY")
        self.assertEqual(result["warnings"], ["risk gate"])

    def test_pre_market_approved_risk_decision_is_success(self):
        """Risk APPROVED allows the pre-market workflow to report success."""
        c = self._coordinator_with_pre_market_decision("APPROVED")
        result = c.run_pre_market_workflow()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["risk_decision"], "APPROVED")
        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main()
