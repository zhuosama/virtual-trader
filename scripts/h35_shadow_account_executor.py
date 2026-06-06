#!/usr/bin/env python3
"""H35 — Shadow/Paper Account Executor for deep_value_top8_shadow.

Runs the deep_value_top8_shadow strategy using CN_PIT_FileSource, computes
paper-only trades, applies stop conditions (max drawdown, monthly loss,
consecutive losing sells, turnover warn/block), and writes results to
value_account/ directories.  Non-destructive — never touches production
strategies/active.json or accounts/.

Outputs (relative to PROJECT_ROOT):
  - value_account/logs/h35_shadow_trades.jsonl
  - value_account/reports/h35_shadow_state.json
  - value_account/reports/h35_shadow_daily_report.md
  - backtest/runs/fundamental_value_h35_shadow_run.json

Gates:
  - Config mode must be "paper"
  - auto_live_orders must be false
  - Data quality deploy blockers prevent trading
  - Max drawdown stop (-12%)
  - Monthly loss stop (-8%)
  - Consecutive losing sells (>=5)
  - Monthly turnover warn (>40%) / block (>75%)
  - Annualized turnover warn (>1.5x) / block (>3.0x)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
RUNS_DIR = PROJECT_ROOT / "backtest" / "runs"
CONFIG_PATH = PROJECT_ROOT / "value_account" / "h34_shadow_account_config.json"

sys.path.insert(0, str(EXPERIMENTS_DIR))

from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    run_fundamental_backtest,
    BacktestResult,
)

# ── defaults ───────────────────────────────────────────────────────────
DEFAULT_PRICES = "data/cn_pit/prices_h30_candidate.csv"
DEFAULT_UNIVERSE = "data/cn_pit/universe_h30_candidate.jsonl"
DEFAULT_SNAPSHOTS = "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
DEFAULT_TRADE_LOG = "value_account/logs/h35_shadow_trades.jsonl"
DEFAULT_STATE_FILE = "value_account/reports/h35_shadow_state.json"
DEFAULT_REPORT_FILE = "value_account/reports/h35_shadow_daily_report.md"
DEFAULT_RUN_FILE = "backtest/runs/fundamental_value_h35_shadow_run.json"


# ── helpers ────────────────────────────────────────────────────────────
def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def plain_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def load_config(path: Path = CONFIG_PATH) -> Dict:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return json.loads(path.read_text())


def validate_config(cfg: Dict) -> None:
    """Raise if config mode is not paper or auto_live_orders is true."""
    acct = cfg.get("account", {})
    if acct.get("mode") != "paper":
        raise RuntimeError(
            f"Config mode must be 'paper', got '{acct.get('mode')}'. "
            "Shadow account executor refuses non-paper mode."
        )
    if acct.get("auto_live_orders", False):
        raise RuntimeError(
            "auto_live_orders must be false for shadow account execution."
        )


def compute_monthly_one_way_turnover(
    trades: List[Dict],
    capital: float,
    as_of_date: Optional[str] = None,
) -> float:
    """Trailing 30-day one-way turnover = sum(|amount|)/2/capital for last 30 days.

    Matches H33 convention: all trade amounts (buy+sell) summed, then /2 for one-way.
    """
    if as_of_date is None:
        dated_trades = [str(t.get("date", "")) for t in trades if t.get("date")]
        as_of_date = max(dated_trades) if dated_trades else datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    total_amount = sum(
        abs(float(t.get("amount", 0)))
        for t in trades
        if cutoff <= str(t.get("date", "")) <= as_of_date
    )
    return total_amount / 2.0 / capital if capital > 0 else 0.0


def compute_annualized_turnover(trades: List[Dict], capital: float) -> float:
    """Trailing annualized one-way turnover = avg monthly (all trades/2/capital) * 12."""
    by_month: Dict[str, float] = defaultdict(float)
    for t in trades:
        month = str(t.get("date", ""))[:7]
        if month:
            by_month[month] += abs(float(t.get("amount", 0)))
    if not by_month:
        return 0.0
    avg_monthly_one_way = sum(v / 2.0 / capital for v in by_month.values()) / len(by_month)
    return avg_monthly_one_way * 12.0


def compute_consecutive_losing_sells(trades: List[Dict]) -> int:
    """Count consecutive losing closed trades, ignoring trailing buy records."""
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


def worst_monthly_return(equity_curve: Optional["pd.Series"]) -> Optional[float]:
    """Return worst calendar-month return from an equity curve."""
    if equity_curve is None or len(equity_curve) == 0:
        return None
    returns = []
    for _, values in equity_curve.groupby(equity_curve.index.to_period("M")):
        if values.empty:
            continue
        start_value = float(values.iloc[0])
        end_value = float(values.iloc[-1])
        if start_value > 0:
            returns.append(end_value / start_value - 1.0)
    return min(returns) if returns else None


# ── gate checks ────────────────────────────────────────────────────────
def check_stop_conditions(
    trades: List[Dict],
    capital: float,
    cfg: Dict,
    backtest_metrics: Dict,
    equity_curve: Optional["pd.Series"] = None,
    as_of_date: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Return (blockers, warnings) from stop conditions and turnover gates.

    Uses H34 config thresholds from the config file.
    Drawdown is taken from the equity curve (most accurate) or backtest
    metrics if equity_curve is not available.
    """
    blockers: List[str] = []
    warnings: List[str] = []
    sc = cfg.get("stop_conditions", [])
    tc = cfg.get("caps", {}).get("turnover", {})

    # ── max drawdown (from equity curve if available, else from metrics) ─
    dd_threshold = None
    for sc_item in sc:
        if sc_item.get("id") == "max_drawdown":
            dd_threshold = sc_item.get("threshold")
            break
    if dd_threshold is not None:
        if equity_curve is not None and len(equity_curve) > 0:
            eq = equity_curve
            peak = eq.expanding().max()
            dd = float(((eq - peak) / peak).min())
            if dd <= dd_threshold:
                blockers.append(f"max_drawdown:{plain_pct(dd)} <= {plain_pct(dd_threshold)}")
        else:
            dd = backtest_metrics.get("max_drawdown", 0)
            if dd <= dd_threshold:
                blockers.append(f"max_drawdown:{plain_pct(dd)} <= {plain_pct(dd_threshold)}")

    # ── monthly loss ───────────────────────────────────────────────────
    ml_threshold = None
    for sc_item in sc:
        if sc_item.get("id") == "monthly_loss":
            ml_threshold = sc_item.get("threshold")
            break
    if ml_threshold is not None:
        month_ret = worst_monthly_return(equity_curve)
        if month_ret is None:
            month_ret = backtest_metrics.get("total_return", 0)
        if month_ret <= ml_threshold:
            blockers.append(
                f"monthly_loss:{plain_pct(month_ret)} <= {plain_pct(ml_threshold)}"
            )

    # ── consecutive losing sells ───────────────────────────────────────
    consec_threshold = None
    for sc_item in sc:
        if sc_item.get("id") == "consecutive_losers":
            consec_threshold = sc_item.get("threshold")
            break
    if consec_threshold is not None:
        streak = compute_consecutive_losing_sells(trades)
        if streak >= consec_threshold:
            blockers.append(f"consecutive_losing_sells:{streak} >= {consec_threshold}")

    # ── monthly turnover warn / block ──────────────────────────────────
    monthly_max = tc.get("monthly_one_way_max_pct", 0.75)
    monthly_warn = tc.get("monthly_one_way_warn_pct", 0.40)
    monthly_turn = compute_monthly_one_way_turnover(trades, capital, as_of_date=as_of_date)
    if monthly_turn > monthly_max:
        blockers.append(f"monthly_turnover:{plain_pct(monthly_turn)} > {plain_pct(monthly_max)} (block)")
    elif monthly_turn > monthly_warn:
        warnings.append(f"monthly_turnover:{plain_pct(monthly_turn)} > {plain_pct(monthly_warn)} (warn)")

    # ── annualized turnover warn / block ───────────────────────────────
    ann_max = tc.get("annualized_max_x", 3.0)
    ann_warn = tc.get("annualized_warn_x", 1.5)
    ann_turn = compute_annualized_turnover(trades, capital)
    if ann_turn > ann_max:
        blockers.append(f"annualized_turnover:{ann_turn:.2f}x > {ann_max:.1f}x (block)")
    elif ann_turn > ann_warn:
        warnings.append(f"annualized_turnover:{ann_turn:.2f}x > {ann_warn:.1f}x (warn)")

    return blockers, warnings


# ── report generation ──────────────────────────────────────────────────
def build_daily_report(
    bt: BacktestResult,
    cfg: Dict,
    blockers: List[str],
    warnings: List[str],
    start: str,
    end: str,
    run_ts: str,
    dry_run: bool,
) -> str:
    m = bt.metrics
    p = cfg.get("strategy", {}).get("params", {})
    lines = [
        "# H35 — Shadow Account Daily Report",
        "",
        f"**Generated:** {run_ts}",
        f"**Mode:** {'DRY RUN' if dry_run else 'PAPER'}",
        f"**Window:** {start} → {end}",
        f"**Strategy:** deep_value_top8_shadow",
        "",
        "## Parameters",
        "",
        f"- top_n: {p.get('top_n', '?')}",
        f"- max_position_pct: {p.get('max_position_pct', '?')}",
        f"- stop_loss_pct: {p.get('stop_loss_pct', '?')}",
        f"- take_profit_pct: {p.get('take_profit_pct', '?')}",
        f"- quality_filter: {p.get('quality_filter', '?')}",
        f"- capital (from config): ¥{cfg.get('account', {}).get('initial_capital', '?'):,}",
        "",
        "## Performance",
        "",
        f"- Total return: {pct(m.get('total_return', 0))}",
        f"- Annual return: {pct(m.get('annual_return', 0))}",
        f"- Sharpe ratio: {m.get('sharpe_ratio', 0):.2f}",
        f"- Max drawdown: {pct(m.get('max_drawdown', 0))}",
        f"- HS300 return: {pct(m.get('hs300_return', 0))}",
        f"- Excess return: {pct(m.get('excess_return', 0))}",
        f"- Win rate: {pct(m.get('win_rate', 0))}",
        f"- Profit factor: {m.get('profit_factor', 0):.2f}",
        f"- Closed trades: {m.get('trade_count', 0)}",
        f"- Trading days: {m.get('trading_days', 0)}",
        "",
        "## Gates",
        "",
        f"- Data quality can_deploy: {bt.can_deploy}",
        f"- Execution blocked: {len(blockers) > 0}",
    ]
    if blockers:
        lines.append("- Blockers:")
        for b in blockers:
            lines.append(f"  - {b}")
    if warnings:
        lines.append("- Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")
    if not blockers and not warnings:
        lines.append("- All gates passed: no blockers, no warnings.")
    lines.extend([
        "",
        "## Decision",
        "",
    ])
    if dry_run:
        lines.append("**DRY RUN** — no files written. Report generated for preview only.")
    elif blockers:
        lines.append("**BLOCKED** — stop conditions or turnover limits prevent trading.")
    else:
        lines.append("**PASS** — shadow trading is allowed. Trades and state have been logged.")
    lines.append("")
    return "\n".join(lines)


def build_state(bt: BacktestResult, cfg: Dict, blockers: List[str], warnings: List[str],
                start: str, end: str, run_ts: str, dry_run: bool) -> Dict:
    m = bt.metrics
    return {
        "generated_at": run_ts,
        "dry_run": dry_run,
        "window": {"start": start, "end": end},
        "strategy_id": "deep_value_top8_shadow",
        "config_source": str(CONFIG_PATH),
        "capital": cfg.get("account", {}).get("initial_capital"),
        "performance": {
            "total_return": m.get("total_return"),
            "annual_return": m.get("annual_return"),
            "sharpe_ratio": m.get("sharpe_ratio"),
            "max_drawdown": m.get("max_drawdown"),
            "hs300_return": m.get("hs300_return"),
            "excess_return": m.get("excess_return"),
            "win_rate": m.get("win_rate"),
            "profit_factor": m.get("profit_factor"),
            "trade_count": m.get("trade_count"),
            "trading_days": m.get("trading_days"),
            "data_quality": m.get("data_quality"),
        },
        "gates": {
            "can_deploy": bt.can_deploy,
            "deploy_blockers": list(bt.deploy_blockers),
            "execution_blocked": len(blockers) > 0,
            "execution_blockers": blockers,
            "execution_warnings": warnings,
        },
        "status": "blocked" if blockers else ("dry_run" if dry_run else "active"),
    }


def write_trades_jsonl(trades: List[Dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")


def write_state(state: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str))


def write_report(report: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report)


# ── main ───────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="H35 Shadow Account Executor — paper-only deep_value_top8_shadow"
    )
    parser.add_argument("--start", default="2025-01-01",
                        help="Backtest start date (default: 2025-01-01)")
    parser.add_argument("--end", default="2026-05-18",
                        help="Backtest end date (default: 2026-05-18)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview report only; do not write output files.")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--prices-file", default=DEFAULT_PRICES)
    parser.add_argument("--universe-file", default=DEFAULT_UNIVERSE)
    parser.add_argument("--snapshots-file", default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--trade-log", default=DEFAULT_TRADE_LOG)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--report-file", default=DEFAULT_REPORT_FILE)
    parser.add_argument("--run-file", default=DEFAULT_RUN_FILE)
    args = parser.parse_args()

    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 1. Load and validate config ────────────────────────────────────
    cfg = load_config(resolve_path(args.config))
    validate_config(cfg)
    capital = float(cfg.get("account", {}).get("initial_capital", 300000))
    strategy_params = deepcopy(cfg.get("strategy", {}).get("params", {}))
    # Ensure capital is passed (not in config's params directly)
    strategy_params.pop("capital", None)  # not a param for run_fundamental_backtest

    # ── 2. Run backtest via CN_PIT_FileSource ──────────────────────────
    source = CN_PIT_FileSource(
        prices_path=str(resolve_path(args.prices_file)),
        universe_path=str(resolve_path(args.universe_file)),
        universe_snapshots_path=str(resolve_path(args.snapshots_file)),
    )
    bt = run_fundamental_backtest(
        data_source=source,
        start_date=args.start,
        end_date=args.end,
        capital=capital,
        **strategy_params,
    )

    # ── 3. Check stop conditions and turnover gates ────────────────────
    blockers, warnings = check_stop_conditions(
        bt.trades, capital, cfg, bt.metrics, equity_curve=bt.equity_curve,
        as_of_date=args.end,
    )
    # Also include data-quality deploy_blockers from the backtest engine
    for db in bt.deploy_blockers:
        blockers.append(f"data_quality:{db}")

    # ── 4. Build outputs ───────────────────────────────────────────────
    report_text = build_daily_report(bt, cfg, blockers, warnings,
                                     args.start, args.end, run_ts, args.dry_run)
    state = build_state(bt, cfg, blockers, warnings,
                        args.start, args.end, run_ts, args.dry_run)

    # Run file (aggregate backtest run record)
    run_record = {
        "generated_at": run_ts,
        "dry_run": args.dry_run,
        "config_source": "H34",
        "window": {"start": args.start, "end": args.end},
        "strategy": "deep_value_top8_shadow",
        "params": strategy_params,
        "capital": capital,
        "backtest_metrics": bt.metrics,
        "can_deploy": bt.can_deploy,
        "deploy_blockers": bt.deploy_blockers,
        "execution_blockers": blockers,
        "execution_warnings": warnings,
        "state": state,
        "trade_count": len(bt.trades),
    }

    # ── 5. Output — skip file writes on dry-run ──────────────────────
    if not args.dry_run:
        trade_log_path = resolve_path(args.trade_log)
        state_file_path = resolve_path(args.state_file)
        report_file_path = resolve_path(args.report_file)
        run_file_path = resolve_path(args.run_file)

        write_trades_jsonl(bt.trades, trade_log_path)
        write_state(state, state_file_path)
        write_report(report_text, report_file_path)
        write_state(run_record, run_file_path)

        print(f"Wrote: {trade_log_path}")
        print(f"Wrote: {state_file_path}")
        print(f"Wrote: {report_file_path}")
        print(f"Wrote: {run_file_path}")
    else:
        print("[DRY-RUN] No files written.")

    # ── 6. Print key metrics ──────────────────────────────────────────
    print(f"")
    print(f"╔══════════════════════════════════════════════╗")
    print(f"║  H35 Shadow Account — deep_value_top8_shadow  ║")
    print(f"╚══════════════════════════════════════════════╝")
    print(f"  Window:          {args.start} → {args.end}")
    print(f"  Capital:         ¥{capital:,.0f}")
    print(f"  Mode:            {'DRY-RUN' if args.dry_run else 'PAPER'}")
    print(f"  Total return:    {pct(bt.metrics.get('total_return', 0))}")
    print(f"  Sharpe:          {bt.metrics.get('sharpe_ratio', 0):.2f}")
    print(f"  MaxDD:           {pct(bt.metrics.get('max_drawdown', 0))}")
    print(f"  HS300 return:    {pct(bt.metrics.get('hs300_return', 0))}")
    print(f"  Excess return:   {pct(bt.metrics.get('excess_return', 0))}")
    print(f"  Win rate:        {pct(bt.metrics.get('win_rate', 0))}")
    print(f"  Trade count:     {bt.metrics.get('trade_count', 0)}")
    print(f"  Data quality OK: {bt.can_deploy}")
    if blockers:
        print(f"  ⛔ Blocked: {len(blockers)} blocker(s)")
        for b in blockers:
            print(f"      - {b}")
    else:
        print(f"  ✅ No blockers — gates clear")
    if warnings:
        for w in warnings:
            print(f"  ⚠  {w}")
    print(f"")

    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
