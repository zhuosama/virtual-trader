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


class TestFloorLift(unittest.TestCase):
    """S1 fix: compute_target_weights must honor total_position_floor by
    deploying idle cash into already-held names (up to max_single), instead of
    leaving the floor as dead config. Claim-free: it only re-sizes names the
    strategy already chose; it never invents new tickers."""

    def _strat(self, max_single, floor):
        return {"main_strategy": {"rules": {"position_sizing": {
            "max_single_position": max_single,
            "total_position_floor": floor,
        }}}}

    def test_floor_lifts_existing_holdings_toward_floor(self):
        # Two held names, plenty of headroom under a 0.30 cap; floor 0.50.
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 50.0},   # 0.05
                {"code": "000333", "market_value": 100.0},  # 0.10
            ]}},
            self._strat(max_single=0.30, floor=0.50),
        )
        plan = {
            "position_sizing": {"total_position": 0.55},
            "actions": [{"account": "both", "code": "ALL", "action": "hold"}],
        }
        weights = planner.compute_target_weights("main", plan)
        # idle cash deployed into the two held names up to the floor
        self.assertAlmostEqual(sum(weights.values()), 0.50, places=4)
        # no name exceeds the single-name cap
        self.assertLessEqual(weights["600900"], 0.30 + 1e-9)
        self.assertLessEqual(weights["000333"], 0.30 + 1e-9)
        # both were lifted above their starting weight
        self.assertGreater(weights["600900"], 0.05)
        self.assertGreater(weights["000333"], 0.10)

    def test_floor_lift_is_partial_and_honest_when_caps_block_floor(self):
        # Real main-account shape: 8% single-name cap, only 2 names -> max
        # reachable is 0.16, far below a 0.50 floor. Must NOT fabricate names.
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 50.0},   # 0.05
                {"code": "000651", "market_value": 80.0},   # 0.08 (already at cap)
            ]}},
            self._strat(max_single=0.08, floor=0.50),
        )
        plan = {
            "position_sizing": {"total_position": 0.55},
            "actions": [{"account": "both", "code": "ALL", "action": "hold"}],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertAlmostEqual(weights["600900"], 0.08, places=4)  # lifted to cap
        self.assertAlmostEqual(weights["000651"], 0.08, places=4)  # stays at cap
        self.assertAlmostEqual(sum(weights.values()), 0.16, places=4)  # honest gap
        self.assertEqual(set(weights), {"600900", "000651"})  # no invented names

    def test_no_floor_lift_when_floor_absent(self):
        # Backward compatibility: without total_position_floor, a hold plan must
        # still return current holding weights unchanged.
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 50.0},
            ]}},
            {"main_strategy": {"rules": {"position_sizing": {"max_single_position": 0.30}}}},
        )
        plan = {
            "position_sizing": {"total_position": 0.55},
            "actions": [{"account": "both", "code": "ALL", "action": "hold"}],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertAlmostEqual(weights["600900"], 0.05)

    def test_no_floor_lift_when_already_above_floor(self):
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 300.0},  # 0.30
                {"code": "000333", "market_value": 300.0},  # 0.30
            ]}},
            self._strat(max_single=0.40, floor=0.50),
        )
        plan = {
            "position_sizing": {"total_position": 0.80},
            "actions": [{"account": "both", "code": "ALL", "action": "hold"}],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertAlmostEqual(weights["600900"], 0.30)
        self.assertAlmostEqual(weights["000333"], 0.30)

    def test_floor_never_lifts_above_total_position_ceiling(self):
        # Misconfig guard: floor > ceiling must not push deployment past ceiling.
        planner = _planner(
            {"main": {"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 50.0},
            ]}},
            self._strat(max_single=0.90, floor=0.80),
        )
        plan = {
            "position_sizing": {"total_position": 0.40},  # ceiling below floor
            "actions": [{"account": "both", "code": "ALL", "action": "hold"}],
        }
        weights = planner.compute_target_weights("main", plan)
        self.assertLessEqual(sum(weights.values()), 0.40 + 1e-9)


if __name__ == "__main__":
    unittest.main()
