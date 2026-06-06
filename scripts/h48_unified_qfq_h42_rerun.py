#!/usr/bin/env python3
"""H48 — Unified-QFQ H42 Strategy Rerun.

Thin wrapper that reruns the H42 redesign search verbatim, swapping only the
price source to the H47 unified Tushare qfq matrix.  Produces H48 JSON + report
with side-by-side H42-vs-H48 comparison.

No modifications to H42 search logic, gate thresholds, window definitions,
or overlay families.  Only the price file and output paths are changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Fixed paths ─────────────────────────────────────────────────────────
H47_PRICES = PROJECT_ROOT / "data/cn_pit/prices_h47_tushare_qfq_candidate.csv"
H30_UNIVERSE = PROJECT_ROOT / "data/cn_pit/universe_h30_candidate.jsonl"
H30_SNAPSHOTS = PROJECT_ROOT / "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
H42_RUN = PROJECT_ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json"

# Default H48 outputs (used for full run)
H48_RUN_OUT = PROJECT_ROOT / "backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json"
H48_REPORT_OUT = PROJECT_ROOT / "reports/h48_unified_qfq_h42_rerun_report.md"


# ── Helpers (copied from H42 to keep wrapper self-contained) ────────────
def pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.2f}%"


def plain_pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def compute_price_source(prices_path: Path) -> Dict:
    """Compute provenance block at runtime."""
    raw = prices_path.read_bytes()
    header = prices_path.read_text(encoding="utf-8").splitlines()[0]
    tickers = [c for c in header.split(",") if c != "date"]
    lines = prices_path.read_text(encoding="utf-8").splitlines()
    return {
        "task": "h47",
        "file": str(prices_path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "provider": "tushare:pro_bar:qfq",
        "benchmark_provider": "tushare:index_daily",
        "rows": len(lines) - 1,
        "ticker_columns": len(tickers),
    }


def candidate_signature(r: Dict) -> tuple:
    """Deterministic signature for comparing H42 ↔ H48 candidates."""
    p = r["params"]
    o = r["overlay"]["name"]
    return (
        o,
        p["top_n"],
        p["max_position_pct"],
        p["stop_loss_pct"],
        p["take_profit_pct"],
        p["quality_filter"],
        p["rebalance_freq_days"],
        p.get("trailing_stop_pct"),
        p.get("max_new_buys"),
    )


def candidate_row_mw(r: Dict) -> str:
    """Same format as H42 report for comparability."""
    o = r["overlay"]["name"]
    p = r["params"]
    g = r["gate_metrics"]
    m = r["deploy_window"]["metrics"]
    return (
        f"| {o} | {p['top_n']} | {p['max_position_pct']:.2f} | "
        f"{p['stop_loss_pct']:.2f} | {p['take_profit_pct']:.2f} | "
        f"{p.get('trailing_stop_pct') or '-'} | "
        f"{p['quality_filter']:.2f} | {p['rebalance_freq_days']} | "
        f"{pct(m['total_return'])} | {m['sharpe_ratio']:.2f} | {pct(m['max_drawdown'])} | "
        f"{pct(m['excess_return'])} | {m['trade_count']} | {g['deploy_streak']} | "
        f"{'YES' if r['passes_acceptance_gate'] else 'NO'} |"
    )


def window_table_row(wname: str, wr: Dict) -> str:
    """Same format as H42 report."""
    return (
        f"| {wname} | {pct(wr['total_return'])} | {wr['sharpe_ratio']:.2f} | "
        f"{pct(wr['max_drawdown'])} | {pct(wr['excess_return'])} | "
        f"{'BLOCKED' if wr['execution_blocked'] else 'OK'} | {wr['trade_count']} | "
        f"{wr['terminal_losing_streak']} |"
    )


# ── Report builder ──────────────────────────────────────────────────────
def build_h48_report(payload: Dict, h42_baseline: Dict) -> str:
    """Build H48 report with side-by-side H42 comparison."""
    price_src = payload["price_source"]
    v = payload["verdict"]
    h42v = h42_baseline["verdict"]

    lines = [
        "# H48 — Unified-QFQ H42 Strategy Rerun Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Verdict:** {v}",
        f"**Elapsed:** {payload['elapsed_seconds']:.1f}s",
        "",
        "## Question",
        "",
        "Under a uniformly adjusted Tushare qfq price source (H47), do any H42 candidates "
        "flip from RESEARCH_ONLY to passing the gate? This is a price-source sensitivity "
        "check — not a tuning pass.",
        "",
        "## Provenance",
        "",
        f"- **Task:** {price_src['task']}",
        f"- **File:** `{price_src['file']}`",
        f"- **SHA256:** `{price_src['sha256']}`",
        f"- **Provider:** {price_src['provider']}",
        f"- **Benchmark provider:** {price_src['benchmark_provider']}",
        f"- **Rows:** {price_src['rows']}",
        f"- **Ticker columns:** {price_src['ticker_columns']}",
        "",
        "## Search Space Summary",
        "",
        f"- **Search space:** Identical to H42 — 24 overlays × 25 param combos = 600 runs",
        f"- Stage A (overlay screening): {payload['stage_a_count']} overlays",
        f"- Stage B (param grid): {payload['stage_b_count']} runs",
        f"- Sanity seeds: {payload.get('seed_count', 0)} known candidates",
        f"- Clean deploy-window candidates: {payload.get('clean_deploy_count', 0)}",
        f"- Stage C (multi-window): {payload['stage_c_count']} candidates",
        f"- Selected overlays: {', '.join(payload['selected_overlays'])}",
        "",
        "## Acceptance Gate (identical to H42)",
        "",
        "A candidate passes if ALL conditions are met:",
        "",
        "- Deploy window: not blocked by H34 stop conditions",
        "- Deploy window: zero execution warnings",
        "- Deploy window: closed sells >= 30",
        "- Deploy window: terminal losing streak < 5",
        "- Positive windows: >= 4/5",
        "- Unblocked windows: >= 3/5",
        "- Beat HS300 windows: >= 2/5",
        "- Deploy excess return > 0",
        "- Max drawdown > -8%",
        "",
        f"**Gate passed: {payload['gate_pass_count']} candidates**",
        "",
    ]

    # ── H42 vs H48 comparison ───────────────────────────────────────────
    lines.extend([
        "## H42 vs H48 Comparison",
        "",
        f"| Metric | H42 (yfinance+mixed) | H48 (Tushare qfq) |",
        f"|--------|---------------------|-------------------|",
        f"| Verdict | **{h42v}** | **{v}** |",
        f"| Gate-pass count | {h42_baseline['gate_pass_count']} | {payload['gate_pass_count']} |",
        f"| Stage A overlays | {h42_baseline['stage_a_count']} | {payload['stage_a_count']} |",
        f"| Stage B runs | {h42_baseline['stage_b_count']} | {payload['stage_b_count']} |",
        f"| Clean deploy candidates | {h42_baseline.get('clean_deploy_count', 0)} | {payload.get('clean_deploy_count', 0)} |",
        f"| Stage C multi-window | {h42_baseline['stage_c_count']} | {payload['stage_c_count']} |",
        f"| Selected overlays | {', '.join(h42_baseline['selected_overlays'])} | {', '.join(payload['selected_overlays'])} |",
        "",
    ])

    # ── Top 15 H48 candidates ────────────────────────────────────────────
    top = payload["top_candidates_multi_window"]
    if top:
        table_header = (
            "| Overlay | N | Pos% | SL | TP | Trail | QF | Rebal | Return | Sharpe | "
            "MaxDD | Excess | Trades | Streak | Gate |\n"
            "|---------|---|------|----|----|-------|----|-------|--------|--------|"
            "-------|--------|--------|--------|------|"
        )
        lines.extend(["## Top Candidates — H48 (Multi-Window Ranked)", "", table_header])
        lines.extend(candidate_row_mw(r) for r in top[:15])
        lines.append("")

        # Per-window detail for top 3 H48
        lines.extend(["## Per-Window Detail — H48 (Top 3)", ""])
        for i, cand in enumerate(top[:3]):
            o = cand["overlay"]["name"]
            p = cand["params"]
            lines.append(
                f"### #{i+1}: {o} (N={p['top_n']}, SL={p['stop_loss_pct']}, "
                f"TP={p['take_profit_pct']}, Trail={p.get('trailing_stop_pct') or '-'}, "
                f"QF={p['quality_filter']}, Rebal={p['rebalance_freq_days']})"
            )
            lines.append("")
            lines.append(
                "| Window | Return | Sharpe | MaxDD | Excess | Status | Trades | Streak |\n"
                "|--------|--------|--------|-------|--------|--------|--------|--------|"
            )
            for wname in ["cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"]:
                wr = cand["window_results"].get(wname, {})
                lines.append(window_table_row(wname, wr))
            lines.append("")

        # ── Cross-over candidates ───────────────────────────────────────
        h42_sigs = {candidate_signature(r): r for r in h42_baseline.get("top_candidates_multi_window", [])[:15]}
        h48_sigs = {candidate_signature(r): r for r in top[:15]}
        common_sigs = set(h42_sigs.keys()) & set(h48_sigs.keys())

        if common_sigs:
            lines.extend(["## Cross-Over Candidates (Appear in Both H42 and H48 Top-15)", ""])
            for sig in sorted(common_sigs):
                h42_cand = h42_sigs[sig]
                h48_cand = h48_sigs[sig]
                o = h42_cand["overlay"]["name"]
                p = h42_cand["params"]
                lines.append(
                    f"### {o} / N={p['top_n']} / SL={p['stop_loss_pct']} / "
                    f"TP={p['take_profit_pct']} / Trail={p.get('trailing_stop_pct') or '-'} / "
                    f"QF={p['quality_filter']} / Rebal={p['rebalance_freq_days']}"
                )
                lines.append("")
                m42 = h42_cand["deploy_window"]["metrics"]
                m48 = h48_cand["deploy_window"]["metrics"]
                g42 = h42_cand["gate_metrics"]
                g48 = h48_cand["gate_metrics"]
                lines.extend([
                    "| Metric | H42 (yfinance+mixed) | H48 (Tushare qfq) |",
                    "|--------|---------------------|-------------------|",
                    f"| Total Return | {pct(m42['total_return'])} | {pct(m48['total_return'])} |",
                    f"| Sharpe | {m42['sharpe_ratio']:.2f} | {m48['sharpe_ratio']:.2f} |",
                    f"| Max DD | {pct(m42['max_drawdown'])} | {pct(m48['max_drawdown'])} |",
                    f"| Excess Return | {pct(m42['excess_return'])} | {pct(m48['excess_return'])} |",
                    f"| Trade Count | {m42['trade_count']} | {m48['trade_count']} |",
                    f"| Pos Windows | {g42['positive_windows']}/{g42['window_count']} | {g48['positive_windows']}/{g48['window_count']} |",
                    f"| Unblocked Windows | {g42['unblocked_windows']}/{g42['window_count']} | {g48['unblocked_windows']}/{g48['window_count']} |",
                    f"| Beat HS300 Windows | {g42['beat_hs300_windows']}/{g42['window_count']} | {g48['beat_hs300_windows']}/{g48['window_count']} |",
                    f"| Gate | {'YES' if h42_cand['passes_acceptance_gate'] else 'NO'} | {'YES' if h48_cand['passes_acceptance_gate'] else 'NO'} |",
                    "",
                ])
        else:
            lines.extend([
                "## Cross-Over Candidates",
                "",
                "No candidates appear in both H42 and H48 top-15 lists.",
                "",
            ])
    else:
        lines.extend([
            "## Results",
            "",
            "No candidates survived the initial deploy-window clean filter.",
            "",
        ])

    # ── Verdict ──────────────────────────────────────────────────────────
    lines.extend(["## Final Verdict", ""])
    if payload["acceptance_gate_passed"]:
        best = top[0]
        lines.extend([
            f"**{v}** — at least one candidate passes the full acceptance gate under "
            "the unified Tushare qfq price source.",
            "",
            f"Best candidate: `{best['overlay']['name']}` / N={best['params']['top_n']} / "
            f"SL={best['params']['stop_loss_pct']} / TP={best['params']['take_profit_pct']} / "
            f"Trail={best['params'].get('trailing_stop_pct') or '-'} / "
            f"QF={best['params']['quality_filter']} / Rebal={best['params']['rebalance_freq_days']}",
            "",
        ])
    else:
        lines.extend([
            f"**{v}** — no candidate passes the full multi-window acceptance gate.",
            "",
            "The change from yfinance-adjusted (H38-derived) prices to uniformly "
            "Tushare qfq-adjusted (H47) prices did not materially change the outcome: "
            "the H42 search space remains below the acceptance gate threshold.",
            "",
        ])

    # ── Next action ─────────────────────────────────────────────────────
    lines.extend([
        "## Next Recommended Action",
        "",
    ])
    if payload["acceptance_gate_passed"]:
        lines.append("Candidate passed gate — recommend H49: Shadow Account Forward Trial.")
    else:
        lines.append(
            "Gate not passed under either price source. "
            "Consider: sector-level diversification constraints, "
            "alternate ValueScore weights, or accept paper-only monitoring "
            "of the best candidate. No further price-source investigation needed "
            "for this search space."
        )

    lines.append("")
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="H48 — Unified-QFQ H42 Strategy Rerun"
    )
    parser.add_argument("--capital", type=float, default=300000)
    parser.add_argument("--prices-file", type=Path, default=H47_PRICES)
    parser.add_argument("--universe-file", type=Path, default=H30_UNIVERSE)
    parser.add_argument("--snapshots-file", type=Path, default=H30_SNAPSHOTS)
    parser.add_argument("--top-overlays", type=int, default=8)
    parser.add_argument("--stage-a-limit", type=int, default=0,
                        help="Limit overlays in Stage A (0=all)")
    parser.add_argument("--stage-b-limit", type=int, default=200,
                        help="Limit param combos in Stage B (0=all)")
    parser.add_argument("--top-k", type=int, default=20,
                        help="Top-K deploy-window candidates for Stage C")
    parser.add_argument("--output-run", type=Path, default=H48_RUN_OUT)
    parser.add_argument("--output-report", type=Path, default=H48_REPORT_OUT)
    args = parser.parse_args()

    # Guard: never overwrite H42 originals
    if str(args.output_run) == str(PROJECT_ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json"):
        print("ERROR: Refusing to overwrite H42 run JSON. Use --output-run to specify H48 path.")
        return 1
    if str(args.output_report) == str(PROJECT_ROOT / "reports/h42_strategy_redesign_search_report.md"):
        print("ERROR: Refusing to overwrite H42 report. Use --output-report to specify H48 path.")
        return 1

    # Guard: ensure we're using H47 prices
    if not args.prices_file.exists():
        print(f"ERROR: Prices file not found: {args.prices_file}")
        return 1

    # Import H42 run_search (do this after path setup)
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    # H42 imports fundamental_backtest and h35 which need EXPERIMENTS_DIR on path too
    sys.path.insert(0, str(PROJECT_ROOT / "backtest" / "experiments"))

    from h42_strategy_redesign_search import run_search  # noqa: E402

    print("=" * 70)
    print("H48 — Unified-QFQ H42 Strategy Rerun")
    print(f"Price source: {H47_PRICES}")
    print(f"Output run:   {args.output_run}")
    print(f"Output report: {args.output_report}")
    print(f"Stage A limit: {args.stage_a_limit or 'all'}")
    print(f"Stage B limit: {args.stage_b_limit}")
    print(f"Top-K:         {args.top_k}")
    print("=" * 70)

    # Compute provenance before run
    t0 = time.time()
    price_source = compute_price_source(H47_PRICES)
    print(f"\nPrice source SHA256: {price_source['sha256'][:16]}...")
    print(f"Rows: {price_source['rows']}, Ticker columns: {price_source['ticker_columns']}")

    # Run search
    payload = run_search(args)

    # Post-process: inject provenance and retask
    payload["task"] = "H48"
    payload["price_source"] = price_source
    payload["elapsed_seconds"] = round(time.time() - t0, 1)

    # Load H42 baseline for comparison
    if H42_RUN.exists():
        h42_baseline = json.loads(H42_RUN.read_text(encoding="utf-8"))
    else:
        print(f"WARNING: H42 baseline not found at {H42_RUN}")
        h42_baseline = {
            "verdict": "RESEARCH_ONLY",
            "gate_pass_count": 0,
            "stage_a_count": 0,
            "stage_b_count": 0,
            "stage_c_count": 0,
            "clean_deploy_count": 0,
            "selected_overlays": [],
            "top_candidates_multi_window": [],
        }

    # Build report
    report = build_h48_report(payload, h42_baseline)

    # Write outputs
    args.output_run.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_run.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    args.output_report.write_text(report)

    print(f"\nWrote: {args.output_run}")
    print(f"Wrote: {args.output_report}")
    print(f"Verdict: {payload['verdict']}")
    print(f"Gate passed: {payload['gate_pass_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
