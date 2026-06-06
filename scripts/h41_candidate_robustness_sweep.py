#!/usr/bin/env python3
"""H41 — robustness sweep over H39 top candidates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from fundamental_backtest import CN_PIT_FileSource, HS300_TICKER  # noqa: E402
from h39_shadow_unblock_search import (  # noqa: E402
    DEFAULT_CAPITAL,
    DEFAULT_CONFIG,
    DEFAULT_PRICES,
    DEFAULT_SNAPSHOTS,
    DEFAULT_UNIVERSE,
    Overlay,
    Params,
    load_json,
    pct,
    run_overlay_backtest,
)

H39_JSON = PROJECT_ROOT / "backtest/runs/fundamental_value_h39_unblock_search.json"
OUTPUT = PROJECT_ROOT / "backtest/runs/fundamental_value_h41_candidate_robustness_sweep.json"
REPORT = PROJECT_ROOT / "reports/h41_candidate_robustness_sweep_report.md"

WINDOWS: List[Tuple[str, str, str]] = [
    ("cal_2024", "2024-01-01", "2024-12-31"),
    ("h1_2025", "2025-01-01", "2025-06-30"),
    ("h2_2025", "2025-07-01", "2025-12-31"),
    ("ytd_2026", "2026-01-01", "2026-05-21"),
    ("deploy_2025_2026", "2025-01-01", "2026-05-21"),
]


def overlay_from_dict(data: Dict) -> Overlay:
    return Overlay(
        name=data.get("name", "unknown"),
        mom20_min=data.get("mom20_min"),
        mom60_min=data.get("mom60_min"),
        ma_window=data.get("ma_window"),
        vol20_max=data.get("vol20_max"),
        market_ma_window=data.get("market_ma_window"),
        market_ret20_min=data.get("market_ret20_min"),
    )


def params_from_dict(data: Dict) -> Params:
    return Params(
        top_n=int(data["top_n"]),
        max_position_pct=float(data["max_position_pct"]),
        stop_loss_pct=float(data["stop_loss_pct"]),
        take_profit_pct=float(data["take_profit_pct"]),
        quality_filter=float(data["quality_filter"]),
        rebalance_freq_days=int(data["rebalance_freq_days"]),
    )


def candidate_key(result: Dict) -> str:
    return json.dumps({"overlay": result["overlay"], "params": result["params"]}, sort_keys=True)


def load_candidates(limit: int) -> List[Dict]:
    h39 = load_json(H39_JSON)
    pools = []
    for key in ["top_clean_candidates", "least_bad", "top_by_return", "top_by_sharpe"]:
        pools.extend(h39.get(key, []))
    unique = {}
    for result in pools:
        unique.setdefault(candidate_key(result), result)
    candidates = list(unique.values())
    candidates.sort(
        key=lambda r: (
            r.get("execution_blocked", True),
            r.get("terminal_losing_streak", 999),
            -r.get("metrics", {}).get("sharpe_ratio", -999),
            -r.get("metrics", {}).get("total_return", -999),
        )
    )
    return candidates[:limit]


def run_candidate_windows(source: CN_PIT_FileSource, candidate: Dict) -> Dict:
    params = params_from_dict(candidate["params"])
    overlay = overlay_from_dict(candidate["overlay"])
    config = load_json(DEFAULT_CONFIG)
    windows = {}
    for name, start, end in WINDOWS:
        universe = source.get_price_universe(start, end)
        prices = source.get_price_history(list(universe) + [HS300_TICKER], start, end)
        result = run_overlay_backtest(
            source, prices, start, end, DEFAULT_CAPITAL, params, overlay, config
        )
        windows[name] = {
            "start": start,
            "end": end,
            "result": result,
        }
    return windows


def summarize(candidate: Dict, windows: Dict) -> Dict:
    positive = 0
    unblocked = 0
    beat_hs300 = 0
    deploy = windows["deploy_2025_2026"]["result"]
    for payload in windows.values():
        r = payload["result"]
        m = r["metrics"]
        positive += int(m["total_return"] > 0)
        unblocked += int(not r["execution_blocked"])
        beat_hs300 += int(m["excess_return"] > 0)
    return {
        "candidate": {
            "overlay": candidate["overlay"],
            "params": candidate["params"],
        },
        "positive_windows": positive,
        "unblocked_windows": unblocked,
        "beat_hs300_windows": beat_hs300,
        "deploy_return": deploy["metrics"]["total_return"],
        "deploy_sharpe": deploy["metrics"]["sharpe_ratio"],
        "deploy_streak": deploy["terminal_losing_streak"],
        "deploy_trade_count": deploy["metrics"]["trade_count"],
        "deploy_blocked": deploy["execution_blocked"],
        "deploy_warnings": deploy["execution_warnings"],
        "windows": windows,
    }


def rank_key(row: Dict):
    return (
        row["deploy_blocked"],
        -row["unblocked_windows"],
        -row["positive_windows"],
        -row["beat_hs300_windows"],
        row["deploy_streak"],
        -row["deploy_sharpe"],
        -row["deploy_return"],
    )


def table_row(row: Dict) -> str:
    c = row["candidate"]
    p = c["params"]
    o = c["overlay"]
    warnings = "; ".join(row["deploy_warnings"]) if row["deploy_warnings"] else "none"
    return (
        f"| {o['name']} | {p['top_n']} | {p['max_position_pct']:.2f} | "
        f"{p['stop_loss_pct']:.2f} | {p['take_profit_pct']:.2f} | {p['quality_filter']:.2f} | "
        f"{p['rebalance_freq_days']} | {pct(row['deploy_return'])} | {row['deploy_sharpe']:.2f} | "
        f"{row['deploy_trade_count']} | {row['deploy_streak']} | "
        f"{row['positive_windows']}/5 | {row['unblocked_windows']}/5 | "
        f"{row['beat_hs300_windows']}/5 | {warnings} |"
    )


def build_report(payload: Dict) -> str:
    lines = [
        "# H41 — Candidate Robustness Sweep Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Candidates evaluated:** {payload['candidate_count']}",
        "",
        "## Ranked Candidates",
        "",
        "| Overlay | top_n | max_pos | SL | TP | QF | Rebal | Deploy Return | Sharpe | Trades | Streak | Positive | Unblocked | Beat HS300 | Warnings |",
        "|---------|-------|---------|----|----|----|-------|---------------|--------|--------|--------|----------|-----------|------------|----------|",
    ]
    for row in payload["ranked"][:15]:
        lines.append(table_row(row))
    best = payload["ranked"][0] if payload["ranked"] else None
    lines.extend(["", "## Verdict", ""])
    if not best:
        lines.append("No candidates were available.")
    elif best["unblocked_windows"] <= 1 or best["beat_hs300_windows"] == 0:
        lines.append(
            "**RESEARCH_ONLY** — no reviewed candidate is robust enough for live promotion. "
            "The best candidates can be used for paper-only forward observation."
        )
    else:
        lines.append(
            "**CANDIDATE_FOR_FORWARD_TRIAL** — best candidate improves robustness enough "
            "to justify a paper-only forward trial, not live promotion."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="H41 robustness sweep over H39 top candidates")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    source = CN_PIT_FileSource(
        prices_path=str(DEFAULT_PRICES),
        universe_path=str(DEFAULT_UNIVERSE),
        universe_snapshots_path=str(DEFAULT_SNAPSHOTS),
    )
    candidates = load_candidates(args.limit)
    rows = []
    for idx, candidate in enumerate(candidates, 1):
        print(f"[{idx}/{len(candidates)}] {candidate['overlay']['name']} {candidate['params']}")
        rows.append(summarize(candidate, run_candidate_windows(source, candidate)))
    rows.sort(key=rank_key)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candidate_count": len(rows),
        "ranked": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    REPORT.write_text(build_report(payload))
    print(f"Wrote: {OUTPUT}")
    print(f"Wrote: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
