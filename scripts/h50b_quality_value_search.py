#!/usr/bin/env python3
"""H50b — Quality-Value Composite Redesign Search.

Designs a 3-component PIT-safe ValueScoreH50 from H50a fundamentals,
runtime-substitutes it into the H42 search framework via monkey-patch,
runs focused search, and reports beat_HS300_windows delta.

D1: 3 components (profitability, balance_sheet, cash_flow), no valuation.
D2: PIT-safe quantile rank with cross-section cache, equal weight.
D3: Runtime monkey-patch of ValueScore in 2 modules + finally restore.
D4-A: CN_PIT_FileSource.get_fundamentals patched for per-rebalance date hook.
D5: Sector max_weight cap (no min_sectors).
D6: Narrow search grid (5 overlays x 4 sector caps x 144 params, 200 cap).
D7: H42 acceptance gate verbatim.
D8: Stage C rank by beat_HS300_windows desc, deploy_excess desc tiebreaker.
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

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

# ── Imports from H42 / H49b ────────────────────────────────────────────
from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    COMMISSION_RATE,
    HS300_TICKER,
    SLIPPAGE_BPS,
    STAMP_TAX_RATE,
    TRANSFER_FEE_RATE,
    ValueScore as _FB_ValueScore_Original,
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

# Ensure h42_strategy_redesign_search.ValueScore is captured for patching
# (Module already imported via `from h42_strategy_redesign_search import ...`)
import sys as _sys
_h42_mod = _sys.modules["h42_strategy_redesign_search"]
_H42_ValueScore_Original = _h42_mod.ValueScore


# ── H50b-specific paths ─────────────────────────────────────────────────
H47_PRICES = PROJECT_ROOT / "data/cn_pit/prices_h47_tushare_qfq_candidate.csv"
H30_UNIVERSE = PROJECT_ROOT / "data/cn_pit/universe_h30_candidate.jsonl"
H30_SNAPSHOTS = PROJECT_ROOT / "data/cn_pit/universe_snapshots_h30_candidate.jsonl"
SECTOR_CSV = PROJECT_ROOT / "data/cn_pit/sector_metadata_sw_l1.csv"
H50A_JSONL = PROJECT_ROOT / "data/cn_pit/fundamentals_h50a_pit_quality.jsonl"
DEFAULT_CONFIG = PROJECT_ROOT / "value_account/h34_shadow_account_config.json"

RUN_OUT = PROJECT_ROOT / "backtest/runs/fundamental_value_h50b_quality_value_search.json"
REPORT_OUT = PROJECT_ROOT / "reports/h50b_quality_value_search_report.md"

# Input SHA256s (pre-computed, validated at script start)
INPUT_SHA256 = {
    "prices": "34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc",
    "sector_metadata": "923762b79566894f7a85d0d0f7cdb835ac1bf7b43d262130ec35938cb6fa76f2",
    "fundamentals": "eeea2005b243070d7790b458f7232979ba945d18fa0056ecb775e3e408bc43b7",
    "universe": "c59919c3022e2e4d803aa37b50c9dec388d709f00f9921e03443ab11b8ea832f",
    "universe_snapshots": "5c50b179e10ece2c6baa822695be87ca565eb197f7435f38a021485df31cee25",
}


# ═════════════════════════════════════════════════════════════════════════
# H50A Fundamentals Panel
# ═════════════════════════════════════════════════════════════════════════

def load_h50a_panel(path: Path) -> Dict[str, List[dict]]:
    """Load H50a JSONL into {ticker: list[row]} sorted by filing_date ASC."""
    panel: Dict[str, list] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ticker = row["ticker"]
            if ticker not in panel:
                panel[ticker] = []
            panel[ticker].append(row)
    # Sort each ticker's rows by filing_date ASC
    for ticker in panel:
        panel[ticker].sort(key=lambda r: r["filing_date"])
    return panel


def pit_lookup(panel: Dict[str, List[dict]], ticker: str, as_of_date: str) -> Optional[dict]:
    """PIT-safe lookup: latest row with filing_date <= as_of_date.
    
    Walks from end of sorted list. Returns None if no row.
    """
    rows = panel.get(ticker)
    if not rows:
        return None
    for row in reversed(rows):
        if row["filing_date"] <= as_of_date:
            return row
    return None


# ═════════════════════════════════════════════════════════════════════════
# ValueScoreH50
# ═════════════════════════════════════════════════════════════════════════

# Score fields per component
PROFITABILITY_FIELDS = ["roe", "roa", "gross_margin", "operating_margin"]
BALANCE_SHEET_FIELDS = ["current_ratio", "quick_ratio", "debt_to_equity"]
CASH_FLOW_FIELDS = ["operating_cash_flow_to_revenue", "free_cash_flow", "accruals_ratio"]

# Fields to invert after ranking (higher raw = worse quality → 1 - rank)
INVERTED_FIELDS = {"debt_to_equity", "accruals_ratio"}

ALL_SCORE_FIELDS = PROFITABILITY_FIELDS + BALANCE_SHEET_FIELDS + CASH_FLOW_FIELDS

COMPONENT_DEFS = {
    "profitability": (PROFITABILITY_FIELDS, 2),
    "balance_sheet": (BALANCE_SHEET_FIELDS, 2),
    "cash_flow": (CASH_FLOW_FIELDS, 2),
}


@dataclass
class ValueScoreH50:
    """3-component quality-value composite from H50a fundamentals.
    
    Interface matches ValueScore for monkey-patch compatibility:
    - total: float in [0, 1] (equal-weight mean of 3 components)
    - components_used: dict of {component: [sub-fields used]}
    """
    ticker: str
    as_of_date: str
    filing_date: str
    profitability_score: float = 0.0
    balance_sheet_score: float = 0.0
    cash_flow_score: float = 0.0
    total: float = 0.0
    components_used: dict = field(default_factory=dict)

    # Class-level references (set at substitution time)
    _panel: ClassVar[Dict[str, List[dict]]] = {}
    _as_of_ref: ClassVar[List] = [None]
    _xs_cache: ClassVar[Dict[str, Dict]] = {}
    _universe_tickers: ClassVar[List[str]] = []
    _exclusion_counts: ClassVar[Dict[str, int]] = {}

    @classmethod
    def from_fundamentals(cls, ticker, fundamentals_dict) -> "ValueScoreH50 | None":
        """PIT-safe score lookup via cross-section cache.
        
        fundamentals_dict is ignored (D3 design) — we look up from H50A_PANEL.
        Returns None if ticker is excluded by per-component minimum.
        """
        as_of_date = cls._as_of_ref[0]
        if as_of_date is None:
            raise RuntimeError(
                "AS_OF_DATE_REF is None — per-rebalance hook (get_fundamentals patch) "
                "did not fire. This is a PIT-safety violation."
            )

        # Lazy precompute cross-section for this date
        if as_of_date not in cls._xs_cache:
            cls._compute_cross_section(as_of_date)

        cached = cls._xs_cache[as_of_date].get(ticker)
        return cached

    @classmethod
    def _compute_cross_section(cls, as_of_date: str) -> None:
        """Compute full cross-section ranks for one rebalance date.

        Builds DataFrame of N tickers × 10 score fields using PIT-safe lookup,
        then winsorize → rank → invert → aggregate.
        Stores results in _xs_cache[as_of_date].
        """
        rows = []
        tickers_seen = []

        for ticker in cls._universe_tickers:
            row = pit_lookup(cls._panel, ticker, as_of_date)
            if row is None:
                continue
            # Extract score fields (may be None)
            values = {}
            for field in ALL_SCORE_FIELDS:
                val = row.get(field)
                values[field] = val
            values["_ticker"] = ticker
            values["_filing_date"] = row["filing_date"]
            rows.append(values)
            tickers_seen.append(ticker)

        if not rows:
            cls._xs_cache[as_of_date] = {}
            return

        df = pd.DataFrame(rows).set_index("_ticker")
        filing_map = df["_filing_date"].to_dict()

        # Drop the filing_date column for numerical processing
        score_cols = [f for f in ALL_SCORE_FIELDS if f in df.columns]
        df_scores = df[score_cols].copy()

        # Convert all score columns to numeric (None → NaN)
        for col in score_cols:
            df_scores[col] = pd.to_numeric(df_scores[col], errors="coerce")

        # Step 1: Winsorize each column at (p1, p99) of THIS cross-section
        for col in score_cols:
            col_data = df_scores[col].dropna()
            if len(col_data) < 3:
                continue
            p01 = col_data.quantile(0.01)
            p99 = col_data.quantile(0.99)
            if p01 < p99:
                df_scores[col] = df_scores[col].clip(p01, p99)

        rank_df = pd.DataFrame(index=df_scores.index)
        for col in score_cols:
            col_data = df_scores[col].dropna()
            if len(col_data) < 2:
                # Not enough data to rank — assign neutral 0.5 only to non-NaN rows
                import numpy as np
                rank_df[col] = np.where(df_scores[col].notna(), 0.5, np.nan)
                continue
            # Rank ascending (lower rank = lower value)
            ranks = df_scores[col].rank(pct=True)
            # For invert fields: apply after ranking
            if col in INVERTED_FIELDS:
                ranks = 1.0 - ranks
            rank_df[col] = ranks

        # Step 3: Per-component aggregation with minimum sub-field rule
        cache_entry: Dict[str, Optional[dict]] = {}

        for ticker in tickers_seen:
            # Check per-component minimums
            excluded = False
            exclusion_reason = None
            component_scores = {}
            components_used = {}

            for comp_name, (fields, min_required) in COMPONENT_DEFS.items():
                available = [
                    f for f in fields
                    if f in rank_df.columns and pd.notna(rank_df.loc[ticker, f])
                ]
                if len(available) < min_required:
                    excluded = True
                    exclusion_reason = f"{comp_name}_below_min"
                    # Track partial for diagnostics
                    components_used[comp_name] = available
                    break
                comp_mean = float(rank_df.loc[ticker, available].mean())
                component_scores[comp_name] = comp_mean
                components_used[comp_name] = available

            if excluded:
                cls._exclusion_counts[exclusion_reason] = cls._exclusion_counts.get(exclusion_reason, 0) + 1
                cache_entry[ticker] = None
                continue

            # Total = equal-weight mean of 3 components
            total = float(
                (component_scores["profitability"]
                 + component_scores["balance_sheet"]
                 + component_scores["cash_flow"])
                / 3.0
            )

            vs = cls(
                ticker=ticker,
                as_of_date=as_of_date,
                filing_date=filing_map.get(ticker, ""),
                profitability_score=round(component_scores["profitability"], 6),
                balance_sheet_score=round(component_scores["balance_sheet"], 6),
                cash_flow_score=round(component_scores["cash_flow"], 6),
                total=round(total, 6),
                components_used=components_used,
            )
            cache_entry[ticker] = vs

        cls._xs_cache[as_of_date] = cache_entry

    @classmethod
    def get_exclusion_counts(cls) -> dict:
        """Return per-component exclusion counts."""
        return dict(cls._exclusion_counts)

    @classmethod
    def get_rebalance_count(cls) -> int:
        """Return number of rebalance dates in xs_cache."""
        return len(cls._xs_cache)

    @classmethod
    def get_tickers_seen(cls) -> int:
        """Return total unique tickers that appeared in any rebalance."""
        tickers = set()
        for date_cache in cls._xs_cache.values():
            tickers.update(date_cache.keys())
        return len(tickers)


# ═════════════════════════════════════════════════════════════════════════
# Monkey-Patch Installer
# ═════════════════════════════════════════════════════════════════════════

# Mutable reference for per-rebalance date
AS_OF_DATE_REF: List[Optional[str]] = [None]

# Originals for restoration
_ORIGINAL_FB_VALUESCORE = None
_ORIGINAL_H42_VALUESCORE = None
_ORIGINAL_GET_FUNDAMENTALS = None


def install_patches(h50a_panel: Dict[str, List[dict]], universe_tickers: List[str]):
    """Install monkey-patches for ValueScore + date hook.

    Must be called before any backtest/search logic runs.
    Returns a restore function.
    """
    global _ORIGINAL_FB_VALUESCORE, _ORIGINAL_H42_VALUESCORE, _ORIGINAL_GET_FUNDAMENTALS
    _fb = _sys.modules.get("fundamental_backtest")
    if _fb is None:
        _fb = _sys.modules["__main__"]  # shouldn't happen
    _h42 = _sys.modules["h42_strategy_redesign_search"]

    # Set up ValueScoreH50 class-level refs
    ValueScoreH50._panel = h50a_panel
    ValueScoreH50._as_of_ref = AS_OF_DATE_REF
    ValueScoreH50._xs_cache = {}
    ValueScoreH50._exclusion_counts = {"profitability_below_min": 0, "balance_sheet_below_min": 0, "cash_flow_below_min": 0}
    ValueScoreH50._universe_tickers = list(universe_tickers)

    # Capture originals
    _ORIGINAL_FB_VALUESCORE = _fb.ValueScore
    _ORIGINAL_H42_VALUESCORE = _h42.ValueScore
    _ORIGINAL_GET_FUNDAMENTALS = CN_PIT_FileSource.get_fundamentals

    # Patch ValueScore in both modules
    _fb.ValueScore = ValueScoreH50
    _h42.ValueScore = ValueScoreH50

    # Patch CN_PIT_FileSource.get_fundamentals for date hook
    @functools.wraps(_ORIGINAL_GET_FUNDAMENTALS)
    def _patched_get_fundamentals(self, tickers, as_of_date):
        AS_OF_DATE_REF[0] = as_of_date
        return _ORIGINAL_GET_FUNDAMENTALS(self, tickers, as_of_date)

    CN_PIT_FileSource.get_fundamentals = _patched_get_fundamentals

    # Patch h49b's ValueScore reference too (if loaded)
    if "h49b_sector_neutral_rs_search" in _sys.modules:
        _h49b = _sys.modules["h49b_sector_neutral_rs_search"]
        _h49b.ValueScore = ValueScoreH50

    def restore():
        _fb.ValueScore = _ORIGINAL_FB_VALUESCORE
        _h42.ValueScore = _ORIGINAL_H42_VALUESCORE
        CN_PIT_FileSource.get_fundamentals = _ORIGINAL_GET_FUNDAMENTALS
        ValueScoreH50._xs_cache.clear()
        if "h49b_sector_neutral_rs_search" in _sys.modules:
            _sys.modules["h49b_sector_neutral_rs_search"].ValueScore = _ORIGINAL_FB_VALUESCORE

    return restore


# ═════════════════════════════════════════════════════════════════════════
# H50b Overlays (D6: 5 overlays)
# ═════════════════════════════════════════════════════════════════════════

H50B_OVERLAY_NAMES = [
    "rel20_ge_0_and_ma60",
    "rel60_ge_0",
    "price_gt_ma120",
    "intra_sector_rs60",
    "none",  # baseline: pure ValueScoreH50, no overlay filter
]


@dataclass(frozen=True)
class H50bOverlay:
    """Overlay for H50b — mirrors H49b but only 5 names."""
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
    # H49b-specific (only used for intra_sector_rs60)
    intra_sector_rs_window: Optional[int] = None
    intra_sector_rs_top_quartile: bool = False

    def to_h42_overlay(self) -> H42Overlay:
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
        return asdict(self)


def build_h50b_overlays() -> List[H50bOverlay]:
    """Build the 5 H50b overlays from H42's full list."""
    all_h42 = {ov.name: ov for ov in h42_build_overlays()}
    overlays = []
    for name in H50B_OVERLAY_NAMES:
        if name == "none":
            overlays.append(H50bOverlay(name="none"))
        elif name == "intra_sector_rs60":
            overlays.append(H50bOverlay(
                name="intra_sector_rs60",
                intra_sector_rs_window=60, intra_sector_rs_top_quartile=True,
            ))
        elif name in all_h42:
            ov = all_h42[name]
            overlays.append(H50bOverlay(
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
        else:
            raise ValueError(f"Unknown overlay name: {name} — not in H42 overlays")
    return overlays


# ═════════════════════════════════════════════════════════════════════════
# H50b Params (D6: narrow grid, no min_sectors)
# ═════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class H50bParams:
    top_n: int
    max_position_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    quality_filter: float
    rebalance_freq_days: int
    trailing_stop_pct: Optional[float] = None
    max_new_buys: Optional[int] = None
    # H50b-specific
    sector_max_weight_pct: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def signature(self) -> tuple:
        return (
            self.top_n, self.max_position_pct, self.stop_loss_pct,
            self.take_profit_pct, self.quality_filter, self.rebalance_freq_days,
            self.trailing_stop_pct, self.max_new_buys,
            self.sector_max_weight_pct,
        )


SECTOR_MAX_WEIGHT_VALUES_H50B = [0.20, 0.25, 0.30, 1.00]


def build_h50b_param_grid() -> List[H50bParams]:
    """D6: Narrow grid. No min_sectors axis."""
    grid = []
    for top_n, max_pos, sl, tp, qf, rebalance in itertools.product(
        [8, 10],
        [0.05, 0.06, 0.08],
        [0.08, 0.10],
        [0.22, 0.25],
        [0.30, 0.40, 0.50],
        [42, 63],
    ):
        grid.append(H50bParams(
            top_n=top_n, max_position_pct=max_pos,
            stop_loss_pct=sl, take_profit_pct=tp,
            quality_filter=qf, rebalance_freq_days=rebalance,
            sector_max_weight_pct=1.0,
        ))
    # Apply sector caps to the full grid
    final = []
    base = list(grid)
    for sc in SECTOR_MAX_WEIGHT_VALUES_H50B:
        if sc == 1.0:
            final.extend(base)
        else:
            for p in base:
                final.append(H50bParams(
                    top_n=p.top_n, max_position_pct=p.max_position_pct,
                    stop_loss_pct=p.stop_loss_pct, take_profit_pct=p.take_profit_pct,
                    quality_filter=p.quality_filter, rebalance_freq_days=p.rebalance_freq_days,
                    sector_max_weight_pct=sc,
                ))
    return final


# ═════════════════════════════════════════════════════════════════════════
# H50b Sector-Aware Backtest (D5: hard cap, no min_sectors)
# ═════════════════════════════════════════════════════════════════════════

def passes_h50b_overlay(
    sfc: SectorFeatureCache, idx: int, ticker: str, overlay: H50bOverlay
) -> bool:
    """Check overlay for ticker. 'none' overlay passes everything."""
    if overlay.name == "none":
        current = sfc._row_value(idx, ticker)
        return current is not None

    # Standard H42 checks
    h42_ov = overlay.to_h42_overlay()
    if not h42_passes_overlay(sfc.fc, idx, ticker, h42_ov):
        return False

    # Intra-sector RS check
    if overlay.intra_sector_rs_top_quartile and overlay.intra_sector_rs_window:
        if not sfc.is_top_quartile_in_sector(
            idx, ticker, overlay.intra_sector_rs_window
        ):
            return False

    return True


def run_h50b_backtest(
    data_source: CN_PIT_FileSource,
    prices: pd.DataFrame,
    sfc: SectorFeatureCache,
    start: str,
    end: str,
    capital: float,
    params: H50bParams,
    overlay: H50bOverlay,
    config: Dict,
    return_details: bool = False,
) -> Dict:
    """Single-window backtest with sector cap (no min_sectors).
    
    Uses ValueScoreH50 via the monkey-patched reference.
    sector_max_weight_pct: hard cap, drop-and-substitute next-ranked.
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
    total_value = capital

    for idx, dt in enumerate(trading_dates):
        date_str = dt.strftime("%Y-%m-%d")
        day_prices = prices.iloc[idx]
        hs300_val = day_prices.get(sfc.hs300)
        if not is_missing(hs300_val):
            hs300_last = float(hs300_val)
            if hs300_base is None:
                hs300_base = float(hs300_val)

        # Exit checks (identical to H42/H49b)
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
                    "sector": pos.get("sector", ""),
                })
                del positions[ticker]

        # Rebalance (sector-aware, D5: hard cap, no min_sectors)
        if idx - last_rebalance_idx >= params.rebalance_freq_days:
            live_universe = data_source.get_universe(date_str)
            scoped = [t for t in live_universe if t in prices.columns]

            # Verify all scoped tickers have sector mapping
            unmapped = [t for t in scoped if sfc.get_sector(t) is None]
            if unmapped:
                raise ValueError(
                    f"Tickers missing sector data at {date_str}: {unmapped}. "
                    f"H49a claims 100% coverage — data integrity failure."
                )

            fundamentals = data_source.get_fundamentals(scoped, date_str)
            scores = []
            for ticker in scoped:
                if ticker in positions:
                    continue
                if not passes_h50b_overlay(sfc, idx, ticker, overlay):
                    continue
                score = ValueScoreH50.from_fundamentals(ticker, fundamentals)
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
                        "sector": pos.get("sector", ""),
                    })
                    del positions[ticker]

            # Sector-cap enforcement at construction (D5)
            sector_values: Dict[str, float] = {}
            for ticker, pos in list(positions.items()):
                px = day_prices.get(ticker, pos["avg_cost"])
                if not is_missing(px):
                    sect = sfc.get_sector(ticker) or "__UNMAPPED__"
                    sector_values[sect] = sector_values.get(sect, 0) + pos["shares"] * px

            # Max new buys
            n_slots = params.top_n - len(positions)
            n_to_buy = min(n_slots, params.top_n)
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

                sect = sfc.get_sector(ticker) or "__UNMAPPED__"

                # Sector cap check
                if params.sector_max_weight_pct < 1.0:
                    current_sector_val = sector_values.get(sect, 0)
                    target_amount = min(capital * params.max_position_pct, budget_per_slot)
                    projected_sector_val = current_sector_val + target_amount
                    projected_sector_wt = projected_sector_val / total_value if total_value > 0 else 0
                    if projected_sector_wt > params.sector_max_weight_pct:
                        continue  # skip, substitute next-ranked

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
                sector_values[sect] = sector_values.get(sect, 0) + cost
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
        trades, capital, load_json(DEFAULT_CONFIG), metrics,
        equity_curve=eq, as_of_date=end)
    blockers.extend(period_blockers)
    streak = compute_consecutive_losing_sells(trades)
    monthly_turnover = compute_monthly_one_way_turnover(trades, capital, as_of_date=end)
    annualized_turnover = compute_annualized_turnover(trades, capital)

    # Compute realized sector count at start and end
    def _realized_sectors(pos_dict, day_prices, sfc):
        sectors = set()
        for ticker in pos_dict:
            sect = sfc.get_sector(ticker)
            if sect:
                sectors.add(sect)
        return len(sectors)

    # Sector counts are computed here but not enforced
    if return_details:
        _start_sectors = _realized_sectors(positions, day_prices, sfc)
    else:
        _start_sectors = 0

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


# ═════════════════════════════════════════════════════════════════════════
# Multi-Window Evaluation
# ═════════════════════════════════════════════════════════════════════════

def evaluate_h50b_candidate_multi_window(
    data_source: CN_PIT_FileSource,
    params: H50bParams,
    overlay: H50bOverlay,
    config: Dict,
    capital: float,
    sector_map: Dict[str, str],
) -> Dict:
    """Backtest candidate across all 5 windows."""
    window_results = {}
    for wname, (wstart, wend) in WINDOWS.items():
        universe = data_source.get_price_universe(wstart, wend)
        prices = data_source.get_price_history(
            list(universe) + [HS300_TICKER], wstart, wend
        )
        fc = FeatureCache(prices, HS300_TICKER)
        sfc = SectorFeatureCache(fc, sector_map)
        window_results[wname] = run_h50b_backtest(
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


# ═════════════════════════════════════════════════════════════════════════
# Scoring and Ranking (D8)
# ═════════════════════════════════════════════════════════════════════════

def score_candidate_h50b(result: Dict) -> Tuple:
    """D8: Rank by beat_HS300_windows desc, deploy_excess desc tiebreaker."""
    g = result["gate_metrics"]
    m = result["deploy_window"]["metrics"]
    return (
        -g["beat_hs300_windows"],
        -g["positive_windows"],
        -(m.get("excess_return", 0) or 0),
        -(m.get("sharpe_ratio", 0) or 0),
    )


# ═════════════════════════════════════════════════════════════════════════
# Search Orchestration
# ═════════════════════════════════════════════════════════════════════════

def _dedup_sig(result: Dict) -> tuple:
    p = result.get("params", {})
    o = result.get("overlay", {})
    return (
        o.get("name", ""),
        p.get("top_n"), p.get("max_position_pct"), p.get("stop_loss_pct"),
        p.get("take_profit_pct"), p.get("quality_filter"), p.get("rebalance_freq_days"),
        p.get("trailing_stop_pct"), p.get("max_new_buys"),
        p.get("sector_max_weight_pct"),
    )


def run_h50b_search(args) -> Dict:
    t0 = time.time()
    config = load_json(DEFAULT_CONFIG)
    sector_map = load_sector_map(SECTOR_CSV)

    # Load H50a panel
    print("Loading H50a fundamentals panel...")
    h50a_panel = load_h50a_panel(H50A_JSONL)
    print(f"  Loaded {len(h50a_panel)} tickers, {sum(len(v) for v in h50a_panel.values())} rows")

    # Get universe tickers
    source = CN_PIT_FileSource(
        prices_path=str(H47_PRICES),
        universe_path=str(H30_UNIVERSE),
        universe_snapshots_path=str(H30_SNAPSHOTS),
    )
    universe_tickers = source.get_price_universe("2025-01-01", "2026-05-21")
    print(f"  Universe: {len(universe_tickers)} tickers")

    # Install patches
    restore_fn = install_patches(h50a_panel, list(universe_tickers))

    try:
        overlays = build_h50b_overlays()
        grid = build_h50b_param_grid()
        print(f"H50b overlays: {len(overlays)}, param combos: {len(grid)}")

        # ── Stage A: overlay screening ──────────────────────────────────
        stage_a_limit = min(args.stage_a_limit, len(overlays)) if args.stage_a_limit else len(overlays)
        stage_a_overlays = overlays[:stage_a_limit]
        stage_a_results = []
        for ov in stage_a_overlays:
            universe = source.get_price_universe("2025-01-01", "2026-05-21")
            prices = source.get_price_history(
                list(universe) + [HS300_TICKER], "2025-01-01", "2026-05-21"
            )
            fc = FeatureCache(prices, HS300_TICKER)
            sfc = SectorFeatureCache(fc, sector_map)
            r = run_h50b_backtest(
                source, prices, sfc, "2025-01-01", "2026-05-21",
                args.capital, H50bParams(
                    top_n=8, max_position_pct=0.08, stop_loss_pct=0.08,
                    take_profit_pct=0.22, quality_filter=0.30, rebalance_freq_days=63,
                    sector_max_weight_pct=1.0,
                ), ov, config
            )
            stage_a_results.append(r)
            print(f"  Stage A [{ov.name}]: ret={r['metrics']['total_return']:.4f}, "
                  f"sharpe={r['metrics']['sharpe_ratio']:.2f}, trades={r['metrics']['trade_count']}")

        # Select top overlays
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

        # ── Stage B: param grid ─────────────────────────────────────────
        # Stage B always uses deploy window
        base_overlay_map = {o.name: o for o in overlays}
        stage_b_limit = args.stage_b_limit if args.stage_b_limit else len(grid)

        # Sample grid for Stage B
        import random
        random.seed(42)
        sampled_grid = random.sample(grid, min(stage_b_limit, len(grid)))

        stage_b_results = []
        for i, params in enumerate(sampled_grid):
            ov_name = selected_names[i % len(selected_names)]
            ov = base_overlay_map[ov_name]
            universe = source.get_price_universe("2025-01-01", "2026-05-21")
            prices = source.get_price_history(
                list(universe) + [HS300_TICKER], "2025-01-01", "2026-05-21"
            )
            fc = FeatureCache(prices, HS300_TICKER)
            sfc = SectorFeatureCache(fc, sector_map)
            r = run_h50b_backtest(
                source, prices, sfc, "2025-01-01", "2026-05-21",
                args.capital, params, ov, config
            )
            stage_b_results.append(r)
            if (i + 1) % 25 == 0:
                print(f"  Stage B [{i+1}/{len(sampled_grid)}]: "
                      f"ret={r['metrics']['total_return']:.4f}, "
                      f"sharpe={r['metrics']['sharpe_ratio']:.2f}")

        # ── Sanity seeds ────────────────────────────────────────────────
        seed_results = []
        for seed_overlay, seed_params in SANITY_SEEDS:
            seed_ov_name = seed_overlay.name
            ov = base_overlay_map.get(seed_ov_name)
            if ov is None:
                # Build from the H42 overlay
                ov = H50bOverlay(
                    name=seed_ov_name,
                    mom20_min=seed_overlay.mom20_min,
                    mom60_min=seed_overlay.mom60_min,
                    mom120_min=seed_overlay.mom120_min,
                    ma_window=seed_overlay.ma_window,
                    vol20_max=seed_overlay.vol20_max,
                    market_ma_window=seed_overlay.market_ma_window,
                    market_ret20_min=seed_overlay.market_ret20_min,
                    rel20_min=seed_overlay.rel20_min,
                    rel60_min=seed_overlay.rel60_min,
                    rel120_min=seed_overlay.rel120_min,
                    near_60d_high_pct=seed_overlay.near_60d_high_pct,
                )
            seed_p = H50bParams(
                top_n=seed_params.top_n,
                max_position_pct=seed_params.max_position_pct,
                stop_loss_pct=seed_params.stop_loss_pct,
                take_profit_pct=seed_params.take_profit_pct,
                quality_filter=seed_params.quality_filter,
                rebalance_freq_days=seed_params.rebalance_freq_days,
                trailing_stop_pct=seed_params.trailing_stop_pct,
                max_new_buys=seed_params.max_new_buys,
                sector_max_weight_pct=1.0,
            )
            universe = source.get_price_universe("2025-01-01", "2026-05-21")
            prices = source.get_price_history(
                list(universe) + [HS300_TICKER], "2025-01-01", "2026-05-21"
            )
            fc = FeatureCache(prices, HS300_TICKER)
            sfc = SectorFeatureCache(fc, sector_map)
            r = run_h50b_backtest(
                source, prices, sfc, "2025-01-01", "2026-05-21",
                args.capital, seed_p, ov, config
            )
            seed_results.append(r)

        # ── Merge and dedup clean candidates ────────────────────────────
        all_results = stage_a_results + stage_b_results + seed_results
        clean_deploy = [
            r for r in all_results
            if not r["execution_blocked"]
            and r["metrics"]["total_return"] > 0
            and r["metrics"]["sharpe_ratio"] >= 1.0
            and r["metrics"]["trade_count"] >= 30
            and r["terminal_losing_streak"] < 5
        ]
        seen_sigs = set()
        deduped_clean = []
        for r in clean_deploy:
            sig = _dedup_sig(r)
            if sig not in seen_sigs:
                seen_sigs.add(sig)
                deduped_clean.append(r)
        deduped_clean.sort(
            key=lambda r: (-r["metrics"]["sharpe_ratio"], -r["metrics"]["excess_return"]),
        )
        print(f"Clean deploy-window candidates (unique): {len(deduped_clean)}/{len(clean_deploy)} total")

        # ── Stage C: multi-window ──────────────────────────────────────
        base_overlay_map = {o.name: o for o in overlays}
        top_k = args.top_k if args.top_k else 15
        mw_candidates = []
        for r in deduped_clean[:top_k]:
            ov_name = r["overlay"]["name"]
            ov = base_overlay_map.get(ov_name, overlays[0])
            p = r["params"]
            h50b_p = H50bParams(
                top_n=p["top_n"],
                max_position_pct=p["max_position_pct"],
                stop_loss_pct=p["stop_loss_pct"],
                take_profit_pct=p["take_profit_pct"],
                quality_filter=p["quality_filter"],
                rebalance_freq_days=p["rebalance_freq_days"],
                trailing_stop_pct=p.get("trailing_stop_pct"),
                max_new_buys=p.get("max_new_buys"),
                sector_max_weight_pct=p.get("sector_max_weight_pct", 1.0),
            )
            mw = evaluate_h50b_candidate_multi_window(
                source, h50b_p, ov, config, args.capital, sector_map
            )
            mw_candidates.append(mw)
            print(f"  Stage C [{ov_name}]: gate_pass={mw['passes_acceptance_gate']}, "
                  f"beat_HS300={mw['gate_metrics']['beat_hs300_windows']}/5")

        mw_candidates.sort(key=score_candidate_h50b)
        gate_pass = [r for r in mw_candidates if r["passes_acceptance_gate"]]

        # ── Compute exclusion stats ─────────────────────────────────────
        exclusion_counts = ValueScoreH50.get_exclusion_counts()
        rebalances_total = ValueScoreH50.get_rebalance_count()
        tickers_seen = ValueScoreH50.get_tickers_seen()

        # Total possible (ticker × rebalance) pairs
        total_pairs = tickers_seen * max(rebalances_total, 1)
        total_exclusions = sum(exclusion_counts.values())
        exclusion_rate = (total_exclusions / total_pairs * 100) if total_pairs > 0 else 0.0

        # Compute sector counts for best candidate
        best = mw_candidates[0] if mw_candidates else None
        if best and args.top_k:
            # Get deploy window details
            dw = best.get("deploy_window", {})
            sector_start = "N/A"
            sector_end = "N/A"
        else:
            sector_start = "N/A"
            sector_end = "N/A"

        # ── Read baselines ──────────────────────────────────────────────
        h42_baseline = load_json(PROJECT_ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json")
        h48_baseline = load_json(PROJECT_ROOT / "backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json")
        h49b_baseline = load_json(PROJECT_ROOT / "backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json")

        # ── Provenance ──────────────────────────────────────────────────
        elapsed = time.time() - t0
        verdict = "CANDIDATE_FOR_FORWARD_TRIAL" if gate_pass else "RESEARCH_ONLY"

        provenance = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task": "H50b",
            "verdict": verdict,
            "elapsed_seconds": round(elapsed, 1),
            "stage_a_count": len(stage_a_results),
            "stage_b_count": len(stage_b_results),
            "seed_count": len(seed_results),
            "stage_c_count": len(mw_candidates),
            "clean_deploy_count": len(deduped_clean),
            "gate_pass_count": len(gate_pass),
            "selected_overlays": selected_names,
            "data_sources": {
                "prices": {
                    "task": "h47",
                    "file": str(H47_PRICES.name),
                    "sha256": INPUT_SHA256["prices"],
                },
                "sector_metadata": {
                    "task": "h49a",
                    "file": str(SECTOR_CSV.name),
                    "sha256": INPUT_SHA256["sector_metadata"],
                    "snapshot_date": "2026-05-23",
                },
                "fundamentals": {
                    "task": "h50a",
                    "file": "data/cn_pit/fundamentals_h50a_pit_quality.jsonl",
                    "sha256": INPUT_SHA256["fundamentals"],
                    "rows": 12398,
                },
                "universe": {
                    "file": str(H30_UNIVERSE.name),
                    "sha256": INPUT_SHA256["universe"],
                },
                "universe_snapshots": {
                    "file": str(H30_SNAPSHOTS.name),
                    "sha256": INPUT_SHA256["universe_snapshots"],
                },
            },
            "scorer_substitution": {
                "from": "fundamental_backtest.ValueScore",
                "to": "h50b_quality_value_search.ValueScoreH50",
                "patched_modules": [
                    "backtest.experiments.fundamental_backtest",
                    "scripts.h42_strategy_redesign_search",
                ],
                "restored_after_run": True,
                "v1_class_repr": repr(_ORIGINAL_FB_VALUESCORE),
                "v2_class_repr": repr(ValueScoreH50),
            },
            "scorer_design": {
                "components": ["profitability", "balance_sheet", "cash_flow"],
                "component_weights": [0.333, 0.333, 0.334],
                "field_aggregation": "winsorize_p1_p99 + cross_sectional_rank + equal_weight_mean",
                "valuation_omitted_reason": "no PIT-safe source per H45 PRD",
            },
            "exclusion_stats": {
                "rebalances_total": rebalances_total,
                "tickers_seen": tickers_seen,
                "exclusion_rate_pct": round(exclusion_rate, 2),
                "exclusion_reasons": exclusion_counts,
            },
            "top_candidates_multi_window": mw_candidates,
        }

        # ── Build report ─────────────────────────────────────────────────
        report = build_h50b_report(provenance, h42_baseline, h48_baseline, h49b_baseline)

        # ── Write outputs ───────────────────────────────────────────────
        with open(args.output_run, "w", encoding="utf-8") as fh:
            json.dump(json_safe(provenance), fh, indent=2, ensure_ascii=False, default=str)
        with open(args.output_report, "w", encoding="utf-8") as fh:
            fh.write(report)

        print(f"\nSearch complete in {elapsed/60:.1f}min")
        print(f"Verdict: {verdict}")
        print(f"Gate passed: {len(gate_pass)}")
        print(f"Exclusion rate: {exclusion_rate:.1f}%")
        print(f"Run JSON: {args.output_run}")
        print(f"Report: {args.output_report}")

        return provenance

    finally:
        restore_fn()
        print("Patches restored.")


# ═════════════════════════════════════════════════════════════════════════
# Report Builder
# ═════════════════════════════════════════════════════════════════════════

def build_h50b_report(
    payload: Dict,
    h42_baseline: Dict,
    h48_baseline: Dict,
    h49b_baseline: Dict,
) -> str:
    prov = payload["data_sources"]
    v = payload["verdict"]
    h42v = h42_baseline.get("verdict", "RESEARCH_ONLY")
    h48v = h48_baseline.get("verdict", "RESEARCH_ONLY")
    h49bv = h49b_baseline.get("verdict", "RESEARCH_ONLY")

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
    h50b_best = payload.get("top_candidates_multi_window", [])
    h50b_best_beat = max(
        (r.get("gate_metrics", {}).get("beat_hs300_windows", 0) for r in h50b_best[:15]),
        default=0,
    )

    excl = payload.get("exclusion_stats", {})

    def best_deploy_excess(run_data):
        top = run_data.get("top_candidates_multi_window", [])
        if not top:
            return 0
        return max(
            r.get("deploy_window", {}).get("metrics", {}).get("excess_return", 0) or 0
            for r in top[:15]
        )

    h42_excess = best_deploy_excess(h42_baseline)
    h48_excess = best_deploy_excess(h48_baseline)
    h49b_excess = best_deploy_excess(h49b_baseline)
    h50b_excess = best_deploy_excess(payload)

    lines = [
        "# H50b — Quality-Value Composite Redesign Search Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Verdict:** {v}",
        f"**Elapsed:** {payload['elapsed_seconds']:.1f}s",
        "",
        "## Question",
        "",
        "Does a 3-component quality-value composite (profitability + balance-sheet "
        "+ cash-flow quality, no valuation) from H50a PIT fundamentals move "
        "`beat_HS300_windows` above the H42/H48/H49b 1/5 floor?",
        "",
        "## Data Sources",
        "",
        f"- **Prices:** H47 QFQ prices, SHA256 `{prov['prices']['sha256'][:16]}...`",
        f"- **Sector metadata:** H49a SW L1, SHA256 `{prov['sector_metadata']['sha256'][:16]}...`",
        f"- **Fundamentals:** H50a V2, SHA256 `{prov['fundamentals']['sha256'][:16]}...`, {prov['fundamentals']['rows']} rows",
        f"- **Universe:** H30 candidate universe, SHA256 `{prov['universe']['sha256'][:16]}...`",
        "",
        "## Scorer Substitution",
        "",
        f"- **From:** {payload['scorer_substitution']['from']}",
        f"- **To:** {payload['scorer_substitution']['to']}",
        f"- **Patched modules:** {', '.join(payload['scorer_substitution']['patched_modules'])}",
        f"- **Restored:** {payload['scorer_substitution']['restored_after_run']}",
        f"- **Date hook:** CN_PIT_FileSource.get_fundamentals (patched to set AS_OF_DATE_REF)",
        "",
        "## ValueScoreH50 Design",
        "",
        "### D1: 3 components, no valuation",
        "",
        "- **Profitability:** ROE (roe_waa), ROA, gross_margin, operating_margin",
        "- **Balance-sheet strength:** current_ratio, quick_ratio, debt_to_equity (inverted)",
        "- **Cash-flow quality:** operating_cash_flow_to_revenue, free_cash_flow, accruals_ratio (Sloan-inverted, RAW SIGNED, NO abs)",
        "- **Valuation omitted:** No PIT-safe source per H45 PRD",
        "",
        "### D2: PIT-safe quantile rank, equal weight",
        "",
        "- Per-component minimum: profitability >= 2/4, balance_sheet >= 2/3, cash_flow >= 2/3",
        "- Per-sub-field: winsorize (p1, p99) → cross-sectional rank [0,1] → invert for D/E and accruals",
        "- Per-component: mean of available sub-field ranks",
        "- Total: equal-weight mean of 3 components",
        "- Cross-section cache: lazy precompute per rebalance date",
        "",
        "## Search Space Summary",
        "",
        f"- Overlays: 5 (rel20_ge_0_and_ma60, rel60_ge_0, price_gt_ma120, intra_sector_rs60, none)",
        f"- Sector caps: {SECTOR_MAX_WEIGHT_VALUES_H50B}",
        f"- Param grid: 144 base combos × 4 sector caps = 576 total",
        f"- Stage A: {payload['stage_a_count']} overlays screened",
        f"- Stage B: {payload['stage_b_count']} runs (capped at {payload['stage_b_count']})",
        f"- Clean deploy-window candidates: {payload.get('clean_deploy_count', 0)}",
        f"- Stage C (multi-window): {payload['stage_c_count']} candidates",
        f"- Selected overlays: {', '.join(payload.get('selected_overlays', []))}",
        "",
        "## Exclusion Stats",
        "",
        f"- Rebalances total: {excl.get('rebalances_total', 'N/A')}",
        f"- Tickers seen: {excl.get('tickers_seen', 'N/A')}",
        f"- Exclusion rate: {excl.get('exclusion_rate_pct', 'N/A')}%",
        f"- Per-component: {excl.get('exclusion_reasons', {})}",
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
        "## H42 vs H48 vs H49b vs H50b Comparison",
        "",
        "| Metric | H42 | H48 | H49b | H50b |",
        "|--------|-----|-----|------|------|",
        f"| Verdict | **{h42v}** | **{h48v}** | **{h49bv}** | **{v}** |",
        f"| Gate-pass count | {h42_baseline.get('gate_pass_count', 0)} | {h48_baseline.get('gate_pass_count', 0)} | {h49b_baseline.get('gate_pass_count', 0)} | {payload.get('gate_pass_count', 0)} |",
        f"| Best beat_HS300_windows (top-15) | {h42_best_beat}/5 | {h48_best_beat}/5 | {h49b_best_beat}/5 | {h50b_best_beat}/5 |",
        f"| Best deploy excess | {h42_excess:.1%} | {h48_excess:.1%} | {h49b_excess:.1%} | {h50b_excess:.1%} |",
        f"| Stage A overlays | {h42_baseline.get('stage_a_count', 0)} | {h48_baseline.get('stage_a_count', 0)} | {h49b_baseline.get('stage_a_count', 0)} | {payload['stage_a_count']} |",
        f"| Stage B runs | {h42_baseline.get('stage_b_count', 0)} | {h48_baseline.get('stage_b_count', 0)} | {h49b_baseline.get('stage_b_count', 0)} | {payload['stage_b_count']} |",
        f"| Clean deploy candidates | {h42_baseline.get('clean_deploy_count', 0)} | {h48_baseline.get('clean_deploy_count', 0)} | {h49b_baseline.get('clean_deploy_count', 0)} | {payload.get('clean_deploy_count', 0)} |",
        f"| Stage C multi-window | {h42_baseline.get('stage_c_count', 0)} | {h48_baseline.get('stage_c_count', 0)} | {h49b_baseline.get('stage_c_count', 0)} | {payload['stage_c_count']} |",
        f"| Exclusion rate | N/A | N/A | N/A | {excl.get('exclusion_rate_pct', 'N/A')}% |",
        "",
    ]

    top = payload.get("top_candidates_multi_window", [])
    if top:
        lines.extend([
            "## Top 15 H50b Candidates (Ranked by beat_HS300_windows ↓)",
            "",
            "| Overlay | N | Pos% | SL | TP | QF | Rebal | Cap | Return | Sharpe | MaxDD | Excess | Trades | Streak | Beat | Gate |",
            "|---------|---|------|----|----|----|-------|-----|--------|--------|-------|--------|--------|--------|------|------|",
        ])
        for r in top[:15]:
            ov = r["overlay"]["name"]
            p = r["params"]
            m = r["deploy_window"]["metrics"]
            g = r["gate_metrics"]
            lines.append(
                f"| {ov} | {p['top_n']} | {p['max_position_pct']} | {p['stop_loss_pct']} | "
                f"{p['take_profit_pct']} | {p['quality_filter']} | {p['rebalance_freq_days']} | "
                f"{p.get('sector_max_weight_pct', 1.0)} | {m['total_return']:.3f} | "
                f"{m['sharpe_ratio']:.2f} | {m['max_drawdown']:.3f} | "
                f"{m['excess_return']:.3f} | {m['trade_count']} | "
                f"{r['deploy_window']['terminal_losing_streak']} | "
                f"{g['beat_hs300_windows']}/5 | {'PASS' if r['passes_acceptance_gate'] else 'FAIL'} |"
            )
        lines.append("")

        # Per-window detail for top 3
        lines.extend(["## Per-Window Detail (Top 3)", ""])
        for i, cand in enumerate(top[:3]):
            o = cand["overlay"]["name"]
            p = cand["params"]
            lines.append(
                f"### #{i+1}: {o} (N={p['top_n']}, SL={p['stop_loss_pct']}, "
                f"TP={p['take_profit_pct']}, QF={p['quality_filter']}, "
                f"Rebal={p['rebalance_freq_days']}, Cap={p.get('sector_max_weight_pct', 1.0)})"
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

    # Sector sanity
    lines.extend([
        "## Sector Concentration (Best Candidate)",
        "",
        "Sector count diagnostic (informational, no gate). "
        "H50b uses hard sector_max_weight cap with DROP of min_sectors_in_portfolio.",
        "",
        f"**Best candidate sector_max_weight_pct:** "
        f"{top[0]['params'].get('sector_max_weight_pct', 1.0) if top else 'N/A'}",
        "",
    ])

    # Verdict and final answer
    if h50b_best_beat > h49b_best_beat:
        answer = (
            f"Yes — the quality-value composite improved beat_HS300_windows "
            f"from H49b's {h49b_best_beat}/5 to {h50b_best_beat}/5."
        )
    elif h50b_best_beat == h49b_best_beat:
        answer = (
            f"No change — beat_HS300_windows stayed at {h50b_best_beat}/5, "
            f"same as H49b's {h49b_best_beat}/5."
        )
    else:
        answer = (
            f"No — beat_HS300_windows decreased from H49b's {h49b_best_beat}/5 "
            f"to H50b's {h50b_best_beat}/5."
        )

    lines.extend([
        "## Verdict",
        "",
        f"**{v}**",
        "",
        f"**Did the Quality-Value composite move beat_HS300_windows?** {answer}",
        "",
    ])

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="H50b — Quality-Value Composite Redesign Search")
    parser.add_argument("--stage-a-limit", type=int, default=None,
                        help="Limit Stage A to N overlays")
    parser.add_argument("--stage-b-limit", type=int, default=200,
                        help="Limit Stage B to N runs (default 200)")
    parser.add_argument("--top-k", type=int, default=15,
                        help="Top-K for Stage C multi-window (default 15)")
    parser.add_argument("--output-run", type=str, default=str(RUN_OUT),
                        help="Output JSON path")
    parser.add_argument("--output-report", type=str, default=str(REPORT_OUT),
                        help="Output Markdown report path")
    parser.add_argument("--capital", type=float, default=500000,
                        help="Starting capital")
    parser.add_argument("--top-overlays", type=int, default=3,
                        help="Top N overlays to select for Stage B")
    args = parser.parse_args()

    print(f"H50b Quality-Value Composite Search")
    print(f"  Stage A limit: {args.stage_a_limit or 'all'}")
    print(f"  Stage B limit: {args.stage_b_limit}")
    print(f"  Top-K: {args.top_k}")
    print(f"  Output: {args.output_run}, {args.output_report}")

    run_h50b_search(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
