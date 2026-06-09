#!/usr/bin/env python3
"""H34 — Turnover / Robustness optimization for deep_value_top8.

Searches low-turnover variants around deep_value_top8 using existing
CN_PIT_FileSource data files.  Non-destructive: only writes H34 artifacts.

Scoring criteria (in order):
  1. can_deploy  (boolean, True preferred)
  2. lower max_monthly_turnover  (lower = better, warned at >50%)
  3. lower annualized_turnover  (lower = better, warned at >2.0x)
  4. positive deploy-window excess_return vs HS300
  5. less bad OOS windows (minimise negative return in 2024/ytd_2026)
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# ── paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
RUNS_DIR = PROJECT_ROOT / "backtest" / "runs"
REPORTS_DIR = PROJECT_ROOT / "reports"

sys.path.insert(0, str(EXPERIMENTS_DIR))

from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    VALUE_STRATEGY_VARIANTS,
    run_fundamental_backtest,
)

# ── default file paths ─────────────────────────────────────────────────
DEFAULT_PRICES = "data/cn_pit/prices_h30_candidate.csv"
DEFAULT_UNIVERSE = "data/cn_pit/universe_h30_candidate.jsonl"
DEFAULT_SNAPSHOTS = "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
DEFAULT_OUTPUT = "backtest/runs/fundamental_value_h34_turnover_optimization.json"
DEFAULT_REPORT = "reports/h34_turnover_optimization_report.md"

# ── H33 turnover warp thresholds (reused for scoring) ──────────────────
WARN_MONTHLY_TURNOVER = 0.50
WARN_ANNUAL_TURNOVER = 2.00
CAPITAL = 500000.0

# ── computation windows ────────────────────────────────────────────────
DEPLOY_WINDOW = ("deploy_2025_2026", "2025-01-01", "2026-05-18")
OOS_WINDOWS: List[Tuple[str, str, str]] = [
    ("cal_2024", "2024-01-01", "2024-12-31"),
    ("h1_2025", "2025-01-01", "2025-06-30"),
    ("h2_2025", "2025-07-01", "2025-12-31"),
    ("ytd_2026", "2026-01-01", "2026-05-18"),
]

# ── parameter grid ─────────────────────────────────────────────────────
# Each param uses a list of (label, value) pairs to keep labels readable.
# Base = deep_value_top8: top_n=8, max_position_pct=0.12, stop_loss_pct=0.08,
#                          take_profit_pct=0.25, quality_filter=0.30,
#                          rebalance_freq_days=63
BASE_PARAMS = deepcopy(VALUE_STRATEGY_VARIANTS["deep_value_top8"])
# Base rebalance_freq_days = 63 (quarterly) — not stored in VARIANTS but
# the default in run_fundamental_backtest.  We add it explicitly.
BASE_PARAMS["rebalance_freq_days"] = 63

PARAM_GRID = {
    "rebalance_freq_days": [
        ("quarterly_63d", 63),
        ("semi_126d", 126),
        ("triannual_189d", 189),
        ("annual_252d", 252),
    ],
    "top_n": [("top8", 8), ("top10", 10), ("top12", 12)],
    "max_position_pct": [
        ("pos12pct", 0.12),
        ("pos10pct", 0.10),
        ("pos8pct", 0.08),
        ("pos6pct", 0.06),
    ],
    "stop_loss_pct": [
        ("sl8pct", 0.08),
        ("sl10pct", 0.10),
        ("sl12pct", 0.12),
    ],
    "take_profit_pct": [
        ("tp25pct", 0.25),
        ("tp30pct", 0.30),
        ("tp35pct", 0.35),
    ],
}


# ── helpers ─────────────────────────────────────────────────────────────
def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def plain_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def result_metrics(result) -> Dict:
    metrics = deepcopy(result.metrics)
    metrics["can_deploy"] = result.can_deploy
    metrics["deploy_blockers"] = list(result.deploy_blockers)
    return metrics


# ── turnover from trades (reimplemented concisely, consistent with H33) ─
def compute_turnover(trades: List[Dict], capital: float) -> Dict:
    """Return max_monthly_turnover and annualized_turnover like H33."""
    from collections import defaultdict

    by_month: Dict[str, float] = defaultdict(float)
    for t in trades:
        month = str(t["date"])[:7]
        by_month[month] += float(t.get("amount", 0.0))
    months = sorted(by_month.items())
    if not months:
        return {"max_monthly_turnover": 0.0, "annualized_turnover": 0.0}
    max_monthly = max(v / 2.0 / capital for _, v in months)
    avg_monthly = sum(v / 2.0 / capital for _, v in months) / len(months)
    return {
        "max_monthly_turnover": max_monthly,
        "annualized_turnover": avg_monthly * 12.0,
    }


# ── run a single variant across deploy + OOS windows ───────────────────
def run_variant(
    source: CN_PIT_FileSource,
    params: Dict,
    label: str,
) -> Dict:
    """Run deploy window + all OOS windows for one parameter set.

    Returns a dict suitable for appending to the result 'runs' list.
    """
    # Deploy window
    name, start, end = DEPLOY_WINDOW
    result = run_fundamental_backtest(
        data_source=source,
        start_date=start,
        end_date=end,
        capital=CAPITAL,
        **params,
    )
    metrics = result_metrics(result)
    turn = compute_turnover(result.trades, CAPITAL)

    deploy = {
        "total_return": metrics["total_return"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
        "profit_factor": metrics["profit_factor"],
        "trade_count": metrics["trade_count"],
        "hs300_return": metrics["hs300_return"],
        "excess_return": metrics["excess_return"],
        "can_deploy": metrics["can_deploy"],
        "deploy_blockers": metrics["deploy_blockers"],
        "max_monthly_turnover": turn["max_monthly_turnover"],
        "annualized_turnover": turn["annualized_turnover"],
    }

    # OOS windows
    oos_results: Dict[str, Dict] = {}
    for win_name, win_start, win_end in OOS_WINDOWS:
        r = run_fundamental_backtest(
            data_source=source,
            start_date=win_start,
            end_date=win_end,
            capital=CAPITAL,
            **params,
        )
        m = result_metrics(r)
        oos_results[win_name] = {
            "total_return": m["total_return"],
            "sharpe_ratio": m["sharpe_ratio"],
            "max_drawdown": m["max_drawdown"],
            "trade_count": m["trade_count"],
            "can_deploy": m["can_deploy"],
        }

    return {
        "label": label,
        "params": params,
        "deploy": deploy,
        "oos_windows": oos_results,
    }


# ── scoring ─────────────────────────────────────────────────────────────
def score_variant(v: Dict) -> Tuple:
    """Lower-is-better tuple for sorting.

    Priority:
      1. can_deploy (False → high score)
      2. max_monthly_turnover
      3. annualized_turnover
      4. negative excess_return in deploy window (more negative = worse)
      5. worst OOS return (more negative = worse)
    """
    d = v["deploy"]
    deployable = 0 if d["can_deploy"] else 1

    # Turnover scores (raw values, lower better)
    monthly_turn = d["max_monthly_turnover"]
    annual_turn = d["annualized_turnover"]

    # Excess: negative = bad, so we penalise negative excess
    excess = d.get("excess_return", 0.0) or 0.0

    # OOS: average the two most negative windows (cal_2024 + ytd_2026)
    oos_worst = 0.0
    count = 0
    for win_name in ("cal_2024", "ytd_2026"):
        o = v["oos_windows"].get(win_name, {})
        ret = o.get("total_return", 0.0) or 0.0
        if ret < oos_worst:
            oos_worst = ret
        if ret < 0:
            count += 1

    return (
        deployable,
        monthly_turn,
        annual_turn,
        -excess,          # more negative excess → higher score (=worse)
        -oos_worst,       # more negative OOS → higher score (=worse)
    )


# ── report builder ─────────────────────────────────────────────────────
def score_label(v: Dict) -> str:
    """Generate a compact score summary line."""
    d = v["deploy"]
    parts = []
    parts.append("DEPLOY" if d["can_deploy"] else "BLOCKED")
    parts.append(f"mTurn={d['max_monthly_turnover']*100:.1f}%")
    parts.append(f"aTurn={d['annualized_turnover']:.2f}x")
    parts.append(f"excess={d['excess_return']*100:+.2f}%")
    # worst OOS
    worst_ret = 0.0
    for win in ("cal_2024", "ytd_2026"):
        r = v["oos_windows"].get(win, {}).get("total_return", 0.0) or 0.0
        if r < worst_ret:
            worst_ret = r
    parts.append(f"OOS_worst={worst_ret*100:+.2f}%")
    return " | ".join(parts)


def build_report(result: Dict) -> str:
    lines = [
        "# H34 — Turnover / Robustness Optimization",
        "",
        f"**Generated:** {result['generated_at']}",
        "",
        "## Inputs",
        "",
        f"- Prices: `{result['inputs']['prices_path']}`",
        f"- Universe: `{result['inputs']['universe_path']}`",
        f"- Snapshot evidence: `{result['inputs']['universe_snapshots_path']}`",
        f"- Base strategy: `deep_value_top8`",
        f"- Base params: `{json.dumps(result['base_params'])}`",
        f"- Capital: ¥{CAPITAL:,.0f}",
        "",
        "## Parameter Grid",
        "",
    ]
    for param, entries in PARAM_GRID.items():
        vals = ", ".join(v for v, _ in entries)
        lines.append(f"- `{param}`: {vals}")
    lines.append("")

    # ── scoring summary ──
    lines.extend([
        "## Scoring Summary",
        "",
        "Sorted by: deployable > lower monthly turnover > lower annualized > "
        "positive excess > less bad OOS (cal_2024/ytd_2026).",
        "",
        "| Rank | Label | Deploy | mTurn | aTurn | Return | Sharpe | MaxDD | Win% | PF | Trades | Excess | OOS_worst |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])

    for i, run in enumerate(result["ranked_runs"], 1):
        d = run["deploy"]
        worst_ret = 0.0
        for win in ("cal_2024", "ytd_2026"):
            r = run["oos_windows"].get(win, {}).get("total_return", 0.0) or 0.0
            if r < worst_ret:
                worst_ret = r
        lines.append(
            f"| {i} | `{run['label']}` | "
            f"{'YES' if d['can_deploy'] else 'NO'} | "
            f"{plain_pct(d['max_monthly_turnover'])} | "
            f"{d['annualized_turnover']:.2f}x | "
            f"{pct(d['total_return'])} | "
            f"{d['sharpe_ratio']:.2f} | "
            f"{pct(d['max_drawdown'])} | "
            f"{plain_pct(d['win_rate'])} | "
            f"{d['profit_factor']:.2f} | "
            f"{d['trade_count']} | "
            f"{pct(d['excess_return'])} | "
            f"{pct(worst_ret)} |"
        )
    lines.append("")

    # ── deploy window detail ──
    lines.extend([
        "## Deploy Window Detail (`deploy_2025_2026`)",
        "",
        "| Label | Param change | Return | Sharpe | MaxDD | mTurn | aTurn | Trades | Excess | Deploy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for run in result["ranked_runs"]:
        d = run["deploy"]
        # Build a readable param-change column
        changes = []
        for k, v in run["params"].items():
            base_v = result["base_params"].get(k)
            if v == base_v:
                continue
            changes.append(f"{k}={v}")
        change_str = ", ".join(changes) if changes else "(base)"
        lines.append(
            f"| `{run['label']}` | {change_str} | "
            f"{pct(d['total_return'])} | {d['sharpe_ratio']:.2f} | "
            f"{pct(d['max_drawdown'])} | {plain_pct(d['max_monthly_turnover'])} | "
            f"{d['annualized_turnover']:.2f}x | {d['trade_count']} | "
            f"{pct(d['excess_return'])} | {'YES' if d['can_deploy'] else 'NO'} |"
        )
    lines.append("")

    # ── OOS detail for top 5 ──
    lines.extend([
        "## OOS Windows (Top 5 candidates)",
        "",
        "| Label | cal_2024 ret | h1_2025 ret | h2_2025 ret | ytd_2026 ret |",
        "|---|---:|---:|---:|---:|",
    ])
    for run in result["ranked_runs"][:5]:
        label = run["label"]
        oos = run["oos_windows"]
        lines.append(
            f"| `{label}` | "
            f"{pct(oos.get('cal_2024', {}).get('total_return', 0.0))} | "
            f"{pct(oos.get('h1_2025', {}).get('total_return', 0.0))} | "
            f"{pct(oos.get('h2_2025', {}).get('total_return', 0.0))} | "
            f"{pct(oos.get('ytd_2026', {}).get('total_return', 0.0))} |"
        )
    lines.append("")

    # ── recommendation ──
    lines.append("## Recommendation")
    lines.append("")
    if result["ranked_runs"]:
        best = result["ranked_runs"][0]
        base = result.get("best_deployable_base")
        lines.append(f"**Top candidate:** `{best['label']}`")
        lines.append("")
        lines.append(f"- Params: `{json.dumps(best['params'])}`")
        lines.append(f"- Deploy window:")
        for k, v in best["deploy"].items():
            if isinstance(v, float):
                lines.append(f"  - {k}: {v:.4f}")
            else:
                lines.append(f"  - {k}: {v}")
        lines.append("")
        lines.append(f"- vs base `deep_value_top8` (deployable={base['can_deploy']}):")
        lines.append(f"  - Base mTurn={plain_pct(base['max_monthly_turnover'])} "
                     f"→ {plain_pct(best['deploy']['max_monthly_turnover'])}")
        lines.append(f"  - Base aTurn={base['annualized_turnover']:.2f}x "
                     f"→ {best['deploy']['annualized_turnover']:.2f}x")
        lines.append(f"  - Base excess={pct(base['excess_return'])} "
                     f"→ {pct(best['deploy']['excess_return'])}")
        lines.append("")
    lines.extend([
        "## Notes",
        "",
        "- H34 is a non-destructive search.  No candidate data files are promoted.",
        "- Turnover is computed as (monthly_trade_amount / 2 / capital), consistent with H33.",
        "- The base run is included as the first grid point (`rebalance_freq_days=63`).",
        "- OOS windows use the extended end date 2026-05-18 (same as deploy window).",
        "- Recommended next step: if a candidate clears all gates, run a full H31+H33 against it.",
        "",
    ])
    return "\n".join(lines)


# ── main ────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="H34 turnover / robustness optimization for deep_value_top8"
    )
    parser.add_argument("--prices-file", default=DEFAULT_PRICES)
    parser.add_argument("--universe-file", default=DEFAULT_UNIVERSE)
    parser.add_argument("--universe-snapshots-file", default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()

    source = CN_PIT_FileSource(
        prices_path=str(resolve_path(args.prices_file)),
        universe_path=str(resolve_path(args.universe_file)),
        universe_snapshots_path=str(resolve_path(args.universe_snapshots_file)),
    )

    # Build the full candidate grid
    all_runs: List[Dict] = []
    seen: set = set()

    for param, entries in PARAM_GRID.items():
        for label_suffix, value in entries:
            params = deepcopy(BASE_PARAMS)
            params[param] = value
            # Deduplicate by frozenset of param items
            key = frozenset(params.items())
            if key in seen:
                continue
            seen.add(key)
            label = f"{param}={label_suffix}"
            print(f"[h34] running {label}...", end="", flush=True)
            run_data = run_variant(source, params, label)
            all_runs.append(run_data)
            d = run_data["deploy"]
            print(
                f" deploy={d['can_deploy']} "
                f"ret={pct(d['total_return'])} "
                f"mTurn={plain_pct(d['max_monthly_turnover'])} "
                f"aTurn={d['annualized_turnover']:.2f}x "
                f"excess={pct(d['excess_return'])}"
            )

    # Sort by scoring tuple
    all_runs.sort(key=score_variant)

    # Base deploy run (reference)
    base_run = run_variant(source, BASE_PARAMS, "base_deep_value_top8")
    best_base = base_run["deploy"]

    # Assemble result
    result = {
        "label": "H34",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "prices_path": str(resolve_path(args.prices_file)),
            "universe_path": str(resolve_path(args.universe_file)),
            "universe_snapshots_path": str(resolve_path(args.universe_snapshots_file)),
        },
        "base_params": BASE_PARAMS,
        "best_deployable_base": best_base,
        "param_grid_config": {
            k: [v for _, v in entries] for k, entries in PARAM_GRID.items()
        },
        "total_candidates": len(all_runs),
        "deployable_count": sum(1 for r in all_runs if r["deploy"]["can_deploy"]),
        "ranked_runs": all_runs,
    }

    output_path = resolve_path(args.output)
    report_path = resolve_path(args.report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str)
    )
    report_path.write_text(build_report(result))

    print(f"\nSaved JSON: {output_path}")
    print(f"Saved report: {report_path}")
    print(f"Total candidates: {result['total_candidates']}")
    print(f"Deployable: {result['deployable_count']}")
    if all_runs:
        best = all_runs[0]
        print(f"\nTop candidate: `{best['label']}`")
        print(f"  {score_label(best)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
