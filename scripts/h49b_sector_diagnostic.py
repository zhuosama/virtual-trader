#!/usr/bin/env python3
"""H49b Sector Diagnostic — Sector Distribution + Multi-Mapped Follow-Up.

Replays the best candidate's deploy window using run_sector_aware_backtest,
captures holdings at first and last rebalance, joins to H49a SW L1, and
patches the H49b report with real numbers between HTML-comment markers.

Idempotent: second run with same inputs produces zero git diff.

Author: Hermes Agent (H49b Diagnostic Patch)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

# ── Path setup ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from fundamental_backtest import CN_PIT_FileSource, HS300_TICKER       # noqa: E402
from h49b_sector_neutral_rs_search import (                           # noqa: E402
    H49bParams, H49bOverlay, SectorFeatureCache,
    load_sector_map, run_sector_aware_backtest,
    H47_PRICES, H30_UNIVERSE, H30_SNAPSHOTS, SECTOR_CSV,
    H49A_COVERAGE, RUN_OUT, REPORT_OUT, DEFAULT_CONFIG,
    load_json,
)
from h42_strategy_redesign_search import FeatureCache, WINDOWS        # noqa: E402

# ── Constants ───────────────────────────────────────────────────────────
CAPITAL = 300000  # matches H49b CLI default
SECTOR_CAP = 0.25
SECTOR_CAP_TOLERANCE = 0.005  # 50 bps for price drift between rebalances

# ── Markers ─────────────────────────────────────────────────────────────
SECTOR_BEGIN = "<!-- h49b-diag:sector-distribution:begin -->"
SECTOR_END   = "<!-- h49b-diag:sector-distribution:end -->"
MULTI_BEGIN  = "<!-- h49b-diag:multi-mapped:begin -->"
MULTI_END    = "<!-- h49b-diag:multi-mapped:end -->"


# ═══════════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════════

def load_multi_mapped_tickers() -> Set[str]:
    """Return the set of multi-mapped ticker codes from H49a coverage JSON."""
    data = load_json(H49A_COVERAGE)
    return {entry["ticker"] for entry in data.get("multi_mapped", [])}


# ═══════════════════════════════════════════════════════════════════════
# Holdings extraction from trades
# ═══════════════════════════════════════════════════════════════════════

def get_rebalance_holdings(
    trades: List[Dict],
) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    """Extract holdings at first and last rebalance dates from trades.

    Returns (first_holdings, last_holdings) where each is
    {ticker: {shares, avg_cost, sector}}.
    """
    buy_dates = sorted(set(t["date"] for t in trades if t["action"] == "buy"))
    if not buy_dates:
        return {}, {}

    first_reb_date = buy_dates[0]
    last_reb_date = buy_dates[-1]

    def holdings_at(date_str: str) -> Dict[str, Dict]:
        holdings: Dict[str, Dict] = {}
        for t in trades:
            if t["date"] > date_str:
                break
            ticker = t["ticker"]
            if t["action"] == "buy":
                holdings[ticker] = {
                    "shares": t["shares"],
                    "avg_cost": t["price"],
                    "sector": t.get("sector", "UNKNOWN"),
                }
            elif t["action"] == "sell" and ticker in holdings:
                del holdings[ticker]
        return holdings

    return holdings_at(first_reb_date), holdings_at(last_reb_date)


def get_all_time_holdings(trades: List[Dict]) -> Set[str]:
    """Return the union of all tickers ever held during deploy window."""
    return {t["ticker"] for t in trades if t["action"] == "buy"}


# ═══════════════════════════════════════════════════════════════════════
# Sector distribution computation
# ═══════════════════════════════════════════════════════════════════════

def compute_sector_table(
    holdings: Dict[str, Dict],
    sector_map: Dict[str, str],
) -> Tuple[List[Dict], bool, int]:
    """Build sorted sector table, cap-held check, sector count.

    Returns (rows, cap_held, sector_count).
    rows: list of {sector, count, total_weight_pct, max_weight_pct} sorted desc.
    cap_held: True iff max_weight <= SECTOR_CAP + TOLERANCE for every sector.
    """
    if not holdings:
        return [], False, 0

    total_value = sum(h["shares"] * h["avg_cost"] for h in holdings.values())
    if total_value <= 0:
        return [], False, 0

    sectors: Dict[str, Dict] = defaultdict(
        lambda: {"count": 0, "total_weight": 0.0, "max_weight": 0.0})

    for ticker, h in holdings.items():
        weight = (h["shares"] * h["avg_cost"]) / total_value
        sect_name = sector_map.get(ticker, "UNMAPPED")
        s = sectors[sect_name]
        s["count"] += 1
        s["total_weight"] += weight
        s["max_weight"] = max(s["max_weight"], weight)

    cap_held = True
    for s in sectors.values():
        if s["max_weight"] > SECTOR_CAP + SECTOR_CAP_TOLERANCE:
            cap_held = False

    rows = []
    for sect_name, s in sorted(sectors.items(),
                               key=lambda x: -x[1]["total_weight"]):
        rows.append({
            "sector": sect_name,
            "count": s["count"],
            "total_weight_pct": s["total_weight"] * 100,
            "max_weight_pct": s["max_weight"] * 100,
        })

    return rows, cap_held, len(sectors)


# ═══════════════════════════════════════════════════════════════════════
# Report body builders
# ═══════════════════════════════════════════════════════════════════════

def build_sector_section(
    first_rows: List[Dict], first_cap: bool, first_count: int,
    last_rows:  List[Dict], last_cap:  bool, last_count:  int,
) -> str:
    """Build the Sector Distribution (Best Candidate) section body."""
    lines = []

    lines.append("### Holdings at Deploy Start (First Rebalance)")
    lines.append("")
    lines.append("| Sector | Count | Total Weight | Max Weight |")
    lines.append("|--------|-------|-------------|------------|")
    for r in first_rows:
        lines.append(f"| {r['sector']} | {r['count']} | "
                     f"{r['total_weight_pct']:.1f}% | {r['max_weight_pct']:.1f}% |")
    lines.append("")

    lines.append("### Holdings at Deploy End (Last Rebalance)")
    lines.append("")
    lines.append("| Sector | Count | Total Weight | Max Weight |")
    lines.append("|--------|-------|-------------|------------|")
    for r in last_rows:
        lines.append(f"| {r['sector']} | {r['count']} | "
                     f"{r['total_weight_pct']:.1f}% | {r['max_weight_pct']:.1f}% |")
    lines.append("")

    cap_pass = first_cap and last_cap
    lines.append(f"**Cap-held check: {'PASS' if cap_pass else 'FAIL'}** "
                 f"(sector_max_weight_pct={SECTOR_CAP}, tolerance=±{SECTOR_CAP_TOLERANCE})")
    if not cap_pass:
        violations = []
        if not first_cap:
            violations.append("start")
        if not last_cap:
            violations.append("end")
        lines.append(f"Cap violation at: {', '.join(violations)}.")
    lines.append("")

    lines.append(f"**Sector count:** start={first_count}, end={last_count} "
                 f"(min_sectors_in_portfolio=7)")
    if first_count >= 7 and last_count >= 7:
        lines.append("Sector count requirement met at both snapshots.")
    else:
        parts = []
        if first_count < 7:
            parts.append("start")
        if last_count < 7:
            parts.append("end")
        lines.append(f"⚠ Sector count below minimum at: {' and '.join(parts)}.")

    return "\n".join(lines)


def build_multi_mapped_section(
    holdings_count: int,
    intersection_count: int,
    intersection_pct: float,
    intersecting_tickers: Set[str],
    sector_map: Dict[str, str],
) -> str:
    """Build the Multi-Mapped Follow-Up section body."""
    lines = []

    lines.append(f"- **All-time deploy holdings (unique tickers):** {holdings_count}")
    lines.append(f"- **H49a multi-mapped tickers in holdings:** {intersection_count}")
    lines.append(f"- **Intersection percentage:** {intersection_pct:.1f}%")
    lines.append("")

    if intersection_pct > 30:
        lines.append("**FOLLOW-UP REQUIRED** — intersection exceeds 30% threshold.")
        lines.append("")
        lines.append("| Ticker | Primary SW L1 Industry |")
        lines.append("|--------|------------------------|")
        for ticker in sorted(intersecting_tickers):
            industry = sector_map.get(ticker, "UNMAPPED")
            lines.append(f"| {ticker} | {industry} |")
    else:
        lines.append("**OK — multi-mapped contamination below threshold.**")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Report patching
# ═══════════════════════════════════════════════════════════════════════

def _replace_between_markers(
    text: str,
    begin_marker: str,
    end_marker: str,
    new_body: str,
) -> str:
    """Replace content between two HTML-comment markers with new_body.

    Raises RuntimeError if markers are not found.
    Returns text unchanged if replacement would be identical (idempotent).
    """
    if begin_marker not in text or end_marker not in text:
        raise RuntimeError(
            f"Markers {begin_marker} … {end_marker} not found in report. "
            "Run without --dry-run first to insert markers."
        )
    pattern = re.escape(begin_marker) + r".*?" + re.escape(end_marker)
    replacement = begin_marker + "\n" + new_body + "\n" + end_marker
    result = re.sub(pattern, replacement, text, flags=re.DOTALL)
    return result


def insert_markers_first_time(report_path: Path) -> bool:
    """Insert HTML-comment markers around the two placeholder sections.

    Only runs if markers are absent. Returns True if markers were inserted.
    """
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    if SECTOR_BEGIN in content and MULTI_BEGIN in content:
        return False

    modified = False

    # ── Sector Distribution section ──────────────────────────────────
    if SECTOR_BEGIN not in content:
        # Find "## Sector Distribution (Best Candidate)\n\n...\n\n## ..."
        pattern = (
            r"(## Sector Distribution \(Best Candidate\)\n\n)"
            r"(.*?)"
            r"(\n## )"
        )
        match = re.search(pattern, content, re.DOTALL)
        if match:
            new_block = (
                match.group(1)
                + SECTOR_BEGIN + "\n"
                + match.group(2).strip() + "\n"
                + SECTOR_END + "\n\n"
                + match.group(3)
            )
            content = content[:match.start()] + new_block + content[match.end():]
            modified = True

    # ── Multi-Mapped Follow-Up section ───────────────────────────────
    if MULTI_BEGIN not in content:
        pattern = (
            r"(## Multi-Mapped Follow-Up\n\n)"
            r"(.*?)"
            r"(\n## )"
        )
        match = re.search(pattern, content, re.DOTALL)
        if match:
            new_block = (
                match.group(1)
                + MULTI_BEGIN + "\n"
                + match.group(2).strip() + "\n"
                + MULTI_END + "\n\n"
                + match.group(3)
            )
            content = content[:match.start()] + new_block + content[match.end():]
            modified = True

    if modified:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)

    return modified


def patch_report(report_path: Path, sector_body: str, multi_body: str) -> None:
    """Replace marker-delimited bodies in the H49b report."""
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    content = _replace_between_markers(content, SECTOR_BEGIN, SECTOR_END, sector_body)
    content = _replace_between_markers(content, MULTI_BEGIN, MULTI_END, multi_body)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)


# ═══════════════════════════════════════════════════════════════════════
# Sync file update
# ═══════════════════════════════════════════════════════════════════════

def append_closure_note(cap_pass: bool, intersection_pct: float) -> None:
    """Append a one-line closure note to docs/strategy-optimization-sync.md
    under the H49b (S18) row. Idempotent: skips if line already present.
    """
    sync_path = PROJECT_ROOT / "docs" / "strategy-optimization-sync.md"
    if not sync_path.exists():
        print(f"  (sync file not found: {sync_path})")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    closure = (
        f"H49b report sections backfilled by `scripts/h49b_sector_diagnostic.py` "
        f"on {today}; cap held {'PASS' if cap_pass else 'FAIL'}; "
        f"multi-mapped intersection {intersection_pct:.1f}%."
    )

    with open(sync_path, "r", encoding="utf-8") as f:
        content = f.read()

    if closure in content:
        print("  (closure note already present, skipping)")
        return

    # Append as a new line after the last content
    content = content.rstrip() + "\n" + closure + "\n"
    with open(sync_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Appended closure note to {sync_path}")


# ═══════════════════════════════════════════════════════════════════════
# Core logic
# ═══════════════════════════════════════════════════════════════════════

def run_diagnostic(dry_run: bool, output_path: str | None = None) -> Dict:
    """Run the full diagnostic: replay, compute, patch.

    Returns a dict with summary values for the final response.
    """
    # ── 1. Load H49b run data ─────────────────────────────────────────
    run_data = load_json(RUN_OUT)
    best = run_data["top_candidates_multi_window"][0]

    # ── 2. Build params and overlay from deploy_window ─────────────────
    deploy_params = best["deploy_window"]["params"]
    deploy_overlay = best["deploy_window"]["overlay"]
    params = H49bParams(**deploy_params)
    overlay = H49bOverlay(**deploy_overlay)

    # ── 3. Build data source ──────────────────────────────────────────
    source = CN_PIT_FileSource(
        prices_path=str(H47_PRICES),
        universe_path=str(H30_UNIVERSE),
        universe_snapshots_path=str(H30_SNAPSHOTS),
    )

    # ── 4. Deploy window ──────────────────────────────────────────────
    wstart, wend = WINDOWS["deploy_2025_2026"]

    # ── 5. Load sector data ───────────────────────────────────────────
    sector_map = load_sector_map(SECTOR_CSV)
    multi_mapped_tickers = load_multi_mapped_tickers()

    # ── 6. Build feature cache and replay ─────────────────────────────
    config = load_json(DEFAULT_CONFIG)
    universe = source.get_price_universe(wstart, wend)
    prices = source.get_price_history(
        list(universe) + [HS300_TICKER], wstart, wend
    )
    fc = FeatureCache(prices, HS300_TICKER)
    sfc = SectorFeatureCache(fc, sector_map)

    print(f"Running sector-aware backtest: {overlay.name}")
    print(f"  Params: N={params.top_n}, maxpos={params.max_position_pct}, "
          f"SL={params.stop_loss_pct}, TP={params.take_profit_pct}, "
          f"QF={params.quality_filter}, Rebal={params.rebalance_freq_days}, "
          f"Cap={params.sector_max_weight_pct}, MinS={params.min_sectors_in_portfolio}")

    result = run_sector_aware_backtest(
        source, prices, sfc, wstart, wend,
        CAPITAL, params, overlay, config,
        return_details=True,
    )

    trades = result["trades"]
    n_buys = len([t for t in trades if t["action"] == "buy"])
    n_sells = len([t for t in trades if t["action"] == "sell"])
    print(f"  Trades: {len(trades)} total ({n_buys} buys, {n_sells} sells)")

    # ── 7. Holdings at first/last rebalance ───────────────────────────
    first_holdings, last_holdings = get_rebalance_holdings(trades)

    if not first_holdings:
        print("ERROR: No buy trades found — cannot compute sector distribution.")
        sys.exit(1)

    print(f"  First rebalance: {len(first_holdings)} positions")
    print(f"  Last rebalance:  {len(last_holdings)} positions")

    # ── 8. Sector Distribution ────────────────────────────────────────
    first_rows, first_cap, first_count = compute_sector_table(
        first_holdings, sector_map)
    last_rows, last_cap, last_count = compute_sector_table(
        last_holdings, sector_map)

    cap_pass = first_cap and last_cap
    print(f"  Cap-held: {'PASS' if cap_pass else 'FAIL'} "
          f"(start={'PASS' if first_cap else 'FAIL'}, "
          f"end={'PASS' if last_cap else 'FAIL'})")
    print(f"  Sector count: start={first_count}, end={last_count}")

    sector_body = build_sector_section(
        first_rows, first_cap, first_count,
        last_rows, last_cap, last_count,
    )

    # ── 9. Multi-Mapped Follow-Up ────────────────────────────────────
    all_holdings = get_all_time_holdings(trades)
    intersecting = all_holdings & multi_mapped_tickers

    holdings_count = len(all_holdings)
    intersection_count = len(intersecting)
    intersection_pct = (
        intersection_count / holdings_count * 100 if holdings_count > 0 else 0
    )

    print(f"  Multi-mapped: holdings={holdings_count}, "
          f"intersection={intersection_count}, pct={intersection_pct:.1f}%")

    multi_body = build_multi_mapped_section(
        holdings_count, intersection_count, intersection_pct,
        intersecting, sector_map,
    )

    # ── 10. Patch report ──────────────────────────────────────────────
    if dry_run:
        import shutil
        target = Path(output_path or "/tmp/h49b_report_smoke.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPORT_OUT, target)

        inserted = insert_markers_first_time(target)
        if inserted:
            print(f"  Inserted markers into {target}")

        patch_report(target, sector_body, multi_body)
        print(f"  Patched report written to {target}")
    else:
        report_path = REPORT_OUT
        inserted = insert_markers_first_time(report_path)
        if inserted:
            print("  Inserted HTML-comment markers into report (first run)")

        patch_report(report_path, sector_body, multi_body)
        print(f"  Patched {report_path}")

        # Append closure note to sync file
        append_closure_note(cap_pass, intersection_pct)

    return {
        "cap_pass": cap_pass,
        "first_cap": first_cap,
        "last_cap": last_cap,
        "first_count": first_count,
        "last_count": last_count,
        "holdings_count": holdings_count,
        "intersection_count": intersection_count,
        "intersection_pct": intersection_pct,
        "followup_required": intersection_pct > 30,
    }


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="H49b Sector Diagnostic")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Write output to --output-report instead of editing in place",
    )
    parser.add_argument(
        "--output-report", type=str, default="/tmp/h49b_report_smoke.md",
        help="Output path for --dry-run (default: /tmp/h49b_report_smoke.md)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("H49b Sector Diagnostic")
    print("=" * 60)

    summary = run_diagnostic(
        dry_run=args.dry_run,
        output_path=args.output_report if args.dry_run else None,
    )

    print()
    print("=" * 60)
    print("Diagnostic complete.")
    print(f"  Cap-held: {'PASS' if summary['cap_pass'] else 'FAIL'}")
    print(f"  Sector count: start={summary['first_count']}, "
          f"end={summary['last_count']}")
    print(f"  Multi-mapped intersection: "
          f"{summary['intersection_count']}/{summary['holdings_count']} "
          f"({summary['intersection_pct']:.1f}%)")
    print(f"  {'FOLLOW-UP REQUIRED' if summary['followup_required'] else 'OK — below threshold'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
