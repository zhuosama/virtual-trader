import os
import sys
import unittest

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


def prices():
    idx = pd.to_datetime([
        f"2026-04-{day:02d}"
        for day in range(1, 31)
        if day not in (4, 5, 11, 12, 18, 19, 25, 26)
    ])
    values = list(range(100, 100 + len(idx)))
    return pd.DataFrame(
        {
            "600519.SS": values,
            "000858.SZ": [100 + i * 0.5 for i in range(len(idx))],
            "000300.SS": [4000 + i * 5 for i in range(len(idx))],
        },
        index=idx,
    )


def strategy():
    return {
        "version": "1.0.0",
        "parameters": {
            "breakout_lookback": 3,
            "take_profit_pct": 15,
            "stop_loss_pct": 7,
            "max_single_position": 0.10,
            "min_single_position": 0.04,
            "base_position_target": 0.65,
            "time_stop_days": 7,
        },
        "rules": {
            "entry": {
                "condition": "MA20 trend confirmation",
            },
            "position_sizing": {
                "initial_position": 0.10,
                "max_single_position": 0.10,
                "total_position_limit": 0.8,
            },
        },
    }


class TestStrategySimulator(unittest.TestCase):
    def test_supported_numeric_diff_changes_metrics(self):
        from backtest.strategy_simulator import build_oos_evidence

        current = strategy()
        proposal = {
            "account": "main",
            "diff": [
                {"path": "main_strategy.parameters.take_profit_pct", "old": 15, "new": 3},
            ],
        }
        watchlist = {
            "stocks": [
                {"code": "600519", "name": "贵州茅台", "tag": "main"},
                {"code": "000858", "name": "五粮液", "tag": "main"},
            ]
        }
        window = {
            "status": "OK",
            "start": "2026-04-01",
            "end": "2026-04-30",
            "trading_days": len(prices()),
        }
        evidence = build_oos_evidence(current, proposal, watchlist, prices(), window)

        self.assertEqual(evidence["status"], "OK")
        self.assertIn("current", evidence)
        self.assertIn("proposed", evidence)
        self.assertNotEqual(evidence["current"]["total_ret"], evidence["proposed"]["total_ret"])

    def test_unsupported_prose_diff_returns_infra_error(self):
        from backtest.strategy_simulator import build_oos_evidence

        proposal = {
            "account": "main",
            "diff": [
                {
                    "path": "main_strategy.rules.entry.condition",
                    "old": "MA20",
                    "new": "MA20 + commodity",
                }
            ],
        }
        evidence = build_oos_evidence(
            strategy(),
            proposal,
            {"stocks": []},
            prices(),
            {"status": "OK", "start": "2026-04-01", "end": "2026-04-30", "trading_days": 20},
        )

        self.assertEqual(evidence["status"], "INFRA_ERROR")
        self.assertEqual(evidence["reason"], "UNSUPPORTED_STRATEGY_DIFF")
        self.assertEqual(evidence["unsupported_paths"], ["main_strategy.rules.entry.condition"])

    def test_supported_rootless_paths_are_accepted(self):
        from backtest.strategy_simulator import apply_supported_diff, unsupported_diff_paths

        diff = [
            {"path": "parameters.take_profit_pct", "old": 15, "new": 3},
            {"path": "rules.position_sizing.initial_position", "old": 0.10, "new": 0.05},
        ]

        self.assertEqual(unsupported_diff_paths(diff), [])
        updated = apply_supported_diff(strategy(), diff)
        self.assertEqual(updated["parameters"]["take_profit_pct"], 3)
        self.assertEqual(updated["rules"]["position_sizing"]["initial_position"], 0.05)

    def test_unsupported_numeric_market_data_diff_returns_infra_error(self):
        from backtest.strategy_simulator import build_oos_evidence

        proposal = {
            "account": "main",
            "diff": [
                {"path": "main_strategy.parameters.min_roe", "old": 12, "new": 14},
                {"path": "main_strategy.parameters.volume_ratio_threshold", "old": 1.4, "new": 1.6},
                {"path": "main_strategy.rules.commodity_stock_rules.data_tracking", "old": {}, "new": {}},
            ],
        }
        evidence = build_oos_evidence(
            strategy(),
            proposal,
            {"stocks": []},
            prices(),
            {"status": "OK", "start": "2026-04-01", "end": "2026-04-30", "trading_days": 20},
        )

        self.assertEqual(evidence["status"], "INFRA_ERROR")
        self.assertEqual(evidence["reason"], "UNSUPPORTED_STRATEGY_DIFF")
        self.assertEqual(
            evidence["unsupported_paths"],
            [
                "main_strategy.parameters.min_roe",
                "main_strategy.parameters.volume_ratio_threshold",
                "main_strategy.rules.commodity_stock_rules.data_tracking",
            ],
        )


if __name__ == "__main__":
    unittest.main()
