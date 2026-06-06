#!/usr/bin/env python3
"""H31 — PIT value strategy robustness checks.

Runs temporal/OOS slices and one-at-a-time parameter sensitivity against the
H30 candidate PIT data. This script is non-destructive: it writes only H31
result/report artifacts and never promotes candidate data files.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


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


DEFAULT_PRICES = "data/cn_pit/prices_h30_candidate.csv"
DEFAULT_UNIVERSE = "data/cn_pit/universe_h30_candidate.jsonl"
DEFAULT_SNAPSHOTS = "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
DEFAULT_OUTPUT = "backtest/runs/fundamental_value_h31_robustness.json"
DEFAULT_REPORT = "reports/h31_pit_robustness_report.md"

DEFAULT_WINDOWS: List[Tuple[str, str, str]] = [
    ("cal_2024", "2024-01-01", "2024-12-31"),
    ("h1_2025", "2025-01-01", "2025-06-30"),
    ("h2_2025", "2025-07-01", "2025-12-31"),
    ("ytd_2026", "2026-01-01", "2026-05-06"),
    ("deploy_2025_2026", "2025-01-01", "2026-05-06"),
]

SENSITIVITY_WINDOW = ("deploy_2025_2026", "2025-01-01", "2026-05-06")

SENSITIVITY_GRID = {
    "top_n": [8, 10, 12],
    "max_position_pct": [0.08, 0.10, 0.12],
    "stop_loss_pct": [0.05, 0.06, 0.08, 0.10],
    "take_profit_pct": [0.18, 0.22, 0.25, 0.30],
    "quality_filter": [0.25, 0.30, 0.35, 0.40],
}


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


def compact_metrics(metrics: Dict) -> Dict:
    keys = [
        "total_return",
        "sharpe_ratio",
        "max_drawdown",
        "win_rate",
        "profit_factor",
        "trade_count",
        "hs300_return",
        "excess_return",
        "can_deploy",
        "deploy_blockers",
    ]
    return {k: metrics.get(k) for k in keys}


def run_windows(source: CN_PIT_FileSource, windows: Iterable[Tuple[str, str, str]]) -> Dict:
    output: Dict[str, Dict] = {}
    for window_name, start, end in windows:
        print(f"\n[window] {window_name}: {start} -> {end}")
        output[window_name] = {"start": start, "end": end, "strategies": {}}
        for strategy, params in VALUE_STRATEGY_VARIANTS.items():
            print(f"  running {strategy}...")
            result = run_fundamental_backtest(
                data_source=source,
                start_date=start,
                end_date=end,
                capital=500000,
                **params,
            )
            output[window_name]["strategies"][strategy] = result_metrics(result)
            m = result.metrics
            print(
                f"    ret={pct(m['total_return'])} sharpe={m['sharpe_ratio']:.2f} "
                f"trades={m['trade_count']} deploy={result.can_deploy}"
            )
    return output


def run_sensitivity(source: CN_PIT_FileSource, base_strategy: str) -> Dict:
    window_name, start, end = SENSITIVITY_WINDOW
    base_params = deepcopy(VALUE_STRATEGY_VARIANTS[base_strategy])
    output = {
        "strategy": base_strategy,
        "window": window_name,
        "start": start,
        "end": end,
        "base_params": base_params,
        "runs": {},
    }

    print(f"\n[sensitivity] {base_strategy}: {start} -> {end}")
    base_result = run_fundamental_backtest(
        data_source=source,
        start_date=start,
        end_date=end,
        capital=500000,
        **base_params,
    )
    output["base"] = result_metrics(base_result)
    print(
        f"  base ret={pct(base_result.metrics['total_return'])} "
        f"sharpe={base_result.metrics['sharpe_ratio']:.2f}"
    )

    for param, values in SENSITIVITY_GRID.items():
        output["runs"][param] = {}
        for value in values:
            params = deepcopy(base_params)
            params[param] = value
            run_name = str(value)
            print(f"  {param}={value}...")
            result = run_fundamental_backtest(
                data_source=source,
                start_date=start,
                end_date=end,
                capital=500000,
                **params,
            )
            output["runs"][param][run_name] = {
                "params": params,
                "metrics": result_metrics(result),
            }
    return output


def ranked_strategies(window_result: Dict) -> List[Tuple[str, Dict]]:
    return sorted(
        window_result["strategies"].items(),
        key=lambda item: (
            item[1].get("can_deploy", False),
            item[1].get("sharpe_ratio", -999),
            item[1].get("total_return", -999),
        ),
        reverse=True,
    )


def summarize_windows(windows: Dict) -> Dict:
    summary: Dict[str, Dict] = {}
    for window_name, window_result in windows.items():
        ranked = ranked_strategies(window_result)
        strategy_count = len(window_result["strategies"])
        summary[window_name] = {
            "best_by_gate_sharpe": ranked[0][0] if ranked else None,
            "deployable_count": sum(1 for _, m in ranked if m.get("can_deploy")),
            "positive_count": sum(1 for _, m in ranked if (m.get("total_return") or 0) > 0),
            "beat_hs300_count": sum(1 for _, m in ranked if (m.get("excess_return") or 0) > 0),
            "strategy_count": strategy_count,
        }
    return summary


def summarize_sensitivity(sensitivity: Dict) -> Dict:
    base = sensitivity["base"]
    base_return = base["total_return"]
    base_sharpe = base["sharpe_ratio"]
    rows = []
    for param, runs in sensitivity["runs"].items():
        for value, payload in runs.items():
            metrics = payload["metrics"]
            rows.append({
                "param": param,
                "value": value,
                "total_return": metrics["total_return"],
                "sharpe_ratio": metrics["sharpe_ratio"],
                "max_drawdown": metrics["max_drawdown"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "trade_count": metrics["trade_count"],
                "can_deploy": metrics["can_deploy"],
                "delta_return": metrics["total_return"] - base_return,
                "delta_sharpe": metrics["sharpe_ratio"] - base_sharpe,
            })
    rows.sort(key=lambda r: (r["can_deploy"], r["sharpe_ratio"], r["total_return"]), reverse=True)
    return {
        "base_return": base_return,
        "base_sharpe": base_sharpe,
        "best_by_sharpe": rows[0] if rows else None,
        "deployable_count": sum(1 for r in rows if r["can_deploy"]),
        "positive_count": sum(1 for r in rows if r["total_return"] > 0),
        "beat_base_return_count": sum(1 for r in rows if r["total_return"] > base_return),
        "beat_base_sharpe_count": sum(1 for r in rows if r["sharpe_ratio"] > base_sharpe),
        "runs": rows,
    }


def metric_table_row(name: str, metrics: Dict) -> str:
    deploy = "YES" if metrics.get("can_deploy") else "NO"
    return (
        f"| {name} | {pct(metrics['total_return'])} | {metrics['sharpe_ratio']:.2f} | "
        f"{pct(metrics['max_drawdown'])} | {plain_pct(metrics['win_rate'])} | "
        f"{metrics['profit_factor']:.2f} | {metrics['trade_count']} | "
        f"{pct(metrics['hs300_return'])} | {pct(metrics['excess_return'])} | {deploy} |"
    )


def generate_report(result: Dict) -> str:
    label = result.get("label", "H31")
    lines = [
        f"# {label} — PIT Robustness Report",
        "",
        f"**Generated:** {result['generated_at']}",
        "",
        "## Inputs",
        "",
        f"- Prices: `{result['inputs']['prices_path']}`",
        f"- Universe: `{result['inputs']['universe_path']}`",
        f"- Snapshot evidence: `{result['inputs']['universe_snapshots_path']}`",
        "",
        "## OOS / Temporal Windows",
        "",
    ]

    window_summary = result["summary"]["windows"]
    for window_name, window_result in result["windows"].items():
        s = window_summary[window_name]
        strategy_count = s["strategy_count"]
        lines.extend([
            f"### {window_name} `{window_result['start']}` -> `{window_result['end']}`",
            "",
            (
                f"- Best by gate+Sharpe: `{s['best_by_gate_sharpe']}`; "
                f"deployable: {s['deployable_count']}/{strategy_count}; "
                f"positive: {s['positive_count']}/{strategy_count}; "
                f"beat HS300: {s['beat_hs300_count']}/{strategy_count}"
            ),
            "",
            "| Strategy | Return | Sharpe | MaxDD | Win% | PF | Trades | HS300 | Excess | Deploy |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ])
        for strategy, metrics in ranked_strategies(window_result):
            lines.append(metric_table_row(strategy, metrics))
        lines.append("")

    sens = result["sensitivity"]
    sens_summary = result["summary"]["sensitivity"]
    best = sens_summary["best_by_sharpe"]
    lines.extend([
        "## Deep Value Sensitivity",
        "",
        f"- Base strategy: `{sens['strategy']}`",
        f"- Window: `{sens['start']}` -> `{sens['end']}`",
        f"- Base return: {pct(sens_summary['base_return'])}; base Sharpe: {sens_summary['base_sharpe']:.2f}",
        f"- Deployable runs: {sens_summary['deployable_count']}/{len(sens_summary['runs'])}",
        f"- Positive runs: {sens_summary['positive_count']}/{len(sens_summary['runs'])}",
        f"- Beat base return: {sens_summary['beat_base_return_count']}/{len(sens_summary['runs'])}",
        f"- Beat base Sharpe: {sens_summary['beat_base_sharpe_count']}/{len(sens_summary['runs'])}",
        "",
    ])
    if best:
        lines.extend([
            (
                f"Best sensitivity run by Sharpe: `{best['param']}={best['value']}` "
                f"return {pct(best['total_return'])}, Sharpe {best['sharpe_ratio']:.2f}, "
                f"MaxDD {pct(best['max_drawdown'])}."
            ),
            "",
        ])
    lines.extend([
        "| Param | Value | Return | ΔReturn | Sharpe | ΔSharpe | MaxDD | Win% | PF | Trades | Deploy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in sens_summary["runs"][:20]:
        deploy = "YES" if row["can_deploy"] else "NO"
        lines.append(
            f"| {row['param']} | {row['value']} | {pct(row['total_return'])} | "
            f"{pct(row['delta_return'])} | {row['sharpe_ratio']:.2f} | "
            f"{row['delta_sharpe']:+.2f} | {pct(row['max_drawdown'])} | "
            f"{plain_pct(row['win_rate'])} | {row['profit_factor']:.2f} | "
            f"{row['trade_count']} | {deploy} |"
        )
    lines.extend([
        "",
        "## Initial Read",
        "",
        "H31 is a robustness screen, not a promotion step. A strategy should not be promoted solely because it passes the data gate; it should also show stable behavior across windows and avoid depending on a narrow parameter setting.",
        "",
        "## Decision",
        "",
        f"- The PIT data is usable for the tested windows, but `{sens['strategy']}` is not robust enough for broad live promotion.",
        f"- `{sens['strategy']}` remains a research candidate for the deployment window, yet it still loses money in `cal_2024` and `ytd_2026` slices.",
        "- The next gate should be execution realism: liquidity filter, turnover, impact cost, and concentration checks.",
        "- Promotion should require passing those execution gates plus a clean OOS window that was not used for parameter selection.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="H31 PIT robustness checks")
    parser.add_argument("--prices-file", default=DEFAULT_PRICES)
    parser.add_argument("--universe-file", default=DEFAULT_UNIVERSE)
    parser.add_argument("--universe-snapshots-file", default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--base-strategy", default="deep_value", choices=sorted(VALUE_STRATEGY_VARIANTS))
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--label", default="H31")
    args = parser.parse_args()

    source = CN_PIT_FileSource(
        prices_path=str(resolve_path(args.prices_file)),
        universe_path=str(resolve_path(args.universe_file)),
        universe_snapshots_path=str(resolve_path(args.universe_snapshots_file)),
    )

    windows = run_windows(source, DEFAULT_WINDOWS)
    sensitivity = run_sensitivity(source, args.base_strategy)
    result = {
        "label": args.label,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "prices_path": str(resolve_path(args.prices_file)),
            "universe_path": str(resolve_path(args.universe_file)),
            "universe_snapshots_path": str(resolve_path(args.universe_snapshots_file)),
        },
        "windows": windows,
        "sensitivity": sensitivity,
    }
    result["summary"] = {
        "windows": summarize_windows(windows),
        "sensitivity": summarize_sensitivity(sensitivity),
    }

    output_path = resolve_path(args.output)
    report_path = resolve_path(args.report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    report_path.write_text(generate_report(result))
    print(f"\nSaved JSON: {output_path}")
    print(f"Saved report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
