#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1: ExecutionModel shadow skeleton (no gates).

diff_to_orders is a pure function (target_weights -> candidate orders) and
MUST be idempotent: positions already matching target -> []. execute_plan in
shadow mode writes only to value_account/ and MUST NOT touch accounts/*.json
or trades/. Tests construct ExecutionModel directly (cheap __init__) and use a
tmp data_dir for shadow-write isolation.
"""

import json
import os
import sys
import tempfile
import time
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from execution_model import ExecutionModel  # noqa: E402


def _model(data_dir=None, eps=0.005, mode="shadow"):
    if data_dir is None:
        data_dir = tempfile.mkdtemp()
    return ExecutionModel(data_dir=data_dir, exec_config={"mode": mode, "eps": eps}, mode=mode)


# positions: list of {code, market_value, ...}; total_value drives current weight.
def _positions(*pairs):
    return [{"code": c, "market_value": mv} for c, mv in pairs]


class TestDiffToOrders(unittest.TestCase):

    def test_matching_positions_returns_empty(self):
        m = _model()
        positions = _positions(("600900", 100.0), ("000333", 50.0))
        total_value = 1000.0
        # current weights: 0.10 / 0.05
        target = {"600900": 0.10, "000333": 0.05}
        prices = {"600900": 27.72, "000333": 10.0}
        orders = m.diff_to_orders(positions, target, prices, "main", total_value)
        self.assertEqual(orders, [])

    def test_empty_target_weights_never_liquidates(self):
        # No plan / empty target map = no opinion -> hold, NEVER sell-all.
        # An empty target must not be read as "everything -> 0" (liquidation).
        m = _model()
        positions = _positions(("600900", 100.0), ("000333", 50.0))
        orders = m.diff_to_orders(positions, {}, {}, "main", 1000.0)
        self.assertEqual(orders, [])

    def test_idempotent_rerun_is_empty(self):
        m = _model()
        positions = _positions(("600900", 100.0))
        total_value = 1000.0
        target = {"600900": 0.10}
        prices = {"600900": 27.72}
        first = m.diff_to_orders(positions, target, prices, "main", total_value)
        self.assertEqual(first, [])
        second = m.diff_to_orders(positions, target, prices, "main", total_value)
        self.assertEqual(second, [])

    def test_under_target_produces_buy(self):
        m = _model()
        positions = _positions(("600900", 100.0))  # current 0.10
        total_value = 1000.0
        target = {"600900": 0.20}
        prices = {"600900": 27.72}
        orders = m.diff_to_orders(positions, target, prices, "main", total_value)
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual(o["side"], "buy")
        self.assertEqual(o["code"], "600900")
        self.assertEqual(o["account"], "main")
        self.assertAlmostEqual(o["target_weight"], 0.20)
        self.assertAlmostEqual(o["delta_weight"], 0.10)
        self.assertAlmostEqual(o["est_amount"], 0.10 * total_value)

    def test_over_target_produces_sell(self):
        m = _model()
        positions = _positions(("600900", 300.0))  # current 0.30
        total_value = 1000.0
        target = {"600900": 0.10}
        prices = {"600900": 27.72}
        orders = m.diff_to_orders(positions, target, prices, "main", total_value)
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual(o["side"], "sell")
        self.assertAlmostEqual(o["delta_weight"], -0.20)
        self.assertAlmostEqual(o["est_amount"], 0.20 * total_value)

    def test_new_target_code_produces_buy(self):
        m = _model()
        positions = _positions(("600900", 100.0))
        total_value = 1000.0
        target = {"600900": 0.10, "000333": 0.08}  # 000333 not held
        prices = {"600900": 27.72, "000333": 10.0}
        orders = m.diff_to_orders(positions, target, prices, "main", total_value)
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual(o["code"], "000333")
        self.assertEqual(o["side"], "buy")
        self.assertAlmostEqual(o["target_weight"], 0.08)

    def test_dropped_code_produces_sell(self):
        m = _model()
        positions = _positions(("600900", 100.0), ("000333", 50.0))
        total_value = 1000.0
        target = {"600900": 0.10}  # 000333 dropped (target 0)
        prices = {"600900": 27.72, "000333": 10.0}
        orders = m.diff_to_orders(positions, target, prices, "main", total_value)
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual(o["code"], "000333")
        self.assertEqual(o["side"], "sell")
        self.assertAlmostEqual(o["target_weight"], 0.0)
        self.assertAlmostEqual(o["delta_weight"], -0.05)

    def test_below_eps_not_emitted(self):
        m = _model(eps=0.005)
        positions = _positions(("600900", 100.0))  # current 0.10
        total_value = 1000.0
        target = {"600900": 0.103}  # delta 0.003 < eps
        prices = {"600900": 27.72}
        orders = m.diff_to_orders(positions, target, prices, "main", total_value)
        self.assertEqual(orders, [])

    def test_zero_total_value_returns_empty(self):
        m = _model()
        orders = m.diff_to_orders([], {"600900": 0.1}, {"600900": 1.0}, "main", 0)
        self.assertEqual(orders, [])


class TestExecutePlanShadow(unittest.TestCase):

    def _setup_data_dir(self):
        data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(data_dir, "accounts"))
        os.makedirs(os.path.join(data_dir, "trades"))
        # a pre-existing accounts/main.json we will assert is untouched
        main_path = os.path.join(data_dir, "accounts", "main.json")
        with open(main_path, "w") as f:
            json.dump({"total_value": 1000.0, "positions": [
                {"code": "600900", "market_value": 100.0, "current_price": 27.72},
            ]}, f)
        return data_dir, main_path

    def test_shadow_run_writes_shadow_log_and_does_not_touch_accounts(self):
        data_dir, main_path = self._setup_data_dir()
        before_mtime = os.path.getmtime(main_path)
        trades_dir = os.path.join(data_dir, "trades")
        trades_before = sorted(os.listdir(trades_dir))

        m = _model(data_dir=data_dir, mode="shadow")
        plan = {
            "total_position": 0.8,
            "target_weights": {"600900": 0.20},  # under target -> buy
        }
        prices = {"600900": 27.72}
        account_state = {"total_value": 1000.0, "positions": [
            {"code": "600900", "market_value": 100.0, "current_price": 27.72},
        ]}
        report = m.execute_plan("main", plan, prices, account_state)

        # return shape
        self.assertEqual(report["account"], "main")
        self.assertEqual(report["mode"], "shadow")
        self.assertEqual(report["executed"], 0)
        self.assertTrue(len(report["candidates"]) >= 1)
        self.assertIn("decisions", report)

        # shadow log written
        log_path = os.path.join(data_dir, "value_account", "logs", "exec_shadow_main.jsonl")
        self.assertTrue(os.path.exists(log_path), "shadow jsonl log not written")
        with open(log_path) as f:
            lines = [json.loads(ln) for ln in f if ln.strip()]
        self.assertTrue(len(lines) >= 1)

        # shadow state snapshot written
        state_path = os.path.join(data_dir, "value_account", "reports", "exec_shadow_state.json")
        self.assertTrue(os.path.exists(state_path), "shadow state snapshot not written")

        # accounts/main.json mtime unchanged
        after_mtime = os.path.getmtime(main_path)
        self.assertEqual(before_mtime, after_mtime, "accounts/main.json was modified in shadow mode!")

        # trades/ unchanged
        self.assertEqual(trades_before, sorted(os.listdir(trades_dir)), "trades/ was modified in shadow mode!")

    def test_shadow_log_appends_across_runs(self):
        data_dir, _ = self._setup_data_dir()
        m = _model(data_dir=data_dir, mode="shadow")
        plan = {"total_position": 0.8, "target_weights": {"600900": 0.20}}
        prices = {"600900": 27.72}
        account_state = {"total_value": 1000.0, "positions": [
            {"code": "600900", "market_value": 100.0, "current_price": 27.72},
        ]}
        m.execute_plan("main", plan, prices, account_state)
        m.execute_plan("main", plan, prices, account_state)
        log_path = os.path.join(data_dir, "value_account", "logs", "exec_shadow_main.jsonl")
        with open(log_path) as f:
            lines = [ln for ln in f if ln.strip()]
        self.assertEqual(len(lines), 2, "shadow log should append one record per run")


class TestConfigLoading(unittest.TestCase):

    def test_default_mode_is_shadow_when_no_config(self):
        data_dir = tempfile.mkdtemp()
        m = ExecutionModel(data_dir=data_dir, exec_config=None, mode=None)
        self.assertEqual(m.mode, "shadow")
        self.assertAlmostEqual(m.eps, 0.005)

    def test_reads_config_execution_json(self):
        data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(data_dir, "config"))
        with open(os.path.join(data_dir, "config", "execution.json"), "w") as f:
            json.dump({"mode": "shadow", "eps": 0.01}, f)
        m = ExecutionModel(data_dir=data_dir, exec_config=None, mode=None)
        self.assertEqual(m.mode, "shadow")
        self.assertAlmostEqual(m.eps, 0.01)


if __name__ == "__main__":
    unittest.main()
