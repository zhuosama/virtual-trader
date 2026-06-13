#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F5: detect_deployment_stall — 资金部署停滞告警。

补 F4 的盲区：detect_iteration_stall 只在「策略停滞 + 跑输基准」时告警，
监控不到「资金部署停滞」（仓位长期远低于 total_position_floor）。2026-06-08→
06-12 主账户卡在 ~16-17%（下限 50%）整整 5 个交易日却无人被告警，正是此盲区。

纯函数，most-recent-last。仓位读数为百分数（17.4），floor 为小数（0.50）。
"""

import json
import os
import sys
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from strategy_maintainer import detect_deployment_stall  # noqa: E402
from coordinator import MultiAgentCoordinator  # noqa: E402


def _coordinator(data_dir):
    c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
    c.data_dir = data_dir
    return c


def _seed(data_dir, position_pcts, floor=0.50):
    strat = os.path.join(data_dir, "strategies")
    os.makedirs(strat, exist_ok=True)
    perf = [{"date": f"2026-06-{8+i:02d}", "main_position_pct": p}
            for i, p in enumerate(position_pcts)]
    with open(os.path.join(strat, "performance_history.json"), "w") as f:
        json.dump(perf, f)
    active = {"main_strategy": {"rules": {"position_sizing": {}}}}
    if floor is not None:
        active["main_strategy"]["rules"]["position_sizing"]["total_position_floor"] = floor
    with open(os.path.join(strat, "active.json"), "w") as f:
        json.dump(active, f)


class TestDeploymentStall(unittest.TestCase):

    def test_fires_when_five_days_below_floor_minus_margin(self):
        # 6/8-6/12 形态：连续 5 日 ~16-17%，下限 50%，阈值 45%。
        hist = [16.0, 17.0, 16.5, 17.4, 16.8]
        msg = detect_deployment_stall(hist, floor=0.50)
        self.assertIsNotNone(msg)
        self.assertIn("资金部署停滞", msg)

    def test_silent_when_any_day_at_or_above_threshold(self):
        # 其中一天回到 46%（≥ 阈值 45）→ 不算连续停滞。
        hist = [16.0, 46.0, 16.5, 17.4, 16.8]
        self.assertIsNone(detect_deployment_stall(hist, floor=0.50))

    def test_silent_when_history_shorter_than_min_stall_days(self):
        hist = [16.0, 17.0, 16.5]  # 只有 3 天
        self.assertIsNone(detect_deployment_stall(hist, floor=0.50))

    def test_silent_when_floor_not_configured(self):
        hist = [16.0, 17.0, 16.5, 17.4, 16.8]
        self.assertIsNone(detect_deployment_stall(hist, floor=None))

    def test_silent_when_at_floor(self):
        # 恰好达到下限 50%（> 阈值 45）→ 沉默。
        hist = [50.0, 50.0, 50.0, 50.0, 50.0]
        self.assertIsNone(detect_deployment_stall(hist, floor=0.50))

    def test_none_readings_filtered_then_fires(self):
        # None（离线日）被过滤；剩余有效读数 ≥5 且全部低于阈值 → 告警。
        hist = [None, 16.0, 17.0, None, 16.5, 17.4, 16.8]
        self.assertIsNotNone(detect_deployment_stall(hist, floor=0.50))

    def test_silent_when_too_few_valid_after_filtering_none(self):
        hist = [None, 16.0, None, 17.0, None]  # 只剩 2 个有效读数
        self.assertIsNone(detect_deployment_stall(hist, floor=0.50))

    def test_margin_band_just_below_threshold_fires(self):
        # 阈值 = 50 - 5 = 45；连续 44% 仍触发（低于 45）。
        hist = [44.0, 44.0, 44.0, 44.0, 44.0]
        self.assertIsNotNone(detect_deployment_stall(hist, floor=0.50))

    def test_margin_band_just_above_threshold_silent(self):
        # 连续 45% 恰好等于阈值（≥）→ 沉默（margin 容忍无信号低仓）。
        hist = [45.0, 45.0, 45.0, 45.0, 45.0]
        self.assertIsNone(detect_deployment_stall(hist, floor=0.50))


class TestDeploymentStallWiring(unittest.TestCase):
    """接线集成：_deployment_stall_warning 读 performance_history + active.json
    的 floor，全链路驱动 detect_deployment_stall。抓字段名/floor 路径回归。"""

    def test_warning_fires_from_real_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp, [16.0, 17.0, 16.5, 17.4, 16.8], floor=0.50)
            msg = _coordinator(tmp)._deployment_stall_warning()
            self.assertIsNotNone(msg)
            self.assertIn("资金部署停滞", msg)

    def test_silent_when_floor_absent_in_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp, [16.0, 17.0, 16.5, 17.4, 16.8], floor=None)
            self.assertIsNone(_coordinator(tmp)._deployment_stall_warning())

    def test_silent_when_deployed_above_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(tmp, [55.0, 56.0, 54.0, 55.5, 57.0], floor=0.50)
            self.assertIsNone(_coordinator(tmp)._deployment_stall_warning())

    def test_swallows_missing_files_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 无 strategies/ 目录 → 防御式吞掉，返回 None，不抛。
            self.assertIsNone(_coordinator(tmp)._deployment_stall_warning())


if __name__ == "__main__":
    unittest.main()
