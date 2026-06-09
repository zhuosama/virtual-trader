#!/usr/bin/env python3
"""H53-FIX cross-family composite — paper-only FORWARD IC monitor.

Sibling of scripts/h46_paper_forward_monitor.py, but for a FACTOR-IC signal
rather than a strategy. The H53-FIX cross-family composite (Charter §5 #2
spike, SIGNAL_NEGATIVE at IR=0.317) is "landed into the system" the only way
its governance allows: as a research-only forward monitor that recomputes the
composite's IC/IR on each run and appends a dated snapshot, so its forward
decay/stability can be observed on real data — WITHOUT placing orders, touching
strategies/active.json, or fabricating P&L it does not have.

Why IC and not return/drawdown: this is a cross-sectional factor signal, not a
backtested strategy. It has an IC and an IR; it does NOT have trades, P&L, or a
drawdown until it runs through the (currently non-frozen) backtest engine. The
H46 strategy monitor tracks return/excess/MaxDD; this monitor tracks IC/IR/ρ̄ —
the metrics this object actually produces. Inventing P&L here would be
fabrication.

Hard prohibitions (same spirit as h46): no live orders, no production-config
writes, no value-account positions/trades, no promotion. RESEARCH_ONLY.

The four composite legs are the best full-coverage (n_obs>=300) OK factor per
distinct theme from the H53-FIX bench, fixed at registration for forward
comparability:
    reversal=alpha_065  volatility=alpha_054  volume=alpha_163  momentum=alpha_048

Usage:
    python3 scripts/h53fix_ic_forward_monitor.py            # append a snapshot
    python3 scripts/h53fix_ic_forward_monitor.py --dry-run  # print, do not write
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OHLV = ROOT / "data/cn_pit/ohlcv_h53fix_tushare_qfq.csv"
CLOSE = ROOT / "data/cn_pit/prices_h47_tushare_qfq_candidate.csv"
FACTOR_DIR = ROOT / "backtest/factors/gtja191"
BENCH = ROOT / "backtest/runs/h53fix_gtja191_ic_bench.json"
RUN_OUT = ROOT / "backtest/runs/h53fix_ic_forward_monitor.json"
REPORT_OUT = ROOT / "reports/h53fix_ic_forward_monitor_report.md"

# Composite legs fixed at registration (best full-coverage factor per theme).
COMPOSITE_LEGS = {
    "reversal": "alpha_065",
    "volatility": "alpha_054",
    "volume": "alpha_163",
    "momentum": "alpha_048",
}
IC_START = "2025-01-01"
# IC_END defaults to the panel's last date each run, so the observation window
# extends forward as new data is appended to the OHLCV panel.

# Registration baseline from the spike that created this monitor (verified
# against /tmp/spike_xfam/result.json on 2026-05-31). Used only for drift
# comparison in the report; never used in place of a fresh computation.
BASELINE = {
    "registered_at": "2026-05-31",
    "ic_window": f"{IC_START}..2026-05-18",
    "n_common_dates": 329,
    "composite_ir": 0.3165,
    "composite_mean_ic": 0.0383,
    "rho_mean": 0.4437,
    "threshold_ir": 0.5,
    "verdict": "SIGNAL_NEGATIVE",
}

# gtja191 factor files do `from src.factors.base import ...`; mirror the bench
# harness by putting the factor dir on sys.path.
if str(FACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(FACTOR_DIR))
import importlib.util  # noqa: E402


def _load_compute(fid: str):
    spec = importlib.util.spec_from_file_location(fid, FACTOR_DIR / f"{fid}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compute


def _wide(ohlv: pd.DataFrame, col: str) -> pd.DataFrame:
    w = ohlv[["date", "ticker", col]].dropna(subset=[col]).pivot(
        index="date", columns="ticker", values=col
    )
    w.index = pd.to_datetime(w.index)
    return w.sort_index()


def _daily_ic_series(scores: pd.DataFrame, fwd: pd.DataFrame, ic_dates) -> pd.Series:
    scores = scores.sort_index()
    s = scores.loc[scores.index.intersection(ic_dates)]
    out = {}
    for d in ic_dates:
        if d not in s.index or d not in fwd.index:
            continue
        a, b = s.loc[d], fwd.loc[d]
        common = a.index.intersection(b.index)
        a, b = a[common].dropna(), b[common].dropna()
        common = a.index.intersection(b.index)
        if len(common) < 10:
            continue
        ic = a[common].rank().corr(b[common].rank())
        if pd.notna(ic):
            out[d] = float(ic)
    return pd.Series(out).sort_index()


def compute_snapshot() -> dict:
    """Recompute the composite IC/IR on the current panel. Pure read."""
    ohlv = pd.read_csv(OHLV, dtype={"date": str, "ticker": str})
    panel = {
        c: _wide(ohlv, c)
        for c in ["open", "high", "low", "close", "volume", "amount"]
        if c in ohlv.columns
    }
    close_m = pd.read_csv(CLOSE, index_col=0, parse_dates=True).sort_index()
    ic_end = pd.to_datetime(ohlv["date"]).max().strftime("%Y-%m-%d")
    fwd = close_m.pct_change(fill_method=None).shift(-1).loc[IC_START:ic_end]
    ic_dates = fwd.index

    series, legs = {}, []
    for theme, fid in COMPOSITE_LEGS.items():
        ser = _daily_ic_series(_load_compute(fid)(panel), fwd, ic_dates)
        if len(ser) < 10:
            legs.append({"theme": theme, "factor_id": fid, "error": "n_obs<10"})
            continue
        series[fid] = ser
        mu, sd = ser.mean(), ser.std(ddof=1)
        legs.append({
            "theme": theme, "factor_id": fid,
            "mean_ic": float(mu), "std_ic": float(sd),
            "ir": float(mu / sd) if sd > 0 else 0.0, "n_obs": int(len(ser)),
        })

    ids = [l["factor_id"] for l in legs if "error" not in l]
    if len(ids) < 2:
        return {"error": "fewer than 2 legs computable", "legs": legs, "ic_end": ic_end}

    mat = pd.DataFrame(series)[ids].dropna()
    corr = mat.corr()
    off = [corr.iloc[i, j] for i in range(len(ids)) for j in range(len(ids)) if i < j]
    rho_mean = float(np.mean(off)) if off else float("nan")
    ir_avg = float(np.mean([abs(l["ir"]) for l in legs if "ir" in l]))

    raw_aligned = sum(
        (mat[fid] * (1 if mat[fid].mean() >= 0 else -1)) for fid in ids
    ) / len(ids)
    emp_mu, emp_sd = raw_aligned.mean(), raw_aligned.std(ddof=1)
    emp_ir = float(emp_mu / emp_sd) if emp_sd > 0 else 0.0

    return {
        "ic_window": f"{IC_START}..{ic_end}",
        "n_common_dates": int(len(mat)),
        "legs": legs,
        "pairwise_corr": {
            f"{a}|{b}": float(corr.loc[a, b])
            for i, a in enumerate(ids) for b in ids[i + 1:]
        },
        "rho_mean": rho_mean,
        "ir_avg_single": ir_avg,
        "composite_ir": emp_ir,
        "composite_mean_ic": float(emp_mu),
        "threshold_ir": BASELINE["threshold_ir"],
        "passes_threshold": bool(abs(emp_ir) > 0.5 and abs(emp_mu) > 0.03),
    }


def build_payload() -> dict:
    snap = compute_snapshot()
    snap_dated = {"observed_at": datetime.now(timezone.utc).isoformat(), **snap}

    # Append-only forward history.
    history = []
    if RUN_OUT.exists():
        try:
            prev = json.loads(RUN_OUT.read_text(encoding="utf-8"))
            history = prev.get("forward_history", [])
        except Exception:
            history = []
    history.append(snap_dated)

    return {
        "monitor": "h53fix_cross_family_composite_ic",
        "task": "H53-FIX-MONITOR",
        "status": "RESEARCH_ONLY",
        "paper_only": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "description": (
            "Forward IC monitor for the H53-FIX cross-family composite "
            "(Charter §5 #2 spike, SIGNAL_NEGATIVE). Tracks composite IC/IR/ρ̄ "
            "on real data over time. Not a strategy: no P&L, no orders, no config."
        ),
        "composite_legs": COMPOSITE_LEGS,
        "registration_baseline": BASELINE,
        "latest": snap_dated,
        "forward_history": history,
        "monitor_cadence": {
            "per_run": ["composite_ir", "composite_mean_ic", "rho_mean", "n_common_dates"],
            "drift_vs_baseline": ["composite_ir delta", "rho_mean delta"],
        },
        "hard_prohibitions": [
            "do_not_place_live_orders",
            "do_not_modify_production_config",
            "do_not_write_value_account_positions_or_trades",
            "do_not_promote_to_active_json",
            "do_not_fabricate_pnl_or_drawdown",
        ],
        "promotion_blockers": [
            "spike verdict is SIGNAL_NEGATIVE (composite IR 0.317 < 0.5 threshold)",
            "ENGINE-OHLV-V1 diff uncommitted; engine not frozen (no engine-frozen-vN tag)",
            "no H42 9-condition acceptance-gate run exists for this signal",
        ],
    }


def build_report(payload: dict) -> str:
    latest = payload["latest"]
    base = payload["registration_baseline"]
    if "error" in latest:
        return f"# H53-FIX IC Forward Monitor\n\nERROR: {latest['error']}\n"

    d_ir = latest["composite_ir"] - base["composite_ir"]
    d_rho = latest["rho_mean"] - base["rho_mean"]
    lines = [
        "# H53-FIX Cross-Family Composite — Paper-Only Forward IC Monitor",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Status:** {payload['status']} · **Paper only:** {payload['paper_only']}",
        f"**Monitor:** {payload['monitor']}",
        "",
        "> Tracks the IC/IR of a cross-sectional factor composite over time. "
        "This is a research signal, NOT a strategy — it has no P&L, trades, or "
        "drawdown, and is never promoted to production. See promotion_blockers.",
        "",
        "## Latest snapshot",
        "",
        f"- IC window: {latest['ic_window']} ({latest['n_common_dates']} common dates)",
        f"- Composite IR: **{latest['composite_ir']:+.4f}** "
        f"(threshold {latest['threshold_ir']}, baseline {base['composite_ir']:+.4f}, "
        f"Δ {d_ir:+.4f})",
        f"- Composite mean |IC|: {abs(latest['composite_mean_ic']):.4f} "
        f"(baseline {base['composite_mean_ic']:.4f})",
        f"- Mean pairwise ρ̄: {latest['rho_mean']:.4f} "
        f"(baseline {base['rho_mean']:.4f}, Δ {d_rho:+.4f})",
        f"- Passes IR>0.5 threshold: **{latest['passes_threshold']}**",
        "",
        "## Composite legs (fixed at registration)",
        "",
        "| Theme | Factor | Mean IC | IR | Obs |",
        "|-------|--------|---------|-----|-----|",
    ]
    for leg in latest["legs"]:
        if "error" in leg:
            lines.append(f"| {leg['theme']} | {leg['factor_id']} | — | ERR: {leg['error']} | — |")
        else:
            lines.append(
                f"| {leg['theme']} | {leg['factor_id']} | {leg['mean_ic']:+.4f} | "
                f"{leg['ir']:+.4f} | {leg['n_obs']} |"
            )
    lines += [
        "",
        f"## Forward history ({len(payload['forward_history'])} snapshots)",
        "",
        "| Observed (UTC) | IC window | Composite IR | mean|IC| | ρ̄ | dates |",
        "|----------------|-----------|--------------|----------|-----|-------|",
    ]
    for h in payload["forward_history"]:
        if "error" in h:
            continue
        lines.append(
            f"| {h['observed_at'][:19]} | {h['ic_window']} | {h['composite_ir']:+.4f} | "
            f"{abs(h['composite_mean_ic']):.4f} | {h['rho_mean']:.4f} | {h['n_common_dates']} |"
        )
    lines += [
        "",
        "## Promotion blockers (why this stays paper-only)",
        "",
    ]
    for b in payload["promotion_blockers"]:
        lines.append(f"- {b}")
    lines += [
        "",
        "## Prohibitions",
        "",
    ]
    for p in payload["hard_prohibitions"]:
        lines.append(f"- {p}")
    lines += [
        "",
        "## Verdict",
        "",
        "**RESEARCH_ONLY** — forward observation of a SIGNAL_NEGATIVE factor "
        "composite. No promotion, no orders, no production-config changes. The "
        "monitor exists to detect whether forward IC drifts materially from the "
        "registration baseline (e.g. decays toward 0, or — unexpectedly — rises "
        "toward the 0.5 bar). Either way the decision returns to the user/Codex.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="H53-FIX composite paper-only forward IC monitor")
    ap.add_argument("--dry-run", action="store_true", help="compute + print, do not write artifacts")
    args = ap.parse_args()

    payload = build_payload()
    report = build_report(payload)

    if args.dry_run:
        print(report)
        print("\n[dry-run] no files written.")
        return 0

    RUN_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    RUN_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_OUT.write_text(report, encoding="utf-8")
    latest = payload["latest"]
    print(f"Wrote {RUN_OUT}")
    print(f"Wrote {REPORT_OUT}")
    print(f"Status: {payload['status']} · composite_ir={latest.get('composite_ir')} "
          f"passes={latest.get('passes_threshold')} "
          f"history={len(payload['forward_history'])} snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
