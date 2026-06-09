#!/usr/bin/env python3
"""Validate consistency between H39-H42/H46-H47 JSON and Markdown reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parent.parent
PROVIDER_LABEL_H51A = "tushare:daily"


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    json_path: Path
    report_path: Path
    validator: Callable[[dict, str], List[str]]


@dataclass
class ArtifactCheck:
    name: str
    passed: bool
    detail: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(read_text(path))


def has_line(report: str, expected: str) -> bool:
    return any(line.strip() == expected for line in report.splitlines())


def report_contains(report: str, expected: str) -> bool:
    return expected in report


def add_missing(errors: List[str], report: str, expected: str, label: Optional[str] = None) -> None:
    if not report_contains(report, expected):
        errors.append(f"missing {label or expected!r}")


def add_missing_line(errors: List[str], report: str, expected: str, label: Optional[str] = None) -> None:
    if not has_line(report, expected):
        errors.append(f"missing line {label or expected!r}")


def status_from_bool(value: bool, true_value: str, false_value: str) -> str:
    return true_value if value else false_value


def validate_h39(data: dict, report: str) -> List[str]:
    errors: List[str] = []
    status = status_from_bool(data.get("candidate_found", False), "CANDIDATE_FOUND", "RESEARCH_ONLY")
    add_missing_line(errors, report, f"**Status:** {status}", "status")
    add_missing(errors, report, f"Stage A overlays screened: {data['stage_a_count']}", "stage_a_count")
    add_missing(errors, report, f"Stage B runs: {data['stage_b_count']}", "stage_b_count")
    add_missing(errors, report, f"Clean candidates: {data['clean_candidate_count']}", "clean_candidate_count")
    if len(data.get("top_clean_candidates", [])) != data["clean_candidate_count"]:
        errors.append(
            f"top_clean_candidates length {len(data.get('top_clean_candidates', []))} "
            f"!= clean_candidate_count {data['clean_candidate_count']}"
        )
    return errors


def validate_h40(data: dict, report: str) -> List[str]:
    errors: List[str] = []
    robust = data["robustness"]
    status = status_from_bool(
        bool(robust.get("robust_enough_for_shadow_candidate")),
        "CANDIDATE_FOR_FORWARD_TRIAL",
        "RESEARCH_ONLY",
    )
    add_missing_line(errors, report, f"**Status:** {status}", "status")
    add_missing(errors, report, f"Positive windows: {robust['positive_windows']}/{robust['window_count']}", "positive_windows")
    add_missing(errors, report, f"Unblocked windows: {robust['unblocked_windows']}/{robust['window_count']}", "unblocked_windows")
    add_missing(errors, report, f"Beat HS300 windows: {robust['beat_hs300_windows']}/{robust['window_count']}", "beat_hs300_windows")
    audit = data["execution_audit"]
    add_missing(errors, report, f"Execution can deploy: {audit['execution_can_deploy']}", "execution_can_deploy")
    add_missing(errors, report, f"Missing liquidity trades: {audit['liquidity']['missing_liquidity_count']}/{audit['liquidity']['trade_count']}", "missing_liquidity")
    if audit["execution_blockers"]:
        add_missing(errors, report, "### Execution Blockers", "execution_blockers section")
    else:
        add_missing(errors, report, "### Execution Blockers\n- none", "empty execution_blockers")
    return errors


def validate_h41(data: dict, report: str) -> List[str]:
    errors: List[str] = []
    add_missing_line(errors, report, f"**Candidates evaluated:** {data['candidate_count']}", "candidate_count")
    ranked = data.get("ranked", [])
    if len(ranked) != data["candidate_count"]:
        errors.append(f"ranked length {len(ranked)} != candidate_count {data['candidate_count']}")
    if ranked:
        best = ranked[0]
        add_missing(errors, report, f"| {best['candidate']['overlay']['name']} |", "top candidate row")
        expected_verdict = (
            "RESEARCH_ONLY"
            if best["unblocked_windows"] <= 1 or best["beat_hs300_windows"] == 0
            else "CANDIDATE_FOR_FORWARD_TRIAL"
        )
        add_missing(errors, report, f"**{expected_verdict}**", "verdict")
    return errors


def validate_h42(data: dict, report: str) -> List[str]:
    errors: List[str] = []
    expected = {
        "verdict": f"**Verdict:** {data['verdict']}",
        "stage_a": f"Stage A (overlay screening): {data['stage_a_count']} overlays",
        "stage_b": f"Stage B (param grid): {data['stage_b_count']} runs",
        "seed": f"Sanity seeds: {data.get('seed_count', 0)} known candidates",
        "clean": f"Clean deploy-window candidates: {data['clean_deploy_count']}",
        "stage_c": f"Stage C (multi-window): {data['stage_c_count']} candidates",
        "gate": f"Gate passed: {data['gate_pass_count']} candidates",
    }
    for label, snippet in expected.items():
        add_missing(errors, report, snippet, label)
    if len(data.get("top_candidates_multi_window", [])) != data["stage_c_count"]:
        errors.append(
            f"top_candidates_multi_window length {len(data.get('top_candidates_multi_window', []))} "
            f"!= stage_c_count {data['stage_c_count']}"
        )
    gate_pass = sum(1 for item in data.get("top_candidates_multi_window", []) if item.get("passes_acceptance_gate"))
    if gate_pass != data["gate_pass_count"]:
        errors.append(f"computed gate_pass_count {gate_pass} != json {data['gate_pass_count']}")
    return errors


def validate_h46(data: dict, report: str) -> List[str]:
    errors: List[str] = []
    summary = data["summary"]
    expected = {
        "status": f"**Status:** {data['status']}",
        "paper": f"**Paper only:** {data['paper_only']}",
        "candidate_count": f"Candidate count: {summary['candidate_count']}",
        "paper_count": f"Paper-only candidates: {summary['paper_only_count']}",
        "gate": f"Gate passed: {summary['total_gate_pass']} candidates",
        "verdict": "**RESEARCH_ONLY**",
    }
    for label, snippet in expected.items():
        add_missing(errors, report, snippet, label)
    if data.get("status") != "RESEARCH_ONLY":
        errors.append(f"status {data.get('status')} != RESEARCH_ONLY")
    if not data.get("paper_only"):
        errors.append("paper_only is not true")
    if len(data.get("candidates", [])) != summary["candidate_count"]:
        errors.append(
            f"candidates length {len(data.get('candidates', []))} "
            f"!= candidate_count {summary['candidate_count']}"
        )
    if summary["paper_only_count"] != summary["candidate_count"]:
        errors.append(
            f"paper_only_count {summary['paper_only_count']} "
            f"!= candidate_count {summary['candidate_count']}"
        )
    live_gate = [item for item in data.get("candidates", []) if item.get("gate_status") != "PAPER_ONLY"]
    if live_gate:
        errors.append(f"non-paper gate statuses: {len(live_gate)}")
    return errors


def validate_h47(data: dict, report: str) -> List[str]:
    errors: List[str] = []
    coverage = data["coverage"]
    summary = data["fetch_summary"]
    expected = {
        "status": f"**Status:** {data['status']}",
        "provider": f"**Provider:** {coverage['provider']}",
        "stock_adjustment": f"**Stock adjustment:** {coverage['stock_adjustment']}",
        "benchmark": f"**Benchmark provider:** {coverage['benchmark_provider']}",
        "benchmark_adjustment": f"**Benchmark adjustment:** {coverage['benchmark_adjustment']}",
        "active_gap_fill": f"**Active gap fill:** {coverage['active_gap_fill']}",
        "ok": f"OK: {coverage['ok']}",
        "rows": f"Rows: {coverage['rows']}",
        "requested": f"Requested tickers: {summary['requested_tickers']}",
        "successful": f"Successful tickers: {summary['successful_tickers']}",
        "verdict": "**CANDIDATE_DATASET**",
    }
    for label, snippet in expected.items():
        add_missing(errors, report, snippet, label)
    if data.get("status") != "CANDIDATE_DATASET":
        errors.append(f"status {data.get('status')} != CANDIDATE_DATASET")
    if coverage.get("provider") != "tushare:pro_bar:qfq":
        errors.append(f"provider {coverage.get('provider')} != tushare:pro_bar:qfq")
    if coverage.get("stock_adjustment") != "qfq":
        errors.append(f"stock_adjustment {coverage.get('stock_adjustment')} != qfq")
    if coverage.get("benchmark_provider") != "tushare:index_daily":
        errors.append(f"benchmark_provider {coverage.get('benchmark_provider')} != tushare:index_daily")
    if coverage.get("benchmark_adjustment") != "published_index_level":
        errors.append(
            f"benchmark_adjustment {coverage.get('benchmark_adjustment')} != published_index_level"
        )
    if coverage.get("active_gap_fill") != "ffill_active_period_from_tushare_qfq_close":
        errors.append(
            f"active_gap_fill {coverage.get('active_gap_fill')} "
            "!= ffill_active_period_from_tushare_qfq_close"
        )
    if coverage.get("ok") is not True:
        errors.append("coverage.ok is not true")
    if coverage.get("missing_columns"):
        errors.append(f"missing_columns not empty: {len(coverage.get('missing_columns', []))}")
    if coverage.get("missing_data_columns"):
        errors.append(f"missing_data_columns not empty: {len(coverage.get('missing_data_columns', []))}")
    if summary.get("successful_tickers", 0) > summary.get("requested_tickers", 0):
        errors.append("successful_tickers exceeds requested_tickers")
    if len(data.get("fetch_results", [])) != summary.get("requested_tickers", 0):
        errors.append(
            f"fetch_results length {len(data.get('fetch_results', []))} "
            f"!= requested_tickers {summary.get('requested_tickers', 0)}"
        )
    return errors


def validate_h48(data: dict, report: str) -> List[str]:
    """Validate H48 artifacts: mirrors validate_h42 + enforces price-source provenance."""
    errors: List[str] = []
    # ── H42-mirror checks ──────────────────────────────────────────────
    expected = {
        "verdict": f"**Verdict:** {data['verdict']}",
        "stage_a": f"Stage A (overlay screening): {data['stage_a_count']} overlays",
        "stage_b": f"Stage B (param grid): {data['stage_b_count']} runs",
        "seed": f"Sanity seeds: {data.get('seed_count', 0)} known candidates",
        "clean": f"Clean deploy-window candidates: {data['clean_deploy_count']}",
        "stage_c": f"Stage C (multi-window): {data['stage_c_count']} candidates",
        "gate": f"Gate passed: {data['gate_pass_count']} candidates",
    }
    for label, snippet in expected.items():
        add_missing(errors, report, snippet, label)
    if len(data.get("top_candidates_multi_window", [])) != data["stage_c_count"]:
        errors.append(
            f"top_candidates_multi_window length {len(data.get('top_candidates_multi_window', []))} "
            f"!= stage_c_count {data['stage_c_count']}"
        )
    gate_pass = sum(1 for item in data.get("top_candidates_multi_window", []) if item.get("passes_acceptance_gate"))
    if gate_pass != data["gate_pass_count"]:
        errors.append(f"computed gate_pass_count {gate_pass} != json {data['gate_pass_count']}")

    # ── H48-specific provenance checks ─────────────────────────────────
    ps = data.get("price_source", {})
    if not ps:
        errors.append("price_source block missing")
    else:
        if ps.get("task") != "h47":
            errors.append(f"price_source.task {ps.get('task')!r} != 'h47'")
        if not isinstance(ps.get("file"), str) or not ps["file"].endswith("prices_h47_tushare_qfq_candidate.csv"):
            errors.append(f"price_source.file does not end with prices_h47_tushare_qfq_candidate.csv: {ps.get('file')!r}")
        if ps.get("provider") != "tushare:pro_bar:qfq":
            errors.append(f"price_source.provider {ps.get('provider')!r} != tushare:pro_bar:qfq")
        if ps.get("benchmark_provider") != "tushare:index_daily":
            errors.append(f"price_source.benchmark_provider {ps.get('benchmark_provider')!r} != tushare:index_daily")
        if not isinstance(ps.get("sha256"), str) or len(ps.get("sha256", "")) != 64:
            errors.append(f"price_source.sha256 missing or wrong length: {len(ps.get('sha256', ''))}")
        if not isinstance(ps.get("rows"), int) or ps.get("rows", 0) <= 0:
            errors.append(f"price_source.rows not a positive int: {ps.get('rows')!r}")
        if not isinstance(ps.get("ticker_columns"), int) or ps.get("ticker_columns", 0) <= 0:
            errors.append(f"price_source.ticker_columns not a positive int: {ps.get('ticker_columns')!r}")

    # ── Report-side provenance checks ──────────────────────────────────
    add_missing(errors, report, "**Task:** h47", "price_source.task in report")
    add_missing(errors, report, "prices_h47_tushare_qfq_candidate.csv", "H47 prices filename in report")
    add_missing(errors, report, "**Provider:** tushare:pro_bar:qfq", "price_source.provider in report")
    add_missing(errors, report, "**Benchmark provider:** tushare:index_daily", "price_source.benchmark_provider in report")

    # Task field must be H48
    if data.get("task") != "H48":
        errors.append(f"task {data.get('task')!r} != 'H48'")

    return errors


def validate_h49b(data: dict, report: str) -> List[str]:
    """Validate H49b artifacts: mirrors validate_h42 + enforces data_sources provenance."""
    errors: List[str] = []

    # ── H42-mirror checks ──────────────────────────────────────────────
    expected = {
        "verdict": f"**Verdict:** {data['verdict']}",
        "stage_a": f"Stage A (overlay screening): {data['stage_a_count']} overlays",
        "stage_b": f"Stage B (param grid): {data['stage_b_count']} runs",
        "clean": f"Clean deploy-window candidates: {data['clean_deploy_count']}",
        "stage_c": f"Stage C (multi-window): {data['stage_c_count']} candidates",
        "gate": f"Gate passed: {data['gate_pass_count']} candidates",
    }
    for label, snippet in expected.items():
        add_missing(errors, report, snippet, label)
    if len(data.get("top_candidates_multi_window", [])) != data["stage_c_count"]:
        errors.append(
            f"top_candidates_multi_window length "
            f"{len(data.get('top_candidates_multi_window', []))} "
            f"!= stage_c_count {data['stage_c_count']}"
        )
    gate_pass = sum(
        1 for item in data.get("top_candidates_multi_window", [])
        if item.get("passes_acceptance_gate")
    )
    if gate_pass != data["gate_pass_count"]:
        errors.append(f"computed gate_pass_count {gate_pass} != json {data['gate_pass_count']}")

    # ── Data sources provenance checks ─────────────────────────────────
    ds = data.get("data_sources", {})
    if not ds:
        errors.append("data_sources block missing")
    else:
        for key in ("prices", "sector_metadata", "universe"):
            if key not in ds:
                errors.append(f"data_sources.{key} missing")
                continue
            src = ds[key]
            if not isinstance(src.get("sha256"), str) or len(src.get("sha256", "")) != 64:
                errors.append(f"data_sources.{key}.sha256 missing or wrong length")

        # Prices must reference h47
        if ds.get("prices", {}).get("task") != "h47":
            errors.append(
                f"data_sources.prices.task {ds.get('prices', {}).get('task')!r} != 'h47'"
            )

        # Sector metadata must reference h49a
        sm = ds.get("sector_metadata", {})
        if sm.get("task") != "h49a":
            errors.append(f"data_sources.sector_metadata.task {sm.get('task')!r} != 'h49a'")
        if not isinstance(sm.get("snapshot_date"), str) or len(sm.get("snapshot_date", "")) < 8:
            errors.append("data_sources.sector_metadata.snapshot_date missing or invalid")
        if sm.get("provider") != "tushare:index_classify+index_member":
            errors.append(
                f"data_sources.sector_metadata.provider "
                f"{sm.get('provider')!r} != tushare:index_classify+index_member"
            )

    # ── Report-side provenance checks ──────────────────────────────────
    add_missing(errors, report, "**Prices:** H47 QFQ prices", "prices provenance in report")
    add_missing(errors, report, "**Sector metadata:** H49a SW L1", "sector provenance in report")
    add_missing(errors, report, "## Data Sources", "data sources section")
    add_missing(errors, report, "## Design Choices", "design choices section")
    add_missing(errors, report, "## H42 vs H48 vs H49b Comparison", "comparison section")
    add_missing(errors, report, "## Did Sector-Neutral Selection Help?", "delta section")
    add_missing(errors, report, "## Final Verdict", "final verdict section")

    # Task field must be H49b
    if data.get("task") != "H49b":
        errors.append(f"task {data.get('task')!r} != 'H49b'")

    return errors


def validate_h50a(data: dict, report: str) -> List[str]:
    """Validate H50a V2 artifacts: provenance, coverage thresholds, field completeness."""
    errors: List[str] = []
    prov = data.get("provenance", {})

    # ── Provenance (V2: 4-endpoint provider) ──
    if not prov:
        errors.append("provenance block missing")
    else:
        expected_provider = "tushare:fina_indicator+income+cashflow+balancesheet"
        if prov.get("provider") != expected_provider:
            errors.append(f"provider {prov.get('provider')!r} != {expected_provider!r}")
        if not isinstance(prov.get("endpoints"), list) or len(prov.get("endpoints", [])) != 4:
            errors.append(f"endpoints missing or != 4: {prov.get('endpoints')!r}")
        if "ingested_at" not in prov:
            errors.append("ingested_at missing from provenance")
        if prov.get("revision") != "V2":
            errors.append(f"revision {prov.get('revision')!r} != 'V2'")

    # ── Coverage fields ──
    for key in ("ticker_coverage_pct", "total_rows", "per_field_non_null_pct",
                "hard_field_min_pct", "soft_field_min_pct",
                "intermediate_min_pct", "verdict"):
        if key not in data:
            errors.append(f"coverage JSON missing {key}")

    # ── Per-field coverage must include all required fields (V2: added _total_cogs) ──
    per_field = data.get("per_field_non_null_pct", {})
    required_score_fields = [
        "roe", "roa", "gross_margin", "operating_margin",
        "current_ratio", "quick_ratio", "debt_to_equity",
        "operating_cash_flow_to_revenue", "free_cash_flow", "accruals_ratio",
    ]
    required_intermediate = [
        "_net_income", "_net_cashflow_op", "_total_assets",
        "_op_income", "_total_revenue", "_total_cogs",
    ]
    for f in required_score_fields + required_intermediate:
        if f not in per_field:
            errors.append(f"per_field_non_null_pct missing field {f}")

    # ── Coverage gates (V2: 85% hard, 85% intermediates, ROE count) ──
    gates = data.get("gates", {})
    if not gates:
        errors.append("gates block missing")
    else:
        for gate in ("ticker_coverage_ge_98pct", "hard_fields_ge_85pct",
                      "soft_fields_ge_50pct", "intermediates_ge_85pct",
                      "roe_overlap_count_ge_100"):
            if gate not in gates:
                errors.append(f"gates missing {gate}")

    # ── Ticker coverage threshold ──
    if data.get("ticker_coverage_pct", 0) < 98.0:
        errors.append(
            f"ticker_coverage {data['ticker_coverage_pct']}% below 98% gate"
        )

    # ── Hard field threshold (V2: 85%) ──
    if data.get("hard_field_min_pct", 0) < 85.0:
        errors.append(
            f"hard_field_min {data['hard_field_min_pct']}% below 85% gate"
        )

    # ── Soft field threshold ──
    if data.get("soft_field_min_pct", 0) < 50.0:
        errors.append(
            f"soft_field_min {data['soft_field_min_pct']}% below 50% gate"
        )

    # ── Intermediate threshold (V2: 85%) ──
    if data.get("intermediate_min_pct", 0) < 85.0:
        errors.append(
            f"intermediate_min {data['intermediate_min_pct']}% below 85% gate"
        )

    # ── ROE overlap ──
    roe_overlap = data.get("roe_overlap", {})
    if "overlap_count" not in roe_overlap:
        errors.append("roe_overlap missing overlap_count")
    if "anomaly_count" not in roe_overlap:
        errors.append("roe_overlap missing anomaly_count")
    if roe_overlap.get("overlap_count", 0) < 100:
        errors.append(
            f"roe_overlap count {roe_overlap.get('overlap_count', 0)} < 100"
        )

    # ── Fetch failures must be present (V2: per-endpoint) ──
    if "fetch_failures" not in data:
        errors.append("fetch_failures missing from coverage")
    elif isinstance(data.get("fetch_failures"), list):
        for ff in data["fetch_failures"]:
            if "endpoint" not in ff:
                errors.append(f"fetch_failure missing endpoint field: {ff}")
                break

    # ── Report-side checks ──
    add_missing(errors, report, "tushare:fina_indicator+income+cashflow+balancesheet",
                "provider in report")
    add_missing(errors, report, "## Coverage Summary", "coverage section")
    add_missing(errors, report, "## Per-Field Non-Null Distribution", "per-field section")
    add_missing(errors, report, "## Coverage Gates", "gates section")
    add_missing(errors, report, "## ROE Overlap Analysis", "roe overlap section")
    add_missing(errors, report, "## Verdict", "verdict section")

    # Verify verdict matches gates (V2 gate set)
    gates_all_ok = all(gates.get(g, False) for g in (
        "ticker_coverage_ge_98pct", "hard_fields_ge_85pct",
        "soft_fields_ge_50pct", "intermediates_ge_85pct",
        "roe_overlap_count_ge_100",
    ))
    expected_verdict = "CANDIDATE_DATASET" if gates_all_ok else "BLOCKED"
    actual_verdict = data.get("verdict", "")
    if actual_verdict != expected_verdict:
        errors.append(
            f"verdict {actual_verdict!r} != expected {expected_verdict!r} "
            f"based on gates {gates}"
        )

    return errors


def validate_h50b(data: dict, report: str) -> List[str]:
    """Validate H50b artifacts: data_sources sha256, scorer_substitution, scorer_design, exclusion_stats."""
    errors: List[str] = []

    # ── Data sources ──
    ds = data.get("data_sources", {})
    for key in ("prices", "sector_metadata", "fundamentals", "universe"):
        if key not in ds:
            errors.append(f"data_sources missing {key}")
        elif "sha256" not in ds[key]:
            errors.append(f"data_sources.{key} missing sha256")
        elif len(ds[key].get("sha256", "")) != 64:
            errors.append(f"data_sources.{key} sha256 invalid length")

    # ── Scorer substitution ──
    ss = data.get("scorer_substitution", {})
    if ss.get("from") != "fundamental_backtest.ValueScore":
        errors.append(f"scorer_substitution.from={ss.get('from')} != fundamental_backtest.ValueScore")
    if "h50b" not in ss.get("to", ""):
        errors.append(f"scorer_substitution.to={ss.get('to')} does not contain h50b")
    if ss.get("restored_after_run") is not True:
        errors.append(f"scorer_substitution.restored_after_run={ss.get('restored_after_run')} != true")
    if not ss.get("patched_modules"):
        errors.append("scorer_substitution.patched_modules empty")
    elif len(ss["patched_modules"]) < 2:
        errors.append(f"scorer_substitution.patched_modules has < 2 entries: {ss['patched_modules']}")

    # ── Scorer design ──
    sd = data.get("scorer_design", {})
    if sd.get("components") != ["profitability", "balance_sheet", "cash_flow"]:
        errors.append(f"scorer_design.components={sd.get('components')} != [profitability, balance_sheet, cash_flow]")
    if "valuation_omitted_reason" not in sd:
        errors.append("scorer_design.valuation_omitted_reason missing")

    # ── Exclusion stats ──
    es = data.get("exclusion_stats", {})
    for key in ("rebalances_total", "tickers_seen", "exclusion_rate_pct", "exclusion_reasons"):
        if key not in es:
            errors.append(f"exclusion_stats.{key} missing")
    if es.get("exclusion_rate_pct", 0) >= 30:
        errors.append(f"exclusion_rate_pct={es['exclusion_rate_pct']}% >= 30% warning threshold")

    # ── Report checks ──
    add_missing(errors, report, f"**Verdict:** {data.get('verdict', '')}", "verdict in report")
    add_missing(errors, report, "Data Sources", "data_sources section")
    add_missing(errors, report, "Scorer Substitution", "scorer_substitution section")
    add_missing(errors, report, "ValueScoreH50 Design", "scorer_design section")
    add_missing(errors, report, "Exclusion Stats", "exclusion_stats section")
    add_missing(errors, report, "Acceptance Gate", "acceptance gate section")
    add_missing(errors, report, "H42 vs H48 vs H49b vs H50b", "comparison table")

    return errors


def validate_h49a(data: dict, report: str) -> List[str]:
    """Validate H49a artifacts: provenance, coverage thresholds, histogram sanity."""
    errors: List[str] = []
    prov = data.get("provenance", {})

    # ── Provenance checks ──
    if not prov:
        errors.append("provenance block missing")
    else:
        if prov.get("provider") != "tushare:index_classify+index_member":
            errors.append(f"provider {prov.get('provider')!r} != tushare:index_classify+index_member")
        if prov.get("level") != "L1":
            errors.append(f"level {prov.get('level')!r} != L1")
        if prov.get("src") != "SW2021":
            errors.append(f"src {prov.get('src')!r} != SW2021")
        if not isinstance(prov.get("snapshot_date"), str) or len(prov["snapshot_date"]) < 8:
            errors.append(f"snapshot_date missing or invalid: {prov.get('snapshot_date')!r}")

    # ── Coverage counts ──
    for key in ("universe_ticker_count", "mapped_count", "unmapped_count",
                "multi_mapped_count", "coverage_pct"):
        if key not in data:
            errors.append(f"{key} missing from coverage")

    if "mapped_count" in data and "universe_ticker_count" in data:
        expected_pct = round(data["mapped_count"] / data["universe_ticker_count"] * 100, 2) \
            if data["universe_ticker_count"] else 0
        if abs(data.get("coverage_pct", 0) - expected_pct) > 0.1:
            errors.append(
                f"coverage_pct {data['coverage_pct']} != computed {expected_pct}"
            )

    # ── Coverage threshold (≥98%) ──
    if data.get("coverage_pct", 0) < 98.0:
        errors.append(
            f"coverage {data['coverage_pct']}% below 98% gate"
        )

    # ── Industry histogram sanity ──
    hist = data.get("industry_histogram", {})
    if not hist:
        errors.append("industry_histogram is empty")
    else:
        universe_n = data.get("universe_ticker_count", 1)
        for code, info in hist.items():
            if info.get("count", 0) > 0.4 * universe_n:
                errors.append(
                    f"industry {code} has {info['count']}/{universe_n} "
                    f"({info['count']/universe_n*100:.1f}%) > 40% of universe"
                )

    # ── Unmapped tickers must have reasons ──
    unmapped = data.get("unmapped_tickers", [])
    for entry in unmapped:
        if "reason" not in entry:
            errors.append(f"unmapped ticker {entry.get('ticker', '?')} missing reason")

    # ── Report-side checks ──
    add_missing(errors, report, "**Provider:** tushare:index_classify+index_member",
                "provider in report")
    add_missing(errors, report, f"**Snapshot date:** {prov.get('snapshot_date', '')}",
                "snapshot_date in report")
    add_missing(errors, report, "## Coverage Summary", "coverage section")
    add_missing(errors, report, "## Industry Histogram", "histogram section")
    add_missing(errors, report, "## Unmapped Tickers", "unmapped section")
    add_missing(errors, report, "## Multi-Mapped Tickers", "multi-mapped section")
    add_missing(errors, report, "## Verdict", "verdict section")

    return errors


def validate_h51a(data: dict, report: str) -> List[str]:
    """Validate H51a artifacts: provenance, coverage gates, vol unit, fetch_failures schema."""
    errors: List[str] = []
    prov = data.get("provenance", {})

    # ── Provenance checks ──
    if not prov:
        errors.append("provenance block missing")
    else:
        if prov.get("provider") != PROVIDER_LABEL_H51A:
            errors.append(f"provider {prov.get('provider')!r} != {PROVIDER_LABEL_H51A!r}")
        if "source_url" not in prov:
            errors.append("source_url missing from provenance")

    # ── Required top-level fields ──
    if "snapshot_date" not in data:
        errors.append("snapshot_date missing")
    if "ticker_coverage_pct" not in data:
        errors.append("ticker_coverage_pct missing")
    if "avg_rows_per_ticker" not in data:
        errors.append("avg_rows_per_ticker missing")
    if "total_rows" not in data:
        errors.append("total_rows missing")
    if "verdict" not in data:
        errors.append("verdict missing")

    # ── Columns check ──
    expected_columns = ["date", "ticker", "amount_rmb", "vol_shares", "source"]
    actual_columns = data.get("columns", [])
    if actual_columns != expected_columns:
        errors.append(f"columns {actual_columns!r} != {expected_columns!r}")

    # ── Vol unit check ──
    vol_unit = data.get("vol_unit", "")
    if "shares" not in vol_unit.lower() or "×100" not in vol_unit:
        errors.append(f"vol_unit {vol_unit!r} does not confirm shares (×100 from 手)")

    # ── Fetch failures schema ──
    failures = data.get("fetch_failures", None)
    if failures is None:
        errors.append("fetch_failures missing from coverage")
    elif isinstance(failures, list):
        for ff in failures:
            if "ticker" not in ff or "reason" not in ff:
                errors.append(f"fetch_failure missing ticker/reason: {ff}")
                break

    # ── Ticker coverage threshold (≥98%) ──
    tcp = data.get("ticker_coverage_pct", 0)
    if tcp < 98.0:
        errors.append(
            f"ticker_coverage {tcp}% below 98% gate"
        )

    # ── Avg rows threshold ──
    art = data.get("avg_rows_per_ticker", 0)
    if art < 600.0:
        errors.append(
            f"avg_rows_per_ticker {art} below 600 gate"
        )

    # ── ADTV gate ──
    adtv = data.get("adtv_gate", {})
    if adtv.get("overall_pct", 0) < 95.0:
        errors.append(
            f"adtv overall_pct {adtv.get('overall_pct')}% below 95% gate"
        )

    # ── Per-window ADTV gate ──
    adtv_per_window = data.get("adtv_computability_per_window", {})
    if not adtv_per_window:
        errors.append("adtv_computability_per_window missing")
    else:
        expected_windows = {"cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"}
        actual_windows = set(adtv_per_window.keys())
        missing_wins = expected_windows - actual_windows
        if missing_wins:
            errors.append(f"adtv_computability_per_window missing windows: {sorted(missing_wins)}")
        for wname, winfo in adtv_per_window.items():
            pct = winfo.get("computable_pct", 0)
            if pct < 95.0:
                errors.append(f"adtv_computability_per_window {wname} computable_pct {pct}% below 95%")

    # ── Unit sanity: implied price = amount_rmb / vol_shares must be in [0.5, 5000] RMB ──
    try:
        import csv
        import statistics
        csv_path = ROOT / "data/cn_pit/liquidity_h51a_daily_amount.csv"
        if csv_path.exists():
            prices = []
            with csv_path.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    vs = float(row.get("vol_shares", 0))
                    ar = float(row.get("amount_rmb", 0))
                    if vs > 0 and ar > 0:
                        prices.append(ar / vs)
            if len(prices) >= 100:
                sample = prices[:500] if len(prices) > 500 else prices
                sample.sort()
                median = statistics.median(sample)
                if median < 0.5:
                    errors.append(f"unit sanity FAIL: median implied price {median:.4f} RMB < 0.5 (units bug? expected 5-100 for HS300)")
                elif median > 5000.0:
                    errors.append(f"unit sanity FAIL: median implied price {median:.2f} RMB > 5000 (anomalous)")
            # < 100 samples: silently skip (e.g. test environments)
    except Exception as exc:
        errors.append(f"unit sanity ERROR: {exc}")

    # ── Fetch failures threshold ──
    if data.get("fetch_failures_count", 0) > 10:
        errors.append(
            f"fetch_failures_count {data['fetch_failures_count']} exceeds 10"
        )

    # ── Report-side checks ──
    add_missing(errors, report, PROVIDER_LABEL_H51A, "provider in report")
    add_missing(errors, report, "## Coverage Summary", "coverage section")
    add_missing(errors, report, "## Coverage Gates", "gates section")
    add_missing(errors, report, "## ADTV Computability", "adtv section")
    add_missing(errors, report, "## Fetch Failures", "failures section")
    add_missing(errors, report, "## Vol Unit Conversion", "vol unit section")
    add_missing(errors, report, "## Verdict", "verdict section")

    # ── Verify verdict consistency ──
    gates = data.get("gates", {})
    gates_all_ok = all(gates.get(g, False) for g in (
        "ticker_coverage_ge_98pct", "avg_rows_per_ticker_ge_600",
        "adtv_computable_ge_95pct", "fetch_failures_le_10",
        "adtv_computable_per_window_ge_95pct",
    ))
    expected_verdict = "CANDIDATE_DATASET" if gates_all_ok else "BLOCKED"
    actual_verdict = data.get("verdict", "")
    if actual_verdict != expected_verdict:
        errors.append(
            f"verdict {actual_verdict!r} != expected {expected_verdict!r} "
            f"based on gates {gates}"
        )

    return errors


def validate_h52a(data: dict, report: str) -> List[str]:
    """Validate H52a artifacts: provenance, coverage thresholds, schema pinning."""
    errors: List[str] = []
    prov = data.get("provenance", {})

    # ── Provenance checks ──
    if not prov:
        errors.append("provenance block missing")
    else:
        if prov.get("provider") != "tushare:index_weight":
            errors.append(f"provider {prov.get('provider')!r} != tushare:index_weight")
        if prov.get("index_code") != "000905.SH":
            errors.append(f"index_code {prov.get('index_code')!r} != 000905.SH")
        if "endpoints_used" not in prov:
            errors.append("endpoints_used missing from provenance")
        if "snapshot_cadence" not in prov:
            errors.append("snapshot_cadence missing from provenance")
        if prov.get("snapshot_cadence") != "monthly_last_trading_day":
            errors.append(
                f"snapshot_cadence {prov.get('snapshot_cadence')!r} != monthly_last_trading_day"
            )

    # ── Coverage gates ──
    total_snapshots = data.get("total_snapshots", 0)
    if total_snapshots < 80:
        errors.append(f"total_snapshots {total_snapshots} < 80")

    min_members = data.get("min_members_per_snapshot", 0)
    if min_members < 480:
        errors.append(f"min_members_per_snapshot {min_members} < 480")

    avg_members = data.get("avg_members_per_snapshot", 0)
    if avg_members < 490:
        errors.append(f"avg_members_per_snapshot {avg_members} < 490")

    fetch_fails = data.get("fetch_failures_count", 0)
    if fetch_fails > 5:
        errors.append(f"fetch_failures_count {fetch_fails} > 5")

    unique_tickers = data.get("unique_tickers_count", 0)
    if unique_tickers < 700:
        errors.append(f"unique_tickers_count {unique_tickers} < 700")

    # Row count consistency
    intervals_count = data.get("membership_intervals_count", 0)
    universe_path = ROOT / "data/cn_pit/universe_h52a_csi500.jsonl"
    if universe_path.exists():
        universe_rows = sum(1 for _ in universe_path.open(encoding="utf-8"))
        if universe_rows != intervals_count:
            errors.append(
                f"universe file row count {universe_rows} != membership_intervals_count {intervals_count}"
            )
    else:
        errors.append(f"universe file missing: {universe_path}")

    snapshots_path = ROOT / "data/cn_pit/universe_snapshots_h52a_csi500.jsonl"
    if snapshots_path.exists():
        snapshot_rows = sum(1 for _ in snapshots_path.open(encoding="utf-8"))
        # Total snapshot rows should be >= total_snapshots * ~500
        expected_min = total_snapshots * min_members if total_snapshots > 0 else 0
        if snapshot_rows < expected_min:
            errors.append(
                f"snapshot rows {snapshot_rows} < expected min {expected_min} "
                f"({total_snapshots} snapshots × {min_members} min members)"
            )
    else:
        errors.append(f"snapshots file missing: {snapshots_path}")

    # ── Report-side checks ──
    add_missing(errors, report, "tushare:index_weight", "provider in report")
    add_missing(errors, report, "000905.SH", "index code in report")
    add_missing(errors, report, "monthly_last_trading_day", "snapshot cadence in report")
    add_missing(errors, report, "## Coverage Summary", "coverage section")
    add_missing(errors, report, "## Design Decisions", "design decisions section")
    add_missing(errors, report, "## Coverage Gates", "gates section")
    add_missing(errors, report, "## Verdict", "verdict section")
    add_missing(errors, report, "## Top 5 Most-Consistent Members", "most-consistent section")
    add_missing(errors, report, "## Top 5 Most-Volatile Membership", "most-volatile section")

    # ── Verify verdict consistency ──
    gates_ok = (
        total_snapshots >= 80
        and min_members >= 480
        and avg_members >= 490
        and fetch_fails <= 5
        and unique_tickers >= 700
    )
    expected_verdict = "CANDIDATE_DATASET" if gates_ok else "BLOCKED"
    actual_verdict = data.get("verdict", "")
    if actual_verdict != expected_verdict:
        errors.append(
            f"verdict {actual_verdict!r} != expected {expected_verdict!r}"
        )

    return errors


def validate_h52b(data: dict, report: str) -> List[str]:
    """Validate H52b artifacts: provenance, coverage gates, universe_source asserts H52a."""
    errors: List[str] = []
    prov = data.get("provenance", {})

    # ── Provenance checks ──
    if not prov:
        errors.append("provenance block missing")
    else:
        if prov.get("provider") != "tushare:index_classify+index_member":
            errors.append(f"provider {prov.get('provider')!r} != tushare:index_classify+index_member")
        if prov.get("level") != "L1":
            errors.append(f"level {prov.get('level')!r} != L1")
        if prov.get("src") != "SW2021":
            errors.append(f"src {prov.get('src')!r} != SW2021")
        if not isinstance(prov.get("snapshot_date"), str) or len(prov["snapshot_date"]) < 8:
            errors.append(f"snapshot_date missing or invalid: {prov.get('snapshot_date')!r}")
        if not isinstance(prov.get("snapshot_timestamp"), str):
            errors.append(f"snapshot_timestamp missing or invalid: {prov.get('snapshot_timestamp')!r}")

    # ── universe_source MUST be H52a, not H30 ──
    if data.get("universe_source") != "data/cn_pit/universe_h52a_csi500.jsonl":
        errors.append(
            f"universe_source {data.get('universe_source')!r} != "
            f"data/cn_pit/universe_h52a_csi500.jsonl"
        )

    # ── Coverage counts ──
    for key in ("universe_ticker_count", "mapped_count", "unmapped_count",
                "multi_mapped_count", "coverage_pct"):
        if key not in data:
            errors.append(f"{key} missing from coverage")

    universe_n = data.get("universe_ticker_count", 0)

    if universe_n != 1074:
        errors.append(f"universe_ticker_count {universe_n} != 1074 (H52a unique ticker count)")

    if "mapped_count" in data and universe_n:
        mapped = data["mapped_count"]
        unmapped = data.get("unmapped_count", 0)
        if mapped + unmapped != universe_n:
            errors.append(f"mapped_count ({mapped}) + unmapped_count ({unmapped}) != universe_ticker_count ({universe_n})")
        expected_pct = round(mapped / universe_n * 100, 2)
        if abs(data.get("coverage_pct", 0) - expected_pct) > 0.1:
            errors.append(f"coverage_pct {data['coverage_pct']} != computed {expected_pct}")

    # ── Coverage threshold (≥95%) ──
    if data.get("coverage_pct", 0) < 95.0:
        errors.append(f"coverage {data['coverage_pct']}% below 95% gate")

    # ── Industry histogram sanity ──
    hist = data.get("industry_histogram", {})
    if not hist:
        errors.append("industry_histogram is empty")
    else:
        for code, info in hist.items():
            if universe_n and info.get("count", 0) > 0.4 * universe_n:
                errors.append(
                    f"industry {code} has {info['count']}/{universe_n} "
                    f"({info['count']/universe_n*100:.1f}%) > 40% of universe"
                )

    # ── multi_mapped sanity cap (≤50%) ──
    multi = data.get("multi_mapped_count", 0)
    if universe_n and multi / universe_n > 0.50:
        errors.append(
            f"multi_mapped {multi}/{universe_n} ({multi/universe_n*100:.1f}%) > 50% sanity cap"
        )

    # ── Unmapped tickers must have reasons ──
    unmapped = data.get("unmapped_tickers", [])
    for entry in unmapped:
        if "reason" not in entry or not entry.get("reason"):
            errors.append(f"unmapped ticker {entry.get('ticker', '?')} missing non-empty reason")

    # ── fetch_failures cap ──
    failures = data.get("fetch_failures", [])
    if len(failures) > 3:
        errors.append(f"fetch_failures {len(failures)} > 3")

    # ── CSV row count check ──
    metadata_path = ROOT / "data/cn_pit/sector_metadata_h52b_csi500.csv"
    if metadata_path.exists():
        csv_rows = sum(1 for _ in metadata_path.open(encoding="utf-8")) - 1  # minus header
        if csv_rows != data.get("mapped_count", 0):
            errors.append(f"CSV rows {csv_rows} != mapped_count {data.get('mapped_count')}")
    else:
        errors.append(f"CSV file missing: {metadata_path}")

    # ── Hard prohibitions: must NOT touch H49a files ──
    h49a_path = ROOT / "data/cn_pit/sector_metadata_sw_l1.csv"
    if not h49a_path.exists():
        errors.append("H49a sector file missing (must stay intact): " + str(h49a_path))

    # ── Report-side checks ──
    add_missing(errors, report, "**Provider:** tushare:index_classify+index_member",
                "provider in report")
    add_missing(errors, report, f"**Snapshot date:** {prov.get('snapshot_date', '')}",
                "snapshot_date in report")
    add_missing(errors, report, "## Coverage Summary", "coverage section")
    add_missing(errors, report, "## Industry Histogram", "histogram section")
    add_missing(errors, report, "## Unmapped Tickers", "unmapped section")
    add_missing(errors, report, "## Multi-Mapped Tickers", "multi-mapped section")
    add_missing(errors, report, "## Fetch Failures", "fetch failures section")
    add_missing(errors, report, "## Verdict", "verdict section")
    add_missing(errors, report, "universe_h52a_csi500.jsonl", "universe source reference in report")

    # ── Verdict consistency ──
    gates_ok = (
        data.get("coverage_pct", 0) >= 95.0
        and len(data.get("unmapped_tickers", [])) <= 53
        and len(hist) >= 25
        and len(failures) <= 3
    )
    if universe_n and multi / universe_n > 0.50:
        gates_ok = False
    for info in hist.values():
        if universe_n and info.get("count", 0) > 0.4 * universe_n:
            gates_ok = False
    expected_verdict = "CANDIDATE_DATASET" if gates_ok else "BLOCKED"
    actual_verdict = data.get("verdict", "")
    if actual_verdict != expected_verdict:
        errors.append(f"verdict {actual_verdict!r} != expected {expected_verdict!r}")

    return errors


def validate_h51b(data: dict, report: str) -> List[str]:
    """Validate H51b artifacts: data_sources sha256, scorer+sizing substitutions, risk_model_design, exclusion_stats."""
    errors: List[str] = []

    # ── Data sources (5 required: prices, sector_metadata, fundamentals, adtv_liquidity, universe) ──
    ds = data.get("data_sources", {})
    for key in ("prices", "sector_metadata", "fundamentals", "adtv_liquidity", "universe"):
        if key not in ds:
            errors.append(f"data_sources missing {key}")
        elif "sha256" not in ds[key]:
            errors.append(f"data_sources.{key} missing sha256")
        elif len(ds[key].get("sha256", "")) != 64:
            errors.append(f"data_sources.{key} sha256 invalid length")

    # adtv_liquidity must reference h51a
    adtv = ds.get("adtv_liquidity", {})
    if adtv.get("task") != "h51a":
        errors.append(f"data_sources.adtv_liquidity.task={adtv.get('task')} != 'h51a'")

    # ── Scorer substitution (reused from H50b) ──
    ss = data.get("scorer_substitution", {})
    if ss.get("from") != "fundamental_backtest.ValueScore":
        errors.append(f"scorer_substitution.from={ss.get('from')} != fundamental_backtest.ValueScore")
    if "h50b" not in ss.get("to", ""):
        errors.append(f"scorer_substitution.to={ss.get('to')} does not contain h50b")
    if ss.get("restored_after_run") is not True:
        errors.append(f"scorer_substitution.restored_after_run={ss.get('restored_after_run')} != true")

    # ── Sizing substitution ──
    sz = data.get("sizing_substitution", {})
    if sz.get("restored_after_run") is not True:
        errors.append(f"sizing_substitution.restored_after_run={sz.get('restored_after_run')} != true")
    if "from" not in sz:
        errors.append("sizing_substitution.from missing")
    if "to" not in sz:
        errors.append("sizing_substitution.to missing")
    if "sizing_block_diff" not in sz:
        errors.append("sizing_substitution.sizing_block_diff missing")

    # ── Risk model design ──
    rd = data.get("risk_model_design", {})
    if rd.get("min_active_names") != 5:
        errors.append(f"risk_model_design.min_active_names={rd.get('min_active_names')} != 5")
    if rd.get("vol_window_days") != 60:
        errors.append(f"risk_model_design.vol_window_days={rd.get('vol_window_days')} != 60")
    if rd.get("adtv_window_days") != 20:
        errors.append(f"risk_model_design.adtv_window_days={rd.get('adtv_window_days')} != 20")
    if rd.get("vol_return_basis") != "log":
        errors.append(f"risk_model_design.vol_return_basis={rd.get('vol_return_basis')} != 'log'")

    # ── Exclusion stats ──
    es = data.get("exclusion_stats", {})
    for key in ("rebalances_total", "vol_insufficient_data", "adtv_insufficient_data", "min_active_names_violated_count"):
        if key not in es:
            errors.append(f"exclusion_stats.{key} missing")

    # ── Report-side checks ──
    add_missing(errors, report, f"**Verdict:** {data.get('verdict', '')}", "verdict in report")
    add_missing(errors, report, "## Data Sources", "data_sources section")
    add_missing(errors, report, "## Scorer Substitution", "scorer_substitution section")
    add_missing(errors, report, "## Sizing Substitution", "sizing_substitution section")
    add_missing(errors, report, "## Risk Model Design", "risk_model_design section")
    add_missing(errors, report, "## Exclusion Stats", "exclusion_stats section")
    add_missing(errors, report, "## H42 vs H48 vs H49b vs H50b vs H51b", "5-way comparison")
    add_missing(errors, report, "## Verdict", "verdict section")

    return errors


def validate_h52c(data: dict, report: str) -> List[str]:
    """Validate H52c artifacts: provenance, 1076 columns, unit sanity, coverage gates."""
    errors: List[str] = []
    prov = data.get("provenance", {})
    cov = data.get("coverage", {})
    anom = data.get("anomalies", {})

    # ── Provenance checks ──
    if not prov:
        errors.append("provenance block missing")
    else:
        expected = {
            "stock_provider": "tushare:daily",
            "adjustment_provider": "tushare:adj_factor",
            "qfq_method": "snapshot_qfq_local_compute",
            "benchmark_provider": "tushare:index_daily",
            "benchmark_ticker": "000300.SS",
            "universe_source": "data/cn_pit/universe_h52a_csi500.jsonl",
        }
        for key, expected_val in expected.items():
            actual = prov.get(key)
            if actual != expected_val:
                errors.append(f"provenance.{key} {actual!r} != {expected_val!r}")

    # ── Coverage gate assertions ──
    if "ticker_coverage_pct" not in cov:
        errors.append("coverage.ticker_coverage_pct missing")
    elif cov["ticker_coverage_pct"] < 98.0:
        errors.append(f"ticker_coverage_pct {cov['ticker_coverage_pct']} < 98.0")

    if "min_trade_days_per_ticker" not in cov:
        errors.append("coverage.min_trade_days_per_ticker missing")
    # Brief allows short-lived members (new listings/delisted); max 5 with <60 days
    short_history = cov.get("tickers_with_short_history", [])
    if len(short_history) > 5:
        errors.append(f"tickers_with_short_history count {len(short_history)} > 5: {short_history}")

    median_price = cov.get("median_implied_qfq_price_rmb", 0)
    if median_price < 0.5 or median_price > 5000.0:
        errors.append(f"median_implied_qfq_price_rmb {median_price} not in [0.5, 5000]")

    # ADTV per-window computability
    adtv = cov.get("adtv_computability_per_window", {})
    expected_windows = {"cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"}
    if not adtv:
        errors.append("adtv_computability_per_window missing")
    else:
        for wname in expected_windows:
            if wname not in adtv:
                errors.append(f"adtv window {wname} missing")
            elif adtv[wname].get("computable_pct", 0) < 95.0:
                errors.append(f"adtv {wname} computable_pct {adtv[wname].get('computable_pct')} < 95.0")

    if "benchmark_coverage_pct" not in cov:
        errors.append("benchmark_coverage_pct missing")
    elif cov["benchmark_coverage_pct"] < 99.0:
        errors.append(f"benchmark_coverage_pct {cov['benchmark_coverage_pct']} < 99.0")

    if "universe_ticker_count" not in cov or cov["universe_ticker_count"] != 1074:
        errors.append(f"universe_ticker_count {cov.get('universe_ticker_count')} != 1074")

    # ── Fetch failures threshold ──
    failures = data.get("fetch_failures", [])
    if len(failures) > 20:
        errors.append(f"fetch_failures count {len(failures)} > 20")

    # ── Anomaly thresholds ──
    if anom.get("extreme_pct_chg_anomalies", 0) > 500:
        errors.append(f"extreme_pct_chg_anomalies {anom.get('extreme_pct_chg_anomalies')} > 500")
    if anom.get("tickers_with_no_qfq", 0) > 10:
        errors.append(f"tickers_with_no_qfq {anom.get('tickers_with_no_qfq')} > 10")
    if anom.get("tickers_with_no_h52c_data", 0) > 60:
        errors.append(f"tickers_with_no_h52c_data {anom.get('tickers_with_no_h52c_data')} > 60")

    # ── Column counts (verify CSV files exist and have correct dimensions) ──
    prices_path = ROOT / "data/cn_pit/prices_h52c_csi500_qfq.csv"
    if prices_path.exists():
        try:
            import csv
            with prices_path.open(encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                n_cols = len(header)
            if n_cols != 1076:
                errors.append(f"prices CSV has {n_cols} columns, expected 1076")
        except Exception as exc:
            errors.append(f"failed to read prices CSV: {exc}")

    liquidity_path = ROOT / "data/cn_pit/liquidity_h52c_csi500_daily_amount.csv"
    if liquidity_path.exists():
        try:
            import csv
            with liquidity_path.open(encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader)
            if header != ["date", "ticker", "amount_rmb", "vol_shares", "source"]:
                errors.append(f"liquidity CSV header {header} != expected 5 columns")
        except Exception as exc:
            errors.append(f"failed to read liquidity CSV: {exc}")

    # ── Date-format regression assertion (H52h strengthening) ──
    import pandas as _pd
    iso_pattern_date = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for csv_key, csv_path in [
        ("prices", prices_path),
        ("liquidity", liquidity_path),
    ]:
        if not csv_path.exists():
            continue
        try:
            df_sample = _pd.read_csv(csv_path, nrows=5, dtype={"date": str})
            for v in df_sample["date"]:
                if not iso_pattern_date.match(str(v)):
                    errors.append(f"h52c {csv_key} CSV date column not ISO YYYY-MM-DD: got {v!r}")
                    break
        except Exception as exc:
            errors.append(f"h52c {csv_key} date-format check failed: {exc}")

    # ── Report-side checks ──
    add_missing(errors, report, "H52c", "task in report")
    add_missing(errors, report, "## Provenance", "provenance section")
    add_missing(errors, report, "## Coverage Summary", "coverage section")
    add_missing(errors, report, "## ADTV Computability", "adtv section")
    add_missing(errors, report, "## Anomalies", "anomalies section")
    add_missing(errors, report, "## Fetch Failures", "failures section")
    add_missing(errors, report, "## Unit Conversions", "unit conversions section")
    add_missing(errors, report, "## Verdict", "verdict section")

    # Tushare provider references
    add_missing(errors, report, "tushare:daily", "stock provider in report")
    add_missing(errors, report, "snapshot_qfq_local_compute", "qfq method in report")

    return errors


def validate_h52d(data: dict, report: str) -> List[str]:
    """Validate H52d artifacts: provenance, coverage gates, field completeness."""
    errors: List[str] = []
    prov = data.get("provenance", {})

    # ── Provenance ──
    if not prov:
        errors.append("provenance block missing")
    else:
        expected_provider = "tushare:fina_indicator+income+cashflow+balancesheet"
        if prov.get("provider") != expected_provider:
            errors.append(f"provider {prov.get('provider')!r} != {expected_provider!r}")
        if not isinstance(prov.get("endpoints"), list) or len(prov.get("endpoints", [])) != 4:
            errors.append(f"endpoints missing or != 4: {prov.get('endpoints')!r}")
        if prov.get("axis") not in ("ticker", "period"):
            errors.append(f"axis {prov.get('axis')!r} not in (ticker, period)")
        if prov.get("universe_source") != "data/cn_pit/universe_h52a_csi500.jsonl":
            errors.append(f"universe_source {prov.get('universe_source')!r} != expected")

    # ── Coverage fields ──
    for key in ("ticker_coverage_pct", "total_rows", "per_field_non_null_pct",
                "hard_field_min_pct", "soft_field_min_pct",
                "intermediate_min_pct", "verdict"):
        if key not in data:
            errors.append(f"missing {key}")

    # ── Per-field coverage ──
    per_field = data.get("per_field_non_null_pct", {})
    required_score = ALL_SCORE_FIELDS = [
        "roe", "roa", "gross_margin", "operating_margin",
        "current_ratio", "quick_ratio", "debt_to_equity",
        "operating_cash_flow_to_revenue", "free_cash_flow", "accruals_ratio",
    ]
    required_intermediate = [
        "_net_income", "_net_cashflow_op", "_total_assets",
        "_op_income", "_total_revenue", "_total_cogs",
    ]
    for f in required_score + required_intermediate:
        if f not in per_field:
            errors.append(f"per_field_non_null_pct missing {f}")

    # ── Gates ──
    gates = data.get("gates", {})
    if not gates:
        errors.append("gates block missing")
    else:
        for gate in ("ticker_coverage_ge_98pct", "hard_fields_ge_85pct",
                      "soft_fields_ge_50pct", "intermediates_ge_85pct",
                      "accruals_ratio_ge_50pct", "h50a_overlap_ge_99pct"):
            if gate not in gates:
                errors.append(f"gates missing {gate}")

    # ── Threshold assertions ──
    if data.get("ticker_coverage_pct", 0) < 98.0:
        errors.append(f"ticker_coverage {data['ticker_coverage_pct']}% < 98%")
    if data.get("hard_field_min_pct", 0) < 85.0:
        errors.append(f"hard_field_min {data['hard_field_min_pct']}% < 85%")
    if data.get("soft_field_min_pct", 0) < 50.0:
        errors.append(f"soft_field_min {data['soft_field_min_pct']}% < 50%")
    if data.get("intermediate_min_pct", 0) < 85.0:
        errors.append(f"intermediate_min {data['intermediate_min_pct']}% < 85%")

    # ── ROE overlap ──
    roe = data.get("h50a_overlap", {})
    if "overlap_count" not in roe:
        errors.append("h50a_overlap missing overlap_count")

    # ── Fetch failures ──
    if "fetch_failures" not in data:
        errors.append("fetch_failures missing")
    elif len(data.get("fetch_failures", [])) > 5:
        errors.append(f"fetch_failures {len(data['fetch_failures'])} > 5")

    # ── Universe ticker count ──
    if data.get("universe_ticker_count") != 1074:
        errors.append(f"universe_ticker_count {data.get('universe_ticker_count')} != 1074")

    # ── Period count ──
    if data.get("period_count", 0) < 24:
        errors.append(f"period_count {data.get('period_count')} < 24")

    # ── Report-side checks ──
    add_missing(errors, report, "tushare:fina_indicator+income+cashflow+balancesheet", "provider in report")
    add_missing(errors, report, "## Coverage Summary", "coverage section")
    add_missing(errors, report, "## Per-Field Non-Null Distribution", "per-field section")
    add_missing(errors, report, "## Coverage Gates", "gates section")
    add_missing(errors, report, "## H50a ROE Overlap", "roe overlap section")
    add_missing(errors, report, "## Verdict", "verdict section")

    # Verify verdict matches gates
    gates_all_ok = all(gates.get(g, False) for g in (
        "ticker_coverage_ge_98pct", "hard_fields_ge_85pct",
        "soft_fields_ge_50pct", "intermediates_ge_85pct",
        "accruals_ratio_ge_50pct", "h50a_overlap_ge_99pct",
    ))
    expected_verdict = "CANDIDATE_DATASET" if gates_all_ok else "BLOCKED"
    actual_verdict = data.get("verdict", "")
    if actual_verdict != expected_verdict:
        errors.append(f"verdict {actual_verdict!r} != expected {expected_verdict!r}")

    return errors


def validate_h52e(data: dict, report: str) -> List[str]:
    """Validate H52e: all 3 sub-JSONs exist, verdict fields, sha256 audit, unified report."""
    errors: List[str] = []
    csi_sha = {}
    # Compute sha256 for CSI500 files
    import hashlib
    csi_files = [
        ("universe", ROOT / "data/cn_pit/universe_h52a_csi500.jsonl"),
        ("universe_snapshots", ROOT / "data/cn_pit/universe_snapshots_h52a_csi500.jsonl"),
        ("sector_metadata", ROOT / "data/cn_pit/sector_metadata_h52b_csi500.csv"),
        ("prices", ROOT / "data/cn_pit/prices_h52c_csi500_qfq.csv"),
        ("adtv_liquidity", ROOT / "data/cn_pit/liquidity_h52c_csi500_daily_amount.csv"),
        ("fundamentals", ROOT / "data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl"),
    ]
    for key, path in csi_files:
        if not path.exists():
            errors.append(f"CSI500 file missing: {path}")
            continue
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        csi_sha[key] = h.hexdigest()

    # 3 sub-JSON paths
    sub_runs = {
        "h42": ROOT / "backtest/runs/fundamental_value_h52e_csi500_smoke_h42.json",
        "h50b": ROOT / "backtest/runs/fundamental_value_h52e_csi500_smoke_h50b.json",
        "h51b": ROOT / "backtest/runs/fundamental_value_h52e_csi500_smoke_h51b.json",
    }
    for label, run_path in sub_runs.items():
        if not run_path.exists():
            errors.append(f"{label}: run JSON missing at {run_path}")
            continue
        try:
            run_data = load_json(run_path)
        except Exception as exc:
            errors.append(f"{label}: invalid JSON: {exc}")
            continue

        # verdict field
        verdict = run_data.get("verdict", "")
        if not verdict or not isinstance(verdict, str):
            errors.append(f"{label}: verdict missing or invalid: {verdict!r}")

        # provenance checks
        if label == "h42":
            inputs = run_data.get("inputs", {})
            if not str(inputs.get("prices_file", "")).endswith("prices_h52c_csi500_qfq.csv"):
                errors.append(f"{label}: inputs.prices_file does not reference CSI500 prices")
            if not str(inputs.get("universe_file", "")).endswith("universe_h52a_csi500.jsonl"):
                errors.append(f"{label}: inputs.universe_file does not reference CSI500 universe")
            if not str(inputs.get("snapshots_file", "")).endswith("universe_snapshots_h52a_csi500.jsonl"):
                errors.append(f"{label}: inputs.snapshots_file does not reference CSI500 snapshots")
        else:
            ds = run_data.get("data_sources", {})
            # Sha256-only checks (file field is hardcoded for some entries)
            sha_checks = [
                ("prices", "prices"),
                ("sector_metadata", "sector_metadata"),
                ("fundamentals", "fundamentals"),
                ("universe", "universe"),
            ]
            if label == "h50b":
                sha_checks.append(("universe_snapshots", "universe_snapshots"))
            elif label == "h51b":
                sha_checks.append(("adtv_liquidity", "adtv_liquidity"))

            for ds_key, sha_key in sha_checks:
                entry = ds.get(ds_key, {})
                if not entry:
                    errors.append(f"{label}: data_sources.{ds_key} missing")
                    continue
                actual_sha = entry.get("sha256", "")
                expected_sha = csi_sha.get(sha_key, "")
                if actual_sha != expected_sha:
                    errors.append(
                        f"{label}: data_sources.{ds_key}.sha256 mismatch "
                        f"(actual={actual_sha[:16]}..., expected={expected_sha[:16]}...)"
                    )

            # File basename checks — only for dynamically-derived fields
            dynamic_checks = {
                "prices": "prices_h52c_csi500_qfq.csv",
                "sector_metadata": "sector_metadata_h52b_csi500.csv",
                "universe": "universe_h52a_csi500.jsonl",
            }
            if label == "h50b":
                dynamic_checks["universe_snapshots"] = "universe_snapshots_h52a_csi500.jsonl"
            for ds_key, expected_basename in dynamic_checks.items():
                entry = ds.get(ds_key, {})
                actual_file = entry.get("file", "")
                if not actual_file.endswith(expected_basename):
                    errors.append(
                        f"{label}: data_sources.{ds_key}.file={actual_file!r} does not end with {expected_basename!r}"
                    )

    # unified report exists
    unified = ROOT / "reports/h52e_csi500_framework_smoke_report.md"
    if not unified.exists():
        errors.append(f"unified report missing at {unified}")
    else:
        report_text = read_text(unified)
        for sub in ["H42", "H50B", "H51B"]:
            if sub not in report_text:
                errors.append(f"unified report missing section: {sub}")

    return errors


def validate_h52g(data: dict, report: str) -> List[str]:
    """Validate H52g: diagnostic JSON schema, hypothesis checks, root-cause verdict, H42 subtrace."""
    errors: List[str] = []

    # ── Required top-level fields ──
    required_keys = [
        "task", "generated_at", "diagnostic_baseline", "locked_params",
        "h30", "csi500", "diff", "hypotheses", "root_cause_verdict",
        "h52h_fix_path", "h52f_interpretation", "h42_baseline_trace",
    ]
    for key in required_keys:
        if key not in data:
            errors.append(f"missing top-level key: {key}")

    # ── task field ──
    if data.get("task") != "H52g":
        errors.append(f"task {data.get('task')!r} != 'H52g'")

    # ── diagnostic_baseline ──
    if data.get("diagnostic_baseline") != "H50b":
        errors.append(f"diagnostic_baseline {data.get('diagnostic_baseline')!r} != 'H50b'")

    # ── locked_params ──
    lp = data.get("locked_params", {})
    expected_lp = {"top_n": 8, "max_position_pct": 0.08, "stop_loss_pct": 0.08,
                   "take_profit_pct": 0.25, "quality_filter": 0.40, "rebalance_freq_days": 63}
    for k, v in expected_lp.items():
        if lp.get(k) != v:
            errors.append(f"locked_params.{k} = {lp.get(k)!r} != {v!r}")

    # ── H30 baseline ──
    h30 = data.get("h30", {})
    if "can_deploy" not in h30:
        errors.append("h30.can_deploy missing")

    # ── CSI500 baseline ──
    csi = data.get("csi500", {})
    if "can_deploy" not in csi:
        errors.append("csi500.can_deploy missing")

    # ── diff ──
    diff = data.get("diff", {})
    if "first_divergence" not in diff:
        errors.append("diff.first_divergence missing")

    # ── hypotheses ──
    hypos = data.get("hypotheses", {})
    for h_name in ["H_A", "H_B", "H_C", "H_D", "H_E", "H_F"]:
        if h_name not in hypos:
            errors.append(f"hypotheses.{h_name} missing")
            continue
        h = hypos[h_name]
        if "verdict" not in h:
            errors.append(f"hypotheses.{h_name}.verdict missing")
        elif h["verdict"] not in ("PASS", "FAIL"):
            errors.append(f"hypotheses.{h_name}.verdict {h['verdict']!r} not in (PASS, FAIL)")
        if "findings" not in h:
            errors.append(f"hypotheses.{h_name}.findings missing")

    # ── root_cause_verdict ──
    rcv = data.get("root_cause_verdict", "")
    if rcv not in ("ROOT_CAUSE_IDENTIFIED", "MULTI_CAUSE", "UNKNOWN"):
        errors.append(f"root_cause_verdict {rcv!r} not in valid set")

    # ── H42 baseline trace ──
    h42t = data.get("h42_baseline_trace", {})
    if "executed" not in h42t:
        errors.append("h42_baseline_trace.executed missing")

    # ── Report-side checks ──
    add_missing(errors, report, "# H52g — CSI500 Zero-Candidate Diagnostic Report", "report title")
    add_missing(errors, report, "## H30 Baseline BacktestResult", "H30 section")
    add_missing(errors, report, "## CSI500 Baseline BacktestResult", "CSI500 section")
    add_missing(errors, report, "## First Divergence", "first divergence section")
    add_missing(errors, report, "## Hypothesis Checks", "hypothesis section")
    for h_name in ["H_A", "H_B", "H_C", "H_D", "H_E", "H_F"]:
        add_missing(errors, report, f"### {h_name}:", f"hypothesis {h_name} heading")
    add_missing(errors, report, "## Root-Cause Verdict", "root cause section")
    add_missing(errors, report, "## Optional H42-Baseline Sub-Trace", "H42 subtrace section")
    add_missing(errors, report, "## Interpretation for H52f Verdict", "H52f interpretation section")

    return errors


def validate_h52h(data: dict, report: str) -> List[str]:
    """Validate H52h: diagnostic JSON schema, fix action, sha256 diff, idempotency."""
    errors: List[str] = []

    # ── Required top-level fields ──
    required_keys = [
        "task", "generated_at", "fix_type",
        "prices", "liquidity", "coverage_update", "idempotent",
    ]
    for key in required_keys:
        if key not in data:
            errors.append(f"missing top-level key: {key}")

    # ── task field ──
    if data.get("task") != "H52h":
        errors.append(f"task {data.get('task')!r} != 'H52h'")

    # ── fix_type ──
    if data.get("fix_type") != "int64_to_iso_date_format":
        errors.append(f"fix_type {data.get('fix_type')!r} != 'int64_to_iso_date_format'")

    # ── prices section ──
    prices = data.get("prices", {})
    for field in ["file", "format_before", "action", "sha256_before", "sha256_after"]:
        if field not in prices:
            errors.append(f"prices.{field} missing")
    if prices.get("format_before") not in ("int", "iso"):
        errors.append(f"prices.format_before {prices.get('format_before')!r} not in ('int', 'iso')")
    if prices.get("action") not in ("converted", "already_iso", "simulated", "skip"):
        errors.append(f"prices.action {prices.get('action')!r} not in valid set")
    if prices.get("sha256_before") == prices.get("sha256_after") and prices.get("action") == "converted":
        errors.append("prices sha256 unchanged despite 'converted' action")

    # ── liquidity section ──
    liquidity = data.get("liquidity", {})
    for field in ["file", "format_before", "action", "sha256_before", "sha256_after"]:
        if field not in liquidity:
            errors.append(f"liquidity.{field} missing")
    if liquidity.get("format_before") not in ("int", "iso"):
        errors.append(f"liquidity.format_before {liquidity.get('format_before')!r} not in ('int', 'iso')")
    if liquidity.get("action") not in ("converted", "already_iso", "simulated", "skip"):
        errors.append(f"liquidity.action {liquidity.get('action')!r} not in valid set")

    # ── coverage_update ──
    cu = data.get("coverage_update", {})
    for field in ["prices_sha_before", "prices_sha_after", "liquidity_sha_before", "liquidity_sha_after", "updated"]:
        if field not in cu:
            errors.append(f"coverage_update.{field} missing")
    if prices.get("action") == "converted" and cu.get("updated") is not True:
        errors.append("coverage_update.updated should be True after conversion")

    # ── idempotent ──
    if not isinstance(data.get("idempotent"), bool):
        errors.append(f"idempotent {data.get('idempotent')!r} not a bool")

    # ── Report-side checks ──
    add_missing(errors, report, "# H52h — H52c Date Format Fix Report", "report title")
    add_missing(errors, report, "## Phase 1: Date Format Fix", "phase 1 section")
    add_missing(errors, report, "### Prices CSV", "prices section")
    add_missing(errors, report, "### Liquidity CSV", "liquidity section")
    add_missing(errors, report, "### Coverage JSON Update", "coverage section")
    add_missing(errors, report, "Sha256 before", "sha256 before")
    add_missing(errors, report, "Sha256 after", "sha256 after")

    return errors


def validate_h52f(data: dict, report: str) -> List[str]:
    """Validate H52f: all 4 sub-JSONs exist, verdict fields, sha256 audit, master report.
    Specific H52e gap closer: h51b sub-JSON's adtv_liquidity.sha256 == file_sha256(CSI500_ADTV)."""
    errors: List[str] = []
    import hashlib

    # Compute sha256 for CSI500 files
    csi_sha = {}
    csi_files = [
        ("universe", ROOT / "data/cn_pit/universe_h52a_csi500.jsonl"),
        ("universe_snapshots", ROOT / "data/cn_pit/universe_snapshots_h52a_csi500.jsonl"),
        ("sector_metadata", ROOT / "data/cn_pit/sector_metadata_h52b_csi500.csv"),
        ("prices", ROOT / "data/cn_pit/prices_h52c_csi500_qfq.csv"),
        ("adtv_liquidity", ROOT / "data/cn_pit/liquidity_h52c_csi500_daily_amount.csv"),
        ("fundamentals", ROOT / "data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl"),
    ]
    for key, path in csi_files:
        if not path.exists():
            errors.append(f"CSI500 file missing: {path}")
            continue
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        csi_sha[key] = h.hexdigest()

    # 4 sub-JSON paths
    sub_runs = {
        "h42": ROOT / "backtest/runs/fundamental_value_h52f_csi500_h42.json",
        "h49b": ROOT / "backtest/runs/fundamental_value_h52f_csi500_h49b.json",
        "h50b": ROOT / "backtest/runs/fundamental_value_h52f_csi500_h50b.json",
        "h51b": ROOT / "backtest/runs/fundamental_value_h52f_csi500_h51b.json",
    }
    for label, run_path in sub_runs.items():
        if not run_path.exists():
            errors.append(f"{label}: run JSON missing at {run_path}")
            continue
        try:
            run_data = load_json(run_path)
        except Exception as exc:
            errors.append(f"{label}: invalid JSON: {exc}")
            continue

        # verdict field
        verdict = run_data.get("verdict", "")
        if not verdict or not isinstance(verdict, str):
            errors.append(f"{label}: verdict missing or invalid: {verdict!r}")

        # provenance checks
        if label == "h42":
            inputs = run_data.get("inputs", {})
            if not str(inputs.get("prices_file", "")).endswith("prices_h52c_csi500_qfq.csv"):
                errors.append(f"{label}: inputs.prices_file does not reference CSI500 prices")
            if not str(inputs.get("universe_file", "")).endswith("universe_h52a_csi500.jsonl"):
                errors.append(f"{label}: inputs.universe_file does not reference CSI500 universe")
            if not str(inputs.get("snapshots_file", "")).endswith("universe_snapshots_h52a_csi500.jsonl"):
                errors.append(f"{label}: inputs.snapshots_file does not reference CSI500 snapshots")
        else:
            ds = run_data.get("data_sources", {})
            # Sha256 checks — only for keys that exist in this sub's provenance
            sha_checks = [
                ("prices", "prices"),
                ("sector_metadata", "sector_metadata"),
                ("universe", "universe"),
            ]
            if label in ("h49b", "h50b", "h51b"):
                # fundamentals present in h50b/h51b; optional in h49b
                if "fundamentals" in ds:
                    sha_checks.append(("fundamentals", "fundamentals"))
            if label == "h50b":
                sha_checks.append(("universe_snapshots", "universe_snapshots"))
            elif label == "h51b":
                sha_checks.append(("adtv_liquidity", "adtv_liquidity"))

            for ds_key, sha_key in sha_checks:
                entry = ds.get(ds_key, {})
                if not entry:
                    errors.append(f"{label}: data_sources.{ds_key} missing")
                    continue
                actual_sha = entry.get("sha256", "")
                expected_sha = csi_sha.get(sha_key, "")
                if actual_sha != expected_sha:
                    errors.append(
                        f"{label}: data_sources.{ds_key}.sha256 mismatch "
                        f"(actual={actual_sha[:16]}..., expected={expected_sha[:16]}...)"
                    )

            # File basename checks — only for dynamically-derived fields
            dynamic_checks = {
                "prices": "prices_h52c_csi500_qfq.csv",
                "sector_metadata": "sector_metadata_h52b_csi500.csv",
                "universe": "universe_h52a_csi500.jsonl",
            }
            if label == "h50b":
                dynamic_checks["universe_snapshots"] = "universe_snapshots_h52a_csi500.jsonl"
            # NOTE: fundamentals + adtv_liquidity basenames hardcoded — not checked
            for ds_key, expected_basename in dynamic_checks.items():
                entry = ds.get(ds_key, {})
                actual_file = entry.get("file", "")
                if not actual_file.endswith(expected_basename):
                    errors.append(
                        f"{label}: data_sources.{ds_key}.file={actual_file!r} "
                        f"does not end with {expected_basename!r}"
                    )

            # H52e gap closer: h51b MUST have valid adtv_liquidity sha256
            if label == "h51b":
                adtv_entry = ds.get("adtv_liquidity", {})
                if adtv_entry:
                    actual_adtv = adtv_entry.get("sha256", "")
                    expected_adtv = csi_sha.get("adtv_liquidity", "")
                    if actual_adtv != expected_adtv:
                        errors.append(
                            f"{label}: H52e GAP CLOSER FAILED — adtv_liquidity.sha256 mismatch "
                            f"(got {actual_adtv[:16]}..., expected {expected_adtv[:16]}...)"
                        )
                else:
                    errors.append(f"{label}: data_sources.adtv_liquidity entry missing")

    # master report exists
    master = ROOT / "reports/h52f_csi500_full_pipeline_master_report.md"
    if not master.exists():
        errors.append(f"master report missing at {master}")
    else:
        report_text = read_text(master)
        for required in ["H30 vs CSI500 Comparison", "Aggregate H52f Verdict", "Next-Step"]:
            if required not in report_text:
                errors.append(f"master report missing section: {required}")
        for sub in ["h42", "h49b", "h50b", "h51b"]:
            if sub not in report_text:
                errors.append(f"master report missing sub: {sub}")

    return errors


def artifact_specs(root: Path = ROOT) -> Dict[str, ArtifactSpec]:
    return {
        "h39": ArtifactSpec(
            "h39",
            root / "backtest/runs/fundamental_value_h39_unblock_search.json",
            root / "reports/h39_shadow_unblock_search_report.md",
            validate_h39,
        ),
        "h40": ArtifactSpec(
            "h40",
            root / "backtest/runs/fundamental_value_h40_h39_candidate_audit.json",
            root / "reports/h40_h39_candidate_audit_report.md",
            validate_h40,
        ),
        "h41": ArtifactSpec(
            "h41",
            root / "backtest/runs/fundamental_value_h41_candidate_robustness_sweep.json",
            root / "reports/h41_candidate_robustness_sweep_report.md",
            validate_h41,
        ),
        "h42": ArtifactSpec(
            "h42",
            root / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json",
            root / "reports/h42_strategy_redesign_search_report.md",
            validate_h42,
        ),
        "h46": ArtifactSpec(
            "h46",
            root / "backtest/runs/fundamental_value_h46_paper_forward_monitor.json",
            root / "reports/h46_paper_forward_monitor_report.md",
            validate_h46,
        ),
        "h47": ArtifactSpec(
            "h47",
            root / "data/cn_pit/price_coverage_h47.json",
            root / "reports/h47_tushare_qfq_price_rebuild_report.md",
            validate_h47,
        ),
        "h48": ArtifactSpec(
            "h48",
            root / "backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json",
            root / "reports/h48_unified_qfq_h42_rerun_report.md",
            validate_h48,
        ),
        "h49a": ArtifactSpec(
            "h49a",
            root / "data/cn_pit/sector_coverage_h49a.json",
            root / "reports/h49a_sw_industry_ingestion_report.md",
            validate_h49a,
        ),
        "h49b": ArtifactSpec(
            "h49b",
            root / "backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json",
            root / "reports/h49b_sector_neutral_rs_search_report.md",
            validate_h49b,
        ),
        "h50a": ArtifactSpec(
            "h50a",
            root / "data/cn_pit/fundamentals_coverage_h50a.json",
            root / "reports/h50a_pit_quality_ingestion_report.md",
            validate_h50a,
        ),
        "h50b": ArtifactSpec(
            "h50b",
            root / "backtest/runs/fundamental_value_h50b_quality_value_search.json",
            root / "reports/h50b_quality_value_search_report.md",
            validate_h50b,
        ),
        "h51a": ArtifactSpec(
            "h51a",
            root / "data/cn_pit/liquidity_coverage_h51a.json",
            root / "reports/h51a_daily_amount_ingestion_report.md",
            validate_h51a,
        ),
        "h51b": ArtifactSpec(
            "h51b",
            root / "backtest/runs/fundamental_value_h51b_risk_model_search.json",
            root / "reports/h51b_risk_model_search_report.md",
            validate_h51b,
        ),
        "h52a": ArtifactSpec(
            "h52a",
            root / "data/cn_pit/universe_coverage_h52a.json",
            root / "reports/h52a_csi500_universe_report.md",
            validate_h52a,
        ),
        "h52b": ArtifactSpec(
            "h52b",
            root / "data/cn_pit/sector_coverage_h52b.json",
            root / "reports/h52b_csi500_sw_industry_ingestion_report.md",
            validate_h52b,
        ),
        "h52c": ArtifactSpec(
            "h52c",
            root / "data/cn_pit/price_coverage_h52c.json",
            root / "reports/h52c_csi500_daily_facts_ingestion_report.md",
            validate_h52c,
        ),
        "h52d": ArtifactSpec(
            "h52d",
            root / "data/cn_pit/fundamentals_coverage_h52d.json",
            root / "reports/h52d_csi500_pit_quality_ingestion_report.md",
            validate_h52d,
        ),
        "h52e": ArtifactSpec(
            "h52e",
            root / "backtest/runs/fundamental_value_h52e_csi500_smoke_h42.json",
            root / "reports/h52e_csi500_framework_smoke_report.md",
            validate_h52e,
        ),
        "h52f": ArtifactSpec(
            "h52f",
            root / "backtest/runs/fundamental_value_h52f_csi500_h42.json",
            root / "reports/h52f_csi500_full_pipeline_master_report.md",
            validate_h52f,
        ),
        "h52g": ArtifactSpec(
            "h52g",
            root / "data/cn_pit/h52g_diagnostic.json",
            root / "reports/h52g_csi500_zero_candidate_diagnostic_report.md",
            validate_h52g,
        ),
        "h52h": ArtifactSpec(
            "h52h",
            root / "data/cn_pit/h52h_fix_diagnostic.json",
            root / "reports/h52h_csi500_date_fix_report.md",
            validate_h52h,
        ),
    }


def validate_spec(spec: ArtifactSpec) -> ArtifactCheck:
    missing = [str(path) for path in [spec.json_path, spec.report_path] if not path.exists()]
    if missing:
        return ArtifactCheck(spec.name, False, "missing files: " + ", ".join(missing))
    try:
        data = load_json(spec.json_path)
    except Exception as exc:  # noqa: BLE001
        return ArtifactCheck(spec.name, False, f"invalid json: {exc}")
    report = read_text(spec.report_path)
    errors = spec.validator(data, report)
    return ArtifactCheck(spec.name, not errors, "ok" if not errors else "; ".join(errors))


def run_checks(selected: Optional[str] = None) -> List[ArtifactCheck]:
    specs = artifact_specs()
    if selected:
        if selected not in specs:
            names = ", ".join(sorted(specs))
            raise ValueError(f"unknown artifact {selected!r}; choose one of {names}")
        specs = {selected: specs[selected]}
    return [validate_spec(spec) for spec in specs.values()]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate H39-H42/H46-H48/H49a JSON/report consistency")
    parser.add_argument("--artifact", choices=sorted(artifact_specs()), help="Validate one artifact family")
    args = parser.parse_args(argv)

    checks = run_checks(args.artifact)
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")
    failed = [check for check in checks if not check.passed]
    print(f"\nResult: {len(checks) - len(failed)}/{len(checks)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
