#!/usr/bin/env python3
"""H53-FIX: Fetch tushare pro_bar qfq OHLCV+amount for 482 H47 universe tickers.

Output: data/cn_pit/ohlcv_h53fix_tushare_qfq.csv (long format)
Polite: 0.35s sleep between tickers. Incremental save every 20 tickers.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import tushare as ts

TOKEN = os.environ.get("TUSHARE_TOKEN")
if not TOKEN:
    print("FATAL: TUSHARE_TOKEN not set. STOP.", file=sys.stderr)
    sys.exit(1)

ts.set_token(TOKEN)
# Use ts.pro_bar() directly — pro.pro_bar() does not support this endpoint

PROJECT = Path("/Users/zhuosama/.hermes/virtual-trader")
TICKER_FILE = PROJECT / "data/cn_pit/prices_h47_tushare_qfq_candidate.csv"
OUTPUT_PATH = PROJECT / "data/cn_pit/ohlcv_h53fix_tushare_qfq.csv"
COVERAGE_PATH = PROJECT / "data/cn_pit/ohlcv_coverage_h53fix.json"

START = "20241001"
END = "20260518"
COLUMNS = ["date", "ticker", "open", "high", "low", "close", "volume", "amount"]
SLEEP = 0.35
SAVE_EVERY = 20

# Load tickers
tickers = []
with open(TICKER_FILE) as f:
    header = next(csv.reader(f))
    for col in header[1:]:
        col = col.strip()
        if col:
            tickers.append(col)

n_total = len(tickers)
print(f"[H53FIX] {n_total} tickers | {START} → {END} | sleep={SLEEP}s")
print(f"[H53FIX] Output: {OUTPUT_PATH}")
print(f"[H53FIX] Est wall time: ~{n_total * SLEEP / 60:.0f} min")

fetch_results = {}
all_dfs = []
t0 = time.monotonic()

for idx, ticker in enumerate(tickers):
    i = idx + 1
    # Convert exchange suffix: tushare pro_bar expects .SH not .SS
    ts_ticker = ticker.replace(".SS", ".SH") if ticker.endswith(".SS") else ticker
    try:
        df = ts.pro_bar(ts_code=ts_ticker, adj="qfq", start_date=START, end_date=END)
    except Exception as e:
        fetch_results[ticker] = {"status": "error", "rows": 0, "err": str(e)[:120]}
        if i % 50 == 0 or i == n_total:
            print(f"  [{i}/{n_total}] {ticker} ERROR: {str(e)[:80]}", flush=True)
        time.sleep(SLEEP)
        continue

    if df is None or df.empty:
        fetch_results[ticker] = {"status": "empty", "rows": 0, "err": None}
        if i % 50 == 0 or i == n_total:
            print(f"  [{i}/{n_total}] {ticker} EMPTY", flush=True)
        time.sleep(SLEEP)
        continue

    df = df.rename(columns={"trade_date": "date", "ts_code": "ticker", "vol": "volume"})
    # Convert back to H47 matrix format (.SH → .SS) for alignment with close matrix
    df["ticker"] = df["ticker"].str.replace(".SH", ".SS", regex=False)
    available = [c for c in COLUMNS if c in df.columns]
    df = df[available]
    df["date"] = df["date"].astype(str)

    n = len(df)
    fetch_results[ticker] = {"status": "ok", "rows": n, "err": None}
    all_dfs.append(df)

    if i % 50 == 0 or i == n_total:
        elapsed = time.monotonic() - t0
        eta = elapsed / i * (n_total - i)
        print(f"  [{i}/{n_total}] {ticker} OK ({n} rows) | total_rows={sum(r['rows'] for r in fetch_results.values() if r['status']=='ok')} | elapsed={elapsed:.0f}s eta={eta:.0f}s", flush=True)

    # Incremental save every SAVE_EVERY
    if i % SAVE_EVERY == 0 and all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined[[c for c in COLUMNS if c in combined.columns]]
        combined.to_csv(OUTPUT_PATH, index=False)

    time.sleep(SLEEP)

# Final save
if all_dfs:
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined[[c for c in COLUMNS if c in combined.columns]]
    combined.to_csv(OUTPUT_PATH, index=False)
    total_rows = len(combined)
    n_tickers_fetched = combined.ticker.nunique()
    print(f"\n[H53FIX] DONE. {total_rows} rows, {n_tickers_fetched} tickers")
else:
    print("\n[H53FIX] FATAL: No data fetched!", file=sys.stderr)
    sys.exit(1)

# Coverage report
ok_tickers   = [t for t, r in fetch_results.items() if r["status"] == "ok"]
empty_tickers = [t for t, r in fetch_results.items() if r["status"] == "empty"]
error_tickers = [t for t, r in fetch_results.items() if r["status"] == "error"]
ok_rows = sum(r["rows"] for r in fetch_results.values() if r["status"] == "ok")

combined = pd.concat(all_dfs, ignore_index=True)
amount_total = int(combined["amount"].notna().sum()) if "amount" in combined.columns else 0
amount_pct = round(amount_total / len(combined) * 100, 2) if len(combined) > 0 else 0.0

coverage = {
    "source_provider": "tushare:pro_bar:qfq",
    "universe": "H47 CSI300 frozen close matrix (482 tickers)",
    "total_tickers_attempted": n_total,
    "ok_tickers": len(ok_tickers),
    "empty_tickers": len(empty_tickers),
    "error_tickers": len(error_tickers),
    "total_rows": len(combined),
    "amount_nonnull": amount_total,
    "amount_nonnull_pct": amount_pct,
    "per_ticker": fetch_results,
    "empty_ticker_list": empty_tickers,
    "error_ticker_list": error_tickers,
    "date_span": {
        "earliest": str(combined["date"].min()),
        "latest": str(combined["date"].max()),
    },
}

with open(COVERAGE_PATH, "w") as f:
    json.dump(coverage, f, indent=2, ensure_ascii=False)

print(f"[H53FIX] Coverage: OK={len(ok_tickers)} Empty={len(empty_tickers)} Error={len(error_tickers)}")
print(f"[H53FIX] Amount non-null: {amount_total}/{len(combined)} = {amount_pct}%")
if empty_tickers:
    print(f"[H53FIX] Empty: {empty_tickers}")
if error_tickers:
    print(f"[H53FIX] Error: {error_tickers}")
print(f"[H53FIX] Coverage saved: {COVERAGE_PATH}")
