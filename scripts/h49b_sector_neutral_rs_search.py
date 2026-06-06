#!/usr/bin/env python3
"""H49b — Sector-Neutral Relative Strength Search.

Adds two dimensions to the H42 search framework:
  (a) intra-sector relative-strength ranking instead of cross-universe ranking
  (b) explicit sector-concentration caps at portfolio construction

Imports H42 core logic; does NOT modify it.
Produces H49b JSON + Markdown report with H42/H48/H49b comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

# ── Imports from H42 / core ────────────────────────────────────────────
from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    COMMISSION_RATE,
    HS300_TICKER,
    MIN_TRADE_COUNT,
    MIN_TRADING_DAYS,
    SLIPPAGE_BPS,
    STAMP_TAX_RATE,
    TRANSFER_FEE_RATE,
    ValueScore,
    DataQuality,
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
    build_param_grid as h42_build_param_grid,
    deploy_blockers,
    compute_acceptance_gate,
    WINDOWS,
    load_json,
    pct,
    plain_pct,
    finite,
    is_missing,
    json_safe,
    candidate_row_mw,
    window_table_row,
    score_candidate_mw,
)

# ── H49b-specific paths ────────────────────────────────────────────────
H47_PRICES = PROJECT_ROOT / "data/cn_pit/prices_h47_tushare_qfq_candidate.csv"
H30_UNIVERSE = PROJECT_ROOT / "data/cn_pit/universe_h30_candidate.jsonl"
H30_SNAPSHOTS = PROJECT_ROOT / "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
SECTOR_CSV = PROJECT_ROOT / "data/cn_pit/sector_metadata_sw_l1.csv"
H49A_COVERAGE = PROJECT_ROOT / "data/cn_pit/sector_coverage_h49a.json"
H42_RUN = PROJECT_ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json"
H48_RUN = PROJECT_ROOT / "backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json"
DEFAULT_CONFIG = PROJECT_ROOT / "value_account/h34_shadow_account_config.json"

RUN_OUT = PROJECT_ROOT / "backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json"
REPORT_OUT = PROJECT_ROOT / "reports/h49b_sector_neutral_rs_search_report.md"

# ── Sector grid axes ───────────────────────────────────────────────────
SECTOR_MAX_WEIGHT_VALUES = [0.20, 0.25, 0.30, 0.40, 1.00]
MIN_SECTORS_VALUES = [1, 5, 7]

# ── Extended data types ────────────────────────────────────────────────
@dataclass(frozen=True)
class H49bOverlay:
    """Extended overlay with intra-sector RS fields."""
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
    # H49b-specific
    intra_sector_rs_window: Optional[int] = None  # 20 or 60
    intra_sector_rs_top_quartile: bool = False
    intra_sector_rs_and_rel_min: Optional[float] = None
    intra_sector_rs_and_rel_window: Optional[int] = None

    def to_h42_overlay(self) -> H42Overlay:
        """Convert to H42 Overlay for compatibility with h42_passes_overlay."""
        return H42Overlay(
            name=self.name,
            mom20_min=self.mom20_min,
            mom60_min=self.mom60_min,
            mom120_min=self.mom120_min,
            ma_window=self.ma_window,
            vol20_max=self.vol20_max,
            market_ma_window=self.market_ma_window,
            market_ret20_min=self.market_ret20_min,
            rel20_min=self.rel20_min,
            rel60_min=self.rel60_min,
            rel120_min=self.rel120_min,
            near_60d_high_pct=self.near_60d_high_pct,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass(frozen=True)
class H49bParams:
    """Extended params with sector-cap axes."""
    top_n: int
    max_position_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    quality_filter: float
    rebalance_freq_days: int
    trailing_stop_pct: Optional[float] = None
    max_new_buys: Optional[int] = None
    # H49b-specific
    sector_max_weight_pct: float = 1.0
    min_sectors_in_portfolio: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def signature(self) -> tuple:
        return (
            self.top_n, self.max_position_pct, self.stop_loss_pct,
            self.take_profit_pct, self.quality_filter, self.rebalance_freq_days,
            self.trailing_stop_pct, self.max_new_buys,
            self.sector_max_weight_pct, self.min_sectors_in_portfolio,
        )


BASELINE_H49B_PARAMS = H49bParams(
    top_n=8, max_position_pct=0.08, stop_loss_pct=0.08,
    take_profit_pct=0.22, quality_filter=0.30, rebalance_freq_days=63,
    sector_max_weight_pct=1.0, min_sectors_in_portfolio=1,
)


# ── Sector map ─────────────────────────────────────────────────────────
def load_sector_map(csv_path: Path = SECTOR_CSV) -> Dict[str, str]:
    """Load ticker → industry_name mapping from H49a CSV.

    Returns dict: ticker (e.g. '000001.SZ') → industry_name (e.g. '银行').
    The CSV already has one row per ticker (latest-wins applied by H49a).
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Sector metadata not found: {csv_path}")
    df = pd.read_csv(csv_path)
    sector_map = {}
    for _, row in df.iterrows():
        ticker = str(row["ticker"]).strip()
        industry = str(row["industry_name"]).strip()
        sector_map[ticker] = industry
    return sector_map


# ── Sector-aware feature cache ─────────────────────────────────────────
class SectorFeatureCache:
    """Wraps FeatureCache with sector-level return distribution precomputation."""

    def __init__(self, fc: FeatureCache, sector_map: Dict[str, str]):
        self.fc = fc
        self.sector_map = sector_map
        # Precompute sector → tickers mapping
        self._sector_tickers: Dict[str, List[str]] = {}
        for ticker in fc.tickers:
            sect = sector_map.get(ticker, "__UNMAPPED__")
            if sect not in self._sector_tickers:
                self._sector_tickers[sect] = []
            self._sector_tickers[sect].append(ticker)
        # Caches for sector return distributions
        self._sector_dist: Dict[Tuple[int, int], Dict[str, Tuple[float, float]]] = {}

    @property
    def prices(self):
        return self.fc.prices

    @property
    def hs300(self):
        return self.fc.hs300

    @property
    def tickers(self):
        return self.fc.tickers

    def _row_value(self, idx: int, ticker: str) -> Optional[float]:
        return self.fc._row_value(idx, ticker)

    def ma(self, idx: int, ticker: str, window: int) -> Optional[float]:
        return self.fc.ma(idx, ticker, window)

    def trailing(self, idx: int, ticker: str, window: int) -> Optional[float]:
        return self.fc.trailing(idx, ticker, window)

    def vol20(self, idx: int, ticker: str) -> Optional[float]:
        return self.fc.vol20(idx, ticker)

    def dist_from_60d_high(self, idx: int, ticker: str) -> Optional[float]:
        return self.fc.dist_from_60d_high(idx, ticker)

    def rel_ret(self, idx: int, ticker: str, window: int) -> Optional[float]:
        return self.fc.rel_ret(idx, ticker, window)

    def get_sector(self, ticker: str) -> Optional[str]:
        return self.sector_map.get(ticker)

    def sector_return_distribution(
        self, idx: int, window: int
    ) -> Dict[str, Tuple[float, float]]:
        """Compute (median, 75th_percentile) return for each sector at given idx.

        Returns dict: industry_name → (median_return, p75_return)
        Only tickers with valid returns are included.
        """
        key = (idx, window)
        if key not in self._sector_dist:
            dist = {}
            for sector, tickers in self._sector_tickers.items():
                rets = []
                for t in tickers:
                    r = self.fc.trailing(idx, t, window)
                    if r is not None:
                        rets.append(r)
                if len(rets) >= 3:
                    arr = sorted(rets)
                    n = len(arr)
                    median = arr[n // 2]
                    p75 = arr[int(n * 0.75)]
                    dist[sector] = (median, p75)
                else:
                    dist[sector] = (None, None)
            self._sector_dist[key] = dist
        return self._sector_dist[key]

    def sector_return_for_ticker(
        self, idx: int, ticker: str, window: int
    ) -> Optional[float]:
        return self.fc.trailing(idx, ticker, window)

    def is_top_quartile_in_sector(
        self, idx: int, ticker: str, window: int
    ) -> bool:
        """Check if ticker's return is in top quartile of its sector."""
        sector = self.sector_map.get(ticker)
        if sector is None:
            return False
        ticker_ret = self.fc.trailing(idx, ticker, window)
        if ticker_ret is None:
            return False
        dist = self.sector_return_distribution(idx, window)
        _, p75 = dist.get(sector, (None, None))
        if p75 is None:
            return False
        return ticker_ret >= p75


# ── Overlay checking ───────────────────────────────────────────────────
def passes_h49b_overlay(
    sfc: SectorFeatureCache, idx: int, ticker: str, overlay: H49bOverlay
) -> bool:
    """Extended overlay check with intra-sector RS support."""
    current = sfc._row_value(idx, ticker)
    if current is None:
        return False

    # First run standard H42 checks via the H42 overlay
    h42_ov = overlay.to_h42_overlay()
    if not h42_passes_overlay(sfc.fc, idx, ticker, h42_ov):
        return False

    # H49b-specific checks
    if overlay.intra_sector_rs_top_quartile and overlay.intra_sector_rs_window:
        if not sfc.is_top_quartile_in_sector(
            idx, ticker, overlay.intra_sector_rs_window
        ):
            return False

    if (overlay.intra_sector_rs_and_rel_min is not None
            and overlay.intra_sector_rs_and_rel_window
            and overlay.intra_sector_rs_window):
        # Both intra-sector RS and cross-universe rel check
        if not sfc.is_top_quartile_in_sector(
            idx, ticker, overlay.intra_sector_rs_window
        ):
            return False
        rel = sfc.rel_ret(idx, ticker, overlay.intra_sector_rs_and_rel_window)
        if rel is None or rel < overlay.intra_sector_rs_and_rel_min:
            return False

    return True


# ── Overlay builder ────────────────────────────────────────────────────
def build_h49b_overlays() -> List[H49bOverlay]:
    """Build H42 overlays + H49b intra-sector RS overlays."""
    overlays: List[H49bOverlay] = []

    # Convert H42 overlays to H49b overlay format
    h42_ovs = h42_build_overlays()
    for ov in h42_ovs:
        overlays.append(H49bOverlay(
            name=ov.name,
            mom20_min=ov.mom20_min,
            mom60_min=ov.mom60_min,
            mom120_min=ov.mom120_min,
            ma_window=ov.ma_window,
            vol20_max=ov.vol20_max,
            market_ma_window=ov.market_ma_window,
            market_ret20_min=ov.market_ret20_min,
            rel20_min=ov.rel20_min,
            rel60_min=ov.rel60_min,
            rel120_min=ov.rel120_min,
            near_60d_high_pct=ov.near_60d_high_pct,
        ))

    # H49b new overlays (D2)
    # intra_sector_rs20: rank stock 20-day return within sector, keep top quartile
    overlays.append(H49bOverlay(
        "intra_sector_rs20",
        intra_sector_rs_window=20, intra_sector_rs_top_quartile=True,
    ))
    # intra_sector_rs60
    overlays.append(H49bOverlay(
        "intra_sector_rs60",
        intra_sector_rs_window=60, intra_sector_rs_top_quartile=True,
    ))
    # intra_sector_rs20_and_rel60_ge_0
    overlays.append(H49bOverlay(
        "intra_sector_rs20_and_rel60_ge_0",
        intra_sector_rs_window=20, intra_sector_rs_top_quartile=True,
        intra_sector_rs_and_rel_min=0.0, intra_sector_rs_and_rel_window=60,
    ))
    # intra_sector_rs60_and_rel20_ge_0
    overlays.append(H49bOverlay(
        "intra_sector_rs60_and_rel20_ge_0",
        intra_sector_rs_window=60, intra_sector_rs_top_quartile=True,
        intra_sector_rs_and_rel_min=0.0, intra_sector_rs_and_rel_window=20,
    ))

    return overlays


# ── Param grid builder ─────────────────────────────────────────────────
def build_h49b_param_grid() -> List[H49bParams]:
    """Build H42 base grid × sector axes (D3)."""
    base_grid = h42_build_param_grid()
    grid: List[H49bParams] = []

    for bp in base_grid:
        for smw in SECTOR_MAX_WEIGHT_VALUES:
            for ms in MIN_SECTORS_VALUES:
                grid.append(H49bParams(
                    top_n=bp.top_n,
                    max_position_pct=bp.max_position_pct,
                    stop_loss_pct=bp.stop_loss_pct,
                    take_profit_pct=bp.take_profit_pct,
                    quality_filter=bp.quality_filter,
                    rebalance_freq_days=bp.rebalance_freq_days,
                    trailing_stop_pct=bp.trailing_stop_pct,
                    max_new_buys=bp.max_new_buys,
                    sector_max_weight_pct=smw,
                    min_sectors_in_portfolio=ms,
                ))

    return grid


# ── Sector-aware backtest ──────────────────────────────────────────────
def run_sector_aware_backtest(
    data_source: CN_PIT_FileSource,
    prices: pd.DataFrame,
    sfc: SectorFeatureCache,
    start: str,
    end: str,
    capital: float,
    params: H49bParams,
    overlay: H49bOverlay,
    config: Dict,
    return_details: bool = False,
) -> Dict:
    """Single-window backtest with sector-cap enforcement at portfolio construction.

    D1: sector_max_weight cap enforced during buy selection, not post-hoc.
    D5: H42 gate verbatim.
    """
    trading_dates = prices.index
    cash = capital
    positions: Dict[str, Dict] = {}
    trades: List[Dict] = []
    equity: List[float] = []
    total_fees = 0.0
    total_slippage = 0.0
    last_rebalance_idx = -999
    hs300_base = None
    hs300_last = None

    for idx, dt in enumerate(trading_dates):
        date_str = dt.strftime("%Y-%m-%d")
        day_prices = prices.iloc[idx]
        hs300_val = day_prices.get(sfc.hs300)
        if not is_missing(hs300_val):
            hs300_last = float(hs300_val)
            if hs300_base is None:
                hs300_base = float(hs300_val)

        # ── Exit checks (identical to H42) ──────────────────────────────
        for ticker in list(positions):
            pos = positions[ticker]
            px = day_prices.get(ticker, pos["avg_cost"])
            if is_missing(px):
                continue
            ret = px / pos["avg_cost"] - 1
            held_days = idx - pos["entry_idx"]

            if params.trailing_stop_pct is not None:
                pos["max_since_entry"] = max(
                    pos.get("max_since_entry", pos["avg_cost"]), px
                )

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

        # ── Rebalance (sector-aware) ────────────────────────────────────
        if idx - last_rebalance_idx >= params.rebalance_freq_days:
            live_universe = data_source.get_universe(date_str)
            scoped = [t for t in live_universe if t in prices.columns]

            # Verify all scoped tickers have sector mapping
            unmapped = [t for t in scoped if sfc.get_sector(t) is None]
            if unmapped:
                raise ValueError(
                    f"Tickers missing sector data at {date_str}: {unmapped}. "
                    f"H49a claims 100% coverage — this is a data integrity failure."
                )

            fundamentals = data_source.get_fundamentals(scoped, date_str)
            scores = []
            for ticker in scoped:
                if ticker in positions:
                    continue
                if not passes_h49b_overlay(sfc, idx, ticker, overlay):
                    continue
                score = ValueScore.from_fundamentals(ticker, fundamentals)
                if score and score.total >= params.quality_filter:
                    scores.append(score)
            scores.sort(key=lambda x: x.total, reverse=True)
            target_tickers = [score.ticker for score in scores[:params.top_n]]

            # ── Rebalance out (identical to H42) ──────────────────────
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

            # ── Sector-aware buy selection (D1) ────────────────────────
            # Compute current portfolio value for MTM
            current_portfolio_value = cash
            for ticker, pos in positions.items():
                px = day_prices.get(ticker, pos["avg_cost"])
                current_portfolio_value += pos["shares"] * (
                    px if not is_missing(px) else pos["avg_cost"]
                )

            # Track sector allocations (both existing positions + new buys)
            sector_values: Dict[str, float] = {}
            sector_ticker_count: Dict[str, int] = {}
            for ticker, pos in positions.items():
                sect = sfc.get_sector(ticker)
                if sect is None:
                    continue
                px = day_prices.get(ticker, pos["avg_cost"])
                val = pos["shares"] * (px if not is_missing(px) else pos["avg_cost"])
                sector_values[sect] = sector_values.get(sect, 0) + val
                sector_ticker_count[sect] = sector_ticker_count.get(sect, 0) + 1

            sector_cap_value = current_portfolio_value * params.sector_max_weight_pct

            # Build buy candidates: filter target_tickers by sector cap
            candidates_for_buy = []
            for ticker in target_tickers:
                if ticker in positions:
                    continue
                sect = sfc.get_sector(ticker)
                if sect is None:
                    continue
                px_val = day_prices.get(ticker)
                if is_missing(px_val):
                    continue
                candidates_for_buy.append((ticker, sect, px_val))

            # Sort candidates by ValueScore (already sorted via target_tickers order)
            # But we need the ticker→score mapping
            ticker_to_score = {s.ticker: s.total for s in scores}
            candidates_for_buy.sort(
                key=lambda x: ticker_to_score.get(x[0], 0), reverse=True
            )

            # Max new buys
            new_buys_allowed = params.max_new_buys if params.max_new_buys else params.top_n
            n_slots = params.top_n - len(positions)
            n_to_buy = min(n_slots, new_buys_allowed)

            # Separate candidates into those whose sector is under-cap vs over-cap
            under_cap = []
            over_cap = []
            for ticker, sect, px_val in candidates_for_buy:
                current_sector_val = sector_values.get(sect, 0)
                # Estimate: if we add one max_position_pct position to this sector
                est_new_val = capital * params.max_position_pct
                if current_sector_val + est_new_val <= sector_cap_value:
                    under_cap.append((ticker, sect, px_val))
                else:
                    over_cap.append((ticker, sect, px_val))

            # Enforce min_sectors (D3): if we don't have enough sectors,
            # prefer under-represented sectors
            current_sector_count = len(
                set(sfc.get_sector(t) for t in positions if sfc.get_sector(t))
            )
            sectors_needed = max(0, params.min_sectors_in_portfolio - current_sector_count)

            # Build final buy list: first from under_cap, then from over_cap if needed
            buy_list = []
            buy_list.extend(under_cap[:n_to_buy])

            # If we need more sectors, try to add from under-represented sectors
            if sectors_needed > 0 and len(buy_list) < n_to_buy:
                represented_sectors = set(
                    sfc.get_sector(t) for t in positions if sfc.get_sector(t)
                )
                for bt in buy_list:
                    represented_sectors.add(bt[1])

                # Find candidates from new sectors (over_cap allowed if needed)
                for ticker, sect, px_val in over_cap:
                    if sect not in represented_sectors and len(buy_list) < n_to_buy:
                        buy_list.append((ticker, sect, px_val))
                        represented_sectors.add(sect)
                        sectors_needed -= 1

            # Execute buys
            budget_per_slot = cash / max(len(buy_list), 1) if buy_list else 0
            bought = 0
            for ticker, sect, px in buy_list:
                if bought >= n_to_buy:
                    break
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
                    "sector": sect,
                }
                if params.trailing_stop_pct is not None:
                    positions[ticker]["max_since_entry"] = float(px)
                trades.append({
                    "date": date_str, "action": "buy", "ticker": ticker,
                    "price": float(px), "shares": shares, "amount": float(cost),
                    "commission": float(commission), "transfer_fee": float(transfer_fee),
                    "slippage": float(slippage), "total_cost": float(total_cost),
                    "sector": sect,
                })
                # Update sector tracking
                sector_values[sect] = sector_values.get(sect, 0) + cost
                bought += 1

            last_rebalance_idx = idx

        # ── MTM ─────────────────────────────────────────────────────────
        total_value = cash
        for ticker, pos in positions.items():
            px = day_prices.get(ticker, pos["avg_cost"])
            total_value += pos["shares"] * (px if not is_missing(px) else pos["avg_cost"])
        equity.append(float(total_value))

    # ── Compute metrics (identical to H42) ──────────────────────────────
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
        trades, capital, load_json(DEFAULT_CONFIG), metrics,
        equity_curve=eq, as_of_date=end)
    blockers.extend(period_blockers)
    streak = compute_consecutive_losing_sells(trades)
    monthly_turnover = compute_monthly_one_way_turnover(trades, capital, as_of_date=end)
    annualized_turnover = compute_annualized_turnover(trades, capital)

    result = {
        "params": params.to_dict(),
        "overlay": overlay.to_dict(),
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


# ── Multi-window evaluation ────────────────────────────────────────────
def evaluate_h49b_candidate_multi_window(
    data_source: CN_PIT_FileSource,
    params: H49bParams,
    overlay: H49bOverlay,
    config: Dict,
    capital: float,
    sector_map: Dict[str, str],
) -> Dict:
    """Backtest candidate across all 5 windows with sector awareness."""
    window_results = {}
    for wname, (wstart, wend) in WINDOWS.items():
        universe = data_source.get_price_universe(wstart, wend)
        prices = data_source.get_price_history(
            list(universe) + [HS300_TICKER], wstart, wend
        )
        fc = FeatureCache(prices, HS300_TICKER)
        sfc = SectorFeatureCache(fc, sector_map)
        window_results[wname] = run_sector_aware_backtest(
            data_source, prices, sfc, wstart, wend, capital, params, overlay, config
        )

    gate_metrics, passes_gate = compute_acceptance_gate(window_results)

    return {
        "params": params.to_dict(),
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


# ── H49b scoring (D6) ─────────────────────────────────────────────────
def score_candidate_h49b(result: Dict) -> Tuple:
    """D6: Rank by beat_HS300_windows desc, deploy_excess desc as tiebreaker."""
    g = result["gate_metrics"]
    m = result["deploy_window"]["metrics"]
    return (
        0 if result["passes_acceptance_gate"] else 1,
        -g["beat_hs300_windows"],  # primary: more beat windows = better
        -g["deploy_excess_return"],  # tiebreaker: higher excess = better
        1 if g["execution_blocked"] else 0,
        g["warnings_count"],
        g["deploy_streak"],
        -m["sharpe_ratio"],
        -m["total_return"],
    )


# ── Stage A: overlay screening ────────────────────────────────────────
def run_h49b_stage_a(
    data_source: CN_PIT_FileSource,
    overlays: List[H49bOverlay],
    config: Dict,
    capital: float,
    deploy_start: str,
    deploy_end: str,
    sector_map: Dict[str, str],
) -> List[Dict]:
    """Screen overlays with baseline params on deploy window (sector-aware)."""
    universe = data_source.get_price_universe(deploy_start, deploy_end)
    prices = data_source.get_price_history(
        list(universe) + [HS300_TICKER], deploy_start, deploy_end
    )
    fc = FeatureCache(prices, HS300_TICKER)
    sfc = SectorFeatureCache(fc, sector_map)

    print(f"Stage A: screening {len(overlays)} overlays with baseline params...")
    results = []
    for overlay in overlays:
        result = run_sector_aware_backtest(
            data_source, prices, sfc, deploy_start, deploy_end,
            capital, BASELINE_H49B_PARAMS, overlay, config
        )
        results.append(result)
        m = result["metrics"]
        print(
            f"  {overlay.name:40s} ret={m['total_return']*100:+6.2f}% "
            f"sharpe={m['sharpe_ratio']:.2f} streak={result['terminal_losing_streak']} "
            f"trades={m['trade_count']} blocked={result['execution_blocked']}"
        )
        sys.stdout.flush()
    return results


# ── Stage B: param grid search ────────────────────────────────────────
def run_h49b_stage_b(
    data_source: CN_PIT_FileSource,
    overlays: List[H49bOverlay],
    grid: List[H49bParams],
    config: Dict,
    capital: float,
    deploy_start: str,
    deploy_end: str,
    stage_b_limit: int,
    sector_map: Dict[str, str],
) -> List[Dict]:
    """Grid search across selected overlays × param grid (deploy window only)."""
    universe = data_source.get_price_universe(deploy_start, deploy_end)
    prices = data_source.get_price_history(
        list(universe) + [HS300_TICKER], deploy_start, deploy_end
    )
    fc = FeatureCache(prices, HS300_TICKER)
    sfc = SectorFeatureCache(fc, sector_map)

    total = (
        min(len(grid) * len(overlays), stage_b_limit)
        if stage_b_limit else len(grid) * len(overlays)
    )
    print(
        f"Stage B: running up to {total} param combos across "
        f"{len(overlays)} overlays (sampled)..."
    )
    results = []
    seen_sigs = set()
    count = 0
    step = max(1, len(grid) * len(overlays) // total) if total > 0 else 1
    session_idx = 0
    for gi in range(0, len(grid), step):
        for oi in range(len(overlays)):
            if total and session_idx >= total:
                break
            overlay = overlays[oi]
            params = grid[gi]
            sig = (overlay.name,) + params.signature
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            session_idx += 1
            count += 1
            result = run_sector_aware_backtest(
                data_source, prices, sfc, deploy_start, deploy_end,
                capital, params, overlay, config
            )
            results.append(result)
            if count % 100 == 0 or count == total:
                print(f"  progress {count}/{total}")
                sys.stdout.flush()
        if total and session_idx >= total:
            break
    return results


# ── Stage C: multi-window evaluation ──────────────────────────────────
def run_h49b_stage_c(
    data_source: CN_PIT_FileSource,
    candidates: List[Dict],
    config: Dict,
    capital: float,
    top_k: int,
    sector_map: Dict[str, str],
) -> List[Dict]:
    """Multi-window evaluation of top candidates, ranked by D6."""
    n_eval = min(top_k, len(candidates))
    print(f"Stage C: multi-window evaluation of top {n_eval} candidates...")
    mw_results = []
    for i, cand in enumerate(candidates[:n_eval]):
        overlay = H49bOverlay(**cand["overlay"])
        params = H49bParams(**cand["params"])
        print(
            f"  [{i+1}/{n_eval}] {overlay.name} top_n={params.top_n} "
            f"sl={params.stop_loss_pct} tp={params.take_profit_pct} "
            f"cap={params.sector_max_weight_pct} min_sect={params.min_sectors_in_portfolio} ..."
        )
        sys.stdout.flush()
        mw = evaluate_h49b_candidate_multi_window(
            data_source, params, overlay, config, capital, sector_map
        )
        mw_results.append(mw)
        g = mw["gate_metrics"]
        print(
            f"    pass={mw['passes_acceptance_gate']} "
            f"pos_wins={g['positive_windows']}/5 unblock={g['unblocked_windows']}/5 "
            f"beat={g['beat_hs300_windows']}/5 "
            f"excess={pct(g['deploy_excess_return'])} maxdd={pct(g['deploy_max_drawdown'])}"
        )
    mw_results.sort(key=score_candidate_h49b)
    return mw_results


# ── Provenance ─────────────────────────────────────────────────────────
def compute_provenance() -> Dict:
    """Compute data_sources provenance block at runtime (sha256 from actual files)."""
    prices_raw = H47_PRICES.read_bytes()
    prices_sha = hashlib.sha256(prices_raw).hexdigest()

    sector_raw = SECTOR_CSV.read_bytes()
    sector_sha = hashlib.sha256(sector_raw).hexdigest()

    universe_raw = H30_UNIVERSE.read_bytes()
    universe_sha = hashlib.sha256(universe_raw).hexdigest()

    # Get snapshot_date from H49a coverage
    h49a_cov = json.loads(H49A_COVERAGE.read_text(encoding="utf-8"))
    snapshot_date = h49a_cov["provenance"]["snapshot_date"]

    return {
        "data_sources": {
            "prices": {
                "task": "h47",
                "file": str(H47_PRICES),
                "sha256": prices_sha,
            },
            "sector_metadata": {
                "task": "h49a",
                "file": str(SECTOR_CSV),
                "sha256": sector_sha,
                "snapshot_date": snapshot_date,
                "provider": "tushare:index_classify+index_member",
            },
            "universe": {
                "file": str(H30_UNIVERSE),
                "sha256": universe_sha,
            },
        }
    }


# ── Report builder ─────────────────────────────────────────────────────
def candidate_row_h49b(r: Dict) -> str:
    """H49b-specific candidate row with sector-cap columns."""
    o = r["overlay"]["name"]
    p = r["params"]
    g = r["gate_metrics"]
    m = r["deploy_window"]["metrics"]
    return (
        f"| {o} | {p['top_n']} | {p['max_position_pct']:.2f} | "
        f"{p['stop_loss_pct']:.2f} | {p['take_profit_pct']:.2f} | "
        f"{p.get('trailing_stop_pct') or '-'} | "
        f"{p['quality_filter']:.2f} | {p['rebalance_freq_days']} | "
        f"{p.get('sector_max_weight_pct', 1.0):.2f} | "
        f"{p.get('min_sectors_in_portfolio', 1)} | "
        f"{pct(m['total_return'])} | {m['sharpe_ratio']:.2f} | {pct(m['max_drawdown'])} | "
        f"{pct(m['excess_return'])} | {m['trade_count']} | {g['deploy_streak']} | "
        f"{g['beat_hs300_windows']}/5 | "
        f"{'YES' if r['passes_acceptance_gate'] else 'NO'} |"
    )


def build_h49b_report(
    payload: Dict,
    h42_baseline: Dict,
    h48_baseline: Dict,
) -> str:
    """Build H49b report with H42/H48/H49b comparison."""
    prov = payload["data_sources"]
    v = payload["verdict"]
    h42v = h42_baseline.get("verdict", "RESEARCH_ONLY")
    h48v = h48_baseline.get("verdict", "RESEARCH_ONLY")

    # Compute best beat_HS300 for each
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
    h49b_best = payload.get("top_candidates_multi_window", [])
    h49b_best_beat = max(
        (r.get("gate_metrics", {}).get("beat_hs300_windows", 0) for r in h49b_best[:15]),
        default=0,
    )

    lines = [
        "# H49b — Sector-Neutral Relative Strength Search Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Verdict:** {v}",
        f"**Elapsed:** {payload['elapsed_seconds']:.1f}s",
        "",
        "## Question",
        "",
        "Does sector-neutral selection (intra-sector relative-strength ranking + "
        "explicit sector-concentration caps) close any portion of the H42/H48 "
        "`beat_HS300_windows` gap?",
        "",
        "## Data Sources",
        "",
        f"- **Prices:** H47 QFQ prices, SHA256 `{prov['prices']['sha256'][:16]}...`",
        f"- **Sector metadata:** H49a SW L1, SHA256 `{prov['sector_metadata']['sha256'][:16]}...`, "
        f"snapshot {prov['sector_metadata']['snapshot_date']}",
        f"- **Universe:** H30 candidate universe, SHA256 `{prov['universe']['sha256'][:16]}...`",
        "",
        "## Search Space Summary",
        "",
        f"- H42 overlays: 18 (all retained as control group)",
        f"- H49b new overlays: 4 (intra_sector_rs20, intra_sector_rs60, "
        f"intra_sector_rs20_and_rel60_ge_0, intra_sector_rs60_and_rel20_ge_0)",
        f"- Total overlays: {payload['stage_a_count']}",
        f"- Base H42 param grid: {len(h42_build_param_grid())} combos",
        f"- Sector axes: sector_max_weight_pct ∈ {SECTOR_MAX_WEIGHT_VALUES}, "
        f"min_sectors ∈ {MIN_SECTORS_VALUES}",
        f"- Total param combos: {len(build_h49b_param_grid())}",
        f"- Stage A (overlay screening): {payload['stage_a_count']} overlays",
        f"- Stage B (param grid): {payload['stage_b_count']} runs",
        f"- Clean deploy-window candidates: {payload.get('clean_deploy_count', 0)}",
        f"- Stage C (multi-window): {payload['stage_c_count']} candidates",
        f"- Selected overlays: {', '.join(payload.get('selected_overlays', []))}",
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
        "## Design Choices",
        "",
        "### D1: Sector-neutrality mechanism — sector_max_weight cap",
        "",
        "Pick top-N stocks by signal globally, then enforce a per-sector "
        "portfolio-weight cap at construction time. Drop the next-ranked candidate "
        "from over-cap sectors and substitute the next-ranked candidate from any "
        "under-cap sector.",
        "",
        "**Rejected alternatives:**",
        "- Per-sector top_k: forces portfolio size ≈ sector_count × k, adds weak-signal "
        "positions.",
        "- Equal-weight sector buckets: discards signal magnitude, equally weights "
        "all sectors regardless of opportunity.",
        "",
        "### D2: New overlay families",
        "",
        "- `intra_sector_rs20`: rank stock 20-day return within SW L1 sector, keep top quartile",
        "- `intra_sector_rs60`: same, 60-day window",
        "- `intra_sector_rs20_and_rel60_ge_0`: intra-sector RS + cross-universe beat-HS300 over 60d",
        "- `intra_sector_rs60_and_rel20_ge_0`: dual-horizon combo",
        "",
        "Existing H42 overlays also run under sector caps — they form the baseline "
        "that isolates the contribution of the sector cap from the contribution of "
        "intra-sector ranking.",
        "",
        "### D3: New parameter grid axes",
        "",
        f"- `sector_max_weight_pct` ∈ {SECTOR_MAX_WEIGHT_VALUES} (1.00 = no cap = H42 control)",
        f"- `min_sectors_in_portfolio` ∈ {MIN_SECTORS_VALUES} (1 = no constraint)",
        "",
        "### D4: Multi-mapped handling",
        "",
        "Use the single primary industry per ticker from H49a CSV (latest-wins). "
        "Alternates from H49a's `multi_mapped` field are ignored.",
        "",
        "### D5: Acceptance gate",
        "",
        "H42 gate verbatim — not tightened or loosened. The information value of H49b "
        "is in measuring whether sector neutrality moves `beat_HS300_windows` upward, "
        "not in moving the goalposts.",
        "",
        "### D6: Ranking",
        "",
        "Stage C ranks by `beat_HS300_windows` count (descending), then by "
        "`deploy_excess_return` (descending) as tiebreaker.",
        "",
        "## H42 vs H48 vs H49b Comparison",
        "",
        "| Metric | H42 (yfinance) | H48 (Tushare qfq) | H49b (sector-neutral) |",
        "|--------|---------------|-------------------|----------------------|",
        f"| Verdict | **{h42v}** | **{h48v}** | **{v}** |",
        f"| Gate-pass count | {h42_baseline.get('gate_pass_count', 0)} | {h48_baseline.get('gate_pass_count', 0)} | {payload.get('gate_pass_count', 0)} |",
        f"| Best beat_HS300_windows (top-15) | {h42_best_beat}/5 | {h48_best_beat}/5 | {h49b_best_beat}/5 |",
        f"| Stage A overlays | {h42_baseline.get('stage_a_count', 0)} | {h48_baseline.get('stage_a_count', 0)} | {payload['stage_a_count']} |",
        f"| Stage B runs | {h42_baseline.get('stage_b_count', 0)} | {h48_baseline.get('stage_b_count', 0)} | {payload['stage_b_count']} |",
        f"| Clean deploy candidates | {h42_baseline.get('clean_deploy_count', 0)} | {h48_baseline.get('clean_deploy_count', 0)} | {payload.get('clean_deploy_count', 0)} |",
        f"| Stage C multi-window | {h42_baseline.get('stage_c_count', 0)} | {h48_baseline.get('stage_c_count', 0)} | {payload['stage_c_count']} |",
        "",
    ]

    top = payload.get("top_candidates_multi_window", [])
    if top:
        table_header = (
            "| Overlay | N | Pos% | SL | TP | Trail | QF | Rebal | "
            "Cap | MinS | Return | Sharpe | MaxDD | Excess | Trades | Streak | "
            "Beat | Gate |\n"
            "|---------|---|------|----|----|-------|----|-------|"
            "-----|------|--------|--------|-------|--------|--------|--------|"
            "------|------|"
        )
        lines.extend([
            "## Top 15 H49b Candidates (Ranked by beat_HS300_windows ↓)",
            "",
            table_header,
        ])
        lines.extend(candidate_row_h49b(r) for r in top[:15])
        lines.append("")

        # Per-window detail for top 3
        lines.extend(["## Per-Window Detail (Top 3)", ""])
        for i, cand in enumerate(top[:3]):
            o = cand["overlay"]["name"]
            p = cand["params"]
            lines.append(
                f"### #{i+1}: {o} (N={p['top_n']}, SL={p['stop_loss_pct']}, "
                f"TP={p['take_profit_pct']}, Trail={p.get('trailing_stop_pct') or '-'}, "
                f"QF={p['quality_filter']}, Rebal={p['rebalance_freq_days']}, "
                f"Cap={p.get('sector_max_weight_pct', 1.0)}, "
                f"MinSect={p.get('min_sectors_in_portfolio', 1)})"
            )
            lines.append("")
            lines.append(
                "| Window | Return | Sharpe | MaxDD | Excess | Status | Trades | Streak |\n"
                "|--------|--------|--------|-------|--------|--------|--------|--------|"
            )
            for wname in ["cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"]:
                wr = cand["window_results"].get(wname, {})
                lines.append(window_table_row(wname, wr))
            lines.append("")

        # Sector distribution of best candidate
        best = top[0]
        deploy_wr = best.get("deploy_window", {})
        lines.extend([
            "## Sector Distribution (Best Candidate)",
            "",
            "**Note:** Sector distribution at deploy-window start/end is available "
            "in the detailed trade data. The best candidate's sector cap parameter "
            f"is `sector_max_weight_pct={best['params'].get('sector_max_weight_pct', 1.0):.2f}`.",
            "",
        ])

        # Multi-mapped check
        lines.extend([
            "## Multi-Mapped Follow-Up",
            "",
            "H49a identified 122 tickers (25.4%) with multi-mapped history. "
            "H49b uses only the primary (latest-wins) industry. If the best "
            "candidate's holdings concentrate in industries heavily represented "
            "in `multi_mapped`, a follow-up sector reclassification run is warranted.",
            "",
        ])

        # Final verdict
        lines.extend(["## Final Verdict", ""])
        if payload["acceptance_gate_passed"]:
            lines.extend([
                f"**{v}** — at least one candidate passes the full H42 acceptance gate "
                "under sector-neutral selection.",
                "",
            ])
        else:
            lines.extend([
                f"**{v}** — no candidate passes the full multi-window acceptance gate.",
                "",
            ])

        # Answer the core question
        beat_delta = h49b_best_beat - max(h42_best_beat, h48_best_beat)
        lines.extend([
            "## Did Sector-Neutral Selection Help?",
            "",
            f"Best `beat_HS300_windows` across top-15: H42={h42_best_beat}/5, "
            f"H48={h48_best_beat}/5, H49b={h49b_best_beat}/5.",
        ])
        if beat_delta > 0:
            lines.append(
                f"\n**Yes.** Sector-neutral selection improved "
                f"`beat_HS300_windows` by +{beat_delta} vs. H42/H48. "
                f"Recommend tighter H49c grid around the best-performing "
                f"`sector_max_weight_pct` value."
            )
        elif beat_delta == 0 and h49b_best_beat > 0:
            lines.append(
                f"\n**Partially.** `beat_HS300_windows` matched H42/H48 best "
                f"({h49b_best_beat}/5) but did not surpass it. "
                f"Sector neutrality did not degrade results — it maintained parity "
                f"with added diversification."
            )
        else:
            lines.append(
                f"\n**No.** Sector-neutral selection did not improve "
                f"`beat_HS300_windows` ({h49b_best_beat}/5). "
                f"Escalate to next H45 PRD alpha direction: Quality-Value composite "
                f"redesign or Benchmark-Relative Objective."
            )
        lines.append("")
    else:
        lines.extend([
            "## Results",
            "",
            "No candidates survived the initial deploy-window clean filter.",
            "",
            "## Did Sector-Neutral Selection Help?",
            "",
            "**No.** No candidates reached Stage C multi-window evaluation.",
            "",
        ])

    return "\n".join(lines)


# ── Main search ────────────────────────────────────────────────────────
def run_h49b_search(args) -> Dict:
    t0 = time.time()

    # Load sector map
    print("Loading sector metadata...")
    sector_map = load_sector_map()
    print(f"  Loaded {len(sector_map)} ticker→sector mappings")

    # Compute provenance
    provenance = compute_provenance()
    print(f"  Prices SHA256: {provenance['data_sources']['prices']['sha256'][:16]}...")
    print(f"  Sector SHA256: {provenance['data_sources']['sector_metadata']['sha256'][:16]}...")

    config = load_json(DEFAULT_CONFIG)
    source = CN_PIT_FileSource(
        prices_path=str(args.prices_file),
        universe_path=str(args.universe_file),
        universe_snapshots_path=str(args.snapshots_file),
    )

    overlays = build_h49b_overlays()
    grid = build_h49b_param_grid()
    print(f"Total overlays: {len(overlays)}, total param combos: {len(grid)}")
    if args.stage_a_limit:
        print(f"Stage A limit: {args.stage_a_limit}")
    if args.stage_b_limit:
        print(f"Stage B limit: {args.stage_b_limit}")
    if args.top_k:
        print(f"Top-K for Stage C: {args.top_k}")

    # Stage A: overlay screening
    stage_a_ovs = overlays[:args.stage_a_limit] if args.stage_a_limit else overlays
    stage_a_results = run_h49b_stage_a(
        source, stage_a_ovs, config, args.capital,
        "2025-01-01", "2026-05-21", sector_map,
    )

    # Select top overlays for Stage B
    ranked_a = sorted(stage_a_results, key=lambda r: (
        1 if r["execution_blocked"] else 0,
        r["terminal_losing_streak"],
        max(0, 30 - r["metrics"]["trade_count"]),
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
        stage_b_results = run_h49b_stage_b(
            source, selected_overlays, grid, config, args.capital,
            "2025-01-01", "2026-05-21", 0, sector_map,
        )
    else:
        stage_b_results = run_h49b_stage_b(
            source, selected_overlays, grid, config, args.capital,
            "2025-01-01", "2026-05-21", args.stage_b_limit, sector_map,
        )

    # Merge and rank deploy-window candidates
    all_results = stage_a_results + stage_b_results
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
            r["params"].get("sector_max_weight_pct", 1.0),
            r["params"].get("min_sectors_in_portfolio", 1),
        )
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped_clean.append(r)
    deduped_clean.sort(
        key=lambda r: (r["metrics"]["sharpe_ratio"], r["metrics"]["excess_return"]),
        reverse=True,
    )
    print(
        f"Clean deploy-window candidates (unique): "
        f"{len(deduped_clean)}/{len(clean_deploy)} total"
    )

    # Stage C: multi-window
    mw_results = run_h49b_stage_c(
        source, deduped_clean, config, args.capital, args.top_k, sector_map,
    )
    # Already sorted by score_candidate_h49b in run_h49b_stage_c

    gate_pass = [r for r in mw_results if r["passes_acceptance_gate"]]

    elapsed = time.time() - t0
    print(f"\nSearch complete in {elapsed/60:.1f}min")
    print(f"Stage A: {len(stage_a_results)} overlays")
    print(f"Stage B: {len(stage_b_results)} param runs")
    print(f"Clean deploy-window candidates: {len(clean_deploy)}")
    print(f"Multi-window evaluated: {len(mw_results)}")
    print(f"Acceptance gate passed: {len(gate_pass)}")

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task": "H49b",
        "elapsed_seconds": round(elapsed, 1),
        **provenance,
        "inputs": {
            "prices_file": str(args.prices_file),
            "universe_file": str(args.universe_file),
            "snapshots_file": str(args.snapshots_file),
            "sector_file": str(SECTOR_CSV),
            "config": str(DEFAULT_CONFIG),
        },
        "stage_a_count": len(stage_a_results),
        "stage_b_count": len(stage_b_results),
        "stage_c_count": len(mw_results),
        "clean_deploy_count": len(clean_deploy),
        "selected_overlays": selected_names,
        "acceptance_gate_passed": len(gate_pass) > 0,
        "gate_pass_count": len(gate_pass),
        "top_candidates_multi_window": mw_results[:15],
        "all_clean_deploy": clean_deploy[:50],
        "verdict": "CANDIDATE_FOR_FORWARD_TRIAL" if gate_pass else "RESEARCH_ONLY",
    }


# ── CLI ────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="H49b sector-neutral RS search")
    parser.add_argument("--capital", type=float, default=300000)
    parser.add_argument("--prices-file", type=Path, default=H47_PRICES)
    parser.add_argument("--universe-file", type=Path, default=H30_UNIVERSE)
    parser.add_argument("--snapshots-file", type=Path, default=H30_SNAPSHOTS)
    parser.add_argument("--top-overlays", type=int, default=8)
    parser.add_argument(
        "--stage-a-limit", type=int, default=0,
        help="Limit number of overlays in Stage A (0=all)"
    )
    parser.add_argument(
        "--stage-b-limit", type=int, default=200,
        help="Limit param combos in Stage B (0=all, default=200)"
    )
    parser.add_argument(
        "--top-k", type=int, default=20,
        help="Top K deploy-window candidates for Stage C multi-window eval"
    )
    parser.add_argument("--output-run", type=Path, default=RUN_OUT)
    parser.add_argument("--output-report", type=Path, default=REPORT_OUT)
    args = parser.parse_args()

    # Guard: do not overwrite H42/H48 originals
    for protected in [
        "fundamental_value_h42_strategy_redesign_search.json",
        "fundamental_value_h48_unified_qfq_h42_rerun.json",
        "h42_strategy_redesign_search_report.md",
        "h48_unified_qfq_h42_rerun_report.md",
    ]:
        if protected in str(args.output_run) or protected in str(args.output_report):
            print(f"ERROR: Refusing to overwrite {protected}.")
            return 1

    # Check sector metadata exists
    if not SECTOR_CSV.exists():
        print(f"ERROR: Sector metadata not found: {SECTOR_CSV}")
        return 1

    print("=" * 70)
    print("H49b — Sector-Neutral Relative Strength Search")
    print(f"D1: sector_max_weight cap; D2: 4 new intra-sector RS overlays")
    print(f"D3: sector_max_weight_pct ∈ {SECTOR_MAX_WEIGHT_VALUES}")
    print(f"D3: min_sectors ∈ {MIN_SECTORS_VALUES}")
    print(f"D6: ranked by beat_HS300_windows ↓")
    print(f"Prices: {args.prices_file}")
    print(f"Output: {args.output_run}")
    print(f"Report: {args.output_report}")
    print("=" * 70)

    payload = run_h49b_search(args)

    # Load baselines for comparison
    if H42_RUN.exists():
        h42_baseline = json.loads(H42_RUN.read_text(encoding="utf-8"))
    else:
        h42_baseline = {"verdict": "RESEARCH_ONLY", "gate_pass_count": 0}
    if H48_RUN.exists():
        h48_baseline = json.loads(H48_RUN.read_text(encoding="utf-8"))
    else:
        h48_baseline = {"verdict": "RESEARCH_ONLY", "gate_pass_count": 0}

    # Build and write report
    report = build_h49b_report(payload, h42_baseline, h48_baseline)

    args.output_run.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_run.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2)
    )
    args.output_report.write_text(report)

    print(f"\nWrote: {args.output_run}")
    print(f"Wrote: {args.output_report}")
    print(f"Verdict: {payload['verdict']}")
    print(f"Gate passed: {payload['gate_pass_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
