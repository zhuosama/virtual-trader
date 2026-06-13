#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F6: 中性市低仓建仓 — _generate_neutral_plan 不再无脑 hold。

验证（2026-06-13）暴露的最深结构问题：_generate_neutral_plan 只生成一个 hold，
完全不调 _generate_main_account_actions。只有 bullish 日开新仓 → 主账户从 17%→50%
只能靠 bullish 日；6/8-6/12 全中性/看跌 → 一直空仓。而 active.json 的
cash_drag_alert 明确声明「仓位低于 50% 触发信号扫描」，代码从未实现。

F6：中性市用 F2b 过滤后的候选温和建仓（total_position=0.55 上限 + canary 限速
约束），无合格候选则保持 hold（对齐 cash_drag_alert「不为执行而执行」）。
"""

import json
import os
import sys
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from execution_planner import ExecutionPlannerAgent  # noqa: E402


def _kline(closes):
    return [{"day": str(i), "close": float(c), "volume": 1e8} for i, c in enumerate(closes, 1)]


UPTREND = lambda code, n=25: _kline(list(range(1, 26)))        # MA5>MA20 → F2b PASS
DOWNTREND = lambda code, n=25: _kline(list(range(26, 1, -1)))  # MA5<MA20 → F2b FAIL

STRATS = {
    "main_strategy": {"name": "价值趋势混合",
                      "parameters": {"initial_position": 0.08, "stop_loss_pct": 7,
                                     "min_turnover_billion": 3}},
    "lab_strategy": {"name": "动量突破",
                     "parameters": {"initial_position": 0.15, "stop_loss_pct": 5.5}},
}


def _planner(data_dir, kline_fn):
    p = ExecutionPlannerAgent.__new__(ExecutionPlannerAgent)
    p.data_dir = data_dir
    p.strategies = STRATS
    p._fetch_daily_kline = kline_fn
    return p


def _seed_watchlist(tmp):
    md = os.path.join(tmp, "market-data")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, "watchlist.json"), "w", encoding="utf-8") as f:
        json.dump({"stocks": [
            {"code": "600519", "name": "贵州茅台", "tag": "main"},
            {"code": "600036", "name": "招商银行", "tag": "main"},
            {"code": "300124", "name": "汇川技术", "tag": "lab"},
        ]}, f, ensure_ascii=False)


class TestNeutralPlanDeploys(unittest.TestCase):

    def test_neutral_generates_buys_when_candidates_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_watchlist(tmp)
            plan = _planner(tmp, UPTREND)._generate_neutral_plan([], [])
            buys = [a for a in plan["actions"] if a.get("action") == "buy"]
            self.assertGreater(len(buys), 0)  # 中性市也建仓，不再无脑 hold
            for b in buys:
                self.assertIn("stop_loss", b)  # 复用 _generate_main_account_actions

    def test_neutral_holds_when_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_watchlist(tmp)
            plan = _planner(tmp, DOWNTREND)._generate_neutral_plan([], [])
            buys = [a for a in plan["actions"] if a.get("action") == "buy"]
            holds = [a for a in plan["actions"] if a.get("action") == "hold"]
            self.assertEqual(len(buys), 0)        # F2b 全拒 → 无建仓
            self.assertEqual(len(holds), 1)       # fallback：保持现状

    def test_neutral_total_position_stays_055(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_watchlist(tmp)
            for kline in (UPTREND, DOWNTREND):
                plan = _planner(tmp, kline)._generate_neutral_plan([], [])
                self.assertEqual(plan["position_sizing"]["total_position"], 0.55)
                self.assertEqual(plan["market_regime"], "neutral")


if __name__ == "__main__":
    unittest.main()
