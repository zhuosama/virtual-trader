import json
import os
import sys

import numpy as np
import pandas as pd


WORKTREE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, WORKTREE)

from backtest.strategy_simulator import build_oos_evidence


days = pd.date_range("2026-04-15", periods=21, freq="B")
np.random.seed(42)
trigger_path = [
    100.0,
    100.0,
    100.0,
    100.0,
    100.0,
    101.0,
    106.0,
    110.0,
    114.0,
    113.0,
    112.0,
    111.0,
    110.0,
    109.0,
    108.0,
    107.0,
    106.0,
    105.0,
    104.0,
    103.0,
    102.0,
]
prices = pd.DataFrame(
    {
        "600519.SS": trigger_path,
        "601088.SS": 100 * (1 + np.random.normal(0.001, 0.01, 21)).cumprod(),
        "000858.SZ": 100 * (1 + np.random.normal(0.0005, 0.008, 21)).cumprod(),
        "300750.SZ": 100 * (1 + np.random.normal(0.001, 0.02, 21)).cumprod(),
        "002230.SZ": 100 * (1 + np.random.normal(0.001, 0.02, 21)).cumprod(),
        "000300.SS": 100 * (1 + np.random.normal(0.0003, 0.004, 21)).cumprod(),
    },
    index=days,
)
current = {
    "parameters": {
        "max_single_position": 0.10,
        "take_profit_pct": 15,
        "stop_loss_pct": 7,
        "breakout_lookback": 5,
        "time_stop_days": 10,
    },
    "rules": {"position_sizing": {"total_position_limit": 0.8}},
}
watchlist = {
    "stocks": [
        {"code": "600519", "tag": "main"},
        {"code": "601088", "tag": "main"},
        {"code": "000858", "tag": "main"},
    ]
}
window = {"status": "OK", "start": "2026-04-15", "end": "2026-05-13", "trading_days": 21}

sup = {
    "account": "main",
    "diff": [{"path": "main_strategy.parameters.take_profit_pct", "old": 15, "new": 12}],
}
ev = build_oos_evidence(current, sup, watchlist, prices, window)
print("=== SUPPORTED ===")
print(json.dumps(ev, indent=2, default=str))
assert ev["status"] == "OK", "FAIL: supported numeric diff should be OK"
assert ev["current"] != ev["proposed"], "FAIL: simulator produced identical metrics (audit blind)"
assert round(ev["current"]["sharpe"], 6) != round(ev["proposed"]["sharpe"], 6), (
    "FAIL: current/proposed Sharpe equal at 6-decimal precision"
)

unsup = {
    "account": "main",
    "diff": [
        {
            "path": "main_strategy.rules.exit.take_profit",
            "old": "涨幅达15%",
            "new": "涨幅达12%",
        }
    ],
}
ev2 = build_oos_evidence(current, unsup, watchlist, prices, window)
print("\n=== UNSUPPORTED ===")
print(json.dumps(ev2, indent=2, ensure_ascii=False))
assert ev2["status"] == "INFRA_ERROR"
assert ev2["reason"] == "UNSUPPORTED_STRATEGY_DIFF"

print("\nA-L2 pass")
