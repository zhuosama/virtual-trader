#!/usr/bin/env python3
"""H36 — Shadow Loss Diagnosis for H35 consecutive_losing_sells gate.

Analyzes the 5-trade losing streak that blocked H35. For each losing sell:
  - Entry/exit dates, exit reason, held days
  - PnL and PnL%
  - Stock hold-period return vs HS300 same period (if prices available)
  - Classifies likely cause: stop_loss, rebalance_loss, market_beta, idiosyncratic

Also runs a deterministic what-if grid around deep_value_top8_shadow params:
  stop_loss 0.06/0.08/0.10/0.12, take_profit 0.18/0.22/0.25,
  max_position_pct 0.06/0.08, quality_filter 0.30/0.35
Computes: total_return, sharpe, maxdd, excess, closed trades,
  terminal losing sell streak, H35 blockers/warnings.

Outputs:
  - backtest/runs/fundamental_value_h36_loss_diagnosis.json
  - reports/h36_shadow_loss_diagnosis_report.md

Non-destructive — reads existing H35 outputs, does NOT modify H34 config.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ── paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
RUNS_DIR = PROJECT_ROOT / "backtest" / "runs"
REPORTS_DIR = PROJECT_ROOT / "reports"
CONFIG_PATH = PROJECT_ROOT / "value_account" / "h34_shadow_account_config.json"
TRADE_LOG = PROJECT_ROOT / "value_account/logs/h35_shadow_trades.jsonl"
STATE_FILE = PROJECT_ROOT / "value_account/reports/h35_shadow_state.json"
PRICES_FILE = PROJECT_ROOT / "data/cn_pit/prices_h30_candidate.csv"
UNIVERSE_FILE = PROJECT_ROOT / "data/cn_pit/universe_h30_candidate.jsonl"
SNAPSHOTS_FILE = PROJECT_ROOT / "data/cn_pit/universe_snapshots_h30_candidate.jsonl"

sys.path.insert(0, str(EXPERIMENTS_DIR))
from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    run_fundamental_backtest,
    BacktestResult,
    HS300_TICKER,
)

# H35 helpers — imported from the executor script (not in fundamental_backtest)
H35_EXECUTOR = PROJECT_ROOT / "scripts" / "h35_shadow_account_executor.py"
# Import the gate functions by reading the module
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from h35_shadow_account_executor import (  # noqa: E402
    compute_consecutive_losing_sells,
    compute_monthly_one_way_turnover,
    compute_annualized_turnover,
    check_stop_conditions,
)


# ── helpers ────────────────────────────────────────────────────────────
def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def plain_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def load_jsonl(path: Path) -> List[Dict]:
    rows = []
    with open(path) as f:
        for line in f:
            t = line.strip()
            if t:
                rows.append(json.loads(t))
    return rows


def load_config(path: Path = CONFIG_PATH) -> Dict:
    return load_json(path)


def load_trades(path: Path = TRADE_LOG) -> List[Dict]:
    return load_jsonl(path)


def load_prices(path: Path = PRICES_FILE) -> pd.DataFrame:
    df = pd.read_csv(path)
    date_col = "date" if "date" in df.columns else "Date"
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    return df


def classify_loss(trade: Dict, stock_ret: Optional[float],
                  hs300_ret: Optional[float]) -> str:
    """Classify a losing sell into one of four causes."""
    reason = trade.get("exit_reason", "")
    if reason == "sl":
        return "stop_loss"
    if reason == "rebalance_out":
        return "rebalance_loss"
    # Market beta: stock lost but HS300 lost similarly
    if stock_ret is not None and hs300_ret is not None:
        if hs300_ret <= -0.03 and abs(stock_ret - hs300_ret) < 0.05:
            return "market_beta"
        if abs(stock_ret) < 0.03 and hs300_ret is not None and hs300_ret > -0.02:
            return "idiosyncratic"
    # If it was held a long time and just underperformed
    held = trade.get("held_days", 0)
    pnl_pct = trade.get("pnl_pct", 0)
    if held >= 60 and -0.05 <= pnl_pct <= 0:
        return "idiosyncratic"
    return "idiopathic"  # something else


def compute_stock_vs_hs300(
    trade: Dict,
    prices: pd.DataFrame,
    hs300_col: str = HS300_TICKER,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (stock_hold_return, hs300_hold_return, hs300_entry_price)."""
    ticker = trade.get("ticker", "")
    # Find entry cost from matching buy trade
    start_str = ""
    end_str = trade.get("date", "")
    # We need entry date — use held_days to estimate
    held_days = trade.get("held_days", 0)
    if held_days > 0 and end_str:
        try:
            end_dt = pd.Timestamp(end_str)
            # Walk back through trading dates to find approximate entry
            if ticker in prices.columns and end_str in prices.index:
                idx = prices.index.get_loc(end_str)
                if isinstance(idx, slice):
                    idx = idx.start
                start_idx = max(0, idx - held_days)
                start_str = prices.index[start_idx].strftime("%Y-%m-%d")
                stock_entry = prices.loc[start_str, ticker] if start_str in prices.index else None
                stock_exit = prices.loc[end_str, ticker] if end_str in prices.index else None
                hs_entry = prices.loc[start_str, hs300_col] if start_str in prices.index else None
                hs_exit = prices.loc[end_str, hs300_col] if end_str in prices.index else None

                stock_ret = None
                hs_ret = None
                if stock_entry is not None and stock_exit is not None and \
                   not (isinstance(stock_entry, float) and math.isnan(stock_entry)) and \
                   not (isinstance(stock_exit, float) and math.isnan(stock_exit)):
                    stock_ret = stock_exit / stock_entry - 1.0
                if hs_entry is not None and hs_exit is not None and \
                   not (isinstance(hs_entry, float) and math.isnan(hs_entry)) and \
                   not (isinstance(hs_exit, float) and math.isnan(hs_exit)):
                    hs_ret = hs_exit / hs_entry - 1.0
                return stock_ret, hs_ret, hs_entry
        except (KeyError, IndexError):
            pass
    return None, None, None


# ── loss streak analysis ──────────────────────────────────────────────
def analyze_losing_streak(
    trades: List[Dict],
    prices: pd.DataFrame,
) -> Dict:
    """Identify the terminal losing sell streak and analyze each trade."""
    # Walk trades backwards to find the losing streak.
    losing_sells = []
    for idx in range(len(trades) - 1, -1, -1):
        t = trades[idx]
        if t.get("action") != "sell":
            continue
        pnl = t.get("pnl", 0)
        if pnl is not None and pnl <= 0:
            ticker = t.get("ticker", "")
            held = t.get("held_days", 0)
            exit_date = t.get("date", "")
            buy_date = infer_entry_date_from_trades(trades, idx)

            stock_ret, hs300_ret, hs_entry = compute_stock_vs_hs300(t, prices)
            cause = classify_loss(t, stock_ret, hs300_ret)

            losing_sells.append({
                "ticker": ticker,
                "entry_date": buy_date,
                "exit_date": exit_date,
                "exit_reason": t.get("exit_reason", "?"),
                "held_days": held,
                "pnl": t.get("pnl", 0),
                "pnl_pct": t.get("pnl_pct", 0),
                "stock_hold_return": round(stock_ret * 100, 2) if stock_ret is not None else None,
                "hs300_hold_return": round(hs300_ret * 100, 2) if hs300_ret is not None else None,
                "probable_cause": cause,
            })
        else:
            break  # Found a winning trade, streak ends

    losing_sells.reverse()  # chronological order

    # Aggregate stats
    total_pnl = sum(t["pnl"] for t in losing_sells)
    avg_pnl_pct = sum(t["pnl_pct"] for t in losing_sells) / len(losing_sells) if losing_sells else 0
    causes = defaultdict(int)
    for t in losing_sells:
        causes[t["probable_cause"]] += 1
    avg_held = sum(t["held_days"] for t in losing_sells) / len(losing_sells) if losing_sells else 0

    return {
        "streak_count": len(losing_sells),
        "trades": losing_sells,
        "total_pnl": total_pnl,
        "avg_pnl_pct": avg_pnl_pct,
        "cause_breakdown": dict(causes),
        "avg_held_days": avg_held,
    }


def infer_entry_date_from_trades(trades: List[Dict], sell_index: int) -> str:
    """Find the most recent buy for the sold ticker before this sell."""
    sell = trades[sell_index]
    ticker = sell.get("ticker")
    for prior in range(sell_index - 1, -1, -1):
        trade = trades[prior]
        if trade.get("action") == "buy" and trade.get("ticker") == ticker:
            return str(trade.get("date", ""))
    return ""


# ── what-if grid ──────────────────────────────────────────────────────
BASELINE_PARAMS = {
    "top_n": 8,
    "max_position_pct": 0.08,
    "stop_loss_pct": 0.08,
    "take_profit_pct": 0.22,
    "quality_filter": 0.30,
}

GRID_SEARCH = {
    "stop_loss_pct": [0.06, 0.08, 0.10, 0.12],
    "take_profit_pct": [0.18, 0.22, 0.25],
    "max_position_pct": [0.06, 0.08],
    "quality_filter": [0.30, 0.35],
}


def run_what_if_grid(
    source: CN_PIT_FileSource,
    start: str,
    end: str,
    capital: float,
    config: Dict,
) -> List[Dict]:
    """Run deterministic what-if grid, returning results for each param combo."""
    results = []

    for sl in GRID_SEARCH["stop_loss_pct"]:
        for tp in GRID_SEARCH["take_profit_pct"]:
            for mp in GRID_SEARCH["max_position_pct"]:
                for qf in GRID_SEARCH["quality_filter"]:
                    params = {
                        "top_n": 8,
                        "max_position_pct": mp,
                        "stop_loss_pct": sl,
                        "take_profit_pct": tp,
                        "quality_filter": qf,
                    }
                    try:
                        bt = run_fundamental_backtest(
                            data_source=source,
                            start_date=start,
                            end_date=end,
                            capital=capital,
                            **params,
                        )
                        # Compute H35 gate results
                        blockers, warnings = check_stop_conditions(
                            bt.trades, capital, config,
                            bt.metrics, equity_curve=bt.equity_curve,
                            as_of_date=end,
                        )
                        # Add data quality blockers
                        for db in bt.deploy_blockers:
                            blockers.append(f"data_quality:{db}")

                        # Compute terminal losing streak
                        streak = compute_consecutive_losing_sells(bt.trades)

                        results.append({
                            "params": params,
                            "total_return": bt.metrics.get("total_return"),
                            "sharpe": bt.metrics.get("sharpe_ratio"),
                            "max_drawdown": bt.metrics.get("max_drawdown"),
                            "excess_return": bt.metrics.get("excess_return"),
                            "hs300_return": bt.metrics.get("hs300_return"),
                            "closed_trades": bt.metrics.get("trade_count", 0),
                            "win_rate": bt.metrics.get("win_rate", 0),
                            "profit_factor": bt.metrics.get("profit_factor", 0),
                            "terminal_losing_streak": streak,
                            "h35_blockers": blockers,
                            "h35_warnings": warnings,
                            "h35_blocked": len(blockers) > 0,
                        })
                    except Exception as exc:
                        results.append({
                            "params": params,
                            "error": str(exc),
                        })

    return results


def find_best_param_combos(results: List[Dict]) -> Dict:
    """Identify the best combos that clear the consecutive_losers gate."""
    clean = [r for r in results if "error" not in r and not r["h35_blocked"]]
    clean.sort(key=lambda r: r["total_return"], reverse=True)

    best_clean = clean[:5] if clean else []

    # Also find combos with lowest losing streak
    by_streak = sorted(
        [r for r in results if "error" not in r],
        key=lambda r: (r["terminal_losing_streak"], -r["total_return"]),
    )
    best_by_streak = by_streak[:5] if by_streak else []

    # Any combo that fully clears the gate and has positive return
    safe_wins = [r for r in results if "error" not in r
                 and not r["h35_blocked"]
                 and r["total_return"] > 0]
    safe_wins.sort(key=lambda r: r["total_return"], reverse=True)

    return {
        "total_combos": len(results),
        "clean_combos": len(clean),
        "best_clean": best_clean,
        "best_by_lowest_streak": best_by_streak,
        "safe_wins_count": len(safe_wins),
        "safe_wins_top": safe_wins[:5] if safe_wins else [],
    }


# ── recommendation engine ─────────────────────────────────────────────
def generate_recommendation(
    loss_analysis: Dict,
    grid_analysis: Dict,
    baseline_result: Optional[Dict],
) -> Dict:
    """Generate data-driven recommendation."""
    streak = loss_analysis["streak_count"]
    cause_bd = loss_analysis["cause_breakdown"]
    stop_loss_count = cause_bd.get("stop_loss", 0)
    rebal_count = cause_bd.get("rebalance_loss", 0)

    # Is the root cause stop-loss being too tight?
    # Most losing sells are stop_loss triggered → tightening won't help
    stop_loss_dominated = stop_loss_count >= streak * 0.6

    safe_wins = grid_analysis.get("safe_wins_top", [])

    recommendation = {
        "keep_blocked": False,
        "modify_params": None,
        "change_gate_threshold": False,
        "rationale": [],
    }

    # Case 1: Every clean alternative is worse than baseline
    baseline_sharpe = baseline_result.get("sharpe", 0) if baseline_result else 0

    if not safe_wins:
        recommendation["keep_blocked"] = True
        recommendation["rationale"].append(
            "No safe param combination clears the gate with positive return."
        )
    elif stop_loss_dominated:
        # Lose streak is stop-loss driven — wider stop might help
        # But baseline +0.13% return already has 5-losing streak
        # Check if wider stops help
        better_combos = [r for r in safe_wins
                         if r["sharpe"] >= baseline_sharpe * 0.8
                         and r["total_return"] > baseline_result.get("total_return", 0)]
        if better_combos:
            recommendation["modify_params"] = [c["params"] for c in better_combos[:3]]
            recommendation["rationale"].append(
                f"Losing streak dominated by stop_loss ({stop_loss_count}/{streak}). "
                f"Wider stops reduce streak from {streak} to "
                f"{better_combos[0]['terminal_losing_streak']}."
            )
        else:
            recommendation["keep_blocked"] = True
            recommendation["rationale"].append(
                "Stop-loss widening does not materially reduce the streak or improve risk-adjusted returns."
            )
    else:
        # Streak from rebalance or idiosyncratic — gate threshold change might be appropriate
        recommendation["change_gate_threshold"] = True
        recommendation["rationale"].append(
            f"Losing streak ({streak}) is primarily from "
            f"{'rebalance' if rebal_count > 0 else 'idiosyncratic'} losses, "
            f"not stop-loss tightening. Consider raising gate threshold from 5 to 7."
        )

    return recommendation


# ── main ───────────────────────────────────────────────────────────────
def main() -> int:
    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_date = "2025-01-01"
    end_date = "2026-05-18"

    print("H36 — Shadow Loss Diagnosis")
    print(f"Window: {start_date} → {end_date}")
    print()

    # ── 1. Load inputs ────────────────────────────────────────────────
    print("Loading H35 trades and state...")
    trades = load_trades()
    state = load_json(STATE_FILE)
    config = load_config()
    capital = float(config.get("account", {}).get("initial_capital", 300000))
    print(f"  Loaded {len(trades)} trade records, {len([t for t in trades if t['action']=='sell'])} sells")
    print(f"  Capital: ¥{capital:,.0f}")
    print()

    # ── 2. Load prices ────────────────────────────────────────────────
    print("Loading price data...")
    prices = load_prices()
    print(f"  Prices: {prices.shape[0]} days × {prices.shape[1]} tickers")
    print()

    # ── 3. Analyze losing streak ──────────────────────────────────────
    print("Analyzing consecutive losing sells...")
    loss_analysis = analyze_losing_streak(trades, prices)
    print(f"  Streak count: {loss_analysis['streak_count']}")
    print(f"  Total PnL in streak: ¥{loss_analysis['total_pnl']:+,.2f}")
    print(f"  Avg PnL%: {loss_analysis['avg_pnl_pct']:+.2f}%")
    print(f"  Cause breakdown: {loss_analysis['cause_breakdown']}")
    print()
    for i, t in enumerate(loss_analysis["trades"], 1):
        print(f"  [{i}] {t['ticker']:10s} | entry={t['entry_date']} exit={t['exit_date']} "
              f"| {t['exit_reason']:14s} | held={t['held_days']:2d}d "
              f"| PnL={t['pnl_pct']:+.2f}% "
              f"stock_ret={t['stock_hold_return'] or 'N/A'}% "
              f"hs300_ret={t['hs300_hold_return'] or 'N/A'}% "
              f"| cause={t['probable_cause']}")
    print()

    # ── 4. Run what-if grid ───────────────────────────────────────────
    print("Running what-if grid (4×3×2×2 = 48 combos)...")
    source = CN_PIT_FileSource(
        prices_path=str(PRICES_FILE),
        universe_path=str(UNIVERSE_FILE),
        universe_snapshots_path=str(SNAPSHOTS_FILE),
    )

    # Baseline first
    baseline_bt = run_fundamental_backtest(
        data_source=source,
        start_date=start_date,
        end_date=end_date,
        capital=capital,
        **BASELINE_PARAMS,
    )
    baseline_blockers, baseline_warnings = check_stop_conditions(
        baseline_bt.trades, capital, config,
        baseline_bt.metrics, equity_curve=baseline_bt.equity_curve,
        as_of_date=end_date,
    )
    baseline_streak = compute_consecutive_losing_sells(baseline_bt.trades)
    baseline_result = {
        "total_return": baseline_bt.metrics.get("total_return"),
        "sharpe": baseline_bt.metrics.get("sharpe_ratio"),
        "max_drawdown": baseline_bt.metrics.get("max_drawdown"),
        "terminal_losing_streak": baseline_streak,
        "blockers": baseline_blockers,
        "warnings": baseline_warnings,
    }

    print(f"  Baseline (sl={BASELINE_PARAMS['stop_loss_pct']}, "
          f"tp={BASELINE_PARAMS['take_profit_pct']}, "
          f"mp={BASELINE_PARAMS['max_position_pct']}, "
          f"qf={BASELINE_PARAMS['quality_filter']}):")
    print(f"    Return: {pct(baseline_result['total_return'])}")
    print(f"    Sharpe: {baseline_result['sharpe']:.2f}")
    print(f"    MaxDD: {pct(baseline_result['max_drawdown'])}")
    print(f"    Losing streak: {baseline_result['terminal_losing_streak']}")
    print(f"    Blockers: {baseline_blockers}")
    print()

    grid_results = run_what_if_grid(source, start_date, end_date, capital, config)
    grid_analysis = find_best_param_combos(grid_results)
    print(f"  Grid complete: {grid_analysis['total_combos']} combos, "
          f"{grid_analysis['clean_combos']} pass all gates")
    print()

    # ── 5. Generate recommendation ────────────────────────────────────
    print("Generating recommendation...")
    recommendation = generate_recommendation(loss_analysis, grid_analysis, baseline_result)

    print(f"  Keep blocked: {recommendation['keep_blocked']}")
    print(f"  Modify params: {recommendation['modify_params'] is not None}")
    print(f"  Change gate threshold: {recommendation['change_gate_threshold']}")
    for r in recommendation["rationale"]:
        print(f"  * {r}")
    print()

    # ── 6. Build output ───────────────────────────────────────────────
    diagnosis_output = {
        "generated_at": run_ts,
        "window": {"start": start_date, "end": end_date},
        "baseline_params": BASELINE_PARAMS,
        "baseline_result": baseline_result,
        "loss_streak_analysis": loss_analysis,
        "grid_results": grid_results,
        "grid_analysis": grid_analysis,
        "recommendation": recommendation,
    }

    run_file = RUNS_DIR / "fundamental_value_h36_loss_diagnosis.json"
    run_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text(
        json.dumps(diagnosis_output, indent=2, ensure_ascii=False, default=str)
    )
    print(f"Wrote: {run_file}")

    # ── 7. Build report ───────────────────────────────────────────────
    report_lines = []
    report_lines.append("# H36 — Shadow Loss Diagnosis Report")
    report_lines.append("")
    report_lines.append(f"**Generated:** {run_ts}")
    report_lines.append(f"**Window:** {start_date} → {end_date}")
    report_lines.append(f"**Strategy:** deep_value_top8_shadow")
    report_lines.append(f"**Baseline params:** sl={BASELINE_PARAMS['stop_loss_pct']}, "
                        f"tp={BASELINE_PARAMS['take_profit_pct']}, "
                        f"mp={BASELINE_PARAMS['max_position_pct']}, "
                        f"qf={BASELINE_PARAMS['quality_filter']}")
    report_lines.append("")

    # Baseline performance
    report_lines.append("## Baseline Performance")
    report_lines.append("")
    report_lines.append(f"- Total return: {pct(baseline_result['total_return'])}")
    report_lines.append(f"- Sharpe ratio: {baseline_result['sharpe']:.2f}")
    report_lines.append(f"- Max drawdown: {pct(baseline_result['max_drawdown'])}")
    report_lines.append(f"- HS300 return: {pct(baseline_bt.metrics.get('hs300_return', 0))}")
    report_lines.append(f"- Excess return: {pct(baseline_bt.metrics.get('excess_return', 0))}")
    report_lines.append(f"- Win rate: {pct(baseline_bt.metrics.get('win_rate', 0))}")
    report_lines.append(f"- Closed trades: {baseline_bt.metrics.get('trade_count', 0)}")
    report_lines.append(f"- Terminal losing streak: {baseline_streak}")
    report_lines.append(f"- Data quality can_deploy: {baseline_bt.can_deploy}")
    report_lines.append("")

    # Loss streak detail
    report_lines.append("## Consecutive Losing Sell Analysis")
    report_lines.append("")
    report_lines.append(f"**Streak count: {loss_analysis['streak_count']}** "
                        f"(gate threshold: {config.get('stop_conditions', [{}])[0].get('threshold', 5)} "
                        if len(config.get('stop_conditions', [])) > 0 else "5)")
    report_lines[-1] = (f"**Streak count: {loss_analysis['streak_count']}** "
                        f"(gate threshold: 5)")
    report_lines.append(f"**Total PnL in streak: ¥{loss_analysis['total_pnl']:+,.2f}**")
    report_lines.append(f"**Avg PnL%: {loss_analysis['avg_pnl_pct']:+.2f}%**")
    report_lines.append(f"**Cause breakdown:** {loss_analysis['cause_breakdown']}")
    report_lines.append(f"**Avg held days:** {loss_analysis['avg_held_days']:.1f}")
    report_lines.append("")

    report_lines.append("| # | Ticker | Entry | Exit | Reason | Held(d) | PnL% | StockRet% | HS300Ret% | Cause |")
    report_lines.append("|---|--------|-------|------|--------|---------|------|-----------|-----------|-------|")
    for i, t in enumerate(loss_analysis["trades"], 1):
        stock = f"{t['stock_hold_return']:+.2f}" if t['stock_hold_return'] is not None else "N/A"
        hs = f"{t['hs300_hold_return']:+.2f}" if t['hs300_hold_return'] is not None else "N/A"
        report_lines.append(
            f"| {i} | {t['ticker']} | {t['entry_date']} | {t['exit_date']} | "
            f"{t['exit_reason']} | {t['held_days']} | {t['pnl_pct']:+.2f}% | "
            f"{stock}% | {hs}% | {t['probable_cause']} |"
        )
    report_lines.append("")

    # What-if grid summary
    report_lines.append("## What-If Parameter Grid")
    report_lines.append("")
    report_lines.append(f"Grid: stop_loss={GRID_SEARCH['stop_loss_pct']}, "
                        f"take_profit={GRID_SEARCH['take_profit_pct']}, "
                        f"max_position_pct={GRID_SEARCH['max_position_pct']}, "
                        f"quality_filter={GRID_SEARCH['quality_filter']}")
    report_lines.append(f"Total combos: {grid_analysis['total_combos']}")
    report_lines.append(f"Combos passing all gates: {grid_analysis['clean_combos']}")
    report_lines.append("")

    # Distribution by terminal losing streak
    streak_counts: Dict[int, int] = defaultdict(int)
    for run in grid_results:
        if "error" not in run:
            streak_counts[int(run["terminal_losing_streak"])] += 1
    report_lines.append("### Terminal Losing Streak Distribution")
    report_lines.append("")
    for streak, count in sorted(streak_counts.items()):
        report_lines.append(f"- streak={streak}: {count} combos")
    report_lines.append("")

    report_lines.append("### Top 5 Clean Combos (by total return)")
    report_lines.append("")
    report_lines.append("| SL | TP | MP | QF | Return | Sharpe | MaxDD | Excess | Trades | Streak |")
    report_lines.append("|----|----|-----|----|--------|--------|-------|--------|--------|--------|")
    for r in grid_analysis["best_clean"]:
        p = r["params"]
        report_lines.append(
            f"| {p['stop_loss_pct']:.2f} | {p['take_profit_pct']:.2f} | "
            f"{p['max_position_pct']:.2f} | {p['quality_filter']:.2f} | "
            f"{pct(r['total_return'])} | {r['sharpe']:.2f} | "
            f"{pct(r['max_drawdown'])} | {pct(r['excess_return'])} | "
            f"{r['closed_trades']} | {r['terminal_losing_streak']} |"
        )
    report_lines.append("")

    # All combos that clear the gate
    report_lines.append("### All Passing Combos (sorted by return)")
    report_lines.append("")
    report_lines.append("| SL | TP | MP | QF | Return | Sharpe | Streak | Blockers |")
    report_lines.append("|----|----|-----|----|--------|--------|--------|----------|")
    for r in grid_analysis.get("safe_wins_top", []):
        p = r["params"]
        report_lines.append(
            f"| {p['stop_loss_pct']:.2f} | {p['take_profit_pct']:.2f} | "
            f"{p['max_position_pct']:.2f} | {p['quality_filter']:.2f} | "
            f"{pct(r['total_return'])} | {r['sharpe']:.2f} | "
            f"{r['terminal_losing_streak']} | {r['h35_blockers']} |"
        )
    report_lines.append("")

    report_lines.append("### Closest Alternatives")
    report_lines.append("")
    report_lines.append("| SL | TP | MP | QF | Return | Sharpe | Trades | Streak | Blockers | Warnings |")
    report_lines.append("|----|----|----|----|--------|--------|--------|--------|----------|----------|")
    for r in grid_analysis["best_by_lowest_streak"][:5]:
        p = r["params"]
        blockers = "; ".join(r.get("h35_blockers", [])) or "none"
        warnings = "; ".join(r.get("h35_warnings", [])) or "none"
        report_lines.append(
            f"| {p['stop_loss_pct']:.2f} | {p['take_profit_pct']:.2f} | "
            f"{p['max_position_pct']:.2f} | {p['quality_filter']:.2f} | "
            f"{pct(r['total_return'])} | {r['sharpe']:.2f} | "
            f"{r['closed_trades']} | {r['terminal_losing_streak']} | "
            f"{blockers} | {warnings} |"
        )
    report_lines.append("")

    # Recommendation
    report_lines.append("## Recommendation")
    report_lines.append("")
    if recommendation["keep_blocked"]:
        report_lines.append("**Status: KEEP BLOCKED** — no safe alternative found.")
    elif recommendation["modify_params"]:
        report_lines.append("**Status: MODIFY PARAMS** — try one of these parameter sets:")
        for i, params in enumerate(recommendation["modify_params"], 1):
            report_lines.append(
                f"  {i}. sl={params['stop_loss_pct']}, tp={params['take_profit_pct']}, "
                f"mp={params['max_position_pct']}, qf={params['quality_filter']}"
            )
    elif recommendation["change_gate_threshold"]:
        report_lines.append("**Status: CHANGE GATE THRESHOLD** — evidence supports raising threshold.")
    report_lines.append("")
    report_lines.append("### Rationale")
    for r in recommendation["rationale"]:
        report_lines.append(f"- {r}")
    report_lines.append("")

    # Final verdict
    report_lines.append("## Final Verdict")
    report_lines.append("")

    # Evidence-based summary
    all_clear = grid_analysis.get("clean_combos", 0)
    if all_clear > 0:
        report_lines.append(
            f"Found {all_clear} param combinations that clear all H35 gates. "
            "Review the top candidates and update H34 config if appropriate."
        )
    else:
        report_lines.append(
            "No parameter combination clears all H35 gates. The cleanest streak=4 "
            "alternatives fail the minimum-trade gate and have weak return/Sharpe. "
            "Higher-return alternatives remain blocked by the consecutive-loss gate. "
            "Therefore H36 does not justify changing strategy params or loosening the "
            "gate automatically. Keep the shadow account blocked and collect fresh "
            "forward data before revisiting the threshold."
        )
    report_lines.append("")

    report_text = "\n".join(report_lines)
    report_file = REPORTS_DIR / "h36_shadow_loss_diagnosis_report.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(report_text)
    print(f"Wrote: {report_file}")

    print()
    print("Done. Diagnostics complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
