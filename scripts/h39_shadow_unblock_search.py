#!/usr/bin/env python3
"""H39 — non-destructive shadow strategy unblock search.

Searches entry/risk overlays for the H38 shadow strategy without modifying
H34 config or any canonical H35-H38 artifacts.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    COMMISSION_RATE,
    DataQuality,
    HS300_TICKER,
    MIN_TRADE_COUNT,
    MIN_TRADING_DAYS,
    SLIPPAGE_BPS,
    STAMP_TAX_RATE,
    TRANSFER_FEE_RATE,
    ValueScore,
)
from h35_shadow_account_executor import (  # noqa: E402
    check_stop_conditions,
    compute_annualized_turnover,
    compute_consecutive_losing_sells,
    compute_monthly_one_way_turnover,
)

DEFAULT_START = "2025-01-01"
DEFAULT_END = "2026-05-21"
DEFAULT_CAPITAL = 300000
DEFAULT_PRICES = PROJECT_ROOT / "data/cn_pit/prices_h38_candidate.csv"
DEFAULT_UNIVERSE = PROJECT_ROOT / "data/cn_pit/universe_h30_candidate.jsonl"
DEFAULT_SNAPSHOTS = PROJECT_ROOT / "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
DEFAULT_CONFIG = PROJECT_ROOT / "value_account/h34_shadow_account_config.json"
RUN_OUT = PROJECT_ROOT / "backtest/runs/fundamental_value_h39_unblock_search.json"
REPORT_OUT = PROJECT_ROOT / "reports/h39_shadow_unblock_search_report.md"


@dataclass(frozen=True)
class Overlay:
    name: str
    mom20_min: Optional[float] = None
    mom60_min: Optional[float] = None
    ma_window: Optional[int] = None
    vol20_max: Optional[float] = None
    market_ma_window: Optional[int] = None
    market_ret20_min: Optional[float] = None


@dataclass(frozen=True)
class Params:
    top_n: int
    max_position_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    quality_filter: float
    rebalance_freq_days: int


BASELINE_PARAMS = Params(
    top_n=8,
    max_position_pct=0.08,
    stop_loss_pct=0.08,
    take_profit_pct=0.22,
    quality_filter=0.30,
    rebalance_freq_days=63,
)


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.2f}%"


def plain_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def finite(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def json_safe(obj):
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return finite(obj)


def build_overlays() -> List[Overlay]:
    overlays = [Overlay("none")]
    overlays += [Overlay(f"mom20_ge_{int(t * 100)}", mom20_min=t) for t in [-0.05, 0.0, 0.03]]
    overlays += [Overlay(f"mom60_ge_{int(t * 100)}", mom60_min=t) for t in [-0.10, -0.05, 0.0]]
    overlays += [Overlay("price_gt_ma20", ma_window=20), Overlay("price_gt_ma60", ma_window=60)]
    overlays += [Overlay(f"vol20_le_{int(t * 100)}", vol20_max=t) for t in [0.03, 0.04, 0.05]]
    overlays += [
        Overlay("hs300_gt_ma60", market_ma_window=60),
        Overlay("hs300_ret20_ge_0", market_ret20_min=0.0),
        Overlay("mom20_ge_0_and_ma60", mom20_min=0.0, ma_window=60),
        Overlay("mom60_ge_-5_and_vol20_le_4", mom60_min=-0.05, vol20_max=0.04),
        Overlay("hs300_ma60_and_mom20_ge_-5", market_ma_window=60, mom20_min=-0.05),
        Overlay("hs300_ma60_and_price_gt_ma60", market_ma_window=60, ma_window=60),
        Overlay("vol20_le_4_and_price_gt_ma60", vol20_max=0.04, ma_window=60),
    ]
    return overlays


def build_param_grid() -> List[Params]:
    grid = []
    for top_n, max_pos, sl, tp, qf, rebalance in itertools.product(
        [5, 6, 8],
        [0.05, 0.06, 0.08],
        [0.08, 0.10, 0.12],
        [0.18, 0.22, 0.25],
        [0.30, 0.35, 0.40, 0.45],
        [63, 84, 126],
    ):
        grid.append(Params(top_n, max_pos, sl, tp, qf, rebalance))
    return grid


def row_value(prices: pd.DataFrame, idx: int, ticker: str) -> Optional[float]:
    if ticker not in prices.columns:
        return None
    value = prices.iloc[idx].get(ticker)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def trailing_return(prices: pd.DataFrame, idx: int, ticker: str, window: int) -> Optional[float]:
    if idx < window or ticker not in prices.columns:
        return None
    current = row_value(prices, idx, ticker)
    past = row_value(prices, idx - window, ticker)
    if current is None or past is None or past <= 0:
        return None
    return current / past - 1.0


def moving_average(prices: pd.DataFrame, idx: int, ticker: str, window: int) -> Optional[float]:
    if idx + 1 < window or ticker not in prices.columns:
        return None
    series = prices[ticker].iloc[idx + 1 - window:idx + 1].dropna()
    if len(series) < window:
        return None
    return float(series.mean())


def vol20(prices: pd.DataFrame, idx: int, ticker: str) -> Optional[float]:
    if idx < 20 or ticker not in prices.columns:
        return None
    returns = prices[ticker].pct_change(fill_method=None).iloc[idx + 1 - 20:idx + 1].dropna()
    if len(returns) < 15:
        return None
    return float(returns.std())


def passes_overlay(prices: pd.DataFrame, idx: int, ticker: str, overlay: Overlay) -> bool:
    current = row_value(prices, idx, ticker)
    if current is None:
        return False

    if overlay.mom20_min is not None:
        ret = trailing_return(prices, idx, ticker, 20)
        if ret is None or ret < overlay.mom20_min:
            return False
    if overlay.mom60_min is not None:
        ret = trailing_return(prices, idx, ticker, 60)
        if ret is None or ret < overlay.mom60_min:
            return False
    if overlay.ma_window is not None:
        ma = moving_average(prices, idx, ticker, overlay.ma_window)
        if ma is None or current <= ma:
            return False
    if overlay.vol20_max is not None:
        vol = vol20(prices, idx, ticker)
        if vol is None or vol > overlay.vol20_max:
            return False
    if overlay.market_ma_window is not None:
        market = row_value(prices, idx, HS300_TICKER)
        ma = moving_average(prices, idx, HS300_TICKER, overlay.market_ma_window)
        if market is None or ma is None or market <= ma:
            return False
    if overlay.market_ret20_min is not None:
        ret = trailing_return(prices, idx, HS300_TICKER, 20)
        if ret is None or ret < overlay.market_ret20_min:
            return False
    return True


def deploy_blockers(data_source: CN_PIT_FileSource, prices: pd.DataFrame, start: str, end: str,
                    n_days: int, n_sells: int, total_ret: float, sharpe: float) -> Tuple[bool, List[str], Dict]:
    dq: DataQuality = data_source.data_quality_for_period(start, end)
    blockers = []
    if dq.survivorship_bias:
        blockers.append("data_quality: survivorship_bias=true")
    if dq.future_function:
        blockers.append("data_quality: future_function=true")
    if dq.filing_delay:
        blockers.append("data_quality: filing_delay=true")
    if dq.ungated_fundamentals:
        blockers.append("data_quality: ungated_fundamentals=true")
    if data_source.research_only:
        blockers.append("research_only: no deployment permitted")
    coverage = data_source.price_data_coverage_for_period(start, end)
    if not coverage.get("ok"):
        blockers.append(f"price_coverage: {coverage.get('reason', 'coverage_failed')}")
    if n_days < MIN_TRADING_DAYS:
        blockers.append(f"insufficient_trading_days: {n_days} < {MIN_TRADING_DAYS}")
    if n_sells < MIN_TRADE_COUNT:
        blockers.append(f"insufficient_trades: {n_sells} < {MIN_TRADE_COUNT}")
    if total_ret < 0:
        blockers.append(f"negative_total_return: {total_ret*100:.2f}%")
    if sharpe < 0:
        blockers.append(f"negative_sharpe: {sharpe:.2f}")
    return len(blockers) == 0, blockers, {"data_quality": dq.to_dict(), "price_coverage": coverage}


def run_overlay_backtest(
    data_source: CN_PIT_FileSource,
    prices: pd.DataFrame,
    start: str,
    end: str,
    capital: float,
    params: Params,
    overlay: Overlay,
    config: Dict,
    return_details: bool = False,
) -> Dict:
    trading_dates = prices.index
    cash = capital
    positions: Dict[str, Dict] = {}
    trades: List[Dict] = []
    equity = []
    total_fees = 0.0
    total_slippage = 0.0
    last_rebalance_idx = -999
    hs300_base = None
    hs300_last = None

    for idx, dt in enumerate(trading_dates):
        date_str = dt.strftime("%Y-%m-%d")
        day_prices = prices.iloc[idx]
        hs300_val = day_prices.get(HS300_TICKER)
        if hs300_val is not None and not (isinstance(hs300_val, float) and math.isnan(hs300_val)):
            hs300_last = float(hs300_val)
            if hs300_base is None:
                hs300_base = float(hs300_val)

        for ticker in list(positions):
            pos = positions[ticker]
            px = day_prices.get(ticker, pos["avg_cost"])
            if isinstance(px, float) and math.isnan(px):
                continue
            ret = px / pos["avg_cost"] - 1
            held_days = idx - pos["entry_idx"]
            if ret >= params.take_profit_pct or ret <= -params.stop_loss_pct or held_days >= 252:
                shares = pos["shares"]
                amount = px * shares
                commission = max(amount * COMMISSION_RATE, 5)
                stamp_tax = amount * STAMP_TAX_RATE
                transfer_fee = amount * TRANSFER_FEE_RATE
                slippage = amount * SLIPPAGE_BPS / 10000
                net = amount - commission - stamp_tax - transfer_fee - slippage
                pnl = net - pos["avg_cost"] * shares
                cash += net
                total_fees += commission + stamp_tax + transfer_fee
                total_slippage += slippage
                trades.append({
                    "date": date_str, "action": "sell", "ticker": ticker,
                    "price": float(px), "shares": shares, "amount": float(amount),
                    "pnl": float(pnl), "pnl_pct": float(ret * 100),
                    "commission": float(commission), "stamp_tax": float(stamp_tax),
                    "slippage": float(slippage),
                    "exit_reason": "tp" if ret >= params.take_profit_pct else ("sl" if ret <= -params.stop_loss_pct else "time"),
                    "held_days": held_days,
                })
                del positions[ticker]

        if idx - last_rebalance_idx >= params.rebalance_freq_days:
            live_universe = data_source.get_universe(date_str)
            scoped = [t for t in live_universe if t in prices.columns]
            fundamentals = data_source.get_fundamentals(scoped, date_str)
            scores = []
            for ticker in scoped:
                if ticker in positions:
                    continue
                if not passes_overlay(prices, idx, ticker, overlay):
                    continue
                score = ValueScore.from_fundamentals(ticker, fundamentals)
                if score and score.total >= params.quality_filter:
                    scores.append(score)
            scores.sort(key=lambda x: x.total, reverse=True)
            target_tickers = [score.ticker for score in scores[:params.top_n]]

            for ticker in list(positions):
                if ticker not in target_tickers:
                    pos = positions[ticker]
                    px = day_prices.get(ticker, pos["avg_cost"])
                    if isinstance(px, float) and math.isnan(px):
                        px = pos["avg_cost"]
                    shares = pos["shares"]
                    amount = px * shares
                    commission = max(amount * COMMISSION_RATE, 5)
                    stamp_tax = amount * STAMP_TAX_RATE
                    transfer_fee = amount * TRANSFER_FEE_RATE
                    slippage = amount * SLIPPAGE_BPS / 10000
                    net = amount - commission - stamp_tax - transfer_fee - slippage
                    pnl = net - pos["avg_cost"] * shares
                    cash += net
                    total_fees += commission + stamp_tax + transfer_fee
                    total_slippage += slippage
                    trades.append({
                        "date": date_str, "action": "sell", "ticker": ticker,
                        "price": float(px), "shares": shares, "amount": float(amount),
                        "pnl": float(pnl), "pnl_pct": float(pnl / (pos["avg_cost"] * shares) * 100),
                        "commission": float(commission), "stamp_tax": float(stamp_tax),
                        "slippage": float(slippage), "exit_reason": "rebalance_out",
                        "held_days": idx - pos["entry_idx"],
                    })
                    del positions[ticker]

            n_slots = params.top_n - len(positions)
            budget_per_slot = cash / max(n_slots, 1) if n_slots > 0 else 0
            for ticker in target_tickers:
                if ticker in positions:
                    continue
                px = day_prices.get(ticker)
                if px is None or (isinstance(px, float) and math.isnan(px)):
                    continue
                target_amount = min(capital * params.max_position_pct, budget_per_slot)
                if target_amount <= 0 or cash < target_amount:
                    continue
                shares = int(target_amount / px / 100) * 100
                if shares <= 0:
                    continue
                cost = px * shares
                commission = max(cost * COMMISSION_RATE, 5)
                transfer_fee = cost * TRANSFER_FEE_RATE
                slippage = cost * SLIPPAGE_BPS / 10000
                total_cost = cost + commission + transfer_fee + slippage
                if total_cost > cash:
                    continue
                cash -= total_cost
                total_fees += commission + transfer_fee
                total_slippage += slippage
                positions[ticker] = {"shares": shares, "avg_cost": float(px), "entry_idx": idx, "buy_date": date_str}
                trades.append({
                    "date": date_str, "action": "buy", "ticker": ticker,
                    "price": float(px), "shares": shares, "amount": float(cost),
                    "commission": float(commission), "transfer_fee": float(transfer_fee),
                    "slippage": float(slippage), "total_cost": float(total_cost),
                })
            last_rebalance_idx = idx

        total_value = cash
        for ticker, pos in positions.items():
            px = day_prices.get(ticker, pos["avg_cost"])
            total_value += pos["shares"] * (px if not isinstance(px, float) or not math.isnan(px) else pos["avg_cost"])
        equity.append(float(total_value))

    eq = pd.Series(equity, index=trading_dates)
    returns = eq.pct_change().dropna()
    n_days = len(eq)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1 if n_days else 0
    annual_ret = ((1 + total_ret) ** (252 / n_days) - 1) if n_days else 0
    volatility = float(returns.std() * math.sqrt(252)) if len(returns) > 1 else 0
    sharpe = float(returns.mean() / returns.std() * math.sqrt(252)) if len(returns) > 1 and returns.std() > 0 else 0
    max_dd = float(((eq - eq.expanding().max()) / eq.expanding().max()).min())
    hs300_ret = hs300_last / hs300_base - 1 if hs300_base and hs300_last else 0
    sells = [t for t in trades if t["action"] == "sell"]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    losses = [t for t in sells if t.get("pnl", 0) <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    win_rate = len(wins) / len(sells) if sells else 0

    can_deploy, db, dq_meta = deploy_blockers(data_source, prices, start, end, n_days, len(sells), total_ret, sharpe)
    metrics = {
        "total_return": float(total_ret),
        "annual_return": float(annual_ret),
        "volatility": float(volatility),
        "sharpe_ratio": float(sharpe),
        "max_drawdown": float(max_dd),
        "hs300_return": float(hs300_ret),
        "excess_return": float(total_ret - hs300_ret),
        "trade_count": len(sells),
        "buy_count": len([t for t in trades if t["action"] == "buy"]),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "total_fees": float(total_fees),
        "total_slippage": float(total_slippage),
        "trading_days": n_days,
    }
    blockers, warnings = check_stop_conditions(
        trades, capital, load_json(DEFAULT_CONFIG), metrics, equity_curve=eq, as_of_date=end
    )
    blockers.extend(f"data_quality:{b}" for b in db)
    streak = compute_consecutive_losing_sells(trades)
    monthly_turnover = compute_monthly_one_way_turnover(trades, capital, as_of_date=end)
    annualized_turnover = compute_annualized_turnover(trades, capital)

    result = {
        "params": asdict(params),
        "overlay": asdict(overlay),
        "metrics": metrics,
        "can_deploy_data_quality": can_deploy,
        "data_quality_meta": dq_meta,
        "execution_blocked": len(blockers) > 0,
        "execution_blockers": blockers,
        "execution_warnings": warnings,
        "terminal_losing_streak": streak,
        "monthly_one_way_turnover": float(monthly_turnover),
        "annualized_turnover": float(annualized_turnover),
        "last_8_sells": sells[-8:],
    }
    if return_details:
        result["trades"] = trades
        result["equity_curve"] = [
            {"date": dt.strftime("%Y-%m-%d"), "value": float(value)}
            for dt, value in eq.items()
        ]
    return result


def score_candidate(result: Dict) -> Tuple:
    m = result["metrics"]
    blocked = 1 if result["execution_blocked"] else 0
    trade_penalty = max(0, 30 - m["trade_count"])
    warning_count = len(result["execution_warnings"])
    return (
        blocked,
        trade_penalty,
        result["terminal_losing_streak"],
        warning_count,
        -m["sharpe_ratio"],
        -m["total_return"],
    )


def run_search(args) -> Dict:
    config = load_json(DEFAULT_CONFIG)
    source = CN_PIT_FileSource(
        prices_path=str(args.prices_file),
        universe_path=str(args.universe_file),
        universe_snapshots_path=str(args.snapshots_file),
    )
    universe = source.get_price_universe(args.start, args.end)
    prices = source.get_price_history(list(universe) + [HS300_TICKER], args.start, args.end)

    overlays = build_overlays()
    baseline = run_overlay_backtest(source, prices, args.start, args.end, args.capital, BASELINE_PARAMS, Overlay("none"), config)

    print(f"Stage A: screening {len(overlays)} overlays with baseline params...")
    stage_a = []
    for overlay in overlays:
        result = run_overlay_backtest(source, prices, args.start, args.end, args.capital, BASELINE_PARAMS, overlay, config)
        stage_a.append(result)
        print(
            f"  {overlay.name:30s} ret={result['metrics']['total_return']*100:+6.2f}% "
            f"sharpe={result['metrics']['sharpe_ratio']:.2f} "
            f"streak={result['terminal_losing_streak']} "
            f"blocked={result['execution_blocked']}"
        )

    stage_a_ranked = sorted(stage_a, key=score_candidate)
    low_streak_ranked = sorted(
        stage_a,
        key=lambda r: (
            r["terminal_losing_streak"],
            max(0, 30 - r["metrics"]["trade_count"]),
            -r["metrics"]["sharpe_ratio"],
            -r["metrics"]["total_return"],
        ),
    )
    high_quality_ranked = sorted(
        stage_a,
        key=lambda r: (
            r["execution_blocked"],
            -r["metrics"]["sharpe_ratio"],
            -r["metrics"]["total_return"],
            r["terminal_losing_streak"],
        ),
    )
    selected_overlay_names = []
    for result in stage_a_ranked + low_streak_ranked + high_quality_ranked:
        name = result["overlay"]["name"]
        if name not in selected_overlay_names:
            selected_overlay_names.append(name)
        if len(selected_overlay_names) >= args.top_overlays:
            break
    selected_overlays = [overlay for overlay in overlays if overlay.name in selected_overlay_names]

    grid = build_param_grid()
    if args.max_param_combos and args.max_param_combos < len(grid):
        grid = grid[:args.max_param_combos]

    print(f"Stage B: running {len(grid)} param combos across {len(selected_overlays)} overlays...")
    results = []
    total = len(grid) * len(selected_overlays)
    count = 0
    for overlay in selected_overlays:
        for params in grid:
            count += 1
            result = run_overlay_backtest(source, prices, args.start, args.end, args.capital, params, overlay, config)
            results.append(result)
            if count % 250 == 0 or count == total:
                print(f"  progress {count}/{total}")

    all_results = stage_a + results
    clean = [
        r for r in all_results
        if not r["execution_blocked"]
        and r["metrics"]["total_return"] > 0
        and r["metrics"]["sharpe_ratio"] >= 1.0
        and r["metrics"]["trade_count"] >= 30
    ]
    clean.sort(key=lambda r: (r["metrics"]["sharpe_ratio"], r["metrics"]["total_return"]), reverse=True)
    least_bad = sorted(all_results, key=score_candidate)[:20]
    top_by_return = sorted(all_results, key=lambda r: r["metrics"]["total_return"], reverse=True)[:20]
    top_by_sharpe = sorted(all_results, key=lambda r: r["metrics"]["sharpe_ratio"], reverse=True)[:20]

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task": "H39",
        "window": {"start": args.start, "end": args.end},
        "inputs": {
            "prices_file": str(args.prices_file),
            "universe_file": str(args.universe_file),
            "snapshots_file": str(args.snapshots_file),
            "config": str(DEFAULT_CONFIG),
        },
        "baseline": baseline,
        "stage_a_count": len(stage_a),
        "stage_b_count": len(results),
        "selected_overlays": selected_overlay_names,
        "candidate_found": len(clean) > 0,
        "clean_candidate_count": len(clean),
        "top_clean_candidates": clean[:20],
        "least_bad": least_bad,
        "top_by_return": top_by_return,
        "top_by_sharpe": top_by_sharpe,
    }


def candidate_row(result: Dict) -> str:
    p = result["params"]
    o = result["overlay"]
    m = result["metrics"]
    blockers = "; ".join(result["execution_blockers"]) if result["execution_blockers"] else "none"
    warnings = "; ".join(result["execution_warnings"]) if result["execution_warnings"] else "none"
    return (
        f"| {o['name']} | {p['top_n']} | {p['max_position_pct']:.2f} | "
        f"{p['stop_loss_pct']:.2f} | {p['take_profit_pct']:.2f} | "
        f"{p['quality_filter']:.2f} | {p['rebalance_freq_days']} | "
        f"{pct(m['total_return'])} | {m['sharpe_ratio']:.2f} | "
        f"{pct(m['max_drawdown'])} | {m['trade_count']} | "
        f"{result['terminal_losing_streak']} | {result['annualized_turnover']:.2f}x | "
        f"{blockers} | {warnings} |"
    )


def build_report(payload: Dict) -> str:
    baseline = payload["baseline"]
    bm = baseline["metrics"]
    status = "CANDIDATE_FOUND" if payload["candidate_found"] else "KEEP_BLOCKED"
    lines = [
        "# H39 — Shadow Strategy Unblock Search Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Status:** {status}",
        f"**Window:** {payload['window']['start']} -> {payload['window']['end']}",
        "",
        "## Baseline H38",
        "",
        f"- Total return: {pct(bm['total_return'])}",
        f"- Sharpe: {bm['sharpe_ratio']:.2f}",
        f"- Max drawdown: {pct(bm['max_drawdown'])}",
        f"- HS300 return: {pct(bm['hs300_return'])}",
        f"- Excess return: {pct(bm['excess_return'])}",
        f"- Closed sells: {bm['trade_count']}",
        f"- Terminal losing streak: {baseline['terminal_losing_streak']}",
        f"- Monthly turnover: {plain_pct(baseline['monthly_one_way_turnover'])}",
        f"- Annualized turnover: {baseline['annualized_turnover']:.2f}x",
        f"- Blockers: {', '.join(baseline['execution_blockers']) if baseline['execution_blockers'] else 'none'}",
        f"- Warnings: {', '.join(baseline['execution_warnings']) if baseline['execution_warnings'] else 'none'}",
        "",
        "## Search Summary",
        "",
        f"- Stage A overlays screened: {payload['stage_a_count']}",
        f"- Stage B runs: {payload['stage_b_count']}",
        f"- Selected overlays: {', '.join(payload['selected_overlays'])}",
        f"- Clean candidates: {payload['clean_candidate_count']}",
        "",
    ]

    table_header = (
        "| Overlay | top_n | max_pos | SL | TP | QF | Rebal | Return | Sharpe | MaxDD | "
        "Trades | Streak | AnnTurn | Blockers | Warnings |\n"
        "|---------|-------|---------|----|----|----|-------|--------|--------|-------|"
        "--------|--------|---------|----------|----------|"
    )
    if payload["top_clean_candidates"]:
        lines.extend(["## Top Clean Candidates", "", table_header])
        lines.extend(candidate_row(r) for r in payload["top_clean_candidates"][:10])
        lines.append("")
    else:
        lines.extend([
            "## Top Clean Candidates",
            "",
            "No candidate cleared all H34/H38 execution gates while keeping positive return, Sharpe >= 1.0, and at least 30 closed sells.",
            "",
        ])

    lines.extend(["## Least-Bad Alternatives", "", table_header])
    lines.extend(candidate_row(r) for r in payload["least_bad"][:10])
    lines.append("")

    lines.extend(["## Best Return Candidates", "", table_header])
    lines.extend(candidate_row(r) for r in payload["top_by_return"][:10])
    lines.append("")

    lines.extend(["## Last 8 Closed Sells For Best Alternative", ""])
    best = payload["top_clean_candidates"][0] if payload["top_clean_candidates"] else payload["least_bad"][0]
    lines.append(f"Selected: overlay `{best['overlay']['name']}`, params `{best['params']}`")
    lines.append("")
    lines.append("| Date | Ticker | Reason | PnL% | Held(d) |")
    lines.append("|------|--------|--------|------|---------|")
    for trade in best["last_8_sells"]:
        lines.append(
            f"| {trade.get('date')} | {trade.get('ticker')} | {trade.get('exit_reason')} | "
            f"{trade.get('pnl_pct', 0):+.2f}% | {trade.get('held_days', '')} |"
        )
    lines.append("")

    if payload["candidate_found"]:
        best = payload["top_clean_candidates"][0]
        lines.extend([
            "## Verdict",
            "",
            "**CANDIDATE_FOUND** — at least one dry-run candidate clears the current execution blocker without changing the H34 gate thresholds.",
            "",
            "Do not promote automatically. Next step: rerun H31/H33-style robustness and execution audit on the recommended candidate, then run a forward-only shadow period.",
            "",
            f"Recommended dry-run candidate: `{best['overlay']['name']}` with params `{best['params']}`.",
        ])
    else:
        lines.extend([
            "## Verdict",
            "",
            "**KEEP_BLOCKED** — the tested overlays did not produce a deployable shadow candidate under the existing H34 gates.",
            "",
            "Next step: inspect the least-bad alternatives and consider a larger model change rather than parameter tuning, such as a different value-score formula or explicit sector/industry risk control.",
        ])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="H39 shadow strategy unblock search")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--prices-file", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--universe-file", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--snapshots-file", type=Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--top-overlays", type=int, default=8)
    parser.add_argument("--max-param-combos", type=int, default=0,
                        help="Optional cap for param grid size; 0 means full grid.")
    args = parser.parse_args()

    print("H39 — Shadow Strategy Unblock Search")
    print(f"Window: {args.start} -> {args.end}")
    payload = run_search(args)

    RUN_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    RUN_OUT.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2))
    REPORT_OUT.write_text(build_report(payload))

    print(f"Wrote: {RUN_OUT}")
    print(f"Wrote: {REPORT_OUT}")
    print(f"Verdict: {'CANDIDATE_FOUND' if payload['candidate_found'] else 'KEEP_BLOCKED'}")
    print(f"Clean candidates: {payload['clean_candidate_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
