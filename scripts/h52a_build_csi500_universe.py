#!/usr/bin/env python3
"""H52a — CSI500 PIT Universe History from Tushare index_weight.

Axis-flip ingestion: query index_weight by index_code='000905.SH' and trade_date
(one snapshot per month-end), NOT per-ticker.  ~90 API calls total at 5 calls/sec
hard cap → ~18 seconds pure network.  Resumable per-snapshot cache.

Outputs:
  data/cn_pit/universe_h52a_csi500.jsonl           — membership-interval rows
  data/cn_pit/universe_snapshots_h52a_csi500.jsonl  — per-snapshot rows
  data/cn_pit/universe_coverage_h52a.json           — provenance + counts
  reports/h52a_csi500_universe_report.md            — human-readable report
  data/cn_pit/raw/h52a_tushare_index_weight/<date>.csv — raw per-snapshot cache
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ═══════════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════════
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data/cn_pit"
RAW_DIR = DATA_DIR / "raw/h52a_tushare_index_weight"
REPORTS_DIR = ROOT / "reports"

DEFAULT_OUTPUT_UNIVERSE = DATA_DIR / "universe_h52a_csi500.jsonl"
DEFAULT_OUTPUT_SNAPS = DATA_DIR / "universe_snapshots_h52a_csi500.jsonl"
DEFAULT_OUTPUT_COVERAGE = DATA_DIR / "universe_coverage_h52a.json"
DEFAULT_OUTPUT_REPORT = REPORTS_DIR / "h52a_csi500_universe_report.md"

INDEX_CODE = "000905.SH"
PROVIDER_LABEL = "tushare:index_weight"
SOURCE_URL = "https://tushare.pro/document/2?doc_id=96"
DEFAULT_START = "2019-01-01"
DEFAULT_END = "2026-05-21"
NOW_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
NOW_ISO = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

# Data quality thresholds
MIN_MEMBERS_PER_SNAPSHOT = 480  # ~4% tolerance below nominal 500
RATE_LIMIT_CALLS_PER_SEC = 5
MAX_RETRIES_PER_SNAPSHOT = 5
BACKOFF_INITIAL = 2.0
BACKOFF_CAP = 60.0


# ═══════════════════════════════════════════════════════════════════════════
# Token resolution  (same chain as h50a/h51a)
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
    # Fallback: ingest_cn_pit_data._get_tushare_token (checks config.yaml)
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
    # Tushare built-in
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
# Helpers
# ═══════════════════════════════════════════════════════════════════════════
def compact_date(d: str) -> str:
    return d.replace("-", "")


def dashed_date(d: str) -> str:
    """Normalise a date string to YYYY-MM-DD."""
    d = d.strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def code_from_ticker(ticker: str) -> str:
    """Strip exchange suffix: 000001.SZ → 000001."""
    return ticker.replace(".SS", "").replace(".SZ", "")


def ticker_from_ts_code(ts_code: str) -> str:
    """Convert Tushare ts_code to Yahoo ticker: 000001.SH → 000001.SS, 000001.SZ → 000001.SZ."""
    ts_code = str(ts_code).strip()
    if not ts_code:
        return ""
    if ts_code.endswith(".SH"):
        return ts_code.replace(".SH", ".SS")
    if ts_code.endswith(".SZ"):
        return ts_code
    # Already a Yahoo ticker?
    if ts_code.endswith((".SS", ".SZ")):
        return ts_code
    # Bare numeric code → guess exchange
    if ts_code.startswith(("5", "6", "9")):
        return f"{ts_code}.SS"
    return f"{ts_code}.SZ"


def parse_iso_date(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════════
# Rate limiter
# ═══════════════════════════════════════════════════════════════════════════
class RateLimiter:
    def __init__(self, calls_per_sec: float = 5.0):
        self.min_interval = 1.0 / calls_per_sec
        self._last_call: float = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════
# Trade calendar → month-end dates
# ═══════════════════════════════════════════════════════════════════════════
def fetch_trade_cal(pro, start: str, end: str) -> pd.DataFrame:
    """Fetch SSE trading calendar, return DataFrame with cal_date column."""
    df = pro.trade_cal(
        exchange="SSE",
        is_open=1,
        start_date=compact_date(start),
        end_date=compact_date(end),
    )
    return df


def month_end_trade_dates(cal_df: pd.DataFrame) -> List[str]:
    """Given trade_cal with cal_date, return last open day of each covered month."""
    cal_df = cal_df.copy()
    cal_df["cal_date"] = cal_df["cal_date"].astype(str)
    cal_df["dt"] = pd.to_datetime(cal_df["cal_date"], format="%Y%m%d")
    cal_df["year_month"] = cal_df["dt"].dt.to_period("M")
    # Last trading day of each month
    last_dates = cal_df.groupby("year_month")["dt"].max().reset_index()
    last_dates = last_dates.sort_values("dt")
    return [d.strftime("%Y-%m-%d") for d in last_dates["dt"]]


# ═══════════════════════════════════════════════════════════════════════════
# Single-snapshot fetch with retry + cache
# ═══════════════════════════════════════════════════════════════════════════
def fetch_one_snapshot(
    pro,
    trade_date: str,
    raw_dir: Path,
    rate_limiter: RateLimiter,
) -> Tuple[List[Dict], Optional[str]]:
    """Fetch index_weight for one trade_date.

    Returns (rows, error_reason).  error_reason is None on success.
    Caches raw CSV at raw_dir/<trade_date>.csv.
    """
    cache_path = raw_dir / f"{trade_date}.csv"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Cache hit
    if cache_path.exists() and cache_path.stat().st_size > 0:
        try:
            df = pd.read_csv(cache_path)
            if not df.empty:
                return df.to_dict(orient="records"), None
        except Exception:
            pass  # Corrupt cache → re-fetch

    # Fetch with retry + backoff
    ts_date = compact_date(trade_date)
    last_error = None
    for attempt in range(MAX_RETRIES_PER_SNAPSHOT):
        rate_limiter.wait()
        try:
            df = pro.index_weight(index_code=INDEX_CODE, trade_date=ts_date)
            # Cache raw response
            df.to_csv(cache_path, index=False)
            rows = df.to_dict(orient="records")
            return rows, None
        except Exception as exc:
            last_error = str(exc)
            msg_lower = last_error.lower()
            # Rate-limit / server error → retry
            if "429" in msg_lower or "timeout" in msg_lower or "busy" in msg_lower:
                wait = min(BACKOFF_INITIAL * (2 ** attempt) + random.random(), BACKOFF_CAP)
                print(f"  ⚠ trade_date={trade_date} attempt {attempt+1}/{MAX_RETRIES_PER_SNAPSHOT}: "
                      f"backing off {wait:.1f}s ({last_error})")
                time.sleep(wait)
                continue
            # Non-retryable error
            break

    return [], last_error


# ═══════════════════════════════════════════════════════════════════════════
# Snapshot normalisation
# ═══════════════════════════════════════════════════════════════════════════
def normalise_snapshot_rows(
    raw_rows: List[Dict], trade_date: str
) -> Tuple[List[Dict], List[Dict]]:
    """Convert raw Tushare rows to standard snapshot dicts.

    Returns (valid_rows, anomaly_rows).  anomaly_rows are NaN-weight rows
    that we preserve rather than silently drop.
    """
    valid: List[Dict] = []
    anomalies: List[Dict] = []

    for idx, row in enumerate(raw_rows):
        ts_code = str(row.get("con_code") or row.get("ts_code") or "")
        ticker = ticker_from_ts_code(ts_code)
        if not ticker:
            continue
        code = code_from_ticker(ticker)
        raw_weight = row.get("weight")

        # NaN weight → record as anomaly, then skip from valid set
        if raw_weight is None or (isinstance(raw_weight, float) and math.isnan(raw_weight)):
            anomalies.append({
                "ticker": ticker,
                "snapshot_date": trade_date,
                "raw_weight": None,
            })
            continue

        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            anomalies.append({
                "ticker": ticker,
                "snapshot_date": trade_date,
                "raw_weight": str(raw_weight),
            })
            continue

        valid.append({
            "index_code": INDEX_CODE,
            "con_code": ts_code,
            "code": code,
            "ticker": ticker,
            "trade_date": trade_date,
            "weight": weight,
            "source_provider": PROVIDER_LABEL,
            "source_url": SOURCE_URL,
            "ingested_at": NOW_UTC,
            "source_row": idx,
        })

    return valid, anomalies


# ═══════════════════════════════════════════════════════════════════════════
# Membership-interval reconstruction (D3)
# ═══════════════════════════════════════════════════════════════════════════
def snapshots_to_intervals_h52a(snapshots: List[Dict]) -> List[Dict]:
    """Convert monthly constituent snapshots to continuous interval rows.

    D3 spec: weight = mean across interval snapshots; ticker rejoining gets
    a NEW row with different effective_date/end_date.
    """
    if not snapshots:
        return []

    # Validate trade_date on all snapshots
    for i, s in enumerate(snapshots):
        td = s.get("trade_date", "")
        if not td:
            raise ValueError(f"snapshots_to_intervals: snapshot[{i}] missing trade_date")
        try:
            parse_iso_date(td)
        except ValueError as exc:
            raise ValueError(f"snapshots_to_intervals: snapshot[{i}] invalid trade_date={td!r}: {exc}")

    # Group snapshots by ticker, sorted by trade_date
    by_ticker: Dict[str, List[Dict]] = {}
    for s in snapshots:
        ticker = s.get("ticker", "")
        if not ticker:
            continue
        by_ticker.setdefault(ticker, []).append(s)
    for ticker in by_ticker:
        by_ticker[ticker].sort(key=lambda s: s["trade_date"])

    all_dates = sorted(set(s["trade_date"] for s in snapshots))
    intervals: List[Dict] = []

    for ticker, snaps_list in by_ticker.items():
        present_dates = set(s["trade_date"] for s in snaps_list)
        presence = [(d, d in present_dates) for d in all_dates]

        i = 0
        while i < len(presence):
            date, is_present = presence[i]
            if not is_present:
                i += 1
                continue

            # Start of an interval
            effective_date = date
            # Gather all snapshots in this continuous interval
            interval_snaps = []
            j = i
            while j < len(presence) and presence[j][1]:
                interval_date = presence[j][0]
                for s in snaps_list:
                    if s["trade_date"] == interval_date:
                        interval_snaps.append(s)
                        break
                j += 1

            # Compute mean weight across interval
            weights = [s.get("weight") for s in interval_snaps if s.get("weight") is not None]
            mean_weight = round(sum(weights) / len(weights), 6) if weights else None
            snapshot_count = len(interval_snaps)

            if j < len(presence):
                # Next snapshot date is absent → end_date = that date
                end_date = presence[j][0]
            else:
                end_date = ""  # still active

            intervals.append({
                "ticker": ticker,
                "code": code_from_ticker(ticker),
                "name": "",
                "effective_date": effective_date,
                "end_date": end_date,
                "source_url": SOURCE_URL,
                "ingested_at": NOW_UTC,
                "index_code": INDEX_CODE,
                "weight": mean_weight,
                "source_provider": PROVIDER_LABEL,
                "snapshot_count": snapshot_count,
            })
            i = j

    return intervals


# ═══════════════════════════════════════════════════════════════════════════
# Coverage + report
# ═══════════════════════════════════════════════════════════════════════════
def write_coverage_json(
    path: Path,
    snapshots: List[Dict],
    intervals: List[Dict],
    fetch_failures: List[Dict],
    anomalies: List[Dict],
    dates: List[str],
) -> Dict:
    members_per_snapshot: Dict[str, int] = {}
    for s in snapshots:
        d = s["trade_date"]
        members_per_snapshot[d] = members_per_snapshot.get(d, 0) + 1

    total_snapshots = len(dates)
    unique_tickers = sorted(set(s["ticker"] for s in snapshots))
    member_counts = list(members_per_snapshot.values())
    avg_members = round(sum(member_counts) / len(member_counts), 1) if member_counts else 0
    min_members = min(member_counts) if member_counts else 0
    snapshot_date_range = f"{dates[0]} → {dates[-1]}" if dates else "N/A"

    # Determine verdict
    verdict = "CANDIDATE_DATASET"
    block_reasons = []
    if total_snapshots < 80:
        block_reasons.append(f"total_snapshots={total_snapshots} < 80")
    if min_members < 480:
        block_reasons.append(f"min_members_per_snapshot={min_members} < 480")
    if avg_members < 490:
        block_reasons.append(f"avg_members={avg_members} < 490")
    if len(fetch_failures) > 5:
        block_reasons.append(f"fetch_failures={len(fetch_failures)} > 5")
    if len(unique_tickers) < 700:
        block_reasons.append(f"unique_tickers={len(unique_tickers)} < 700")
    # Date range check
    if dates:
        if dates[0] > "2019-01-31":
            block_reasons.append(f"start_date={dates[0]} > 2019-01-31")
        if dates[-1] < "2026-04-30":
            block_reasons.append(f"end_date={dates[-1]} < 2026-04-30")

    if block_reasons:
        verdict = "BLOCKED"

    coverage = {
        "provenance": {
            "provider": PROVIDER_LABEL,
            "index_code": INDEX_CODE,
            "endpoints_used": ["index_weight", "trade_cal"],
            "snapshot_date_range": snapshot_date_range,
            "snapshot_cadence": "monthly_last_trading_day",
            "snapshot_timestamp": NOW_UTC,
        },
        "total_snapshots": total_snapshots,
        "unique_tickers_count": len(unique_tickers),
        "membership_intervals_count": len(intervals),
        "avg_members_per_snapshot": avg_members,
        "min_members_per_snapshot": min_members,
        "fetch_failures": fetch_failures,
        "fetch_failures_count": len(fetch_failures),
        "data_quality_anomalies": anomalies,
        "data_quality_anomalies_count": len(anomalies),
        "verdict": verdict,
        "block_reasons": block_reasons,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n")
    return coverage


def write_report(
    path: Path,
    coverage: Dict,
    snapshots: List[Dict],
    intervals: List[Dict],
    dates: List[str],
) -> None:
    prov = coverage["provenance"]

    # Top-5 most consistent members (by snapshot_count in intervals)
    most_consistent = sorted(intervals, key=lambda r: r["snapshot_count"], reverse=True)[:5]

    # Top-5 most volatile membership (by interval count per ticker)
    from collections import Counter
    interval_counts = Counter(r["ticker"] for r in intervals)
    most_volatile = interval_counts.most_common(5)

    # Per-snapshot member count range
    members_per = {}
    for s in snapshots:
        d = s["trade_date"]
        members_per[d] = members_per.get(d, 0) + 1
    member_counts = list(members_per.values())

    lines = [
        "# H52a — CSI500 PIT Universe History Report",
        "",
        f"**Generated:** {NOW_ISO}",
        f"**Verdict:** {coverage['verdict']}",
        "",
        "## Provenance",
        "",
        f"- **Provider:** {prov['provider']}",
        f"- **Index code:** {prov['index_code']}",
        f"- **Snapshot cadence:** {prov['snapshot_cadence']}",
        f"- **Snapshot date range:** {prov['snapshot_date_range']}",
        f"- **Snapshot timestamp:** {prov['snapshot_timestamp']}",
        "",
        "## Coverage Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total snapshots | {coverage['total_snapshots']} |",
        f"| Snapshot date range | {prov['snapshot_date_range']} |",
        f"| Unique tickers | {coverage['unique_tickers_count']} |",
        f"| Membership intervals | {coverage['membership_intervals_count']} |",
        f"| Avg members per snapshot | {coverage['avg_members_per_snapshot']} |",
        f"| Min members per snapshot | {coverage['min_members_per_snapshot']} |",
        f"| Fetch failures | {coverage['fetch_failures_count']} |",
        f"| Data quality anomalies (NaN weights) | {coverage['data_quality_anomalies_count']} |",
        "",
        "## Design Decisions",
        "",
        "- **D1. Axis-flip:** Query by `index_code='000905.SH'` per trade_date, not per ticker.",
        "- **D2. Ticker normalisation:** Yahoo format (`.SS`/`.SZ`) for downstream backtester compatibility.",
        "- **D3. Membership intervals:** Mean weight across interval snapshots; rejoining tickers get new rows.",
        "- **D4. Data quality:** NaN weights logged as anomalies; snapshots below 480 members retried; empty responses skipped.",
        "- **D5. Rate limit:** 5 calls/sec hard cap with exponential backoff + jitter on 429.",
    ]

    if coverage["data_quality_anomalies"]:
        lines.append("")
        lines.append("## Data Quality Anomalies (NaN Weights)")
        lines.append("")
        for a in coverage["data_quality_anomalies"][:20]:
            lines.append(f"- `{a['ticker']}` @ {a['snapshot_date']}: weight=None")

    if coverage["fetch_failures"]:
        lines.append("")
        lines.append("## Fetch Failures")
        lines.append("")
        for ff in coverage["fetch_failures"]:
            lines.append(f"- {ff['snapshot_date']}: {ff['reason']}")

    lines.extend([
        "",
        "## Top 5 Most-Consistent Members",
        "",
        "| Ticker | Code | Snapshots | Weight (mean) | Interval |",
        "|--------|------|-----------|---------------|----------|",
    ])
    for r in most_consistent:
        interval = f"{r['effective_date']} → {r['end_date'] or 'present'}"
        lines.append(f"| {r['ticker']} | {r['code']} | {r['snapshot_count']} | {r['weight']:.4f} | {interval} |")

    lines.extend([
        "",
        "## Top 5 Most-Volatile Membership",
        "",
        "| Ticker | Interval Count |",
        "|--------|---------------|",
    ])
    for ticker, count in most_volatile:
        lines.append(f"| {ticker} | {count} |")

    lines.extend([
        "",
        "## Coverage Gates",
        "",
        f"| Gate | Threshold | Actual | Status |",
        f"|------|-----------|--------|--------|",
        f"| total_snapshots | ≥ 80 | {coverage['total_snapshots']} | {'✅' if coverage['total_snapshots'] >= 80 else '❌'} |",
        f"| min_members_per_snapshot | ≥ 480 | {coverage['min_members_per_snapshot']} | {'✅' if coverage['min_members_per_snapshot'] >= 480 else '❌'} |",
        f"| avg_members_per_snapshot | ≥ 490 | {coverage['avg_members_per_snapshot']} | {'✅' if coverage['avg_members_per_snapshot'] >= 490 else '❌'} |",
        f"| fetch_failures | ≤ 5 | {coverage['fetch_failures_count']} | {'✅' if coverage['fetch_failures_count'] <= 5 else '❌'} |",
        f"| unique_tickers | ≥ 700 | {coverage['unique_tickers_count']} | {'✅' if coverage['unique_tickers_count'] >= 700 else '❌'} |",
        "",
        "## Verdict",
        "",
        f"**{coverage['verdict']}**",
    ])
    if coverage.get("block_reasons"):
        lines.append("")
        lines.append("Block reasons:")
        for r in coverage["block_reasons"]:
            lines.append(f"- {r}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def write_jsonl(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════
def run(args: argparse.Namespace) -> int:
    token = get_tushare_token()
    import tushare as ts
    pro = ts.pro_api(token)

    raw_dir = Path(args.raw_dir) if args.raw_dir else RAW_DIR
    output_universe = Path(args.output_universe) if args.output_universe else DEFAULT_OUTPUT_UNIVERSE
    output_snaps = Path(args.output_snapshots) if args.output_snapshots else DEFAULT_OUTPUT_SNAPS
    output_coverage = Path(args.output_coverage) if args.output_coverage else DEFAULT_OUTPUT_COVERAGE
    output_report = Path(args.output_report) if args.output_report else DEFAULT_OUTPUT_REPORT

    start_str = args.start or DEFAULT_START
    end_str = args.end or DEFAULT_END

    rate_limiter = RateLimiter(RATE_LIMIT_CALLS_PER_SEC)

    # ── Step 1: Trade calendar → month-end dates ──
    print(f"Fetching trade calendar {start_str} → {end_str}...")
    rate_limiter.wait()
    cal_df = fetch_trade_cal(pro, start_str, end_str)
    dates = month_end_trade_dates(cal_df)
    # Filter to requested window
    start_d = dashed_date(start_str)
    end_d = dashed_date(end_str)
    dates = [d for d in dates if start_d <= d <= end_d]
    print(f"  Month-end trading days: {len(dates)} ({dates[0]} → {dates[-1]})")

    # ── Step 2: Fetch each month-end snapshot ──
    all_snapshots: List[Dict] = []
    all_anomalies: List[Dict] = []
    fetch_failures: List[Dict] = []

    for i, trade_date in enumerate(dates):
        do_print = (i % 10 == 0) or i == len(dates) - 1
        if do_print:
            print(f"  [{i+1}/{len(dates)}] {trade_date}...")

        rows, error = fetch_one_snapshot(pro, trade_date, raw_dir, rate_limiter)

        if error:
            # Empty response or rate-limit exhaustion
            fetch_failures.append({"snapshot_date": trade_date, "reason": str(error)[:200]})
            print(f"    ⚠ FAILED (retries exhausted): {error}")
            continue

        if not rows:
            fetch_failures.append({"snapshot_date": trade_date, "reason": "empty response"})
            print(f"    ⚠ empty response")
            continue

        # Normalise
        valid, anomalies = normalise_snapshot_rows(rows, trade_date)
        all_anomalies.extend(anomalies)

        # Data quality: check member count
        if len(valid) < MIN_MEMBERS_PER_SNAPSHOT:
            print(f"    ⚠ {len(valid)} members < {MIN_MEMBERS_PER_SNAPSHOT} threshold — retrying...")
            # Retry up to 2 more times
            retry_ok = False
            for retry_i in range(2):
                time.sleep(1)
                rows2, err2 = fetch_one_snapshot(pro, trade_date, raw_dir, rate_limiter)
                if err2:
                    continue
                valid2, anom2 = normalise_snapshot_rows(rows2, trade_date)
                all_anomalies.extend(anom2)
                if len(valid2) >= MIN_MEMBERS_PER_SNAPSHOT:
                    valid = valid2
                    retry_ok = True
                    print(f"    ✓ retry ok: {len(valid)} members")
                    break
            if not retry_ok:
                fetch_failures.append({
                    "snapshot_date": trade_date,
                    "reason": f"member count {len(valid)} < {MIN_MEMBERS_PER_SNAPSHOT} after retries",
                })
                print(f"    ⚠ still below threshold — logged as fetch_failure, snapshot skipped")
                continue

        all_snapshots.extend(valid)

    print(f"\n  Total snapshot rows: {len(all_snapshots)}")
    print(f"  Fetch failures: {len(fetch_failures)}")
    print(f"  Data quality anomalies (NaN weights): {len(all_anomalies)}")

    if not all_snapshots:
        print("\n❌ No valid snapshots collected — aborting.")
        return 1

    # ── Step 3: Membership-interval reconstruction ──
    print("Building membership intervals (D3)...")
    intervals = snapshots_to_intervals_h52a(all_snapshots)
    print(f"  Intervals: {len(intervals)}")

    # ── Step 4: Write outputs ──
    print(f"\nWriting outputs...")
    write_jsonl(output_universe, intervals)
    print(f"  Universe: {output_universe} ({len(intervals)} rows)")
    write_jsonl(output_snaps, all_snapshots)
    print(f"  Snapshots: {output_snaps} ({len(all_snapshots)} rows)")

    # Recompute actual snapshot dates from collected data (might diff from planned if some failed)
    actual_dates = sorted(set(s["trade_date"] for s in all_snapshots))
    coverage = write_coverage_json(
        output_coverage, all_snapshots, intervals,
        fetch_failures, all_anomalies, actual_dates,
    )
    print(f"  Coverage: {output_coverage}")

    write_report(output_report, coverage, all_snapshots, intervals, actual_dates)
    print(f"  Report: {output_report}")

    # ── Step 5: Summary ──
    print(f"\n{'='*60}")
    print(f"H52a complete")
    print(f"  Verdict: {coverage['verdict']}")
    print(f"  Snapshots: {coverage['total_snapshots']}")
    print(f"  Date range: {coverage['provenance']['snapshot_date_range']}")
    print(f"  Unique tickers: {coverage['unique_tickers_count']}")
    print(f"  Intervals: {coverage['membership_intervals_count']}")
    print(f"  Avg/min members: {coverage['avg_members_per_snapshot']} / {coverage['min_members_per_snapshot']}")
    print(f"  Fetch failures: {coverage['fetch_failures_count']}")
    print(f"  NaN anomalies: {coverage['data_quality_anomalies_count']}")

    return 0 if coverage["verdict"] == "CANDIDATE_DATASET" else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="H52a — CSI500 PIT Universe History")
    p.add_argument("--start", default=DEFAULT_START, help="Start date YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--end", default=DEFAULT_END, help="End date YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--raw-dir", default=None, help="Raw cache directory (default: data/cn_pit/raw/h52a_tushare_index_weight/)")
    p.add_argument("--output-universe", default=None, help="Universe jsonl output path")
    p.add_argument("--output-snapshots", default=None, help="Snapshots jsonl output path")
    p.add_argument("--output-coverage", default=None, help="Coverage json output path")
    p.add_argument("--output-report", default=None, help="Report md output path")
    return p


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
