#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0b data contract: coordinator.build_plan_record.

Pure helper that assembles the persisted `plan` record (with target_weights
for main + lab) from a planner, the trading_plan, and the validation_result.
Built via __new__ to avoid coordinator __init__ I/O + agent construction.
"""

import os
import sys
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from coordinator import MultiAgentCoordinator  # noqa: E402
from execution_planner import ExecutionPlannerAgent  # noqa: E402


def _planner():
    p = ExecutionPlannerAgent.__new__(ExecutionPlannerAgent)
    p.accounts = {
        "main": {"total_value": 1000.0, "positions": [
            {"code": "600900", "market_value": 100.0},
        ]},
        "lab": {"total_value": 500.0, "positions": [
            {"code": "300750", "market_value": 75.0},
        ]},
    }
    p.strategies = {
        "main_strategy": {"rules": {"position_sizing": {"max_single_position": 0.5}}},
        "lab_strategy": {"rules": {"position_sizing": {"max_single_position": 0.2}}},
    }
    return p


def _coord():
    return MultiAgentCoordinator.__new__(MultiAgentCoordinator)


class TestBuildPlanRecord(unittest.TestCase):

    def test_record_has_target_weights_dicts(self):
        coord = _coord()
        planner = _planner()
        plan = {
            "market_regime": "neutral",
            "position_sizing": {"total_position": 0.55},
            "actions": [{"account": "both", "code": "ALL", "action": "hold"}],
        }
        validation_result = {"decision": "APPROVED"}
        record = coord.build_plan_record(planner, plan, validation_result)
        self.assertIsInstance(record["target_weights"]["main"], dict)
        self.assertIsInstance(record["target_weights"]["lab"], dict)
        self.assertAlmostEqual(record["target_weights"]["main"]["600900"], 0.10)
        self.assertAlmostEqual(record["target_weights"]["lab"]["300750"], 0.15)

    def test_record_carries_decision_and_total_position_and_actions(self):
        coord = _coord()
        planner = _planner()
        plan = {
            "market_regime": "bullish",
            "position_sizing": {"total_position": 0.7},
            "actions": [{"account": "main", "code": "000001", "action": "buy", "position_size": 0.05}],
        }
        validation_result = {"decision": "MODIFY"}
        record = coord.build_plan_record(planner, plan, validation_result)
        self.assertEqual(record["decision"], "MODIFY")
        self.assertEqual(record["market_regime"], "bullish")
        self.assertEqual(record["total_position"], 0.7)
        self.assertEqual(record["actions"], plan["actions"])
        self.assertIn("generated_at", record)

    def test_record_total_position_defaults_to_one_when_absent(self):
        coord = _coord()
        planner = _planner()
        plan = {"market_regime": "neutral", "actions": []}
        record = coord.build_plan_record(planner, plan, {"decision": "APPROVED"})
        self.assertEqual(record["total_position"], 1.0)


if __name__ == "__main__":
    unittest.main()
