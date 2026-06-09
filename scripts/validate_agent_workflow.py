#!/usr/bin/env python3
"""Validate H43 agent-workflow guardrails and recent H42 artifacts."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_required_files() -> Check:
    files = [
        "AGENTS.md",
        "CLAUDE.md",
        "docs/hermes-h43-workflow-repair-plan.md",
        "docs/hermes-h42-strategy-redesign-search-task.md",
        "docs/hermes-h44-artifact-consistency-validator-task.md",
        "docs/hermes-h45-next-alpha-prd.md",
        "docs/hermes-h46-paper-forward-monitor-task.md",
        "docs/hermes-h47-production-price-rebuild-task.md",
        "docs/agents/workflow.md",
        "docs/agents/issue-tracker.md",
        "docs/agents/triage-labels.md",
        "docs/agents/domain.md",
        "docs/agents/hxx-task-template.md",
        "docs/agents/review-prompt-template.md",
        "docs/agents/next-slices.md",
        "scripts/validate_agent_workflow.py",
        "scripts/validate_hxx_artifacts.py",
        "tests/test_validate_agent_workflow.py",
        "tests/test_validate_hxx_artifacts.py",
        "scripts/h46_paper_forward_monitor.py",
        "tests/test_h46_paper_forward_monitor.py",
        "scripts/h47_build_tushare_qfq_prices.py",
        "tests/test_h47_build_tushare_qfq_prices.py",
        "scripts/h42_strategy_redesign_search.py",
        "tests/test_h42_strategy_redesign_search.py",
        "backtest/runs/fundamental_value_h42_strategy_redesign_search.json",
        "reports/h42_strategy_redesign_search_report.md",
    ]
    missing = [f for f in files if not (ROOT / f).exists()]
    return Check("WF-1", not missing, "missing: " + ", ".join(missing) if missing else "all required files present")


def check_template_sections() -> Check:
    text = read(ROOT / "docs/agents/hxx-task-template.md")
    required = [
        "## Context",
        "## Objective",
        "## Inputs",
        "## Outputs",
        "## Hard Prohibitions",
        "## Smoke Command",
        "## Full Command",
        "## Verification",
        "## Acceptance Gate",
        "## Review Prompt",
        "## Closure Note",
    ]
    missing = [section for section in required if section not in text]
    return Check("WF-2", not missing, "missing sections: " + ", ".join(missing) if missing else "task template complete")


def check_h42_json_report_consistency() -> Check:
    data = json.loads(read(ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json"))
    report = read(ROOT / "reports/h42_strategy_redesign_search_report.md")
    expectations = {
        "verdict": f"**Verdict:** {data['verdict']}",
        "stage_a": f"Stage A (overlay screening): {data['stage_a_count']} overlays",
        "stage_b": f"Stage B (param grid): {data['stage_b_count']} runs",
        "seed": f"Sanity seeds: {data.get('seed_count', 0)} known candidates",
        "clean": f"Clean deploy-window candidates: {data['clean_deploy_count']}",
        "stage_c": f"Stage C (multi-window): {data['stage_c_count']} candidates",
        "gate": f"Gate passed: {data['gate_pass_count']} candidates",
    }
    missing = [name for name, snippet in expectations.items() if snippet not in report]
    return Check("WF-3", not missing, "missing report snippets: " + ", ".join(missing) if missing else "H42 JSON/report counts match")


def check_no_open_sync_items() -> Check:
    text = read(ROOT / "docs/strategy-optimization-sync.md")
    open_rows = [
        line for line in text.splitlines()
        if re.match(r"^\| S\d+ \|", line) and "| OPEN |" in line
    ]
    return Check("WF-4", not open_rows, "open rows: " + "; ".join(open_rows) if open_rows else "no OPEN tracked items")


def check_no_h42_blocker_label_pollution() -> Check:
    paths = [
        ROOT / "scripts/h42_strategy_redesign_search.py",
        ROOT / "backtest/runs/fundamental_value_h42_strategy_redesign_search.json",
        ROOT / "reports/h42_strategy_redesign_search_report.md",
    ]
    bad_patterns = [
        "data_quality:insufficient",
        "data_quality:negative",
        "data_quality:price_coverage",
        "data_quality:research_only",
        "data_quality:data_quality",
    ]
    hits: List[str] = []
    for path in paths:
        text = read(path)
        for pattern in bad_patterns:
            if pattern in text:
                hits.append(f"{path.relative_to(ROOT)}:{pattern}")
    return Check("WF-5", not hits, "hits: " + ", ".join(hits) if hits else "H42 blocker labels clean")


def check_h42_tests_cover_runtime_path() -> Check:
    text = read(ROOT / "tests/test_h42_strategy_redesign_search.py")
    required = [
        "FeatureCache",
        "passes_overlay",
        "compute_acceptance_gate",
        "evaluate_candidate_multi_window",
    ]
    missing = [item for item in required if item not in text]
    return Check("WF-6", not missing, "missing test coverage markers: " + ", ".join(missing) if missing else "H42 runtime helpers covered")


def check_h45_prd_sections() -> Check:
    text = read(ROOT / "docs/hermes-h45-next-alpha-prd.md")
    required = [
        "## Problem Statement",
        "## Solution",
        "## User Stories",
        "## Data Requirements",
        "## Experiment Design",
        "## Deployment Gates",
        "## Out of Scope",
        "parameter-only tuning",
        "HS300 excess return",
        "point-in-time",
        "Universe:",
        "Fundamentals:",
        "Prices:",
        "Benchmark:",
        "Sector classification:",
        "Liquidity:",
        "Audit metadata:",
        "Development window:",
        "Validation windows:",
        "Test window:",
        "Data-quality gate passes",
        "Price-source gate passes",
        "Execution gate passes",
        "Multi-window robustness passes",
        "Benchmark robustness passes",
        "JSON/report consistency validator passes",
    ]
    missing = [item for item in required if item not in text]
    story_count = text.count("As the strategy owner")
    if story_count < 15:
        missing.append(f"user stories >= 15 (found {story_count})")
    return Check("WF-7", not missing, "missing PRD markers: " + ", ".join(missing) if missing else "H45 PRD covers alpha gates")


def run_checks() -> Iterable[Check]:
    return [
        check_required_files(),
        check_template_sections(),
        check_h42_json_report_consistency(),
        check_no_open_sync_items(),
        check_no_h42_blocker_label_pollution(),
        check_h42_tests_cover_runtime_path(),
        check_h45_prd_sections(),
    ]


def main() -> int:
    checks = list(run_checks())
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")
    failed = [check for check in checks if not check.passed]
    print(f"\nResult: {len(checks) - len(failed)}/{len(checks)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
