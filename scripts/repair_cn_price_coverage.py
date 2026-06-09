#!/usr/bin/env python3
"""
H26B — CN PIT Price Coverage Repair Script
===========================================
Non-destructive analysis of price coverage for the HS300 universe.
Identifies missing historical tickers across checkpoints and optionally
fetches missing data into a candidate CSV (never overwrites prices.csv).

Usage:
  # Analysis only (safe, no writes to prices.csv)
  python scripts/repair_cn_price_coverage.py --analyze

  # Analysis + attempt network fetch of missing tickers
  python scripts/repair_cn_price_coverage.py --fetch-missing \
      --start 2020-01-01 --end 2026-05-18

Output artifacts (all under data/cn_pit/ unless specified):
  - price_coverage_h26.json         machine-readable coverage report
  - prices_h26_candidate.csv        optional, only if fetch succeeds
  - reports/h26_price_coverage_report.md  human-readable report (project root)
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "cn_pit"
REPORTS_DIR = PROJECT_ROOT / "reports"

UNIVERSE_FILE = DATA_DIR / "universe.jsonl"
PRICES_FILE = DATA_DIR / "prices.csv"
COVERAGE_JSON = DATA_DIR / "price_coverage_h26.json"
CANDIDATE_CSV = DATA_DIR / "prices_h26_candidate.csv"
REPORT_MD = REPORTS_DIR / "h26_price_coverage_report.md"

# H27 paths — dynamically set by --output-prefix
H27_CANDIDATE_CSV = DATA_DIR / "prices_h27_candidate.csv"
H27_COVERAGE_JSON = DATA_DIR / "price_coverage_h27.json"
H27_REPORT_MD = REPORTS_DIR / "h27_price_backfill_report.md"
H30_CANDIDATE_CSV = DATA_DIR / "prices_h30_candidate.csv"
H30_COVERAGE_JSON = DATA_DIR / "price_coverage_h30.json"
H30_REPORT_MD = REPORTS_DIR / "h30_price_backfill_report.md"

# Qlib paths
QLIB_FEATURES_DIR = Path.home() / ".qlib" / "qlib_data" / "cn_data" / "features"


# ---------------------------------------------------------------------------
# Universe parsing
# ---------------------------------------------------------------------------
def load_universe(path: Path) -> pd.DataFrame:
    """Load universe.jsonl into a DataFrame with ticker, effective_date, end_date."""
    df = pd.read_json(path, lines=True)
    df["effective_date"] = pd.to_datetime(df["effective_date"]).dt.date
    end_dates = pd.to_datetime(df["end_date"].replace("", pd.NA), errors="coerce")
    df["end_date"] = end_dates.dt.date
    df["end_date"] = df["end_date"].apply(lambda d: d if pd.notna(d) else date(9999, 12, 31))
    return df


def active_tickers_on(universe: pd.DataFrame, target_date: date) -> Set[str]:
    """Return the set of tickers active on the given date."""
    mask = (universe["effective_date"] <= target_date) & (
        universe["end_date"] >= target_date
    )
    active = universe.loc[mask, "ticker"]
    return set(active)


# ---------------------------------------------------------------------------
# Prices parsing
# ---------------------------------------------------------------------------
def load_prices(path: Path) -> pd.DataFrame:
    """Load prices.csv. Returns DataFrame with 'date' as index, ticker columns."""
    df = pd.read_csv(path, parse_dates=["date"])
    df.set_index("date", inplace=True)
    return df


def price_ticker_set(prices: pd.DataFrame) -> Set[str]:
    """Return the set of ticker columns in prices.csv (excl date + benchmark)."""
    tickers = set(str(c) for c in prices.columns)
    tickers.discard("date")
    tickers.discard("000300.SS")
    return tickers


def covered_tickers_on(
    prices: pd.DataFrame, target_date, price_tickers: Set[str]
) -> Tuple[Set[str], Set[str]]:
    """
    Return (column_covered, data_covered) for target_date.
    column_covered: active tickers that exist as columns in prices (even if NaN)
    data_covered: active tickers with non-null price data on target_date
    """
    try:
        row = prices.loc[pd.Timestamp(target_date)]
    except KeyError:
        return set(), set()

    if isinstance(row, pd.Series):
        data_tickers = set(str(t) for t in row.dropna().index)
    else:
        data_tickers = set(str(t) for t in row.dropna(axis=1).iloc[0].index)

    data_tickers.discard("000300.SS")
    return price_tickers, data_tickers


# ---------------------------------------------------------------------------
# Checkpoint generation
# ---------------------------------------------------------------------------
def nearest_trading_date(
    prices_index: pd.DatetimeIndex, target: date
) -> Optional[date]:
    """Find the nearest trading day on or after the target date."""
    candidates = prices_index[prices_index >= pd.Timestamp(target)]
    if len(candidates) > 0:
        return candidates[0].date()
    return None


def nearest_trading_date_on_or_before(
    prices_index: pd.DatetimeIndex, target: date
) -> Optional[date]:
    """Find the nearest trading day on or before the target date."""
    candidates = prices_index[prices_index <= pd.Timestamp(target)]
    if len(candidates) > 0:
        return candidates[-1].date()
    return None


def _nearest_trading_or(
    prices_index: pd.DatetimeIndex, target: date, fallback: date
) -> date:
    """Return nearest trading date or fallback."""
    nt = nearest_trading_date(prices_index, target)
    return nt if nt is not None else fallback


def generate_checkpoints(prices_index: pd.DatetimeIndex) -> List[date]:
    """Generate checkpoint dates: first, last, yearly starts, target period.
    All snapped to nearest available trading day."""
    first_date = prices_index.min().date()
    last_date = prices_index.max().date()

    checkpoints = [first_date, last_date]

    # Yearly starts — snap Jan 2 to nearest trading day
    for year in range(first_date.year, last_date.year + 1):
        candidate = nearest_trading_date(prices_index, date(year, 1, 2))
        if candidate and candidate not in checkpoints:
            checkpoints.append(candidate)

    # Target deployment period — snap to nearest trading day
    target_start = nearest_trading_date(prices_index, date(2025, 1, 1))
    if target_start and target_start not in checkpoints:
        checkpoints.append(target_start)

    target_end = nearest_trading_date_on_or_before(prices_index, date(2026, 5, 18))
    if target_end and target_end not in checkpoints:
        checkpoints.append(target_end)

    # Deduplicate and sort
    return sorted(set(checkpoints))


# ---------------------------------------------------------------------------
# Coverage analysis
# ---------------------------------------------------------------------------
def analyze_coverage(
    universe: pd.DataFrame, prices: pd.DataFrame, checkpoints: List[date],
    target_start: Optional[date] = None, target_end: Optional[date] = None,
) -> dict:
    """Run coverage analysis for all checkpoints. Returns report dict.

    H28 (M4): Accepts optional target_start/target_end; defaults to 2025-01-01 / 2026-05-18.
    """
    price_first = prices.index.min().date()
    price_last = prices.index.max().date()

    all_price_tickers = price_ticker_set(prices)

    checkpoint_results = []
    missing_column_set: Set[str] = set()  # Ticketers with no column at all
    missing_data_set: Set[str] = set()  # Ticketers with column but no data

    for cp_date in checkpoints:
        active = active_tickers_on(universe, cp_date)
        col_tickers, data_tickers = covered_tickers_on(
            prices, cp_date, all_price_tickers
        )

        # Column coverage: active tickers that exist as price columns
        column_covered = active & col_tickers
        # Data coverage: active tickers with non-null data
        data_covered = active & data_tickers
        # Missing column: active but no column
        missing_col = active - col_tickers
        # Has column but no data on this date
        col_no_data = column_covered - data_covered

        checkpoint_results.append(
            {
                "date": cp_date.isoformat(),
                "active_universe": len(active),
                "column_covered": len(column_covered),
                "data_covered": len(data_covered),
                "missing_col_count": len(missing_col),  # no column at all
                "missing_data_count": len(col_no_data),  # column but NaN
                "missing_tickers": sorted(missing_col),
                "column_no_data_tickers": sorted(col_no_data),
            }
        )
        missing_column_set.update(missing_col)
        missing_data_set.update(col_no_data)

    # Target period coverage — use nearest trading dates
    target_start_req = target_start or date(2025, 1, 1)
    target_end_req = target_end or date(2026, 5, 18)

    ts_nearest = _nearest_trading_or(prices.index, target_start_req, target_start_req)
    te_nearest = nearest_trading_date_on_or_before(prices.index, target_end_req) or target_end_req

    def _coverage_at(d: date):
        active = active_tickers_on(universe, d)
        col_t, data_t = covered_tickers_on(prices, d, all_price_tickers)
        col_cov = active & col_t
        data_cov = active & data_t
        return {
            "active_universe": len(active),
            "column_covered": len(col_cov),
            "data_covered": len(data_cov),
            "missing_col": len(active - col_t),
            "missing_data": len(col_cov - data_cov),
            "missing_tickers": sorted(active - col_t),
        }

    report = {
        "price_date_range": f"{price_first.isoformat()} -> {price_last.isoformat()}",
        "price_rows": len(prices),
        "price_ticker_columns": len(all_price_tickers),
        "universe_rows": len(universe),
        "checkpoints": checkpoint_results,
        "target_period": {
            "requested": {
                "start": target_start_req.isoformat(),
                "end": target_end_req.isoformat(),
            },
            "nearest_trading": {
                "start": ts_nearest.isoformat(),
                "end": te_nearest.isoformat(),
            },
            "start": _coverage_at(ts_nearest),
            "end": _coverage_at(te_nearest),
        },
        "union_missing_columns": sorted(missing_column_set),
        "union_missing_columns_count": len(missing_column_set),
        "union_missing_data": sorted(missing_data_set),
        "union_missing_data_count": len(missing_data_set),
    }
    return report


# ---------------------------------------------------------------------------
# Network fetch (best-effort, only to candidate file)
# ---------------------------------------------------------------------------
def fetch_missing_tickers(
    tickers: List[str],
    start_date: str,
    end_date: str,
    allow_akshare: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Attempt to fetch historical prices for missing tickers.
    Tries multiple free sources in order.
    Returns DataFrame with date index and ticker columns, or None if all fail.
    """
    results = {}

    # --- Source 1: yfinance (AkShare wrapper if available, else yfinance) ---
    try:
        import yfinance as yf

        print(f"[fetch] Trying yfinance for {len(tickers)} tickers...")
        # Convert tickers like 000001.SZ → 000001.SZ (yfinance uses same format)
        data = yf.download(
            tickers,
            start=start_date,
            end=end_date,
            progress=False,
            auto_adjust=True,
            timeout=20,
            threads=True,
        )
        if not data.empty:
            # yfinance returns MultiIndex columns: (Adj Close, ticker), etc.
            if isinstance(data.columns, pd.MultiIndex):
                close = data["Close"] if "Close" in data else data["Adj Close"]
            elif "Close" in data.columns or "Adj Close" in data.columns:
                close_col = "Close" if "Close" in data.columns else "Adj Close"
                close = pd.DataFrame({t: data[close_col] for t in tickers})
            else:
                close = data

            if not close.empty:
                close = close.dropna(how="all")
                if not close.empty:
                    results["yfinance"] = close
                    print(
                        f"[fetch] yfinance succeeded: {len(close.columns)} tickers, "
                        f"{len(close)} rows"
                    )
    except Exception as e:
        print(f"[fetch] yfinance failed: {e}")

    # --- Source 2: AkShare ---
    if allow_akshare and not results:
        try:
            import akshare as ak

            print(f"[fetch] Trying akshare for individual tickers...")
            akshare_data = {}
            for ticker in tickers[:20]:  # Limit to avoid rate-limiting
                try:
                    # AkShare stock_zh_a_hist expects bare A-share code (000001, 600519).
                    code = ticker.split(".")[0]

                    df = ak.stock_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date=start_date.replace("-", ""),
                        end_date=end_date.replace("-", ""),
                        adjust="qfq",
                    )
                    if df is not None and not df.empty:
                        df["日期"] = pd.to_datetime(df["日期"])
                        df.set_index("日期", inplace=True)
                        akshare_data[ticker] = df["收盘"]
                except Exception:
                    pass

            if akshare_data:
                result = pd.DataFrame(akshare_data)
                result = result.dropna(how="all")
                if not result.empty:
                    results["akshare"] = result
                    print(
                        f"[fetch] akshare succeeded: {len(result.columns)} tickers, "
                        f"{len(result)} rows"
                    )
        except Exception as e:
            print(f"[fetch] akshare failed: {e}")

    if results:
        # Merge results from sources
        combined = pd.DataFrame()
        for source, df in results.items():
            if combined.empty:
                combined = df
            else:
                combined = combined.join(df, how="outer")

        combined.index.name = "date"
        combined = combined.sort_index()
        return combined

    return None


def merge_candidate(
    original: pd.DataFrame, new_data: pd.DataFrame, tickers_to_add: Set[str]
) -> Tuple[pd.DataFrame, Dict]:
    """
    Merge new price data into a copy of the original DataFrame, preserving all
    existing columns and adding new ones from new_data. Uses reindex + combine_first
    to only fill NaN positions without overwriting existing values.

    H28 (M2): Returns (candidate_df, stats_dict) so callers can report
    out-of-range rows.
    """
    candidate = original.copy()
    stats = {"added": [], "out_of_range_rows": {}, "in_range_nonnull_rows": {}}

    for ticker in tickers_to_add:
        if ticker not in new_data.columns:
            continue
        series = new_data[ticker]
        aligned = series.reindex(candidate.index)
        # Fill only NaN positions: supports both new columns and NaN patching
        if ticker in candidate.columns:
            candidate[ticker] = candidate[ticker].combine_first(aligned)
        else:
            candidate[ticker] = aligned
        stats["added"].append(ticker)
        stats["in_range_nonnull_rows"][ticker] = int(aligned.notna().sum())
        stats["out_of_range_rows"][ticker] = int(
            series.notna().sum() - aligned.notna().sum()
        )
    return candidate, stats


def inspect_existing_candidate(original: pd.DataFrame, candidate_path: Path = None) -> Optional[dict]:
    """Summarize an existing candidate CSV without fetching network data.

    H28 (M6): Reuses candidate_safety_checks internally so the two paths
    (direct safety check vs. inspect) produce identical conclusions.
    """
    if candidate_path is None:
        candidate_path = CANDIDATE_CSV
    if not candidate_path.exists():
        return None

    candidate = load_prices(candidate_path)
    safety = candidate_safety_checks(original, candidate)
    original_cols = set(original.columns)
    candidate_cols = set(candidate.columns)
    new_tickers = sorted(candidate_cols - original_cols - {"date"})

    return {
        "status": "existing",
        "attempted": None,
        "fetched_by_provider": None,
        "actually_added": len(new_tickers),
        "new_tickers": new_tickers,
        "improvement": bool(new_tickers),
        "safety_passed": safety["passed"],
        "safety_issues": safety["issues"],
        "missing_existing_cols": safety["missing_existing_cols"],
        "reduced_nonnull_existing_cols": safety["reduced_nonnull_existing_cols"],
    }


# ---------------------------------------------------------------------------
# Qlib features probe
# ---------------------------------------------------------------------------
def probe_qlib_features() -> dict:
    """Probe the Qlib features directory. Returns status dict."""
    result = {
        "qlib_features_dir": str(QLIB_FEATURES_DIR),
        "qlib_features_present": False,
        "qlib_features_file_count": 0,
        "qlib_features_error": None,
    }
    if QLIB_FEATURES_DIR.exists():
        try:
            files = list(QLIB_FEATURES_DIR.glob("*"))
            result["qlib_features_present"] = len(files) > 0
            result["qlib_features_file_count"] = len(files)
        except Exception as e:
            result["qlib_features_error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# BaoStock fetch (free, no token)
# ---------------------------------------------------------------------------
def fetch_baostock(
    tickers: List[str], start_date: str, end_date: str
) -> pd.DataFrame:
    """Fetch tickers from BaoStock. Returns DataFrame with ticker columns."""
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        print(f"[baostock] Login failed: {lg.error_msg}")
        bs.logout()
        return pd.DataFrame()

    # Convert tickers: 000001.SZ → sz.000001, 600519.SS → sh.600519
    baostock_data = {}
    for ticker in tickers:
        try:
            code = ticker.split(".")[0]
            suffix = ticker.split(".")[1].upper()
            market = "sh" if suffix == "SS" else "sz"
            bs_code = f"{market}.{code}"

            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,close",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",  # 前复权
            )
            if rs.error_code != "0":
                continue

            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue

            df = pd.DataFrame(rows, columns=["date", "close"])
            df["date"] = pd.to_datetime(df["date"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df.set_index("date", inplace=True)
            series = df["close"].dropna()
            if len(series) > 0:
                baostock_data[ticker] = series
        except Exception:
            pass

    bs.logout()

    if baostock_data:
        result = pd.DataFrame(baostock_data).sort_index()
        return result
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Candidate safety checks
# ---------------------------------------------------------------------------
def candidate_safety_checks(
    original: pd.DataFrame, candidate: pd.DataFrame
) -> dict:
    """Run safety checks comparing candidate to original prices.csv."""
    issues = []
    original_cols = set(original.columns)
    candidate_cols = set(candidate.columns)

    # All original columns present
    missing_existing = sorted(original_cols - candidate_cols)
    if missing_existing:
        issues.append(f"Missing {len(missing_existing)} original columns: {missing_existing[:5]}...")

    # Original non-null counts unchanged or higher
    reduced_nonnull = []
    for col in original_cols & candidate_cols:
        orig_nn = original[col].notna().sum()
        cand_nn = candidate[col].notna().sum()
        if cand_nn < orig_nn:
            reduced_nonnull.append(f"{col}({orig_nn}->{cand_nn})")
    if reduced_nonnull:
        issues.append(f"{len(reduced_nonnull)} columns have reduced non-null counts")

    # Benchmark present
    if "000300.SS" not in candidate_cols:
        issues.append("Benchmark 000300.SS missing from candidate")

    # Row count matches
    if len(candidate) != len(original):
        issues.append(f"Row count mismatch: {len(original)} vs {len(candidate)}")

    # Date range matches
    if len(candidate) > 0 and len(original) > 0:
        if candidate.index.min() != original.index.min():
            issues.append(f"Min date mismatch: {original.index.min()} vs {candidate.index.min()}")
        if candidate.index.max() != original.index.max():
            issues.append(f"Max date mismatch: {original.index.max()} vs {candidate.index.max()}")

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "missing_existing_cols": missing_existing,
        "reduced_nonnull_existing_cols": reduced_nonnull,
    }


def nonempty_ticker_columns(df: pd.DataFrame, tickers: Set[str]) -> Set[str]:
    """Return requested tickers whose fetched series has at least one non-null value."""
    found = set()
    for ticker in tickers:
        if ticker in df.columns and df[ticker].notna().sum() > 0:
            found.add(ticker)
    return found


def output_paths(prefix: str) -> Tuple[Path, Path, Path]:
    """Return (coverage_json, report_md, candidate_csv) for a prefix.

    H28 (L): Only accepts known staged prefixes. Other values raise ValueError.
    """
    normalized = str(prefix or "h26").lower()
    if normalized == "h26":
        return COVERAGE_JSON, REPORT_MD, CANDIDATE_CSV
    if normalized == "h27":
        return H27_COVERAGE_JSON, H27_REPORT_MD, H27_CANDIDATE_CSV
    if normalized == "h30":
        return H30_COVERAGE_JSON, H30_REPORT_MD, H30_CANDIDATE_CSV
    raise ValueError(f"Unsupported output prefix: {prefix!r}. Expected 'h26', 'h27', or 'h30'.")


def write_report_artifacts(
    report: dict,
    coverage_json: Path,
    report_md: Path,
    candidate_info: Optional[dict] = None,
) -> None:
    """Write machine-readable JSON and Markdown coverage report."""
    coverage_json.parent.mkdir(parents=True, exist_ok=True)
    with open(coverage_json, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    with open(report_md, "w") as f:
        f.write(generate_report(report, candidate_info))


def run_incremental_backfill(
    original: pd.DataFrame,
    universe: pd.DataFrame,
    start_date: str,
    end_date: str,
    batch_size: int,
    use_slow_fallbacks: bool = False,
    prefix: str = "h27",
    base_path: Optional[Path] = None,
    candidate_path: Optional[Path] = None,
    coverage_json: Optional[Path] = None,
    report_md: Optional[Path] = None,
) -> dict:
    """Run candidate-aware incremental backfill and write prefixed artifacts."""
    prefix = str(prefix or "h27").lower()
    qlib_probe = probe_qlib_features()
    if coverage_json is None or report_md is None or candidate_path is None:
        default_coverage, default_report, default_candidate = output_paths(prefix)
        coverage_json = coverage_json or default_coverage
        report_md = report_md or default_report
        candidate_path = candidate_path or default_candidate
    if base_path is None:
        if prefix == "h28" and H27_CANDIDATE_CSV.exists():
            base_path = H27_CANDIDATE_CSV
        elif CANDIDATE_CSV.exists():
            base_path = CANDIDATE_CSV
        else:
            base_path = PRICES_FILE
    working = load_prices(base_path)

    initial_report = analyze_coverage(universe, working, generate_checkpoints(working.index))
    missing_before = initial_report["union_missing_columns"]
    to_fill = sorted(set(missing_before) | set(initial_report["union_missing_data"]))
    working_cols = set(working.columns)

    label = prefix.upper()
    print(f"{label} base candidate: {base_path}")
    print(f"Qlib features present: {qlib_probe['qlib_features_present']} ({qlib_probe['qlib_features_file_count']} entries)")
    print(f"Missing columns before {label}: {len(missing_before)}")
    print(f"Column-but-NaN tickers queued: {len(initial_report['union_missing_data'])}")
    print(f"Total to fill: {len(to_fill)}")

    candidate = working.copy()
    fetched_by_provider = 0
    newly_added: Set[str] = set()
    failures = {}

    for i in range(0, len(to_fill), max(batch_size, 1)):
        batch = to_fill[i:i + max(batch_size, 1)]
        print(f"\n[{label}] Batch {i // max(batch_size, 1) + 1}: {len(batch)} tickers")
        fetched = fetch_missing_tickers(
            batch,
            start_date,
            end_date,
            allow_akshare=use_slow_fallbacks,
        )
        batch_success = set()
        if fetched is not None and not fetched.empty:
            batch_set = set(batch)
            batch_success = nonempty_ticker_columns(fetched, batch_set)
            fetched_by_provider += len(batch_success)
            candidate, _merge_stats = merge_candidate(candidate, fetched, batch_success)

        remaining_batch = sorted(set(batch) - batch_success)
        if remaining_batch and use_slow_fallbacks:
            try:
                bs_data = fetch_baostock(remaining_batch, start_date, end_date)
                bs_success = nonempty_ticker_columns(bs_data, set(remaining_batch))
                if bs_success:
                    fetched_by_provider += len(bs_success)
                    candidate, _bs_stats = merge_candidate(candidate, bs_data, bs_success)
                    batch_success |= bs_success
            except Exception as exc:
                for ticker in remaining_batch:
                    failures[ticker] = f"baostock: {exc}"

        for ticker in sorted(set(batch) - batch_success):
            reason = "no provider returned non-null data"
            if not use_slow_fallbacks:
                reason = "no yfinance data; slow fallbacks skipped"
            failures.setdefault(ticker, reason)

        new_now = (set(candidate.columns) - working_cols - {"date"}) & set(batch)
        newly_added.update(new_now)
        print(f"[{label}] Added so far: {len(newly_added)}")

    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    if candidate_path.exists():
        backup_path = Path(str(candidate_path) + ".bak")
        import shutil
        shutil.copy2(candidate_path, backup_path)
        print(f"[{label}] Backed up existing candidate: {backup_path}")
    candidate.to_csv(candidate_path)

    final_report = analyze_coverage(universe, candidate, generate_checkpoints(candidate.index))
    safety = candidate_safety_checks(original, candidate)
    remaining_missing = final_report["union_missing_columns"]
    remaining_column_nan = final_report["union_missing_data"]
    candidate_full_validate_ready = (
        safety["passed"]
        and all(cp["missing_col_count"] == 0 for cp in final_report["checkpoints"])
        and all(cp["missing_data_count"] == 0 for cp in final_report["checkpoints"])
    )
    manual_replacement_recommended = (
        candidate_full_validate_ready
        and len(remaining_missing) == 0
        and len(remaining_column_nan) == 0
    )

    candidate_info = {
        "status": f"{prefix}_created",
        "base_path": str(base_path),
        "qlib_probe": qlib_probe,
        "attempted": len(missing_before),
        "fetched_by_provider": fetched_by_provider,
        "actually_added": len(newly_added),
        "new_tickers": sorted(newly_added),
        f"missing_before_{prefix}": len(missing_before),
        "missing_before": len(missing_before),
        "remaining_missing_columns": len(remaining_missing),
        "remaining_missing_tickers": remaining_missing,
        "remaining_column_nan": len(remaining_column_nan),
        "remaining_column_nan_tickers": remaining_column_nan,
        "candidate_full_validate_ready": candidate_full_validate_ready,
        "manual_replacement_recommended": manual_replacement_recommended,
        "safety": safety,
        "failures": failures,
        "improvement": bool(newly_added),
        "missing_existing_cols": safety["missing_existing_cols"],
        "reduced_nonnull_existing_cols": safety["reduced_nonnull_existing_cols"],
    }

    write_report_artifacts(final_report, coverage_json, report_md, candidate_info)
    print(f"\nWrote {label} candidate: {candidate_path}")
    print(f"Wrote {label} coverage: {coverage_json}")
    print(f"Wrote {label} report: {report_md}")
    print(f"Missing columns after {label}: {len(remaining_missing)}")
    print(f"Manual replacement recommended: {manual_replacement_recommended}")
    return candidate_info


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_report(report: dict, candidate_info: Optional[dict] = None) -> str:
    """Generate Markdown report string from coverage report dict."""
    status = str(candidate_info.get("status", "")) if candidate_info else ""
    if status.startswith("h28"):
        title = "# H28 — CN PIT Price Backfill Report"
    elif status.startswith("h27"):
        title = "# H27 — CN PIT Price Backfill Report"
    else:
        title = "# H26B — CN PIT Price Coverage Report"

    lines = [
        title,
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- **Price date range:** {report['price_date_range']}",
        f"- **Price rows:** {report['price_rows']}",
        f"- **Price columns (tickers):** {report['price_ticker_columns']}",
        f"- **Universe rows:** {report['universe_rows']}",
        f"- **Union missing columns (tickers absent from prices.csv):** {report['union_missing_columns_count']}",
        f"- **Union missing data (have column but NaN on some date):** {report.get('union_missing_data_count', 0)}",
        "",
        "## Target Period Coverage",
        "",
        f"- **Requested:** {report['target_period']['requested']['start']} → {report['target_period']['requested']['end']}",
        f"- **Nearest trading:** {report['target_period']['nearest_trading']['start']} → {report['target_period']['nearest_trading']['end']}",
        "",
        "| Metric | Start | End |",
        "|--------|-------|-----|",
        f"| Active universe | {report['target_period']['start']['active_universe']} | {report['target_period']['end']['active_universe']} |",
        f"| Column coverage | {report['target_period']['start']['column_covered']}/{report['target_period']['start']['active_universe']} | {report['target_period']['end']['column_covered']}/{report['target_period']['end']['active_universe']} |",
        f"| Data coverage | {report['target_period']['start']['data_covered']}/{report['target_period']['start']['active_universe']} | {report['target_period']['end']['data_covered']}/{report['target_period']['end']['active_universe']} |",
        f"| Missing columns | {report['target_period']['start']['missing_col']} | {report['target_period']['end']['missing_col']} |",
        f"| Column-but-NaN | {report['target_period']['start']['missing_data']} | {report['target_period']['end']['missing_data']} |",
        "",
        "## Checkpoint Coverage",
        "",
        "| Date | Active | Col Covered | Data Covered | Missing Col | Col-NaN |",
        "|------|--------|-------------|--------------|-------------|---------|",
    ]

    for cp in report["checkpoints"]:
        lines.append(
            f"| {cp['date']} | {cp['active_universe']} | "
            f"{cp['column_covered']} | {cp['data_covered']} | "
            f"{cp['missing_col_count']} | {cp['missing_data_count']} |"
        )

    lines.extend(
        [
            "",
            "## Coverage Gap Detail",
            "",
        ]
    )

    for cp in report["checkpoints"]:
        if cp["missing_col_count"] > 0:
            lines.append(f"### {cp['date']} — {cp['missing_col_count']} missing columns")
            lines.append("")
            # Truncate long ticker lists for readability
            tickers_list = cp["missing_tickers"]
            shown = tickers_list[:50] if len(tickers_list) > 50 else tickers_list
            for t in shown:
                lines.append(f"- `{t}`")
            if len(tickers_list) > 50:
                lines.append(f"- ... and {len(tickers_list) - 50} more")
            lines.append("")
        if cp.get("missing_data_count", 0) > 0:
            lines.append(
                f"### {cp['date']} — {cp['missing_data_count']} have column but NaN data"
            )
            lines.append("")
            nan_tickers = cp.get("column_no_data_tickers", [])
            shown = nan_tickers[:20] if len(nan_tickers) > 20 else nan_tickers
            for t in shown:
                lines.append(f"- `{t}`")
            if len(nan_tickers) > 20:
                lines.append(f"- ... and {len(nan_tickers) - 20} more")
            lines.append("")

    if candidate_info:
        attempted = candidate_info.get("attempted")
        fetched = candidate_info.get("fetched_by_provider")
        attempted_text = "N/A" if attempted is None else str(attempted)
        fetched_text = "N/A" if fetched is None else str(fetched)
        lines.extend(
            [
                "## Candidate CSV",
                "",
                f"- **Status:** {candidate_info.get('status', 'unknown')}",
                f"- **Attempted:** {attempted_text}",
                f"- **Fetched by provider:** {fetched_text}",
                f"- **Actually added to CSV:** {candidate_info.get('actually_added', len(candidate_info.get('new_tickers', [])))}",
                f"- **New tickers:** {', '.join('`' + t + '`' for t in candidate_info.get('new_tickers', []))}",
                f"- **Coverage improvement:** {'Yes' if candidate_info.get('improvement') else 'No'}",
                f"- **Missing existing columns:** {len(candidate_info.get('missing_existing_cols', []))}",
                f"- **Existing columns with reduced non-null count:** {len(candidate_info.get('reduced_nonnull_existing_cols', []))}",
                f"- **Remaining missing columns:** {candidate_info.get('remaining_missing_columns', 'N/A')}",
                f"- **Candidate full-validate ready:** {candidate_info.get('candidate_full_validate_ready', 'N/A')}",
                f"- **Manual replacement recommended:** {candidate_info.get('manual_replacement_recommended', 'N/A')}",
                "",
            ]
        )

        failures = candidate_info.get("failures", {})
        if failures:
            failure_items = list(failures.items())
            shown = failure_items[:50]
            remaining = len(failure_items) - 50 if len(failure_items) > 50 else 0
            lines.extend([
                f"## Backfill Failures ({len(failure_items)})",
                "",
                "| Ticker | Reason |",
                "|--------|--------|",
            ])
            for ticker, reason in shown:
                reason_str = str(reason).replace("|", "\\|")
                lines.append(f"| {ticker} | {reason_str} |")
            if remaining:
                lines.append(f"| ... | ... and {remaining} more |")
            lines.append("")

    lines.extend(
        [
            "## Recommended Next Data Sources",
            "",
            "To clear the `price_coverage` full-file validation blocker, "
            "the following sources can fill historical gaps:",
            "",
            "1. **Qlib full CN price bundle** — `qlib_data/cn_data/features/` "
            "contains daily OHLCV for all A-shares; run a dedicated "
            "collector to extract missing tickers from the Qlib binary store.",
            "2. **Tushare Pro** — `tushare.pro_bar()` with `ts_code` format; "
            "requires API token. Can fetch full A-share history.",
            "3. **BaoStock** — Free, no token needed. "
            "`bs.query_history_k_data_plus()` supports `sh.600000` format.",
            "4. **AKShare** — Free, no token, but rate-limited. "
            "`ak.stock_zh_a_hist()` supports individual stock history.",
            "5. **Local vendor CSV** — If you have a historical price dump "
            "(Wind/Bloomberg/JoinQuant), merge via the `ticker` column.",
            "",
            "### Recommended approach",
            "",
            "Use the Qlib binary store as the primary source (it already contains "
            "the full HS300 history). Extract missing tickers with:",
            "",
            "```python",
            "from qlib.data import D",
            "# Load from Qlib binary store for missing tickers",
            "# Merge into candidate CSV",
            "```",
            "",
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="H26B — CN PIT Price Coverage Repair"
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run coverage analysis and produce report artifacts",
    )
    parser.add_argument(
        "--fetch-missing",
        action="store_true",
        help="Attempt to fetch missing historical prices from network sources",
    )
    parser.add_argument(
        "--backfill-missing",
        action="store_true",
        help=(
            "Incremental backfill: start from an existing candidate/prices file, "
            "fetch missing columns, and write a prefixed candidate CSV"
        ),
    )
    parser.add_argument(
        "--start", type=str, default="2020-01-01", help="Fetch start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end", type=str, default="2026-05-18", help="Fetch end date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--max-fetch-tickers",
        type=int,
        default=50,
        help="Max number of tickers to attempt fetching (default: 50)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Batch size for yfinance download (default: 10)",
    )
    parser.add_argument(
        "--prices-file",
        type=str,
        default=None,
        help="Analyze a specific prices file instead of default prices.csv",
    )
    parser.add_argument(
        "--universe-file",
        type=str,
        default=None,
        help="Analyze a specific universe JSONL instead of default universe.jsonl",
    )
    parser.add_argument(
        "--output-prefix",
        type=str,
        default="h26",
        help="Output file prefix (default: h26); use h27/h30 for staged backfill outputs",
    )
    parser.add_argument(
        "--slow-fallbacks",
        action="store_true",
        help="Enable slower AKShare/BaoStock fallback fetches during staged backfill",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.analyze and not args.fetch_missing and not args.backfill_missing:
        parser.print_help()
        print("\nERROR: Must specify --analyze, --fetch-missing, and/or --backfill-missing")
        sys.exit(1)

    # --- Load data ---
    print("=" * 60)
    print("CN PIT Price Coverage Repair")
    print("=" * 60)

    universe_path = Path(args.universe_file) if args.universe_file else UNIVERSE_FILE
    if not universe_path.is_absolute():
        universe_path = PROJECT_ROOT / universe_path
    if not universe_path.exists():
        print(f"ERROR: universe file not found: {universe_path}")
        sys.exit(1)
    prices_path = Path(args.prices_file) if args.prices_file else PRICES_FILE
    if not prices_path.is_absolute():
        prices_path = PROJECT_ROOT / prices_path
    if not prices_path.exists():
        print(f"ERROR: prices file not found: {prices_path}")
        sys.exit(1)

    universe = load_universe(universe_path)
    prices = load_prices(prices_path)
    original_prices = load_prices(PRICES_FILE)
    effective_prefix = "h27" if args.backfill_missing and args.output_prefix == "h26" else args.output_prefix
    coverage_json, report_md, candidate_csv = output_paths(effective_prefix)
    print(f"\nLoaded {len(universe)} universe rows from {universe_path.name}")
    print(f"Loaded {len(prices)} price rows × {len(prices.columns)} columns from {prices_path.name}")

    # --- Analyze ---
    if args.analyze:
        print("\n" + "-" * 40)
        print("ANALYSIS MODE")
        print("-" * 40)

        checkpoints = generate_checkpoints(prices.index)
        report = analyze_coverage(
            universe, prices, checkpoints,
            target_start=date.fromisoformat(args.start) if args.start else None,
            target_end=date.fromisoformat(args.end) if args.end else None,
        )

        # Print summary
        print(f"\nPrice range: {report['price_date_range']}")
        print(f"Checkpoints: {len(report['checkpoints'])}")
        print(f"\nTarget period coverage:")
        ts = report["target_period"]
        print(
            f"  Start ({ts['requested']['start']} → {ts['nearest_trading']['start']}): "
            f"col={ts['start']['column_covered']}/{ts['start']['active_universe']} "
            f"data={ts['start']['data_covered']}/{ts['start']['active_universe']} "
            f"({ts['start']['missing_col']} missing columns)"
        )
        print(
            f"  End   ({ts['requested']['end']} → {ts['nearest_trading']['end']}): "
            f"col={ts['end']['column_covered']}/{ts['end']['active_universe']} "
            f"data={ts['end']['data_covered']}/{ts['end']['active_universe']} "
            f"({ts['end']['missing_col']} missing columns)"
        )

        print(f"\nCheckpoint detail:")
        for cp in report["checkpoints"]:
            pct = (
                cp["column_covered"] / cp["active_universe"] * 100
                if cp["active_universe"] > 0
                else 0
            )
            flag = " 🔴" if cp["missing_col_count"] > 0 else " ✅"
            print(
                f"  {cp['date']}: col={cp['column_covered']}/{cp['active_universe']} "
                f"({pct:.1f}%) data={cp['data_covered']} "
                f"— {cp['missing_col_count']} missing col, "
                f"{cp['missing_data_count']} col-NaN{flag}"
            )

        print(
            f"\nUnion missing columns (all checkpoints): "
            f"{report['union_missing_columns_count']}"
        )
        if report["union_missing_columns_count"] <= 20:
            for t in report["union_missing_columns"]:
                print(f"  - {t}")

        candidate_info = inspect_existing_candidate(original_prices, candidate_csv)
        if candidate_info and effective_prefix != "h26":
            candidate_info["status"] = f"{effective_prefix}_existing"
            safety = candidate_safety_checks(original_prices, prices)
            candidate_full_validate_ready = (
                safety["passed"]
                and all(cp.get("missing_col_count", 0) == 0 for cp in report["checkpoints"])
                and all(cp.get("missing_data_count", 0) == 0 for cp in report["checkpoints"])
            )
            candidate_info["remaining_missing_columns"] = report["union_missing_columns_count"]
            candidate_info["remaining_column_nan"] = report.get("union_missing_data_count", 0)
            candidate_info["candidate_full_validate_ready"] = candidate_full_validate_ready
            candidate_info["manual_replacement_recommended"] = (
                candidate_full_validate_ready
                and report["union_missing_columns_count"] == 0
                and report.get("union_missing_data_count", 0) == 0
            )
        write_report_artifacts(report, coverage_json, report_md, candidate_info)
        print(f"\nWrote: {coverage_json}")
        print(f"Wrote: {report_md}")

    # --- Fetch missing ---
    if args.fetch_missing:
        print("\n" + "-" * 40)
        print("FETCH MODE (non-destructive)")
        print("-" * 40)

        # Recompute report if not already done
        checkpoints = generate_checkpoints(prices.index)
        report = analyze_coverage(
            universe, prices, checkpoints,
            target_start=date.fromisoformat(args.start) if args.start else None,
            target_end=date.fromisoformat(args.end) if args.end else None,
        )

        missing = report["union_missing_columns"]
        print(f"Total unique missing tickers: {len(missing)}")

        fetch_set = missing[: args.max_fetch_tickers]
        print(f"Attempting fetch for up to {len(fetch_set)} tickers...")

        new_data = fetch_missing_tickers(fetch_set, args.start, args.end)

        candidate_info = {}
        if new_data is not None and not new_data.empty:
            # Find which tickers we actually got from fetch
            fetched_tickers = set(new_data.columns)
            attempted_success = fetched_tickers & set(missing)
            print(
                f"\nFetch returned {len(attempted_success)}/{len(fetch_set)} tickers"
            )

            # Merge into candidate
            candidate, _fetch_stats = merge_candidate(prices, new_data, attempted_success)
            candidate.to_csv(CANDIDATE_CSV)

            # Count actually-added columns (some may have been all-NaN)
            actual_new = set(candidate.columns) - set(prices.columns) - {"date"}
            actual_added = actual_new & set(missing)

            print(f"Wrote candidate CSV: {CANDIDATE_CSV}")
            print(f"  Rows: {len(candidate)}, Columns: {len(candidate.columns)}")
            print(f"  Actually added tickers: {len(actual_added)}")
            print(f"  Original file NOT modified: {PRICES_FILE}")

            candidate_info = {
                "status": "created",
                "attempted": len(fetch_set),
                "fetched_by_provider": len(attempted_success),
                "actually_added": len(actual_added),
                "new_tickers": list(sorted(actual_added)),
                "improvement": len(actual_added) > 0,
            }
        else:
            print("\nNetwork fetch failed or returned no data.")
            print("Existing prices.csv remains untouched.")
            candidate_info = {
                "status": "failed",
                "attempted": len(fetch_set),
                "fetched_by_provider": 0,
                "actually_added": 0,
                "new_tickers": [],
                "improvement": False,
            }

        # Update report with candidate info
        md_content = generate_report(report, candidate_info)
        with open(REPORT_MD, "w") as f:
            f.write(md_content)
        print(f"Updated report: {REPORT_MD}")

    # --- Incremental backfill ---
    if args.backfill_missing:
        print("\n" + "-" * 40)
        print(f"{effective_prefix.upper()} BACKFILL MODE (non-destructive)")
        print("-" * 40)
        run_incremental_backfill(
            original=original_prices,
            universe=universe,
            start_date=args.start,
            end_date=args.end,
            batch_size=args.batch_size,
            use_slow_fallbacks=args.slow_fallbacks,
            prefix=effective_prefix,
            base_path=prices_path if args.prices_file else None,
            candidate_path=candidate_csv,
            coverage_json=coverage_json,
            report_md=report_md,
        )

    # --- Final checks ---
    print("\n" + "=" * 60)
    print("ACCEPTANCE CHECK SUMMARY")
    print("=" * 60)

    # Verify prices.csv unchanged (check mtime via stat)
    prices_mtime = os.path.getmtime(PRICES_FILE)
    print(f"✅ prices.csv unchanged (mtime: {datetime.fromtimestamp(prices_mtime)})")

    if coverage_json.exists():
        print(f"✅ {coverage_json.name} exists ({os.path.getsize(coverage_json)} bytes)")
    else:
        print(f"❌ {coverage_json.name} NOT generated")

    if report_md.exists():
        print(f"✅ {report_md.name} exists ({os.path.getsize(report_md)} bytes)")
    else:
        print(f"❌ {report_md.name} NOT generated")

    print("\nDone.")


if __name__ == "__main__":
    main()
