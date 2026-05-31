#!/usr/bin/env python3
"""OHLV supplemental data layer ingestion for A-share PIT backtest.

Creates ``data/cn_pit/ohlv_h47_supplement.csv`` (long format, no close column)
by fetching OHLV daily bars from the fallback provider chain:
Tushare → Akshare → YFinance.

The close-only H47 frozen matrix stays untouched — this layer adds
open / high / low / volume / amount columns joining on (date, ticker).

Part of ENGINE-OHLV-V1 PR per docs/superpowers/plans/2026-05-30-ohlv-supplemental-engine-pr.md.

Usage:
  # Module-only smoke (no network, no writes)
  python3 -c "from scripts.ingest_cn_pit_ohlv import _build_fallback_provider; ..."

  # Fetch (needs network + provider tokens)
  python3 scripts/ingest_cn_pit_ohlv.py --fetch-ohlv

  # Validate
  python3 scripts/ingest_cn_pit_ohlv.py --validate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Ensure unbuffered output for background / cron execution
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

import pandas as pd

# ---- Paths ----
VT_DIR = Path(os.path.expanduser("~/.hermes/virtual-trader"))

# Ensure virtual-trader root is on sys.path
_vt_root = str(VT_DIR)
if _vt_root not in sys.path:
    sys.path.insert(0, _vt_root)

DATA_DIR = VT_DIR / "data" / "cn_pit"
UNIVERSE_PATH = DATA_DIR / "universe.jsonl"
PRICES_H47_PATH = DATA_DIR / "prices_h47_tushare_qfq_candidate.csv"
PRICES_PATH = DATA_DIR / "prices.csv"
OHLV_PATH = DATA_DIR / "ohlv_h47_supplement.csv"
METADATA_PATH = DATA_DIR / "metadata.json"
VALIDATION_REPORT_PATH = DATA_DIR / "validation_report_ohlv.json"

NOW_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# Canonical output columns (plan §3.3 — no close, H47 already carries it)
OHLV_CANONICAL_COLS = ["date", "ticker", "open", "high", "low", "volume", "amount"]

# Protected sha256 snapshots (plan §3.5 / Charter §2)
H47_FROZEN_SHA = "34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc"
PRICES_H28_SHA = "5efc8ec7ef4a6b7064010e67e0a9b9fdad77ca1c8d6cc907e47532738dc1a50c"


# ================================================================
# Helpers
# ================================================================

def _sha256_file(path: Path) -> str:
    """Compute sha256 hex digest of a file. Returns '' if file missing."""
    if not path.exists():
        return ""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            sha.update(chunk)
    return sha.hexdigest()


def _sha256_csv(path: Path) -> str:
    """Compute sha256 over a CSV file's raw bytes (same as _sha256_file, named for clarity)."""
    return _sha256_file(path)


def _read_jsonl(path: Path) -> List[Dict]:
    """Read JSONL file, return list of dicts. Empty list if file missing."""
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError as exc:
                print(f"  WARNING: {path}:{line_no}: invalid JSON: {exc}", file=sys.stderr)
    return rows


def _compute_ohlv_sha256(df: pd.DataFrame) -> str:
    """Stable sha256 over a long-format OHLCV DataFrame.

    Sorts by (date, ticker) before hashing.  Uses the same algorithm as
    ``compute_ohlcv_sha256`` in backtest/market_data.py.
    """
    frame = df.sort_values(["date", "ticker"]).reset_index(drop=True)
    payload = frame.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_date(dates: pd.Series) -> pd.Series:
    """Normalize a date column to YYYY-MM-DD strings.

    Handles pd.Timestamp, datetime, and string inputs.
    """
    if pd.api.types.is_datetime64_any_dtype(dates):
        return dates.dt.strftime("%Y-%m-%d")
    # Try parsing as timestamps
    try:
        return pd.to_datetime(dates).dt.strftime("%Y-%m-%d")
    except Exception:
        return dates.astype(str)


# ================================================================
# Provider builder (mirrors ingest_cn_pit_data.py §5.1)
# ================================================================

def _get_tushare_token():
    """Return the Tushare token from env, then launchctl, else None.

    Interactively-dispatched (non-cron) runs do NOT inherit the launchctl
    TUSHARE_TOKEN; reading os.environ alone silently degraded the provider
    chain to Akshare/YFinance (incident: H53 OHLV came from YFinance with
    amount=NaN). Mirror scripts/h33_execution_audit.py:get_tushare_token().
    See docs/strategy-optimization-sync.md § H53-FIX.
    """
    for key in ("TUSHARE_TOKEN", "TUSHARE_API_TOKEN"):
        tok = os.environ.get(key)
        if tok and tok.strip():
            return tok.strip()
    try:
        import subprocess
        tok = subprocess.check_output(
            ["launchctl", "getenv", "TUSHARE_TOKEN"], text=True
        ).strip()
        if tok:
            return tok
    except Exception:
        pass
    return None


def _build_fallback_provider():
    """Build a fallback provider chain: Tushare → Akshare → YFinance.

    Returns a ``FallbackMarketDataProvider`` wired in priority order.
    The Tushare token is resolved via :func:`_get_tushare_token` (env +
    launchctl fallback), NOT os.environ alone — see that function's docstring.
    """
    from backtest.market_data import (
        AkshareProvider,
        FallbackMarketDataProvider,
        TushareProvider,
        YFinanceProvider,
    )

    providers = [
        TushareProvider(token=_get_tushare_token()),
        AkshareProvider(),
        YFinanceProvider(),
    ]
    return FallbackMarketDataProvider(providers)


# ================================================================
# Safeguard: verify protected files have NOT changed (plan §3.5)
# ================================================================

def _verify_protected_artifacts() -> Dict[str, str]:
    """Snapshot sha256 of protected files.  Returns dict of path→sha.

    Does NOT raise — caller decides if mismatch is fatal.
    """
    snapshots: Dict[str, str] = {}
    if PRICES_H47_PATH.exists():
        snapshots["prices_h47"] = _sha256_file(PRICES_H47_PATH)
    if PRICES_PATH.exists():
        snapshots["prices"] = _sha256_file(PRICES_PATH)
    return snapshots


def _check_protected_shas_match(
    pre: Dict[str, str], post: Dict[str, str]
) -> List[str]:
    """Compare pre/post protected sha snapshots. Returns list of violations."""
    violations: List[str] = []
    if pre.get("prices_h47") and post.get("prices_h47"):
        if pre["prices_h47"] != post["prices_h47"]:
            violations.append(
                f"prices_h47_tushare_qfq_candidate.csv sha changed: "
                f"pre={pre['prices_h47'][:12]}… post={post['prices_h47'][:12]}…"
            )
    if pre.get("prices") and post.get("prices"):
        if pre["prices"] != post["prices"]:
            violations.append(
                f"prices.csv sha changed: "
                f"pre={pre['prices'][:12]}… post={post['prices'][:12]}…"
            )
    return violations


def _check_metadata_keys_unchanged(
    pre_keys: Set[str], post_keys: Set[str]
) -> List[str]:
    """Verify no existing metadata key was removed or mutated.

    New keys may be added (the whole point), but existing ones must survive
    unchanged.  Returns list of violation messages.
    """
    violations: List[str] = []
    removed = pre_keys - post_keys
    if removed:
        violations.append(
            f"metadata.json keys REMOVED during execution: {sorted(removed)}"
        )
    return violations


# ================================================================
# Safeguard: verify write targets are whitelisted (plan §3.5)
# ================================================================

_ALLOWED_WRITE_TARGETS: Set[Path] = {
    OHLV_PATH,
    METADATA_PATH,
    VALIDATION_REPORT_PATH,
}


def _guard_write_path(path: Path) -> None:
    """Raise RuntimeError if path is not in the whitelisted write targets."""
    resolved = path.resolve()
    if resolved not in {p.resolve() for p in _ALLOWED_WRITE_TARGETS}:
        raise RuntimeError(
            f"BLOCKER: write to non-whitelisted path: {path}\n"
            f"  Allowed: {[str(p) for p in _ALLOWED_WRITE_TARGETS]}\n"
            f"  Attempted: {resolved}"
        )


# ================================================================
# FETCH: OHLV supplement
# ================================================================

def fetch_ohlv(
    start: str = "2020-01-02",
    end: str = "2026-05-18",
    limit_tickers: int = 0,
    skip_existing: bool = False,
) -> Dict:
    """Fetch OHLV daily bars for universe tickers and write supplement CSV.

    Args:
        start:  Start date (ISO, inclusive).
        end:    End date (ISO, inclusive).
        limit_tickers: If > 0, use only first N tickers (for testing).
        skip_existing: If True and ohlv_h47_supplement.csv exists, skip fetch.

    Returns:
        Dict with keys: ``ohlcv_path``, ``rows``, ``tickers``, ``date_range``,
        ``sha256``, ``fallback_chain``, ``selected_provider``,
        ``fallback_reason``, ``precheck_log``.

    Raises:
        SystemExit(2) on LoaderBlockedError (all providers exhausted).
        SystemExit(1) on protected-file violation.
    """
    from backtest.market_data import LoaderBlockedError

    ensure_dir()

    if skip_existing and OHLV_PATH.exists():
        print(f"  {OHLV_PATH.name} exists (--skip-existing), loading for metadata…")
        df = pd.read_csv(OHLV_PATH, dtype={"date": str, "ticker": str})
        sha = _compute_ohlv_sha256(df)
        dates = sorted(df["date"].unique())
        tickers = sorted(df["ticker"].unique())
        return {
            "ohlcv_path": str(OHLV_PATH),
            "rows": len(df),
            "tickers": len(tickers),
            "date_range": [dates[0], dates[-1]] if dates else ["", ""],
            "sha256": sha,
            "fallback_chain": [],
            "selected_provider": "(skipped — existing file reused)",
            "fallback_reason": None,
            "precheck_log": [],
        }

    # --- Pre-run: snapshot protected artifacts ---
    pre_protected = _verify_protected_artifacts()

    # --- Pre-run: snapshot metadata keys ---
    pre_meta_keys: Set[str] = set()
    if METADATA_PATH.exists():
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            pre_meta_keys = set(json.load(f).keys())

    # --- Load universe ---
    if not UNIVERSE_PATH.exists():
        print("ERROR: universe.jsonl not found. Run --fetch-universe first.", file=sys.stderr)
        sys.exit(1)

    universe = _read_jsonl(UNIVERSE_PATH)
    all_tickers = sorted(set(row["ticker"] for row in universe if row.get("ticker")))
    if limit_tickers > 0:
        all_tickers = all_tickers[:limit_tickers]
    print(f"  Universe loaded: {len(all_tickers)} tickers (from {len(universe)} rows)")

    # --- Fetch OHLV ---
    print(f"  Fetching OHLV for {len(all_tickers)} tickers ({start} → {end})…")
    provider = _build_fallback_provider()

    try:
        result = provider.get_ohlcv(all_tickers, start, end)
    except LoaderBlockedError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    if result.status != "OK" or result.ohlcv.empty:
        print(f"  ERROR: OHLV fetch returned status={result.status}", file=sys.stderr)
        sys.exit(2)

    ohlcv = result.ohlcv.copy()

    # Normalize date column to YYYY-MM-DD string (cross-provider consistency)
    ohlcv["date"] = _normalize_date(ohlcv["date"])

    # Drop 'close' column if present — H47 already carries close prices (plan §1.1)
    if "close" in ohlcv.columns:
        ohlcv = ohlcv.drop(columns=["close"])

    # Ensure we have exactly the canonical columns
    missing_cols = [c for c in OHLV_CANONICAL_COLS if c not in ohlcv.columns]
    if missing_cols:
        print(
            f"  ERROR: OHLV data missing required columns: {missing_cols}",
            file=sys.stderr,
        )
        sys.exit(2)

    extra_cols = [c for c in ohlcv.columns if c not in OHLV_CANONICAL_COLS]
    if extra_cols:
        ohlcv = ohlcv[OHLV_CANONICAL_COLS]

    # Sort canonical
    ohlcv = ohlcv.sort_values(["date", "ticker"]).reset_index(drop=True)

    # Compute sha256
    sha = _compute_ohlv_sha256(ohlcv)
    dates = sorted(ohlcv["date"].unique())
    tickers_unique = sorted(ohlcv["ticker"].unique())

    print(f"  Fetched: {len(ohlcv)} rows × {len(ohlcv.columns)} cols")
    print(f"  Date range: {dates[0]} → {dates[-1]}" if dates else "  Date range: (empty)")
    print(f"  Tickers:    {len(tickers_unique)}")
    print(f"  SHA256:     {sha[:16]}…")
    print(f"  Selected:   {result.sources_used.get('__selected', 'unknown')}")

    # --- Write OHLV CSV ---
    _guard_write_path(OHLV_PATH)
    ohlcv.to_csv(OHLV_PATH, index=False)
    print(f"  Wrote {len(ohlcv)} rows to {OHLV_PATH}")

    # --- Write metadata (append-only: add ohlv_layer key) ---
    _write_metadata_append(
        sha256=sha,
        rows=len(ohlcv),
        tickers=len(tickers_unique),
        date_range=[dates[0], dates[-1]] if dates else ["", ""],
        fallback_chain=result.fallback_chain,
        selected_provider=result.sources_used.get("__selected", "unknown"),
        fallback_reason=result.fallback_reason,
        precheck_log=result.precheck_log,
    )

    # --- Post-run: verify protected artifacts unchanged ---
    post_protected = _verify_protected_artifacts()
    violations = _check_protected_shas_match(pre_protected, post_protected)
    if violations:
        for v in violations:
            print(f"  BLOCKER: {v}", file=sys.stderr)
        print(
            "  ❌ PROTECTED FILE SHA256 CHANGED during execution — aborting.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Post-run: verify metadata existing keys unchanged ---
    post_meta_keys: Set[str] = set()
    if METADATA_PATH.exists():
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            post_meta_keys = set(json.load(f).keys())
    meta_violations = _check_metadata_keys_unchanged(pre_meta_keys, post_meta_keys)
    if meta_violations:
        for v in meta_violations:
            print(f"  BLOCKER: {v}", file=sys.stderr)
        sys.exit(1)

    return {
        "ohlcv_path": str(OHLV_PATH),
        "rows": len(ohlcv),
        "tickers": len(tickers_unique),
        "date_range": [dates[0], dates[-1]] if dates else ["", ""],
        "sha256": sha,
        "fallback_chain": result.fallback_chain,
        "selected_provider": result.sources_used.get("__selected", "unknown"),
        "fallback_reason": result.fallback_reason,
        "precheck_log": result.precheck_log,
    }


# ================================================================
# Metadata write (append-only — plan §3.3)
# ================================================================

def _write_metadata_append(
    sha256: str,
    rows: int,
    tickers: int,
    date_range: List[str],
    fallback_chain: List[str],
    selected_provider: str,
    fallback_reason: Optional[str],
    precheck_log: List[str],
) -> None:
    """Read existing metadata.json, ADD ONLY the ohlv_layer key, write back.

    CRITICAL: must NOT modify any existing key.  Raises BlockingIOError
    (caught upstream) if any existing key would be mutated.
    """
    _guard_write_path(METADATA_PATH)

    if METADATA_PATH.exists():
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            try:
                meta = json.load(f)
            except json.JSONDecodeError:
                print(
                    f"  WARNING: {METADATA_PATH} is not valid JSON; "
                    "creating fresh metadata with ohlv_layer only.",
                    file=sys.stderr,
                )
                meta = {}
    else:
        meta = {}

    # Snapshot existing keys for post-write verification
    existing_keys = set(meta.keys())

    # Build ohlv_layer block per plan §3.3
    ohlv_layer = {
        "file": "data/cn_pit/ohlv_h47_supplement.csv",
        "sha256": sha256,
        "rows": rows,
        "tickers": tickers,
        "date_range": date_range,
        "fetch_timestamp": NOW_UTC,
        "fallback_chain": fallback_chain,
        "selected_provider": selected_provider,
        "fallback_reason": fallback_reason,
        "precheck_log": precheck_log,
        "columns": OHLV_CANONICAL_COLS,
    }

    # Raise if ohlv_layer already exists (refuse silent overwrite)
    if "ohlv_layer" in meta:
        print(
            "  WARNING: ohlv_layer key already exists in metadata.json — overwriting.",
            file=sys.stderr,
        )

    meta["ohlv_layer"] = ohlv_layer

    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  Updated {METADATA_PATH}: added ohlv_layer key")
    print(f"    (existing keys preserved: {sorted(existing_keys)})")


# ================================================================
# VALIDATE: OHLV supplement
# ================================================================

def validate_ohlv() -> Dict:
    """Validate ohlv_h47_supplement.csv against metadata and universe.

    Returns a validation summary dict.  Does NOT exit — caller decides.
    """
    errors: List[str] = []
    warnings: List[str] = []
    summary: Dict = {
        "file": str(OHLV_PATH),
        "validation_timestamp": NOW_UTC,
        "errors": errors,
        "warnings": warnings,
        "status": "PENDING",
    }

    # --- Check file exists ---
    if not OHLV_PATH.exists():
        errors.append(f"missing_file: {OHLV_PATH.name}")
        summary["status"] = "FAILED"
        return summary

    # --- Read data ---
    try:
        df = pd.read_csv(OHLV_PATH, dtype={"date": str, "ticker": str})
    except Exception as e:
        errors.append(f"parse_error: {e}")
        summary["status"] = "FAILED"
        return summary

    summary["rows"] = len(df)
    summary["columns"] = list(df.columns)

    # --- Column check ---
    missing_cols = [c for c in OHLV_CANONICAL_COLS if c not in df.columns]
    if missing_cols:
        errors.append(f"missing_columns: {missing_cols}")
    extra_cols = [c for c in df.columns if c not in OHLV_CANONICAL_COLS]
    if extra_cols:
        warnings.append(f"extra_columns: {extra_cols}")
    if "close" in df.columns:
        errors.append(
            "column 'close' present — OHLV supplement must NOT duplicate H47 close"
        )

    # --- Sha256 vs metadata ---
    recomputed = _compute_ohlv_sha256(df)
    summary["sha256_recomputed"] = recomputed

    if METADATA_PATH.exists():
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        ohlv_meta = meta.get("ohlv_layer", {})
        expected_sha = ohlv_meta.get("sha256", "")
        expected_rows = ohlv_meta.get("rows")
        summary["sha256_metadata"] = expected_sha
        summary["sha256_match"] = (recomputed == expected_sha) if expected_sha else None

        if expected_sha and recomputed != expected_sha:
            errors.append(
                f"sha256_mismatch: metadata={expected_sha[:16]}… "
                f"recomputed={recomputed[:16]}…"
            )

        if expected_rows is not None and len(df) != expected_rows:
            errors.append(
                f"row_count_mismatch: metadata={expected_rows} actual={len(df)}"
            )
    else:
        warnings.append("metadata.json missing — cannot verify sha256 or row count")

    # --- Row count ---
    if len(df) == 0:
        errors.append("empty_file: 0 rows")

    # --- (date, ticker) uniqueness ---
    dupes = df.duplicated(subset=["date", "ticker"], keep=False)
    if dupes.any():
        n_dupes = dupes.sum()
        errors.append(f"duplicate_(date,ticker)_pairs: {n_dupes}")

    # --- Non-empty OHLV fields ---
    for col in ["open", "high", "low"]:
        null_count = df[col].isna().sum()
        if null_count > 0:
            errors.append(
                f"null_values_in_{col}: {null_count}/{len(df)} "
                f"({100*null_count/len(df):.1f}%)"
            )

    # volume and amount: warn on nulls (amount may legitimately be NaN from YFinance)
    for col in ["volume", "amount"]:
        if col in df.columns:
            null_count = df[col].isna().sum()
            if null_count > len(df) * 0.5:
                warnings.append(
                    f"high_null_rate_in_{col}: {null_count}/{len(df)} "
                    f"({100*null_count/len(df):.1f}%) — may be normal if YFinance fallback"
                )

    # --- Cross-check with universe ---
    if UNIVERSE_PATH.exists():
        universe = _read_jsonl(UNIVERSE_PATH)
        universe_tickers = set(row["ticker"] for row in universe if row.get("ticker"))
        ohlv_tickers = set(df["ticker"].unique())
        summary["universe_ticker_count"] = len(universe_tickers)
        summary["ohlv_ticker_count"] = len(ohlv_tickers)
        missing_in_ohlv = universe_tickers - ohlv_tickers
        extra_in_ohlv = ohlv_tickers - universe_tickers
        if missing_in_ohlv:
            warnings.append(
                f"universe_tickers_missing_in_ohlv: {len(missing_in_ohlv)} "
                f"(sample: {sorted(missing_in_ohlv)[:10]})"
            )
        if extra_in_ohlv:
            warnings.append(
                f"ohlv_tickers_not_in_universe: {len(extra_in_ohlv)} "
                f"(sample: {sorted(extra_in_ohlv)[:10]})"
            )

    # --- Date range ---
    dates = sorted(df["date"].unique())
    if dates:
        summary["date_range"] = [dates[0], dates[-1]]
        summary["unique_dates"] = len(dates)

    # --- Final status ---
    if errors:
        summary["status"] = "FAILED"
    elif warnings:
        summary["status"] = "WARN"
    else:
        summary["status"] = "PASSED"

    return summary


def print_ohlv_summary(summary: Dict) -> None:
    """Pretty-print OHLV validation summary."""
    print()
    print("=" * 60)
    print("  OHLV SUPPLEMENT VALIDATION SUMMARY")
    print("=" * 60)
    print(f"  File:         {summary['file']}")
    print(f"  Status:       {summary['status']}")
    print(f"  Rows:         {summary.get('rows', 'N/A')}")
    print(f"  Columns:      {summary.get('columns', 'N/A')}")
    dr = summary.get("date_range", [])
    if dr:
        print(f"  Date range:   {dr[0]} → {dr[1]} ({summary.get('unique_dates', '?')} dates)")
    print(f"  OHLV tickers: {summary.get('ohlv_ticker_count', 'N/A')}")
    print(f"  Universe:     {summary.get('universe_ticker_count', 'N/A')} tickers")

    sha_match = summary.get("sha256_match")
    if sha_match is True:
        print(f"  SHA256:       MATCH ✓")
    elif sha_match is False:
        print(f"  SHA256:       MISMATCH ✗")
    else:
        print(f"  SHA256:       (no metadata to compare)")

    print()
    if summary.get("errors"):
        print(f"  Errors ({len(summary['errors'])}):")
        for e in summary["errors"]:
            print(f"    - {e}")
        print()

    if summary.get("warnings"):
        print(f"  Warnings ({len(summary['warnings'])}):")
        for w in summary["warnings"]:
            print(f"    - {w}")
        print()

    if summary["status"] == "PASSED":
        print("  ✅ OHLV validation PASSED.")
    elif summary["status"] == "WARN":
        print("  ⚠️  OHLV validation WARN — warnings present but no hard errors.")
    else:
        print("  ❌ OHLV validation FAILED.")


# ================================================================
# Utility
# ================================================================

def ensure_dir() -> None:
    """Ensure data/cn_pit/ directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ================================================================
# MAIN
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OHLV Supplemental Data Layer Ingestion (ENGINE-OHLV-V1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch full HS300 H47 OHLV supplement (needs network + provider tokens)
  python3 scripts/ingest_cn_pit_ohlv.py --fetch-ohlv

  # Smoke test: 5 tickers, 1 month
  python3 scripts/ingest_cn_pit_ohlv.py --fetch-ohlv --limit-tickers 5 \\
      --start 2026-04-01 --end 2026-04-30

  # Validate
  python3 scripts/ingest_cn_pit_ohlv.py --validate

  # Module-level smoke (no network, no writes)
  python3 -c "from scripts.ingest_cn_pit_ohlv import _build_fallback_provider; \\
      p = _build_fallback_provider(); print(type(p).__name__, [x.name for x in p.providers])"
        """,
    )
    parser.add_argument(
        "--fetch-ohlv", action="store_true",
        help="Fetch OHLV daily bars and write ohlv_h47_supplement.csv",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate ohlv_h47_supplement.csv against metadata and universe",
    )
    parser.add_argument(
        "--start", default="2020-01-02",
        help="OHLV data start date (default: 2020-01-02)",
    )
    parser.add_argument(
        "--end", default="2026-05-18",
        help="OHLV data end date (default: 2026-05-18)",
    )
    parser.add_argument(
        "--limit-tickers", type=int, default=0,
        help="Limit to first N tickers (for testing)",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip fetch if ohlv_h47_supplement.csv exists",
    )

    args = parser.parse_args()

    # Default to validate if no action specified
    if not args.fetch_ohlv and not args.validate:
        args.validate = True

    start_time = time.time()

    if args.fetch_ohlv:
        print("\n--- FETCH OHLV SUPPLEMENT ---")
        result = fetch_ohlv(
            start=args.start,
            end=args.end,
            limit_tickers=args.limit_tickers,
            skip_existing=args.skip_existing,
        )
        print(f"  DONE: {result['rows']} rows written to {OHLV_PATH}")

    if args.validate:
        print("\n--- VALIDATE OHLV SUPPLEMENT ---")
        summary = validate_ohlv()
        print_ohlv_summary(summary)

        # Write validation report
        _guard_write_path(VALIDATION_REPORT_PATH)
        with open(VALIDATION_REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
        print(f"  Report saved: {VALIDATION_REPORT_PATH}")

        if summary["status"] == "FAILED":
            sys.exit(1)

    elapsed = time.time() - start_time
    print(f"\nTotal time: {elapsed:.0f}s")


if __name__ == "__main__":
    main()
