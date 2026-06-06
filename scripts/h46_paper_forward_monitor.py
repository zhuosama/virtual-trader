#!/usr/bin/env python3
"""H46 paper-only forward monitor for rejected-but-interesting candidates.

This script is intentionally non-trading. It reads completed H39/H42 research
artifacts and turns the best candidates into a paper-only watchlist with
benchmark-relative metrics and explicit research-only gates.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parent.parent
H39_RUN = ROOT / "backtest/runs/fundamental_value_h39_unblock_search.json"
H42_RUN = ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json"
H49B_RUN = ROOT / "backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json"
H50B_RUN = ROOT / "backtest/runs/fundamental_value_h50b_quality_value_search.json"
RUN_OUT = ROOT / "backtest/runs/fundamental_value_h46_paper_forward_monitor.json"
REPORT_OUT = ROOT / "reports/h46_paper_forward_monitor_report.md"


@dataclass(frozen=True)
class PaperCandidate:
    source: str
    rank: int
    overlay: str
    params: Dict
    metrics: Dict
    terminal_losing_streak: int
    execution_blockers: List[str]
    execution_warnings: List[str]
    annualized_turnover: Optional[float]
    gate_status: str
    research_only_reason: str
    registered_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "source": self.source,
            "rank": self.rank,
            "overlay": self.overlay,
            "params": self.params,
            "metrics": self.metrics,
            "terminal_losing_streak": self.terminal_losing_streak,
            "execution_blockers": self.execution_blockers,
            "execution_warnings": self.execution_warnings,
            "annualized_turnover": self.annualized_turnover,
            "gate_status": self.gate_status,
            "research_only_reason": self.research_only_reason,
            "registered_at": self.registered_at,
        }


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.2f}%"


def plain_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def overlay_name(candidate: Dict) -> str:
    return candidate.get("overlay", {}).get("name", "unknown")


def metrics_from_candidate(candidate: Dict) -> Dict:
    metrics = dict(candidate.get("metrics", {}))
    metrics.setdefault("excess_return", metrics.get("total_return", 0) - metrics.get("hs300_return", 0))
    return metrics


def candidate_from_h39(candidate: Dict, rank: int) -> PaperCandidate:
    metrics = metrics_from_candidate(candidate)
    return PaperCandidate(
        source="H39",
        rank=rank,
        overlay=overlay_name(candidate),
        params=dict(candidate.get("params", {})),
        metrics=metrics,
        terminal_losing_streak=int(candidate.get("terminal_losing_streak", 0)),
        execution_blockers=list(candidate.get("execution_blockers", [])),
        execution_warnings=list(candidate.get("execution_warnings", [])),
        annualized_turnover=candidate.get("annualized_turnover"),
        gate_status="PAPER_ONLY",
        research_only_reason="H40-H42 robustness rejected live/config promotion.",
    )


def candidate_from_h42(candidate: Dict, rank: int) -> PaperCandidate:
    deploy = candidate.get("deploy_window", {})
    metrics = metrics_from_candidate(deploy)
    passes_gate = bool(candidate.get("passes_acceptance_gate", False))
    reason = (
        "No H42 candidate passed the full multi-window acceptance gate."
        if not passes_gate
        else "Candidate still requires forward-only observation before any promotion."
    )
    return PaperCandidate(
        source="H42",
        rank=rank,
        overlay=overlay_name(candidate),
        params=dict(candidate.get("params", {})),
        metrics=metrics,
        terminal_losing_streak=int(deploy.get("terminal_losing_streak", 0)),
        execution_blockers=list(deploy.get("execution_blockers", [])),
        execution_warnings=list(deploy.get("execution_warnings", [])),
        annualized_turnover=deploy.get("annualized_turnover"),
        gate_status="PAPER_ONLY",
        research_only_reason=reason,
    )


def candidate_from_h49b(candidate: Dict, rank: int) -> PaperCandidate:
    """Load best H49b sector-neutral RS candidate (forward-only paper track)."""
    deploy = candidate.get("deploy_window", {})
    metrics = metrics_from_candidate(deploy)
    return PaperCandidate(
        source="H49b",
        rank=rank,
        overlay=overlay_name(candidate),
        params=dict(candidate.get("params", {})),
        metrics=metrics,
        terminal_losing_streak=int(deploy.get("terminal_losing_streak", 0)),
        execution_blockers=list(deploy.get("execution_blockers", [])),
        execution_warnings=list(deploy.get("execution_warnings", [])),
        annualized_turnover=deploy.get("annualized_turnover"),
        gate_status="PAPER_ONLY",
        research_only_reason="H49b sector-neutral RS candidate; forward-only paper observation from 2026-05-23.",
        registered_at="2026-05-23",
    )


def candidate_from_h50b(candidate: Dict, rank: int) -> PaperCandidate:
    """Load best H50b quality-value candidate (forward-only paper track)."""
    deploy = candidate.get("deploy_window", {})
    metrics = metrics_from_candidate(deploy)
    return PaperCandidate(
        source="H50b",
        rank=rank,
        overlay=overlay_name(candidate),
        params=dict(candidate.get("params", {})),
        metrics=metrics,
        terminal_losing_streak=int(deploy.get("terminal_losing_streak", 0)),
        execution_blockers=list(deploy.get("execution_blockers", [])),
        execution_warnings=list(deploy.get("execution_warnings", [])),
        annualized_turnover=deploy.get("annualized_turnover"),
        gate_status="PAPER_ONLY",
        research_only_reason="H50b quality-value candidate; forward-only paper observation from 2026-05-23.",
        registered_at="2026-05-23",
    )


def collect_candidates(h39: Dict, h42: Dict, h49b: Dict, h50b: Dict, top_n: int) -> List[PaperCandidate]:
    candidates: List[PaperCandidate] = []
    h39_clean = h39.get("top_clean_candidates", [])
    if h39_clean:
        candidates.append(candidate_from_h39(h39_clean[0], 1))

    for idx, item in enumerate(h42.get("top_candidates_multi_window", [])[:top_n], start=1):
        candidates.append(candidate_from_h42(item, idx))

    h49b_top = h49b.get("top_candidates_multi_window", [])
    if h49b_top:
        candidates.append(candidate_from_h49b(h49b_top[0], 1))

    h50b_top = h50b.get("top_candidates_multi_window", [])
    if h50b_top:
        candidates.append(candidate_from_h50b(h50b_top[0], 1))

    return candidates


def compute_summary(candidates: Iterable[PaperCandidate]) -> Dict:
    rows = list(candidates)
    if not rows:
        return {
            "candidate_count": 0,
            "paper_only_count": 0,
            "best_excess_return": None,
            "worst_max_drawdown": None,
            "max_losing_streak": 0,
            "total_gate_pass": 0,
        }
    return {
        "candidate_count": len(rows),
        "paper_only_count": sum(1 for row in rows if row.gate_status == "PAPER_ONLY"),
        "best_excess_return": max(row.metrics.get("excess_return", 0) for row in rows),
        "worst_max_drawdown": min(row.metrics.get("max_drawdown", 0) for row in rows),
        "max_losing_streak": max(row.terminal_losing_streak for row in rows),
        "total_gate_pass": 0,
    }


def worldquant_reference() -> Dict:
    return {
        "scope": "design_reference_only",
        "borrowed_concepts": [
            "dataset categories",
            "universe coverage",
            "delay/look-ahead control",
            "sector or industry neutralization",
            "decay/signal smoothing",
            "single-name capital limits",
            "turnover monitoring",
        ],
        "missing_for_production_alpha": [
            "analyst estimates",
            "news/sentiment",
            "options",
            "model data",
            "insider transactions",
            "short interest",
            "point-in-time sector classification",
        ],
        "monitor_mapping": {
            "delay": "All candidates are treated as delay-1 or slower; no same-day signal use.",
            "universe": "Uses H30/H38 PIT HS300 candidate universe evidence.",
            "neutralization": "Not implemented yet; H45 requires sector-aware redesign.",
            "decay": "Approximated by rebalance frequency; no daily alpha decay engine yet.",
            "truncation": "Approximated by max_position_pct.",
            "nan_handling": "Fail closed via data-quality and price-coverage gates.",
        },
    }


def build_payload(top_n: int) -> Dict:
    h39 = load_json(H39_RUN)
    h42 = load_json(H42_RUN)
    h49b = load_json(H49B_RUN)
    h50b = load_json(H50B_RUN)
    candidates = collect_candidates(h39, h42, h49b, h50b, top_n)
    summary = compute_summary(candidates)
    cadence = {
        "daily": [
            "hs300_excess_return",
            "max_drawdown",
            "trade_count",
            "terminal_losing_streak",
            "gate_status",
        ],
        "weekly": [
            "beat_hs300_status",
            "turnover_status",
            "data_quality_status",
            "research_only_review_note",
        ],
    }
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task": "H46",
        "status": "RESEARCH_ONLY",
        "paper_only": True,
        "inputs": {
            "h39_run": str(H39_RUN),
            "h42_run": str(H42_RUN),
            "h49b_run": str(H49B_RUN),
            "h50b_run": str(H50B_RUN),
        },
        "summary": summary,
        "monitor_cadence": cadence,
        "worldquant_reference": worldquant_reference(),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "hard_prohibitions": [
            "do_not_place_live_orders",
            "do_not_modify_production_config",
            "do_not_write_value_account_positions_or_trades",
        ],
        "next_required_work": [
            "H47 production price rebuild before production promotion.",
            "H45 alpha-source implementation before another parameter-only grid.",
            "Paper monitor may be refreshed after new forward-only data is available.",
        ],
    }


def build_report(payload: Dict) -> str:
    summary = payload["summary"]
    lines = [
        "# H46 - Paper-Only Forward Monitor Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Status:** {payload['status']}",
        f"**Paper only:** {payload['paper_only']}",
        "",
        "## Summary",
        "",
        f"- Candidate count: {summary['candidate_count']}",
        f"- Paper-only candidates: {summary['paper_only_count']}",
        f"- Gate passed: {summary['total_gate_pass']} candidates",
        f"- Best excess return: {pct(summary['best_excess_return'])}",
        f"- Worst max drawdown: {pct(summary['worst_max_drawdown'])}",
        f"- Max losing streak: {summary['max_losing_streak']}",
        "",
        "## Candidate Watchlist",
        "",
        "| Source | Rank | Overlay | Return | HS300 | Excess | MaxDD | Trades | Streak | Turnover | Gate |",
        "|--------|------|---------|--------|-------|--------|-------|--------|--------|----------|------|",
    ]
    for candidate in payload["candidates"]:
        metrics = candidate["metrics"]
        lines.append(
            "| {source} | {rank} | {overlay} | {ret} | {hs300} | {excess} | {dd} | {trades} | {streak} | {turnover} | {gate} |".format(
                source=candidate["source"],
                rank=candidate["rank"],
                overlay=candidate["overlay"],
                ret=pct(metrics.get("total_return")),
                hs300=pct(metrics.get("hs300_return")),
                excess=pct(metrics.get("excess_return")),
                dd=pct(metrics.get("max_drawdown")),
                trades=metrics.get("trade_count", 0),
                streak=candidate["terminal_losing_streak"],
                turnover=plain_pct(candidate.get("annualized_turnover")),
                gate=candidate["gate_status"],
            )
        )

    wq = payload["worldquant_reference"]
    lines.extend([
        "",
        "## Monitor Cadence",
        "",
        f"- Daily metrics: {', '.join(payload['monitor_cadence']['daily'])}",
        f"- Weekly metrics: {', '.join(payload['monitor_cadence']['weekly'])}",
        "",
        "## WorldQuant-Inspired Reference",
        "",
        f"- Scope: {wq['scope']}",
        f"- Borrowed concepts: {', '.join(wq['borrowed_concepts'])}",
        f"- Missing for production alpha: {', '.join(wq['missing_for_production_alpha'])}",
        "",
        "## Prohibitions",
        "",
    ])
    for item in payload["hard_prohibitions"]:
        lines.append(f"- {item}")

    lines.extend([
        "",
        "## Verdict",
        "",
        "**RESEARCH_ONLY** - H46 creates a paper-only watchlist. It does not promote any strategy or modify live/shadow production config.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build H46 paper-only forward monitor artifacts")
    parser.add_argument("--top-n", type=int, default=3, help="Number of H42 candidates to include")
    parser.add_argument("--output-run", type=Path, default=RUN_OUT)
    parser.add_argument("--output-report", type=Path, default=REPORT_OUT)
    args = parser.parse_args()

    payload = build_payload(max(args.top_n, 0))
    args.output_run.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_run.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_report.write_text(build_report(payload), encoding="utf-8")
    print(f"Wrote H46 run: {args.output_run}")
    print(f"Wrote H46 report: {args.output_report}")
    print(f"Status: {payload['status']}")
    print(f"Candidates: {payload['summary']['candidate_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

