#!/usr/bin/env python3
"""H37 — Forward-Only Shadow Account Monitor.

A read-only watchdog that sits after H35 (blocked) and H36 (diagnosis,
keep blocked).  It does NOT run backtests, does NOT modify config, and
does NOT generate trades.  It answers only one question:

  "What would it take for the shadow account to unblock naturally?"

It reads:
  - H35 state/trade log (value_account/reports/h35_shadow_state.json)
  - H35 trade log (value_account/logs/h35_shadow_trades.jsonl)
  - H36 diagnosis JSON (backtest/runs/fundamental_value_h36_loss_diagnosis.json)
  - H34 config (value_account/h34_shadow_account_config.json)
  - Current PIT price file (data/cn_pit/prices_h30_candidate.csv) — optional

It produces:
  - value_account/reports/h37_forward_monitor_state.json
  - reports/h37_forward_shadow_monitor_report.md

Non-destructive — never writes to production config or accounts.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "value_account" / "h34_shadow_account_config.json"
TRADE_LOG = PROJECT_ROOT / "value_account/logs/h35_shadow_trades.jsonl"
STATE_FILE = PROJECT_ROOT / "value_account/reports/h35_shadow_state.json"
DIAGNOSIS_FILE = PROJECT_ROOT / "backtest/runs/fundamental_value_h36_loss_diagnosis.json"
PRICES_FILE = PROJECT_ROOT / "data/cn_pit/prices_h30_candidate.csv"
STATE_OUT = PROJECT_ROOT / "value_account/reports/h37_forward_monitor_state.json"
REPORT_OUT = PROJECT_ROOT / "reports/h37_forward_shadow_monitor_report.md"


# ── helpers ────────────────────────────────────────────────────────────
def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def plain_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            t = line.strip()
            if t:
                rows.append(json.loads(t))
    return rows


def parse_date(d: str) -> date:
    return date.fromisoformat(d)


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


# ── consecutive-losing-sell logic (mirrors H35) ────────────────────────
def compute_consecutive_losing_sells(trades: List[Dict]) -> int:
    """Count consecutive losing closed sells at end of trade list."""
    losing_streak = 0
    for t in reversed(trades):
        if t.get("action") != "sell":
            continue
        pnl = t.get("pnl", 0)
        if pnl is not None and pnl <= 0:
            losing_streak += 1
        else:
            break
    return losing_streak


def get_last_profitable_sell_index(trades: List[Dict]) -> Optional[int]:
    """Return index of most recent profitable sell, or None if never profitable."""
    for idx in range(len(trades) - 1, -1, -1):
        t = trades[idx]
        if t.get("action") == "sell" and t.get("pnl", 0) > 0:
            return idx
    return None


def computed_ended_losing_streak(trades: List[Dict]) -> Optional[int]:
    """
    If the terminal losing streak ended after a profitable sell (i.e., there is a
    profitable sell after the start of the streak), return the streak length.
    Otherwise None — streak is ongoing.
    """
    # Walk backwards, count consecutive losses
    losing_count = 0
    for idx in range(len(trades) - 1, -1, -1):
        t = trades[idx]
        if t.get("action") != "sell":
            continue
        pnl = t.get("pnl", 0)
        if pnl is not None and pnl <= 0:
            losing_count += 1
        else:
            return None  # Found a profitable sell — streak is not terminal
    # Never found a profitable sell
    return losing_count


# ── stale-data check ──────────────────────────────────────────────────
def days_since_last_price(prices_path: Path) -> Optional[int]:
    """Return calendar days between last price-close date and today."""
    if not prices_path.exists():
        return None
    try:
        import pandas as pd
        # Read only the date column (2KB head should contain dates)
        df = pd.read_csv(prices_path, nrows=5)
        if "date" in df.columns or "Date" in df.columns:
            date_col = "date" if "date" in df.columns else "Date"
            # Read just the date column to find last date
            dates = pd.read_csv(prices_path, usecols=[date_col])
            last_date = dates.iloc[-1, 0]
            last = parse_date(str(last_date).strip())
            today = date.today()
            return (today - last).days
    except Exception:
        pass
    return None


# ── compute what would unblock ────────────────────────────────────────
def compute_unblock_conditions(
    trades: List[Dict],
    state: Dict,
    diagnosis: Dict,
    config: Dict,
    as_of_date: str,
) -> Dict:
    """
    Determine forward-only conditions for natural unblock.
    Returns a structured dict.
    """
    # Current blockers
    gates = state.get("gates", {})
    blockers = list(gates.get("execution_blockers", []))
    warnings = list(gates.get("execution_warnings", []))

    # Find threshold from config
    consec_threshold = 5  # default
    for sc in config.get("stop_conditions", []):
        if sc.get("id") == "consecutive_losers":
            consec_threshold = sc.get("threshold", 5)
            break

    current_streak = compute_consecutive_losing_sells(trades)

    # Last profitable sell info
    last_win_idx = get_last_profitable_sell_index(trades)
    last_win_info = None
    if last_win_idx is not None:
        lt = trades[last_win_idx]
        last_win_info = {
            "ticker": lt.get("ticker", "?"),
            "exit_date": lt.get("date", "?"),
            "pnl": lt.get("pnl", 0),
            "pnl_pct": lt.get("pnl_pct", 0),
        }

    # Count losing sells since last profitable sell
    losses_since_last_win = 0
    if last_win_idx is not None:
        for idx in range(last_win_idx + 1, len(trades)):
            t = trades[idx]
            if t.get("action") == "sell" and (t.get("pnl", 0) or 0) <= 0:
                losses_since_last_win += 1
    else:
        losses_since_last_win = current_streak

    # Scenario A: Next profitable sell breaks streak
    # Number of consecutive losses that a single profitable sell would clear
    next_profitable_sell_pnl_required = None
    if current_streak >= consec_threshold:
        # Need a profitable sell, then the streak counter resets
        # But the gate checks current streak — so after 1 profitable sell,
        # the streak would be 0 (if no more losses after it)
        next_profitable_sell_pnl_required = "> 0"  # any profitable sell resets

    # Scenario B: Enough new trades dilute the streak
    # After N new trades with at most (consec_threshold - 1) losses,
    # the streak drops below threshold
    new_trades_needed = max(0, current_streak - consec_threshold + 1)

    # Scenario C: What-if analysis from H36 — stored in diagnosis
    h36_recommendation = diagnosis.get("recommendation", {})
    h36_keep_blocked = h36_recommendation.get("keep_blocked", True)
    h36_safe_params = []
    if not h36_keep_blocked:
        h36_safe_params = h36_recommendation.get("modify_params", [])

    # Count sell types in the losing streak
    streak_trades = []
    for idx in range(len(trades) - 1, -1, -1):
        t = trades[idx]
        if t.get("action") != "sell":
            continue
        if (t.get("pnl", 0) or 0) <= 0:
            streak_trades.append(t)
        else:
            break
    streak_trades.reverse()  # chronological

    exit_reasons = defaultdict(int)
    total_pnl = 0.0
    for t in streak_trades:
        exit_reasons[t.get("exit_reason", "?")] += 1
        total_pnl += float(t.get("pnl", 0))

    # Window info from state
    window = state.get("window", {"start": "", "end": ""})
    window_end = window.get("end", "")
    days_since_window_end = None
    if window_end:
        try:
            days_since_window_end = (parse_date(as_of_date) - parse_date(window_end)).days
        except ValueError:
            pass

    # ── build natural unblock conditions ──────────────────────────────
    natural_unblock_conditions = []

    # Primary: need next closed sell to be profitable (capacity bound)
    # OR fewer than 5 (consec_threshold) terminal losing sells after N new trades
    if current_streak >= consec_threshold:
        natural_unblock_conditions.append(
            f"Next closed sell from existing shadow holdings must be profitable (PnL > 0) to reset the "
            f"consecutive losing streak counter below {consec_threshold}."
        )
        natural_unblock_conditions.append(
            f"Alternatively, {new_trades_needed} new profitable closed sell "
            f"could offset the current streak of {current_streak} to bring "
            f"it below {consec_threshold}; H37 must not synthesize this event."
        )

    # Secondary: data freshness
    if days_since_window_end is not None and days_since_window_end > 5:
        natural_unblock_conditions.append(
            f"Price data window ended {days_since_window_end} days ago "
            f"({window_end}). Fresh price data is needed before any "
            f"forward trading consideration.  Update prices file to "
            f"include post-{window_end} data."
        )

    # tertiary: exit reason pattern
    if exit_reasons.get("sl", 0) >= current_streak * 0.6 and current_streak > 0:
        natural_unblock_conditions.append(
            f"Loss streak is stop-loss dominated ({exit_reasons['sl']}/{current_streak}). "
            f"Wider stop-loss or quality filter tightening may reduce "
            f"vulnerability, but no H36 grid combo cleared all gates."
        )

    natural_unblock_conditions.append(
        f"H36 diagnosis found 0/{diagnosis.get('grid_analysis', {}).get('total_combos', 0)} "
        f"parameter combinations that clear all gates: KEEP BLOCKED recommendation "
        f"remains valid pending fresh forward data."
    )

    # ── forbidden actions ─────────────────────────────────────────────
    forbidden_actions = [
        "DO NOT modify H34 shadow account config (max_drawdown, consecutive_losers, position caps)",
        "DO NOT change stop_conditions thresholds in config (consecutive_losers:5 gate stays)",
        "DO NOT synthesize paper trades from H37",
        "DO NOT open new buys while H35 remains blocked",
        "DO NOT override the blocked status",
        "DO NOT run backtest with --write to modify active strategies",
    ]

    # ── next run checklist ────────────────────────────────────────────
    next_run_checklist = [
        "1. Verify price data extends beyond 2026-05-18 (current window end)",
        "2. After data update, re-run H35 executor to recompute gate state",
        "3. If a profitable sell finally occurs in shadow mode, re-check gate",
        "4. After 5+ new trades, re-evaluate consecutive_losers streak",
        "5. If stale for 30+ days, consider archiving shadow and initiating H40 check",
    ]

    return {
        "current_streak": current_streak,
        "consecutive_threshold": consec_threshold,
        "last_profitable_sell": last_win_info,
        "losses_since_last_profitable_sell": losses_since_last_win,
        "next_profitable_sell_pnl_required": next_profitable_sell_pnl_required,
        "exit_reason_breakdown": dict(exit_reasons),
        "streak_total_pnl": round(total_pnl, 2),
        "days_since_window_end": days_since_window_end,
        "natural_unblock_conditions": natural_unblock_conditions,
        "h36_keep_blocked": h36_keep_blocked,
        "new_trades_needed_to_dilute": new_trades_needed,
    }


# ── stale data assessment ─────────────────────────────────────────────
def assess_staleness(prices_path: Path, window_end: str, as_of_date: str) -> Dict:
    """Evaluate whether the price data is stale relative to H35 window."""
    result = {
        "price_data_available": prices_path.exists(),
        "window_end": window_end,
        "days_since_window_end": None,
        "stale_warning": None,
        "severity": "ok",
    }

    if not result["price_data_available"]:
        result["stale_warning"] = "Price data file not found. Cannot assess."
        result["severity"] = "unknown"
        return result

    try:
        import pandas as pd
        df = pd.read_csv(prices_path, nrows=5)
        date_col = "date" if "date" in df.columns else "Date"
        dates = pd.read_csv(prices_path, usecols=[date_col])
        last_price_date_str = str(dates.iloc[-1, 0]).strip()
        last_price_date = parse_date(last_price_date_str)
        as_of = parse_date(as_of_date)
        days = (as_of - last_price_date).days
        result["last_price_date"] = last_price_date_str
        result["days_since_last_price"] = days

        # Staleness from window end
        if window_end:
            window_d = parse_date(window_end)
            since_window = (as_of - window_d).days
            result["days_since_window_end"] = since_window

            # Compare price data to window end
            price_days = (last_price_date - window_d).days
            result["price_data_beyond_window_days"] = price_days

            if since_window > 30:
                result["stale_warning"] = (
                    f"H35 window ended {since_window} days ago ({window_end}). "
                    f"Data is stale. Recommend archive and H40 review."
                )
                result["severity"] = "critical"
            elif since_window > 7:
                result["stale_warning"] = (
                    f"H35 window ended {since_window} days ago ({window_end}). "
                    f"Data becoming stale. Fresh prices needed before next evaluation."
                )
                result["severity"] = "warning"
            else:
                result["stale_warning"] = (
                    f"H35 window ended {since_window} day(s) ago. "
                    f"Data still fresh; no action needed."
                )
                result["severity"] = "ok"
    except Exception as exc:
        result["stale_warning"] = f"Could not parse price dates: {exc}"
        result["severity"] = "unknown"

    return result


# ── build output state ────────────────────────────────────────────────
def build_state(
    as_of_date: str,
    dry_run: bool,
    h35_state: Dict,
    trades: List[Dict],
    diagnosis: Dict,
    config: Dict,
    unblock: Dict,
    staleness: Dict,
) -> Dict:
    """Build machine-readable monitor state."""
    # Count sell types
    sells = [t for t in trades if t.get("action") == "sell"]
    buys = [t for t in trades if t.get("action") == "buy"]

    # H36 grid summary
    grid = diagnosis.get("grid_analysis", {})

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of_date": as_of_date,
        "dry_run": dry_run,
        "monitor_version": "H37",
        "inputs": {
            "h35_state": str(STATE_FILE),
            "h35_trade_log": str(TRADE_LOG),
            "h36_diagnosis": str(DIAGNOSIS_FILE),
            "h34_config": str(CONFIG_PATH),
            "price_data": str(PRICES_FILE) if PRICES_FILE.exists() else "not_found",
        },
        "blockers": [
            "consecutive_losing_sells:{} >= {}".format(
                unblock["current_streak"], unblock["consecutive_threshold"]
            )
        ] if unblock["current_streak"] >= unblock["consecutive_threshold"] else [],
        "warnings": ["annualized_turnover:>1.5x (from H35 state)"]
            if h35_state.get("gates", {}).get("execution_warnings")
            else [],
        "terminal_losing_streak": unblock["current_streak"],
        "threshold": unblock["consecutive_threshold"],
        "exit_reason_breakdown": unblock["exit_reason_breakdown"],
        "streak_total_pnl": unblock["streak_total_pnl"],
        "days_since_last_window_end": unblock["days_since_window_end"],
        "last_profitable_sell": unblock["last_profitable_sell"],
        "staleness": {
            "days_since_window_end": staleness.get("days_since_window_end"),
            "last_price_date": staleness.get("last_price_date"),
            "severity": staleness.get("severity"),
            "message": staleness.get("stale_warning"),
        },
        "unblock_assessment": {
            "natural_unblock_conditions": unblock["natural_unblock_conditions"],
            "h36_keep_blocked": unblock["h36_keep_blocked"],
            "new_trades_needed_to_dilute_streak": unblock["new_trades_needed_to_dilute"],
            "losses_since_last_profitable_sell": unblock["losses_since_last_profitable_sell"],
            "next_profitable_sell_pnl_required": unblock["next_profitable_sell_pnl_required"],
        },
        "h36_grid_summary": {
            "total_combos": grid.get("total_combos", 0),
            "clean_combos": grid.get("clean_combos", 0),
            "safe_wins_count": grid.get("safe_wins_count", 0),
        },
        "trade_summary": {
            "total_trades": len(trades),
            "total_buys": len(buys),
            "total_sells": len(sells),
            "winning_sells": sum(1 for s in sells if (s.get("pnl", 0) or 0) > 0),
            "losing_sells": sum(1 for s in sells if (s.get("pnl", 0) or 0) <= 0),
        },
        "forbidden_actions": [
            "DO NOT modify H34 shadow account config (stop_conditions, caps, params)",
            "DO NOT change consecutive_losers threshold from 5",
            "DO NOT synthesize paper trades from H37",
            "DO NOT open new buys while H35 remains blocked",
            "DO NOT run backtest with destructive write",
            "DO NOT override blocked status programmatically",
        ],
        "next_actions": [
            "1. Verify PIT price data has been updated past 2026-05-18",
            "2. Re-run H35 executor when fresh data is available",
            "3. Monitor for first profitable closed sell from existing holdings to reset losing streak",
            "4. After 5+ new round-trip trades, re-evaluate gate",
        ],
        "status": "blocked" if unblock["current_streak"] >= unblock["consecutive_threshold"] else "watch",
        "status_rationale": (
            "consecutive_losing_sells gate triggered; H36 confirmed 0/48 param combos clear all gates. "
            "KEEP BLOCKED pending forward data."
            if unblock["current_streak"] >= unblock["consecutive_threshold"]
            else "consecutive_losing_sells gate is below threshold; keep monitoring."
        ),
    }


def build_report(
    state: Dict,
    unblock: Dict,
    staleness: Dict,
    as_of_date: str,
    dry_run: bool,
    diagnosis: Dict,
    trades: List[Dict],
    h35_state: Dict,
    h36_recommendation: Dict,
) -> str:
    """Build human-readable markdown report."""
    lines = []
    lines.append("# H37 — Forward Shadow Monitor Report")
    lines.append("")
    lines.append(f"**Generated:** {state['generated_at']}")
    lines.append(f"**As-of date:** {as_of_date}")
    lines.append(f"**Mode:** {'DRY RUN' if dry_run else 'MONITOR'}")
    lines.append(f"**Status:** {state['status'].upper()}")
    lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## Current State")
    lines.append("")
    perf = h35_state.get("performance", {})
    lines.append(f"- Total return: {pct(perf.get('total_return', 0))}")
    lines.append(f"- Sharpe ratio: {perf.get('sharpe_ratio', 0):.2f}")
    lines.append(f"- Max drawdown: {pct(perf.get('max_drawdown', 0))}")
    lines.append(f"- Win rate: {pct(perf.get('win_rate', 0))}")
    lines.append(f"- Closed trades: {perf.get('trade_count', 0)}")
    lines.append(f"- Terminal losing streak: {unblock['current_streak']} " +
                 f"(threshold: {unblock['consecutive_threshold']})")
    lines.append(f"- H35 window: {h35_state.get('window', {}).get('start', '?')} → " +
                 f"{h35_state.get('window', {}).get('end', '?')}")
    lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## Blockers & Warnings")
    lines.append("")
    blockers = state.get("blockers", [])
    if blockers:
        lines.append("### Blockers")
        for b in blockers:
            lines.append(f"- ⛔ {b}")
    warnings_list = state.get("warnings", [])
    if warnings_list:
        lines.append("")
        lines.append("### Warnings")
        for w in warnings_list:
            lines.append(f"- ⚠ {w}")
    if not blockers and not warnings_list:
        lines.append("- All gates clear (should not happen in H37).")
    lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## Losing Streak Analysis")
    lines.append("")
    lines.append(f"**Terminal streak:** {unblock['current_streak']} losing sells " +
                 f"(threshold: {unblock['consecutive_threshold']})")
    lines.append(f"**Streak PnL:** ¥{unblock['streak_total_pnl']:+,.2f}")
    lines.append(f"**Exit reason breakdown:** {unblock['exit_reason_breakdown']}")
    lines.append("")
    if unblock.get("last_profitable_sell"):
        lw = unblock["last_profitable_sell"]
        pnl_pct_val = lw.get("pnl_pct", 0)
        if isinstance(pnl_pct_val, (int, float)):
            pnl_str = f"{pnl_pct_val:+.2f}%"
        else:
            pnl_str = str(pnl_pct_val)
        lines.append(f"**Last profitable sell:** {lw['ticker']} on {lw['exit_date']} " +
                     f"(PnL: {pnl_str})")
        lines.append(f"**Losses since last profitable sell:** {unblock['losses_since_last_profitable_sell']}")
    else:
        lines.append("**Last profitable sell:** None — all sells since inception have been losers.")
    lines.append("")
    lines.append("### Losing Sell Detail (chronological, terminal streak)")
    losing_streak_trades = []
    for idx in range(len(trades) - 1, -1, -1):
        t = trades[idx]
        if t.get("action") != "sell":
            continue
        pnl = t.get("pnl", 0)
        if pnl is not None and pnl <= 0:
            losing_streak_trades.append(t)
        else:
            break
    losing_streak_trades.reverse()

    if losing_streak_trades:
        lines.append("| # | Ticker | Exit Date | Exit Reason | Held(d) | PnL% |")
        lines.append("|---|--------|-----------|-------------|---------|------|")
        for i, t in enumerate(losing_streak_trades, 1):
            pnl_val = t.get("pnl_pct", 0)
            if isinstance(pnl_val, (int, float)):
                pnl_str = f"{pnl_val:+.2f}%" if abs(pnl_val) < 100 else f"{pnl_val:+.2f}%"
            else:
                pnl_str = str(pnl_val)
            lines.append(
                f"| {i} | {t.get('ticker', '?')} | {t.get('date', '?')} "
                f"| {t.get('exit_reason', '?')} | {t.get('held_days', '?')} "
                f"| {pnl_str} |"
            )
        lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## Natural Unblock Conditions")
    lines.append("")
    for cond in unblock["natural_unblock_conditions"]:
        lines.append(f"- {cond}")

    lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## Staleness Check")
    lines.append("")
    lines.append(f"- H35 window ended: {staleness.get('window_end', '?')}")
    lines.append(f"- Days since window end: {staleness.get('days_since_window_end', '?')}")
    lines.append(f"- Last price data date: {staleness.get('last_price_date', 'N/A')}")
    lines.append(f"- Severity: {staleness.get('severity', 'unknown')}")
    if staleness.get("stale_warning"):
        lines.append(f"- Warning: {staleness['stale_warning']}")
    lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## H36 Diagnosis Cross-Reference")
    lines.append("")
    grid = diagnosis.get("grid_analysis", {})
    lines.append(f"- Total param combos tested: {grid.get('total_combos', '?')}")
    lines.append(f"- Combos clearing all gates: {grid.get('clean_combos', 0)}")
    lines.append(f"- Safe wins (positive return + all gates clear): {grid.get('safe_wins_count', 0)}")
    lines.append(f"- H36 recommendation: KEEP BLOCKED = {h36_recommendation.get('keep_blocked', '?')}")
    lines.append("")
    lines.append("### Closest Alternatives (from H36)")
    closest = grid.get("best_by_lowest_streak", [])[:5]
    if closest:
        lines.append("| SL | TP | MP | QF | Return | Sharpe | Trades | Streak | Blockers |")
        lines.append("|----|----|-----|----|--------|--------|--------|--------|----------|")
        for r in closest:
            p = r.get("params", {})
            blockers_str = "; ".join(r.get("h35_blockers", [])) or "none"
            inc = r.get("total_return", 0)
            if inc is None:
                inc = 0
            lines.append(
                f"| {p.get('stop_loss_pct', '?'):.2f} | {p.get('take_profit_pct', '?'):.2f} "
                f"| {p.get('max_position_pct', '?'):.2f} | {p.get('quality_filter', '?'):.2f} "
                f"| {pct(float(inc)) if isinstance(inc, (int, float)) else '?'} "
                f"| {r.get('sharpe', 0):.2f} "
                f"| {r.get('closed_trades', 0)} "
                f"| {r.get('terminal_losing_streak', '?')} "
                f"| {blockers_str} |"
            )
    else:
        lines.append("- No closest alternatives found.")
    lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## Next Run Checklist")
    lines.append("")
    for step in state.get("next_actions", []):
        lines.append(f"- {step}")
    lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## Forbidden Actions")
    lines.append("")
    for fa in state.get("forbidden_actions", []):
        lines.append(f"- {fa}")
    lines.append("")

    # ════════════════════════════════════════════════════════════════════
    lines.append("## Trade Summary")
    lines.append("")
    ts = state.get("trade_summary", {})
    lines.append(f"- Total trades: {ts.get('total_trades', 0)}")
    lines.append(f"- Buys: {ts.get('total_buys', 0)}")
    lines.append(f"- Sells: {ts.get('total_sells', 0)}")
    lines.append(f"- Winning sells: {ts.get('winning_sells', 0)}")
    lines.append(f"- Losing sells: {ts.get('losing_sells', 0)}")
    lines.append("")

    if dry_run:
        lines.append("---")
        lines.append("**DRY RUN** — no files written. Report for preview only.")

    return "\n".join(lines)


# ── main ───────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="H37 Forward Shadow Monitor — read-only watchdog after H35/H36"
    )
    parser.add_argument("--as-of", default=None,
                        help="Override as-of date (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only; do not write output files.")
    args = parser.parse_args()

    as_of_date = args.as_of or today_str()
    dry_run = args.dry_run

    print(f"H37 — Forward Shadow Monitor")
    print(f"As-of: {as_of_date}")
    print(f"Mode: {'DRY RUN' if dry_run else 'MONITOR'}")
    print()

    # ── 1. Load all inputs ────────────────────────────────────────────
    print("Loading inputs...")
    h35_state = load_json(STATE_FILE)
    trades = load_jsonl(TRADE_LOG)
    diagnosis = load_json(DIAGNOSIS_FILE)
    config = load_json(CONFIG_PATH)

    print(f"  H35 state: {'OK' if h35_state else 'NOT FOUND'}")
    print(f"  Trade log: {len(trades)} records")
    print(f"  H36 diagnosis: {'OK' if diagnosis else 'NOT FOUND'}")
    print(f"  H34 config: {'OK' if config else 'NOT FOUND'}")
    print()

    # ── 2. Compute unblock conditions ─────────────────────────────────
    print("Analyzing forward-unblock conditions...")
    unblock = compute_unblock_conditions(trades, h35_state, diagnosis, config, as_of_date)
    print(f"  Current streak:   {unblock['current_streak']} (threshold: {unblock['consecutive_threshold']})")
    print(f"  Last profitable:  {unblock['last_profitable_sell']}")
    print(f"  Losses since win: {unblock['losses_since_last_profitable_sell']}")
    print(f"  H36 keep blocked: {unblock['h36_keep_blocked']}")
    print()

    # ── 3. Assess staleness ───────────────────────────────────────────
    print("Assessing data staleness...")
    window_end = h35_state.get("window", {}).get("end", "")
    staleness = assess_staleness(PRICES_FILE, window_end, as_of_date)
    print(f"  Price data: {staleness.get('last_price_date', 'N/A')}")
    print(f"  Days since window end: {staleness.get('days_since_window_end', '?')}")
    print(f"  Severity: {staleness.get('severity', 'N/A')}")
    print()

    # ── 4. Build state and report ─────────────────────────────────────
    print("Building monitor state...")
    monitor_state = build_state(
        as_of_date, dry_run, h35_state, trades, diagnosis, config,
        unblock, staleness,
    )
    report_text = build_report(
        monitor_state, unblock, staleness, as_of_date, dry_run,
        diagnosis, trades, h35_state,
        diagnosis.get("recommendation", {}),
    )

    # Print summary
    print(f"  Status: {monitor_state['status']}")
    print(f"  Blockers: {len(monitor_state['blockers'])}")
    print(f"  Unblock conditions: {len(monitor_state['unblock_assessment']['natural_unblock_conditions'])}")
    print(f"  Staleness: {staleness['severity']}")
    print()

    # ── 5. Write output files (unless dry-run) ─────────────────────────
    if not dry_run:
        # State file
        STATE_OUT.parent.mkdir(parents=True, exist_ok=True)
        STATE_OUT.write_text(
            json.dumps(monitor_state, indent=2, ensure_ascii=False, default=str)
        )
        print(f"Wrote: {STATE_OUT}")

        # Report file
        REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
        REPORT_OUT.write_text(report_text)
        print(f"Wrote: {REPORT_OUT}")
    else:
        print("[DRY-RUN] No files written.")
        print()
        # Print the report preview
        print("═" * 60)
        print("REPORT PREVIEW (first 30 lines)")
        print("═" * 60)
        for line in report_text.split("\n")[:30]:
            print(line)

    print()
    print("Done.")

    # Return non-zero if blocked (like H35's convention)
    return 1 if monitor_state["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
