#!/usr/bin/env python3
"""H33 — execution realism audit for PIT value candidates.

Audits trade-level execution feasibility for a selected value strategy:
- one-way monthly turnover
- trade participation vs daily traded value
- concentration from reconstructed positions
- simple incremental impact-cost estimate

The script is non-destructive. It writes H33 artifacts only and never promotes
candidate data files.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
RUNS_DIR = PROJECT_ROOT / "backtest" / "runs"
REPORTS_DIR = PROJECT_ROOT / "reports"
DATA_DIR = PROJECT_ROOT / "data" / "cn_pit"

sys.path.insert(0, str(EXPERIMENTS_DIR))

from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    VALUE_STRATEGY_VARIANTS,
    run_fundamental_backtest,
)


DEFAULT_PRICES = "data/cn_pit/prices_h30_candidate.csv"
DEFAULT_UNIVERSE = "data/cn_pit/universe_h30_candidate.jsonl"
DEFAULT_SNAPSHOTS = "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
DEFAULT_LIQUIDITY_CACHE = "data/cn_pit/liquidity_h33_daily_amount.csv"
DEFAULT_OUTPUT = "backtest/runs/fundamental_value_h33_execution_audit.json"
DEFAULT_REPORT = "reports/h33_execution_audit_report.md"

CAPITAL = 500000.0
WARN_PARTICIPATION = 0.05
BLOCK_PARTICIPATION = 0.10
BLOCK_MISSING_LIQUIDITY = True
BLOCK_MONTHLY_TURNOVER = 1.00
WARN_MONTHLY_TURNOVER = 0.50
BLOCK_ANNUAL_TURNOVER = 4.00
WARN_ANNUAL_TURNOVER = 2.00
BLOCK_SINGLE_POSITION = 0.25
WARN_SINGLE_POSITION = 0.18
BLOCK_TOP3_POSITION = 0.60
WARN_TOP3_POSITION = 0.50
BASE_SLIPPAGE_BPS = 5.0


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def ts_code_from_ticker(ticker: str) -> str:
    if ticker.endswith(".SS"):
        return ticker.replace(".SS", ".SH")
    return ticker


def ticker_from_ts_code(ts_code: str) -> str:
    if ts_code.endswith(".SH"):
        return ts_code.replace(".SH", ".SS")
    return ts_code


def get_tushare_token() -> str:
    token = subprocess.check_output(["launchctl", "getenv", "TUSHARE_TOKEN"], text=True).strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set in launchctl")
    return token


def fetch_tushare_liquidity(tickers: Iterable[str], start: str, end: str) -> pd.DataFrame:
    import tushare as ts

    token = get_tushare_token()
    pro = ts.pro_api(token)
    rows = []
    start_raw = start.replace("-", "")
    end_raw = end.replace("-", "")
    for ticker in sorted(set(tickers)):
        ts_code = ts_code_from_ticker(ticker)
        print(f"[liquidity] fetching {ts_code}")
        df = pro.daily(ts_code=ts_code, start_date=start_raw, end_date=end_raw)
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            trade_date = str(row.get("trade_date", ""))
            if len(trade_date) != 8:
                continue
            amount_k = row.get("amount")
            if pd.isna(amount_k):
                continue
            rows.append({
                "date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
                "ticker": ticker_from_ts_code(str(row.get("ts_code") or ts_code)),
                "amount_rmb": float(amount_k) * 1000.0,
                "source": "tushare:daily.amount",
            })
    return pd.DataFrame(rows)


def load_or_fetch_liquidity(
    cache_path: Path,
    tickers: Iterable[str],
    start: str,
    end: str,
    fetch: bool,
) -> pd.DataFrame:
    required = set(tickers)
    if cache_path.exists():
        cached = pd.read_csv(cache_path)
        if {"date", "ticker", "amount_rmb"}.issubset(cached.columns):
            have = set(cached["ticker"].dropna().astype(str))
            if required <= have or not fetch:
                return cached
    if not fetch:
        return pd.DataFrame(columns=["date", "ticker", "amount_rmb", "source"])
    fetched = fetch_tushare_liquidity(required, start, end)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fetched.to_csv(cache_path, index=False)
    return fetched


def reconstruct_positions(trades: List[Dict], prices: pd.DataFrame, equity_curve: pd.Series) -> Dict:
    positions: Dict[str, int] = defaultdict(int)
    trades_by_date: Dict[str, List[Dict]] = defaultdict(list)
    for trade in trades:
        trades_by_date[trade["date"]].append(trade)

    max_single = 0.0
    max_top3 = 0.0
    max_single_date = ""
    max_top3_date = ""
    daily_snapshots = []

    for dt, row in prices.iterrows():
        date_str = dt.strftime("%Y-%m-%d")
        for trade in trades_by_date.get(date_str, []):
            shares = int(trade.get("shares", 0))
            if trade.get("action") == "buy":
                positions[trade["ticker"]] += shares
            elif trade.get("action") == "sell":
                positions[trade["ticker"]] -= shares
                if positions[trade["ticker"]] <= 0:
                    positions.pop(trade["ticker"], None)

        values = {}
        for ticker, shares in positions.items():
            if ticker not in row.index or pd.isna(row[ticker]):
                continue
            values[ticker] = float(row[ticker]) * shares
        if dt in equity_curve.index:
            total = float(equity_curve.loc[dt])
        else:
            total = sum(values.values())
        if total <= 0:
            continue
        weights = sorted((v / total for v in values.values()), reverse=True)
        single = weights[0] if weights else 0.0
        top3 = sum(weights[:3])
        if single > max_single:
            max_single = single
            max_single_date = date_str
        if top3 > max_top3:
            max_top3 = top3
            max_top3_date = date_str
        daily_snapshots.append({
            "date": date_str,
            "position_count": len(values),
            "single_max": single,
            "top3_weight": top3,
        })

    return {
        "max_single_position": max_single,
        "max_single_position_date": max_single_date,
        "max_top3_position": max_top3,
        "max_top3_position_date": max_top3_date,
        "daily_snapshots": daily_snapshots,
    }


def monthly_turnover(trades: List[Dict], equity_curve: pd.Series) -> Dict:
    by_month: Dict[str, float] = defaultdict(float)
    for trade in trades:
        month = str(trade["date"])[:7]
        by_month[month] += float(trade.get("amount", 0.0))
    months = []
    max_turnover = 0.0
    max_month = ""
    for month, amount in sorted(by_month.items()):
        turnover = amount / 2.0 / CAPITAL
        months.append({"month": month, "trade_amount": amount, "turnover": turnover})
        if turnover > max_turnover:
            max_turnover = turnover
            max_month = month
    avg_monthly = sum(m["turnover"] for m in months) / len(months) if months else 0.0
    return {
        "months": months,
        "max_monthly_turnover": max_turnover,
        "max_month": max_month,
        "total_turnover": sum(by_month.values()) / 2.0 / CAPITAL,
        "annualized_turnover": avg_monthly * 12.0,
    }


def extra_impact_bps(participation: Optional[float]) -> float:
    if participation is None:
        return 0.0
    if participation <= 0.01:
        return 0.0
    if participation <= 0.05:
        return 5.0
    if participation <= 0.10:
        return 20.0
    return 50.0


def audit_liquidity(trades: List[Dict], liquidity: pd.DataFrame) -> Dict:
    if liquidity.empty:
        lookup: Dict[Tuple[str, str], float] = {}
    else:
        lookup = {
            (str(row["date"]), str(row["ticker"])): float(row["amount_rmb"])
            for _, row in liquidity.iterrows()
            if pd.notna(row.get("amount_rmb"))
        }

    audited = []
    missing = []
    high = []
    extra_cost = 0.0
    max_participation = 0.0
    max_participation_trade = None
    for trade in trades:
        if trade.get("action") not in {"buy", "sell"}:
            continue
        date = str(trade["date"])
        ticker = str(trade["ticker"])
        trade_amount = float(trade.get("amount", 0.0))
        daily_amount = lookup.get((date, ticker))
        participation = None
        if daily_amount and daily_amount > 0:
            participation = trade_amount / daily_amount
            if participation > max_participation:
                max_participation = participation
                max_participation_trade = {"date": date, "ticker": ticker, "participation": participation}
            if participation > WARN_PARTICIPATION:
                high.append({"date": date, "ticker": ticker, "participation": participation})
            impact_bps = extra_impact_bps(participation)
            extra_cost += trade_amount * impact_bps / 10000.0
        else:
            missing.append({"date": date, "ticker": ticker, "amount": trade_amount})
        audited.append({
            "date": date,
            "action": trade.get("action"),
            "ticker": ticker,
            "amount": trade_amount,
            "daily_amount_rmb": daily_amount,
            "participation": participation,
        })

    return {
        "trade_count": len(audited),
        "missing_liquidity_count": len(missing),
        "missing_liquidity": missing[:50],
        "high_participation_count": len(high),
        "high_participation": high[:50],
        "max_participation": max_participation,
        "max_participation_trade": max_participation_trade,
        "extra_impact_cost": extra_cost,
        "extra_impact_return_drag": extra_cost / CAPITAL,
        "audited_trades": audited,
    }


def build_report(result: Dict) -> str:
    m = result["backtest_metrics"]
    liq = result["liquidity"]
    turn = result["turnover"]
    conc = result["concentration"]
    lines = [
        "# H33 — Execution Realism Audit",
        "",
        f"**Generated:** {result['generated_at']}",
        "",
        "## Scope",
        "",
        f"- Strategy: `{result['strategy']}`",
        f"- Window: `{result['start']}` -> `{result['end']}`",
        f"- Liquidity source: `{result['liquidity_source']}`",
        "",
        "## Backtest",
        "",
        f"- Return: {pct(m['total_return'])}",
        f"- Sharpe: {m['sharpe_ratio']:.2f}",
        f"- MaxDD: {pct(m['max_drawdown'])}",
        f"- Win rate: {pct(m['win_rate'])}",
        f"- Profit factor: {m['profit_factor']:.2f}",
        f"- Closed trades: {m['trade_count']}",
        f"- Can deploy before execution audit: {m['can_deploy']}",
        "",
        "## Execution Checks",
        "",
        f"- Monthly turnover max: {pct(turn['max_monthly_turnover'])} (`{turn['max_month']}`)",
        f"- Total turnover / capital: {turn['total_turnover']:.2f}x",
        f"- Annualized turnover: {turn['annualized_turnover']:.2f}x",
        f"- Missing liquidity trades: {liq['missing_liquidity_count']}/{liq['trade_count']}",
        f"- Max participation: {pct(liq['max_participation'])}",
        f"- High participation trades (> {pct(WARN_PARTICIPATION)}): {liq['high_participation_count']}",
        f"- Estimated extra impact drag: {pct(liq['extra_impact_return_drag'])}",
        f"- Max single-position weight: {pct(conc['max_single_position'])} (`{conc['max_single_position_date']}`)",
        f"- Max top-3 position weight: {pct(conc['max_top3_position'])} (`{conc['max_top3_position_date']}`)",
        "",
        "## Gate",
        "",
        f"- Execution can deploy: {result['execution_can_deploy']}",
    ]
    if result["execution_blockers"]:
        lines.append("- Blockers:")
        for blocker in result["execution_blockers"]:
            lines.append(f"  - {blocker}")
    else:
        lines.append("- Blockers: none")
    if result["execution_warnings"]:
        lines.append("- Warnings:")
        for warning in result["execution_warnings"]:
            lines.append(f"  - {warning}")
    else:
        lines.append("- Warnings: none")
    lines.extend([
        "",
        "## Decision",
        "",
        "Execution audit is an additional gate on top of PIT data quality and backtest deployment checks. Passing H33 does not solve the OOS weakness from H32; it only says the tested trade list appears executable under the current liquidity and concentration thresholds.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="H33 execution realism audit")
    parser.add_argument("--strategy", default="deep_value_top8", choices=sorted(VALUE_STRATEGY_VARIANTS))
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-05-18")
    parser.add_argument("--prices-file", default=DEFAULT_PRICES)
    parser.add_argument("--universe-file", default=DEFAULT_UNIVERSE)
    parser.add_argument("--universe-snapshots-file", default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--liquidity-cache", default=DEFAULT_LIQUIDITY_CACHE)
    parser.add_argument("--fetch-liquidity", action="store_true")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()

    source = CN_PIT_FileSource(
        prices_path=str(resolve_path(args.prices_file)),
        universe_path=str(resolve_path(args.universe_file)),
        universe_snapshots_path=str(resolve_path(args.universe_snapshots_file)),
    )
    params = VALUE_STRATEGY_VARIANTS[args.strategy]
    backtest = run_fundamental_backtest(
        data_source=source,
        start_date=args.start,
        end_date=args.end,
        capital=CAPITAL,
        **params,
    )
    tickers = sorted({t["ticker"] for t in backtest.trades if t.get("action") in {"buy", "sell"}})
    liquidity_path = resolve_path(args.liquidity_cache)
    liquidity = load_or_fetch_liquidity(liquidity_path, tickers, args.start, args.end, args.fetch_liquidity)

    prices = source.get_price_history(tickers, args.start, args.end)
    concentration = reconstruct_positions(backtest.trades, prices, backtest.equity_curve)
    turnover = monthly_turnover(backtest.trades, backtest.equity_curve)
    liquidity_audit = audit_liquidity(backtest.trades, liquidity)

    blockers = []
    warnings = []
    if not backtest.can_deploy:
        blockers.extend(f"backtest:{b}" for b in backtest.deploy_blockers)
    if BLOCK_MISSING_LIQUIDITY and liquidity_audit["missing_liquidity_count"] > 0:
        blockers.append(f"missing_liquidity:{liquidity_audit['missing_liquidity_count']}")
    if liquidity_audit["max_participation"] > BLOCK_PARTICIPATION:
        blockers.append(f"participation>{pct(BLOCK_PARTICIPATION)}")
    elif liquidity_audit["max_participation"] > WARN_PARTICIPATION:
        warnings.append(f"participation>{pct(WARN_PARTICIPATION)}")
    if turnover["max_monthly_turnover"] > BLOCK_MONTHLY_TURNOVER:
        blockers.append(f"monthly_turnover>{pct(BLOCK_MONTHLY_TURNOVER)}")
    elif turnover["max_monthly_turnover"] > WARN_MONTHLY_TURNOVER:
        warnings.append(f"monthly_turnover>{pct(WARN_MONTHLY_TURNOVER)}")
    if turnover["annualized_turnover"] > BLOCK_ANNUAL_TURNOVER:
        blockers.append(f"annualized_turnover>{BLOCK_ANNUAL_TURNOVER:.1f}x")
    elif turnover["annualized_turnover"] > WARN_ANNUAL_TURNOVER:
        warnings.append(f"annualized_turnover>{WARN_ANNUAL_TURNOVER:.1f}x")
    if concentration["max_single_position"] > BLOCK_SINGLE_POSITION:
        blockers.append(f"single_position>{pct(BLOCK_SINGLE_POSITION)}")
    elif concentration["max_single_position"] > WARN_SINGLE_POSITION:
        warnings.append(f"single_position>{pct(WARN_SINGLE_POSITION)}")
    if concentration["max_top3_position"] > BLOCK_TOP3_POSITION:
        blockers.append(f"top3_position>{pct(BLOCK_TOP3_POSITION)}")
    elif concentration["max_top3_position"] > WARN_TOP3_POSITION:
        warnings.append(f"top3_position>{pct(WARN_TOP3_POSITION)}")

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": args.strategy,
        "start": args.start,
        "end": args.end,
        "params": params,
        "liquidity_source": str(liquidity_path),
        "backtest_metrics": backtest.metrics,
        "turnover": turnover,
        "liquidity": liquidity_audit,
        "concentration": concentration,
        "execution_can_deploy": len(blockers) == 0,
        "execution_blockers": blockers,
        "execution_warnings": warnings,
    }

    output_path = resolve_path(args.output)
    report_path = resolve_path(args.report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    report_path.write_text(build_report(result))
    print(f"Saved JSON: {output_path}")
    print(f"Saved report: {report_path}")
    print(f"Execution can deploy: {result['execution_can_deploy']}")
    if blockers:
        print("Blockers:")
        for blocker in blockers:
            print(f"  - {blocker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
