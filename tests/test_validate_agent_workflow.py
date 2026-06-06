#!/usr/bin/env python3
"""Tests for the H43 workflow validator."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_agent_workflow as workflow  # noqa: E402


class TestValidateAgentWorkflow(unittest.TestCase):
    def test_all_workflow_checks_pass_current_repo(self):
        checks = list(workflow.run_checks())
        failed = [check for check in checks if not check.passed]

        self.assertEqual(failed, [])
        self.assertGreaterEqual(len(checks), 7)

    def test_h42_report_consistency_check_is_named(self):
        check = workflow.check_h42_json_report_consistency()

        self.assertEqual(check.name, "WF-3")
        self.assertTrue(check.passed)

    def test_h45_prd_gate_is_named(self):
        check = workflow.check_h45_prd_sections()

        self.assertEqual(check.name, "WF-7")
        self.assertTrue(check.passed)


if __name__ == "__main__":
    unittest.main()
