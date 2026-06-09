"""
价格/日历数据获取 — tushare us_daily / us_tradecal
"""
import os
import logging
from pathlib import Path
from typing import List

import pandas as pd

logger = logging.getLogger(__name__)

# ── token 读取(参照 scripts/h49a_build_tushare_sw_industry.py) ─────────────
_ROOT = Path(__file__).resolve().parents[2]  # virtual-trader/


def _get_tushare_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    for tp in [_ROOT / "scripts" / ".tushare_token", Path.home() / ".tushare.token"]:
        if tp.exists():
            t = tp.read_text(encoding="utf-8").strip()
            if t:
                return t
    try:
        import tushare as ts
        t = (ts.get_token() or "").strip()
        if t:
            return t
    except Exception:
        pass
    raise RuntimeError("Tushare token not found. Set TUSHARE_TOKEN or scripts/.tushare_token")


_pro_instance = None


def _pro():
    """返回缓存的 tushare pro_api 实例。"""
    global _pro_instance
    if _pro_instance is None:
        import tushare as ts
        ts.set_token(_get_tushare_token())
        _pro_instance = ts.pro_api()
    return _pro_instance


# ── 公开 API ─────────────────────────────────────────────────────────────────

def get_trading_days(end_yyyymmdd: str, n: int) -> List[str]:
    """
    返回截至 end_yyyymmdd 的最近 n 个美股开市日(升序 YYYYMMDD 列表)。
    实际取 end 前 n*3 天范围以保证足够数据量。
    """
    end_dt = pd.Timestamp(end_yyyymmdd)
    start_dt = end_dt - pd.Timedelta(days=n * 3)
    start_str = start_dt.strftime("%Y%m%d")

    df = _pro().query("us_tradecal", start_date=start_str, end_date=end_yyyymmdd)
    if df is None or df.empty:
        return []

    open_days = (
        df[df["is_open"] == 1]["cal_date"]
        .sort_values()
        .tolist()
    )
    return open_days[-n:]


def is_trading_day(yyyymmdd: str) -> bool:
    """判断指定日期是否为美股交易日。"""
    df = _pro().query("us_tradecal", start_date=yyyymmdd, end_date=yyyymmdd)
    if df is None or df.empty:
        return False
    row = df[df["cal_date"] == yyyymmdd]
    if row.empty:
        return False
    return int(row["is_open"].iloc[0]) == 1


def fetch_price_panel(
    codes: List[str],
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    返回价格面板:index=交易日(YYYYMMDD str 升序),columns=ts_code,值=收盘价。
    单票失败 → 记 warning、该列全 NaN,不抛。
    """
    frames = {}
    for code in codes:
        try:
            df = _pro().query(
                "us_daily",
                ts_code=code,
                start_date=start,
                end_date=end,
            )
            if df is None or df.empty:
                logger.warning("fetch_price_panel: no data for %s", code)
                frames[code] = pd.Series(dtype=float, name=code)
                continue
            s = (
                df[["trade_date", "close"]]
                .drop_duplicates("trade_date")
                .set_index("trade_date")["close"]
                .sort_index()
                .rename(code)
            )
            frames[code] = s
        except Exception as exc:
            logger.warning("fetch_price_panel: error fetching %s: %s", code, exc)
            frames[code] = pd.Series(dtype=float, name=code)

    if not frames:
        return pd.DataFrame()

    panel = pd.concat(frames.values(), axis=1)
    panel.index.name = "trade_date"
    panel.sort_index(inplace=True)
    return panel
