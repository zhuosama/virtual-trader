#!/usr/bin/env python3
"""H53-FIX IC Bench Harness: re-run gtja191 factors against tushare-qfq OHLCV+amount.

Reuses the H53 factor files verbatim. Only swaps the OHLV input to the new
tushare-qfq panel aligned to the H47 close-matrix tickers.

Output: backtest/runs/h53fix_gtja191_ic_bench.json
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path("/Users/zhuosama/.hermes/virtual-trader")
OHLV_PATH = PROJECT / "data/cn_pit/ohlcv_h53fix_tushare_qfq.csv"
CLOSE_PATH = PROJECT / "data/cn_pit/prices_h47_tushare_qfq_candidate.csv"
FACTOR_DIR = PROJECT / "backtest/factors/gtja191"
OUTPUT_PATH = PROJECT / "backtest/runs/h53fix_gtja191_ic_bench.json"

IC_START = "2025-01-01"
IC_END = "2026-05-18"

# Status thresholds
THIN_PCT = 30.0  # valid_pct < 30% → COMPUTE_THIN
IC_THRESHOLD_ABS = 0.03
IR_THRESHOLD = 0.5

print("[H53FIX-BENCH] Loading OHLV data...")
ohlv_long = pd.read_csv(OHLV_PATH, dtype={"date": str, "ticker": str})
print(f"  Rows: {len(ohlv_long)}, tickers: {ohlv_long.ticker.nunique()}")

# Pivot to wide panel dict
print("[H53FIX-BENCH] Pivoting to wide panels...")
t0 = time.monotonic()

def to_wide(col: str) -> pd.DataFrame:
    sub = ohlv_long[["date", "ticker", col]].dropna(subset=[col])
    wide = sub.pivot(index="date", columns="ticker", values=col)
    wide.index = pd.to_datetime(wide.index)
    wide = wide.sort_index()
    return wide

panel_cols = ["open", "high", "low", "close", "volume", "amount"]
panel = {}
for col in panel_cols:
    if col in ohlv_long.columns:
        panel[col] = to_wide(col)
        print(f"  {col}: {panel[col].shape[0]} dates × {panel[col].shape[1]} tickers")
    else:
        print(f"  {col}: NOT IN DATA")

print(f"  Pivot done in {time.monotonic()-t0:.1f}s")

# Load close matrix for forward returns
print("[H53FIX-BENCH] Loading close matrix...")
close_matrix = pd.read_csv(CLOSE_PATH, index_col=0, parse_dates=True)
close_matrix = close_matrix.sort_index()
print(f"  Shape: {close_matrix.shape}")

# Compute forward 1-day returns
fwd_ret = close_matrix.pct_change().shift(-1)  # ret[t] = close[t+1]/close[t] - 1
fwd_ret = fwd_ret.loc[IC_START:IC_END]

# Filter IC period
ic_dates = fwd_ret.index
n_total_dates = len(ic_dates)
print(f"[H53FIX-BENCH] IC period: {IC_START} → {IC_END} ({n_total_dates} dates)")

# --- Factor registry ---
# Discover all alpha_XXX.py files
import importlib.util
import re
import sys as _sys

# Ensure factor dir is on path for imports
_factor_dir_str = str(FACTOR_DIR)
if _factor_dir_str not in _sys.path:
    _sys.path.insert(0, _factor_dir_str)

factor_ids = []
for fpath in sorted(FACTOR_DIR.glob("alpha_*.py")):
    m = re.match(r"alpha_(\d+)\.py", fpath.name)
    if m:
        factor_ids.append(f"alpha_{m.group(1)}")

print(f"[H53FIX-BENCH] Found {len(factor_ids)} factor files")

# --- IC computation ---
# --- Manual Spearman rank correlation (scipy not available) ---
def spearman_ic(x, y):
    """Compute Spearman rank IC between two aligned Series."""
    x_rank = x.rank()
    y_rank = y.rank()
    return x_rank.corr(y_rank)


def compute_daily_ic(factor_scores: pd.DataFrame, returns: pd.DataFrame, date) -> tuple:
    """Compute rank IC for a single date. Returns (ic, n_tickers) or (np.nan, 0)."""
    if date not in factor_scores.index or date not in returns.index:
        return np.nan, 0
    fs = factor_scores.loc[date]
    ret = returns.loc[date]
    # Align tickers
    common = fs.index.intersection(ret.index)
    fs = fs[common].dropna()
    ret = ret[common].dropna()
    common = fs.index.intersection(ret.index)
    if len(common) < 10:
        return np.nan, len(common)
    try:
        ic = spearman_ic(fs[common], ret[common])
        if pd.isna(ic):
            return np.nan, len(common)
        return float(ic), len(common)
    except Exception:
        return np.nan, len(common)


def compute_rolling_60d_ir(ic_series: pd.Series) -> tuple:
    """Compute mean and last of rolling 60-day IR."""
    if len(ic_series) < 60:
        # Not enough data: compute IR on all
        mean_ic = ic_series.mean()
        std_ic = ic_series.std(ddof=1)
        ir = mean_ic / std_ic if std_ic > 0 else 0.0
        return ir, ir
    rolling_mean = ic_series.rolling(60, min_periods=30).mean()
    rolling_std = ic_series.rolling(60, min_periods=30).std(ddof=1)
    rolling_ir = rolling_mean / rolling_std.replace(0, np.nan)
    return rolling_ir.mean(), rolling_ir.iloc[-1] if not rolling_ir.empty else np.nan


# --- Run factors ---
results = []
sys.path_orig = list(_sys.path)

for fid in factor_ids:
    mod_name = fid  # e.g., "alpha_001"
    mod_path = FACTOR_DIR / f"{mod_name}.py"

    # Load module
    try:
        spec = importlib.util.spec_from_file_location(mod_name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        results.append({
            "factor_id": fid,
            "mean_ic": None, "std_ic": None, "ir": None,
            "n_obs": 0, "n_total_dates": n_total_dates,
            "n_tickers_mean": 0, "valid_pct": 0.0,
            "rolling_60d_ir_mean": None, "rolling_60d_ir_last": None,
            "abs_mean_ic": None,
            "status": "COMPUTE_FAILED",
            "columns_used": [],
            "theme": [],
            "formula": f"IMPORT ERROR: {str(e)[:100]}",
            "min_warmup_bars": 0,
        })
        continue

    # Get metadata
    meta = getattr(mod, "__alpha_meta__", {})
    columns_required = meta.get("columns_required", [])
    theme = meta.get("theme", [])
    formula = meta.get("formula_latex", "see body")
    min_warmup_bars = meta.get("min_warmup_bars", 1)

    # Check columns
    missing = [c for c in columns_required if c not in panel]
    if missing:
        results.append({
            "factor_id": fid,
            "mean_ic": None, "std_ic": None, "ir": None,
            "n_obs": 0, "n_total_dates": n_total_dates,
            "n_tickers_mean": 0, "valid_pct": 0.0,
            "rolling_60d_ir_mean": None, "rolling_60d_ir_last": None,
            "abs_mean_ic": None,
            "status": "UNSUPPORTED_COLUMN",
            "columns_used": columns_required,
            "theme": theme,
            "formula": formula,
            "min_warmup_bars": min_warmup_bars,
        })
        continue

    # Compute factor
    try:
        factor_scores = mod.compute(panel)
    except Exception as e:
        results.append({
            "factor_id": fid,
            "mean_ic": None, "std_ic": None, "ir": None,
            "n_obs": 0, "n_total_dates": n_total_dates,
            "n_tickers_mean": 0, "valid_pct": 0.0,
            "rolling_60d_ir_mean": None, "rolling_60d_ir_last": None,
            "abs_mean_ic": None,
            "status": "COMPUTE_FAILED",
            "columns_used": columns_required,
            "theme": theme,
            "formula": f"COMPUTE ERROR: {str(e)[:100]}",
            "min_warmup_bars": min_warmup_bars,
        })
        continue

    if not isinstance(factor_scores, pd.DataFrame):
        factor_scores = pd.DataFrame(factor_scores)

    # Align factor scores to IC period
    factor_scores = factor_scores.sort_index()
    ic_scores = factor_scores.loc[factor_scores.index.intersection(ic_dates)]

    # Compute daily ICs
    daily_ics = []
    daily_n_tickers = []
    for d in ic_dates:
        ic_val, n_tick = compute_daily_ic(ic_scores, fwd_ret, d)
        daily_ics.append(ic_val)
        daily_n_tickers.append(n_tick)

    ic_series = pd.Series(daily_ics, index=ic_dates).dropna()
    n_obs = len(ic_series)
    n_tickers_mean = np.mean(daily_n_tickers) if daily_n_tickers else 0.0
    valid_pct = round(n_obs / n_total_dates * 100, 2) if n_total_dates > 0 else 0.0

    if n_obs < 10:
        mean_ic = np.nan
        std_ic = np.nan
        ir = np.nan
        abs_mean_ic = np.nan
        rolling_60d_ir_mean = np.nan
        rolling_60d_ir_last = np.nan
    else:
        mean_ic = float(ic_series.mean())
        std_ic = float(ic_series.std(ddof=1))
        ir = mean_ic / std_ic if std_ic > 0 else 0.0
        abs_mean_ic = abs(mean_ic)
        rolling_60d_ir_mean, rolling_60d_ir_last = compute_rolling_60d_ir(ic_series)
        rolling_60d_ir_mean = float(rolling_60d_ir_mean) if not np.isnan(rolling_60d_ir_mean) else None
        rolling_60d_ir_last = float(rolling_60d_ir_last) if not np.isnan(rolling_60d_ir_last) else None

    # Determine status
    if valid_pct < THIN_PCT:
        status = "COMPUTE_THIN"
    else:
        status = "OK"

    results.append({
        "factor_id": fid,
        "mean_ic": mean_ic,
        "std_ic": std_ic,
        "ir": ir,
        "n_obs": n_obs,
        "n_total_dates": n_total_dates,
        "n_tickers_mean": round(n_tickers_mean, 2),
        "valid_pct": valid_pct,
        "rolling_60d_ir_mean": rolling_60d_ir_mean,
        "rolling_60d_ir_last": rolling_60d_ir_last,
        "abs_mean_ic": abs_mean_ic,
        "status": status,
        "columns_used": columns_required,
        "theme": theme,
        "formula": formula,
        "min_warmup_bars": min_warmup_bars,
    })

# --- Summary ---
ok_results = [r for r in results if r["status"] == "OK"]
thin_results = [r for r in results if r["status"] == "COMPUTE_THIN"]
failed_results = [r for r in results if r["status"] == "COMPUTE_FAILED"]
unsupported_results = [r for r in results if r["status"] == "UNSUPPORTED_COLUMN"]

# Passing threshold
passing = [r for r in ok_results if r["ir"] is not None and abs(r["ir"]) >= IR_THRESHOLD
           and r["abs_mean_ic"] is not None and r["abs_mean_ic"] >= IC_THRESHOLD_ABS]

# Top 5 by |IR|
sorted_by_ir = sorted(
    [r for r in results if r["ir"] is not None and not np.isnan(r["ir"])],
    key=lambda x: abs(x["ir"]),
    reverse=True,
)

summary = {
    "total_factors": len(results),
    "ok_count": len(ok_results),
    "unsupported_count": len(unsupported_results),
    "compute_failed_count": len(failed_results),
    "compute_thin_count": len(thin_results),
    "passing_threshold_count": len(passing),
    "top5_by_ir": sorted_by_ir[:5],
}

output = {
    "run_id": "h53fix_gtja191_ic_bench",
    "run_date": datetime.now(timezone.utc).isoformat(),
    "period": {"start": IC_START, "end": IC_END},
    "thresholds": {"abs_mean_ic_min": IC_THRESHOLD_ABS, "ir_min": IR_THRESHOLD},
    "data_source": {
        "ohlv": str(OHLV_PATH),
        "close": str(CLOSE_PATH),
    },
    "factors": results,
    "summary": summary,
}

with open(OUTPUT_PATH, "w") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\n[H53FIX-BENCH] Results saved: {OUTPUT_PATH}")
print(f"  Total: {len(results)}")
print(f"  OK: {len(ok_results)}")
print(f"  COMPUTE_THIN: {len(thin_results)}")
print(f"  COMPUTE_FAILED: {len(failed_results)}")
print(f"  UNSUPPORTED_COLUMN: {len(unsupported_results)}")
print(f"  Passing threshold (|IC|>{IC_THRESHOLD_ABS} & |IR|>{IR_THRESHOLD}): {len(passing)}")
print(f"\n  Top 5 by |IR|:")
for r in sorted_by_ir[:5]:
    print(f"    {r['factor_id']}: IR={r['ir']:.4f}  IC={r['mean_ic']:.4f}  obs={r['n_obs']}  status={r['status']}")
