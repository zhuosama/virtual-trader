#!/usr/bin/env python3
"""CN PIT data ingestion pipeline for A-share value backtest.

Modes:
  --fetch-universe     Fetch HS300 constituents → universe.jsonl
  --fetch-disclosures  Fetch financials + filing dates → fundamentals.jsonl
  --fetch-prices       Fetch historical prices → prices.csv
  --validate           Validate all output files
  --all                Run all fetch modes + validate

Output: ~/.hermes/virtual-trader/data/cn_pit/{universe,fundamentals}.jsonl, prices.csv

Data quality caveats:
  SURVIVORSHIP_BIAS: Uses current HS300 constituents; historical additions/removals not tracked.
  CURRENT_VALUATION: PE/PB/div_yield/market_cap are yfinance snapshots—not PIT-safe.
  ROE and debt_to_equity come from THS financial abstracts and are matched to CNINFO filing dates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

# Ensure unbuffered output for background/cron execution
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

import pandas as pd
import yfinance as yf

# ---- Paths ----
VT_DIR = Path(os.path.expanduser("~/.hermes/virtual-trader"))

# Ensure virtual-trader root is on sys.path for cross-directory imports
_vt_root = str(VT_DIR)
if _vt_root not in sys.path:
    sys.path.insert(0, _vt_root)

DATA_DIR = VT_DIR / "data" / "cn_pit"
RAW_DIR = DATA_DIR / "raw"
UNIVERSE_PATH = DATA_DIR / "universe.jsonl"
FUNDAMENTALS_PATH = DATA_DIR / "fundamentals.jsonl"
PRICES_PATH = DATA_DIR / "prices.csv"
METADATA_PATH = DATA_DIR / "metadata.json"

NOW_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
NOW_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")
TUSHARE_SNAPSHOT_CARRY_FORWARD_DAYS = 45

# ---- Helpers ----
def normalize_ticker(code: str) -> str:
    """Normalize A-share code to yfinance format (600519.SS, 000858.SZ)."""
    raw = str(code or "").strip().upper()
    if not raw:
        return raw
    if raw.endswith((".SS", ".SZ")):
        return raw
    code_num = raw.split(".")[0]
    suffix = ".SS" if code_num.startswith(("5", "6", "9")) else ".SZ"
    return f"{code_num}{suffix}"


def _within_tushare_carry_forward(snapshot_date: str, end_date: str) -> bool:
    """Allow a short validation window after the latest official index snapshot."""
    try:
        snap = datetime.strptime(snapshot_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return False
    if end <= snap:
        return True
    return (end - snap).days <= TUSHARE_SNAPSHOT_CARRY_FORWARD_DAYS


def parse_pct(val) -> Optional[float]:
    """Parse a percentage string like '36.02%' to float 36.02, or return None."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().replace("%", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_decimal(val) -> Optional[float]:
    """Parse a decimal value, return None on failure."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, rows: List[Dict]):
    """Write list of dicts as JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> List[Dict]:
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
                print(f"  WARNING: {path}:{line_no}: invalid JSON: {exc}")
    return rows


# ================================================================
# FETCH: Universe
# ================================================================
UNIVERSE_SNAPSHOTS_PATH = DATA_DIR / "universe_snapshots.jsonl"


def _get_tushare_token() -> Optional[str]:
    """Return Tushare Pro token from environment or config file, or None."""
    for key in ("TUSHARE_TOKEN", "TUSHARE_API_TOKEN"):
        token = os.environ.get(key)
        if token and token.strip():
            return token.strip()
    # Check config file if exists
    config_path = VT_DIR / "agents" / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            for key in ("tushare_token", "tushare_api_token", "TUSHARE_TOKEN"):
                token = cfg.get(key)
                if token:
                    return token.strip()
        except Exception:
            pass
    return None


def fetch_historical_universe(
    start: str = "2020-01-01",
    end: Optional[str] = None,
) -> Optional[Dict]:
    """Fetch historical HS300 constituent snapshots from Tushare Pro.

    Returns a dict with keys:
      - snapshots: List[Dict] — raw monthly snapshots from Tushare
      - intervals: List[Dict] — converted interval rows for universe.jsonl
      - source_provider: str
      - snapshot_range: (min_date, max_date)
      - snapshot_count: int
      - is_historical: bool — True if real historical data was used

    If no Tushare token is available, returns None and prints a clear
    diagnostic message.
    """
    token = _get_tushare_token()
    if not token:
        print("=" * 60)
        print("  ⚠️  TUSHARE TOKEN NOT FOUND")
        print("  Historical HS300 universe fetch requires Tushare Pro API.")
        print("  Set TUSHARE_TOKEN or TUSHARE_API_TOKEN env var, or add")
        print("  tushare_token to ~/.hermes/virtual-trader/agents/config.yaml")
        print("  Free registration: https://tushare.pro/register")
        print("  API doc: https://tushare.pro/document/2?doc_id=96")
        print("=" * 60)
        return None

    if end is None:
        end = NOW_DATE

    try:
        import tushare as ts
    except ImportError:
        print("  ⚠️  tushare package not installed.")
        print("  Install: /Users/zhuosama/.hermes/hermes-agent/venv/bin/pip install tushare")
        return None

    print(f"Connecting to Tushare Pro for HS300 index_weight ({start} → {end})...")
    pro = ts.pro_api(token)

    # Tushare dates must be YYYYMMDD format
    start_ts = start.replace("-", "")
    end_ts = end.replace("-", "")

    try:
        df = pro.index_weight(
            index_code="399300.SZ",
            start_date=start_ts,
            end_date=end_ts,
        )
    except Exception as e:
        print(f"  Tushare API error: {e}")
        print("  Falling back to current-constituent approximation.")
        return None

    if df is None or df.empty:
        print("  Tushare returned no data for this date range.")
        return None

    print(f"  Got {len(df)} raw snapshot rows from Tushare")
    df = df.sort_values("trade_date")

    # Convert to snapshots list
    snapshots: List[Dict] = []
    for _, row in df.iterrows():
        trade_date = str(row["trade_date"])
        # Normalize to YYYY-MM-DD
        trade_date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        con_code = str(row.get("con_code", "")).strip()
        code = con_code.split(".")[0] if "." in con_code else con_code
        if not code or len(code) < 6:
            code = code.zfill(6)
        weight = row.get("weight")
        try:
            weight = float(weight) if weight is not None else None
        except (ValueError, TypeError):
            weight = None
        snapshots.append({
            "index_code": "399300.SZ",
            "con_code": con_code,
            "code": code,
            "ticker": normalize_ticker(code),
            "trade_date": trade_date_fmt,
            "weight": weight,
            "source_provider": "tushare:index_weight",
        })

    # --- H4: Coverage enforcement ---
    dates = sorted(set(s["trade_date"] for s in snapshots))
    if not dates:
        print("  No valid snapshot dates found.")
        return None
    snapshot_min, snapshot_max = dates[0], dates[-1]
    print(f"  Snapshot coverage: {snapshot_min} → {snapshot_max}")

    # Parse requested start/end for comparison
    start_s = str(start or "").strip()
    end_s = str(end or "").strip()
    coverage_insufficient = False
    if start_s:
        if snapshot_min > start_s:
            print(f"  ⚠️  COVERAGE GAP: earliest snapshot {snapshot_min} > requested start {start_s}")
            coverage_insufficient = True
    if end_s:
        if snapshot_max < end_s:
            print(f"  ⚠️  COVERAGE GAP: latest snapshot {snapshot_max} < requested end {end_s}")
            coverage_insufficient = True

    if coverage_insufficient:
        print()
        print("  ❌ TUSHARE PARTIAL COVERAGE — rejecting as clean historical universe.")
        print("  Tushare returned data but it does not cover the full requested range.")
        print("  Writing partial data as 'clean' would silently introduce SURVIVORSHIP_BIAS.")
        print("  Existing universe.jsonl has NOT been overwritten.")
        print("  Falling back to current-constituent approximation (with SURVIVORSHIP_BIAS marker).")
        return None

    # Write raw snapshots
    ensure_dirs()
    write_jsonl(UNIVERSE_SNAPSHOTS_PATH, snapshots)
    print(f"  Wrote {len(snapshots)} raw snapshots to {UNIVERSE_SNAPSHOTS_PATH}")

    # Convert snapshots to intervals
    intervals = snapshots_to_intervals(snapshots)

    snapshot_range = (snapshot_min, snapshot_max)
    result = {
        "snapshots": snapshots,
        "intervals": intervals,
        "source_provider": "tushare:index_weight",
        "snapshot_range": snapshot_range,
        "snapshot_count": len(snapshots),
        "is_historical": True,
    }

    # Write interval rows to universe.jsonl
    write_jsonl(UNIVERSE_PATH, intervals)
    print(f"  Wrote {len(intervals)} interval records to {UNIVERSE_PATH}")
    return result


def snapshots_to_intervals(snapshots: List[Dict]) -> List[Dict]:
    """Convert monthly constituent snapshots to continuous interval rows.

    Rules:
    - Consecutive appearances → one continuous interval (effective_date → end_date).
    - Disappearance followed by reappearance → two separate intervals.
    - end_date = day before the next snapshot date where stock is absent.
    - Still-active stocks get empty end_date.

    Each output row:
      ticker, code, effective_date, end_date, source_url, ingested_at,
      index_code, weight (from interval-start snapshot), source_provider,
      snapshot_count
    """
    if not snapshots:
        return []

    # Validate trade_date on all snapshots (M1 hardening)
    for i, snap in enumerate(snapshots):
        td = snap.get("trade_date", "")
        if not td:
            raise ValueError(
                f"snapshots_to_intervals: snapshot[{i}] missing trade_date"
            )
        try:
            _parse_date(td)
        except ValueError as exc:
            raise ValueError(
                f"snapshots_to_intervals: snapshot[{i}] invalid trade_date={td!r}: {exc}"
            ) from exc

    # Group snapshots by ticker, sorted by trade_date
    by_ticker: Dict[str, List[Dict]] = {}
    for snap in snapshots:
        ticker = snap.get("ticker", "")
        if not ticker:
            continue
        by_ticker.setdefault(ticker, []).append(snap)

    for ticker in by_ticker:
        by_ticker[ticker].sort(key=lambda s: s["trade_date"])

    # Get the global set of all snapshot dates (sorted)
    all_dates = sorted(set(s["trade_date"] for s in snapshots))

    intervals = []
    for ticker, snaps in by_ticker.items():
        # Build ordered presence map: for each global date, is ticker present?
        present_dates = set(s["trade_date"] for s in snaps)
        presence = [(d, d in present_dates) for d in all_dates]

        # Collapse into continuous intervals
        i = 0
        while i < len(presence):
            date, is_present = presence[i]
            if not is_present:
                i += 1
                continue

            # Start of an interval — grab source fields from the interval-start snapshot
            effective_date = date
            start_snap = None
            for s in snaps:
                if s["trade_date"] == effective_date:
                    start_snap = s
                    break

            start_weight = start_snap.get("weight") if start_snap else None
            # L4: preserve source fields from the interval-start snapshot
            source_url = (
                start_snap.get("source_url")
                if start_snap and start_snap.get("source_url")
                else "https://tushare.pro/document/2?doc_id=96"
            )
            index_code = (
                start_snap.get("index_code")
                if start_snap and start_snap.get("index_code")
                else "399300.SZ"
            )
            source_provider = (
                start_snap.get("source_provider")
                if start_snap and start_snap.get("source_provider")
                else "tushare:index_weight"
            )

            # Find end of this continuous presence
            j = i + 1
            while j < len(presence) and presence[j][1]:
                j += 1

            if j < len(presence):
                # Next snapshot date is absent; end_date = day before that
                next_date = presence[j][0]
                end_date = _day_before(next_date)
            else:
                end_date = ""  # still active

            intervals.append({
                "ticker": ticker,
                "code": snaps[0].get("code", ticker.replace(".SS", "").replace(".SZ", "")),
                "name": snaps[0].get("name", ""),
                "effective_date": effective_date,
                "end_date": end_date,
                "source_url": source_url,
                "ingested_at": NOW_UTC,
                "index_code": index_code,
                "weight": start_weight,
                "source_provider": source_provider,
                "snapshot_count": j - i,
            })
            i = j

    return intervals


def _parse_date(date_str: str) -> datetime:
    """Parse ISO date string YYYY-MM-DD into a datetime. Raises ValueError on invalid input."""
    return datetime.strptime(str(date_str).strip(), "%Y-%m-%d")


def _day_before(date_str: str) -> str:
    """Return YYYY-MM-DD one day before the given date string.

    Raises ValueError on invalid date input (silent propagation is a data-quality risk).
    """
    from datetime import timedelta

    dt = _parse_date(date_str)
    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")


def _interval_active(row: Dict, as_of_date: str) -> bool:
    """Return whether a universe interval row is active on as_of_date."""
    start = row.get("effective_date", "")
    end = row.get("end_date", "") or "9999-12-31"
    return bool(start) and start <= as_of_date and as_of_date <= end


def _checkpoint_dates(dates: List[str]) -> List[str]:
    """Generate checkpoint dates from sorted price date strings.

    H28 (H1): Returns price_start, price_end, and the first trading day of
    each year within the range (mirrors repair_cn_price_coverage.py logic).
    """
    if not dates or len(dates) < 2:
        return dates
    checkpoints = [dates[0], dates[-1]]
    # Add first trading day of each interior year
    start_year = int(dates[0][:4])
    end_year = int(dates[-1][:4])
    for year in range(start_year, end_year + 1):
        target = f"{year}-01-02"
        # Snap to nearest on-or-after date
        for d in dates:
            if d >= target:
                if d not in checkpoints:
                    checkpoints.append(d)
                break
    return sorted(checkpoints)


def fetch_universe(
    years_back: int = 6,
    skip_existing: bool = False,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> List[Dict]:
    """Fetch HS300 constituents and build PIT interval records.

    Preferred path (H22):
    - Attempt historical fetch via Tushare Pro index_weight.
    - On success: writes real interval rows, survivorship_bias=false eligible.

    Fallback (original):
    - Uses CURRENT HS300 constituents (akshare index_stock_cons_csindex).
    - Historical additions/removals are NOT tracked. This introduces
      SURVIVORSHIP BIAS -- documented in metadata.
    - effective_date is approximated.

    Set start/end to attempt historical fetch. If both are None, uses
    the original current-constituent approximation.
    """
    ensure_dirs()

    # --- H22: Attempt historical fetch first ---
    if start is not None:
        hist_result = fetch_historical_universe(start=start, end=end or NOW_DATE)
        if hist_result is not None and hist_result["is_historical"]:
            print("  ✅ Historical HS300 universe ingestion complete (H22).")
            print(f"     Snapshots: {hist_result['snapshot_count']}")
            print(f"     Intervals: {len(hist_result['intervals'])}")
            print(f"     Date range: {hist_result['snapshot_range'][0]} → {hist_result['snapshot_range'][1]}")
            return hist_result["intervals"]
        else:
            print("  Historical universe fetch did not produce usable full-coverage data.")
            print("  Existing universe.jsonl is preserved; use --fetch-universe explicitly for current-only fallback.")
            if UNIVERSE_PATH.exists():
                return read_jsonl(UNIVERSE_PATH)
            return []

    if skip_existing and UNIVERSE_PATH.exists():
        print("  universe.jsonl exists (--skip-existing), loading...")
        return read_jsonl(UNIVERSE_PATH)

    import akshare as ak

    print("Fetching HS300 constituents from akshare...")
    df_const = ak.index_stock_cons_csindex(symbol="000300")
    print(f"  Got {len(df_const)} current constituents")

    codes = df_const["成分券代码"].unique().tolist()
    rows = []

    # NOTE: Current-universe fallback uses a uniform start date (years_back from now)
    # as effective_date for ALL constituents. This is a convenience approximation, NOT
    # the actual index-entry date of each stock. Some stocks entered the index more recently;
    # using the uniform fallback date makes them look like they've been in the index longer.
    # This is one source of SURVIVORSHIP_BIAS — see data_quality_note on each row.
    # Exact join/leave dates require paid CSI index membership history data.
    from_year = datetime.now().year - years_back
    default_effective = f"{from_year}-01-01"

    for _, crow in df_const.iterrows():
        code = str(crow["成分券代码"]).zfill(6)
        ticker = normalize_ticker(code)
        rows.append({
            "ticker": ticker,
            "code": code,
            "name": str(crow.get("成分券名称", "")),
            "effective_date": default_effective,
            "end_date": "",
            "source_url": "https://www.csindex.com.cn/zh-CN/indices/index-detail/000300",
            "ingested_at": NOW_UTC,
            "data_quality_note": (
                "SURVIVORSHIP_BIAS: current constituents only; "
                "historical additions/removals not tracked. "
                f"effective_date approximated to {default_effective}."
            ),
        })

    write_jsonl(UNIVERSE_PATH, rows)
    print(f"  Wrote {len(rows)} records to {UNIVERSE_PATH}")
    return rows


# ================================================================
# H23: Qlib Instruments Universe Import
# ================================================================
QLIB_OPEN_END_DATES = frozenset({"2099-12-31", "2099-01-01", "2999-12-31", "9999-12-31"})


def _normalize_qlib_symbol(symbol: str) -> str:
    """Normalize Qlib instrument symbol to yfinance ticker format.

    Supported formats:
      SH600519 → 600519.SS
      SZ000001 → 000001.SZ
      600519.SH → 600519.SS
      000001.SZ → 000001.SZ (passthrough)
    """
    raw = str(symbol).strip().upper()
    if not raw:
        return raw
    # Already yfinance format
    if raw.endswith((".SS", ".SZ")) and len(raw) > 4:
        return raw
    # Qlib prefix format: SH600519, SZ000001
    if raw.startswith("SH") and len(raw) >= 8:
        code = raw[2:]
        return f"{code}.SS"
    if raw.startswith("SZ") and len(raw) >= 8:
        code = raw[2:]
        return f"{code}.SZ"
    # Suffix format: 600519.SH, 000001.SZ already handled above
    # Fallback: use standard normalize_ticker
    return normalize_ticker(raw)


def _qlib_symbol_market(symbol: str) -> str:
    """Extract market label from Qlib symbol (SH → SSE, SZ → SZSE)."""
    raw = str(symbol).strip().upper()
    if raw.startswith("SH") or raw.endswith(".SH") or raw.endswith(".SS"):
        return "SSE"
    if raw.startswith("SZ") or raw.endswith(".SZ"):
        return "SZSE"
    # Try normalize_ticker suffix
    ticker = normalize_ticker(raw)
    if ticker.endswith(".SS"):
        return "SSE"
    if ticker.endswith(".SZ"):
        return "SZSE"
    return ""


def _parse_qlib_instruments(filepath: Path) -> List[Dict]:
    """Parse a Qlib instruments file (tab-separated, no header).

    Format: symbol start_date end_date
    Example: SH600519	2020-01-01	2099-12-31

    Handles:
    - tab-separated rows
    - whitespace-separated rows
    - optional accidental header row (skipped if starts with 'symbol')
    - Open-end dates (2099-12-31 etc.) → empty end_date
    """
    if not filepath.exists():
        return []

    rows = []
    with open(filepath, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue

            # Skip accidental header row
            if line_no == 1 and text.lower().startswith("symbol"):
                continue

            # Split on whitespace (tabs or spaces)
            parts = text.split()
            if len(parts) < 2:
                continue

            symbol = parts[0]
            start_date = parts[1]
            end_date = parts[2] if len(parts) >= 3 else "2099-12-31"

            ticker = _normalize_qlib_symbol(symbol)
            if not ticker:
                continue

            code = ticker.replace(".SS", "").replace(".SZ", "")

            # Open-end dates → empty end_date
            if end_date in QLIB_OPEN_END_DATES:
                end_date = ""

            rows.append({
                "qlib_symbol": symbol,
                "ticker": ticker,
                "code": code,
                "effective_date": start_date,
                "end_date": end_date,
                "source_provider": "qlib:instruments",
                "source_url": f"file://{filepath}",
                "ingested_at": NOW_UTC,
                "snapshot_count": 1,
                "qlib_market": _qlib_symbol_market(symbol),
            })

    return rows


def _qlib_rows_to_evidence(instrument_rows: List[Dict]) -> List[Dict]:
    """Convert Qlib instrument rows to evidence/snapshot rows.

    Each evidence row represents one membership interval snapshot.
    """
    evidence = []
    for row in instrument_rows:
        evidence.append({
            "ticker": row["ticker"],
            "code": row["code"],
            "trade_date": row["effective_date"],
            "source_provider": "qlib:instruments",
            "source_url": row["source_url"],
            "ingested_at": row["ingested_at"],
            "qlib_symbol": row["qlib_symbol"],
            "qlib_market": row["qlib_market"],
        })
    return evidence


def import_qlib_universe(
    qlib_dir: str = "~/.qlib/qlib_data/cn_data",
    market: str = "csi300",
) -> Optional[Dict]:
    """Import Qlib instruments file as tokenless historical universe.

    Args:
        qlib_dir: Path to Qlib data directory.
        market: Market/index name (e.g., 'csi300').

    Returns:
        Dict with keys: intervals, evidence, interval_count, evidence_count,
        effective_date_range, source_provider. None if file is missing.
    """
    qlib_path = Path(os.path.expanduser(qlib_dir))
    instruments_file = qlib_path / "instruments" / f"{market}.txt"

    print("=" * 60)
    print("  H23 — Qlib Instruments Universe Import")
    print("=" * 60)
    print(f"  Qlib dir:       {qlib_path}")
    print(f"  Market:         {market}")
    print(f"  Expected file:  {instruments_file}")

    if not instruments_file.exists():
        print()
        print("  ⚠️  QLIB INSTRUMENTS FILE NOT FOUND")
        print(f"  Path: {instruments_file}")
        print()
        print("  This fallback requires an existing Qlib instruments file.")
        print("  To generate one:")
        print(f"    cd ~/qlib/scripts/data_collector/cn_index")
        print(f"    python collector.py --index_name CSI300 --qlib_dir {qlib_path} --method parse_instruments")
        print("  See: https://github.com/microsoft/qlib/blob/main/scripts/data_collector/cn_index/README.md")
        print()
        print("  IMPORTANT: Existing data/cn_pit/universe.jsonl has NOT been overwritten.")
        print("  Data quality blockers remain unchanged.")
        return None

    print(f"  File exists, parsing...")

    instrument_rows = _parse_qlib_instruments(instruments_file)
    if not instrument_rows:
        print("  ⚠️  File parsed but produced 0 valid rows.")
        print("  Existing universe.jsonl has NOT been overwritten.")
        return None

    print(f"  Parsed {len(instrument_rows)} instrument rows")

    # Build evidence rows
    evidence_rows = _qlib_rows_to_evidence(instrument_rows)

    # Write universe.jsonl
    ensure_dirs()
    write_jsonl(UNIVERSE_PATH, instrument_rows)
    print(f"  Wrote {len(instrument_rows)} interval records to {UNIVERSE_PATH}")

    # Write universe_snapshots.jsonl evidence
    write_jsonl(UNIVERSE_SNAPSHOTS_PATH, evidence_rows)
    print(f"  Wrote {len(evidence_rows)} evidence rows to {UNIVERSE_SNAPSHOTS_PATH}")

    # Compute stats
    eff_dates = sorted(set(r["effective_date"] for r in instrument_rows if r.get("effective_date")))
    date_range = (eff_dates[0], eff_dates[-1]) if eff_dates else ("", "")

    result = {
        "intervals": instrument_rows,
        "evidence": evidence_rows,
        "interval_count": len(instrument_rows),
        "evidence_count": len(evidence_rows),
        "effective_date_range": date_range,
        "source_provider": "qlib:instruments",
    }

    print(f"  Interval count:    {result['interval_count']}")
    print(f"  Evidence count:    {result['evidence_count']}")
    print(f"  Date range:        {date_range[0]} → {date_range[1]}")
    print(f"  Source provider:   {result['source_provider']}")
    print("  ✅ Qlib universe import complete (H23).")

    return result


# ================================================================
# FETCH: Disclosures / Fundamentals
# ================================================================
def fetch_disclosures(
    years: List[int] = None,
    skip_existing: bool = False,
    tickers: List[str] = None,
) -> List[Dict]:
    """Fetch financial fundamentals with CNINFO filing dates.

    Data sources:
    - ROE, debt_to_equity: akshare stock_financial_abstract_ths (THS)
    - filing_date: akshare stock_report_disclosure (CNINFO)
    - PE, PB, div_yield, market_cap: yfinance snapshot (CURRENT VALUES, not PIT-safe)
    - fcf_yield: computed from yfinance freeCashflow / marketCap (CURRENT)
    """
    import akshare as ak

    ensure_dirs()

    if years is None:
        years = list(range(2020, datetime.now().year + 1))

    if skip_existing and FUNDAMENTALS_PATH.exists():
        print("  fundamentals.jsonl exists (--skip-existing), loading...")
        return read_jsonl(FUNDAMENTALS_PATH)

    # Load universe to get ticker list
    if tickers is None:
        universe = read_jsonl(UNIVERSE_PATH)
        if not universe:
            print("  ERROR: universe.jsonl not found. Run --fetch-universe first.")
            sys.exit(1)
        tickers = [r["ticker"] for r in universe]

    # Extract codes for akshare
    codes = [t.replace(".SS", "").replace(".SZ", "") for t in tickers]
    code_to_ticker = {c: t for c, t in zip(codes, tickers)}

    # ---- Step 1: Get disclosure dates for each year ----
    print(f"Fetching CNINFO disclosure dates for years {years[0]}-{years[-1]}...")
    disclosure_maps: Dict[int, Dict[str, str]] = {}  # year -> {code -> filing_date}
    for year in years:
        period_str = f"{year}年报"
        try:
            df = ak.stock_report_disclosure(market="沪深京", period=period_str)
            disc_map = {}
            for _, row in df.iterrows():
                code = str(row["股票代码"]).zfill(6)
                actual = row.get("实际披露")
                if actual is not None and str(actual) != "NaT":
                    disc_map[code] = str(actual)[:10]  # "YYYY-MM-DD"
            disclosure_maps[year] = disc_map
            print(f"  {period_str}: {len(df)} rows, {len(disc_map)} with actual dates")
        except Exception as e:
            print(f"  {period_str}: ERROR {e}")
            disclosure_maps[year] = {}
        time.sleep(1)

    # ---- Step 2: Get THS financial data per stock (parallel) ----
    print(f"Fetching THS financial abstracts for {len(codes)} tickers (parallel, workers=5)...")
    fin_data: Dict[str, pd.DataFrame] = {}

    def _fetch_one_ths(code: str) -> tuple:
        try:
            df = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
            return (code, df)
        except Exception as e:
            return (code, None)

    completed = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_one_ths, c): c for c in codes}
        for future in as_completed(futures):
            code, df = future.result()
            completed += 1
            if df is not None:
                fin_data[code] = df
            if completed % 50 == 0:
                print(f"  Progress: {completed}/{len(codes)} ({len(fin_data)} ok)")
    print(f"  Done: {len(fin_data)}/{len(codes)} THS abstracts fetched")

    # NOTE: We intentionally skip yfinance snapshot data (PE/PB/div_yield/market_cap/fcf_yield).
    # yfinance always returns CURRENT values — including them in historical records would
    # introduce FUTURE FUNCTION, violating the PIT contract.
    #   "Never backfill current metrics into earlier filing_date records."
    # ROE + debt_to_equity from THS (matched to CNINFO filing dates) are sufficient for
    # the value ranker, which requires ≥2 of (ROE, FCF yield, D/E).

    # ---- Step 3: Combine into fundamental records ----
    print("Combining THS financials with CNINFO filing dates...")
    records = []
    seen_keys = set()

    for code, df_th in fin_data.items():
        ticker = code_to_ticker.get(code)
        if not ticker:
            continue

        for _, row in df_th.iterrows():
            report_year = row.get("报告期")
            if report_year is None:
                continue
            try:
                report_year = int(report_year)
            except (ValueError, TypeError):
                continue

            if report_year not in years:
                continue

            report_period = f"{report_year}-12-31"
            filing_date = disclosure_maps.get(report_year, {}).get(code, "")
            if not filing_date:
                # Fallback: estimate filing date as April 30 of next year
                filing_date = f"{report_year + 1}-04-30"

            roe = parse_pct(row.get("净资产收益率"))
            debt_ratio_pct = parse_pct(row.get("资产负债率"))
            equity_ratio = parse_decimal(row.get("产权比率"))
            # debt_to_equity: prefer 产权比率, fallback to compute from 资产负债率
            if equity_ratio is not None:
                debt_to_equity = equity_ratio
            elif debt_ratio_pct is not None:
                debt_to_equity = round(debt_ratio_pct / (100 - debt_ratio_pct), 4) if debt_ratio_pct < 100 else None
            else:
                debt_to_equity = None

            # Dedup key
            key = (ticker, report_period, filing_date)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            records.append({
                "ticker": ticker,
                "code": code,
                "report_period": report_period,
                "filing_date": filing_date,
                "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": NOW_UTC,
                "roe": roe,
                "debt_to_equity": debt_to_equity,
                "data_quality_note": (
                    "ROE/debt_to_equity: THS annual data matched to CNINFO filing dates. "
                    "PE/PB/div_yield/market_cap/fcf_yield: intentionally OMITTED — "
                    "yfinance snapshots are CURRENT values and NOT PIT-SAFE. "
                    "Value ranker requires ≥2 of (ROE, FCF yield, D/E); ROE + D/E suffice."
                ),
            })

    write_jsonl(FUNDAMENTALS_PATH, records)
    print(f"  Wrote {len(records)} records to {FUNDAMENTALS_PATH}")
    return records


def _build_fallback_provider():
    """Build a fallback provider chain: Tushare → Akshare → YFinance.

    Returns a FallbackMarketDataProvider with providers in priority order.
    Provider configs (e.g. TUSHARE_TOKEN) are read from environment variables.
    """
    from backtest.market_data import (
        AkshareProvider,
        FallbackMarketDataProvider,
        TushareProvider,
        YFinanceProvider,
    )

    providers = [
        TushareProvider(token=os.environ.get("TUSHARE_TOKEN")),
        AkshareProvider(),
        YFinanceProvider(),
    ]
    return FallbackMarketDataProvider(providers)


# ================================================================
# FETCH: Prices
# ================================================================
def fetch_prices(
    start: str = "2020-01-01",
    end: str = None,
    skip_existing: bool = False,
    tickers: List[str] = None,
) -> pd.DataFrame:
    """Fetch historical daily close prices from yfinance.

    Output: wide CSV with 'date' column + ticker columns, including 000300.SS benchmark.
    """
    ensure_dirs()

    if end is None:
        end = NOW_DATE

    if skip_existing and PRICES_PATH.exists():
        print("  prices.csv exists (--skip-existing), loading...")
        return pd.read_csv(PRICES_PATH)

    # Load universe for ticker list
    if tickers is None:
        universe = read_jsonl(UNIVERSE_PATH)
        if not universe:
            print("  ERROR: universe.jsonl not found. Run --fetch-universe first.")
            sys.exit(1)
        tickers = [r["ticker"] for r in universe]

    all_tickers = list(tickers) + ["000300.SS"]

    print(f"Downloading prices for {len(all_tickers)} tickers ({start} → {end})...")

    from backtest.market_data import LoaderBlockedError

    try:
        provider = _build_fallback_provider()
        result = provider.get_close_prices(all_tickers, start, end)
    except LoaderBlockedError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    prices = result.prices.copy()

    # Forward-fill small gaps (1-2 days), keep larger gaps as NaN
    prices = prices.ffill(limit=3)

    # Reset index: date as column
    prices = prices.reset_index()
    if "Date" in prices.columns:
        prices = prices.rename(columns={"Date": "date"})
    if "index" in prices.columns:
        prices = prices.rename(columns={"index": "date"})
    if "date" in prices.columns and hasattr(prices["date"], "dt"):
        prices["date"] = prices["date"].dt.strftime("%Y-%m-%d")

    prices.to_csv(PRICES_PATH, index=False)
    print(f"  Wrote {len(prices)} rows x {len(prices.columns)} cols to {PRICES_PATH}")

    # Save metadata
    meta = {
        "adjustment": "auto_adjusted (splits + dividends)",
        "fetch_date": NOW_UTC,
        "start": start,
        "end": end,
        "ticker_count": len(tickers),
        "benchmark": "000300.SS",
        "rows": len(prices),
        "data_sources": {
            "prices": {
                "fallback_chain": result.fallback_chain,
                "selected_provider": result.sources_used.get("__selected", "unknown"),
                "sha256": result.sources_used.get("__sha256", ""),
                "rows": len(prices),
                "fallback_reason": result.fallback_reason,
                "precheck_log": result.precheck_log,
            }
        }
    }
    with open(METADATA_PATH, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    return prices


# ================================================================
# VALIDATE
# ================================================================
def validate(
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    prices_path: Optional[Path] = None,
    universe_path: Optional[Path] = None,
    universe_snapshots_path: Optional[Path] = None,
) -> Dict:
    """Validate all output files and return a summary dict (H22 extended).

    Args:
        period_start: Optional start date for period-scoped validation (H26A).
        period_end: Optional end date for period-scoped validation (H26A).
        When both are provided, validation is scoped to [period_start, period_end].
        When omitted, performs full-file validation (existing behavior).
    """
    prices_path = Path(prices_path) if prices_path else PRICES_PATH
    universe_path = Path(universe_path) if universe_path else UNIVERSE_PATH
    universe_snapshots_path = (
        Path(universe_snapshots_path)
        if universe_snapshots_path
        else UNIVERSE_SNAPSHOTS_PATH
    )
    price_label = prices_path.name
    universe_label = universe_path.name
    snapshots_label = universe_snapshots_path.name
    errors = []
    warnings = []
    summary = {
        "universe_rows": 0,
        "universe_interval_count": 0,
        "universe_open_intervals": 0,
        "universe_min_effective_date": "",
        "universe_max_effective_date": "",
        "universe_source_providers": [],
        "universe_snapshots_exists": False,
        "active_universe_start_count": 0,
        "active_universe_end_count": 0,
        "active_price_coverage_start": "",
        "active_price_coverage_end": "",
        "active_price_data_start": "",
        "active_price_data_end": "",
        "snapshot_count": 0,
        "snapshot_min_date": "",
        "snapshot_max_date": "",
        "fundamental_rows": 0,
        "ticker_count": 0,
        "price_rows": 0,
        "price_columns": 0,
        "date_range": "",
        "validation_errors": [],
        "data_quality_blockers": [],
        "is_clean": False,
    }

    is_period = bool(period_start and period_end)
    if is_period:
        summary["validation_scope"] = "period"
        summary["period_start"] = period_start
        summary["period_end"] = period_end
        summary["period_active_universe_start_count"] = 0
        summary["period_active_universe_end_count"] = 0
        summary["period_price_coverage_start"] = ""
        summary["period_price_coverage_end"] = ""
        summary["period_price_data_start"] = ""
        summary["period_price_data_end"] = ""
        summary["period_nearest_price_start"] = ""
        summary["period_nearest_price_end"] = ""

    # ---- Check files exist ----
    for path, name in [
        (universe_path, universe_label),
        (FUNDAMENTALS_PATH, "fundamentals.jsonl"),
        (prices_path, price_label),
    ]:
        if not path.exists():
            errors.append(f"missing_file: {name}")

    # ---- Validate universe.jsonl ----
    universe = read_jsonl(universe_path)
    summary["universe_rows"] = len(universe)
    universe_tickers = set()

    if universe:
        for idx, row in enumerate(universe):
            ticker = row.get("ticker", "")
            universe_tickers.add(ticker)
            if not ticker:
                errors.append(f"universe:row{idx}: missing ticker")
            if not row.get("effective_date"):
                errors.append(f"universe:{ticker}: missing effective_date")
            if not row.get("source_url"):
                errors.append(f"universe:{ticker}: missing source_url")
            if not row.get("ingested_at"):
                errors.append(f"universe:{ticker}: missing ingested_at")
            # Check date format
            eff = row.get("effective_date", "")
            end = row.get("end_date", "")
            if eff and end and eff > end:
                errors.append(f"universe:{ticker}: effective_date > end_date")
        summary["ticker_count"] = len(universe_tickers)
        # --- H22: compute interval/open-interval/date-range stats ---
        all_eff_dates = [r["effective_date"] for r in universe if r.get("effective_date")]
        if all_eff_dates:
            summary["universe_min_effective_date"] = min(all_eff_dates)
            summary["universe_max_effective_date"] = max(all_eff_dates)
        open_intervals = [r for r in universe if not r.get("end_date")]
        summary["universe_open_intervals"] = len(open_intervals)
        summary["universe_interval_count"] = len(universe)
        # --- H23: collect source_providers ---
        providers = sorted(set(
            r.get("source_provider", "") for r in universe if r.get("source_provider")
        ))
        summary["universe_source_providers"] = providers
    else:
        warnings.append("universe.jsonl is empty")

    # ---- H22: Validate universe_snapshots.jsonl (if present) ----
    if universe_snapshots_path.exists():
        summary["universe_snapshots_exists"] = True
        snapshots = read_jsonl(universe_snapshots_path)
        summary["snapshot_count"] = len(snapshots)
        if snapshots:
            snap_dates = sorted(set(s["trade_date"] for s in snapshots if s.get("trade_date")))
            if snap_dates:
                summary["snapshot_min_date"] = snap_dates[0]
                summary["snapshot_max_date"] = snap_dates[-1]
            # Check snapshot date range covers universe effective dates
            if all_eff_dates and snap_dates:
                if min(all_eff_dates) < snap_dates[0]:
                    warnings.append(
                        f"universe min effective_date {min(all_eff_dates)} < snapshots start {snap_dates[0]}"
                    )
    else:
        summary["snapshot_count"] = 0

    # ---- Validate fundamentals.jsonl ----
    fundamentals = read_jsonl(FUNDAMENTALS_PATH)
    summary["fundamental_rows"] = len(fundamentals)
    seen_fund_keys = set()
    fund_tickers = set()

    if fundamentals:
        for idx, row in enumerate(fundamentals):
            ticker = row.get("ticker", "")
            fund_tickers.add(ticker)
            rp = row.get("report_period", "")
            fd = row.get("filing_date", "")
            ing = row.get("ingested_at", "")

            if not ticker:
                errors.append(f"fundamentals:row{idx}: missing ticker")
                continue
            if not rp:
                errors.append(f"fundamentals:{ticker}: missing report_period")
            if not fd:
                errors.append(f"fundamentals:{ticker}: missing filing_date")
            if not row.get("source_url"):
                errors.append(f"fundamentals:{ticker}: missing source_url")
            if not ing:
                errors.append(f"fundamentals:{ticker}: missing ingested_at")

            # Check filing_date <= ingested_at
            if fd and ing:
                if fd[:10] > ing[:10]:
                    errors.append(
                        f"fundamentals:{ticker}: filing_date {fd[:10]} > ingested_at {ing[:10]}"
                    )

            # Check duplicates
            key = (ticker, rp, fd)
            if key in seen_fund_keys:
                errors.append(f"fundamentals:{ticker}: duplicate ({ticker}, {rp}, {fd})")
            seen_fund_keys.add(key)
    else:
        warnings.append("fundamentals.jsonl is empty")

    # ---- Validate prices.csv ----
    if prices_path.exists():
        try:
            prices = pd.read_csv(prices_path)
            summary["price_rows"] = len(prices)
            summary["price_columns"] = len(prices.columns)
            dates = []
            if "date" not in prices.columns:
                errors.append(f"{price_label}: missing 'date' column")
            else:
                dates = sorted(prices["date"].dropna().unique())
                if len(dates) >= 2:
                    summary["date_range"] = f"{dates[0]} → {dates[-1]}"

            # Check if any universe tickers are in price columns
            price_cols = set(prices.columns) - {"date", "Date"}
            overlap = universe_tickers & price_cols
            if not overlap:
                errors.append(f"{price_label}: no columns overlap universe tickers")
            elif len(overlap) < len(universe_tickers) * 0.5:
                warnings.append(
                    f"{price_label}: only {len(overlap)}/{len(universe_tickers)} universe tickers have price data"
                )

            # Check benchmark column
            if "000300.SS" not in price_cols:
                warnings.append(f"{price_label}: missing benchmark column 000300.SS")

            def _price_data_cols_on(price_date: str) -> set:
                if not price_date or "date" not in prices.columns:
                    return set()
                rows = prices.loc[prices["date"] == price_date]
                if rows.empty:
                    return set()
                row = rows.iloc[0]
                return {c for c in price_cols if c in row.index and pd.notna(row[c])}

            def _nearest_on_or_after(target: str) -> str:
                for d in dates:
                    if d >= target:
                        return d
                return ""

            def _nearest_on_or_before(target: str) -> str:
                for d in reversed(dates):
                    if d <= target:
                        return d
                return ""

            if universe and dates:
                price_start = dates[0]
                price_end = dates[-1]
                active_start = {r["ticker"] for r in universe if _interval_active(r, price_start)}
                active_end = {r["ticker"] for r in universe if _interval_active(r, price_end)}
                start_overlap = active_start & price_cols
                end_overlap = active_end & price_cols
                start_data_overlap = active_start & _price_data_cols_on(price_start)
                end_data_overlap = active_end & _price_data_cols_on(price_end)
                summary["active_universe_start_count"] = len(active_start)
                summary["active_universe_end_count"] = len(active_end)
                summary["active_price_coverage_start"] = f"{len(start_overlap)}/{len(active_start)}"
                summary["active_price_coverage_end"] = f"{len(end_overlap)}/{len(active_end)}"
                summary["active_price_data_start"] = f"{len(start_data_overlap)}/{len(active_start)}"
                summary["active_price_data_end"] = f"{len(end_data_overlap)}/{len(active_end)}"
                # Full-file blockers only apply in full-file mode (not period-scoped)
                if not is_period:
                    if active_start and len(start_overlap) < len(active_start):
                        dq_blockers = summary.setdefault("data_quality_blockers", [])
                        if "price_coverage" not in dq_blockers:
                            dq_blockers.append("price_coverage")
                        warnings.append(
                            f"{price_label}: active universe coverage at {price_start} is "
                            f"{len(start_overlap)}/{len(active_start)}"
                        )
                    if active_end and len(end_overlap) < len(active_end):
                        dq_blockers = summary.setdefault("data_quality_blockers", [])
                        if "price_coverage" not in dq_blockers:
                            dq_blockers.append("price_coverage")
                        warnings.append(
                            f"{price_label}: active universe coverage at {price_end} is "
                            f"{len(end_overlap)}/{len(active_end)}"
                        )
                    if active_start and len(start_data_overlap) < len(active_start):
                        dq_blockers = summary.setdefault("data_quality_blockers", [])
                        if "price_coverage" not in dq_blockers:
                            dq_blockers.append("price_coverage")
                        warnings.append(
                            f"{price_label}: active price data at {price_start} is "
                            f"{len(start_data_overlap)}/{len(active_start)}"
                        )
                    if active_end and len(end_data_overlap) < len(active_end):
                        dq_blockers = summary.setdefault("data_quality_blockers", [])
                        if "price_coverage" not in dq_blockers:
                            dq_blockers.append("price_coverage")
                        warnings.append(
                            f"{price_label}: active price data at {price_end} is "
                            f"{len(end_data_overlap)}/{len(active_end)}"
                        )

            # ---- H28 (H1): Checkpoint-based price_coverage (union-over-checkpoints) ----
            if not is_period and dates:
                checkpoints = _checkpoint_dates(dates)
                failed_cps = []
                for cp_date in checkpoints:
                    active_cp = {r["ticker"] for r in universe if _interval_active(r, cp_date)}
                    cp_overlap = active_cp & price_cols
                    cp_data_overlap = active_cp & _price_data_cols_on(cp_date)
                    missing = active_cp - price_cols
                    if missing:
                        failed_cps.append({
                            "date": cp_date,
                            "active_count": len(active_cp),
                            "covered_count": len(cp_overlap),
                            "missing_count": len(missing),
                            "sample_missing_tickers": sorted(missing)[:20],
                        })
                if failed_cps:
                    summary["price_coverage_failed_checkpoints"] = failed_cps
                    dq_blockers = summary.setdefault("data_quality_blockers", [])
                    if "price_coverage" not in dq_blockers:
                        dq_blockers.append("price_coverage")
                    for fcp in failed_cps:
                        warnings.append(
                            f"{price_label}: checkpoint {fcp['date']} coverage "
                            f"{fcp['covered_count']}/{fcp['active_count']} "
                            f"({fcp['missing_count']} missing)"
                        )

            # ---- H26A: Period-scoped price coverage ----
            if is_period and universe and dates:
                price_cols = set(prices.columns) - {"date", "Date"}
                price_start = dates[0]
                price_end = dates[-1]
                nearest_period_start = _nearest_on_or_after(period_start)
                nearest_period_end = _nearest_on_or_before(period_end)
                summary["period_nearest_price_start"] = nearest_period_start
                summary["period_nearest_price_end"] = nearest_period_end
                ps_active_start = {
                    r["ticker"] for r in universe
                    if _interval_active(r, nearest_period_start or period_start)
                }
                ps_active_end = {
                    r["ticker"] for r in universe
                    if _interval_active(r, nearest_period_end or period_end)
                }
                ps_start_overlap = ps_active_start & price_cols
                ps_end_overlap = ps_active_end & price_cols
                ps_start_data_overlap = ps_active_start & _price_data_cols_on(nearest_period_start)
                ps_end_data_overlap = ps_active_end & _price_data_cols_on(nearest_period_end)
                summary["period_active_universe_start_count"] = len(ps_active_start)
                summary["period_active_universe_end_count"] = len(ps_active_end)
                summary["period_price_coverage_start"] = f"{len(ps_start_overlap)}/{len(ps_active_start)}"
                summary["period_price_coverage_end"] = f"{len(ps_end_overlap)}/{len(ps_active_end)}"
                summary["period_price_data_start"] = f"{len(ps_start_data_overlap)}/{len(ps_active_start)}"
                summary["period_price_data_end"] = f"{len(ps_end_data_overlap)}/{len(ps_active_end)}"
                if period_start < price_start or period_end > price_end:
                    dq_blockers = summary.setdefault("data_quality_blockers", [])
                    if "price_coverage" not in dq_blockers:
                        dq_blockers.append("price_coverage")
                    warnings.append(
                        f"{price_label}: period {period_start}→{period_end} is outside "
                        f"price range {price_start}→{price_end}"
                    )
                if ps_active_start and len(ps_start_overlap) < len(ps_active_start):
                    dq_blockers = summary.setdefault("data_quality_blockers", [])
                    if "price_coverage" not in dq_blockers:
                        dq_blockers.append("price_coverage")
                    warnings.append(
                        f"{price_label}: period active coverage at {period_start} is "
                        f"{len(ps_start_overlap)}/{len(ps_active_start)}"
                    )
                if ps_active_end and len(ps_end_overlap) < len(ps_active_end):
                    dq_blockers = summary.setdefault("data_quality_blockers", [])
                    if "price_coverage" not in dq_blockers:
                        dq_blockers.append("price_coverage")
                    warnings.append(
                        f"{price_label}: period active coverage at {period_end} is "
                        f"{len(ps_end_overlap)}/{len(ps_active_end)}"
                    )
                if ps_active_start and len(ps_start_data_overlap) < len(ps_active_start):
                    dq_blockers = summary.setdefault("data_quality_blockers", [])
                    if "price_coverage" not in dq_blockers:
                        dq_blockers.append("price_coverage")
                    warnings.append(
                        f"{price_label}: period active price data at "
                        f"{nearest_period_start or period_start} is "
                        f"{len(ps_start_data_overlap)}/{len(ps_active_start)}"
                    )
                if ps_active_end and len(ps_end_data_overlap) < len(ps_active_end):
                    dq_blockers = summary.setdefault("data_quality_blockers", [])
                    if "price_coverage" not in dq_blockers:
                        dq_blockers.append("price_coverage")
                    warnings.append(
                        f"{price_label}: period active price data at "
                        f"{nearest_period_end or period_end} is "
                        f"{len(ps_end_data_overlap)}/{len(ps_active_end)}"
                    )
        except Exception as e:
            errors.append(f"{price_label}: parse error: {e}")
    else:
        errors.append(f"missing_file: {price_label}")

    # ---- Check CN_PIT_FileSource data quality ----
    try:
        sys.path.insert(0, str(VT_DIR / "backtest" / "experiments"))
        from fundamental_backtest import CN_PIT_FileSource
        source = CN_PIT_FileSource(
            str(DATA_DIR),
            prices_path=str(prices_path),
            universe_path=str(universe_path),
            universe_snapshots_path=str(universe_snapshots_path),
        )
        if is_period:
            # H26A: Period-scoped data quality — uses data_quality_for_period
            dq = source.data_quality_for_period(period_start, period_end)
        else:
            dq = source.data_quality
        if not dq.is_clean:
            flags = []
            if dq.survivorship_bias:
                flags.append("survivorship_bias")
            if dq.future_function:
                flags.append("future_function")
            if dq.filing_delay:
                flags.append("filing_delay")
            if dq.ungated_fundamentals:
                flags.append("ungated_fundamentals")
            # Extend existing blockers (preserve price_coverage from earlier checks)
            existing = summary.get("data_quality_blockers", [])
            for f in flags:
                if f not in existing:
                    existing.append(f)
            summary["data_quality_blockers"] = existing
        else:
            summary["is_clean"] = True

        if source.validation_errors:
            errors.extend(source.validation_errors)

        # Check research_only
        if source.research_only:
            summary["data_quality_blockers"].append("research_only")
    except Exception as e:
        errors.append(f"CN_PIT_FileSource: {e}")

    summary["validation_errors"] = errors
    if warnings:
        summary.setdefault("warnings", []).extend(warnings)

    # --- H3: snapshot coverage vs validation window ---
    # If snapshot evidence is present but doesn't span the active validation window,
    # that's a survivorship_bias blocker (not just a warning).
    # These go to data_quality_blockers only — they are NOT structural errors.
    providers = set(summary.get("universe_source_providers", []))
    uses_interval_evidence = providers == {"qlib:instruments"}
    if summary.get("snapshot_count", 0) > 0 and not uses_interval_evidence:
        snap_min = summary.get("snapshot_min_date", "")
        snap_max = summary.get("snapshot_max_date", "")
        if is_period:
            coverage_min, coverage_max = period_start, period_end
            coverage_label = "validation period"
        else:
            price_dates_str = summary.get("date_range", "")
            if not price_dates_str or " → " not in price_dates_str:
                coverage_min = coverage_max = ""
            else:
                coverage_min, coverage_max = price_dates_str.split(" → ")
            coverage_label = "price data"
        if coverage_min and coverage_max:
            if snap_min and snap_min > coverage_min:
                dq_blockers = summary.setdefault("data_quality_blockers", [])
                if "survivorship_bias" not in dq_blockers:
                    dq_blockers.append("survivorship_bias")
                warnings.append(
                    f"snapshot coverage ({snap_min}) starts after {coverage_label} "
                    f"({coverage_min}) — survivorship concern"
                )
            if (
                snap_max
                and coverage_max
                and snap_max < coverage_max
                and not _within_tushare_carry_forward(snap_max, coverage_max)
            ):
                dq_blockers = summary.setdefault("data_quality_blockers", [])
                if "survivorship_bias" not in dq_blockers:
                    dq_blockers.append("survivorship_bias")
                warnings.append(
                    f"snapshot coverage ({snap_max}) ends before {coverage_label} "
                    f"({coverage_max}) — survivorship concern"
                )

    # --- M4: Status semantics ---
    dq_blockers = summary.get("data_quality_blockers", [])
    if errors:
        summary["status"] = "FAILED"
    elif dq_blockers:
        summary["status"] = "BLOCKED"
    else:
        summary["status"] = "PASSED"

    summary["can_deploy_data_quality"] = (
        summary["status"] == "PASSED"
    )

    # H28 (L): Provenance fields — do not affect status
    try:
        summary["prices_csv_mtime"] = os.path.getmtime(str(prices_path))
    except OSError:
        summary["prices_csv_mtime"] = None
    try:
        import hashlib
        sha = hashlib.sha256()
        with open(prices_path, "rb") as f:
            while chunk := f.read(8192):
                sha.update(chunk)
        summary["prices_csv_sha256"] = sha.hexdigest()
    except (OSError, Exception):
        summary["prices_csv_sha256"] = None

    return summary


def print_summary(summary: Dict):
    """Pretty-print validation summary."""
    print()
    print("=" * 60)
    print("  CN PIT DATA INGESTION — VALIDATION SUMMARY")
    print("=" * 60)
    print(f"  Status:        {summary['status']}")
    print(f"  Universe rows: {summary['universe_rows']}")
    print(f"  Fundamental:   {summary['fundamental_rows']} rows")
    print(f"  Tickers:       {summary['ticker_count']}")
    print(f"  Prices:        {summary['price_rows']} rows x {summary['price_columns']} cols")
    print(f"  Date range:    {summary.get('date_range', 'N/A')}")
    print()
    # H22: show universe interval stats
    print(f"  Universe intervals:   {summary.get('universe_interval_count', 0)}")
    print(f"  Universe open:        {summary.get('universe_open_intervals', 0)}")
    print(f"  Universe eff range:   {summary.get('universe_min_effective_date', 'N/A')} → {summary.get('universe_max_effective_date', 'N/A')}")
    providers = summary.get("universe_source_providers", [])
    print(f"  Source providers:     {', '.join(providers) if providers else '(none)'}")
    print(f"  Snapshot evidence:    {'present' if summary.get('universe_snapshots_exists') else 'missing'}")
    if summary.get("active_universe_start_count") or summary.get("active_universe_end_count"):
        print(f"  Active coverage start:{summary.get('active_price_coverage_start', 'N/A')}")
        print(f"  Active coverage end:  {summary.get('active_price_coverage_end', 'N/A')}")
        print(f"  Active data start:    {summary.get('active_price_data_start', 'N/A')}")
        print(f"  Active data end:      {summary.get('active_price_data_end', 'N/A')}")
    if summary.get("snapshot_count"):
        print(f"  Raw snapshots:        {summary['snapshot_count']}")
        print(f"  Snapshot range:       {summary.get('snapshot_min_date', 'N/A')} → {summary.get('snapshot_max_date', 'N/A')}")
    # H26A: Period-scoped validation fields
    if summary.get("validation_scope") == "period":
        print()
        print(f"  Validation scope:     PERIOD [{summary['period_start']} → {summary['period_end']}]")
        print(f"  Period active start:  {summary.get('period_active_universe_start_count', 0)} tickers")
        print(f"  Period active end:    {summary.get('period_active_universe_end_count', 0)} tickers")
        print(f"  Period coverage start:{summary.get('period_price_coverage_start', 'N/A')}")
        print(f"  Period coverage end:  {summary.get('period_price_coverage_end', 'N/A')}")
        print(f"  Period data start:    {summary.get('period_price_data_start', 'N/A')}")
        print(f"  Period data end:      {summary.get('period_price_data_end', 'N/A')}")
    print()

    if summary.get("validation_errors"):
        print(f"  Validation Errors ({len(summary['validation_errors'])}):")
        for e in summary["validation_errors"]:
            print(f"    - {e}")
        print()

    if summary.get("warnings"):
        print(f"  Warnings ({len(summary['warnings'])}):")
        for w in summary["warnings"]:
            print(f"    - {w}")
        print()

    dq_blockers = summary.get("data_quality_blockers", [])
    if dq_blockers:
        print(f"  Data Quality Blockers ({len(dq_blockers)}):")
        for b in dq_blockers:
            print(f"    - {b}")
        print("  ⚠️  Deployment blocked until all flags are False.")
    else:
        print("  Data Quality: ALL CLEAN ✓")
        print("  Deployment gate: PASSED (data quality only)")

    print()
    print(f"  CN_PIT_FileSource.is_clean: {summary['is_clean']}")

    if summary["status"] == "FAILED":
        print()
        print("  ❌ VALIDATION FAILED — structural errors detected, deployment blocked.")
    elif summary["status"] == "BLOCKED":
        print()
        print("  ⚠️  VALIDATION BLOCKED — files are structurally valid but data-quality blockers exist.")
        print("  BLOCKED is not file-structure failure, but data is NOT deployable.")
    else:
        print()
        print("  ✅ VALIDATION PASSED — files valid, no data-quality blockers, deployment-ready.")


# ================================================================
# MAIN
# ================================================================
def main():
    parser = argparse.ArgumentParser(
        description="CN PIT Data Ingestion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/ingest_cn_pit_data.py --all
  python scripts/ingest_cn_pit_data.py --fetch-universe
  python scripts/ingest_cn_pit_data.py --fetch-prices --start 2020-01-01
  python scripts/ingest_cn_pit_data.py --validate
  python scripts/ingest_cn_pit_data.py --validate --prices-file data/cn_pit/prices_h28_candidate.csv
  python scripts/ingest_cn_pit_data.py --validate --universe-file data/cn_pit/universe_h30_candidate.jsonl --universe-snapshots-file data/cn_pit/universe_snapshots_h30_candidate.jsonl
  python scripts/ingest_cn_pit_data.py --validate --period-start 2025-01-01 --period-end 2026-05-18
  python scripts/ingest_cn_pit_data.py --all --years 2022,2023,2024,2025
  python scripts/ingest_cn_pit_data.py --import-qlib-universe --qlib-dir ~/.qlib/qlib_data/cn_data --market csi300
        """,
    )
    parser.add_argument("--fetch-universe", action="store_true", help="Fetch HS300 constituents (current snapshot)")
    parser.add_argument("--fetch-historical-universe", action="store_true", help="Fetch historical HS300 constituent snapshots (H22)")
    parser.add_argument("--import-qlib-universe", action="store_true",
                        help="Import Qlib instruments file as tokenless historical universe (H23)")
    parser.add_argument("--qlib-dir", default="~/.qlib/qlib_data/cn_data",
                        help="Qlib data directory (default: ~/.qlib/qlib_data/cn_data)")
    parser.add_argument("--market", default="csi300",
                        help="Market/index name for Qlib instruments file (default: csi300)")
    parser.add_argument("--fetch-disclosures", action="store_true", help="Fetch financials + filing dates")
    parser.add_argument("--fetch-prices", action="store_true", help="Fetch historical prices")
    parser.add_argument("--validate", action="store_true", help="Validate all data files")
    parser.add_argument("--prices-file", default=None,
                        help="Optional prices CSV to validate instead of data/cn_pit/prices.csv. "
                             "Used for non-destructive candidate validation.")
    parser.add_argument("--universe-file", default=None,
                        help="Optional universe JSONL to validate instead of data/cn_pit/universe.jsonl. "
                             "Used for non-destructive candidate validation.")
    parser.add_argument("--universe-snapshots-file", default=None,
                        help="Optional universe snapshots JSONL to validate instead of "
                             "data/cn_pit/universe_snapshots.jsonl. Used for non-destructive "
                             "candidate validation.")
    parser.add_argument("--period-start", default=None,
                        help="Period start date for --validate (H26A, e.g. 2025-01-01). "
                             "Must be paired with --period-end. "
                             "Scopes validation to a deployment window.")
    parser.add_argument("--period-end", default=None,
                        help="Period end date for --validate (H26A, e.g. 2026-05-18). "
                             "Must be paired with --period-start.")
    parser.add_argument("--all", action="store_true", help="Run all fetch modes + validate")
    parser.add_argument("--start", default="2020-01-01", help="Price/Universe data start date")
    parser.add_argument("--end", default=None, help="Price/Universe data end date (default: today)")
    parser.add_argument("--years", default=None, help="Comma-separated years for disclosures (e.g. 2020,2021,2022)")
    parser.add_argument("--years-back", type=int, default=6, help="Years back for universe effective dates")
    parser.add_argument("--skip-existing", action="store_true", help="Skip fetch if output file exists")
    parser.add_argument("--limit-tickers", type=int, default=0, help="Limit to first N tickers (for testing)")

    args = parser.parse_args()

    # If no action specified, default to validate
    if not any([args.fetch_universe, args.fetch_historical_universe,
                args.fetch_disclosures, args.fetch_prices, args.validate, args.all,
                args.import_qlib_universe]):
        args.validate = True

    do_all = args.all
    years_list = None
    if args.years:
        years_list = [int(y.strip()) for y in args.years.split(",")]

    # ---- Execute ----
    start_time = time.time()

    if args.import_qlib_universe:
        print("\n--- IMPORT QLIB UNIVERSE (H23) ---")
        result = import_qlib_universe(
            qlib_dir=args.qlib_dir,
            market=args.market,
        )
        if result is not None:
            print(f"  DONE: {result['interval_count']} universe records")
        else:
            print("  DONE: no file found, existing data preserved.")
        return 0

    if do_all or args.fetch_historical_universe:
        print("\n--- FETCH HISTORICAL UNIVERSE (H22) ---")
        rows = fetch_universe(
            years_back=args.years_back,
            skip_existing=False,  # Never skip for historical
            start=args.start,
            end=args.end,
        )
        print(f"  DONE: {len(rows)} universe records")
    elif do_all or args.fetch_universe:
        print("\n--- FETCH UNIVERSE ---")
        rows = fetch_universe(years_back=args.years_back, skip_existing=args.skip_existing)
        print(f"  DONE: {len(rows)} universe records")

    if do_all or args.fetch_disclosures:
        print("\n--- FETCH DISCLOSURES ---")
        tickers = None
        if args.limit_tickers > 0:
            universe = read_jsonl(UNIVERSE_PATH)
            tickers = [r["ticker"] for r in universe[:args.limit_tickers]]
        rows = fetch_disclosures(years=years_list, skip_existing=args.skip_existing, tickers=tickers)
        print(f"  DONE: {len(rows)} fundamental records")

    if do_all or args.fetch_prices:
        print("\n--- FETCH PRICES ---")
        tickers = None
        if args.limit_tickers > 0:
            universe = read_jsonl(UNIVERSE_PATH)
            tickers = [r["ticker"] for r in universe[:args.limit_tickers]]
        fetch_prices(start=args.start, end=args.end, skip_existing=args.skip_existing, tickers=tickers)
        print(f"  DONE: prices written to {PRICES_PATH}")

    if do_all or args.validate:
        print("\n--- VALIDATE ---")
        period_start = getattr(args, 'period_start', None)
        period_end = getattr(args, 'period_end', None)
        if period_start and not period_end:
            print("  ERROR: --period-start requires --period-end")
            sys.exit(1)
        if period_end and not period_start:
            print("  ERROR: --period-end requires --period-start")
            sys.exit(1)
        prices_path = Path(args.prices_file) if args.prices_file else PRICES_PATH
        if not prices_path.is_absolute():
            prices_path = VT_DIR / prices_path
        universe_path = Path(args.universe_file) if args.universe_file else UNIVERSE_PATH
        if not universe_path.is_absolute():
            universe_path = VT_DIR / universe_path
        universe_snapshots_path = (
            Path(args.universe_snapshots_file)
            if args.universe_snapshots_file
            else UNIVERSE_SNAPSHOTS_PATH
        )
        if not universe_snapshots_path.is_absolute():
            universe_snapshots_path = VT_DIR / universe_snapshots_path
        validate_kwargs = {
            "period_start": period_start,
            "period_end": period_end,
        }
        if args.prices_file:
            validate_kwargs["prices_path"] = prices_path
        if args.universe_file:
            validate_kwargs["universe_path"] = universe_path
        if args.universe_snapshots_file:
            validate_kwargs["universe_snapshots_path"] = universe_snapshots_path
        summary = validate(**validate_kwargs)
        print_summary(summary)

        # Save validation report
        if period_start and period_end:
            report_name = f"validation_report_{period_start}_{period_end}"
        else:
            report_name = "validation_report"
        if prices_path != PRICES_PATH:
            report_name = f"{report_name}_{prices_path.stem}"
        if universe_path != UNIVERSE_PATH:
            report_name = f"{report_name}_{universe_path.stem}"
        report_path = DATA_DIR / f"{report_name}.json"
        with open(report_path, "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
        print(f"  Report saved: {report_path}")

    elapsed = time.time() - start_time
    print(f"\nTotal time: {elapsed:.0f}s")


if __name__ == "__main__":
    main()
