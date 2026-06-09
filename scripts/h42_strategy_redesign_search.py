#!/usr/bin/env python3
"""H42 — Strategy Redesign Search with benchmark-relative robustness.

Expands beyond H39's grid search across 5 candidate families:
1. Benchmark-relative momentum (rel20/60/120 vs HS300)
2. Drawdown and trend quality (MA filters, distance from 60-day high)
3. Exit discipline (trailing-stop style exits)
4. Portfolio/risk throttles (top_n, max_pos, new_buys_per_rebalance)
5. Multi-window robustness evaluation

Strict acceptance gate for CANDIDATE_FOR_FORWARD_TRIAL.
No network. No production config changes.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
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

# ── Paths ──────────────────────────────────────────────────────────────
DEFAULT_PRICES = PROJECT_ROOT / "data/cn_pit/prices_h38_candidate.csv"
DEFAULT_UNIVERSE = PROJECT_ROOT / "data/cn_pit/universe_h30_candidate.jsonl"
DEFAULT_SNAPSHOTS = PROJECT_ROOT / "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
DEFAULT_CONFIG = PROJECT_ROOT / "value_account/h34_shadow_account_config.json"
RUN_OUT = PROJECT_ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json"
REPORT_OUT = PROJECT_ROOT / "reports/h42_strategy_redesign_search_report.md"

# ── Multi-window definitions ───────────────────────────────────────────
WINDOWS = {
    "cal_2024":        ("2024-01-01", "2024-12-31"),
    "h1_2025":         ("2025-01-01", "2025-06-30"),
    "h2_2025":         ("2025-07-01", "2025-12-31"),
    "ytd_2026":        ("2026-01-01", "2026-05-21"),
    "deploy_2025_2026":("2025-01-01", "2026-05-21"),
}

# ── Data types ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Overlay:
    name: str
    mom20_min: Optional[float] = None
    mom60_min: Optional[float] = None
    mom120_min: Optional[float] = None
    ma_window: Optional[int] = None
    vol20_max: Optional[float] = None
    market_ma_window: Optional[int] = None
    market_ret20_min: Optional[float] = None
    rel20_min: Optional[float] = None
    rel60_min: Optional[float] = None
    rel120_min: Optional[float] = None
    near_60d_high_pct: Optional[float] = None


@dataclass(frozen=True)
class Params:
    top_n: int
    max_position_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    quality_filter: float
    rebalance_freq_days: int
    trailing_stop_pct: Optional[float] = None
    max_new_buys: Optional[int] = None


BASELINE_PARAMS = Params(
    top_n=8, max_position_pct=0.08, stop_loss_pct=0.08,
    take_profit_pct=0.22, quality_filter=0.30, rebalance_freq_days=63,
)

SANITY_SEEDS = [
    (
        Overlay("price_gt_ma20", ma_window=20),
        Params(
            top_n=8,
            max_position_pct=0.08,
            stop_loss_pct=0.08,
            take_profit_pct=0.25,
            quality_filter=0.30,
            rebalance_freq_days=63,
        ),
    ),
]

# ── Helpers ────────────────────────────────────────────────────────────
def load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def pct(value: Optional[float]) -> str:
    if value is None: return "n/a"
    return f"{value * 100:+.2f}%"


def plain_pct(value: Optional[float]) -> str:
    if value is None: return "n/a"
    return f"{value * 100:.2f}%"


def finite(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def is_missing(value) -> bool:
    return value is None or bool(pd.isna(value))


def json_safe(obj):
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return finite(obj)


# ── Feature precomputation cache ───────────────────────────────────────
class FeatureCache:
    """Precompute price-derived features once, reuse across all overlays."""

    def __init__(self, prices: pd.DataFrame, hs300_ticker: str):
        self.prices = prices
        self.hs300 = hs300_ticker
        self.n_days = len(prices)
        self.tickers = list(prices.columns)
        self._ma: Dict[int, Dict[str, Optional[float]]] = {}
        self._mom: Dict[int, Dict[str, Optional[float]]] = {}
        self._vol: Dict[int, Dict[str, Optional[float]]] = {}
        self._near_high: Dict[int, Dict[str, Optional[float]]] = {}

    def _row_value(self, idx: int, ticker: str) -> Optional[float]:
        if ticker not in self.prices.columns:
            return None
        v = self.prices.iloc[idx].get(ticker)
        if is_missing(v):
            return None
        return float(v)

    def ma(self, idx: int, ticker: str, window: int) -> Optional[float]:
        key = (window,)
        if key not in self._ma:
            self._ma[key] = {}
        if idx not in self._ma[key]:
            self._ma[key][idx] = {}
        if ticker not in self._ma[key][idx]:
            if idx + 1 < window or ticker not in self.prices.columns:
                self._ma[key][idx][ticker] = None
            else:
                series = self.prices[ticker].iloc[idx + 1 - window:idx + 1].dropna()
                self._ma[key][idx][ticker] = float(series.mean()) if len(series) >= window else None
        return self._ma[key][idx][ticker]

    def trailing(self, idx: int, ticker: str, window: int) -> Optional[float]:
        key = (window,)
        if key not in self._mom:
            self._mom[key] = {}
        if idx not in self._mom[key]:
            self._mom[key][idx] = {}
        if ticker not in self._mom[key][idx]:
            if idx < window or ticker not in self.prices.columns:
                self._mom[key][idx][ticker] = None
            else:
                cur = self._row_value(idx, ticker)
                past = self._row_value(idx - window, ticker)
                if cur is not None and past is not None and past > 0:
                    self._mom[key][idx][ticker] = cur / past - 1.0
                else:
                    self._mom[key][idx][ticker] = None
        return self._mom[key][idx][ticker]

    def vol20(self, idx: int, ticker: str) -> Optional[float]:
        if 20 not in self._vol:
            self._vol[20] = {}
        if idx not in self._vol[20]:
            self._vol[20][idx] = {}
        if ticker not in self._vol[20][idx]:
            if idx < 20 or ticker not in self.prices.columns:
                self._vol[20][idx][ticker] = None
            else:
                rets = self.prices[ticker].pct_change(fill_method=None).iloc[idx + 1 - 20:idx + 1].dropna()
                self._vol[20][idx][ticker] = float(rets.std()) if len(rets) >= 15 else None
        return self._vol[20][idx][ticker]

    def dist_from_60d_high(self, idx: int, ticker: str) -> Optional[float]:
        if 60 not in self._near_high:
            self._near_high[60] = {}
        if idx not in self._near_high[60]:
            self._near_high[60][idx] = {}
        if ticker not in self._near_high[60][idx]:
            cur = self._row_value(idx, ticker)
            if cur is None or idx < 60 or ticker not in self.prices.columns:
                self._near_high[60][idx][ticker] = None
            else:
                high = self.prices[ticker].iloc[idx - 60:idx + 1].max()
                self._near_high[60][idx][ticker] = cur / high - 1.0 if high > 0 else None
        return self._near_high[60][idx][ticker]

    def rel_ret(self, idx: int, ticker: str, window: int) -> Optional[float]:
        stock_ret = self.trailing(idx, ticker, window)
        hs300_ret = self.trailing(idx, self.hs300, window)
        if stock_ret is not None and hs300_ret is not None:
            return stock_ret - hs300_ret
        return None


# ── Overlay check ──────────────────────────────────────────────────────
def passes_overlay(fc: FeatureCache, idx: int, ticker: str, overlay: Overlay) -> bool:
    current = fc._row_value(idx, ticker)
    if current is None:
        return False

    if overlay.mom20_min is not None:
        ret = fc.trailing(idx, ticker, 20)
        if ret is None or ret < overlay.mom20_min:
            return False
    if overlay.mom60_min is not None:
        ret = fc.trailing(idx, ticker, 60)
        if ret is None or ret < overlay.mom60_min:
            return False
    if overlay.mom120_min is not None:
        ret = fc.trailing(idx, ticker, 120)
        if ret is None or ret < overlay.mom120_min:
            return False
    if overlay.ma_window is not None:
        ma = fc.ma(idx, ticker, overlay.ma_window)
        if ma is None or current <= ma:
            return False
    if overlay.vol20_max is not None:
        vol = fc.vol20(idx, ticker)
        if vol is None or vol > overlay.vol20_max:
            return False
    if overlay.market_ma_window is not None:
        market = fc._row_value(idx, fc.hs300)
        ma = fc.ma(idx, fc.hs300, overlay.market_ma_window)
        if market is None or ma is None or market <= ma:
            return False
    if overlay.market_ret20_min is not None:
        ret = fc.trailing(idx, fc.hs300, 20)
        if ret is None or ret < overlay.market_ret20_min:
            return False
    if overlay.rel20_min is not None:
        rel = fc.rel_ret(idx, ticker, 20)
        if rel is None or rel < overlay.rel20_min:
            return False
    if overlay.rel60_min is not None:
        rel = fc.rel_ret(idx, ticker, 60)
        if rel is None or rel < overlay.rel60_min:
            return False
    if overlay.rel120_min is not None:
        rel = fc.rel_ret(idx, ticker, 120)
        if rel is None or rel < overlay.rel120_min:
            return False
    if overlay.near_60d_high_pct is not None:
        dist = fc.dist_from_60d_high(idx, ticker)
        if dist is None or dist < -overlay.near_60d_high_pct:
            return False

    return True


# ── Overlay builder ────────────────────────────────────────────────────
def build_overlays() -> List[Overlay]:
    overlays = [Overlay("none")]
    # Family 1: Benchmark-relative momentum
    overlays.append(Overlay("rel20_ge_0", rel20_min=0.0))
    overlays.append(Overlay("rel20_ge_-3pct", rel20_min=-0.03))
    overlays.append(Overlay("rel60_ge_0", rel60_min=0.0))
    overlays.append(Overlay("rel60_ge_-5pct", rel60_min=-0.05))
    overlays.append(Overlay("rel120_ge_-3pct", rel120_min=-0.03))
    # Family 2: Trend quality + drawdown distance
    overlays.append(Overlay("price_gt_ma20", ma_window=20))
    overlays.append(Overlay("price_gt_ma60", ma_window=60))
    overlays.append(Overlay("price_gt_ma120", ma_window=120))
    overlays.append(Overlay("near_60d_high_15pct", near_60d_high_pct=0.15))
    overlays.append(Overlay("near_60d_high_25pct", near_60d_high_pct=0.25))
    # Combined
    overlays.append(Overlay("mom20_ge_0_and_ma60", mom20_min=0.0, ma_window=60))
    overlays.append(Overlay("rel20_ge_0_and_ma60", rel20_min=0.0, ma_window=60))
    overlays.append(Overlay("rel60_ge_-5_and_near_high", rel60_min=-0.05, near_60d_high_pct=0.25))
    # Classic H39 overlays for continuity
    overlays.append(Overlay("hs300_ma60_and_price_gt_ma60", market_ma_window=60, ma_window=60))
    overlays.append(Overlay("vol20_le_4_and_price_gt_ma60", vol20_max=0.04, ma_window=60))
    overlays.append(Overlay("mom60_ge_-5_and_vol20_le_4", mom60_min=-0.05, vol20_max=0.04))
    overlays.append(Overlay("hs300_gt_ma60", market_ma_window=60))
    return overlays


def build_param_grid() -> List[Params]:
    grid = []
    for top_n, max_pos, sl, tp, qf, rebalance in itertools.product(
        [5, 6, 8, 10],
        [0.05, 0.06, 0.08],
        [0.08, 0.10],
        [0.18, 0.22, 0.25],
        [0.30, 0.35, 0.40],
        [42, 63, 84, 126],
    ):
        grid.append(Params(top_n, max_pos, sl, tp, qf, rebalance))
    # Add trailing-stop variants for a subset
    for top_n, max_pos, sl, tp, qf, rebalance, trail in itertools.product(
        [6, 8],
        [0.06, 0.08],
        [0.08],
        [0.18, 0.25],
        [0.30, 0.35],
        [63, 84],
        [0.10, 0.12],
    ):
        grid.append(Params(top_n, max_pos, sl, tp, qf, rebalance, trailing_stop_pct=trail))
    return grid


# ── Deploy blockers ────────────────────────────────────────────────────
def deploy_blockers(data_source: CN_PIT_FileSource, prices: pd.DataFrame,
                    start: str, end: str, n_days: int, n_sells: int,
                    total_ret: float, sharpe: float) -> Tuple[bool, List[str], Dict]:
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
        blockers.append("research_only")
    coverage = data_source.price_data_coverage_for_period(start, end)
    if not coverage.get("ok"):
        blockers.append(f"price_coverage: {coverage.get('reason', 'failed')}")
    if n_days < MIN_TRADING_DAYS:
        blockers.append(f"insufficient_trading_days: {n_days} < {MIN_TRADING_DAYS}")
    if n_sells < MIN_TRADE_COUNT:
        blockers.append(f"insufficient_trades: {n_sells} < {MIN_TRADE_COUNT}")
    if total_ret < 0:
        blockers.append(f"negative_total_return: {total_ret*100:.2f}%")
    if sharpe < 0:
        blockers.append(f"negative_sharpe: {sharpe:.2f}")
    return len(blockers) == 0, blockers, {"data_quality": dq.to_dict(), "price_coverage": coverage}


# ── Single-window backtest ─────────────────────────────────────────────
def run_overlay_backtest(
    data_source: CN_PIT_FileSource,
    prices: pd.DataFrame,
    fc: FeatureCache,
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
        hs300_val = day_prices.get(fc.hs300)
        if not is_missing(hs300_val):
            hs300_last = float(hs300_val)
            if hs300_base is None:
                hs300_base = float(hs300_val)

        # Exit checks
        for ticker in list(positions):
            pos = positions[ticker]
            px = day_prices.get(ticker, pos["avg_cost"])
            if is_missing(px):
                continue
            ret = px / pos["avg_cost"] - 1
            held_days = idx - pos["entry_idx"]

            # Trailing stop: track max since entry
            if params.trailing_stop_pct is not None:
                pos["max_since_entry"] = max(pos.get("max_since_entry", pos["avg_cost"]), px)

            exit_reason = None
            if ret >= params.take_profit_pct:
                exit_reason = "tp"
            elif ret <= -params.stop_loss_pct:
                exit_reason = "sl"
            elif params.trailing_stop_pct is not None:
                peak = pos.get("max_since_entry", pos["avg_cost"])
                if px < peak * (1 - params.trailing_stop_pct) and ret > 0:
                    exit_reason = "trailing"
            if not exit_reason and held_days >= 252:
                exit_reason = "time"

            if exit_reason:
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
                    "exit_reason": exit_reason, "held_days": held_days,
                })
                del positions[ticker]

        # Rebalance
        if idx - last_rebalance_idx >= params.rebalance_freq_days:
            live_universe = data_source.get_universe(date_str)
            scoped = [t for t in live_universe if t in prices.columns]
            fundamentals = data_source.get_fundamentals(scoped, date_str)
            scores = []
            for ticker in scoped:
                if ticker in positions:
                    continue
                if not passes_overlay(fc, idx, ticker, overlay):
                    continue
                score = ValueScore.from_fundamentals(ticker, fundamentals)
                if score and score.total >= params.quality_filter:
                    scores.append(score)
            scores.sort(key=lambda x: x.total, reverse=True)
            target_tickers = [score.ticker for score in scores[:params.top_n]]

            # Rebalance out
            for ticker in list(positions):
                if ticker not in target_tickers:
                    pos = positions[ticker]
                    px = day_prices.get(ticker, pos["avg_cost"])
                    if is_missing(px):
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
                        "pnl": float(pnl),
                        "pnl_pct": float(pnl / (pos["avg_cost"] * shares) * 100),
                        "commission": float(commission), "stamp_tax": float(stamp_tax),
                        "slippage": float(slippage), "exit_reason": "rebalance_out",
                        "held_days": idx - pos["entry_idx"],
                    })
                    del positions[ticker]

            # Max new buys per rebalance
            new_buys_allowed = params.max_new_buys if params.max_new_buys else params.top_n
            n_slots = params.top_n - len(positions)
            n_to_buy = min(n_slots, new_buys_allowed)
            budget_per_slot = cash / max(n_to_buy, 1) if n_to_buy > 0 else 0
            bought = 0
            for ticker in target_tickers:
                if ticker in positions:
                    continue
                if bought >= n_to_buy:
                    break
                px = day_prices.get(ticker)
                if is_missing(px):
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
                positions[ticker] = {
                    "shares": shares, "avg_cost": float(px),
                    "entry_idx": idx, "buy_date": date_str,
                }
                if params.trailing_stop_pct is not None:
                    positions[ticker]["max_since_entry"] = float(px)
                trades.append({
                    "date": date_str, "action": "buy", "ticker": ticker,
                    "price": float(px), "shares": shares, "amount": float(cost),
                    "commission": float(commission), "transfer_fee": float(transfer_fee),
                    "slippage": float(slippage), "total_cost": float(total_cost),
                })
                bought += 1
            last_rebalance_idx = idx

        # MTM
        total_value = cash
        for ticker, pos in positions.items():
            px = day_prices.get(ticker, pos["avg_cost"])
            total_value += pos["shares"] * (px if not is_missing(px) else pos["avg_cost"])
        equity.append(float(total_value))

    # Compute metrics
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

    can_deploy, period_blockers, dq_meta = deploy_blockers(
        data_source, prices, start, end, n_days, len(sells), total_ret, sharpe)

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
        trades, capital, load_json(DEFAULT_CONFIG), metrics, equity_curve=eq, as_of_date=end)
    blockers.extend(period_blockers)
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


# ── Multi-window evaluation ───────────────────────────────────────────
def compute_acceptance_gate(window_results: Dict[str, Dict]) -> Tuple[Dict, bool]:
    """Compute H42's nine-condition acceptance gate from window results."""
    deploy = window_results["deploy_2025_2026"]
    m = deploy["metrics"]
    blocked = deploy["execution_blocked"]
    warnings = len(deploy["execution_warnings"])
    streak = deploy["terminal_losing_streak"]
    trades = m["trade_count"]
    excess = m["excess_return"]
    max_dd = m["max_drawdown"]

    positive_windows = sum(
        1 for r in window_results.values()
        if r["metrics"]["total_return"] > 0
    )
    unblocked_windows = sum(
        1 for r in window_results.values()
        if not r["execution_blocked"]
    )
    beat_hs300 = sum(
        1 for r in window_results.values()
        if r["metrics"]["excess_return"] > 0
    )

    metrics = {
        "execution_blocked": blocked,
        "warnings_count": warnings,
        "deploy_trades": trades,
        "deploy_streak": streak,
        "positive_windows": positive_windows,
        "unblocked_windows": unblocked_windows,
        "beat_hs300_windows": beat_hs300,
        "deploy_excess_return": excess,
        "deploy_max_drawdown": max_dd,
    }
    passes_gate = (
        not blocked
        and warnings == 0
        and trades >= 30
        and streak < 5
        and positive_windows >= 4
        and unblocked_windows >= 3
        and beat_hs300 >= 2
        and excess > 0
        and max_dd > -0.08
    )
    return metrics, passes_gate


def evaluate_candidate_multi_window(
    data_source: CN_PIT_FileSource,
    params: Params,
    overlay: Overlay,
    config: Dict,
    capital: float,
) -> Dict:
    """Backtest candidate across all 5 windows, return aggregated verdict."""
    window_results = {}
    for wname, (wstart, wend) in WINDOWS.items():
        universe = data_source.get_price_universe(wstart, wend)
        prices = data_source.get_price_history(list(universe) + [HS300_TICKER], wstart, wend)
        fc = FeatureCache(prices, HS300_TICKER)
        window_results[wname] = run_overlay_backtest(
            data_source, prices, fc, wstart, wend, capital, params, overlay, config)

    gate_metrics, passes_gate = compute_acceptance_gate(window_results)

    return {
        "params": asdict(params),
        "overlay": asdict(overlay),
        "deploy_window": window_results["deploy_2025_2026"],
        "window_results": {k: {
            "total_return": v["metrics"]["total_return"],
            "sharpe_ratio": v["metrics"]["sharpe_ratio"],
            "max_drawdown": v["metrics"]["max_drawdown"],
            "hs300_return": v["metrics"]["hs300_return"],
            "excess_return": v["metrics"]["excess_return"],
            "trade_count": v["metrics"]["trade_count"],
            "execution_blocked": v["execution_blocked"],
            "terminal_losing_streak": v["terminal_losing_streak"],
            "blockers": v["execution_blockers"],
            "warnings": v["execution_warnings"],
        } for k, v in window_results.items()},
        "passes_acceptance_gate": passes_gate,
        "gate_metrics": gate_metrics,
    }


# ── Candidate scoring ─────────────────────────────────────────────────
def score_candidate_mw(result: Dict) -> Tuple:
    """Score for multi-window ranking: lower is better."""
    g = result["gate_metrics"]
    m = result["deploy_window"]["metrics"]
    return (
        0 if result["passes_acceptance_gate"] else 1,
        -g["positive_windows"],
        -g["beat_hs300_windows"],
        1 if g["execution_blocked"] else 0,
        g["warnings_count"],
        g["deploy_streak"],
        -m["sharpe_ratio"],
        -m["total_return"],
    )


# ── Stage A: overlay screening ────────────────────────────────────────
def run_stage_a(
    data_source: CN_PIT_FileSource,
    overlays: List[Overlay],
    config: Dict,
    capital: float,
    deploy_start: str,
    deploy_end: str,
) -> List[Dict]:
    """Screen overlays with baseline params on deploy window."""
    universe = data_source.get_price_universe(deploy_start, deploy_end)
    prices = data_source.get_price_history(list(universe) + [HS300_TICKER], deploy_start, deploy_end)
    fc = FeatureCache(prices, HS300_TICKER)

    print(f"Stage A: screening {len(overlays)} overlays with baseline params...")
    results = []
    for overlay in overlays:
        result = run_overlay_backtest(
            data_source, prices, fc, deploy_start, deploy_end,
            capital, BASELINE_PARAMS, overlay, config)
        results.append(result)
        m = result["metrics"]
        print(
            f"  {overlay.name:35s} ret={m['total_return']*100:+6.2f}% "
            f"sharpe={m['sharpe_ratio']:.2f} strek={result['terminal_losing_streak']} "
            f"trades={m['trade_count']} blocked={result['execution_blocked']}"
        )
        sys.stdout.flush()
    return results


# ── Stage B: param grid search ────────────────────────────────────────
def run_stage_b(
    data_source: CN_PIT_FileSource,
    overlays: List[Overlay],
    grid: List[Params],
    config: Dict,
    capital: float,
    deploy_start: str,
    deploy_end: str,
    stage_b_limit: int,
) -> List[Dict]:
    """Grid search across selected overlays × param grid (deploy window only)."""
    universe = data_source.get_price_universe(deploy_start, deploy_end)
    prices = data_source.get_price_history(list(universe) + [HS300_TICKER], deploy_start, deploy_end)
    fc = FeatureCache(prices, HS300_TICKER)

    # Interleave combos across overlays so each overlay gets proportional share
    total = min(len(grid) * len(overlays), stage_b_limit) if stage_b_limit else len(grid) * len(overlays)
    print(f"Stage B: running up to {total} param combos across {len(overlays)} overlays (sampled)...")
    results = []
    seen_params = set()
    count = 0
    # Sample evenly across the full grid, cycling through overlays
    step = max(1, len(grid) * len(overlays) // total) if total > 0 else 1
    session_idx = 0
    for gi in range(0, len(grid), step):
        for oi in range(len(overlays)):
            if total and session_idx >= total:
                break
            overlay = overlays[oi]
            params = grid[gi]
            sig = (overlay.name, params.top_n, params.max_position_pct, params.stop_loss_pct,
                   params.take_profit_pct, params.quality_filter, params.rebalance_freq_days,
                   params.trailing_stop_pct, params.max_new_buys)
            if sig in seen_params:
                continue
            seen_params.add(sig)
            session_idx += 1
            count += 1
            result = run_overlay_backtest(
                data_source, prices, fc, deploy_start, deploy_end,
                capital, params, overlay, config)
            results.append(result)
            if count % 100 == 0 or count == total:
                print(f"  progress {count}/{total}")
                sys.stdout.flush()
        if total and session_idx >= total:
            break
    return results


def run_sanity_seeds(
    data_source: CN_PIT_FileSource,
    config: Dict,
    capital: float,
    deploy_start: str,
    deploy_end: str,
) -> List[Dict]:
    """Always evaluate known H39 candidates so H42 cannot sample them away."""
    universe = data_source.get_price_universe(deploy_start, deploy_end)
    prices = data_source.get_price_history(list(universe) + [HS300_TICKER], deploy_start, deploy_end)
    fc = FeatureCache(prices, HS300_TICKER)

    print(f"Sanity seeds: running {len(SANITY_SEEDS)} known candidates...")
    results = []
    for overlay, params in SANITY_SEEDS:
        result = run_overlay_backtest(
            data_source, prices, fc, deploy_start, deploy_end,
            capital, params, overlay, config)
        results.append(result)
        m = result["metrics"]
        print(
            f"  {overlay.name:35s} ret={m['total_return']*100:+6.2f}% "
            f"sharpe={m['sharpe_ratio']:.2f} streak={result['terminal_losing_streak']} "
            f"trades={m['trade_count']} blocked={result['execution_blocked']}"
        )
        sys.stdout.flush()
    return results


# ── Stage C: multi-window evaluation ──────────────────────────────────
def run_stage_c(
    data_source: CN_PIT_FileSource,
    candidates: List[Dict],
    config: Dict,
    capital: float,
    top_k: int,
) -> List[Dict]:
    """Multi-window evaluation of top candidates."""
    n_eval = min(top_k, len(candidates))
    print(f"Stage C: multi-window evaluation of top {n_eval} candidates...")
    mw_results = []
    for i, cand in enumerate(candidates[:n_eval]):
        overlay = Overlay(**cand["overlay"])
        params = Params(**cand["params"])
        print(f"  [{i+1}/{n_eval}] {overlay.name} top_n={params.top_n} "
              f"sl={params.stop_loss_pct} tp={params.take_profit_pct} "
              f"trail={params.trailing_stop_pct} ...")
        sys.stdout.flush()
        mw = evaluate_candidate_multi_window(data_source, params, overlay, config, capital)
        mw_results.append(mw)
        g = mw["gate_metrics"]
        print(f"    pass={mw['passes_acceptance_gate']} "
              f"pos_wins={g['positive_windows']}/5 unblock={g['unblocked_windows']}/5 "
              f"beat={g['beat_hs300_windows']}/5 "
              f"excess={pct(g['deploy_excess_return'])} maxdd={pct(g['deploy_max_drawdown'])}")
    return mw_results


# ── Main search ────────────────────────────────────────────────────────
def run_search(args) -> Dict:
    t0 = time.time()
    config = load_json(DEFAULT_CONFIG)
    source = CN_PIT_FileSource(
        prices_path=str(args.prices_file),
        universe_path=str(args.universe_file),
        universe_snapshots_path=str(args.snapshots_file),
    )

    overlays = build_overlays()
    grid = build_param_grid()
    print(f"Total overlays: {len(overlays)}, total param combos: {len(grid)}")
    if args.stage_a_limit:
        print(f"Stage A limit: {args.stage_a_limit}")
    if args.stage_b_limit:
        print(f"Stage B limit: {args.stage_b_limit}")
    if args.top_k:
        print(f"Top-K for Stage C: {args.top_k}")

    # Stage A: overlay screening
    stage_a_results = run_stage_a(
        source, overlays[:args.stage_a_limit] if args.stage_a_limit else overlays,
        config, args.capital, "2025-01-01", "2026-05-21")

    # Select top overlays for Stage B
    ranked_a = sorted(stage_a_results, key=lambda r: (
        1 if r["execution_blocked"] else 0,
        r["terminal_losing_streak"],
        max(0, 30 - r["metrics"]["trade_count"]),  # proximity to 30 trades
        -r["metrics"]["sharpe_ratio"],
        -r["metrics"]["total_return"],
    ))
    selected_names = []
    for r in ranked_a:
        name = r["overlay"]["name"]
        if name not in selected_names:
            selected_names.append(name)
        if len(selected_names) >= args.top_overlays:
            break
    selected_overlays = [o for o in overlays if o.name in selected_names]
    print(f"Selected overlays for Stage B: {selected_names}")

    # Stage B: param grid
    if args.stage_b_limit == 0:
        # full grid
        stage_b_results = run_stage_b(
            source, selected_overlays, grid, config, args.capital,
            "2025-01-01", "2026-05-21", 0)
    else:
        stage_b_results = run_stage_b(
            source, selected_overlays, grid, config, args.capital,
            "2025-01-01", "2026-05-21", args.stage_b_limit)

    seed_results = run_sanity_seeds(
        source, config, args.capital, "2025-01-01", "2026-05-21")

    # Merge and rank deploy-window candidates (dedup by overlay+param signature)
    all_results = stage_a_results + stage_b_results + seed_results
    clean_deploy = [
        r for r in all_results
        if not r["execution_blocked"]
        and r["metrics"]["total_return"] > 0
        and r["metrics"]["sharpe_ratio"] >= 1.0
        and r["metrics"]["trade_count"] >= 30
        and r["terminal_losing_streak"] < 5
    ]
    # Dedup by (overlay_name, param_key)
    seen_sigs = set()
    deduped_clean = []
    for r in clean_deploy:
        sig = (
            r["overlay"]["name"],
            r["params"]["top_n"],
            r["params"]["max_position_pct"],
            r["params"]["stop_loss_pct"],
            r["params"]["take_profit_pct"],
            r["params"]["quality_filter"],
            r["params"]["rebalance_freq_days"],
            r["params"].get("trailing_stop_pct"),
            r["params"].get("max_new_buys"),
        )
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped_clean.append(r)
    deduped_clean.sort(
        key=lambda r: (r["metrics"]["sharpe_ratio"], r["metrics"]["excess_return"]),
        reverse=True)
    print(f"Clean deploy-window candidates (unique): {len(deduped_clean)}/{len(clean_deploy)} total")

    # Stage C: multi-window
    mw_results = run_stage_c(source, deduped_clean, config, args.capital, args.top_k)
    mw_results.sort(key=score_candidate_mw)

    gate_pass = [r for r in mw_results if r["passes_acceptance_gate"]]

    elapsed = time.time() - t0
    print(f"\nSearch complete in {elapsed/60:.1f}min")
    print(f"Stage A: {len(stage_a_results)} overlays")
    print(f"Stage B: {len(stage_b_results)} param runs")
    print(f"Sanity seeds: {len(seed_results)}")
    print(f"Clean deploy-window candidates: {len(clean_deploy)}")
    print(f"Multi-window evaluated: {len(mw_results)}")
    print(f"Acceptance gate passed: {len(gate_pass)}")

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task": "H42",
        "elapsed_seconds": round(elapsed, 1),
        "inputs": {
            "prices_file": str(args.prices_file),
            "universe_file": str(args.universe_file),
            "snapshots_file": str(args.snapshots_file),
            "config": str(DEFAULT_CONFIG),
        },
        "stage_a_count": len(stage_a_results),
        "stage_b_count": len(stage_b_results),
        "seed_count": len(seed_results),
        "stage_c_count": len(mw_results),
        "clean_deploy_count": len(clean_deploy),
        "selected_overlays": selected_names,
        "acceptance_gate_passed": len(gate_pass) > 0,
        "gate_pass_count": len(gate_pass),
        "top_candidates_multi_window": mw_results[:15],
        "all_clean_deploy": clean_deploy[:50],
        "verdict": "CANDIDATE_FOR_FORWARD_TRIAL" if gate_pass else "RESEARCH_ONLY",
    }


# ── Report builder ─────────────────────────────────────────────────────
def candidate_row_mw(r: Dict) -> str:
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
    return (
        f"| {wname} | {pct(wr['total_return'])} | {wr['sharpe_ratio']:.2f} | "
        f"{pct(wr['max_drawdown'])} | {pct(wr['excess_return'])} | "
        f"{'BLOCKED' if wr['execution_blocked'] else 'OK'} | {wr['trade_count']} | "
        f"{wr['terminal_losing_streak']} |"
    )


def build_report(payload: Dict) -> str:
    v = payload["verdict"]
    lines = [
        "# H42 — Strategy Redesign Search Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Verdict:** {v}",
        f"**Elapsed:** {payload['elapsed_seconds']:.1f}s",
        "",
        "## Search Space Summary",
        "",
        f"- Stage A (overlay screening): {payload['stage_a_count']} overlays",
        f"- Stage B (param grid): {payload['stage_b_count']} runs",
        f"- Sanity seeds: {payload.get('seed_count', 0)} known candidates",
        f"- Clean deploy-window candidates: {payload.get('clean_deploy_count', len(payload.get('all_clean_deploy', [])))}",
        f"- Stage C (multi-window): {payload['stage_c_count']} candidates",
        f"- Selected overlays: {', '.join(payload['selected_overlays'])}",
        "",
        "## Acceptance Gate",
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

    top = payload["top_candidates_multi_window"]
    if top:
        table_header = (
            "| Overlay | N | Pos% | SL | TP | Trail | QF | Rebal | Return | Sharpe | "
            "MaxDD | Excess | Trades | Streak | Gate |\n"
            "|---------|---|------|----|----|-------|----|-------|--------|--------|"
            "-------|--------|--------|--------|------|"
        )
        lines.extend(["## Top Candidates (Multi-Window Ranked)", "", table_header])
        lines.extend(candidate_row_mw(r) for r in top[:15])
        lines.append("")

        # Per-window detail for top 3
        lines.extend(["## Per-Window Detail (Top 3)", ""])
        for i, cand in enumerate(top[:3]):
            o = cand["overlay"]["name"]
            p = cand["params"]
            lines.append(f"### #{i+1}: {o} (N={p['top_n']}, SL={p['stop_loss_pct']}, "
                         f"TP={p['take_profit_pct']}, Trail={p.get('trailing_stop_pct') or '-'}, "
                         f"QF={p['quality_filter']}, Rebal={p['rebalance_freq_days']})")
            lines.append("")
            lines.append(
                "| Window | Return | Sharpe | MaxDD | Excess | Status | Trades | Streak |\n"
                "|--------|--------|--------|-------|--------|--------|--------|--------|"
            )
            for wname in ["cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"]:
                wr = cand["window_results"].get(wname, {})
                lines.append(window_table_row(wname, wr))
            lines.append("")

        # Verdict
        if payload["acceptance_gate_passed"]:
            best = top[0]
            lines.extend([
                "## Verdict",
                "",
                "**CANDIDATE_FOR_FORWARD_TRIAL** — at least one candidate passes the full acceptance gate.",
                "",
                f"Recommended: `{best['overlay']['name']}` / N={best['params']['top_n']} / "
                f"SL={best['params']['stop_loss_pct']} / TP={best['params']['take_profit_pct']} / "
                f"Trail={best['params'].get('trailing_stop_pct') or '-'} / "
                f"QF={best['params']['quality_filter']} / Rebal={best['params']['rebalance_freq_days']}",
                "",
                "Next step: forward-only paper shadow observation (H43) before any config promotion.",
            ])
        else:
            lines.extend([
                "## Verdict",
                "",
                "**RESEARCH_ONLY** — no candidate passes the full multi-window acceptance gate.",
                "",
                "The best deploy-window candidates clear H34 stop conditions but lack benchmark-relative robustness across temporal windows.",
                "",
                "Next step: consider sector-level diversification constraints, different value-score weights, or accept paper-only monitoring of the best candidate.",
            ])
    else:
        lines.extend([
            "## Results",
            "",
            "No candidates survived the initial deploy-window clean filter.",
        ])

    lines.append("")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="H42 strategy redesign search")
    parser.add_argument("--capital", type=float, default=300000)
    parser.add_argument("--prices-file", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--universe-file", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--snapshots-file", type=Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--top-overlays", type=int, default=8)
    parser.add_argument("--stage-a-limit", type=int, default=0,
                        help="Limit number of overlays in Stage A (0=all)")
    parser.add_argument("--stage-b-limit", type=int, default=200,
                        help="Limit param combos in Stage B (0=all, default=200)")
    parser.add_argument("--top-k", type=int, default=20,
                        help="Top K deploy-window candidates for Stage C multi-window eval")
    parser.add_argument("--output-run", type=Path, default=RUN_OUT)
    parser.add_argument("--output-report", type=Path, default=REPORT_OUT)
    args = parser.parse_args()

    print("=" * 70)
    print("H42 — Strategy Redesign Search")
    print(f"Families: benchmark-relative momentum, trend quality, exit discipline, risk throttles")
    print(f"Default stage-b-limit: {args.stage_b_limit} (use --stage-b-limit 0 for full grid)")
    print("=" * 70)

    payload = run_search(args)

    args.output_run.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_run.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2))
    args.output_report.write_text(build_report(payload))

    print(f"\nWrote: {args.output_run}")
    print(f"Wrote: {args.output_report}")
    print(f"Verdict: {payload['verdict']}")
    print(f"Gate passed: {payload['gate_pass_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
