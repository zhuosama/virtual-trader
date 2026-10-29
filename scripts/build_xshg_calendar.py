#!/usr/bin/env python3
"""Build a vendored SSE (XSHG) trading calendar from Tushare trade_cal.

Output: data/calendar/xshg_trade_cal.json
  {source, exchange, start, end, generated_at, open_days: [...], closed_days: [...]}
Used by validate_ledger_consistency INV-4 to decide whether a date is a
real non-trading day. Dates outside [start, end] are treated as UNKNOWN.

Usage: python3 scripts/build_xshg_calendar.py 20260101 20261231
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from income_portfolio.adapters import tushare_client  # noqa: E402

OUT = ROOT / "data" / "calendar" / "xshg_trade_cal.json"


def main(begin: str, end: str) -> int:
    pro = tushare_client(timeout=30)
    df = pro.trade_cal(exchange="SSE", start_date=begin, end_date=end)
    rows = df.to_dict("records")
    fmt = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"
    open_days = sorted(fmt(str(r["cal_date"])) for r in rows if int(r["is_open"]) == 1)
    closed = sorted(fmt(str(r["cal_date"])) for r in rows if int(r["is_open"]) == 0)
    if not open_days:
        print("empty calendar, abort", file=sys.stderr)
        return 1
    data = {
        "source": "tushare.pro trade_cal exchange=SSE",
        "exchange": "XSHG",
        "start": fmt(begin),
        "end": fmt(end),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "open_days": open_days,
        "closed_days": closed,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {OUT} open={len(open_days)} closed={len(closed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
