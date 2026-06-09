#!/usr/bin/env python3
"""H40 — robustness and execution audit for the H39 shadow candidate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from fundamental_backtest import CN_PIT_FileSource, HS300_TICKER  # noqa: E402
from h33_execution_audit import (  # noqa: E402
    BLOCK_ANNUAL_TURNOVER,
    BLOCK_MISSING_LIQUIDITY,
    BLOCK_MONTHLY_TURNOVER,
    BLOCK_PARTICIPATION,
    BLOCK_SINGLE_POSITION,
    BLOCK_TOP3_POSITION,
    WARN_ANNUAL_TURNOVER,
    WARN_MONTHLY_TURNOVER,
    WARN_PARTICIPATION,
    WARN_SINGLE_POSITION,
    WARN_TOP3_POSITION,
    audit_liquidity,
    load_or_fetch_liquidity,
    monthly_turnover,
    pct as h33_pct,
    reconstruct_positions,
)
from h35_shadow_account_executor import check_stop_conditions  # noqa: E402
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

DEFAULT_OUTPUT = PROJECT_ROOT / "backtest/runs/fundamental_value_h40_h39_candidate_audit.json"
DEFAULT_REPORT = PROJECT_ROOT / "reports/h40_h39_candidate_audit_report.md"
DEFAULT_LIQUIDITY = PROJECT_ROOT / "data/cn_pit/liquidity_h33_daily_amount.csv"

WINDOWS: List[Tuple[str, str, str]] = [
    ("cal_2024", "2024-01-01", "2024-12-31"),
    ("h1_2025", "2025-01-01", "2025-06-30"),
    ("h2_2025", "2025-07-01", "2025-12-31"),
    ("ytd_2026", "2026-01-01", "2026-05-21"),
    ("deploy_2025_2026", "2025-01-01", "2026-05-21"),
]

CANDIDATE_PARAMS = Params(
    top_n=8,
    max_position_pct=0.08,
    stop_loss_pct=0.08,
    take_profit_pct=0.25,
    quality_filter=0.30,
    rebalance_freq_days=63,
)
CANDIDATE_OVERLAY = Overlay("price_gt_ma20", ma_window=20)


def resolve(path: Path | str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def equity_series(rows: List[Dict]) -> pd.Series:
    return pd.Series(
        [float(row["value"]) for row in rows],
        index=pd.to_datetime([row["date"] for row in rows]),
    )


def compact_run(result: Dict) -> Dict:
    return {
        "params": result["params"],
        "overlay": result["overlay"],
        "metrics": result["metrics"],
        "can_deploy_data_quality": result["can_deploy_data_quality"],
        "execution_blocked": result["execution_blocked"],
        "execution_blockers": result["execution_blockers"],
        "execution_warnings": result["execution_warnings"],
        "terminal_losing_streak": result["terminal_losing_streak"],
        "monthly_one_way_turnover": result["monthly_one_way_turnover"],
        "annualized_turnover": result["annualized_turnover"],
        "last_8_sells": result["last_8_sells"],
    }


def run_candidate(source: CN_PIT_FileSource, start: str, end: str, return_details: bool = False) -> Dict:
    universe = source.get_price_universe(start, end)
    prices = source.get_price_history(list(universe) + [HS300_TICKER], start, end)
    return run_overlay_backtest(
        source,
        prices,
        start,
        end,
        DEFAULT_CAPITAL,
        CANDIDATE_PARAMS,
        CANDIDATE_OVERLAY,
        load_json(DEFAULT_CONFIG),
        return_details=return_details,
    )


def run_windows(source: CN_PIT_FileSource) -> Dict:
    output = {}
    for name, start, end in WINDOWS:
        print(f"[window] {name}: {start} -> {end}")
        output[name] = {
            "start": start,
            "end": end,
            "result": compact_run(run_candidate(source, start, end, return_details=False)),
        }
        m = output[name]["result"]["metrics"]
        print(
            f"  ret={pct(m['total_return'])} sharpe={m['sharpe_ratio']:.2f} "
            f"trades={m['trade_count']} blocked={output[name]['result']['execution_blocked']}"
        )
    return output


def execution_audit(source: CN_PIT_FileSource, run: Dict, start: str, end: str,
                    liquidity_path: Path, fetch_liquidity: bool) -> Dict:
    trades = run["trades"]
    tickers = sorted({t["ticker"] for t in trades if t.get("action") in {"buy", "sell"}})
    liquidity = load_or_fetch_liquidity(liquidity_path, tickers, start, end, fetch_liquidity)
    prices = source.get_price_history(tickers, start, end)
    eq = equity_series(run["equity_curve"])

    concentration = reconstruct_positions(trades, prices, eq)
    turnover = monthly_turnover(trades, eq)
    liquidity_audit = audit_liquidity(trades, liquidity)

    blockers = []
    warnings = []
    if run["execution_blockers"]:
        blockers.extend(f"h39_gate:{b}" for b in run["execution_blockers"])
    if BLOCK_MISSING_LIQUIDITY and liquidity_audit["missing_liquidity_count"] > 0:
        blockers.append(f"missing_liquidity:{liquidity_audit['missing_liquidity_count']}")
    if liquidity_audit["max_participation"] > BLOCK_PARTICIPATION:
        blockers.append(f"participation>{h33_pct(BLOCK_PARTICIPATION)}")
    elif liquidity_audit["max_participation"] > WARN_PARTICIPATION:
        warnings.append(f"participation>{h33_pct(WARN_PARTICIPATION)}")
    if turnover["max_monthly_turnover"] > BLOCK_MONTHLY_TURNOVER:
        blockers.append(f"monthly_turnover>{h33_pct(BLOCK_MONTHLY_TURNOVER)}")
    elif turnover["max_monthly_turnover"] > WARN_MONTHLY_TURNOVER:
        warnings.append(f"monthly_turnover>{h33_pct(WARN_MONTHLY_TURNOVER)}")
    if turnover["annualized_turnover"] > BLOCK_ANNUAL_TURNOVER:
        blockers.append(f"annualized_turnover>{BLOCK_ANNUAL_TURNOVER:.1f}x")
    elif turnover["annualized_turnover"] > WARN_ANNUAL_TURNOVER:
        warnings.append(f"annualized_turnover>{WARN_ANNUAL_TURNOVER:.1f}x")
    if concentration["max_single_position"] > BLOCK_SINGLE_POSITION:
        blockers.append(f"single_position>{h33_pct(BLOCK_SINGLE_POSITION)}")
    elif concentration["max_single_position"] > WARN_SINGLE_POSITION:
        warnings.append(f"single_position>{h33_pct(WARN_SINGLE_POSITION)}")
    if concentration["max_top3_position"] > BLOCK_TOP3_POSITION:
        blockers.append(f"top3_position>{h33_pct(BLOCK_TOP3_POSITION)}")
    elif concentration["max_top3_position"] > WARN_TOP3_POSITION:
        warnings.append(f"top3_position>{h33_pct(WARN_TOP3_POSITION)}")

    return {
        "liquidity_source": str(liquidity_path),
        "turnover": turnover,
        "liquidity": liquidity_audit,
        "concentration": concentration,
        "execution_can_deploy": len(blockers) == 0,
        "execution_blockers": blockers,
        "execution_warnings": warnings,
    }


def robustness_verdict(windows: Dict) -> Dict:
    rows = []
    for name, payload in windows.items():
        r = payload["result"]
        m = r["metrics"]
        rows.append({
            "window": name,
            "positive": m["total_return"] > 0,
            "sharpe_ok": m["sharpe_ratio"] >= 0,
            "blocked": r["execution_blocked"],
            "trade_count": m["trade_count"],
            "excess_return": m["excess_return"],
        })
    return {
        "positive_windows": sum(1 for r in rows if r["positive"]),
        "unblocked_windows": sum(1 for r in rows if not r["blocked"]),
        "beat_hs300_windows": sum(1 for r in rows if r["excess_return"] > 0),
        "window_count": len(rows),
        "rows": rows,
        "robust_enough_for_shadow_candidate": (
            sum(1 for r in rows if r["positive"]) >= 3
            and sum(1 for r in rows if not r["blocked"]) >= 2
        ),
    }


def row(name: str, result: Dict) -> str:
    m = result["metrics"]
    blockers = "; ".join(result["execution_blockers"]) if result["execution_blockers"] else "none"
    warnings = "; ".join(result["execution_warnings"]) if result["execution_warnings"] else "none"
    return (
        f"| {name} | {pct(m['total_return'])} | {m['sharpe_ratio']:.2f} | "
        f"{pct(m['max_drawdown'])} | {m['trade_count']} | "
        f"{result['terminal_losing_streak']} | {result['annualized_turnover']:.2f}x | "
        f"{pct(m['hs300_return'])} | {pct(m['excess_return'])} | {blockers} | {warnings} |"
    )


def build_report(payload: Dict) -> str:
    deploy = payload["deploy_result"]
    ex = payload["execution_audit"]
    rb = payload["robustness"]
    status = "PASS_WITH_WARNINGS"
    if ex["execution_blockers"]:
        status = "BLOCKED"
    elif not rb["robust_enough_for_shadow_candidate"]:
        status = "RESEARCH_ONLY"
    lines = [
        "# H40 — H39 Candidate Robustness + Execution Audit",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Status:** {status}",
        "",
        "## Candidate",
        "",
        f"- Overlay: `{CANDIDATE_OVERLAY.name}` (`price > MA20` at entry)",
        f"- Params: `{payload['candidate_params']}`",
        "",
        "## Deploy Window Result",
        "",
        "| Window | Return | Sharpe | MaxDD | Trades | Streak | AnnTurn | HS300 | Excess | Blockers | Warnings |",
        "|--------|--------|--------|-------|--------|--------|---------|-------|--------|----------|----------|",
        row("deploy_2025_2026", deploy),
        "",
        "## Temporal Robustness",
        "",
        f"- Positive windows: {rb['positive_windows']}/{rb['window_count']}",
        f"- Unblocked windows: {rb['unblocked_windows']}/{rb['window_count']}",
        f"- Beat HS300 windows: {rb['beat_hs300_windows']}/{rb['window_count']}",
        f"- Robust enough for shadow candidate: {rb['robust_enough_for_shadow_candidate']}",
        "",
        "| Window | Return | Sharpe | MaxDD | Trades | Streak | AnnTurn | HS300 | Excess | Blockers | Warnings |",
        "|--------|--------|--------|-------|--------|--------|---------|-------|--------|----------|----------|",
    ]
    for name, payload_window in payload["windows"].items():
        lines.append(row(name, payload_window["result"]))
    turn = ex["turnover"]
    liq = ex["liquidity"]
    conc = ex["concentration"]
    lines.extend([
        "",
        "## Execution Audit",
        "",
        f"- Execution can deploy: {ex['execution_can_deploy']}",
        f"- Monthly turnover max: {pct(turn['max_monthly_turnover'])} (`{turn['max_month']}`)",
        f"- Total turnover / capital: {turn['total_turnover']:.2f}x",
        f"- Annualized turnover: {turn['annualized_turnover']:.2f}x",
        f"- Missing liquidity trades: {liq['missing_liquidity_count']}/{liq['trade_count']}",
        f"- Max participation: {pct(liq['max_participation'])}",
        f"- High participation trades: {liq['high_participation_count']}",
        f"- Extra impact drag: {pct(liq['extra_impact_return_drag'])}",
        f"- Max single-position weight: {pct(conc['max_single_position'])} (`{conc['max_single_position_date']}`)",
        f"- Max top-3 position weight: {pct(conc['max_top3_position'])} (`{conc['max_top3_position_date']}`)",
        "",
        "### Execution Blockers",
    ])
    if ex["execution_blockers"]:
        lines.extend(f"- {b}" for b in ex["execution_blockers"])
    else:
        lines.append("- none")
    lines.append("")
    lines.append("### Execution Warnings")
    if ex["execution_warnings"]:
        lines.extend(f"- {w}" for w in ex["execution_warnings"])
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Verdict",
        "",
    ])
    if status == "BLOCKED":
        lines.append("**BLOCKED** — candidate clears H39 stop-streak gate but fails execution audit.")
    elif status == "RESEARCH_ONLY":
        lines.append("**RESEARCH_ONLY** — execution audit is acceptable, but temporal robustness is still weak. Keep as paper-only.")
    else:
        lines.append("**PASS_WITH_WARNINGS** — candidate may proceed to a paper-only forward trial, but should not be promoted live.")
    lines.append("")
    lines.append("Do not modify H34 config automatically. Promotion requires human signoff and a fresh forward-only observation period.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="H40 audit for H39 candidate")
    parser.add_argument("--prices-file", default=str(DEFAULT_PRICES))
    parser.add_argument("--universe-file", default=str(DEFAULT_UNIVERSE))
    parser.add_argument("--snapshots-file", default=str(DEFAULT_SNAPSHOTS))
    parser.add_argument("--liquidity-cache", default=str(DEFAULT_LIQUIDITY))
    parser.add_argument("--fetch-liquidity", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()

    source = CN_PIT_FileSource(
        prices_path=str(resolve(args.prices_file)),
        universe_path=str(resolve(args.universe_file)),
        universe_snapshots_path=str(resolve(args.snapshots_file)),
    )
    deploy_start, deploy_end = "2025-01-01", "2026-05-21"
    print("[deploy] H39 candidate")
    deploy_result = run_candidate(source, deploy_start, deploy_end, return_details=True)
    windows = run_windows(source)
    ex = execution_audit(
        source,
        deploy_result,
        deploy_start,
        deploy_end,
        resolve(args.liquidity_cache),
        args.fetch_liquidity,
    )
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candidate_params": CANDIDATE_PARAMS.__dict__,
        "candidate_overlay": CANDIDATE_OVERLAY.__dict__,
        "inputs": {
            "prices_file": str(resolve(args.prices_file)),
            "universe_file": str(resolve(args.universe_file)),
            "snapshots_file": str(resolve(args.snapshots_file)),
            "liquidity_cache": str(resolve(args.liquidity_cache)),
        },
        "deploy_result": compact_run(deploy_result),
        "windows": windows,
        "robustness": robustness_verdict(windows),
        "execution_audit": ex,
    }
    output = resolve(args.output)
    report = resolve(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    report.write_text(build_report(payload))
    print(f"Wrote: {output}")
    print(f"Wrote: {report}")
    print(f"Execution can deploy: {ex['execution_can_deploy']}")
    print(f"Robust enough: {payload['robustness']['robust_enough_for_shadow_candidate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
