"""
市值/成长基本面数据获取 — yahooquery
每日对票池做一次快照:marketCap / 营收同比 / 盈利同比 / 行业。
"""
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 交易所后缀白名单(只剥这些;BRK.A / BRK.B 等类 ticker 不在其中)
_EXCHANGE_SUFFIXES = {"O", "N", "K", "P"}


def to_yahoo_symbol(ts_code: str) -> str:
    """
    tushare 美股 ts_code → yahoo ticker。
    仅剥交易所后缀白名单 {O, N, K, P}(如 AAPL.O → AAPL)。
    保留 BRK.A / BRK.B 这类非交易所后缀。
    """
    if "." in ts_code:
        base, suffix = ts_code.rsplit(".", 1)
        if suffix in _EXCHANGE_SUFFIXES:
            return base
    return ts_code


# ── 薄封装,便于 mock ──────────────────────────────────────────────────────────

def _ticker(symbols: List[str]):
    """返回 yahooquery.Ticker 实例。"""
    from yahooquery import Ticker
    return Ticker(symbols)


def _growth(symbols: List[str]) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """
    返回 {symbol: (rev_yoy, earn_yoy)} 的字典。
    优先使用 key_stats 中的现成增长字段;回退从 income_statement 同比算。
    缺失一律返回 None。
    """
    result: Dict[str, Tuple[Optional[float], Optional[float]]] = {s: (None, None) for s in symbols}
    try:
        ticker = _ticker(symbols)
        # 尝试从 financial_data 获取增长率字段
        try:
            fd = ticker.financial_data
            if isinstance(fd, dict):
                for sym in symbols:
                    if sym not in fd or not isinstance(fd[sym], dict):
                        continue
                    rev = fd[sym].get("revenueGrowth")
                    earn = fd[sym].get("earningsGrowth")
                    result[sym] = (
                        float(rev) if rev is not None else None,
                        float(earn) if earn is not None else None,
                    )
        except Exception as exc:
            logger.debug("_growth financial_data error: %s", exc)

        # 回退:从 key_stats 取 revenueGrowth/earningsGrowth
        try:
            ks = ticker.key_stats
            if isinstance(ks, dict):
                for sym in symbols:
                    if result[sym] != (None, None):
                        continue  # 已有值则跳过
                    if sym not in ks or not isinstance(ks[sym], dict):
                        continue
                    rev = ks[sym].get("revenueGrowth", ks[sym].get("revenueQuarterlyGrowth"))
                    earn = ks[sym].get("earningsGrowth", ks[sym].get("netIncomeToCommon"))
                    result[sym] = (
                        float(rev) if rev is not None else None,
                        float(earn) if earn is not None else None,
                    )
        except Exception as exc:
            logger.debug("_growth key_stats error: %s", exc)

    except Exception as exc:
        logger.warning("_growth outer error: %s", exc)

    return result


# ── 公开 API ─────────────────────────────────────────────────────────────────

def fetch_fundamentals(
    codes: List[str],
    batch: int = 50,
) -> Dict[str, dict]:
    """
    返回 {ts_code: {"market_cap", "rev_yoy", "earn_yoy", "sector"}}。
    codes 使用 tushare ts_code(含交易所后缀),内部做 symbol 映射。
    单票/单字段缺失 → None;整批异常 → 记 warning,返回已得部分。
    """
    # ts_code → yahoo symbol 映射表
    symbol_map = {c: to_yahoo_symbol(c) for c in codes}
    yahoo_symbols = list(symbol_map.values())

    # 结果以 yahoo symbol 为 key(便于测试 mock 对齐;调用方用 to_yahoo_symbol 查找)
    result: Dict[str, dict] = {
        sym: {"market_cap": None, "rev_yoy": None, "earn_yoy": None, "sector": None}
        for sym in yahoo_symbols
    }

    # 分批处理
    for i in range(0, len(yahoo_symbols), batch):
        batch_syms = yahoo_symbols[i: i + batch]

        try:
            ticker = _ticker(batch_syms)

            # 市值 / 行业
            try:
                sd = ticker.summary_detail
                asset_profile = None
                try:
                    asset_profile = ticker.asset_profile
                except Exception:
                    pass

                for sym in batch_syms:
                    # market_cap
                    if isinstance(sd, dict) and sym in sd and isinstance(sd[sym], dict):
                        mc = sd[sym].get("marketCap")
                        result[sym]["market_cap"] = float(mc) if mc is not None else None
                    # sector
                    if asset_profile and isinstance(asset_profile, dict):
                        if sym in asset_profile and isinstance(asset_profile[sym], dict):
                            result[sym]["sector"] = asset_profile[sym].get("sector")
            except Exception as exc:
                logger.warning("fetch_fundamentals summary_detail error: %s", exc)

            # 成长率
            try:
                growth = _growth(batch_syms)
                for sym in batch_syms:
                    rv, ey = growth.get(sym, (None, None))
                    result[sym]["rev_yoy"] = rv
                    result[sym]["earn_yoy"] = ey
            except Exception as exc:
                logger.warning("fetch_fundamentals growth error: %s", exc)

        except Exception as exc:
            logger.warning("fetch_fundamentals batch error (batch %d): %s", i, exc)

    return result
