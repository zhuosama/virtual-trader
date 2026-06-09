"""
票池管理:加载静态种子 universe.csv,以及可选的 tushare 刷新工具。
"""
import csv
import logging
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)

_DEFAULT_UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "data" / "universe.csv"


def load_universe(path: str = None) -> List[Dict[str, str]]:
    """
    读取 universe.csv,返回 [{"ts_code": ..., "name": ...}] 列表。
    默认使用 us_trader/data/universe.csv。
    """
    p = Path(path) if path else _DEFAULT_UNIVERSE_PATH
    rows = []
    seen = set()
    with open(p, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["ts_code"].strip()
            name = row["name"].strip()
            if not code or not name:
                continue
            if code in seen:
                logger.warning("load_universe: duplicate ts_code %s, skipping", code)
                continue
            seen.add(code)
            rows.append({"ts_code": code, "name": name})
    return rows


def refresh_universe_from_tushare(classify: str = "EQ", output_path: str = None) -> int:
    """
    可选工具:从 tushare us_basic 刷新票池种子。
    **不在每日链路调用**,仅人工手动执行。
    返回写入行数。
    """
    from us_trader.pipeline.fetch_prices import _pro
    pro = _pro()
    df = pro.query("us_basic", classify=classify)
    if df is None or df.empty:
        logger.warning("refresh_universe_from_tushare: no data returned")
        return 0

    p = Path(output_path) if output_path else _DEFAULT_UNIVERSE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    seen = set()
    with open(p, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts_code", "name"])
        for _, row in df.iterrows():
            code = str(row.get("ts_code", "")).strip()
            name = str(row.get("name", "")).strip()
            if not code or code in seen:
                continue
            seen.add(code)
            writer.writerow([code, name])
            written += 1

    logger.info("refresh_universe_from_tushare: wrote %d rows to %s", written, p)
    return written
