#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 · G7 real execution writer (coordinator._execute_orders).

Converts surviving pass/clamp orders into real trades for canary/live: lot
rounding, A-share buy/sell fee model (buys have NO stamp tax), cash guard,
position/avg_cost updates, trade-file append, atomic account write.

ALL writes go to a tempfile.mkdtemp() data_dir — NEVER the real accounts/trades.
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


def _write_account(data_dir, acct, account):
    os.makedirs(os.path.join(data_dir, "accounts"), exist_ok=True)
    with open(os.path.join(data_dir, "accounts", f"{acct}.json"), "w") as f:
        json.dump(account, f)


def _read_account(data_dir, acct):
    with open(os.path.join(data_dir, "accounts", f"{acct}.json")) as f:
        return json.load(f)


class TestExecuteOrdersBuy(unittest.TestCase):

    def test_buy_creates_position_with_no_stamp_tax(self):
        data_dir = tempfile.mkdtemp()
        account = {
            "cash": 100000.0, "positions": [],
            "initial_capital": 100000.0, "total_value": 100000.0,
            "portfolio_market_value": 0.0, "trade_count": 0,
        }
        _write_account(data_dir, "main", account)
        c = _coord(data_dir)
        orders = [{"account": "main", "code": "600900", "side": "buy",
                   "est_amount": 10000.0, "price": 10.0, "name": "长江电力"}]
        prices = {"600900": 10.0}
        state = {"total_value": 100000.0, "positions": [],
                 "cash": 100000.0}
        result = c._execute_orders("main", orders, prices, account)

        self.assertEqual(len(result["trades"]), 1)
        t = result["trades"][0]
        self.assertEqual(t["action"], "buy")
        self.assertEqual(t["shares"], 1000)  # 10000/10 = 1000, lot-aligned
        self.assertEqual(t["stamp_tax"], 0)  # A-share buys: NO stamp tax
        self.assertGreater(t["commission"], 0)
        self.assertGreater(t["transfer_fee"], 0)
        self.assertEqual(t["source"], "autonomous_exec")
        self.assertEqual(t["execution_type"], "executed")
        # net_amount = amount + fees for a buy
        self.assertAlmostEqual(
            t["net_amount"], t["amount"] + t["commission"] + t["transfer_fee"], places=2)

        # account written: cash down, position added
        acct = _read_account(data_dir, "main")
        pos = next(p for p in acct["positions"] if p["code"] == "600900")
        self.assertEqual(pos["shares"], 1000)
        self.assertAlmostEqual(pos["avg_cost"], 10.0, places=4)
        self.assertLess(acct["cash"], 100000.0)
        self.assertAlmostEqual(
            acct["cash"], 100000.0 - t["net_amount"], places=2)

        # trade file written
        today_files = os.listdir(os.path.join(data_dir, "trades"))
        self.assertTrue(today_files)

    def test_buy_skips_when_rounds_to_zero_shares(self):
        data_dir = tempfile.mkdtemp()
        account = {"cash": 100000.0, "positions": [], "initial_capital": 100000.0,
                   "total_value": 100000.0, "portfolio_market_value": 0.0}
        _write_account(data_dir, "main", account)
        c = _coord(data_dir)
        # est 500 / price 10 = 50 shares < 100 lot → rounds to 0 → skip
        orders = [{"account": "main", "code": "600900", "side": "buy",
                   "est_amount": 500.0, "price": 10.0}]
        result = c._execute_orders("main", orders, {"600900": 10.0}, account)
        self.assertEqual(len(result["trades"]), 0)
        acct = _read_account(data_dir, "main")
        self.assertEqual(acct["cash"], 100000.0)

    def test_buy_clamps_shares_when_cash_insufficient(self):
        data_dir = tempfile.mkdtemp()
        # only enough cash for ~500 shares; order asks 1000
        account = {"cash": 5200.0, "positions": [], "initial_capital": 100000.0,
                   "total_value": 5200.0, "portfolio_market_value": 0.0}
        _write_account(data_dir, "main", account)
        c = _coord(data_dir)
        orders = [{"account": "main", "code": "600900", "side": "buy",
                   "est_amount": 10000.0, "price": 10.0}]
        result = c._execute_orders("main", orders, {"600900": 10.0}, account)
        self.assertEqual(len(result["trades"]), 1)
        t = result["trades"][0]
        self.assertLessEqual(t["shares"], 500)
        self.assertEqual(t["shares"] % 100, 0)
        acct = _read_account(data_dir, "main")
        self.assertGreaterEqual(acct["cash"], 0)

    def test_buy_adds_to_existing_position_recomputes_avg_cost(self):
        data_dir = tempfile.mkdtemp()
        account = {
            "cash": 100000.0,
            "positions": [{"code": "600900", "shares": 1000, "avg_cost": 8.0,
                           "current_price": 10.0, "market_value": 10000.0}],
            "initial_capital": 100000.0, "total_value": 110000.0,
            "portfolio_market_value": 10000.0,
        }
        _write_account(data_dir, "main", account)
        c = _coord(data_dir)
        orders = [{"account": "main", "code": "600900", "side": "buy",
                   "est_amount": 10000.0, "price": 10.0}]
        c._execute_orders("main", orders, {"600900": 10.0}, account)
        acct = _read_account(data_dir, "main")
        pos = next(p for p in acct["positions"] if p["code"] == "600900")
        self.assertEqual(pos["shares"], 2000)
        # avg_cost between 8 and 10: (1000*8 + 1000*10)/2000 = 9.0
        self.assertAlmostEqual(pos["avg_cost"], 9.0, places=4)


class TestExecuteOrdersSell(unittest.TestCase):

    def test_sell_mirrors_reduce_only_fees_with_stamp_tax(self):
        data_dir = tempfile.mkdtemp()
        account = {
            "cash": 1000.0,
            "positions": [{"code": "600900", "shares": 1000, "avg_cost": 8.0,
                           "current_price": 10.0, "market_value": 10000.0}],
            "initial_capital": 100000.0, "total_value": 11000.0,
            "portfolio_market_value": 10000.0, "trade_count": 0,
        }
        _write_account(data_dir, "main", account)
        c = _coord(data_dir)
        orders = [{"account": "main", "code": "600900", "side": "sell",
                   "est_amount": 5000.0, "price": 10.0}]
        result = c._execute_orders("main", orders, {"600900": 10.0}, account)
        self.assertEqual(len(result["trades"]), 1)
        t = result["trades"][0]
        self.assertEqual(t["action"], "sell")
        self.assertEqual(t["shares"], 500)  # 5000/10
        self.assertGreater(t["stamp_tax"], 0)  # sells DO pay stamp tax
        amount = 10.0 * 500
        self.assertAlmostEqual(t["commission"], round(max(amount * 0.0003, 5), 2), places=2)
        self.assertAlmostEqual(t["stamp_tax"], round(amount * 0.001, 2), places=2)
        self.assertAlmostEqual(t["transfer_fee"], round(amount * 0.00002, 2), places=2)
        # net_amount = amount - fees for a sell
        self.assertAlmostEqual(
            t["net_amount"], amount - t["commission"] - t["stamp_tax"] - t["transfer_fee"], places=2)
        self.assertIn("realized_pnl", t)

        acct = _read_account(data_dir, "main")
        pos = next(p for p in acct["positions"] if p["code"] == "600900")
        self.assertEqual(pos["shares"], 500)
        self.assertGreater(acct["cash"], 1000.0)

    def test_sell_removes_position_when_fully_sold(self):
        data_dir = tempfile.mkdtemp()
        account = {
            "cash": 1000.0,
            "positions": [{"code": "600900", "shares": 500, "avg_cost": 8.0,
                           "current_price": 10.0, "market_value": 5000.0}],
            "initial_capital": 100000.0, "total_value": 6000.0,
            "portfolio_market_value": 5000.0,
        }
        _write_account(data_dir, "main", account)
        c = _coord(data_dir)
        orders = [{"account": "main", "code": "600900", "side": "sell",
                   "est_amount": 5000.0, "price": 10.0}]
        c._execute_orders("main", orders, {"600900": 10.0}, account)
        acct = _read_account(data_dir, "main")
        self.assertFalse(any(p["code"] == "600900" for p in acct["positions"]))

    def test_sell_clamped_to_held_shares(self):
        data_dir = tempfile.mkdtemp()
        account = {
            "cash": 1000.0,
            "positions": [{"code": "600900", "shares": 300, "avg_cost": 8.0,
                           "current_price": 10.0, "market_value": 3000.0}],
            "initial_capital": 100000.0, "total_value": 4000.0,
            "portfolio_market_value": 3000.0,
        }
        _write_account(data_dir, "main", account)
        c = _coord(data_dir)
        orders = [{"account": "main", "code": "600900", "side": "sell",
                   "est_amount": 9999.0, "price": 10.0}]
        result = c._execute_orders("main", orders, {"600900": 10.0}, account)
        t = result["trades"][0]
        self.assertEqual(t["shares"], 300)  # cannot sell more than held


class TestExecuteOrdersSnapshot(unittest.TestCase):

    def test_returns_account_snapshot_and_appends_trade_file(self):
        data_dir = tempfile.mkdtemp()
        account = {"cash": 100000.0, "positions": [], "initial_capital": 100000.0,
                   "total_value": 100000.0, "portfolio_market_value": 0.0}
        _write_account(data_dir, "main", account)
        c = _coord(data_dir)
        orders = [{"account": "main", "code": "600900", "side": "buy",
                   "est_amount": 10000.0, "price": 10.0}]
        result = c._execute_orders("main", orders, {"600900": 10.0}, account)
        self.assertIn("snapshot", result)
        self.assertIn("total_value", result["snapshot"])
        # trade file structure
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        tp = os.path.join(data_dir, "trades", today[:7], f"{today}.json")
        self.assertTrue(os.path.exists(tp))
        with open(tp) as f:
            rec = json.load(f)
        self.assertEqual(rec["date"], today)
        self.assertEqual(len(rec["trades"]), 1)
        self.assertIn("main", rec["account_snapshots"])


if __name__ == "__main__":
    unittest.main()
