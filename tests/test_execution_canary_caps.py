#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 · G7 canary caps (apply_canary_caps).

Pure function: clamps per-order size, limits buy/sell counts, and blocks once
cumulative turnover exceeds the daily cap. Only applies in canary mode; live
skips these caps; shadow never executes. No filesystem writes here.
"""

import os
import sys
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from execution_model import ExecutionModel  # noqa: E402


CANARY_CFG = {"per_order": 0.02, "max_buys": 1, "max_sells": 1, "daily_turnover": 0.04}


def _model():
    return ExecutionModel(data_dir="/nonexistent", exec_config={"mode": "canary"}, mode="canary")


class TestApplyCanaryCaps(unittest.TestCase):

    def test_per_order_clamp_to_2pct_nav(self):
        m = _model()
        state = {"total_value": 100000.0, "positions": []}
        orders = [{"account": "main", "code": "600900", "side": "buy",
                   "est_amount": 5000.0, "price": 10.0}]
        decisions = m.apply_canary_caps(orders, state, CANARY_CFG)
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual(d["verdict"], "clamp")
        self.assertEqual(d["gate"], "G7")
        # 2% of 100000 = 2000
        self.assertAlmostEqual(d["order"]["est_amount"], 2000.0, places=6)

    def test_within_per_order_passes_through(self):
        m = _model()
        state = {"total_value": 100000.0, "positions": []}
        orders = [{"account": "main", "code": "600900", "side": "buy",
                   "est_amount": 1500.0, "price": 10.0}]
        decisions = m.apply_canary_caps(orders, state, CANARY_CFG)
        self.assertEqual(decisions[0]["verdict"], "pass")
        self.assertAlmostEqual(decisions[0]["order"]["est_amount"], 1500.0, places=6)

    def test_max_buys_cap_rejects_extra_buys(self):
        m = _model()
        state = {"total_value": 100000.0, "positions": []}
        # two buys, max_buys=1 → the smaller one rejected
        orders = [
            {"account": "main", "code": "A", "side": "buy", "est_amount": 1800.0, "price": 10.0},
            {"account": "main", "code": "B", "side": "buy", "est_amount": 900.0, "price": 10.0},
        ]
        decisions = m.apply_canary_caps(orders, state, CANARY_CFG)
        by_code = {d["order"]["code"]: d for d in decisions}
        self.assertEqual(by_code["A"]["verdict"], "pass")
        self.assertEqual(by_code["B"]["verdict"], "reject")
        self.assertIn("max_buys", by_code["B"]["reason"])

    def test_max_sells_cap_rejects_extra_sells(self):
        m = _model()
        state = {"total_value": 100000.0, "positions": []}
        orders = [
            {"account": "main", "code": "A", "side": "sell", "est_amount": 1800.0, "price": 10.0},
            {"account": "main", "code": "B", "side": "sell", "est_amount": 900.0, "price": 10.0},
        ]
        decisions = m.apply_canary_caps(orders, state, CANARY_CFG)
        by_code = {d["order"]["code"]: d for d in decisions}
        self.assertEqual(by_code["A"]["verdict"], "pass")
        self.assertEqual(by_code["B"]["verdict"], "reject")
        self.assertIn("max_sells", by_code["B"]["reason"])

    def test_daily_turnover_cap_rejects_once_exceeded(self):
        m = _model()
        # daily_turnover 0.04 of 100000 = 4000. Allow many buys via large max_buys.
        cfg = {"per_order": 0.05, "max_buys": 10, "max_sells": 10, "daily_turnover": 0.04}
        state = {"total_value": 100000.0, "positions": []}
        orders = [
            {"account": "main", "code": "A", "side": "buy", "est_amount": 3000.0, "price": 10.0},
            {"account": "main", "code": "B", "side": "buy", "est_amount": 3000.0, "price": 10.0},
        ]
        decisions = m.apply_canary_caps(orders, state, cfg)
        by_code = {d["order"]["code"]: d for d in decisions}
        self.assertEqual(by_code["A"]["verdict"], "pass")
        self.assertEqual(by_code["B"]["verdict"], "reject")
        self.assertIn("daily_turnover", by_code["B"]["reason"])

    def test_clamp_counts_toward_turnover(self):
        # per_order clamps A to 2000; B clamped to 2000; turnover cap 0.04*100000=4000
        # exactly fits both → both pass (boundary inclusive up to cap).
        m = _model()
        cfg = {"per_order": 0.02, "max_buys": 5, "max_sells": 5, "daily_turnover": 0.04}
        state = {"total_value": 100000.0, "positions": []}
        orders = [
            {"account": "main", "code": "A", "side": "buy", "est_amount": 9000.0, "price": 10.0},
            {"account": "main", "code": "B", "side": "buy", "est_amount": 9000.0, "price": 10.0},
        ]
        decisions = m.apply_canary_caps(orders, state, cfg)
        verdicts = {d["order"]["code"]: d["verdict"] for d in decisions}
        self.assertEqual(verdicts["A"], "clamp")
        self.assertEqual(verdicts["B"], "clamp")

    def test_all_pass_under_caps(self):
        m = _model()
        state = {"total_value": 100000.0, "positions": []}
        orders = [
            {"account": "main", "code": "A", "side": "buy", "est_amount": 1500.0, "price": 10.0},
            {"account": "main", "code": "B", "side": "sell", "est_amount": 1200.0, "price": 10.0},
        ]
        decisions = m.apply_canary_caps(orders, state, CANARY_CFG)
        self.assertTrue(all(d["verdict"] == "pass" for d in decisions))


if __name__ == "__main__":
    unittest.main()
