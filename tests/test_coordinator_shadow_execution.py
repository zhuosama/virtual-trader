#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 integration: coordinator.run_shadow_execution.

Reads today's pre-market workflow JSON plan.target_weights, reads prices from
accounts/<acct>.json current_price, runs ExecutionModel.execute_plan in shadow
for main + lab. MUST NOT write accounts/*.json or trades/. Built via __new__ +
tmp data_dir to avoid agent construction.
"""

import json
import os
import sys
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from coordinator import MultiAgentCoordinator  # noqa: E402


def _coord(data_dir):
    c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
    c.data_dir = data_dir
    return c


def _setup(data_dir, date_str, target_weights, main_positions, decision=None,
           main_total_value=1000.0):
    os.makedirs(os.path.join(data_dir, "accounts"))
    os.makedirs(os.path.join(data_dir, "trades"))
    os.makedirs(os.path.join(data_dir, "agents", "workflows"))
    with open(os.path.join(data_dir, "accounts", "main.json"), "w") as f:
        json.dump({"total_value": main_total_value, "positions": main_positions}, f)
    with open(os.path.join(data_dir, "accounts", "lab.json"), "w") as f:
        json.dump({"total_value": 500.0, "positions": [
            {"code": "300750", "market_value": 75.0, "current_price": 150.0},
        ]}, f)
    plan = {"total_position": 0.8, "target_weights": target_weights}
    if decision is not None:
        plan["decision"] = decision
    wf_name = f"workflow_pre_market_{date_str.replace('-', '')}_010101.json"
    with open(os.path.join(data_dir, "agents", "workflows", wf_name), "w") as f:
        json.dump({"workflow_type": "pre_market", "plan": plan}, f)


class TestRunShadowExecution(unittest.TestCase):

    def test_shadow_execution_produces_candidates_and_does_not_touch_accounts(self):
        data_dir = tempfile.mkdtemp()
        date_str = "2026-06-05"
        _setup(
            data_dir, date_str,
            target_weights={"main": {"600900": 0.20}, "lab": {"300750": 0.15}},
            main_positions=[{"code": "600900", "market_value": 100.0, "current_price": 27.72}],
        )
        main_path = os.path.join(data_dir, "accounts", "main.json")
        before_mtime = os.path.getmtime(main_path)
        trades_before = sorted(os.listdir(os.path.join(data_dir, "trades")))

        coord = _coord(data_dir)
        summary = coord.run_shadow_execution(date_str)

        self.assertEqual(summary["mode"], "shadow")
        self.assertTrue(summary["plan_found"])
        # main is under target (0.10 -> 0.20) -> 1 candidate; lab matches -> 0
        self.assertEqual(summary["accounts"]["main"]["candidates"], 1)
        self.assertEqual(summary["accounts"]["main"]["executed"], 0)
        self.assertEqual(summary["accounts"]["lab"]["candidates"], 0)

        # accounts untouched
        self.assertEqual(before_mtime, os.path.getmtime(main_path),
                         "accounts/main.json was modified by shadow execution!")
        self.assertEqual(trades_before, sorted(os.listdir(os.path.join(data_dir, "trades"))),
                         "trades/ was modified by shadow execution!")

        # shadow log written for main
        log_path = os.path.join(data_dir, "value_account", "logs", "exec_shadow_main.jsonl")
        self.assertTrue(os.path.exists(log_path))

    def test_g0_rejects_non_approved_plan_all_candidates(self):
        # Phase 2: a plan without decision==APPROVED -> every candidate rejected at G0.
        data_dir = tempfile.mkdtemp()
        date_str = "2026-06-05"
        _setup(
            data_dir, date_str,
            target_weights={"main": {"600900": 0.20}, "lab": {"300750": 0.15}},
            main_positions=[{"code": "600900", "market_value": 100_000.0, "current_price": 27.72}],
            decision="REJECTED", main_total_value=1_000_000.0,
        )
        coord = _coord(data_dir)
        summary = coord.run_shadow_execution(date_str)
        main = summary["accounts"]["main"]
        # diff still produces the candidate, but it's rejected at G0
        self.assertEqual(main["candidates"], 1)
        self.assertEqual(main["executed"], 0)
        self.assertTrue(all(d["gate"] == "G0" and d["verdict"] == "reject"
                            for d in main["decisions"]))
        self.assertEqual(main["counts"]["reject"], 1)

    def test_approved_plan_flows_through_gates_with_counts(self):
        # Phase 2: APPROVED + realistic NAV -> buy passes gates; summary carries counts.
        data_dir = tempfile.mkdtemp()
        date_str = "2026-06-05"
        os.makedirs(os.path.join(data_dir, "market-data"))
        with open(os.path.join(data_dir, "market-data", "watchlist.json"), "w") as f:
            json.dump({"stocks": [{"code": "600900", "sector": "电力"}]}, f)
        # active.json with loose limits so G2 does not clamp this buy
        os.makedirs(os.path.join(data_dir, "strategies"))
        with open(os.path.join(data_dir, "strategies", "active.json"), "w") as f:
            json.dump({"main_strategy": {"rules": {"position_sizing": {
                "max_single_position": 0.5, "max_sector_exposure": 0.5,
                "total_position_limit": 0.9}}}}, f)
        _setup(
            data_dir, date_str,
            target_weights={"main": {"600900": 0.20}, "lab": {}},
            main_positions=[{"code": "600900", "market_value": 100_000.0, "current_price": 27.72}],
            decision="APPROVED", main_total_value=1_000_000.0,
        )
        coord = _coord(data_dir)
        summary = coord.run_shadow_execution(date_str)
        self.assertIn("counts", summary)
        main = summary["accounts"]["main"]
        self.assertEqual(main["executed"], 0)
        # the 0.10 buy (100k) clears min_order_amount and is whitelisted -> pass
        self.assertEqual(main["counts"]["pass"], 1)
        self.assertEqual(main["counts"]["reject"], 0)
        # accounts untouched
        self.assertEqual(
            sorted(os.listdir(os.path.join(data_dir, "trades"))), [],
            "trades/ written in shadow!")

    def test_kill_switch_halts_all_via_coordinator(self):
        data_dir = tempfile.mkdtemp()
        date_str = "2026-06-05"
        _setup(
            data_dir, date_str,
            target_weights={"main": {"600900": 0.20}, "lab": {}},
            main_positions=[{"code": "600900", "market_value": 100_000.0, "current_price": 27.72}],
            decision="APPROVED", main_total_value=1_000_000.0,
        )
        open(os.path.join(data_dir, "EXECUTION_HALT"), "w").close()
        coord = _coord(data_dir)
        summary = coord.run_shadow_execution(date_str)
        main = summary["accounts"]["main"]
        self.assertEqual(main["executed"], 0)
        self.assertTrue(all(d["verdict"] == "halt" and d["gate"] == "G6"
                            for d in main["decisions"]))

    def test_no_plan_found_returns_empty_plan_flag(self):
        data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(data_dir, "accounts"))
        os.makedirs(os.path.join(data_dir, "agents", "workflows"))
        with open(os.path.join(data_dir, "accounts", "main.json"), "w") as f:
            json.dump({"total_value": 1000.0, "positions": []}, f)
        with open(os.path.join(data_dir, "accounts", "lab.json"), "w") as f:
            json.dump({"total_value": 500.0, "positions": []}, f)
        coord = _coord(data_dir)
        summary = coord.run_shadow_execution("2026-06-05")
        self.assertFalse(summary["plan_found"])
        self.assertEqual(summary["accounts"]["main"]["candidates"], 0)


if __name__ == "__main__":
    unittest.main()
