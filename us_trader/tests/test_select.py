import pandas as pd
import numpy as np
from us_trader.pipeline.select import select
from us_trader.config import load_config


def _panel():
    days = [f"2026{m:02d}{d:02d}" for m in (1, 2, 3, 4, 5, 6) for d in (1, 15)]  # 12 行
    idx = sorted(days)
    # A 强动量, B 弱动量, C 大盘(应被市值剔除)
    return pd.DataFrame({
        "A": [10 + i for i in range(12)],     # 单调上涨
        "B": [20 - 0.1 * i for i in range(12)],  # 下跌
        "C": [50] * 12,
    }, index=idx)


def test_select_ranks_and_filters():
    c = load_config()
    c["momentum"]["lookback_3m"] = 3
    c["momentum"]["lookback_6m"] = 6
    fund = {
        "A": {"market_cap": 1e9, "rev_yoy": 0.3, "earn_yoy": 0.2, "sector": "Tech"},
        "B": {"market_cap": 1e9, "rev_yoy": 0.3, "earn_yoy": 0.2, "sector": "Tech"},
        "C": {"market_cap": 5e10, "rev_yoy": 0.3, "earn_yoy": 0.2, "sector": "Tech"},
    }
    out = select(_panel(), fund, c, as_of="20260615")
    passed = [r["ts_code"] for r in out if r["passed"]]
    assert passed[0] == "A"           # 最强动量排第一
    assert "C" not in passed          # 市值超上限被剔除
    crow = next(r for r in out if r["ts_code"] == "C")
    assert "mcap" in " ".join(crow["reasons"]).lower()


def test_missing_growth_excluded():
    c = load_config()
    c["momentum"]["lookback_3m"] = 3
    c["momentum"]["lookback_6m"] = 6
    fund = {"A": {"market_cap": 1e9, "rev_yoy": None, "earn_yoy": 0.2, "sector": "Tech"}}
    out = select(_panel()[["A"]], fund, c, as_of="20260615")
    assert all(not r["passed"] for r in out)
