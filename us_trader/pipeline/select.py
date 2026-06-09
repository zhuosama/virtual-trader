"""
选股管线:纯函数,可单测。
输入:价格面板、基本面快照、配置、截止日期
输出:含动量分排序的候选列表(passed=True 进入目标持仓)
"""
import logging
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


def select(
    panel: pd.DataFrame,
    fundamentals: Dict[str, dict],
    config: dict,
    as_of: str,
) -> List[dict]:
    """
    选股管线,顺序过滤后打分。

    Args:
        panel: 价格面板(index=YYYYMMDD str 升序,columns=ts_code,值=收盘价)
        fundamentals: {ts_code/yahoo_symbol: {market_cap, rev_yoy, earn_yoy, sector}}
        config: 配置字典
        as_of: 截止日期 YYYYMMDD

    Returns:
        List[dict],每项:
        {ts_code, momentum_score, rev_yoy, earn_yoy, market_cap, passed, reasons}
        passed=True 的按 momentum_score 降序排列在前。
    """
    uni_cfg = config["universe"]
    growth_cfg = config["growth"]
    mom_cfg = config["momentum"]

    price_min = uni_cfg["price_min"]
    mcap_min = uni_cfg["mcap_min"]
    mcap_max = uni_cfg["mcap_max"]
    rev_yoy_min = growth_cfg["rev_yoy_min"]
    earn_yoy_min = growth_cfg["earn_yoy_min"]
    lb_3m = mom_cfg["lookback_3m"]
    lb_6m = mom_cfg["lookback_6m"]
    w_3m = mom_cfg["w_3m"]
    w_6m = mom_cfg["w_6m"]

    # 截止到 as_of 的价格面板切片
    sub = panel[panel.index <= as_of]

    results = []

    for code in panel.columns:
        reasons: List[str] = []
        passed = True

        series = sub[code].dropna()
        if series.empty:
            reasons.append("no price data")
            passed = False
            results.append({
                "ts_code": code,
                "momentum_score": None,
                "rev_yoy": None,
                "earn_yoy": None,
                "market_cap": None,
                "passed": False,
                "reasons": reasons,
            })
            continue

        # 当前价格
        price = float(series.iloc[-1])

        # 1. 流动性:price_min(ADV 降级,因 tushare 美股无量数据)
        if price < price_min:
            reasons.append(f"price<min ({price:.2f} < {price_min})")
            passed = False

        # 基本面:从 fundamentals 获取,key 可能是 ts_code 或 yahoo symbol
        from us_trader.pipeline.fetch_fundamentals import to_yahoo_symbol
        yahoo_sym = to_yahoo_symbol(code)
        fund = fundamentals.get(code) or fundamentals.get(yahoo_sym) or {}

        market_cap = fund.get("market_cap")
        rev_yoy = fund.get("rev_yoy")
        earn_yoy = fund.get("earn_yoy")

        # 2. 中小盘过滤
        if market_cap is None:
            reasons.append("mcap missing")
            passed = False
        elif not (mcap_min <= market_cap <= mcap_max):
            reasons.append(f"mcap out of range ({market_cap:.2e})")
            passed = False

        # 3. 成长过滤
        if rev_yoy is None:
            reasons.append("rev_yoy missing")
            passed = False
        elif rev_yoy < rev_yoy_min:
            reasons.append(f"rev_yoy<min ({rev_yoy:.2f} < {rev_yoy_min})")
            passed = False

        if earn_yoy is None:
            reasons.append("earn_yoy missing")
            passed = False
        elif earn_yoy < earn_yoy_min:
            reasons.append(f"earn_yoy<min ({earn_yoy:.2f} < {earn_yoy_min})")
            passed = False

        # 4. 动量分计算
        momentum_score = None
        if len(series) <= lb_6m:
            reasons.append("insufficient history")
            passed = False
        else:
            try:
                # 使用 iloc(-1-lookback) 作为基准价
                price_3m_ago = float(series.iloc[-1 - lb_3m])
                price_6m_ago = float(series.iloc[-1 - lb_6m])
                ret_3m = price / price_3m_ago - 1
                ret_6m = price / price_6m_ago - 1
                momentum_score = w_3m * ret_3m + w_6m * ret_6m
            except (IndexError, ZeroDivisionError) as exc:
                reasons.append(f"momentum calc error: {exc}")
                passed = False

        results.append({
            "ts_code": code,
            "momentum_score": momentum_score,
            "rev_yoy": rev_yoy,
            "earn_yoy": earn_yoy,
            "market_cap": market_cap,
            "passed": passed,
            "reasons": reasons,
        })

    # 按 momentum_score 降序排列 passed=True 的票
    passed_items = [r for r in results if r["passed"]]
    failed_items = [r for r in results if not r["passed"]]
    passed_items.sort(key=lambda r: r["momentum_score"] if r["momentum_score"] is not None else float("-inf"), reverse=True)

    return passed_items + failed_items
