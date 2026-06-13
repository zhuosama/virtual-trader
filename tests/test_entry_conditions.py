#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F2b: 真实入场过滤的纯函数层 — compute_ma + passes_entry。

替换 execution_planner._check_entry_conditions 永远 return True 的 stub。
纯函数无 I/O：compute_ma 从收盘价序列算均线，passes_entry 用注入的指标判断
MA5>MA20 趋势 + 成交额流动性门，fail-closed（缺指标即拒）。

数据层（新浪日线 fetch + loader）单独测/冒烟；基本面（ROE/负债率，需 PIT）属
F2b-later，不在本纯函数层。
"""

import os
import sys
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from execution_planner import ExecutionPlannerAgent, compute_ma, passes_entry  # noqa: E402


def _planner():
    return ExecutionPlannerAgent.__new__(ExecutionPlannerAgent)


def _uptrend_kline(volume=1e8):
    # 收盘 1..25 升序；ma5=23, ma20=15.5 → MA5>MA20。
    return [{"day": str(i), "close": float(i), "volume": volume} for i in range(1, 26)]


class TestComputeMa(unittest.TestCase):

    def test_simple_average_of_last_window(self):
        closes = [10, 20, 30, 40, 50]
        self.assertAlmostEqual(compute_ma(closes, 5), 30.0)

    def test_uses_only_last_window_values(self):
        closes = [1, 1, 10, 20, 30, 40, 50]  # 末 5 个均值
        self.assertAlmostEqual(compute_ma(closes, 5), 30.0)

    def test_none_when_fewer_than_window(self):
        self.assertIsNone(compute_ma([10, 20, 30], 5))

    def test_none_on_empty(self):
        self.assertIsNone(compute_ma([], 5))
        self.assertIsNone(compute_ma(None, 5))


class TestPassesEntry(unittest.TestCase):

    def _strat(self, min_turnover=3):
        return {"parameters": {"min_turnover_billion": min_turnover}}

    def test_passes_when_uptrend_and_liquid(self):
        ind = {"ma5": 28.0, "ma20": 26.0, "amount_yi": 50.0}
        self.assertTrue(passes_entry(ind, self._strat()))

    def test_rejects_when_ma5_below_ma20(self):
        ind = {"ma5": 25.0, "ma20": 26.0, "amount_yi": 50.0}
        self.assertFalse(passes_entry(ind, self._strat()))

    def test_rejects_when_ma5_equals_ma20(self):
        # 需严格 MA5>MA20（趋势确认，非横盘）。
        ind = {"ma5": 26.0, "ma20": 26.0, "amount_yi": 50.0}
        self.assertFalse(passes_entry(ind, self._strat()))

    def test_rejects_when_amount_below_threshold(self):
        ind = {"ma5": 28.0, "ma20": 26.0, "amount_yi": 2.5}  # <3亿
        self.assertFalse(passes_entry(ind, self._strat()))

    def test_amount_at_threshold_passes(self):
        ind = {"ma5": 28.0, "ma20": 26.0, "amount_yi": 3.0}  # 恰好 3亿
        self.assertTrue(passes_entry(ind, self._strat()))

    def test_fail_closed_when_any_indicator_missing(self):
        base = {"ma5": 28.0, "ma20": 26.0, "amount_yi": 50.0}
        for missing in ("ma5", "ma20", "amount_yi"):
            ind = dict(base)
            ind[missing] = None
            self.assertFalse(passes_entry(ind, self._strat()), missing)
        # 完全缺字段也 fail-closed
        self.assertFalse(passes_entry({}, self._strat()))
        self.assertFalse(passes_entry(None, self._strat()))

    def test_threshold_read_from_strategy(self):
        # 把流动性门提到 60亿 → 50亿成交额被拒。
        ind = {"ma5": 28.0, "ma20": 26.0, "amount_yi": 50.0}
        self.assertFalse(passes_entry(ind, self._strat(min_turnover=60)))

    def test_default_threshold_when_strategy_silent(self):
        # 策略未配 min_turnover_billion → 默认 3亿。
        ind = {"ma5": 28.0, "ma20": 26.0, "amount_yi": 4.0}
        self.assertTrue(passes_entry(ind, {"parameters": {}}))
        self.assertTrue(passes_entry(ind, {}))


class TestParseSinaKline(unittest.TestCase):

    def test_parses_close_and_volume(self):
        raw = ('[{"day":"2026-06-12","open":"10","high":"11","low":"9",'
               '"close":"10.5","volume":"1000000"}]')
        rows = _planner()._parse_sina_kline(raw)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["close"], 10.5)
        self.assertAlmostEqual(rows[0]["volume"], 1000000.0)

    def test_bad_json_returns_empty(self):
        self.assertEqual(_planner()._parse_sina_kline("not json"), [])
        self.assertEqual(_planner()._parse_sina_kline(""), [])
        self.assertEqual(_planner()._parse_sina_kline("null"), [])


class TestComputeIndicators(unittest.TestCase):

    def _planner_with(self, kline):
        p = _planner()
        p._fetch_daily_kline = lambda code, n=25: kline
        return p

    def test_indicators_from_uptrend(self):
        ind = self._planner_with(_uptrend_kline())._compute_indicators("600519")
        self.assertIsNotNone(ind)
        self.assertAlmostEqual(ind["ma5"], 23.0)        # (21+..+25)/5
        self.assertAlmostEqual(ind["ma20"], 15.5)       # (6+..+25)/20
        self.assertGreater(ind["ma5"], ind["ma20"])
        # amount_yi = close*volume/1e8 = 25*1e8/1e8 = 25亿
        self.assertAlmostEqual(ind["amount_yi"], 25.0)

    def test_none_when_insufficient_history(self):
        short = [{"day": str(i), "close": float(i), "volume": 1e8} for i in range(1, 15)]
        self.assertIsNone(self._planner_with(short)._compute_indicators("x"))

    def test_caches_per_code(self):
        calls = []
        p = _planner()
        p._fetch_daily_kline = lambda code, n=25: (calls.append(code) or _uptrend_kline())
        p._compute_indicators("600519")
        p._compute_indicators("600519")
        self.assertEqual(len(calls), 1)  # 第二次命中缓存，不重复 fetch


class TestCheckEntryConditions(unittest.TestCase):
    """改造后的 _check_entry_conditions：真实指标 + fail-closed（替换 stub）。"""

    def test_uptrend_liquid_passes(self):
        p = _planner()
        p._fetch_daily_kline = lambda code, n=25: _uptrend_kline()
        self.assertTrue(
            p._check_entry_conditions({"code": "600519"}, {"parameters": {}}, []))

    def test_fail_closed_when_fetch_empty(self):
        p = _planner()
        p._fetch_daily_kline = lambda code, n=25: []  # 取数失败 → 拒绝
        self.assertFalse(p._check_entry_conditions({"code": "600519"}, {}, []))

    def test_downtrend_rejected(self):
        p = _planner()
        # 收盘 25..1 降序 → MA5<MA20。
        p._fetch_daily_kline = lambda code, n=25: [
            {"day": str(i), "close": float(26 - i), "volume": 1e8} for i in range(1, 26)]
        self.assertFalse(p._check_entry_conditions({"code": "600519"}, {}, []))

    def test_illiquid_rejected(self):
        p = _planner()
        # 上升趋势但成交额极低（volume 小）→ 流动性门拒绝。
        p._fetch_daily_kline = lambda code, n=25: _uptrend_kline(volume=1000.0)
        self.assertFalse(p._check_entry_conditions({"code": "600519"}, {}, []))


if __name__ == "__main__":
    unittest.main()
