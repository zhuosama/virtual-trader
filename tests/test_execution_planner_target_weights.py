#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0 data contract: ExecutionPlannerAgent.compute_target_weights.

Translates the imperative trading plan + current holdings into a per-symbol
target-weight map {code: weight} for one account. Pure over injected
self.accounts / self.strategies (constructed via __new__, no __init__ I/O).
"""

import os
import sys
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from execution_planner import ExecutionPlannerAgent  # noqa: E402


def _planner(accounts, strategies):
    p = ExecutionPlannerAgent.__new__(ExecutionPlannerAgent)
    p.accounts = accounts
    p.strategies = strategies
    return p


MAIN_STRAT = {"main_strategy": {"rules": {"position_sizing": {"max_single_position": 0.5}}}}


class TestComputeTargetWeights(unittest.TestCase):

    def test_hold_plan_returns_current_holding_weights(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0},
                {"code": "000333", "market_value": 50.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.55},
            "actions": [{"account": "both", "code": "ALL", "action": "hold"}],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertAlmostEqual(weights["600900"], 0.10)
        self.assertAlmostEqual(weights["000333"], 0.05)

    def test_buy_sets_target_to_position_size(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.8},
            "actions": [
                {"account": "main", "code": "000333", "action": "buy", "position_size": 0.07},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        # existing holding stays, new buy targets its position_size
        self.assertAlmostEqual(weights["600900"], 0.10)
        self.assertAlmostEqual(weights["000333"], 0.07)

    def test_sell_zeroes_and_drops_the_name(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0},
                {"code": "000333", "market_value": 50.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.8},
            "actions": [
                {"account": "main", "code": "000333", "action": "sell"},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertNotIn("000333", weights)
        self.assertAlmostEqual(weights["600900"], 0.10)

    def test_clear_zeroes_and_drops_the_name(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0},
                {"code": "000333", "market_value": 50.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.8},
            "actions": [
                {"account": "main", "code": "000333", "action": "clear"},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertNotIn("000333", weights)

    def test_reduce_all_is_noop_when_total_at_or_below_target(self):
        # Real-world bearish case: main ~17% current vs 40% target -> empty diff.
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0},
                {"code": "000333", "market_value": 70.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.40},
            "actions": [
                {"account": "main", "code": "ALL", "action": "reduce_position"},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        # current total = 0.17 <= 0.40 -> reduce is a no-op, weights unchanged
        self.assertAlmostEqual(weights["600900"], 0.10)
        self.assertAlmostEqual(weights["000333"], 0.07)

    def test_reduce_all_scales_down_when_total_above_target(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 300.0},
                {"code": "000333", "market_value": 300.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.40},
            "actions": [
                {"account": "main", "code": "ALL", "action": "reduce_position"},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        # current total = 0.60 > 0.40 -> scale by 0.40/0.60; each 0.30 -> 0.20
        self.assertAlmostEqual(weights["600900"], 0.20)
        self.assertAlmostEqual(weights["000333"], 0.20)
        self.assertAlmostEqual(sum(weights.values()), 0.40)

    def test_reduce_specific_code_halves_weight(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0},
                {"code": "000333", "market_value": 80.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.8},
            "actions": [
                {"account": "main", "code": "000333", "action": "reduce_position"},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertAlmostEqual(weights["600900"], 0.10)
        self.assertAlmostEqual(weights["000333"], 0.04)

    def test_single_name_clamped_to_max_single(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": []}},
            MAIN_STRAT,  # max_single_position = 0.5
        )
        plan = {
            "position_sizing": {"total_position": 0.9},
            "actions": [
                {"account": "main", "code": "600900", "action": "buy", "position_size": 0.7},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertAlmostEqual(weights["600900"], 0.5)

    def test_total_clamped_to_total_position(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": []}},
            MAIN_STRAT,  # max_single_position = 0.5
        )
        plan = {
            "position_sizing": {"total_position": 0.6},
            "actions": [
                {"account": "main", "code": "600900", "action": "buy", "position_size": 0.5},
                {"account": "main", "code": "000333", "action": "buy", "position_size": 0.5},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        # sum 1.0 > 0.6 -> scale by 0.6 -> each 0.30
        self.assertAlmostEqual(weights["600900"], 0.30)
        self.assertAlmostEqual(weights["000333"], 0.30)
        self.assertAlmostEqual(sum(weights.values()), 0.6)

    def test_lab_action_ignored_when_computing_main(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.8},
            "actions": [
                {"account": "lab", "code": "300750", "action": "buy", "position_size": 0.15},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertNotIn("300750", weights)
        self.assertAlmostEqual(weights["600900"], 0.10)

    def test_both_action_applies_to_main(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0},
            ]}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.8},
            "actions": [
                {"account": "both", "code": "600900", "action": "sell"},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertNotIn("600900", weights)

    def test_empty_account_returns_empty(self):
        planner = _planner(
            {"main": {"total_value": 0, "positions": []}},
            MAIN_STRAT,
        )
        plan = {
            "position_sizing": {"total_position": 0.8},
            "actions": [
                {"account": "main", "code": "600900", "action": "buy", "position_size": 0.1},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertEqual(weights, {})

    def test_defaults_when_keys_absent(self):
        # no position_sizing, no strategy rules -> total_position=1.0, max_single=1.0
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": []}},
            {},
        )
        plan = {
            "actions": [
                {"account": "main", "code": "600900", "action": "buy", "position_size": 0.9},
            ],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertAlmostEqual(weights["600900"], 0.9)


if __name__ == "__main__":
    unittest.main()
