"""Autospec test: audit_subagent.call_auditor uses LLMClient.call() with correct signature."""
import unittest
from unittest.mock import MagicMock, call

import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import audit_subagent
from llm_client import LLMClient


class TestCallAuditorRealSignature(unittest.TestCase):
    """Verify call_auditor passes args matching LLMClient.call(agent_name, system_prompt, user_message)."""

    def test_call_signature_matches_llm_client(self):
        """call_auditor must call llm_client.call(agent_name, system_prompt, user_message)
        using positional args — not keyword args like system=, user=, model=."""
        mock_client = MagicMock(spec=LLMClient)
        mock_client.call.return_value = '{"verdict": "APPROVE", "reasoning": "ok"}'

        result = audit_subagent.call_auditor(
            "overfitting_auditor", "test prompt", llm_client=mock_client
        )

        # Must have called once (or twice if first was malformed — here it's valid)
        self.assertTrue(mock_client.call.called)
        first_call = mock_client.call.call_args_list[0]

        # Verify positional args: (agent_name, system_prompt, user_message)
        args, kwargs = first_call
        self.assertEqual(args[0], "overfitting_auditor")
        self.assertEqual(args[1], "")  # system_prompt
        self.assertEqual(args[2], "test prompt")  # user_message

        # Must NOT use keyword args named system=, user=, model=
        self.assertNotIn("system", kwargs)
        self.assertNotIn("user", kwargs)
        self.assertNotIn("model", kwargs)

    def test_no_type_error_on_real_interface(self):
        """Simulate the exact call pattern that would fail with old code."""
        mock_client = MagicMock(spec=LLMClient)
        mock_client.call.return_value = '{"verdict": "APPROVE", "reasoning": "fine"}'

        # This is what audit_layer does — the same pattern
        result = audit_subagent.call_auditor(
            "risk_auditor", "some risk prompt", llm_client=mock_client
        )
        self.assertEqual(result["verdict"], "APPROVE")

    def test_retry_on_malformed_json(self):
        """First call returns garbage → retry → second call returns valid JSON."""
        mock_client = MagicMock(spec=LLMClient)
        mock_client.call.side_effect = [
            "not json at all",
            '{"verdict": "REJECT", "reasoning": "bad idea"}',
        ]

        result = audit_subagent.call_auditor(
            "cost_execution_auditor", "prompt", llm_client=mock_client
        )
        self.assertEqual(result["verdict"], "REJECT")
        self.assertEqual(mock_client.call.call_count, 2)

    def test_infra_error_on_exception(self):
        """If llm_client.call raises → INFRA_ERROR, not crash."""
        mock_client = MagicMock(spec=LLMClient)
        mock_client.call.side_effect = ConnectionError("network down")

        result = audit_subagent.call_auditor(
            "overfitting_auditor", "prompt", llm_client=mock_client
        )
        self.assertEqual(result["verdict"], "INFRA_ERROR")
        self.assertIn("network", result.get("reason", result.get("error_type", "")))


if __name__ == "__main__":
    unittest.main()
