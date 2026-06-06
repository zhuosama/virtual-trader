#!/usr/bin/env python3
"""H51a — Risk Model ADTV Data Ingestion from Tushare daily endpoint.

Ingests amount (daily trading value, RMB) and vol (daily volume, converted from
Tushare 手 to absolute shares by ×100) for all 481 universe tickers covering
2023-10-01 → 2026-05-21.  Outputs a long-format CSV, coverage JSON, and report.

Resumable via per-ticker raw cache in data/cn_pit/raw/h51a_tushare_daily/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data/cn_pit"
RAW_DIR = DATA_DIR / "raw/h51a_tushare_daily"
DEFAULT_UNIVERSE = DATA_DIR / "universe_h30_candidate.jsonl"
DEFAULT_OUTPUT_CSV = DATA_DIR / "liquidity_h51a_daily_amount.csv"
DEFAULT_COVERAGE_OUT = DATA_DIR / "liquidity_coverage_h51a.json"
DEFAULT_REPORT_OUT = ROOT / "reports/h51a_daily_amount_ingestion_report.md"
PROVIDER_LABEL = "tushare:daily"
SOURCE_URL = "https://tushare.pro/document/2?doc_id=25"
DEFAULT_START = "2023-10-01"
DEFAULT_END = "2026-05-21"

# H42 deploy/eval windows for ADTV computability gate
H42_WINDOWS = {
    "cal_2024": ("2024-01-02", "2024-12-31"),
    "h1_2025": ("2025-01-02", "2025-06-30"),
    "h2_2025": ("2025-07-01", "2025-12-31"),
    "ytd_2026": ("2026-01-02", "2026-05-21"),
    "deploy_2025_2026": ("2025-01-02", "2026-05-21"),
}

# ═══════════════════════════════════════════════════════════════════════════
# Token discovery (same pattern as h50a_build_tushare_pit_quality.py)
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
    tried = ["$TUSHARE_TOKEN"] + [str(tp) for tp in token_paths] + [
        "ingest_cn_pit_data._get_tushare_token()", "tushare.get_token()"
    ]
    raise RuntimeError(
        f"Tushare token missing — tried: {', '.join(tried)}. "
        f"Write token to {token_paths[0]} or export TUSHARE_TOKEN."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Ticker conversion
# ═══════════════════════════════════════════════════════════════════════════
def yahoo_to_tushare_code(ticker: str) -> str:
    """Convert Yahoo ticker (000001.SZ) → Tushare code (000001.SZ/000001.SH)."""
    if ticker.endswith(".SS"):
        return ticker[:-3] + ".SH"
    if ticker.endswith(".SZ"):
        return ticker
    raise ValueError(f"unsupported ticker suffix: {ticker}")


# ═══════════════════════════════════════════════════════════════════════════
# Universe loading
# ═══════════════════════════════════════════════════════════════════════════
def load_universe_tickers(path: Path) -> List[str]:
    """Load unique tickers from universe JSONL in sorted order."""
    tickers: Dict[str, None] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ticker = str(row.get("ticker", ""))
            if ticker:
                tickers[ticker] = None
    return sorted(tickers)


# ═══════════════════════════════════════════════════════════════════════════
# Per-ticker raw cache (resumable)
# ═══════════════════════════════════════════════════════════════════════════
def cache_path_for(ticker: str, raw_dir: Path) -> Path:
    """Per-ticker cache path using Tushare code."""
    ts_code = yahoo_to_tushare_code(ticker)
    return raw_dir / f"{ts_code}.csv"


def read_cache(ticker: str, raw_dir: Path, start: str, end: str) -> Optional[pd.DataFrame]:
    """Read cached Tushare daily records. Returns None if incomplete."""
    cp = cache_path_for(ticker, raw_dir)
    if not cp.exists():
        return None
    try:
        df = pd.read_csv(cp)
        if df.empty:
            return None
        if "trade_date" not in df.columns:
            return None
        dates = sorted(df["trade_date"].dropna().astype(str).unique())
        if not dates:
            return None
        cache_min = dates[0].replace("-", "")
        cache_max = dates[-1].replace("-", "")
        req_start = start.replace("-", "")
        req_end = end.replace("-", "")
        # Accept cache if it overlaps with the requested date range:
        # (a) cache starts at or before request start (±10 days tolerance), OR
        # (b) cache is entirely within the request window (recent IPOs etc.)
        if (cache_min <= req_start or abs(int(cache_min) - int(req_start)) <= 10) or (
            cache_min >= req_start and cache_min <= req_end
        ):
            return df
    except Exception:
        pass
    return None


def write_cache(ticker: str, raw_dir: Path, df: pd.DataFrame) -> None:
    """Write raw Tushare records to per-ticker CSV cache."""
    if df is None or df.empty:
        return
    cp = cache_path_for(ticker, raw_dir)
    cp.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cp, index=False)


# ═══════════════════════════════════════════════════════════════════════════
# Date helpers
# ═══════════════════════════════════════════════════════════════════════════
def compact_date(date_str: str) -> str:
    return date_str.replace("-", "")


def dashed_date(date_str: str) -> str:
    """Convert YYYYMMDD → YYYY-MM-DD."""
    s = str(date_str).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


# ═══════════════════════════════════════════════════════════════════════════
# Rate limiter
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class RateLimiter:
    """Hard-cap call rate at 5 calls/sec."""
    min_interval: float = 0.2  # 5 calls/sec
    _last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════
# Data fetching with retry
# ═══════════════════════════════════════════════════════════════════════════
def fetch_daily(
    pro_api,
    ts_code: str,
    start: str,
    end: str,
    rate_limiter: RateLimiter,
    max_retries: int = 5,
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Fetch Tushare daily endpoint with exponential backoff + jitter.

    Returns (DataFrame, error_reason). DataFrame is None if fetch failed.
    Returns empty DataFrame if no data for this ticker/period.
    """
    for attempt in range(max_retries):
        rate_limiter.wait()
        try:
            df = pro_api.daily(
                ts_code=ts_code,
                start_date=compact_date(start),
                end_date=compact_date(end),
                fields="ts_code,trade_date,amount,vol",
            )
            if df is None or df.empty:
                return pd.DataFrame(), None
            return df, None
        except Exception as exc:
            msg = str(exc)
            is_rate_limit = (
                "429" in msg
                or "rate" in msg.lower()
                or "limit" in msg.lower()
                or "too many" in msg.lower()
                or "频繁" in msg
            )
            if not is_rate_limit:
                return None, f"fetch error (non-rate-limit): {msg[:200]}"

            if attempt < max_retries - 1:
                base = min(2 ** (attempt + 1), 60)
                jitter = random.uniform(0, base * 0.5)
                sleep_s = base + jitter
                print(f"  ⏳ Rate-limited for {ts_code}, retry {attempt+1}/{max_retries} "
                      f"after {sleep_s:.1f}s...", flush=True)
                time.sleep(sleep_s)
            else:
                return None, f"rate-limit exhausted after {max_retries} retries: {msg[:200]}"

    return None, "unknown fetch error"


# ═══════════════════════════════════════════════════════════════════════════
# Data normalization
# ═══════════════════════════════════════════════════════════════════════════
def normalize_daily_frame(df: pd.DataFrame, yahoo_ticker: str) -> pd.DataFrame:
    """Normalize Tushare daily output to long-format CSV columns.

    Converts vol from 手 (100-share lots) to absolute shares by multiplying by 100.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "ticker", "amount_rmb", "vol_shares", "source"])

    out = df.copy()
    # Ensure trade_date is dashed
    out["date"] = out["trade_date"].astype(str).map(dashed_date)
    # Numeric conversions
    # Tushare daily.amount unit per docs: 千元 (thousand RMB).
    # Convert to absolute RMB at persistence time.
    out["amount_rmb"] = pd.to_numeric(out.get("amount", 0), errors="coerce") * 1000.0
    # Vol: Tushare returns 手 (100-share lots); convert to absolute shares
    raw_vol = pd.to_numeric(out.get("vol", 0), errors="coerce")
    out["vol_shares"] = raw_vol * 100.0
    out["ticker"] = yahoo_ticker
    out["source"] = PROVIDER_LABEL

    # Drop rows missing both amount and vol
    out = out.dropna(subset=["amount_rmb", "vol_shares"], how="all")

    # Select and sort
    out = out[["date", "ticker", "amount_rmb", "vol_shares", "source"]]
    out = out.sort_values("date").reset_index(drop=True)

    return out


# ═══════════════════════════════════════════════════════════════════════════
# ADTV computability gate
# ═══════════════════════════════════════════════════════════════════════════
def compute_adtv_gate(
    df: pd.DataFrame,
    windows: Dict[str, Tuple[str, str]],
) -> Dict[str, Any]:
    """Compute fraction of (ticker × eval_date) pairs with computable 20-day trailing ADTV.

    ADTV is computable if there are ≥10 non-NULL amount_rmb rows in the 20 trading days
    prior to (and including) the eval_date.  Lookback spans the FULL dataset per ticker,
    not just the window — otherwise the first 19 days of any window would always fail.
    """
    if df.empty:
        return {
            "overall_pct": 0.0,
            "per_window": {w: 0.0 for w in windows},
            "details": "empty dataset",
        }

    df_full = df.sort_values(["ticker", "date"]).reset_index(drop=True)

    per_window = {}
    all_pairs_total = 0
    all_pairs_computable = 0

    for wname, (wstart, wend) in windows.items():
        tickers_with_data = df_full[df_full["date"].between(wstart, wend)]["ticker"].unique()
        total_pairs = 0
        computable = 0

        for ticker in tickers_with_data:
            # Full ticker data (all dates, not window-filtered) for lookback
            tdf = df_full[df_full["ticker"] == ticker].sort_values("date")
            dates = tdf["date"].tolist()
            amounts = tdf["amount_rmb"].tolist()

            for i, eval_date in enumerate(dates):
                if wstart <= eval_date <= wend:
                    total_pairs += 1
                    # Look back up to 20 rows (including current) from FULL data
                    lookback = amounts[max(0, i - 19):i + 1]
                    non_null = sum(1 for a in lookback if pd.notna(a) and a > 0)
                    if non_null >= 10:
                        computable += 1

        pct = (computable / total_pairs * 100) if total_pairs > 0 else 0.0
        per_window[wname] = {"pct": pct, "total_pairs": total_pairs, "computable": computable}
        all_pairs_total += total_pairs
        all_pairs_computable += computable

    overall_pct = (all_pairs_computable / all_pairs_total * 100) if all_pairs_total > 0 else 0.0

    return {
        "overall_pct": round(overall_pct, 2),
        "per_window": {w: round(v["pct"], 2) for w, v in per_window.items()},
        "per_window_detail": per_window,
        "total_pairs": all_pairs_total,
        "computable_pairs": all_pairs_computable,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Coverage computation
# ═══════════════════════════════════════════════════════════════════════════
def build_coverage(
    df: pd.DataFrame,
    tickers: List[str],
    fetch_failures: List[Dict[str, str]],
    adtv_gate: Dict[str, Any],
    snapshot_date: str,
) -> Dict[str, Any]:
    """Build coverage JSON with all 4 gates."""
    total_tickers = len(tickers)
    covered_tickers = set(df["ticker"].unique()) if not df.empty else set()
    ticker_coverage_count = len(covered_tickers)
    ticker_coverage_pct = round(ticker_coverage_count / total_tickers * 100, 2) if total_tickers else 0.0

    # Per-ticker row counts
    per_ticker_rows = {}
    if not df.empty:
        per_ticker_rows = df.groupby("ticker").size().to_dict()
    avg_rows = sum(per_ticker_rows.values()) / len(per_ticker_rows) if per_ticker_rows else 0.0

    # Per-date ticker counts
    per_date_counts = {}
    if not df.empty:
        per_date_counts = df.groupby("date")["ticker"].nunique().to_dict()

    # Date range
    date_range = f"{df['date'].min()} → {df['date'].max()}" if not df.empty and "date" in df.columns else "N/A"

    # Build adtv_computability_per_window (per brief spec with start/end/ticker_date_pairs/computable_pct)
    adtv_computability_per_window = {}
    for wname, (wstart, wend) in H42_WINDOWS.items():
        detail = adtv_gate.get("per_window_detail", {}).get(wname, {})
        adtv_computability_per_window[wname] = {
            "start": wstart,
            "end": wend,
            "ticker_date_pairs": detail.get("total_pairs", 0),
            "computable_pct": round(detail.get("pct", 0.0), 2),
        }

    # Gates
    gate_ticker = ticker_coverage_pct >= 98.0
    gate_avg_rows = avg_rows >= 600.0
    gate_adtv = adtv_gate["overall_pct"] >= 95.0
    gate_failures = len(fetch_failures) <= 10
    gate_per_window = all(v["computable_pct"] >= 95.0 for v in adtv_computability_per_window.values())

    all_gates_ok = gate_ticker and gate_avg_rows and gate_adtv and gate_failures and gate_per_window

    return {
        "task": "H51a",
        "snapshot_date": snapshot_date,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": {
            "provider": PROVIDER_LABEL,
            "source_url": SOURCE_URL,
            "start": DEFAULT_START,
            "end": DEFAULT_END,
        },
        "verdict": "CANDIDATE_DATASET" if all_gates_ok else "BLOCKED",
        "universe_ticker_count": total_tickers,
        "ticker_coverage_count": ticker_coverage_count,
        "ticker_coverage_pct": ticker_coverage_pct,
        "total_rows": len(df),
        "avg_rows_per_ticker": round(avg_rows, 1),
        "date_range": date_range,
        "columns": ["date", "ticker", "amount_rmb", "vol_shares", "source"],
        "vol_unit": "shares (absolute, ×100 from Tushare 手)",
        "fetch_failures": fetch_failures,
        "fetch_failures_count": len(fetch_failures),
        "gates": {
            "ticker_coverage_ge_98pct": gate_ticker,
            "avg_rows_per_ticker_ge_600": gate_avg_rows,
            "adtv_computable_ge_95pct": gate_adtv,
            "adtv_computable_per_window_ge_95pct": gate_per_window,
            "fetch_failures_le_10": gate_failures,
        },
        "adtv_computability_per_window": adtv_computability_per_window,
        "adtv_gate": adtv_gate,
        "per_ticker_row_count": per_ticker_rows,
        "per_date_ticker_count": per_date_counts,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Report generation
# ═══════════════════════════════════════════════════════════════════════════
def build_report(coverage: Dict[str, Any]) -> str:
    c = coverage
    gates = c["gates"]
    adtv = c["adtv_gate"]
    lines = [
        "# H51a — Risk Model ADTV Data Ingestion Report",
        "",
        f"**Generated:** {c['generated_at']}",
        f"**Task:** {c['task']}",
        f"**Status:** {c['verdict']}",
        f"**Provider:** {c['provenance']['provider']}",
        f"**Source URL:** {c['provenance']['source_url']}",
        "",
        "## Coverage Summary",
        "",
        f"- Universe tickers: {c['universe_ticker_count']}",
        f"- Ticker coverage: {c['ticker_coverage_count']}/{c['universe_ticker_count']} ({c['ticker_coverage_pct']}%)",
        f"- Total rows: {c['total_rows']}",
        f"- Avg rows per ticker: {c['avg_rows_per_ticker']}",
        f"- Date range: {c['date_range']}",
        f"- Columns: {', '.join(c['columns'])}",
        f"- Vol unit: {c['vol_unit']}",
        "",
        "## Coverage Gates",
        "",
        f"| Gate | Threshold | Actual | Pass |",
        f"|------|-----------|--------|------|",
        f"| Ticker coverage ≥ 98% | 98% | {c['ticker_coverage_pct']}% | {gates['ticker_coverage_ge_98pct']} |",
        f"| Avg rows/ticker ≥ 600 | 600 | {c['avg_rows_per_ticker']} | {gates['avg_rows_per_ticker_ge_600']} |",
        f"| ADTV computable ≥ 95% | 95% | {adtv['overall_pct']}% | {gates['adtv_computable_ge_95pct']} |",
        f"| ADTV per-window ≥ 95% (all 5) | 95% | — | {gates.get('adtv_computable_per_window_ge_95pct', False)} |",
        f"| Fetch failures ≤ 10 | 10 | {c['fetch_failures_count']} | {gates['fetch_failures_le_10']} |",
        "",
        "## ADTV Computability by Window",
        "",
    ]

    for wname, wpct in adtv.get("per_window", {}).items():
        detail = adtv.get("per_window_detail", {}).get(wname, {})
        lines.append(f"- **{wname}**: {wpct}% ({detail.get('computable', '?')}/{detail.get('total_pairs', '?')} pairs)")

    lines.extend([
        "",
        "## Fetch Failures",
        "",
    ])
    failures = c.get("fetch_failures", [])
    if failures:
        for ff in failures:
            lines.append(f"- **{ff['ticker']}**: {ff['reason']}")
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Vol Unit Conversion",
        "",
        "Tushare `daily.vol` returns 手 (100-share lots). The script multiplies by 100",
        "to produce absolute shares in the `vol_shares` column.",
        "",
        "## Safety",
        "",
        "- Did not modify any existing dataset, run JSON, or report.",
        "- Did not modify `liquidity_h33_daily_amount.csv` or `liquidity_h40_h39_candidate_daily_amount.csv`.",
        "- Did not modify production trading config.",
        "- Did not place live orders.",
        "",
        "## Verdict",
        "",
        f"**{c['verdict']}**",
        "",
    ])
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# CSV hash
# ═══════════════════════════════════════════════════════════════════════════
def sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# Main run
# ═══════════════════════════════════════════════════════════════════════════
def run(args: argparse.Namespace) -> int:
    start_time = time.monotonic()

    # Load universe
    tickers = load_universe_tickers(args.universe)
    if args.limit:
        tickers = tickers[: args.limit]
    print(f"Universe: {len(tickers)} tickers (limit={args.limit or 'none'})")

    # ── Token + client (skip for rederive-only) ──
    if not args.rederive_only:
        token = get_tushare_token()
        import tushare as ts
        ts.set_token(token)
        pro = ts.pro_api(token)
    else:
        pro = None  # network path disabled

    # Ensure raw cache dir
    raw_dir = args.raw_dir or RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)

    rate_limiter = RateLimiter()
    fetch_failures: List[Dict[str, str]] = []
    all_frames: List[pd.DataFrame] = []
    cache_hits = 0
    cache_misses = 0

    for idx, ticker in enumerate(tickers, start=1):
        ts_code = yahoo_to_tushare_code(ticker)

        # Check cache
        cached = read_cache(ticker, raw_dir, args.start, args.end)
        if cached is not None:
            print(f"[H51a] {idx}/{len(tickers)} {ticker} (cached, {len(cached)} rows)", flush=True)
            cache_hits += 1
            normalized = normalize_daily_frame(cached, ticker)
            if not normalized.empty:
                all_frames.append(normalized)
            continue

        # ── rederive-only: cache miss → skip (no network) ──
        if args.rederive_only:
            print(f"[H51a] {idx}/{len(tickers)} {ticker} — cache miss, skipping (rederive-only)", flush=True)
            cache_misses += 1
            continue

        cache_misses += 1
        print(f"[H51a] {idx}/{len(tickers)} {ticker} ({ts_code}) fetching...", flush=True)
        raw_df, error = fetch_daily(pro, ts_code, args.start, args.end, rate_limiter)

        if error:
            print(f"  ❌ {ticker}: {error}", flush=True)
            fetch_failures.append({"ticker": ticker, "ts_code": ts_code, "reason": error})
            continue

        if raw_df is None or raw_df.empty:
            print(f"  ⚠️  {ticker}: no data returned", flush=True)
            fetch_failures.append({"ticker": ticker, "ts_code": ts_code, "reason": "empty response"})
            continue

        print(f"  ✓ {ticker}: {len(raw_df)} rows", flush=True)
        # Cache the raw frame
        write_cache(ticker, raw_dir, raw_df)
        # Normalize
        normalized = normalize_daily_frame(raw_df, ticker)
        if not normalized.empty:
            all_frames.append(normalized)

    # Combine all frames
    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        combined = combined.sort_values(["ticker", "date"]).reset_index(drop=True)
    else:
        combined = pd.DataFrame(columns=["date", "ticker", "amount_rmb", "vol_shares", "source"])

    elapsed = time.monotonic() - start_time

    # Compute ADTV gate
    adtv_gate = compute_adtv_gate(combined, H42_WINDOWS)

    # Build coverage
    snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    coverage = build_coverage(combined, tickers, fetch_failures, adtv_gate, snapshot_date)

    # Write outputs
    combined.to_csv(args.output_csv, index=False)
    print(f"\nCSV written: {args.output_csv} ({len(combined)} rows)", flush=True)

    with args.output_coverage.open("w", encoding="utf-8") as fh:
        json.dump(coverage, fh, indent=2, ensure_ascii=False, default=str)
    print(f"Coverage written: {args.output_coverage}", flush=True)

    report = build_report(coverage)
    args.output_report.write_text(report, encoding="utf-8")
    print(f"Report written: {args.output_report}", flush=True)

    # Summary
    print(f"\n{'='*60}")
    print(f"H51a completed in {elapsed:.1f}s")
    print(f"Tickers: {len(tickers)} (cached: {cache_hits}, fetched: {cache_misses})")
    print(f"Total rows: {len(combined)}")
    print(f"Ticker coverage: {coverage['ticker_coverage_pct']}%")
    print(f"Avg rows/ticker: {coverage['avg_rows_per_ticker']}")
    print(f"ADTV computable: {adtv_gate['overall_pct']}%")
    print(f"Fetch failures: {len(fetch_failures)}")
    print(f"Verdict: {coverage['verdict']}")

    # Gate summary
    gates = coverage["gates"]
    for gname, gpass in gates.items():
        status = "✅" if gpass else "❌"
        print(f"  {status} {gname}: {gpass}")

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="H51a — ADTV Data Ingestion from Tushare daily")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE,
                        help="Universe JSONL path")
    parser.add_argument("--start", type=str, default=DEFAULT_START,
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=DEFAULT_END,
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit to first N tickers (for smoke testing)")
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV,
                        help="Output CSV path")
    parser.add_argument("--output-coverage", type=Path, default=DEFAULT_COVERAGE_OUT,
                        help="Coverage JSON path")
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT_OUT,
                        help="Report MD path")
    parser.add_argument("--raw-dir", type=Path, default=None,
                        help="Raw cache directory")
    parser.add_argument("--rederive-only", action="store_true",
                        help="Skip network; re-derive from raw cache only")
    parser.add_argument("--sha256", action="store_true",
                        help="Print SHA256 of output CSV")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
