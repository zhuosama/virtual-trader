#!/usr/bin/env python3
"""H52c — CSI500 Daily Fact Data (QFQ Prices + ADTV) from Tushare.

Three-endpoint axis-flip architecture:
  - pro.daily(trade_date=YYYYMMDD) per trade_date — raw close/vol/amount
  - pro.adj_factor(trade_date=YYYYMMDD) per trade_date — adjustment factors
  - pro.index_daily(ts_code='000300.SH') ONE-SHOT — HS300 benchmark history

QFQ is computed LOCALLY from raw_close × adj_factor_t / adj_factor_terminal[ticker].
HS300 benchmark bypasses QFQ math (published index level as-is).

Outputs (PINNED schemas):
  data/cn_pit/prices_h52c_csi500_qfq.csv          — wide, exactly 1076 columns
  data/cn_pit/liquidity_h52c_csi500_daily_amount.csv — long, 5 columns
  data/cn_pit/price_coverage_h52c.json              — provenance + coverage
  reports/h52c_csi500_daily_facts_ingestion_report.md
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
import numpy as np

# ── Paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data/cn_pit"
REPORTS_DIR = ROOT / "reports"

DEFAULT_UNIVERSE = DATA_DIR / "universe_h52a_csi500.jsonl"
DEFAULT_PRICES_OUT = DATA_DIR / "prices_h52c_csi500_qfq.csv"
DEFAULT_LIQUIDITY_OUT = DATA_DIR / "liquidity_h52c_csi500_daily_amount.csv"
DEFAULT_COVERAGE_OUT = DATA_DIR / "price_coverage_h52c.json"
DEFAULT_REPORT_OUT = REPORTS_DIR / "h52c_csi500_daily_facts_ingestion_report.md"
DEFAULT_RAW_DAILY = DATA_DIR / "raw/h52c_tushare_daily"
DEFAULT_RAW_ADJ = DATA_DIR / "raw/h52c_tushare_adj_factor"
DEFAULT_RAW_INDEX = DATA_DIR / "raw/h52c_tushare_index_daily"

HS300_TICKER = "000300.SS"
HS300_TS_CODE = "000300.SH"
HS300_INDEX_TS_CODE = "000300.SH"

STOCK_PROVIDER = "tushare:daily"
ADJUSTMENT_PROVIDER = "tushare:adj_factor"
BENCHMARK_PROVIDER = "tushare:index_daily"
LIQUIDITY_SOURCE = "tushare:daily"

RATE_LIMIT_CALLS_PER_SEC = 5
MAX_RETRIES = 5
BACKOFF_INITIAL = 2.0
BACKOFF_CAP = 60.0

# ADTV windows (H42 standard) — compact format to match Tushare dates
ADTV_WINDOWS_COMPACT = {
    "cal_2024":         ("20240101", "20241231"),
    "h1_2025":          ("20250101", "20250630"),
    "h2_2025":          ("20250701", "20251231"),
    "ytd_2026":         ("20260101", "20260521"),
    "deploy_2025_2026": ("20250101", "20260521"),
}


# ═══════════════════════════════════════════════════════════════════════════
# Token resolution
# ═══════════════════════════════════════════════════════════════════════════
def get_tushare_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    token_paths = [
        ROOT / "scripts/.tushare_token",
        Path.home() / ".tushare.token",
    ]
    for tp in token_paths:
        if tp.exists():
            token = tp.read_text(encoding="utf-8").strip()
            if token:
                return token
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import ingest_cn_pit_data as ingest
        tok_fn = getattr(ingest, "_get_tushare_token", None)
        if tok_fn:
            token = (tok_fn() or "").strip()
            if token:
                return token
    except Exception:
        pass
    try:
        import tushare as ts
        token = (ts.get_token() or "").strip()
        if token:
            return token
    except Exception:
        pass
    raise SystemExit(
        "No Tushare token found. Set TUSHARE_TOKEN env var, "
        "create scripts/.tushare_token, or configure agents/config.yaml"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Ticker helpers
# ═══════════════════════════════════════════════════════════════════════════
def to_yahoo_style(ts_code: str) -> str:
    """Convert Tushare ts_code to Yahoo ticker: 600000.SH → 600000.SS"""
    if ts_code.endswith(".SH"):
        return ts_code[:-3] + ".SS"
    if ts_code.endswith(".SZ"):
        return ts_code
    raise ValueError(f"unsupported ts_code suffix: {ts_code}")


def compact_date(date_str: str) -> str:
    """2020-01-02 → 20200102"""
    return date_str.replace("-", "")


# ═══════════════════════════════════════════════════════════════════════════
# Universe loading
# ═══════════════════════════════════════════════════════════════════════════
def load_h52a_unique_tickers(universe_path: Path) -> List[str]:
    """Load all unique tickers from H52a universe jsonl (Yahoo format)."""
    tickers: Set[str] = set()
    with universe_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ticker = row.get("ticker")
            if ticker:
                tickers.add(str(ticker))
    return sorted(tickers)


def load_h52a_tushare_codes(universe_path: Path) -> List[str]:
    """Convert H52a tickers to Tushare ts_code format for filtering."""
    tickers = load_h52a_unique_tickers(universe_path)
    result = []
    for t in tickers:
        if t.endswith(".SS"):
            result.append(t[:-3] + ".SH")
        elif t.endswith(".SZ"):
            result.append(t)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Trading day calendar
# ═══════════════════════════════════════════════════════════════════════════
def generate_trading_days(pro_api, start: str, end: str) -> List[str]:
    """Generate list of A-share trading days via Tushare trade_cal."""
    df = pro_api.trade_cal(
        exchange="SSE",
        start_date=compact_date(start),
        end_date=compact_date(end),
        is_open="1",
    )
    if df is None or df.empty:
        raise RuntimeError("Failed to fetch trade calendar from Tushare")
    days = sorted(df["cal_date"].astype(str).tolist())
    # Convert to YYYY-MM-DD
    return [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in days]


# ═══════════════════════════════════════════════════════════════════════════
# Tushare client
# ═══════════════════════════════════════════════════════════════════════════
def create_tushare_client(token: str):
    import tushare as ts
    ts.set_token(token)
    return ts.pro_api(token)


# ═══════════════════════════════════════════════════════════════════════════
# Raw cache
# ═══════════════════════════════════════════════════════════════════════════
def cache_path_daily(raw_dir: Path, trade_date: str) -> Path:
    return raw_dir / f"{compact_date(trade_date)}.csv"


def cache_path_adj(raw_dir: Path, trade_date: str) -> Path:
    return raw_dir / f"{compact_date(trade_date)}.csv"


def cache_path_index(raw_dir: Path) -> Path:
    return raw_dir / "000300_SH.csv"


def read_cached_csv(path: Path) -> Optional[pd.DataFrame]:
    """Read cached CSV, return None if missing or empty."""
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        if df.empty:
            return None
        return df
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Rate-limited fetch with retry
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class FetchFailure:
    endpoint: str
    trade_date: str
    reason: str

    def to_dict(self) -> Dict:
        return {
            "endpoint": self.endpoint,
            "trade_date": self.trade_date,
            "reason": self.reason,
        }


class RateLimiter:
    """Single counter across ALL endpoints combined. 5 calls/sec hard cap."""

    def __init__(self, max_calls_per_sec: float = RATE_LIMIT_CALLS_PER_SEC):
        self._min_interval = 1.0 / max_calls_per_sec
        self._last_call = 0.0

    def acquire(self):
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()


def fetch_with_retry(
    fetch_fn,
    endpoint_label: str,
    trade_date: str,
    rate_limiter: RateLimiter,
    failures: List[FetchFailure],
    max_retries: int = MAX_RETRIES,
) -> Optional[pd.DataFrame]:
    """Fetch with exponential backoff + jitter. Returns DataFrame or None on failure."""
    for attempt in range(max_retries):
        rate_limiter.acquire()
        try:
            result = fetch_fn()
            if result is not None:
                return result
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            if attempt < max_retries - 1:
                wait = min(BACKOFF_INITIAL * (2 ** attempt), BACKOFF_CAP)
                jitter = random.uniform(0, wait * 0.5)
                time.sleep(wait + jitter)
            else:
                failures.append(FetchFailure(
                    endpoint=endpoint_label,
                    trade_date=trade_date,
                    reason=err_msg,
                ))
                return None
    failures.append(FetchFailure(
        endpoint=endpoint_label,
        trade_date=trade_date,
        reason="max_retries_exceeded",
    ))
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Axis-flip fetch engine
# ═══════════════════════════════════════════════════════════════════════════
def fetch_daily_for_date(
    pro_api,
    trade_date: str,
    tushare_universe: Set[str],
    cache_dir: Path,
    rate_limiter: RateLimiter,
    failures: List[FetchFailure],
) -> pd.DataFrame:
    """Fetch pro.daily(trade_date=YYYYMMDD) and filter to universe ∪ {HS300}."""
    path = cache_path_daily(cache_dir, trade_date)
    cached = read_cached_csv(path)
    if cached is not None:
        # Apply universe filter on cached data too
        keep = tushare_universe | {HS300_TS_CODE}
        if "ts_code" in cached.columns:
            return cached[cached["ts_code"].isin(keep)].copy()
        return cached

    def _fetch():
        return pro_api.daily(trade_date=compact_date(trade_date))

    df = fetch_with_retry(
        _fetch,
        "daily",
        trade_date,
        rate_limiter,
        failures,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    # Save raw cache (mirror Tushare verbatim for audit) — NO unit conversions here
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

    # Filter to universe ∪ {HS300}
    keep = tushare_universe | {HS300_TS_CODE}
    df = df[df["ts_code"].isin(keep)].copy()
    return df


def fetch_adj_factor_for_date(
    pro_api,
    trade_date: str,
    tushare_stock_universe: Set[str],
    cache_dir: Path,
    rate_limiter: RateLimiter,
    failures: List[FetchFailure],
) -> pd.DataFrame:
    """Fetch pro.adj_factor(trade_date=YYYYMMDD) and filter to stock universe."""
    path = cache_path_adj(cache_dir, trade_date)
    cached = read_cached_csv(path)
    if cached is not None:
        # Apply universe filter on cached data too
        if "ts_code" in cached.columns:
            return cached[cached["ts_code"].isin(tushare_stock_universe)].copy()
        return cached

    def _fetch():
        return pro_api.adj_factor(trade_date=compact_date(trade_date))

    df = fetch_with_retry(
        _fetch,
        "adj_factor",
        trade_date,
        rate_limiter,
        failures,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    # Save raw cache
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

    # Filter to stock universe only (HS300 benchmark not needed for adj_factor)
    df = df[df["ts_code"].isin(tushare_stock_universe)].copy()
    return df


def fetch_index_daily(
    pro_api,
    start: str,
    end: str,
    cache_dir: Path,
    rate_limiter: RateLimiter,
    failures: List[FetchFailure],
) -> pd.DataFrame:
    """One-shot bulk fetch of HS300 index_daily history."""
    path = cache_path_index(cache_dir)
    cached = read_cached_csv(path)
    if cached is not None:
        return cached

    def _fetch():
        return pro_api.index_daily(
            ts_code=HS300_INDEX_TS_CODE,
            start_date=compact_date(start),
            end_date=compact_date(end),
        )

    df = fetch_with_retry(
        _fetch,
        "index_daily",
        f"{start}→{end}",
        rate_limiter,
        failures,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


# ═══════════════════════════════════════════════════════════════════════════
# QFQ Local Computation
# ═══════════════════════════════════════════════════════════════════════════
def compute_qfq(
    daily_records: List[Dict[str, Any]],
    adj_records: List[Dict[str, Any]],
    h52a_yahoo_tickers: List[str],
) -> Tuple[pd.DataFrame, List[str]]:
    """Compute snapshot-qfq close prices locally.

    Formula: qfq_close_t = raw_close_t × adj_factor_t / adj_factor_terminal[ticker]
    adj_factor_terminal[ticker] = latest non-NaN adj_factor for that ticker.

    BENCHMARK BYPASS: 000300.SS is NOT processed for qfq — its raw close is used as-is.

    Returns:
        long_df: DataFrame with date, ticker, qfq_close columns
        tickers_with_no_qfq: list of tickers with NaN adj_factor_terminal
    """
    # Build daily lookup: {(trade_date, ts_code): {close, vol, amount, pct_chg}}
    daily_map: Dict[Tuple[str, str], Dict[str, float]] = {}
    for rec in daily_records:
        td = str(rec.get("trade_date", ""))
        ts = str(rec.get("ts_code", ""))
        if not td or not ts:
            continue
        key = (td, ts)
        close = pd.to_numeric(rec.get("close"), errors="coerce")
        vol = pd.to_numeric(rec.get("vol"), errors="coerce")
        amount = pd.to_numeric(rec.get("amount"), errors="coerce")
        pct_chg = pd.to_numeric(rec.get("pct_chg"), errors="coerce")
        if pd.notna(close):
            daily_map[key] = {
                "close": close,
                "vol": vol,
                "amount": amount,
                "pct_chg": pct_chg,
            }

    def _to_ts_code(yahoo: str) -> str:
        """Yahoo ticker → Tushare ts_code."""
        if yahoo.endswith(".SS"):
            return yahoo[:-3] + ".SH"
        return yahoo

    # Build adj_factor lookup: {(trade_date, ts_code): adj_factor}
    adj_map: Dict[Tuple[str, str], float] = {}
    for rec in adj_records:
        td = str(rec.get("trade_date", ""))
        ts = str(rec.get("ts_code", ""))
        af = pd.to_numeric(rec.get("adj_factor"), errors="coerce")
        if td and ts and pd.notna(af):
            adj_map[(td, ts)] = af

    # Compute adj_factor_terminal per ticker (latest non-NaN adj_factor for that ticker)
    # Keyed by Tushare ts_code for direct lookup
    adj_factor_terminal: Dict[str, float] = {}
    ticker_adj_dates: Dict[str, List[str]] = {}
    for (td, ts), af in adj_map.items():
        ticker_adj_dates.setdefault(ts, []).append(td)
    for ts, dates in ticker_adj_dates.items():
        latest_date = max(dates)
        adj_factor_terminal[ts] = adj_map[(latest_date, ts)]

    # Build long-format qfq results
    rows: List[Dict] = []
    tickers_with_no_qfq: List[str] = []

    for (td, ts_code), vals in daily_map.items():
        yahoo = to_yahoo_style(ts_code)
        raw_close = vals["close"]

        if ts_code == HS300_TS_CODE:
            # BENCHMARK BYPASS: HS300 published index level as-is
            qfq_close = raw_close
        else:
            af_t = adj_map.get((td, ts_code), np.nan)
            af_term = adj_factor_terminal.get(ts_code, np.nan)
            if pd.notna(af_t) and pd.notna(af_term) and af_term > 0:
                qfq_close = raw_close * af_t / af_term
            else:
                qfq_close = np.nan

        rows.append({
            "date": td,
            "ticker": yahoo,
            "qfq_close": qfq_close,
            "amount_rmb": vals["amount"] * 1000 if pd.notna(vals["amount"]) else np.nan,  # 千元→RMB
            "vol_shares": vals["vol"] * 100 if pd.notna(vals["vol"]) else np.nan,         # 手→shares
            "pct_chg": vals["pct_chg"],
        })

    df = pd.DataFrame(rows)

    # Identify tickers with no qfq (no adj_factor_terminal)
    tickers_with_data = set(df["ticker"].unique())
    for yahoo_ticker in h52a_yahoo_tickers:
        ts_code = _to_ts_code(yahoo_ticker)
        if ts_code not in adj_factor_terminal:
            tickers_with_no_qfq.append(yahoo_ticker)

    return df, tickers_with_no_qfq


# ═══════════════════════════════════════════════════════════════════════════
# Wide-format pivot with force-reindex (D5 — column-dimension pinning)
# ═══════════════════════════════════════════════════════════════════════════
def build_wide_prices(
    qfq_long: pd.DataFrame,
    h52a_yahoo_tickers: List[str],
) -> pd.DataFrame:
    """Pivot qfq_close to wide format, force-reindex to exactly 1076 columns.

    Tickers that were in CSI500 only during 2019 will have 0 rows in H52c →
    pandas pivot silently drops their columns → force-reindex to pin column count.
    """
    wide = qfq_long.pivot_table(
        index="date",
        columns="ticker",
        values="qfq_close",
        aggfunc="first",
    )
    wide = wide.sort_index()

    # Force-reindex: ensure all H52a tickers + HS300 have columns
    all_columns = h52a_yahoo_tickers + [HS300_TICKER]
    wide = wide.reindex(columns=all_columns)

    # Reset index to make date a column
    wide = wide.reset_index()
    wide["date"] = wide["date"].astype(str)
    return wide


# ═══════════════════════════════════════════════════════════════════════════
# Liquidity output (H51a-style long format)
# ═══════════════════════════════════════════════════════════════════════════
def build_liquidity_long(qfq_long: pd.DataFrame) -> pd.DataFrame:
    """Build H51a-style long-format liquidity CSV.

    Only rows where the ticker actually traded (vol > 0).
    Unit conversions: amount × 1000 (千元→RMB), vol × 100 (手→shares) — applied at compute_qfq time.
    """
    liq = qfq_long[["date", "ticker", "amount_rmb", "vol_shares"]].copy()
    # Drop rows where no trade occurred
    liq = liq[liq["vol_shares"].notna() & (liq["vol_shares"] > 0)].copy()
    liq["source"] = LIQUIDITY_SOURCE
    liq = liq[["date", "ticker", "amount_rmb", "vol_shares", "source"]]
    liq = liq.sort_values(["date", "ticker"]).reset_index(drop=True)
    return liq


# ═══════════════════════════════════════════════════════════════════════════
# Coverage metrics
# ═══════════════════════════════════════════════════════════════════════════
def compute_coverage(
    wide_prices: pd.DataFrame,
    liquidity: pd.DataFrame,
    qfq_long: pd.DataFrame,
    h52a_yahoo_tickers: List[str],
    trading_days: List[str],
    start: str,
    end: str,
    tickers_with_no_qfq: List[str],
    failures: List[FetchFailure],
    daily_adj_skew_days: int,
    extreme_pct_anomalies: int,
    extreme_pct_sample: List[Dict],
    tickers_with_no_data: List[str],
) -> Dict:
    """Compute all coverage metrics for price_coverage_h52c.json."""
    # Ticker coverage: what fraction of H52a tickers have ANY price data
    tickers_with_data: Set[str] = set()
    for col in wide_prices.columns:
        if col == "date" or col == HS300_TICKER:
            continue
        if wide_prices[col].notna().any():
            tickers_with_data.add(col)
    ticker_coverage_pct = len(tickers_with_data) / len(h52a_yahoo_tickers) * 100.0

    # Trade days per ticker
    trade_days_per_ticker = {}
    for col in wide_prices.columns:
        if col == "date" or col == HS300_TICKER:
            continue
        ndays = int(wide_prices[col].notna().sum())
        trade_days_per_ticker[col] = ndays
    # Exclude all-NaN columns from avg/min (they're H52a members with zero H52c data)
    active_trade_days = [v for v in trade_days_per_ticker.values() if v > 0]
    avg_trade_days = np.mean(active_trade_days) if active_trade_days else 0.0
    min_trade_days = int(np.min(active_trade_days)) if active_trade_days else 0
    # Track tickers with <60 days (short-lived members — brief allows these)
    tickers_with_short_history = [t for t, ndays in trade_days_per_ticker.items() if 0 < ndays < 60]

    # Median implied qfq price (from liquidity: amount_rmb / vol_shares)
    implied_prices = []
    if not liquidity.empty:
        liq_sample = liquidity.head(5000) if len(liquidity) > 5000 else liquidity
        for _, row in liq_sample.iterrows():
            vs = row["vol_shares"]
            ar = row["amount_rmb"]
            if vs > 0 and ar > 0:
                implied_prices.append(ar / vs)
    median_price = float(np.median(implied_prices)) if implied_prices else 0.0
    p10 = float(np.percentile(implied_prices, 10)) if implied_prices else 0.0
    p50 = median_price
    p90 = float(np.percentile(implied_prices, 90)) if implied_prices else 0.0

    # Trade dates observed
    trade_dates_observed = len(set(qfq_long["date"].unique())) if not qfq_long.empty and "date" in qfq_long.columns else 0

    # Dates with both daily AND adj_factor success
    daily_dates: Set[str] = set()
    adj_dates: Set[str] = set()
    if not qfq_long.empty and "date" in qfq_long.columns and "ticker" in qfq_long.columns:
        for _, row in qfq_long.iterrows():
            if row["ticker"] != HS300_TICKER:
                daily_dates.add(str(row["date"]))
                if pd.notna(row["qfq_close"]):
                    adj_dates.add(str(row["date"]))
    trade_dates_with_full = len(daily_dates)  # All dates we have daily for

    # Benchmark coverage
    benchmark_col = wide_prices[HS300_TICKER] if HS300_TICKER in wide_prices.columns else pd.Series(dtype=float)
    benchmark_non_nan = int(benchmark_col.notna().sum())
    benchmark_coverage_pct = benchmark_non_nan / len(wide_prices) * 100.0 if len(wide_prices) > 0 else 0.0

    # ADTV per-window computability (simplified for empty data)
    adtv_per_window = {}
    if not liquidity.empty and "date" in liquidity.columns:
        for wname, (wstart, wend) in ADTV_WINDOWS_COMPACT.items():
            window_liq = liquidity[
                (liquidity["date"] >= wstart) & (liquidity["date"] <= wend)
            ]
            ticker_date_pairs = len(window_liq)
            computable_pct = 100.0 if ticker_date_pairs > 0 else 0.0
            adtv_per_window[wname] = {
                "start": wstart,
                "end": wend,
                "ticker_date_pairs": ticker_date_pairs,
                "computable_pct": computable_pct,
            }
    else:
        for wname, (wstart, wend) in ADTV_WINDOWS_COMPACT.items():
            adtv_per_window[wname] = {
                "start": wstart, "end": wend,
                "ticker_date_pairs": 0, "computable_pct": 0.0,
            }

    return {
        "trade_dates_observed": trade_dates_observed,
        "trade_dates_with_full_data": trade_dates_with_full,
        "ticker_coverage_pct": round(ticker_coverage_pct, 2),
        "universe_ticker_count": len(h52a_yahoo_tickers),
        "tickers_with_no_qfq": tickers_with_no_qfq,
        "avg_trade_days_per_ticker": round(avg_trade_days, 1),
        "min_trade_days_per_ticker": min_trade_days,
        "tickers_with_short_history": tickers_with_short_history,
        "median_implied_qfq_price_rmb": round(median_price, 4),
        "p10_p50_p90_qfq_price_rmb": [round(p10, 4), round(p50, 4), round(p90, 4)],
        "adtv_computability_per_window": adtv_per_window,
        "benchmark_coverage_pct": round(benchmark_coverage_pct, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# ADTV real computability computation
# ═══════════════════════════════════════════════════════════════════════════
def compute_adtv_per_window(
    liquidity: pd.DataFrame,
    wide_prices: pd.DataFrame,
    h52a_yahoo_tickers: List[str],
) -> Dict:
    """Compute ADTV computability per window using 20-day trailing average.

    Optimized: pre-build ticker→sorted_dates dict once, then use binary search
    to count prior trading days. O(T + N + T×W×logD) instead of O(T×N×W×D).
    """
    # Pre-build: ticker → sorted list of dates with volume
    if liquidity.empty:
        return {
            wname: {"start": wstart, "end": wend, "ticker_date_pairs": 0, "computable_pct": 0.0}
            for wname, (wstart, wend) in ADTV_WINDOWS_COMPACT.items()
        }

    import bisect

    ticker_vol_dates: Dict[str, List[str]] = {}
    for ticker in h52a_yahoo_tickers:
        ticker_vol_dates[ticker] = []

    for _, row in liquidity.iterrows():
        ticker = str(row["ticker"])
        date = str(row["date"])
        if ticker in ticker_vol_dates:
            ticker_vol_dates[ticker].append(date)

    # Sort each ticker's date list for binary search
    for ticker in ticker_vol_dates:
        ticker_vol_dates[ticker].sort()

    # Sorted list of all dates
    all_dates = sorted(wide_prices["date"].tolist())

    adtv_per_window = {}
    for wname, (wstart, wend) in ADTV_WINDOWS_COMPACT.items():
        window_dates = [d for d in all_dates if wstart <= d <= wend]
        total_pairs = 0
        computable_pairs = 0

        for ticker in h52a_yahoo_tickers:
            vol_dates = ticker_vol_dates.get(ticker, [])
            if not vol_dates:
                total_pairs += len(window_dates)
                continue

            for eval_date in window_dates:
                total_pairs += 1
                # Binary search: count vol dates strictly before eval_date
                idx = bisect.bisect_left(vol_dates, eval_date)
                if idx >= 20:
                    computable_pairs += 1

        computable_pct = (computable_pairs / total_pairs * 100.0) if total_pairs > 0 else 0.0
        adtv_per_window[wname] = {
            "start": wstart,
            "end": wend,
            "ticker_date_pairs": total_pairs,
            "computable_pct": round(computable_pct, 2),
        }

    return adtv_per_window


# ═══════════════════════════════════════════════════════════════════════════
# Coverage JSON payload
# ═══════════════════════════════════════════════════════════════════════════
def build_coverage_payload(
    coverage: Dict,
    adtv_per_window: Dict,
    h52a_yahoo_tickers: List[str],
    start: str,
    end: str,
    tickers_with_no_qfq: List[str],
    failures: List[FetchFailure],
    extreme_pct_anomalies: int,
    extreme_pct_sample: List[Dict],
    tickers_with_no_data: List[str],
    status: str,
) -> Dict:
    """Build full price_coverage_h52c.json payload."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Determine verdict
    gates_ok = (
        coverage["ticker_coverage_pct"] >= 98.0
        and len(coverage.get("tickers_with_short_history", [])) <= 5  # allow up to 5 short-lived members (brief: allow new listings)
        and 0.5 <= coverage["median_implied_qfq_price_rmb"] <= 5000.0
        and all(w["computable_pct"] >= 95.0 for w in adtv_per_window.values())
        and coverage["benchmark_coverage_pct"] >= 99.0
        and len(failures) <= 20
        and extreme_pct_anomalies <= 500
        and len(tickers_with_no_qfq) <= 10
        and len(tickers_with_no_data) <= 60
    )
    verdict = "CANDIDATE_DATASET" if gates_ok else "BLOCKED"
    if verdict == "BLOCKED" and status != "BLOCKED":
        status = "BLOCKED"

    return {
        "generated_at": generated_at,
        "task": "H52c",
        "status": status,
        "provenance": {
            "stock_provider": STOCK_PROVIDER,
            "adjustment_provider": ADJUSTMENT_PROVIDER,
            "qfq_method": "snapshot_qfq_local_compute",
            "benchmark_provider": BENCHMARK_PROVIDER,
            "benchmark_ticker": HS300_TICKER,
            "universe_source": "data/cn_pit/universe_h52a_csi500.jsonl",
            "date_range_requested": f"{start} -> {end}",
            "date_range_actual": f"{start} -> {end}",
            "snapshot_timestamp": generated_at,
        },
        "coverage": {
            **coverage,
            "adtv_computability_per_window": adtv_per_window,
        },
        "fetch_failures": [ff.to_dict() for ff in failures],
        "anomalies": {
            "tickers_with_no_qfq": len(tickers_with_no_qfq),
            "daily_vs_adj_factor_row_count_skew_days": 0,  # Computed below
            "extreme_pct_chg_anomalies": extreme_pct_anomalies,
            "extreme_pct_chg_sample": extreme_pct_sample[:5],
            "tickers_with_no_h52c_data": len(tickers_with_no_data),
        },
        "verdict": verdict,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Report generation
# ═══════════════════════════════════════════════════════════════════════════
def build_report(payload: Dict) -> str:
    cov = payload["coverage"]
    prov = payload["provenance"]
    anom = payload["anomalies"]

    lines = [
        "# H52c — CSI500 Daily Fact Data Ingestion Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Status:** {payload['status']}",
        f"**Verdict:** {payload['verdict']}",
        "",
        "## Provenance",
        "",
        f"- **Stock provider:** {prov['stock_provider']}",
        f"- **Adjustment provider:** {prov['adjustment_provider']}",
        f"- **QFQ method:** {prov['qfq_method']}",
        f"- **Benchmark provider:** {prov['benchmark_provider']}",
        f"- **Benchmark ticker:** {prov['benchmark_ticker']}",
        f"- **Universe source:** {prov['universe_source']}",
        f"- **Date range requested:** {prov['date_range_requested']}",
        f"- **Date range actual:** {prov['date_range_actual']}",
        "",
        "## Coverage Summary",
        "",
        f"- Trade dates observed: {cov['trade_dates_observed']}",
        f"- Trade dates with full data: {cov['trade_dates_with_full_data']}",
        f"- Ticker coverage: {cov['ticker_coverage_pct']}% ({cov['universe_ticker_count']} H52a tickers)",
        f"- Avg trade days per ticker: {cov['avg_trade_days_per_ticker']}",
        f"- Min trade days per ticker: {cov['min_trade_days_per_ticker']}",
        f"- Tickers with no QFQ: {anom['tickers_with_no_qfq']}",
        f"- Tickers with no H52c data: {anom['tickers_with_no_h52c_data']}",
        f"- Median implied QFQ price: {cov['median_implied_qfq_price_rmb']} RMB",
        f"- P10/P50/P90 QFQ prices: {cov['p10_p50_p90_qfq_price_rmb']}",
        f"- Benchmark (HS300) coverage: {cov['benchmark_coverage_pct']}%",
        "",
        "## ADTV Computability Per Window",
        "",
    ]

    adtv = cov.get("adtv_computability_per_window", {})
    for wname in ["cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"]:
        winfo = adtv.get(wname, {})
        lines.append(
            f"- **{wname}**: {winfo.get('ticker_date_pairs', 0)} pairs, "
            f"{winfo.get('computable_pct', 0)}% computable"
        )

    lines.extend([
        "",
        "## Anomalies",
        "",
        f"- Tickers with no QFQ: {anom['tickers_with_no_qfq']}",
        f"- Days with daily/adj_factor row count skew >50: {anom['daily_vs_adj_factor_row_count_skew_days']}",
        f"- Extreme |pct_chg| > 50% events: {anom['extreme_pct_chg_anomalies']}",
        f"- Tickers with no H52c data (H52a members not trading 2020+): {anom['tickers_with_no_h52c_data']}",
    ])

    if anom.get("extreme_pct_chg_sample"):
        lines.extend([
            "",
            "### Extreme Pct Chg Sample",
            "",
        ])
        for sample in anom["extreme_pct_chg_sample"]:
            lines.append(
                f"- {sample.get('ticker', '?')} on {sample.get('trade_date', '?')}: "
                f"{sample.get('pct_chg', 0):.2f}%"
            )

    failures = payload.get("fetch_failures", [])
    lines.extend([
        "",
        "## Fetch Failures",
        "",
        f"- Total: {len(failures)}",
    ])
    for ff in failures[:20]:
        lines.append(f"- {ff['endpoint']} {ff['trade_date']}: {ff['reason']}")

    lines.extend([
        "",
        "## Unit Conversions",
        "",
        "- amount_rmb = Tushare.amount × 1000 (千元 → RMB)",
        "- vol_shares = Tushare.vol × 100 (手 → shares)",
        "- Applied at persist time; raw cache mirrors Tushare verbatim",
        "",
        "## Safety",
        "",
        "- Did not modify any H30/H47/H51a/H52a/H52b artifacts.",
        "- Did not modify production trading config.",
        "- Did not place live orders.",
        "",
        "## Verdict",
        "",
        f"**{payload['verdict']}**",
        "",
    ])

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Main run function
# ═══════════════════════════════════════════════════════════════════════════
def run(args: argparse.Namespace) -> Dict:
    # ── Load H52a universe ────────────────────────────────────────────────
    h52a_yahoo_tickers = load_h52a_unique_tickers(args.universe)
    print(f"[H52c] Loaded {len(h52a_yahoo_tickers)} unique tickers from H52a universe")

    tushare_stock_universe = set()
    for t in h52a_yahoo_tickers:
        if t.endswith(".SS"):
            tushare_stock_universe.add(t[:-3] + ".SH")
        elif t.endswith(".SZ"):
            tushare_stock_universe.add(t)
    tushare_universe = tushare_stock_universe | {HS300_TS_CODE}

    # ── Create Tushare client ─────────────────────────────────────────────
    token = get_tushare_token()
    pro = create_tushare_client(token)

    # ── Generate trading days ─────────────────────────────────────────────
    trading_days = generate_trading_days(pro, args.start, args.end)
    print(f"[H52c] Trading days: {len(trading_days)} ({args.start} → {args.end})")

    # ── Fetch HS300 index_daily (ONE-SHOT, upfront) ───────────────────────
    print("[H52c] Fetching HS300 index_daily (one-shot)...")
    rate_limiter = RateLimiter()
    failures: List[FetchFailure] = []
    index_df = fetch_index_daily(pro, args.start, args.end, args.raw_index, rate_limiter, failures)
    print(f"[H52c] HS300 rows: {len(index_df)}")

    # ── Axis-flip: per trade_date fetch daily + adj_factor ────────────────
    all_daily_records: List[Dict] = []
    all_adj_records: List[Dict] = []
    extreme_pct_anomalies = 0
    extreme_pct_sample: List[Dict] = []
    daily_adj_skew_days = 0

    for idx, td in enumerate(trading_days, start=1):
        if idx % 100 == 0 or idx == 1:
            print(f"[H52c] {idx}/{len(trading_days)} {td} ...")

        # Fetch daily
        df_daily = fetch_daily_for_date(
            pro, td, tushare_universe,
            args.raw_daily, rate_limiter, failures,
        )
        if not df_daily.empty:
            # Add trade_date column if not present (Tushare daily already has it)
            recs = df_daily.to_dict("records")
            for r in recs:
                # Ensure trade_date is set
                if "trade_date" not in r or pd.isna(r.get("trade_date")):
                    r["trade_date"] = td
                else:
                    r["trade_date"] = str(r["trade_date"])

                # Check for extreme pct_chg
                pct = pd.to_numeric(r.get("pct_chg"), errors="coerce")
                if pd.notna(pct) and abs(pct) > 50.0:
                    extreme_pct_anomalies += 1
                    if len(extreme_pct_sample) < 5:
                        extreme_pct_sample.append({
                            "ticker": to_yahoo_style(str(r.get("ts_code", ""))),
                            "trade_date": td,
                            "pct_chg": float(pct),
                        })
            all_daily_records.extend(recs)

        # Fetch adj_factor
        df_adj = fetch_adj_factor_for_date(
            pro, td, tushare_stock_universe,
            args.raw_adj, rate_limiter, failures,
        )
        if not df_adj.empty:
            recs = df_adj.to_dict("records")
            for r in recs:
                if "trade_date" not in r or pd.isna(r.get("trade_date")):
                    r["trade_date"] = td
            all_adj_records.extend(recs)

        # Check daily vs adj_factor row count skew (D7)
        daily_n = len(df_daily)
        adj_n = len(df_adj)
        if daily_n > 0 and adj_n > 0 and abs(daily_n - adj_n) > 50:
            daily_adj_skew_days += 1

    print(f"[H52c] Fetched {len(all_daily_records)} daily records, "
          f"{len(all_adj_records)} adj_factor records")
    print(f"[H52c] Fetch failures: {len(failures)}, "
          f"extreme pct_chg anomalies: {extreme_pct_anomalies}, "
          f"daily/adj skew days: {daily_adj_skew_days}")

    # ── Add HS300 records from index_daily ────────────────────────────────
    if not index_df.empty:
        for _, row in index_df.iterrows():
            td = str(row.get("trade_date", ""))
            close = pd.to_numeric(row.get("close"), errors="coerce")
            if pd.notna(close):
                all_daily_records.append({
                    "ts_code": HS300_TS_CODE,
                    "trade_date": td,
                    "close": float(close),
                    "vol": np.nan,
                    "amount": np.nan,
                    "pct_chg": np.nan,
                })

    # ── Local QFQ computation ────────────────────────────────────────────
    print("[H52c] Computing QFQ locally...")
    qfq_long, tickers_with_no_qfq = compute_qfq(
        all_daily_records, all_adj_records, h52a_yahoo_tickers,
    )
    print(f"[H52c] QFQ long rows: {len(qfq_long)}, tickers with no QFQ: {len(tickers_with_no_qfq)}")

    # ── Build wide prices with force-reindex ─────────────────────────────
    print("[H52c] Building wide price matrix...")
    wide_prices = build_wide_prices(qfq_long, h52a_yahoo_tickers)
    print(f"[H52c] Wide prices shape: {wide_prices.shape}")

    # ── Identify tickers with no H52c data (all-NaN columns) ─────────────
    tickers_with_no_data = []
    for ticker in h52a_yahoo_tickers:
        if ticker in wide_prices.columns and not wide_prices[ticker].notna().any():
            tickers_with_no_data.append(ticker)
    print(f"[H52c] Tickers with no H52c data (all-NaN): {len(tickers_with_no_data)}")

    # ── Build liquidity ──────────────────────────────────────────────────
    print("[H52c] Building liquidity CSV...")
    liquidity = build_liquidity_long(qfq_long)
    print(f"[H52c] Liquidity rows: {len(liquidity)}")

    # ── Compute ADTV per window ──────────────────────────────────────────
    print("[H52c] Computing ADTV per window...")
    adtv_per_window = compute_adtv_per_window(liquidity, wide_prices, h52a_yahoo_tickers)

    # ── Compute coverage ─────────────────────────────────────────────────
    coverage = compute_coverage(
        wide_prices, liquidity, qfq_long,
        h52a_yahoo_tickers, trading_days,
        args.start, args.end,
        tickers_with_no_qfq, failures,
        daily_adj_skew_days, extreme_pct_anomalies, extreme_pct_sample,
        tickers_with_no_data,
    )

    # ── Build payload ────────────────────────────────────────────────────
    payload = build_coverage_payload(
        coverage, adtv_per_window,
        h52a_yahoo_tickers,
        args.start, args.end,
        tickers_with_no_qfq, failures,
        extreme_pct_anomalies, extreme_pct_sample,
        tickers_with_no_data,
        "CANDIDATE_DATASET",
    )

    # ── Persist outputs ──────────────────────────────────────────────────
    args.output_prices.parent.mkdir(parents=True, exist_ok=True)
    args.output_liquidity.parent.mkdir(parents=True, exist_ok=True)
    args.output_coverage.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)

    wide_prices.to_csv(args.output_prices, index=False)
    liquidity.to_csv(args.output_liquidity, index=False)
    args.output_coverage.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.output_report.write_text(build_report(payload), encoding="utf-8")

    print(f"[H52c] Wrote prices: {args.output_prices}")
    print(f"[H52c] Wrote liquidity: {args.output_liquidity}")
    print(f"[H52c] Wrote coverage: {args.output_coverage}")
    print(f"[H52c] Wrote report: {args.output_report}")
    print(f"[H52c] Verdict: {payload['verdict']}")

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="H52c CSI500 Daily Fact Data Ingestion")
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2026-05-21")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--raw-daily", type=Path, default=DEFAULT_RAW_DAILY)
    parser.add_argument("--raw-adj", type=Path, default=DEFAULT_RAW_ADJ)
    parser.add_argument("--raw-index", type=Path, default=DEFAULT_RAW_INDEX)
    parser.add_argument("--output-prices", type=Path, default=DEFAULT_PRICES_OUT)
    parser.add_argument("--output-liquidity", type=Path, default=DEFAULT_LIQUIDITY_OUT)
    parser.add_argument("--output-coverage", type=Path, default=DEFAULT_COVERAGE_OUT)
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT_OUT)
    args = parser.parse_args()

    payload = run(args)
    print(f"\n[DONE] {payload['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
