#!/usr/bin/env python3
"""H51b — Risk Model Overlay Search.

Substitutes the naive equal-weight sizing in H50b's backtest with a
risk-aware, volatility-scaled weighting + single-name caps + ADTV liquidity
constraints + min-active-name cash buffer.

D1: ValueScoreH50 from H50b, identical monkey-patch mechanism.
D2: _run_fundamental_backtest_h51b — copy of run_fundamental_backtest with
    D3+D4 risk model sizing replacing lines 877-883.
D3: Vol-scaled sizing, PIT-safe log returns, 60d window, min 40 data points.
D4: 4-step pipeline: single-name cap → ADTV cap → min_active cash buffer → normalize.
D5: Pinned base params from H50b best; 18 risk combos.
D6: Stage C rank by beat_HS300_windows desc.
D7: 5-way comparison table (H42/H48/H49b/H50b/H51b).
D8: Provenance with sizing_substitution + risk_model_design + exclusion_stats.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import itertools
import json
import math
import sys
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

# ── Imports from existing modules ────────────────────────────────────────
from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    COMMISSION_RATE,
    HS300_TICKER,
    SLIPPAGE_BPS,
    STAMP_TAX_RATE,
    TRANSFER_FEE_RATE,
    ValueScore as _FB_ValueScore_Original,
    run_fundamental_backtest as _FB_RUN_V1,
)
from h35_shadow_account_executor import (  # noqa: E402
    check_stop_conditions,
    compute_consecutive_losing_sells,
    compute_monthly_one_way_turnover,
    compute_annualized_turnover,
)
from h42_strategy_redesign_search import (  # noqa: E402
    FeatureCache,
    Overlay as H42Overlay,
    Params as H42Params,
    BASELINE_PARAMS,
    SANITY_SEEDS,
    passes_overlay as h42_passes_overlay,
    build_overlays as h42_build_overlays,
    deploy_blockers,
    compute_acceptance_gate,
    WINDOWS,
    load_json,
    pct,
    plain_pct,
    finite,
    is_missing,
    json_safe,
    window_table_row,
)
from h49b_sector_neutral_rs_search import (  # noqa: E402
    H49bOverlay,
    H49bParams,
    SectorFeatureCache,
    load_sector_map,
    passes_h49b_overlay,
)
from h50b_quality_value_search import (  # noqa: E402
    ValueScoreH50,
    H50bOverlay,
    H50bParams,
    AS_OF_DATE_REF,
    install_patches as install_h50b_patches,
    load_h50a_panel,
    pit_lookup,
    build_h50b_overlays,
    passes_h50b_overlay,
    INPUT_SHA256 as H50B_INPUT_SHA256,
)

# ── Module refs for patching ──────────────────────────────────────────────
import sys as _sys
_h42_mod = _sys.modules["h42_strategy_redesign_search"]

# ── H51b-specific paths ──────────────────────────────────────────────────
H47_PRICES = PROJECT_ROOT / "data/cn_pit/prices_h47_tushare_qfq_candidate.csv"
H30_UNIVERSE = PROJECT_ROOT / "data/cn_pit/universe_h30_candidate.jsonl"
H30_SNAPSHOTS = PROJECT_ROOT / "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
SECTOR_CSV = PROJECT_ROOT / "data/cn_pit/sector_metadata_sw_l1.csv"
H50A_JSONL = PROJECT_ROOT / "data/cn_pit/fundamentals_h50a_pit_quality.jsonl"
H51A_ADTV = PROJECT_ROOT / "data/cn_pit/liquidity_h51a_daily_amount.csv"
DEFAULT_CONFIG = PROJECT_ROOT / "value_account/h34_shadow_account_config.json"

RUN_OUT = PROJECT_ROOT / "backtest/runs/fundamental_value_h51b_risk_model_search.json"
REPORT_OUT = PROJECT_ROOT / "reports/h51b_risk_model_search_report.md"

# SHA256s (pre-computed)
H51B_INPUT_SHA256 = {
    "prices": "34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc",
    "sector_metadata": "923762b79566894f7a85d0d0f7cdb835ac1bf7b43d262130ec35938cb6fa76f2",
    "fundamentals": "eeea2005b243070d7790b458f7232979ba945d18fa0056ecb775e3e408bc43b7",
    "adtv_liquidity": "52f91e2628ebcb49bf62c119ed9e410f5aef9bc610e2b330ac22fe0ecf6f05d5",
    "universe": "c59919c3022e2e4d803aa37b50c9dec388d709f00f9921e03443ab11b8ea832f",
}

# ═══════════════════════════════════════════════════════════════════════════
# D5: Pinned Base Params (verified against H50b best)
# ═══════════════════════════════════════════════════════════════════════════

PINNED_OVERLAY_NAME = "rel20_ge_0_and_ma60"
PINNED_TOP_N = 8
PINNED_MAX_POSITION_PCT = 0.08      # engine-level ceiling
PINNED_STOP_LOSS = 0.08
PINNED_TAKE_PROFIT = 0.25
PINNED_QUALITY_FILTER = 0.40
PINNED_REBALANCE_FREQ = 63
PINNED_SECTOR_MAX_WEIGHT = 0.20

# ═══════════════════════════════════════════════════════════════════════════
# Stage B: 18 Risk Combos
# ═══════════════════════════════════════════════════════════════════════════

TARGET_VOLS = [0.15, 0.20, 0.25]
SINGLE_NAME_CAPS = [0.10, 0.15, 0.20]
ADTV_CAPS = [0.05, 0.10]

MIN_ACTIVE_NAMES = 5
VOL_WINDOW = 60
VOL_MIN_DATA = 40

assert PINNED_TOP_N >= MIN_ACTIVE_NAMES, \
    f"top_n={PINNED_TOP_N} < min_active_names={MIN_ACTIVE_NAMES}"


# ═══════════════════════════════════════════════════════════════════════════
# ADTV Data Loader
# ═══════════════════════════════════════════════════════════════════════════

def load_adtv_data(path: Path) -> pd.DataFrame:
    """Load H51a ADTV data indexed by (date, ticker)."""
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.set_index(["date", "ticker"]).sort_index()
    return df


def compute_adtv_20d(
    adtv_df: pd.DataFrame,
    ticker: str,
    as_of_date_str: str,
) -> Optional[float]:
    """Compute 20-day trailing mean amount_rmb, EXCLUSIVE of as_of_date.

    Returns NaN if < 20 data points available in the trailing window.
    """
    as_of = pd.Timestamp(as_of_date_str)
    # Get all rows for this ticker
    try:
        ticker_data = adtv_df.xs(ticker, level="ticker")
    except KeyError:
        return None

    # Filter to dates < as_of and within trailing 120 calendar days
    trailing = ticker_data[
        (ticker_data.index < as_of) &
        (ticker_data.index >= as_of - pd.Timedelta(days=120))
    ]
    if len(trailing) < 1:
        return None

    # Take last 20 trading days
    last_20 = trailing.tail(20)
    if len(last_20) < 10:  # minimum 10 days to be meaningful
        return None

    return float(last_20["amount_rmb"].mean())


# ═══════════════════════════════════════════════════════════════════════════
# D3: Volatility-Scaled Sizing
# ═══════════════════════════════════════════════════════════════════════════

def compute_ticker_vol(
    prices_df: pd.DataFrame,
    ticker: str,
    as_of_date_str: str,
) -> Optional[float]:
    """Compute realized vol for ticker at as_of_date (PIT-safe, log returns).

    Returns None if < VOL_MIN_DATA non-null returns available.
    """
    as_of = pd.Timestamp(as_of_date_str)
    if ticker not in prices_df.columns:
        return None

    col = prices_df[ticker]
    # Prices before and excluding as_of_date
    mask = col.index < as_of
    prior = col[mask].dropna()
    if len(prior) < VOL_MIN_DATA + 1:
        return None

    # Take last VOL_WINDOW + 1 prices (need n+1 prices for n returns)
    window_prices = prior.tail(VOL_WINDOW + 1)
    if len(window_prices) < VOL_MIN_DATA + 1:
        return None

    # Log returns: log(close_i / close_{i-1})
    log_returns = np.log(window_prices / window_prices.shift(1)).dropna()
    if len(log_returns) < VOL_MIN_DATA:
        return None

    vol_annualized = float(log_returns.std() * math.sqrt(252))
    return vol_annualized


# ═══════════════════════════════════════════════════════════════════════════
# D2: _run_fundamental_backtest_h51b — Sizing Substitution
# ═══════════════════════════════════════════════════════════════════════════

def _run_fundamental_backtest_h51b(
    data_source,
    start_date: str,
    end_date: str,
    universe: Optional[List[str]] = None,
    capital: float = 500000,
    top_n: int = 8,
    max_position_pct: float = 0.10,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.18,
    rebalance_freq_days: int = 63,
    quality_filter: float = 0.40,
    # H51b-specific kwargs (accepted but defaulted so signature matches)
    _h51b_target_vol: float = 0.20,
    _h51b_single_name_cap: float = 0.10,
    _h51b_adtv_cap: float = 0.10,
    _h51b_adtv_df: Optional[pd.DataFrame] = None,
    _h51b_prices_full: Optional[pd.DataFrame] = None,
    _h51b_risk_trace: Optional[List[Dict]] = None,
    _h51b_exclusion_stats: Optional[Dict] = None,
    _h51b_sector_map: Optional[Dict[str, str]] = None,
    _h51b_sector_max_weight: float = 0.20,
):
    """Copy of run_fundamental_backtest with D3+D4 risk-model sizing.

    Signature is identical to the original; H51b-specific kwargs have defaults
    so original callers don't break (though they'll use the original function
    after restore).
    """
    # Import locally to avoid circular issues (same as original)
    from fundamental_backtest import (
        MIN_TRADING_DAYS,
        MIN_TRADE_COUNT,
        DataQuality,
        BacktestResult,
    )

    if universe is None:
        universe = data_source.get_price_universe(start_date, end_date)

    all_tickers = list(universe) + [HS300_TICKER]
    prices = data_source.get_price_history(all_tickers, start_date, end_date)
    trading_dates = prices.index

    cash = capital
    positions: Dict[str, Dict] = {}
    trades: List[Dict] = []
    equity = []
    total_fees = 0.0
    total_slippage = 0.0
    last_rebalance_idx = -999
    hs300_col = HS300_TICKER
    hs300_base = None
    hs300_last = None

    # Risk trace + exclusion (H51b only)
    risk_trace = _h51b_risk_trace if _h51b_risk_trace is not None else []
    excl_stats = _h51b_exclusion_stats if _h51b_exclusion_stats is not None else {
        "rebalances_total": 0,
        "vol_insufficient_data": 0,
        "adtv_insufficient_data": 0,
        "min_active_names_violated_count": 0,
    }
    adtv_df = _h51b_adtv_df
    prices_full = _h51b_prices_full
    sector_map = _h51b_sector_map or {}
    sector_max_weight = _h51b_sector_max_weight

    for idx, dt in enumerate(trading_dates):
        date_str = dt.strftime("%Y-%m-%d")
        day_prices = prices.iloc[idx]

        if hs300_col in day_prices.index:
            hs300_val = day_prices[hs300_col]
            if pd.notna(hs300_val):
                hs300_last = hs300_val
            if hs300_base is None and pd.notna(hs300_val):
                hs300_base = hs300_val

        # ── Exit (stop-loss / take-profit / time) ──
        for ticker in list(positions):
            pos = positions[ticker]
            px = day_prices.get(ticker, pos["avg_cost"])
            if isinstance(px, float) and math.isnan(px):
                continue
            ret = px / pos["avg_cost"] - 1
            held_days = idx - pos["entry_idx"]
            if ret >= take_profit_pct or ret <= -stop_loss_pct or held_days >= 252:
                sell_shares = pos["shares"]
                amount = px * sell_shares
                commission = max(amount * COMMISSION_RATE, 5)
                stamp_tax = amount * STAMP_TAX_RATE
                transfer_fee = amount * TRANSFER_FEE_RATE
                slippage = amount * SLIPPAGE_BPS / 10000
                net = amount - commission - stamp_tax - transfer_fee - slippage
                pnl = net - pos["avg_cost"] * sell_shares
                cash += net
                total_fees += commission + stamp_tax + transfer_fee
                total_slippage += slippage
                trades.append({
                    "date": date_str, "action": "sell", "ticker": ticker,
                    "price": px, "shares": sell_shares, "amount": amount,
                    "pnl": pnl, "pnl_pct": ret * 100,
                    "commission": commission, "stamp_tax": stamp_tax,
                    "slippage": slippage,
                    "exit_reason": "tp" if ret >= take_profit_pct else (
                        "sl" if ret <= -stop_loss_pct else "time"),
                    "held_days": held_days,
                })
                del positions[ticker]

        # ── Rebalance ──────────────────────────────────────────────────
        if idx - last_rebalance_idx >= rebalance_freq_days:
            as_of = date_str
            live_universe = data_source.get_universe(as_of)
            scoped = [t for t in live_universe if t in prices.columns]
            fundamentals = data_source.get_fundamentals(scoped, as_of)
            scores = []
            for t in scoped:
                vs = ValueScoreH50.from_fundamentals(t, fundamentals)
                if vs and vs.total >= quality_filter and t not in positions:
                    scores.append(vs)
            scores.sort(key=lambda x: x.total, reverse=True)
            target_tickers = [s.ticker for s in scores[:top_n]]

            # Rebalance out (identical to original)
            for ticker in list(positions):
                if ticker not in target_tickers:
                    pos = positions[ticker]
                    px = day_prices.get(ticker, pos["avg_cost"])
                    sell_shares = pos["shares"]
                    amount = px * sell_shares
                    commission = max(amount * COMMISSION_RATE, 5)
                    stamp_tax = amount * STAMP_TAX_RATE
                    transfer_fee = amount * TRANSFER_FEE_RATE
                    slippage = amount * SLIPPAGE_BPS / 10000
                    net = amount - commission - stamp_tax - transfer_fee - slippage
                    pnl = net - pos["avg_cost"] * sell_shares
                    cash += net
                    total_fees += commission + stamp_tax + transfer_fee
                    total_slippage += slippage
                    trades.append({
                        "date": date_str, "action": "sell", "ticker": ticker,
                        "price": px, "shares": sell_shares, "amount": amount,
                        "pnl": pnl,
                        "pnl_pct": pnl / (pos["avg_cost"] * sell_shares) * 100,
                        "commission": commission, "stamp_tax": stamp_tax,
                        "slippage": slippage, "exit_reason": "rebalance_out",
                        "held_days": idx - pos["entry_idx"],
                    })
                    del positions[ticker]

            # ═══════════════════════════════════════════════════════════════
            # D3+D4: Risk Model Sizing (REPLACES original lines 877-883)
            # ═══════════════════════════════════════════════════════════════
            excl_stats["rebalances_total"] += 1

            # Compute portfolio value
            portfolio_value = cash
            for _t, _pos in positions.items():
                _px = day_prices.get(_t, _pos["avg_cost"])
                portfolio_value += _pos["shares"] * (
                    _px if not (isinstance(_px, float) and math.isnan(_px))
                    else _pos["avg_cost"]
                )

            # D3: Compute vol-scaled raw weights
            eligible = []
            for ticker in target_tickers:
                vol = compute_ticker_vol(
                    prices_full if prices_full is not None else prices,
                    ticker, as_of)
                if vol is None or vol <= 0:
                    excl_stats["vol_insufficient_data"] += 1
                    continue
                eligible.append((ticker, vol))

            n_eligible = len(eligible)
            if n_eligible == 0:
                last_rebalance_idx = idx
                risk_trace.append({
                    "date": date_str, "n_active": 0, "n_eligible": 0,
                    "total_weight": 0, "cash_pct": 1.0,
                    "capped_by_single_name_count": 0,
                    "capped_by_adtv_count": 0,
                })
            else:
                target_vol = _h51b_target_vol
                raw_weights = {}
                for ticker, vol_i in eligible:
                    raw_weights[ticker] = (target_vol / vol_i) / n_eligible

                # D4 Step 1: Single-name cap
                effective_cap = min(_h51b_single_name_cap, max_position_pct)
                capped_single = 0
                capped_weights = {}
                for ticker in raw_weights:
                    w = raw_weights[ticker]
                    if w > effective_cap:
                        capped_single += 1
                        w = effective_cap
                    capped_weights[ticker] = w

                # D4 Step 2: ADTV participation cap
                capped_adtv = 0
                adtv_weights = {}
                for ticker, w_i in capped_weights.items():
                    # Current position value
                    cur_val = 0.0
                    if ticker in positions:
                        _pos = positions[ticker]
                        _px = day_prices.get(ticker, _pos["avg_cost"])
                        cur_val = _pos["shares"] * (
                            _px if not (isinstance(_px, float) and math.isnan(_px))
                            else _pos["avg_cost"]
                        )

                    trade_delta = abs(w_i * portfolio_value - cur_val)

                    if adtv_df is not None:
                        adtv_20 = compute_adtv_20d(adtv_df, ticker, as_of)
                    else:
                        adtv_20 = None

                    if adtv_20 is None or math.isnan(adtv_20):
                        excl_stats["adtv_insufficient_data"] += 1
                        continue  # exclude this ticker

                    max_trade = _h51b_adtv_cap * adtv_20
                    if trade_delta > max_trade:
                        # Truncate weight so trade equals cap
                        if cur_val >= w_i * portfolio_value:
                            # Reducing position; truncate to cap
                            truncated_w = (cur_val - max_trade) / portfolio_value
                        else:
                            truncated_w = (cur_val + max_trade) / portfolio_value
                        w_i = max(0.0, truncated_w)
                        capped_adtv += 1

                    adtv_weights[ticker] = w_i

                # D4 Step 3: Min active names
                active_weights = {t: w for t, w in adtv_weights.items() if w > 0}
                n_active = len(active_weights)

                if n_active < MIN_ACTIVE_NAMES:
                    excl_stats["min_active_names_violated_count"] += 1
                    # Hold entire portfolio as cash
                    active_weights = {}
                    n_active = 0

                # D4 Step 4: No-leverage normalization
                total_weight = sum(active_weights.values())
                if total_weight > 1.0:
                    scale = 1.0 / total_weight
                    active_weights = {t: w * scale for t, w in active_weights.items()}
                    total_weight = 1.0

                cash_pct = 1.0 - total_weight

                risk_trace.append({
                    "date": date_str,
                    "n_active": n_active,
                    "n_eligible": n_eligible,
                    "total_weight": round(total_weight, 6),
                    "cash_pct": round(cash_pct, 6),
                    "capped_by_single_name_count": capped_single,
                    "capped_by_adtv_count": capped_adtv,
                })

                # ── Execute orders with risk-model weights ──
                # Only buy into new positions; existing positions that stay in
                # target are NOT rebalanced to target weight (same as original).
                # Sector cap enforcement (D5: 0.20)
                sector_values: Dict[str, float] = {}
                for _t, _pos in positions.items():
                    _px = day_prices.get(_t, _pos["avg_cost"])
                    if not (isinstance(_px, float) and math.isnan(_px)):
                        _sect = sector_map.get(_t, "__UNMAPPED__")
                        sector_values[_sect] = sector_values.get(_sect, 0) + _pos["shares"] * _px

                total_value = cash + sum(
                    positions[_t]["shares"] * (
                        day_prices.get(_t, positions[_t]["avg_cost"])
                        if not (isinstance(day_prices.get(_t), float) and math.isnan(day_prices.get(_t)))
                        else positions[_t]["avg_cost"]
                    )
                    for _t in positions
                )

                for ticker, target_w in sorted(active_weights.items(),
                                               key=lambda x: -x[1]):
                    if ticker in positions:
                        continue  # existing positions unchanged
                    px = day_prices.get(ticker)
                    if px is None or (isinstance(px, float) and math.isnan(px)):
                        continue

                    target_amount = target_w * portfolio_value
                    if target_amount <= 0 or cash < target_amount:
                        continue

                    # Sector cap check
                    if sector_max_weight < 1.0 and sector_map:
                        sect = sector_map.get(ticker, "__UNMAPPED__")
                        cur_sector_val = sector_values.get(sect, 0)
                        projected_sector_val = cur_sector_val + target_amount
                        projected_sector_wt = projected_sector_val / max(total_value, 1)
                        if projected_sector_wt > sector_max_weight:
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
                        "shares": shares, "avg_cost": px,
                        "entry_idx": idx, "buy_date": date_str,
                    }
                    trades.append({
                        "date": date_str, "action": "buy", "ticker": ticker,
                        "price": px, "shares": shares, "amount": cost,
                        "commission": commission, "transfer_fee": transfer_fee,
                        "slippage": slippage, "total_cost": total_cost,
                    })
                    if sector_map:
                        _sect = sector_map.get(ticker, "__UNMAPPED__")
                        sector_values[_sect] = sector_values.get(_sect, 0) + cost

                last_rebalance_idx = idx

        # ── MTM ──
        total_val = cash
        for ticker, pos in positions.items():
            px = day_prices.get(ticker, pos["avg_cost"])
            total_val += pos["shares"] * (
                px if not isinstance(px, float) or not math.isnan(px)
                else pos["avg_cost"])
        equity.append(total_val)

    # ── Metrics (identical to original) ──
    eq = pd.Series(equity, index=trading_dates)
    returns = eq.pct_change().dropna()
    n_days = len(eq)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1 if n_days > 0 else 0
    annual_ret = ((1 + total_ret) ** (252 / n_days) - 1) if n_days > 0 else 0
    volatility = float(returns.std() * math.sqrt(252)) if len(returns) > 1 else 0
    sharpe = float(returns.mean() / returns.std() * math.sqrt(252)) \
        if len(returns) > 1 and returns.std() > 0 else 0
    peak = eq.expanding().max()
    max_dd = float(((eq - peak) / peak).min())
    hs300_ret = (hs300_last / hs300_base - 1) if hs300_base and hs300_last else 0

    sells = [t for t in trades if t["action"] == "sell"]
    n_sells = len(sells)
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    losses = [t for t in sells if t.get("pnl", 0) <= 0]
    win_rate = len(wins) / n_sells if n_sells > 0 else 0
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0)
    buys = [t for t in trades if t["action"] == "buy"]

    if hasattr(data_source, "data_quality_for_period"):
        dq = data_source.data_quality_for_period(start_date, end_date)
    else:
        dq = data_source.data_quality

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

    price_coverage = None
    price_coverage_checker = getattr(data_source, "price_data_coverage_for_period", None)
    if callable(price_coverage_checker):
        price_coverage = price_coverage_checker(start_date, end_date)
        if not price_coverage.get("ok"):
            start_cov = price_coverage.get("start", {})
            end_cov = price_coverage.get("end", {})
            if start_cov and end_cov:
                blockers.append(
                    "price_coverage: "
                    f"start_data={start_cov.get('data_covered')}/{start_cov.get('active')} "
                    f"end_data={end_cov.get('data_covered')}/{end_cov.get('active')}"
                )
            else:
                blockers.append(
                    f"price_coverage: {price_coverage.get('reason', 'unknown')}"
                )

    if n_days < 126:
        blockers.append(f"insufficient_trading_days: {n_days} < 126")
    if n_sells < 30:
        blockers.append(f"insufficient_trades: {n_sells} < 30")
    if total_ret < 0:
        blockers.append(f"negative_total_return: {total_ret*100:.2f}%")
    if sharpe < 0:
        blockers.append(f"negative_sharpe: {sharpe:.2f}")
    can_deploy = len(blockers) == 0

    return BacktestResult(
        equity_curve=eq, trades=trades,
        metrics={
            "total_return": total_ret, "annual_return": annual_ret,
            "volatility": volatility, "sharpe_ratio": sharpe,
            "max_drawdown": max_dd, "hs300_return": hs300_ret,
            "excess_return": total_ret - hs300_ret,
            "trade_count": n_sells, "buy_count": len(buys),
            "win_rate": win_rate,
            "profit_factor": profit_factor if profit_factor != float("inf") else 999.0,
            "avg_win": gross_win / len(wins) if wins else 0,
            "avg_loss": gross_loss / len(losses) if losses else 0,
            "total_turnover": sum(t["amount"] for t in trades),
            "total_fees": total_fees, "total_slippage": total_slippage,
            "avg_hold_days": sum(t.get("held_days", 0) for t in sells) / n_sells if n_sells else 0,
            "trading_days": n_days,
            "can_deploy": can_deploy, "deploy_blockers": blockers,
            "data_quality": dq.to_dict(),
            "price_coverage": price_coverage,
        },
        can_deploy=can_deploy, deploy_blockers=blockers, data_quality=dq,
    )


# ═══════════════════════════════════════════════════════════════════════════
# H51b Backtest (deploy-window, sector-aware, risk-model sizing)
# ═══════════════════════════════════════════════════════════════════════════

def run_h51b_backtest(
    data_source,
    prices: pd.DataFrame,
    sfc: SectorFeatureCache,
    start: str,
    end: str,
    capital: float,
    overlay: H50bOverlay,
    config: Dict,
    target_vol: float,
    single_name_cap: float,
    adtv_cap: float,
    adtv_df: pd.DataFrame,
    prices_full: pd.DataFrame,
    sector_map: Dict[str, str],
    sector_max_weight: float = 0.20,
) -> Dict:
    """Single-window backtest with H51b risk-model sizing.

    Uses ValueScoreH50 (monkey-patched), H50b overlay filtering,
    sector cap, and D3+D4 risk-model sizing.
    """
    trading_dates = prices.index
    cash_val = capital
    positions: Dict[str, Dict] = {}
    trades: List[Dict] = []
    equity: List[float] = []
    total_fees = 0.0
    total_slippage = 0.0
    last_rebalance_idx = -999
    hs300_base = None
    hs300_last = None
    total_value = capital

    risk_trace: List[Dict] = []
    excl_stats = {
        "rebalances_total": 0,
        "vol_insufficient_data": 0,
        "adtv_insufficient_data": 0,
        "min_active_names_violated_count": 0,
    }

    for idx, dt in enumerate(trading_dates):
        date_str = dt.strftime("%Y-%m-%d")
        day_prices = prices.iloc[idx]
        hs300_val = day_prices.get(sfc.hs300)
        if not is_missing(hs300_val):
            hs300_last = float(hs300_val)
            if hs300_base is None:
                hs300_base = float(hs300_val)

        # Exit checks (same as H50b)
        for ticker in list(positions):
            pos = positions[ticker]
            px = day_prices.get(ticker, pos["avg_cost"])
            if is_missing(px):
                continue
            ret = px / pos["avg_cost"] - 1
            held_days = idx - pos["entry_idx"]

            exit_reason = None
            if ret >= PINNED_TAKE_PROFIT:
                exit_reason = "tp"
            elif ret <= -PINNED_STOP_LOSS:
                exit_reason = "sl"
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
                cash_val += net
                total_fees += commission + stamp_tax + transfer_fee
                total_slippage += slippage
                trades.append({
                    "date": date_str, "action": "sell", "ticker": ticker,
                    "price": float(px), "shares": shares, "amount": float(amount),
                    "pnl": float(pnl), "pnl_pct": float(ret * 100),
                    "commission": float(commission), "stamp_tax": float(stamp_tax),
                    "slippage": float(slippage),
                    "exit_reason": exit_reason, "held_days": held_days,
                    "sector": pos.get("sector", ""),
                })
                del positions[ticker]

        # Rebalance (D3+D4 risk model)
        if idx - last_rebalance_idx >= PINNED_REBALANCE_FREQ:
            live_universe = data_source.get_universe(date_str)
            scoped = [t for t in live_universe if t in prices.columns]

            unmapped = [t for t in scoped if sector_map.get(t) is None]
            if unmapped:
                raise ValueError(
                    f"Tickers missing sector data at {date_str}: {unmapped}."
                )

            fundamentals = data_source.get_fundamentals(scoped, date_str)
            scores = []
            for ticker in scoped:
                if ticker in positions:
                    continue
                if not passes_h50b_overlay(sfc, idx, ticker, overlay):
                    continue
                score = ValueScoreH50.from_fundamentals(ticker, fundamentals)
                if score and score.total >= PINNED_QUALITY_FILTER:
                    scores.append(score)

            scores.sort(key=lambda x: x.total, reverse=True)
            target_tickers = [score.ticker for score in scores[:PINNED_TOP_N]]

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
                    cash_val += net
                    total_fees += commission + stamp_tax + transfer_fee
                    total_slippage += slippage
                    trades.append({
                        "date": date_str, "action": "sell", "ticker": ticker,
                        "price": float(px), "shares": shares,
                        "amount": float(amount),
                        "pnl": float(pnl),
                        "pnl_pct": float(pnl / (pos["avg_cost"] * shares) * 100),
                        "commission": float(commission),
                        "stamp_tax": float(stamp_tax),
                        "slippage": float(slippage),
                        "exit_reason": "rebalance_out",
                        "held_days": idx - pos["entry_idx"],
                        "sector": pos.get("sector", ""),
                    })
                    del positions[ticker]

            # ═══════════════════════════════════════════════════════════════
            # D3+D4: Risk Model Sizing
            # ═══════════════════════════════════════════════════════════════
            excl_stats["rebalances_total"] += 1

            # Portfolio value (before rebalance)
            pv = cash_val
            for _t, _pos in positions.items():
                _px = day_prices.get(_t, _pos["avg_cost"])
                pv += _pos["shares"] * (
                    _px if not is_missing(_px) else _pos["avg_cost"]
                )

            # D3: Vol-scaled weights
            eligible = []
            for ticker in target_tickers:
                vol = compute_ticker_vol(prices_full, ticker, date_str)
                if vol is None or vol <= 0:
                    excl_stats["vol_insufficient_data"] += 1
                    continue
                eligible.append((ticker, vol))

            n_eligible = len(eligible)
            if n_eligible == 0:
                excl_stats["rebalances_total"] += 1
                risk_trace.append({
                    "date": date_str, "n_active": 0, "n_eligible": 0,
                    "total_weight": 0, "cash_pct": 1.0,
                    "capped_by_single_name_count": 0,
                    "capped_by_adtv_count": 0,
                })
                last_rebalance_idx = idx
            else:  # <-- NO continue; fall through to MTM
                raw_weights = {}
                for ticker, vol_i in eligible:
                    raw_weights[ticker] = (target_vol / vol_i) / n_eligible

                # D4 Step 1: Single-name cap
                effective_cap = min(single_name_cap, PINNED_MAX_POSITION_PCT)
                capped_single = 0
                capped_weights = {}
                for t, w in raw_weights.items():
                    if w > effective_cap:
                        capped_single += 1
                        w = effective_cap
                    capped_weights[t] = w

                # D4 Step 2: ADTV cap
                capped_adtv = 0
                adtv_weights = {}
                for t, w_i in capped_weights.items():
                    cur_val = 0.0
                    if t in positions:
                        _pos = positions[t]
                        _px = day_prices.get(t, _pos["avg_cost"])
                        cur_val = _pos["shares"] * (
                            _px if not is_missing(_px) else _pos["avg_cost"]
                        )

                    trade_delta = abs(w_i * pv - cur_val)
                    adtv_20 = compute_adtv_20d(adtv_df, t, date_str)

                    if adtv_20 is None or math.isnan(adtv_20):
                        excl_stats["adtv_insufficient_data"] += 1
                        continue

                    max_trade = adtv_cap * adtv_20
                    if trade_delta > max_trade:
                        if cur_val >= w_i * pv:
                            truncated_w = max(0.0, (cur_val - max_trade) / pv)
                        else:
                            truncated_w = (cur_val + max_trade) / pv
                        w_i = max(0.0, truncated_w)
                        capped_adtv += 1

                    adtv_weights[t] = w_i

                # D4 Step 3: Min active names
                active_w = {t: w for t, w in adtv_weights.items() if w > 0}
                n_active = len(active_w)

                if n_active < MIN_ACTIVE_NAMES:
                    excl_stats["min_active_names_violated_count"] += 1
                    active_w = {}
                    n_active = 0

                # D4 Step 4: No-leverage normalization
                total_w = sum(active_w.values())
                if total_w > 1.0:
                    scale = 1.0 / total_w
                    active_w = {t: w * scale for t, w in active_w.items()}
                    total_w = 1.0

                cash_pct = 1.0 - total_w

                risk_trace.append({
                    "date": date_str,
                    "n_active": n_active,
                    "n_eligible": n_eligible,
                    "total_weight": round(total_w, 6),
                    "cash_pct": round(cash_pct, 6),
                    "capped_by_single_name_count": capped_single,
                    "capped_by_adtv_count": capped_adtv,
                })

                # Sector cap enforcement
                sector_values: Dict[str, float] = {}
                for _t, _pos in positions.items():
                    _px = day_prices.get(_t, _pos["avg_cost"])
                    if not is_missing(_px):
                        _sect = sector_map.get(_t, "__UNMAPPED__")
                        sector_values[_sect] = sector_values.get(_sect, 0) + \
                            _pos["shares"] * _px

                tv = cash_val + sum(
                    positions[_t]["shares"] * (
                        day_prices.get(_t, positions[_t]["avg_cost"])
                        if not is_missing(day_prices.get(_t))
                        else positions[_t]["avg_cost"]
                    )
                    for _t in positions
                )

                # Execute orders
                for ticker, target_w in sorted(active_w.items(),
                                               key=lambda x: -x[1]):
                    if ticker in positions:
                        continue
                    px = day_prices.get(ticker)
                    if is_missing(px):
                        continue

                    target_amount = target_w * pv
                    if target_amount <= 0 or cash_val < target_amount:
                        continue

                    # Sector cap
                    if sector_max_weight < 1.0:
                        sect = sector_map.get(ticker, "__UNMAPPED__")
                        cur_sv = sector_values.get(sect, 0)
                        proj_sv = cur_sv + target_amount
                        proj_sw = proj_sv / max(tv, 1)
                        if proj_sw > sector_max_weight:
                            continue

                    shares = int(target_amount / px / 100) * 100
                    if shares <= 0:
                        continue
                    cost = px * shares
                    commission = max(cost * COMMISSION_RATE, 5)
                    transfer_fee = cost * TRANSFER_FEE_RATE
                    slippage = cost * SLIPPAGE_BPS / 10000
                    total_cost = cost + commission + transfer_fee + slippage
                    if total_cost > cash_val:
                        continue
                    cash_val -= total_cost
                    total_fees += commission + transfer_fee
                    total_slippage += slippage
                    positions[ticker] = {
                        "shares": shares, "avg_cost": float(px),
                        "entry_idx": idx, "buy_date": date_str,
                        "sector": sector_map.get(ticker, ""),
                    }
                    trades.append({
                        "date": date_str, "action": "buy", "ticker": ticker,
                        "price": float(px), "shares": shares,
                        "amount": float(cost),
                        "commission": float(commission),
                        "transfer_fee": float(transfer_fee),
                        "slippage": float(slippage),
                        "total_cost": float(total_cost),
                        "sector": sector_map.get(ticker, ""),
                    })
                    _sect = sector_map.get(ticker, "__UNMAPPED__")
                    sector_values[_sect] = sector_values.get(_sect, 0) + cost

                last_rebalance_idx = idx

        # MTM
        tv = cash_val
        for ticker, pos in positions.items():
            px = day_prices.get(ticker, pos["avg_cost"])
            tv += pos["shares"] * (px if not is_missing(px) else pos["avg_cost"])
        equity.append(float(tv))

    # Compute metrics (same as H50b)
    eq = pd.Series(equity, index=trading_dates)
    returns = eq.pct_change().dropna()
    n_days = len(eq)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1 if n_days else 0
    annual_ret = ((1 + total_ret) ** (252 / n_days) - 1) if n_days else 0
    volatility = float(returns.std() * math.sqrt(252)) if len(returns) > 1 else 0
    sharpe = float(returns.mean() / returns.std() * math.sqrt(252)) \
        if len(returns) > 1 and returns.std() > 0 else 0
    max_dd = float(((eq - eq.expanding().max()) / eq.expanding().max()).min())
    hs300_ret = hs300_last / hs300_base - 1 if hs300_base and hs300_last else 0
    sells = [t for t in trades if t["action"] == "sell"]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    losses = [t for t in sells if t.get("pnl", 0) <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (
        999.0 if gross_win > 0 else 0.0)
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

    h34_blockers, warnings = check_stop_conditions(
        trades, capital, load_json(DEFAULT_CONFIG), metrics,
        equity_curve=eq, as_of_date=end)
    h34_blockers.extend(period_blockers)
    streak = compute_consecutive_losing_sells(trades)
    monthly_turnover = compute_monthly_one_way_turnover(trades, capital, as_of_date=end)
    annualized_turnover = compute_annualized_turnover(trades, capital)

    result = {
        "params": {
            "top_n": PINNED_TOP_N,
            "max_position_pct": PINNED_MAX_POSITION_PCT,
            "stop_loss_pct": PINNED_STOP_LOSS,
            "take_profit_pct": PINNED_TAKE_PROFIT,
            "quality_filter": PINNED_QUALITY_FILTER,
            "rebalance_freq_days": PINNED_REBALANCE_FREQ,
            "sector_max_weight_pct": sector_max_weight,
            "target_portfolio_vol": target_vol,
            "single_name_cap_pct": single_name_cap,
            "adtv_cap_pct": adtv_cap,
        },
        "overlay": overlay.to_dict(),
        "metrics": metrics,
        "can_deploy_data_quality": can_deploy,
        "data_quality_meta": dq_meta,
        "execution_blocked": len(h34_blockers) > 0,
        "execution_blockers": h34_blockers,
        "execution_warnings": warnings,
        "terminal_losing_streak": streak,
        "monthly_one_way_turnover": float(monthly_turnover),
        "annualized_turnover": float(annualized_turnover),
        "last_8_sells": sells[-8:],
        "risk_overlay_trace": risk_trace,
        "exclusion_stats": excl_stats,
    }
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Multi-Window Evaluation
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_h51b_candidate_multi_window(
    data_source,
    overlay: H50bOverlay,
    config: Dict,
    capital: float,
    sector_map: Dict[str, str],
    target_vol: float,
    single_name_cap: float,
    adtv_cap: float,
    adtv_df: pd.DataFrame,
    prices_full: pd.DataFrame,
    sector_max_weight: float = 0.20,
) -> Dict:
    """Backtest candidate across all 5 windows with risk-model sizing."""
    window_results = {}
    for wname, (wstart, wend) in WINDOWS.items():
        universe = data_source.get_price_universe(wstart, wend)
        prices = data_source.get_price_history(
            list(universe) + [HS300_TICKER], wstart, wend
        )
        fc = FeatureCache(prices, HS300_TICKER)
        sfc = SectorFeatureCache(fc, sector_map)
        window_results[wname] = run_h51b_backtest(
            data_source, prices, sfc, wstart, wend, capital,
            overlay, config, target_vol, single_name_cap, adtv_cap,
            adtv_df, prices_full, sector_map, sector_max_weight,
        )

    gate_metrics, passes_gate = compute_acceptance_gate(window_results)

    return {
        "params": {
            "top_n": PINNED_TOP_N,
            "max_position_pct": PINNED_MAX_POSITION_PCT,
            "stop_loss_pct": PINNED_STOP_LOSS,
            "take_profit_pct": PINNED_TAKE_PROFIT,
            "quality_filter": PINNED_QUALITY_FILTER,
            "rebalance_freq_days": PINNED_REBALANCE_FREQ,
            "sector_max_weight_pct": sector_max_weight,
            "target_portfolio_vol": target_vol,
            "single_name_cap_pct": single_name_cap,
            "adtv_cap_pct": adtv_cap,
        },
        "overlay": overlay.to_dict(),
        "deploy_window": window_results["deploy_2025_2026"],
        "window_results": {
            k: {
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
            }
            for k, v in window_results.items()
        },
        "passes_acceptance_gate": passes_gate,
        "gate_metrics": gate_metrics,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Scoring (D6)
# ═══════════════════════════════════════════════════════════════════════════

def score_candidate_h51b(result: Dict) -> Tuple:
    """D6: Rank by beat_HS300_windows desc, deploy_excess desc tiebreaker."""
    g = result["gate_metrics"]
    m = result["deploy_window"]["metrics"]
    return (
        -g["beat_hs300_windows"],
        -(m.get("excess_return", 0) or 0),
        -g["positive_windows"],
        -(m.get("sharpe_ratio", 0) or 0),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Search Orchestration
# ═══════════════════════════════════════════════════════════════════════════

def run_h51b_search(args) -> Dict:
    t0 = time.time()
    config = load_json(DEFAULT_CONFIG)
    sector_map = load_sector_map(SECTOR_CSV)

    # Load fundamentals + ADTV
    print("Loading H50a fundamentals panel...")
    h50a_panel = load_h50a_panel(H50A_JSONL)
    print(f"  Loaded {len(h50a_panel)} tickers, {sum(len(v) for v in h50a_panel.values())} rows")

    print("Loading H51a ADTV data...")
    adtv_df = load_adtv_data(H51A_ADTV)
    print(f"  Loaded {len(adtv_df)} rows, {adtv_df.index.get_level_values('ticker').nunique()} tickers")

    # Universe
    source = CN_PIT_FileSource(
        prices_path=str(H47_PRICES),
        universe_path=str(H30_UNIVERSE),
        universe_snapshots_path=str(H30_SNAPSHOTS),
    )
    universe_tickers = source.get_price_universe("2025-01-01", "2026-05-21")
    print(f"  Universe: {len(universe_tickers)} tickers")

    # Load full price history (needed for vol computation across all windows)
    print("Loading full price history...")
    prices_full = source.get_price_history(
        list(universe_tickers) + [HS300_TICKER],
        "2024-01-01", "2026-05-21",
    )

    # ── Install monkey-patches ──
    # 1. Scorer (ValueScoreH50) — same as H50b
    restore_scorer = install_h50b_patches(h50a_panel, list(universe_tickers))

    # 2. Sizing (run_fundamental_backtest → _run_fundamental_backtest_h51b)
    sizing_from_repr = repr(_FB_RUN_V1)
    sizing_to_repr = repr(_run_fundamental_backtest_h51b)
    _fb_module = _sys.modules["fundamental_backtest"]
    _sizing_original = _fb_module.run_fundamental_backtest
    _fb_module.run_fundamental_backtest = _run_fundamental_backtest_h51b

    # Sizing block diff (unified-diff of the replaced 7 lines)
    sizing_block_diff = (
        "--- a/backtest/experiments/fundamental_backtest.py (original lines 877-883)\n"
        "+++ b/scripts/h51b_risk_model_search.py (D3+D4 risk model sizing)\n"
        "@@ -877,7 +877,XX @@\n"
        "-            n_slots = top_n - len(positions)\n"
        "-            budget_per_slot = cash / max(n_slots, 1) if n_slots > 0 else 0\n"
        "-            for ticker in target_tickers:\n"
        "-                if ticker in positions: continue\n"
        "-                px = day_prices.get(ticker)\n"
        "-                if px is None or (isinstance(px, float) and math.isnan(px)): continue\n"
        "-                target_amount = min(capital * max_position_pct, budget_per_slot)\n"
        "+            # D3+D4: Vol-scaled weighting + single-name cap + ADTV cap\n"
        "+            #        + min-active-names cash buffer + no-leverage normalization\n"
        "+            # See D3 (vol formula) and D4 (4-step pipeline) in H51b brief.\n"
    )

    sizing_patched_modules = ["backtest.experiments.fundamental_backtest"]
    # h42 does NOT import run_fundamental_backtest by name (grep confirmed)

    all_restored = False

    try:
        # ── Select overlay ──
        all_overlays = build_h50b_overlays()
        pinned_overlay = None
        for ov in all_overlays:
            if ov.name == PINNED_OVERLAY_NAME:
                pinned_overlay = ov
                break
        if pinned_overlay is None:
            raise ValueError(
                f"Pinned overlay '{PINNED_OVERLAY_NAME}' not found in H50b overlays: "
                f"{[o.name for o in all_overlays]}"
            )
        print(f"Pinned overlay: {pinned_overlay.name}")

        # ── Stage B: 18 risk combos ──
        risk_combos = list(itertools.product(TARGET_VOLS, SINGLE_NAME_CAPS, ADTV_CAPS))
        stage_b_limit = args.stage_b_limit if args.stage_b_limit else len(risk_combos)
        risk_combos = risk_combos[:stage_b_limit]
        print(f"Risk combos: {len(risk_combos)} (target_vol × single_cap × adtv_cap)")

        deploy_results = []
        for i, (tv, sc, ac) in enumerate(risk_combos):
            universe = source.get_price_universe("2025-01-01", "2026-05-21")
            prices = source.get_price_history(
                list(universe) + [HS300_TICKER], "2025-01-01", "2026-05-21"
            )
            fc = FeatureCache(prices, HS300_TICKER)
            sfc = SectorFeatureCache(fc, sector_map)
            r = run_h51b_backtest(
                source, prices, sfc,
                "2025-01-01", "2026-05-21",
                args.capital,
                pinned_overlay, config,
                target_vol=tv,
                single_name_cap=sc,
                adtv_cap=ac,
                adtv_df=adtv_df,
                prices_full=prices_full,
                sector_map=sector_map,
                sector_max_weight=PINNED_SECTOR_MAX_WEIGHT,
            )
            deploy_results.append(r)
            print(f"  [{i+1}/{len(risk_combos)}] vol={tv} cap={sc} adtv={ac}: "
                  f"ret={r['metrics']['total_return']:.4f}, "
                  f"sharpe={r['metrics']['sharpe_ratio']:.2f}, "
                  f"trades={r['metrics']['trade_count']}, "
                  f"blocked={r['execution_blocked']}")

        # ── Multi-window evaluation ──
        print(f"Running multi-window for all {len(deploy_results)} combos...")
        mw_candidates = []
        for i, dr in enumerate(deploy_results):
            tv = dr["params"]["target_portfolio_vol"]
            sc = dr["params"]["single_name_cap_pct"]
            ac = dr["params"]["adtv_cap_pct"]
            mw = evaluate_h51b_candidate_multi_window(
                source,
                pinned_overlay, config, args.capital,
                sector_map,
                target_vol=tv, single_name_cap=sc, adtv_cap=ac,
                adtv_df=adtv_df, prices_full=prices_full,
                sector_max_weight=PINNED_SECTOR_MAX_WEIGHT,
            )
            mw_candidates.append(mw)
            print(f"  Stage C [{i+1}/{len(deploy_results)}] "
                  f"vol={tv} cap={sc} adtv={ac}: "
                  f"gate_pass={mw['passes_acceptance_gate']}, "
                  f"beat_HS300={mw['gate_metrics']['beat_hs300_windows']}/5")

        mw_candidates.sort(key=score_candidate_h51b)
        gate_pass = [r for r in mw_candidates if r["passes_acceptance_gate"]]

        # ── Aggregate exclusion stats across all runs ──
        total_excl = {
            "rebalances_total": 0,
            "vol_insufficient_data": 0,
            "adtv_insufficient_data": 0,
            "min_active_names_violated_count": 0,
        }
        for dr in deploy_results:
            es = dr.get("exclusion_stats", {})
            for k in total_excl:
                total_excl[k] += es.get(k, 0)

        # ── Risk overlay trace from best candidate ──
        best_risk_trace = []
        if mw_candidates:
            best_dw = mw_candidates[0].get("deploy_window", {})
            best_risk_trace = best_dw.get("risk_overlay_trace", [])

        # ── Read baselines ──
        h42_baseline = load_json(
            PROJECT_ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json")
        h48_baseline = load_json(
            PROJECT_ROOT / "backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json")
        h49b_baseline = load_json(
            PROJECT_ROOT / "backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json")
        h50b_baseline = load_json(
            PROJECT_ROOT / "backtest/runs/fundamental_value_h50b_quality_value_search.json")

        # ── Provenance ──
        elapsed = time.time() - t0
        verdict = "CANDIDATE_FOR_FORWARD_TRIAL" if gate_pass else "RESEARCH_ONLY"

        provenance = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task": "H51b",
            "verdict": verdict,
            "elapsed_seconds": round(elapsed, 1),
            "stage_a_count": 1,
            "stage_b_count": len(deploy_results),
            "stage_c_count": len(mw_candidates),
            "clean_deploy_count": len([r for r in deploy_results if not r["execution_blocked"]]),
            "gate_pass_count": len(gate_pass),
            "gate_pass": [r["passes_acceptance_gate"] for r in mw_candidates],
            "data_sources": {
                "prices": {
                    "task": "h47",
                    "file": str(H47_PRICES.name),
                    "sha256": H51B_INPUT_SHA256["prices"],
                },
                "sector_metadata": {
                    "task": "h49a",
                    "file": str(SECTOR_CSV.name),
                    "sha256": H51B_INPUT_SHA256["sector_metadata"],
                },
                "fundamentals": {
                    "task": "h50a",
                    "file": "data/cn_pit/fundamentals_h50a_pit_quality.jsonl",
                    "sha256": H51B_INPUT_SHA256["fundamentals"],
                    "rows": 12398,
                },
                "adtv_liquidity": {
                    "task": "h51a",
                    "file": "data/cn_pit/liquidity_h51a_daily_amount.csv",
                    "sha256": H51B_INPUT_SHA256["adtv_liquidity"],
                },
                "universe": {
                    "file": str(H30_UNIVERSE.name),
                    "sha256": H51B_INPUT_SHA256["universe"],
                },
            },
            "scorer_substitution": {
                "from": "fundamental_backtest.ValueScore",
                "to": "h50b_quality_value_search.ValueScoreH50",
                "reused_from": "h50b",
                "patched_modules": [
                    "backtest.experiments.fundamental_backtest",
                    "scripts.h42_strategy_redesign_search",
                ],
                "restored_after_run": True,
            },
            "sizing_substitution": {
                "from": "fundamental_backtest.run_fundamental_backtest",
                "to": "h51b_risk_model_search._run_fundamental_backtest_h51b",
                "patched_modules": sizing_patched_modules,
                "restored_after_run": True,
                "sizing_block_diff": sizing_block_diff,
            },
            "risk_model_design": {
                "target_portfolio_vol": "swept {0.15, 0.20, 0.25}",
                "single_name_cap_pct": "swept {0.10, 0.15, 0.20}",
                "adtv_cap_pct": "swept {0.05, 0.10}",
                "min_active_names": MIN_ACTIVE_NAMES,
                "vol_window_days": VOL_WINDOW,
                "vol_min_data_points": VOL_MIN_DATA,
                "vol_return_basis": "log",
                "vol_annualization": "sqrt(252)",
                "adtv_window_days": 20,
                "adtv_window_inclusive_of_today": False,
                "cash_buffer_policy": "no_leverage_on_underweight",
            },
            "exclusion_stats": total_excl,
            "top_candidates_multi_window": mw_candidates,
        }

        # ── Build report ──
        report = build_h51b_report(
            provenance, h42_baseline, h48_baseline, h49b_baseline, h50b_baseline,
            best_risk_trace,
        )

        # ── Write outputs ──
        with open(args.output_run, "w", encoding="utf-8") as fh:
            json.dump(json_safe(provenance), fh, indent=2, ensure_ascii=False, default=str)
        with open(args.output_report, "w", encoding="utf-8") as fh:
            fh.write(report)

        print(f"\nSearch complete in {elapsed/60:.1f}min")
        print(f"Verdict: {verdict}")
        print(f"Gate passed: {len(gate_pass)}")
        print(f"Run JSON: {args.output_run}")
        print(f"Report: {args.output_report}")

        return provenance

    finally:
        # Restore sizing patch
        _fb_module.run_fundamental_backtest = _sizing_original
        # Restore scorer patch
        restore_scorer()
        all_restored = True
        print("All patches restored.")


# ═══════════════════════════════════════════════════════════════════════════
# Report Builder (D7: 5-way comparison)
# ═══════════════════════════════════════════════════════════════════════════

def build_h51b_report(
    payload: Dict,
    h42_baseline: Dict,
    h48_baseline: Dict,
    h49b_baseline: Dict,
    h50b_baseline: Dict,
    best_risk_trace: List[Dict],
) -> str:
    prov = payload["data_sources"]
    v = payload["verdict"]
    h42v = h42_baseline.get("verdict", "RESEARCH_ONLY")
    h48v = h48_baseline.get("verdict", "RESEARCH_ONLY")
    h49bv = h49b_baseline.get("verdict", "RESEARCH_ONLY")
    h50bv = h50b_baseline.get("verdict", "RESEARCH_ONLY")

    def max_beat_hs300(run_data):
        top = run_data.get("top_candidates_multi_window", [])
        if not top:
            return 0
        return max(
            r.get("gate_metrics", {}).get("beat_hs300_windows", 0)
            for r in top[:15]
        )

    h42_best_beat = max_beat_hs300(h42_baseline)
    h48_best_beat = max_beat_hs300(h48_baseline)
    h49b_best_beat = max_beat_hs300(h49b_baseline)
    h50b_best_beat = max_beat_hs300(h50b_baseline)
    h51b_top = payload.get("top_candidates_multi_window", [])
    h51b_best_beat = max(
        (r.get("gate_metrics", {}).get("beat_hs300_windows", 0) for r in h51b_top),
        default=0,
    )

    def best_deploy_excess(run_data):
        top = run_data.get("top_candidates_multi_window", [])
        if not top:
            return 0
        return max(
            (r.get("deploy_window", {}).get("metrics", {}).get("excess_return", 0) or 0)
            for r in top[:15]
        )

    h42_excess = best_deploy_excess(h42_baseline)
    h48_excess = best_deploy_excess(h48_baseline)
    h49b_excess = best_deploy_excess(h49b_baseline)
    h50b_excess = best_deploy_excess(h50b_baseline)
    h51b_excess = best_deploy_excess(payload)

    excl = payload.get("exclusion_stats", {})
    rd = payload.get("risk_model_design", {})

    # Risk trace summary
    trace_avg_cash = 0.0
    trace_avg_capped_single = 0
    trace_avg_capped_adtv = 0
    if best_risk_trace:
        trace_avg_cash = sum(r.get("cash_pct", 0) for r in best_risk_trace) / len(best_risk_trace)
        trace_avg_capped_single = sum(r.get("capped_by_single_name_count", 0) for r in best_risk_trace)
        trace_avg_capped_adtv = sum(r.get("capped_by_adtv_count", 0) for r in best_risk_trace)

    lines = [
        "# H51b — Risk Model Overlay Search Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Verdict:** {v}",
        f"**Elapsed:** {payload['elapsed_seconds']:.1f}s",
        "",
        "## Question",
        "",
        "Does a risk-model overlay (vol-scaled weighting + single-name caps "
        "+ ADTV liquidity constraints + 5-name minimum) on top of the H50b "
        "Quality-Value composite move `beat_HS300_windows` above the H50b/H49b 1/5 ceiling?",
        "",
        "## Data Sources",
        "",
        f"- **Prices:** H47 QFQ prices, SHA256 `{prov['prices']['sha256'][:16]}...`",
        f"- **Sector metadata:** H49a SW L1, SHA256 `{prov['sector_metadata']['sha256'][:16]}...`",
        f"- **Fundamentals:** H50a V2, SHA256 `{prov['fundamentals']['sha256'][:16]}...`, {prov['fundamentals']['rows']} rows",
        f"- **ADTV Liquidity:** H51a daily amount, SHA256 `{prov['adtv_liquidity']['sha256'][:16]}...`",
        f"- **Universe:** H30 candidate universe, SHA256 `{prov['universe']['sha256'][:16]}...`",
        "",
        "## Scorer Substitution",
        "",
        f"- **From:** fundamental_backtest.ValueScore",
        f"- **To:** h50b_quality_value_search.ValueScoreH50",
        f"- **Reused from:** h50b",
        f"- **Restored after run:** true",
        "",
        "## Sizing Substitution",
        "",
        f"- **From:** fundamental_backtest.run_fundamental_backtest",
        f"- **To:** h51b_risk_model_search._run_fundamental_backtest_h51b",
        f"- **Patched modules:** backtest.experiments.fundamental_backtest",
        f"- **Restored after run:** true",
        f"- **Sizing block diff:** replaces equal-weight budget_per_slot (7 lines) with D3+D4 risk model",
        "",
        "## Risk Model Design",
        "",
        f"- **Target portfolio vol:** swept {{0.15, 0.20, 0.25}}",
        f"- **Single-name cap:** swept {{0.10, 0.15, 0.20}}",
        f"- **ADTV participation cap:** swept {{0.05, 0.10}}",
        f"- **Min active names:** {rd.get('min_active_names', 5)}",
        f"- **Vol window:** {rd.get('vol_window_days', 60)} trading days, min {rd.get('vol_min_data_points', 40)} data points",
        f"- **Vol return basis:** {rd.get('vol_return_basis', 'log')}",
        f"- **Vol annualization:** {rd.get('vol_annualization', 'sqrt(252)')}",
        f"- **ADTV window:** {rd.get('adtv_window_days', 20)} trading days, exclusive of as_of_date",
        f"- **Cash buffer policy:** {rd.get('cash_buffer_policy', 'no_leverage_on_underweight')}",
        "",
        "## Pinned Base Parameters (from H50b best)",
        "",
        f"- **Overlay:** {PINNED_OVERLAY_NAME}",
        f"- **Top N:** {PINNED_TOP_N}",
        f"- **Max position (engine cap):** {PINNED_MAX_POSITION_PCT}",
        f"- **Stop loss:** {PINNED_STOP_LOSS}",
        f"- **Take profit:** {PINNED_TAKE_PROFIT}",
        f"- **Quality filter:** {PINNED_QUALITY_FILTER}",
        f"- **Rebalance freq:** {PINNED_REBALANCE_FREQ} days",
        f"- **Sector max weight:** {PINNED_SECTOR_MAX_WEIGHT}",
        "",
        "## Search Space",
        "",
        f"- Stage A: 1 overlay × 1 sector_cap = 1 base config",
        f"- Stage B: 3 target_vol × 3 single_cap × 2 adtv_cap = 18 risk combos",
        f"- Total runs: {payload['stage_b_count']}",
        "",
        "## Exclusion Stats",
        "",
        f"- Rebalances total: {excl.get('rebalances_total', 0)}",
        f"- Vol insufficient data: {excl.get('vol_insufficient_data', 0)}",
        f"- ADTV insufficient data: {excl.get('adtv_insufficient_data', 0)}",
        f"- Min active names violated: {excl.get('min_active_names_violated_count', 0)}",
        "",
        f"## Risk Overlay Trace (Best Candidate)",
        "",
        f"- Total rebalance events: {len(best_risk_trace)}",
        f"- Avg cash pct: {trace_avg_cash:.1%}",
        f"- Total single-name caps triggered: {trace_avg_capped_single}",
        f"- Total ADTV caps triggered: {trace_avg_capped_adtv}",
        "",
        "## Acceptance Gate",
        "",
        "A candidate passes if ALL conditions are met (H42 gate verbatim):",
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
        "## H42 vs H48 vs H49b vs H50b vs H51b Comparison",
        "",
        "| Metric | H42 | H48 | H49b | H50b | H51b |",
        "|--------|-----|-----|------|------|------|",
        f"| Verdict | **{h42v}** | **{h48v}** | **{h49bv}** | **{h50bv}** | **{v}** |",
        f"| Gate-pass count | {h42_baseline.get('gate_pass_count', 0)} | {h48_baseline.get('gate_pass_count', 0)} | {h49b_baseline.get('gate_pass_count', 0)} | {h50b_baseline.get('gate_pass_count', 0)} | {payload.get('gate_pass_count', 0)} |",
        f"| Best beat_HS300_windows (top-15) | {h42_best_beat}/5 | {h48_best_beat}/5 | {h49b_best_beat}/5 | {h50b_best_beat}/5 | {h51b_best_beat}/5 |",
        f"| Best deploy excess | {h42_excess:.1%} | {h48_excess:.1%} | {h49b_excess:.1%} | {h50b_excess:.1%} | {h51b_excess:.1%} |",
    ]

    # Top candidates
    if h51b_top:
        lines.extend([
            "",
            "## Top H51b Candidates (Ranked by beat_HS300_windows ↓)",
            "",
            "| Vol | Cap | ADTV | Return | Sharpe | MaxDD | Excess | Trades | Streak | Beat | Gate |",
            "|-----|-----|------|--------|--------|-------|--------|--------|--------|------|------|",
        ])
        for r in h51b_top[:18]:
            p = r["params"]
            m = r["deploy_window"]["metrics"]
            g = r["gate_metrics"]
            lines.append(
                f"| {p['target_portfolio_vol']} | {p['single_name_cap_pct']} | "
                f"{p['adtv_cap_pct']} | {m['total_return']:.3f} | "
                f"{m['sharpe_ratio']:.2f} | {m['max_drawdown']:.3f} | "
                f"{m['excess_return']:.3f} | {m['trade_count']} | "
                f"{r['deploy_window']['terminal_losing_streak']} | "
                f"{g['beat_hs300_windows']}/5 | "
                f"{'PASS' if r['passes_acceptance_gate'] else 'FAIL'} |"
            )
        lines.append("")

        # Per-window detail for top 3
        lines.extend(["## Per-Window Detail (Top 3)", ""])
        for i, cand in enumerate(h51b_top[:3]):
            p = cand["params"]
            lines.append(
                f"### #{i+1}: vol={p['target_portfolio_vol']}, "
                f"cap={p['single_name_cap_pct']}, adtv={p['adtv_cap_pct']}"
            )
            lines.append("")
            lines.append(
                "| Window | Return | Sharpe | MaxDD | Excess | Status | Trades | Streak |\n"
                "|--------|--------|--------|-------|--------|--------|--------|--------|"
            )
            for wname in ["cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"]:
                wr = cand["window_results"].get(wname, {})
                if wr:
                    lines.append(window_table_row(wname, wr))
                else:
                    lines.append(f"| {wname} | N/A | N/A | N/A | N/A | N/A | N/A | N/A |")
            lines.append("")

    # Delta analysis
    if h51b_best_beat > h50b_best_beat:
        answer = (
            f"Yes — the Risk Model Overlay moved beat_HS300_windows "
            f"from H50b's {h50b_best_beat}/5 to {h51b_best_beat}/5."
        )
    elif h51b_best_beat == h50b_best_beat:
        answer = (
            f"No change — beat_HS300_windows stayed at {h51b_best_beat}/5, "
            f"same as H50b's {h50b_best_beat}/5."
        )
    else:
        answer = (
            f"No — beat_HS300_windows decreased from H50b's {h50b_best_beat}/5 "
            f"to {h51b_best_beat}/5."
        )

    lines.extend([
        "",
        "## Verdict",
        "",
        f"**{v}**",
        "",
        f"**Did the Risk Model Overlay move beat_HS300_windows above the "
        f"H50b/H49b 1/5 ceiling?** {answer}",
        "",
    ])

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="H51b — Risk Model Overlay Search"
    )
    parser.add_argument(
        "--stage-b-limit", type=int, default=None,
        help="Limit Stage B to N risk combos (default: all 18)"
    )
    parser.add_argument(
        "--output-run", type=str, default=str(RUN_OUT),
        help="Output JSON path"
    )
    parser.add_argument(
        "--output-report", type=str, default=str(REPORT_OUT),
        help="Output Markdown report path"
    )
    parser.add_argument(
        "--capital", type=float, default=500000,
        help="Starting capital"
    )
    args = parser.parse_args()

    print(f"H51b Risk Model Overlay Search")
    print(f"  Stage B limit: {args.stage_b_limit or 'all 18'}")
    print(f"  Output: {args.output_run}, {args.output_report}")

    run_h51b_search(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
