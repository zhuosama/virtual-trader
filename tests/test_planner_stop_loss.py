#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F1 fix: ExecutionPlannerAgent buy actions must carry a stop_loss.

The 2026-06-10 deadlock: planner-generated buy actions had no `stop_loss`
key, so RiskControllerAgent._check_stop_loss_rules forced the whole plan to
decision==MODIFY, and the G0 execution gate then rejected every candidate
('decision!=APPROVED'). On bullish days no new name could ever be bought.

These tests pin the root-cause behavior: every generated buy action carries a
sane stop_loss fraction, so the stop-loss check passes and the plan can reach
APPROVED.
"""

import json
import os
import sys
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from execution_planner import ExecutionPlannerAgent  # noqa: E402
from risk_controller import RiskControllerAgent  # noqa: E402


def _planner(data_dir, strategies=None):
    p = ExecutionPlannerAgent.__new__(ExecutionPlannerAgent)
    p.data_dir = data_dir
    p.strategies = strategies or {}
    # 注入上升趋势日线，使标的通过 F2b 入场过滤（MA5>MA20 + 成交额>3亿），
    # 让这些测试聚焦于 stop_loss 而非入场逻辑；同时避免真实网络 fetch。
    p._fetch_daily_kline = lambda code, n=25: [
        {"day": str(i), "close": float(i), "volume": 1e8} for i in range(1, 26)
    ]
    return p


def _risk_controller():
    rc = RiskControllerAgent.__new__(RiskControllerAgent)
    rc.risk_rules = rc._load_risk_rules()
    return rc


def _write_watchlist(data_dir, stocks):
    md = os.path.join(data_dir, "market-data")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "watchlist.json"), "w", encoding="utf-8") as f:
        json.dump({"stocks": stocks}, f, ensure_ascii=False)


MAIN_STRAT = {"name": "价值趋势混合", "parameters": {"initial_position": 0.08, "stop_loss_pct": 7}}
LAB_STRAT = {"name": "动量突破", "parameters": {"initial_position": 0.15, "stop_loss_pct": 5.5}}


class TestPlannerBuyStopLoss(unittest.TestCase):

    def test_main_buy_actions_carry_stop_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_watchlist(tmp, [
                {"code": "600519", "name": "贵州茅台", "tag": "main"},
                {"code": "000858", "name": "五粮液", "tag": "main"},
            ])
            actions = _planner(tmp)._generate_main_account_actions(MAIN_STRAT, [])
            self.assertEqual(len(actions), 2)
            for a in actions:
                self.assertEqual(a["action"], "buy")
                self.assertIn("stop_loss", a)
                self.assertTrue(0 < a["stop_loss"] < 1, a["stop_loss"])
            # main stop_loss_pct 7 -> fraction 0.07
            self.assertAlmostEqual(actions[0]["stop_loss"], 0.07)

    def test_lab_buy_actions_carry_stop_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_watchlist(tmp, [
                {"code": "300124", "name": "汇川技术", "tag": "lab"},
            ])
            actions = _planner(tmp)._generate_lab_account_actions(LAB_STRAT, [])
            self.assertEqual(len(actions), 1)
            self.assertIn("stop_loss", actions[0])
            # lab stop_loss_pct 5.5 -> fraction 0.055
            self.assertAlmostEqual(actions[0]["stop_loss"], 0.055)

    def test_stop_loss_falls_back_to_account_default_when_unconfigured(self):
        # No stop_loss_pct in strategy params -> per-account default (main 0.07).
        with tempfile.TemporaryDirectory() as tmp:
            _write_watchlist(tmp, [{"code": "600900", "name": "长江电力", "tag": "main"}])
            strat = {"name": "x", "parameters": {"initial_position": 0.08}}
            actions = _planner(tmp)._generate_main_account_actions(strat, [])
            self.assertAlmostEqual(actions[0]["stop_loss"], 0.07)

    def test_planner_buy_actions_pass_risk_stop_loss_check(self):
        # The real deadlock regression: planner buy actions fed to the actual
        # RiskControllerAgent stop-loss check must pass (no MODIFY from missing
        # stop_loss), which is what unblocks the G0 gate downstream.
        with tempfile.TemporaryDirectory() as tmp:
            _write_watchlist(tmp, [
                {"code": "600519", "name": "贵州茅台", "tag": "main"},
                {"code": "000858", "name": "五粮液", "tag": "main"},
            ])
            actions = _planner(tmp)._generate_main_account_actions(MAIN_STRAT, [])
            plan = {"actions": actions}
            res = _risk_controller()._check_stop_loss_rules(plan)
            self.assertTrue(res["passed"], res.get("warnings"))
            self.assertEqual(res["warnings"], [])


if __name__ == "__main__":
    unittest.main()
