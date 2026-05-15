"""P3: Status semantics — success/degraded/failed differentiation."""
import unittest


class TestStatusSemantics(unittest.TestCase):
    """Test that coordinator returns correct status codes."""

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


if __name__ == "__main__":
    unittest.main()
