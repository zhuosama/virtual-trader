#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 · run_autonomous_execution (mode integration + H2 bookkeeping + rollback).

shadow  → no real writes, executed==0, accounts/trades untouched.
canary  → gates + canary caps + _execute_orders + bookkeeping (perf upsert +
          daily report + ledger validation), rollback on validation failure.
live    → gates + _execute_orders + bookkeeping + rollback (canary caps skipped).

ALL writes use tempfile.mkdtemp(). run_shadow_execution stays as an alias.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from coordinator import MultiAgentCoordinator  # noqa: E402


def _coord(data_dir):
    c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
    c.data_dir = data_dir
    return c


def _setup(data_dir, date_str, mode, main_target, main_positions,
           main_total_value, main_cash, decision="APPROVED"):
    for sub in ("accounts", "trades", "config", "strategies",
                os.path.join("agents", "workflows"),
                os.path.join("reports", "daily")):
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
    main = {
        "cash": main_cash,
        "positions": main_positions,
        "initial_capital": 100000.0,
        "total_value": main_total_value,
        "portfolio_market_value": sum(p.get("market_value", 0) for p in main_positions),
        "daily_pnl": 0.0,
        "daily_pnl_pct": 0.0,
        "trade_count": 0,
    }
    with open(os.path.join(data_dir, "accounts", "main.json"), "w") as f:
        json.dump(main, f)
    # lab: no target → no orders
    with open(os.path.join(data_dir, "accounts", "lab.json"), "w") as f:
        json.dump({"cash": 50000.0, "positions": [], "initial_capital": 50000.0,
                   "total_value": 50000.0, "portfolio_market_value": 0.0,
                   "daily_pnl": 0.0, "daily_pnl_pct": 0.0, "trade_count": 0}, f)
    with open(os.path.join(data_dir, "config", "execution.json"), "w") as f:
        json.dump({
            "mode": mode, "eps": 0.005, "kill_switch_path": "EXECUTION_HALT",
            "gates": {"max_orders": 5, "min_order_amount": 1000,
                      "price_deviation": 0.1, "whitelist": None},
            "canary": {"per_order": 0.5, "max_buys": 5, "max_sells": 5,
                       "daily_turnover": 1.0},
            "per_account": {},
        }, f)
    plan = {"decision": decision, "total_position": 0.8,
            "target_weights": {"main": main_target, "lab": {}}}
    wf = f"workflow_pre_market_{date_str.replace('-', '')}_010101.json"
    with open(os.path.join(data_dir, "agents", "workflows", wf), "w") as f:
        json.dump({"workflow_type": "pre_market", "plan": plan}, f)
    # empty perf history so settlement upsert has a list
    with open(os.path.join(data_dir, "strategies", "performance_history.json"), "w") as f:
        json.dump([], f)


class TestShadowMode(unittest.TestCase):

    def test_shadow_does_not_write_accounts_or_trades(self):
        data_dir = tempfile.mkdtemp()
        date_str = "2026-06-05"
        _setup(data_dir, date_str, "shadow",
               main_target={"600900": 0.20},
               main_positions=[{"code": "600900", "shares": 100, "avg_cost": 27.0,
                                "current_price": 27.72, "market_value": 2772.0}],
               main_total_value=100000.0, main_cash=97228.0)
        c = _coord(data_dir)
        main_path = os.path.join(data_dir, "accounts", "main.json")
        before = os.path.getmtime(main_path)
        trades_before = os.listdir(os.path.join(data_dir, "trades"))
        summary = c.run_autonomous_execution(date_str)
        self.assertEqual(summary["mode"], "shadow")
        # executed total == 0
        for a in summary["accounts"].values():
            self.assertEqual(a.get("executed", 0), 0)
        self.assertEqual(os.path.getmtime(main_path), before)
        self.assertEqual(os.listdir(os.path.join(data_dir, "trades")), trades_before)

    def test_run_shadow_execution_alias_still_works(self):
        data_dir = tempfile.mkdtemp()
        date_str = "2026-06-05"
        _setup(data_dir, date_str, "shadow",
               main_target={"600900": 0.20},
               main_positions=[{"code": "600900", "shares": 100, "avg_cost": 27.0,
                                "current_price": 27.72, "market_value": 2772.0}],
               main_total_value=100000.0, main_cash=97228.0)
        c = _coord(data_dir)
        summary = c.run_shadow_execution(date_str)
        self.assertEqual(summary["mode"], "shadow")
        self.assertIn("accounts", summary)


class TestCanaryMode(unittest.TestCase):

    def test_canary_writes_real_trades_and_keeps_ledger_consistent(self):
        data_dir = tempfile.mkdtemp()
        date_str = datetime.now().strftime("%Y-%m-%d")
        _setup(data_dir, date_str, "canary",
               main_target={"600900": 0.20},
               main_positions=[{"code": "600900", "shares": 100, "avg_cost": 27.0,
                                "current_price": 27.72, "market_value": 2772.0}],
               main_total_value=100000.0, main_cash=97228.0)
        c = _coord(data_dir)
        before_cash = json.load(open(os.path.join(data_dir, "accounts", "main.json")))["cash"]
        summary = c.run_autonomous_execution(date_str)
        self.assertEqual(summary["mode"], "canary")
        executed = sum(a.get("executed", 0) for a in summary["accounts"].values())
        self.assertGreater(executed, 0)
        # real trade file exists for today
        tp = os.path.join(data_dir, "trades", date_str[:7], f"{date_str}.json")
        self.assertTrue(os.path.exists(tp))
        rec = json.load(open(tp))
        self.assertTrue(any(t["source"] == "autonomous_exec" for t in rec["trades"]))
        # cash decreased (a buy happened)
        after_cash = json.load(open(os.path.join(data_dir, "accounts", "main.json")))["cash"]
        self.assertLess(after_cash, before_cash)
        # bookkeeping: perf entry + daily report for today exist, validation passed
        perf = json.load(open(os.path.join(
            data_dir, "strategies", "performance_history.json")))
        self.assertTrue(any(e["date"] == date_str for e in perf))
        self.assertTrue(os.path.exists(os.path.join(
            data_dir, "reports", "daily", f"{date_str}.md")))
        self.assertTrue(summary.get("ledger_validation_passed"))
        self.assertNotIn("degraded_reason", summary)

    def test_canary_rollback_on_ledger_validation_failure(self):
        data_dir = tempfile.mkdtemp()
        date_str = datetime.now().strftime("%Y-%m-%d")
        _setup(data_dir, date_str, "canary",
               main_target={"600900": 0.20},
               main_positions=[{"code": "600900", "shares": 100, "avg_cost": 27.0,
                                "current_price": 27.72, "market_value": 2772.0}],
               main_total_value=100000.0, main_cash=97228.0)
        c = _coord(data_dir)
        main_path = os.path.join(data_dir, "accounts", "main.json")
        pre_account = json.load(open(main_path))
        trades_dir_before = []
        tp = os.path.join(data_dir, "trades", date_str[:7], f"{date_str}.json")
        perf_path = os.path.join(data_dir, "strategies", "performance_history.json")
        pre_perf_exists = os.path.exists(perf_path)
        pre_perf = json.load(open(perf_path)) if pre_perf_exists else None

        # Force ledger validation to fail → must trigger rollback
        def _fail_validation(strict=False):
            return {"status": "fail",
                    "failures": [{"inv": "INV-3", "msg": "deliberate"}],
                    "results": []}

        with mock.patch.object(c, "_run_ledger_validation", side_effect=_fail_validation):
            summary = c.run_autonomous_execution(date_str)

        self.assertFalse(summary.get("ledger_validation_passed"))
        self.assertIn("degraded_reason", summary)
        # rollback: account restored to pre-write state
        post_account = json.load(open(main_path))
        self.assertEqual(post_account, pre_account)
        # rollback: trade file removed (it did not exist pre-write)
        self.assertFalse(os.path.exists(tp))
        # rollback: performance_history restored — no corrupted perf entry left to
        # feed G4 protections' equity series on a later run.
        self.assertEqual(os.path.exists(perf_path), pre_perf_exists)
        if pre_perf_exists:
            self.assertEqual(json.load(open(perf_path)), pre_perf)


class TestLiveMode(unittest.TestCase):

    def test_live_writes_and_skips_canary_caps(self):
        data_dir = tempfile.mkdtemp()
        date_str = datetime.now().strftime("%Y-%m-%d")
        # set canary caps absurdly tight; live must ignore them
        _setup(data_dir, date_str, "live",
               main_target={"600900": 0.20},
               main_positions=[{"code": "600900", "shares": 100, "avg_cost": 27.0,
                                "current_price": 27.72, "market_value": 2772.0}],
               main_total_value=100000.0, main_cash=97228.0)
        cfg_path = os.path.join(data_dir, "config", "execution.json")
        cfg = json.load(open(cfg_path))
        cfg["canary"] = {"per_order": 0.0001, "max_buys": 0, "max_sells": 0,
                         "daily_turnover": 0.0001}
        json.dump(cfg, open(cfg_path, "w"))
        c = _coord(data_dir)
        summary = c.run_autonomous_execution(date_str)
        self.assertEqual(summary["mode"], "live")
        executed = sum(a.get("executed", 0) for a in summary["accounts"].values())
        # caps would block everything in canary; live ignores them → executes
        self.assertGreater(executed, 0)


if __name__ == "__main__":
    unittest.main()
