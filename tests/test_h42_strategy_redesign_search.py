#!/usr/bin/env python3
"""Regression tests for H42 strategy redesign helpers."""

import math
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from h42_strategy_redesign_search import (  # noqa: E402
    FeatureCache,
    HS300_TICKER,
    Overlay,
    Params,
    compute_acceptance_gate,
    evaluate_candidate_multi_window,
    is_missing,
    passes_overlay,
)


class TestH42FeatureCache(unittest.TestCase):
    def test_missing_values_include_pandas_nan(self):
        self.assertTrue(is_missing(None))
        self.assertTrue(is_missing(float("nan")))
        self.assertTrue(is_missing(pd.NA))
        self.assertFalse(is_missing(1.0))

    def test_price_and_relative_overlays_are_point_in_time(self):
        dates = pd.bdate_range("2025-01-01", periods=25)
        stock = [100 + i for i in range(25)]
        hs300 = [4000 + i * 5 for i in range(25)]
        prices = pd.DataFrame({
            "AAA.SS": stock,
            HS300_TICKER: hs300,
        }, index=dates)
        fc = FeatureCache(prices, HS300_TICKER)

        self.assertTrue(passes_overlay(fc, 24, "AAA.SS", Overlay("price_gt_ma20", ma_window=20)))
        self.assertTrue(passes_overlay(fc, 24, "AAA.SS", Overlay("rel20_ge_0", rel20_min=0.0)))

        prices.loc[dates[-1], "AAA.SS"] = math.nan
        fc = FeatureCache(prices, HS300_TICKER)
        self.assertFalse(passes_overlay(fc, 24, "AAA.SS", Overlay("price_gt_ma20", ma_window=20)))


class TestH42AcceptanceGate(unittest.TestCase):
    def _window(self, total_return=0.02, excess_return=0.01,
                blocked=False, warnings=None, trades=35,
                streak=1, max_drawdown=-0.02):
        return {
            "metrics": {
                "total_return": total_return,
                "excess_return": excess_return,
                "trade_count": trades,
                "max_drawdown": max_drawdown,
            },
            "execution_blocked": blocked,
            "execution_warnings": warnings or [],
            "terminal_losing_streak": streak,
        }

    def test_acceptance_gate_passes_only_when_all_conditions_hold(self):
        windows = {
            "cal_2024": self._window(),
            "h1_2025": self._window(),
            "h2_2025": self._window(),
            "ytd_2026": self._window(),
            "deploy_2025_2026": self._window(total_return=0.08, excess_return=0.02),
        }
        metrics, passes = compute_acceptance_gate(windows)

        self.assertTrue(passes)
        self.assertEqual(metrics["positive_windows"], 5)
        self.assertEqual(metrics["unblocked_windows"], 5)
        self.assertEqual(metrics["beat_hs300_windows"], 5)

    def test_acceptance_gate_blocks_benchmark_lag_and_weak_windows(self):
        windows = {
            "cal_2024": self._window(total_return=0.01, excess_return=-0.10, blocked=True, trades=20),
            "h1_2025": self._window(total_return=-0.01, excess_return=-0.03, blocked=True, trades=5),
            "h2_2025": self._window(total_return=0.01, excess_return=-0.17, blocked=True, trades=3),
            "ytd_2026": self._window(total_return=-0.01, excess_return=-0.02, blocked=True, trades=0),
            "deploy_2025_2026": self._window(total_return=0.10, excess_return=-0.15, blocked=False, trades=34, streak=0),
        }
        metrics, passes = compute_acceptance_gate(windows)

        self.assertFalse(passes)
        self.assertEqual(metrics["positive_windows"], 3)
        self.assertEqual(metrics["unblocked_windows"], 1)
        self.assertEqual(metrics["beat_hs300_windows"], 0)
        self.assertLess(metrics["deploy_excess_return"], 0)


class TestH42MultiWindowEvaluation(unittest.TestCase):
    class _Source:
        def get_price_universe(self, start, end):
            return ["AAA.SS"]

        def get_price_history(self, tickers, start, end):
            dates = pd.bdate_range(start, periods=5)
            return pd.DataFrame({
                "AAA.SS": [100, 101, 102, 103, 104],
                HS300_TICKER: [4000, 4010, 4020, 4030, 4040],
            }, index=dates)

    def _result(self, start, end):
        is_deploy = start == "2025-01-01" and end == "2026-05-21"
        blocked = not is_deploy
        return {
            "metrics": {
                "total_return": 0.08 if is_deploy else 0.01,
                "sharpe_ratio": 1.2,
                "max_drawdown": -0.02,
                "hs300_return": 0.03,
                "excess_return": 0.02 if is_deploy else -0.01,
                "trade_count": 35 if is_deploy else 5,
            },
            "execution_blocked": blocked,
            "execution_warnings": [],
            "execution_blockers": [] if not blocked else ["insufficient_trades: 5 < 30"],
            "terminal_losing_streak": 1,
        }

    def test_evaluate_candidate_multi_window_returns_deploy_window(self):
        params = Params(
            top_n=8,
            max_position_pct=0.08,
            stop_loss_pct=0.08,
            take_profit_pct=0.25,
            quality_filter=0.30,
            rebalance_freq_days=63,
        )

        def fake_backtest(*args, **kwargs):
            return self._result(args[3], args[4])

        with patch("h42_strategy_redesign_search.run_overlay_backtest", side_effect=fake_backtest):
            result = evaluate_candidate_multi_window(
                self._Source(), params, Overlay("none"), {}, 300000)

        self.assertIn("deploy_window", result)
        self.assertEqual(result["deploy_window"]["metrics"]["trade_count"], 35)
        self.assertFalse(result["passes_acceptance_gate"])
        self.assertEqual(result["gate_metrics"]["unblocked_windows"], 1)


if __name__ == "__main__":
    unittest.main()
