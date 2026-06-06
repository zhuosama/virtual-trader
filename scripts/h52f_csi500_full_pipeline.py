#!/usr/bin/env python3
"""
H52f — CSI500 H42→H51b Full Pipeline Rerun

Runs 4 sub-pipelines (H42 / H49b / H50b / H51b; H48 skipped as redundant)
at PRODUCTION search params against CSI500 data (H52a-d), producing real
verdicts + master comparison report vs H30 baseline.

Usage:
    python scripts/h52f_csi500_full_pipeline.py              # full run
    python scripts/h52f_csi500_full_pipeline.py --force      # re-run all subs
    python scripts/h52f_csi500_full_pipeline.py --dry-run --sub h42 --output-dir /tmp/h52f_smoke
    python scripts/h52f_csi500_full_pipeline.py --dry-run --sub h51b --output-dir /tmp/h52f_smoke

╔══════════════════════════════════════════════════════════════════════════════╗
║  DISCOVERED CLI SURFACE (re-grepped at build time 2026-05-24):              ║
║                                                                            ║
║  Script | Path CLI args                              | Monkey-patch needed ║
║  ───────┼────────────────────────────────────────────┼───────────────────── ║
║  H42    │ --prices-file, --universe-file,            │ NONE                ║
║         │ --snapshots-file                            │                     ║
║  ───────┼────────────────────────────────────────────┼───────────────────── ║
║  H49b   │ --prices-file, --universe-file,            │ SECTOR_CSV          ║
║         │ --snapshots-file (lines 1417-1419)          │ (NO INPUT_SHA256;   ║
║         │                                            │ computed at runtime) ║
║  ───────┼────────────────────────────────────────────┼───────────────────── ║
║  H50b   │ NONE for input paths                       │ H47_PRICES,         ║
║         │ (--stage-a/b-limit, --top-k, --capital,    │ H30_UNIVERSE,       ║
║         │  --output-run, --output-report,             │ H30_SNAPSHOTS,      ║
║         │  --top-overlays)                            │ SECTOR_CSV,         ║
║         │                                            │ H50A_JSONL,         ║
║         │                                            │ INPUT_SHA256        ║
║  ───────┼────────────────────────────────────────────┼───────────────────── ║
║  H51b   │ NONE for input paths                       │ H47_PRICES,         ║
║         │ (--stage-b-limit, --capital,               │ H30_UNIVERSE,       ║
║         │  --output-run, --output-report)             │ H30_SNAPSHOTS,      ║
║         │                                            │ SECTOR_CSV,         ║
║         │                                            │ H50A_JSONL,         ║
║         │                                            │ H51A_ADTV, ← FIXED  ║
║         │                                            │ H51B_INPUT_SHA256   ║
╚══════════════════════════════════════════════════════════════════════════════╝

Defensive rule: if a path is CLI-exposed, USE CLI (NOT monkey-patch).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

# ── Project paths ───────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_ROOT))  # for h51b's sys.modules lookup

# ── CSI500 data files ───────────────────────────────────────────────────
CSI500_PRICES = PROJECT_ROOT / "data/cn_pit/prices_h52c_csi500_qfq.csv"
CSI500_UNIVERSE = PROJECT_ROOT / "data/cn_pit/universe_h52a_csi500.jsonl"
CSI500_SNAPSHOTS = PROJECT_ROOT / "data/cn_pit/universe_snapshots_h52a_csi500.jsonl"
CSI500_SECTOR = PROJECT_ROOT / "data/cn_pit/sector_metadata_h52b_csi500.csv"
CSI500_FUNDAMENTALS = PROJECT_ROOT / "data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl"
CSI500_ADTV = PROJECT_ROOT / "data/cn_pit/liquidity_h52c_csi500_daily_amount.csv"

CSI500_FILE_MAP = {
    "prices": CSI500_PRICES,
    "universe": CSI500_UNIVERSE,
    "universe_snapshots": CSI500_SNAPSHOTS,
    "sector_metadata": CSI500_SECTOR,
    "fundamentals": CSI500_FUNDAMENTALS,
    "adtv_liquidity": CSI500_ADTV,
}

# ── Default output paths ─────────────────────────────────────────────────
OUT_DIR = PROJECT_ROOT / "backtest" / "runs"
REPORT_DIR = PROJECT_ROOT / "reports"

H52F_H42_JSON = OUT_DIR / "fundamental_value_h52f_csi500_h42.json"
H52F_H42_RPT = REPORT_DIR / "h52f_csi500_h42_report.md"
H52F_H49B_JSON = OUT_DIR / "fundamental_value_h52f_csi500_h49b.json"
H52F_H49B_RPT = REPORT_DIR / "h52f_csi500_h49b_report.md"
H52F_H50B_JSON = OUT_DIR / "fundamental_value_h52f_csi500_h50b.json"
H52F_H50B_RPT = REPORT_DIR / "h52f_csi500_h50b_report.md"
H52F_H51B_JSON = OUT_DIR / "fundamental_value_h52f_csi500_h51b.json"
H52F_H51B_RPT = REPORT_DIR / "h52f_csi500_h51b_report.md"
MASTER_REPORT = REPORT_DIR / "h52f_csi500_full_pipeline_master_report.md"

# ── Protected library files (must not be modified) ──────────────────────
PROTECTED_LIBS = [
    SCRIPTS_DIR / "h42_strategy_redesign_search.py",
    SCRIPTS_DIR / "h49b_sector_neutral_rs_search.py",
    SCRIPTS_DIR / "h50b_quality_value_search.py",
    SCRIPTS_DIR / "h51b_risk_model_search.py",
    SCRIPTS_DIR / "h52e_csi500_framework_smoke.py",
    PROJECT_ROOT / "backtest" / "experiments" / "fundamental_backtest.py",
]

# ── Protected data + run files (must not be modified) ────────────────────
PROTECTED_PATHS_25 = [
    "scripts/h42_strategy_redesign_search.py",
    "scripts/h50b_quality_value_search.py",
    "scripts/h51b_risk_model_search.py",
    "scripts/h52e_csi500_framework_smoke.py",
    "backtest/experiments/fundamental_backtest.py",
    "data/cn_pit/universe.jsonl",
    "data/cn_pit/universe_snapshots.jsonl",
    "data/cn_pit/fundamentals.jsonl",
    "data/cn_pit/universe_h30_candidate.jsonl",
    "data/cn_pit/universe_snapshots_h30_candidate.jsonl",
    "data/cn_pit/prices_h47_tushare_qfq_candidate.csv",
    "data/cn_pit/sector_metadata_sw_l1.csv",
    "data/cn_pit/fundamentals_h50a_pit_quality.jsonl",
    "data/cn_pit/liquidity_h51a_daily_amount.csv",
    "data/cn_pit/universe_h52a_csi500.jsonl",
    "data/cn_pit/universe_snapshots_h52a_csi500.jsonl",
    "data/cn_pit/sector_metadata_h52b_csi500.csv",
    "data/cn_pit/prices_h52c_csi500_qfq.csv",
    "data/cn_pit/liquidity_h52c_csi500_daily_amount.csv",
    "data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl",
    "backtest/runs/fundamental_value_h42_strategy_redesign_search.json",
    "backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json",
    "backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json",
    "backtest/runs/fundamental_value_h50b_quality_value_search.json",
    "backtest/runs/fundamental_value_h51b_risk_model_search.json",
]


# ═══════════════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════════════

def file_sha256(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def csi500_sha256_dict() -> Dict[str, str]:
    """Recompute sha256 dict for CSI500 files."""
    return {key: file_sha256(path) for key, path in CSI500_FILE_MAP.items()}


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def json_safe(obj):
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj


def load_h30_baseline(sub: str) -> dict:
    """Load H30 baseline run JSON for comparison."""
    h30_map = {
        "h42": "fundamental_value_h42_strategy_redesign_search.json",
        "h49b": "fundamental_value_h49b_sector_neutral_rs_search.json",
        "h50b": "fundamental_value_h50b_quality_value_search.json",
        "h51b": "fundamental_value_h51b_risk_model_search.json",
    }
    return load_json(OUT_DIR / h30_map[sub])


def extract_metrics(run_json: dict) -> dict:
    """Extract comparison metrics from a run JSON (H30 or CSI500)."""
    candidates = run_json.get("top_candidates_multi_window", [])
    gate_pass = run_json.get("gate_pass_count", 0)
    verdict = run_json.get("verdict", "UNKNOWN")

    beat_vals = []
    deploy_excesses = []
    for c in candidates:
        gm = c.get("gate_metrics", {})
        bw = gm.get("beat_hs300_windows", gm.get("beat_HS300_windows"))
        de = gm.get("deploy_excess_return")
        if bw is not None:
            beat_vals.append(bw)
        if de is not None:
            deploy_excesses.append(de)

    return {
        "verdict": verdict,
        "gate_pass_count": gate_pass,
        "max_beat_HS300": max(beat_vals) if beat_vals else None,
        "max_deploy_excess": max(deploy_excesses) if deploy_excesses else None,
        "candidate_count": len(candidates),
        "stage_a_count": run_json.get("stage_a_count"),
        "stage_b_count": run_json.get("stage_b_count"),
        "stage_c_count": run_json.get("stage_c_count"),
        "clean_deploy_count": run_json.get("clean_deploy_count"),
    }


def sha256_matches_csi500(run_json: dict, csi_sha: dict) -> bool:
    """Check if a run JSON's provenance sha256s match current CSI500 files."""
    ds = run_json.get("data_sources", {})
    for ds_key, sha_key in [
        ("prices", "prices"),
        ("sector_metadata", "sector_metadata"),
        ("fundamentals", "fundamentals"),
        ("universe", "universe"),
    ]:
        entry = ds.get(ds_key, {})
        actual = entry.get("sha256", "")
        expected = csi_sha.get(sha_key, "")
        if actual != expected:
            return False
    # Also check universe_snapshots if present
    us = ds.get("universe_snapshots", {})
    if us.get("sha256", "") != csi_sha.get("universe_snapshots", ""):
        return False
    # For h51b: check adtv_liquidity
    adtv = ds.get("adtv_liquidity", {})
    if adtv and adtv.get("sha256", "") != csi_sha.get("adtv_liquidity", ""):
        return False
    return True


def sub_run_skippable(run_path: Path, csi_sha: dict) -> bool:
    """Check if an existing sub-run JSON can be skipped (resumability).
    Handles both data_sources-based provenance (H49b/H50b/H51b) and
    inputs-based provenance (H42)."""
    if not run_path.exists():
        return False
    try:
        data = load_json(run_path)
        if not data.get("verdict"):
            return False  # Incomplete run
        # H42 uses inputs-based provenance; others use data_sources
        if "data_sources" in data:
            return sha256_matches_csi500(data, csi_sha)
        # H42: verify inputs reference CSI500 files
        inputs = data.get("inputs", {})
        return (
            str(inputs.get("prices_file", "")).endswith("prices_h52c_csi500_qfq.csv")
            and str(inputs.get("universe_file", "")).endswith("universe_h52a_csi500.jsonl")
        )
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Audit Hooks (D4)
# ═══════════════════════════════════════════════════════════════════════════

def audit_h42(run_path: Path) -> List[str]:
    """Post-run audit for H42: verify inputs.*_file paths end with CSI500 basenames."""
    errors = []
    data = load_json(run_path)
    inputs = data.get("inputs", {})

    checks = {
        "prices_file": "prices_h52c_csi500_qfq.csv",
        "universe_file": "universe_h52a_csi500.jsonl",
        "snapshots_file": "universe_snapshots_h52a_csi500.jsonl",
    }
    for field, expected_basename in checks.items():
        actual = str(inputs.get(field, ""))
        if not actual.endswith(expected_basename):
            errors.append(
                f"H42 audit: inputs.{field} = {actual!r} does NOT end with {expected_basename!r}"
            )
    # Verdict check
    if not data.get("verdict"):
        errors.append("H42 audit: missing 'verdict' field")
    # Candidate count NOT checked — 0 candidates is a valid production result
    # (e.g., all blocked by data quality gates)
    return errors


def audit_h49b(run_path: Path) -> List[str]:
    """Post-run audit for H49b: verify data_sources sha256s match CSI500 files."""
    errors = []
    data = load_json(run_path)
    ds = data.get("data_sources", {})
    csi_sha = csi500_sha256_dict()

    sha_checks = {
        "prices": "prices",
        "sector_metadata": "sector_metadata",
        "universe": "universe",
    }
    for ds_key, sha_key in sha_checks.items():
        entry = ds.get(ds_key, {})
        actual = entry.get("sha256", "")
        expected = csi_sha.get(sha_key, "")
        if actual != expected:
            errors.append(
                f"H49b audit: data_sources.{ds_key}.sha256 mismatch "
                f"(got {actual[:16]}..., expected {expected[:16]}...)"
            )

    # File basename checks
    basename_checks = {
        "prices": "prices_h52c_csi500_qfq.csv",
        "sector_metadata": "sector_metadata_h52b_csi500.csv",
        "universe": "universe_h52a_csi500.jsonl",
    }
    for ds_key, expected_bn in basename_checks.items():
        actual_file = ds.get(ds_key, {}).get("file", "")
        if actual_file and not actual_file.endswith(expected_bn):
            errors.append(
                f"H49b audit: data_sources.{ds_key}.file = {actual_file!r} does not end with {expected_bn!r}"
            )

    if not data.get("verdict"):
        errors.append("H49b audit: missing 'verdict' field")
    # Candidate count NOT checked — 0 candidates is valid
    return errors


def audit_h50b(run_path: Path) -> List[str]:
    """Post-run audit for H50b: verify data_sources sha256s match CSI500 files."""
    errors = []
    data = load_json(run_path)
    ds = data.get("data_sources", {})
    csi_sha = csi500_sha256_dict()

    sha_checks = {
        "prices": "prices",
        "sector_metadata": "sector_metadata",
        "fundamentals": "fundamentals",
        "universe": "universe",
        "universe_snapshots": "universe_snapshots",
    }
    for ds_key, sha_key in sha_checks.items():
        entry = ds.get(ds_key, {})
        actual = entry.get("sha256", "")
        expected = csi_sha.get(sha_key, "")
        if actual != expected:
            errors.append(
                f"H50b audit: data_sources.{ds_key}.sha256 mismatch "
                f"(got {actual[:16]}..., expected {expected[:16]}...)"
            )

    basename_checks = {
        "prices": "prices_h52c_csi500_qfq.csv",
        "sector_metadata": "sector_metadata_h52b_csi500.csv",
        "universe": "universe_h52a_csi500.jsonl",
        "universe_snapshots": "universe_snapshots_h52a_csi500.jsonl",
    }
    # NOTE: fundamentals basename NOT checked — H50b hardcodes it as literal
    for ds_key, expected_bn in basename_checks.items():
        entry = ds.get(ds_key, {})
        actual_file = entry.get("file", "")
        if actual_file and not actual_file.endswith(expected_bn):
            errors.append(
                f"H50b audit: data_sources.{ds_key}.file = {actual_file!r} does not end with {expected_bn!r}"
            )

    if not data.get("verdict"):
        errors.append("H50b audit: missing 'verdict' field")
    # Candidate count NOT checked — 0 candidates is valid
    return errors


def audit_h51b(run_path: Path) -> List[str]:
    """Post-run audit for H51b: verify data_sources sha256s match CSI500 files.
    SPECIFICALLY verifies adtv_liquidity.sha256 == file_sha256(CSI500_ADTV) — H52e gap closer."""
    errors = []
    data = load_json(run_path)
    ds = data.get("data_sources", {})
    csi_sha = csi500_sha256_dict()

    sha_checks = {
        "prices": "prices",
        "sector_metadata": "sector_metadata",
        "fundamentals": "fundamentals",
        "adtv_liquidity": "adtv_liquidity",
        "universe": "universe",
    }
    for ds_key, sha_key in sha_checks.items():
        entry = ds.get(ds_key, {})
        actual = entry.get("sha256", "")
        expected = csi_sha.get(sha_key, "")
        if actual != expected:
            errors.append(
                f"H51b audit: data_sources.{ds_key}.sha256 mismatch "
                f"(got {actual[:16]}..., expected {expected[:16]}...)"
            )

    # H52e gap closer: explicitly verify ADTV sha256
    adtv_entry = ds.get("adtv_liquidity", {})
    adtv_actual = adtv_entry.get("sha256", "")
    adtv_expected = csi_sha.get("adtv_liquidity", "")
    if adtv_actual != adtv_expected:
        errors.append(
            f"H52e GAP CLOSER FAILED: adtv_liquidity.sha256 mismatch — "
            f"got {adtv_actual[:16]}... (H30 ADTV?) expected {adtv_expected[:16]}... (CSI500 ADTV)"
        )

    basename_checks = {
        "prices": "prices_h52c_csi500_qfq.csv",
        "sector_metadata": "sector_metadata_h52b_csi500.csv",
        "universe": "universe_h52a_csi500.jsonl",
    }
    # NOTE: fundamentals + adtv_liquidity basenames hardcoded in H51b — not checked
    for ds_key, expected_bn in basename_checks.items():
        entry = ds.get(ds_key, {})
        actual_file = entry.get("file", "")
        if actual_file and not actual_file.endswith(expected_bn):
            errors.append(
                f"H51b audit: data_sources.{ds_key}.file = {actual_file!r} does not end with {expected_bn!r}"
            )

    if not data.get("verdict"):
        errors.append("H51b audit: missing 'verdict' field")
    # Candidate count NOT checked — 0 candidates is valid
    return errors


# ═══════════════════════════════════════════════════════════════════════════
# Sub-Pipeline Runners
# ═══════════════════════════════════════════════════════════════════════════

def run_h42(dry_run: bool, output_dir: Path, csi_sha: dict) -> dict:
    """H42: explicit CLI args via sys.argv patch — NO monkey-patch for paths."""
    import h42_strategy_redesign_search as h42

    result = {
        "sub": "h42",
        "status": "PENDING",
        "verdict": None,
        "elapsed_sec": 0.0,
        "audit_errors": [],
        "candidate_count": 0,
        "max_beat_HS300": None,
        "max_deploy_excess": None,
        "injection_method": "explicit_cli_args",
        "error": None,
    }

    run_out = output_dir / "fundamental_value_h52f_csi500_h42.json"
    report_out = output_dir / "h52f_csi500_h42_report.md"
    result["output_run"] = str(run_out)
    result["output_report"] = str(report_out)

    argv = [
        "h42_strategy_redesign_search.py",
        "--prices-file", str(CSI500_PRICES),
        "--universe-file", str(CSI500_UNIVERSE),
        "--snapshots-file", str(CSI500_SNAPSHOTS),
        "--output-run", str(run_out),
        "--output-report", str(report_out),
        "--top-k", "15",           # Match H30 H42 stage_c_count=15
    ]
    if dry_run:
        argv += ["--stage-a-limit", "1", "--stage-b-limit", "3", "--top-k", "1"]
    else:
        argv += ["--stage-b-limit", "0"]  # H30 had 600 stage_B → run all

    print(f"  H42 argv: {' '.join(a for a in argv if not a.startswith('--'))} --stage-b-limit {'0' if not dry_run else '3'}")
    t0 = time.monotonic()
    try:
        with patch.object(sys, "argv", argv):
            h42.main()
        result["status"] = "SUCCESS"
    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    result["elapsed_sec"] = round(time.monotonic() - t0, 1)

    if run_out.exists():
        audit_errors = audit_h42(run_out)
        result["audit_errors"] = audit_errors
        if audit_errors:
            result["status"] = "AUDIT_FAILED"
            for err in audit_errors:
                print(f"  [AUDIT FAIL] {err}")
        else:
            data = load_json(run_out)
            metrics = extract_metrics(data)
            result.update({
                "verdict": metrics["verdict"],
                "candidate_count": metrics["candidate_count"],
                "max_beat_HS300": metrics["max_beat_HS300"],
                "max_deploy_excess": metrics["max_deploy_excess"],
                "gate_pass_count": metrics["gate_pass_count"],
            })
    else:
        result["audit_errors"].append("Run JSON not found — sub-script did not produce output")

    return result


def run_h49b(dry_run: bool, output_dir: Path, csi_sha: dict) -> dict:
    """H49b: CLI for paths + monkey-patch SECTOR_CSV (hybrid).
    H49b has NO INPUT_SHA256 — computes sha256 at runtime from patched file paths."""
    import h49b_sector_neutral_rs_search as h49b

    result = {
        "sub": "h49b",
        "status": "PENDING",
        "verdict": None,
        "elapsed_sec": 0.0,
        "audit_errors": [],
        "candidate_count": 0,
        "max_beat_HS300": None,
        "max_deploy_excess": None,
        "injection_method": "hybrid_cli_plus_patch",
        "error": None,
    }

    run_out = output_dir / "fundamental_value_h52f_csi500_h49b.json"
    report_out = output_dir / "h52f_csi500_h49b_report.md"
    result["output_run"] = str(run_out)
    result["output_report"] = str(report_out)

    argv = [
        "h49b_sector_neutral_rs_search.py",
        "--prices-file", str(CSI500_PRICES),
        "--universe-file", str(CSI500_UNIVERSE),
        "--snapshots-file", str(CSI500_SNAPSHOTS),
        "--output-run", str(run_out),
        "--output-report", str(report_out),
    ]
    if dry_run:
        argv += ["--stage-a-limit", "1", "--stage-b-limit", "3", "--top-k", "1"]
    else:
        # Production: match H30 h49b (stage_a=22 all, stage_b=200, stage_c=15)
        argv += ["--stage-b-limit", "200", "--top-k", "15"]

    # MUST also patch H47_PRICES, H30_UNIVERSE, H30_SNAPSHOTS —
    # H49b's provenance computes sha256 from module constants (not CLI args).
    h49b_patches = {
        "SECTOR_CSV": CSI500_SECTOR,
        "H47_PRICES": CSI500_PRICES,
        "H30_UNIVERSE": CSI500_UNIVERSE,
        "H30_SNAPSHOTS": CSI500_SNAPSHOTS,
    }
    captured = {k: getattr(h49b, k) for k in h49b_patches}

    print(f"  H49b argv + patch SECTOR_CSV → h52b")
    t0 = time.monotonic()
    try:
        for k, v in h49b_patches.items():
            setattr(h49b, k, v)
        with patch.object(sys, "argv", argv):
            h49b.main()
        result["status"] = "SUCCESS"
    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        for k, v in captured.items():
            setattr(h49b, k, v)
    result["elapsed_sec"] = round(time.monotonic() - t0, 1)

    if run_out.exists():
        audit_errors = audit_h49b(run_out)
        result["audit_errors"] = audit_errors
        if audit_errors:
            result["status"] = "AUDIT_FAILED"
            for err in audit_errors:
                print(f"  [AUDIT FAIL] {err}")
        else:
            data = load_json(run_out)
            metrics = extract_metrics(data)
            result.update({
                "verdict": metrics["verdict"],
                "candidate_count": metrics["candidate_count"],
                "max_beat_HS300": metrics["max_beat_HS300"],
                "max_deploy_excess": metrics["max_deploy_excess"],
                "gate_pass_count": metrics["gate_pass_count"],
            })
    else:
        result["audit_errors"].append("Run JSON not found — sub-script did not produce output")

    return result


def run_h50b(dry_run: bool, output_dir: Path, csi_sha: dict) -> dict:
    """H50b: monkey-patch only (no path CLI exposed)."""
    import h50b_quality_value_search as h50b

    result = {
        "sub": "h50b",
        "status": "PENDING",
        "verdict": None,
        "elapsed_sec": 0.0,
        "audit_errors": [],
        "candidate_count": 0,
        "max_beat_HS300": None,
        "max_deploy_excess": None,
        "injection_method": "monkey_patch_paths",
        "error": None,
    }

    run_out = output_dir / "fundamental_value_h52f_csi500_h50b.json"
    report_out = output_dir / "h52f_csi500_h50b_report.md"
    result["output_run"] = str(run_out)
    result["output_report"] = str(report_out)

    argv = [
        "h50b_quality_value_search.py",
        "--output-run", str(run_out),
        "--output-report", str(report_out),
    ]
    if dry_run:
        argv += ["--stage-a-limit", "1", "--stage-b-limit", "3", "--top-k", "1"]
    else:
        # Production: match H30 h50b (stage_a=5, stage_b=200, stage_c=15)
        argv += ["--stage-a-limit", "5", "--stage-b-limit", "200", "--top-k", "15"]

    patches = {
        "H47_PRICES": CSI500_PRICES,
        "H30_UNIVERSE": CSI500_UNIVERSE,
        "H30_SNAPSHOTS": CSI500_SNAPSHOTS,
        "SECTOR_CSV": CSI500_SECTOR,
        "H50A_JSONL": CSI500_FUNDAMENTALS,
        "INPUT_SHA256": csi_sha,  # H50b uses INPUT_SHA256 for provenance
    }
    captured = {k: getattr(h50b, k) for k in patches}

    print(f"  H50b argv {' '.join(a for a in argv)} + 6 monkey-patches")
    t0 = time.monotonic()
    try:
        for k, v in patches.items():
            setattr(h50b, k, v)
        with patch.object(sys, "argv", argv):
            h50b.main()
        result["status"] = "SUCCESS"
    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        for k, v in captured.items():
            setattr(h50b, k, v)
    result["elapsed_sec"] = round(time.monotonic() - t0, 1)

    if run_out.exists():
        audit_errors = audit_h50b(run_out)
        result["audit_errors"] = audit_errors
        if audit_errors:
            result["status"] = "AUDIT_FAILED"
            for err in audit_errors:
                print(f"  [AUDIT FAIL] {err}")
        else:
            data = load_json(run_out)
            metrics = extract_metrics(data)
            result.update({
                "verdict": metrics["verdict"],
                "candidate_count": metrics["candidate_count"],
                "max_beat_HS300": metrics["max_beat_HS300"],
                "max_deploy_excess": metrics["max_deploy_excess"],
                "gate_pass_count": metrics["gate_pass_count"],
            })
    else:
        result["audit_errors"].append("Run JSON not found — sub-script did not produce output")

    return result


def run_h51b(dry_run: bool, output_dir: Path, csi_sha: dict) -> dict:
    """H51b: monkey-patch only + H51A_ADTV fix (H52e gap closer)."""
    import h51b_risk_model_search as h51b

    result = {
        "sub": "h51b",
        "status": "PENDING",
        "verdict": None,
        "elapsed_sec": 0.0,
        "audit_errors": [],
        "candidate_count": 0,
        "max_beat_HS300": None,
        "max_deploy_excess": None,
        "injection_method": "monkey_patch_paths_with_adtv_fix",
        "error": None,
    }

    run_out = output_dir / "fundamental_value_h52f_csi500_h51b.json"
    report_out = output_dir / "h52f_csi500_h51b_report.md"
    result["output_run"] = str(run_out)
    result["output_report"] = str(report_out)

    argv = [
        "h51b_risk_model_search.py",
        "--output-run", str(run_out),
        "--output-report", str(report_out),
    ]
    if dry_run:
        argv += ["--stage-b-limit", "3"]
    # else: no --stage-b-limit → run all 18 risk combos (production)

    patches = {
        "H47_PRICES": CSI500_PRICES,
        "H30_UNIVERSE": CSI500_UNIVERSE,
        "H30_SNAPSHOTS": CSI500_SNAPSHOTS,
        "SECTOR_CSV": CSI500_SECTOR,
        "H50A_JSONL": CSI500_FUNDAMENTALS,
        "H51A_ADTV": CSI500_ADTV,          # ← H52e gap closer
        "H51B_INPUT_SHA256": csi_sha,       # H51b uses H51B_INPUT_SHA256 for provenance
    }
    captured = {k: getattr(h51b, k) for k in patches}

    print(f"  H51b argv {' '.join(a for a in argv)} + 7 monkey-patches (incl ADTV fix)")
    t0 = time.monotonic()
    try:
        for k, v in patches.items():
            setattr(h51b, k, v)
        with patch.object(sys, "argv", argv):
            h51b.main()
        result["status"] = "SUCCESS"
    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        for k, v in captured.items():
            setattr(h51b, k, v)
    result["elapsed_sec"] = round(time.monotonic() - t0, 1)

    if run_out.exists():
        audit_errors = audit_h51b(run_out)
        result["audit_errors"] = audit_errors
        if audit_errors:
            result["status"] = "AUDIT_FAILED"
            for err in audit_errors:
                print(f"  [AUDIT FAIL] {err}")
        else:
            data = load_json(run_out)
            metrics = extract_metrics(data)
            result.update({
                "verdict": metrics["verdict"],
                "candidate_count": metrics["candidate_count"],
                "max_beat_HS300": metrics["max_beat_HS300"],
                "max_deploy_excess": metrics["max_deploy_excess"],
                "gate_pass_count": metrics["gate_pass_count"],
            })
    else:
        result["audit_errors"].append("Run JSON not found — sub-script did not produce output")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Master Report Builder (D6)
# ═══════════════════════════════════════════════════════════════════════════

def determine_aggregate_verdict(master_results: dict) -> str:
    """Determine H52f aggregate verdict from all sub results."""
    any_gate_pass = False
    all_csi_beats = []
    all_h30_beats = []

    for sub in ["h42", "h49b", "h50b", "h51b"]:
        sub_key = f"csi500_{sub}"
        h30_key = f"h30_{sub}"
        if sub_key in master_results:
            r = master_results[sub_key]
            if r.get("gate_pass_count", 0) > 0:
                any_gate_pass = True
            beat = r.get("max_beat_HS300") or 0  # None → 0
            all_csi_beats.append(beat)
        if h30_key in master_results:
            r = master_results[h30_key]
            beat = r.get("max_beat_HS300") or 0  # None → 0
            all_h30_beats.append(beat)

    max_csi = max(all_csi_beats) if all_csi_beats else 0
    max_h30 = max(all_h30_beats) if all_h30_beats else 0

    if any_gate_pass and max_csi >= 2:
        return "CSI500_BREAKTHROUGH"
    elif max_csi > max_h30:
        return "CSI500_IMPROVED"
    elif max_csi == max_h30:
        return "CSI500_PARITY"
    else:
        return "CSI500_REGRESSION"


def build_master_report(
    csi_sha: dict,
    h30_metrics: dict,
    csi_results: dict,
    sub_results: list,
    elapsed_total: float,
) -> str:
    """Build the master comparison report (D6)."""

    agg_verdict = determine_aggregate_verdict({
        **{f"csi500_{sub}": r for sub, r in csi_results.items()},
        **{f"h30_{sub}": h30_metrics[sub] for sub in h30_metrics},
    })

    lines = []
    lines.append("# H52f — CSI500 Full Pipeline Master Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Total elapsed: {elapsed_total:.1f}s ({elapsed_total/60:.1f} min)")
    lines.append("")

    # Sub-pipeline status summary
    lines.append("## Sub-Pipeline Status")
    lines.append("")
    lines.append(f"| Sub | Status | Verdict | Elapsed | Gate Pass | Max beat_HS300 | Max deploy_excess |")
    lines.append(f"|-----|--------|---------|---------|-----------|----------------|-------------------|")
    for sr in sub_results:
        sub = sr["sub"]
        status = sr["status"]
        verdict = sr.get("verdict", "—")
        elapsed = f"{sr['elapsed_sec']:.0f}s"
        gp = sr.get("gate_pass_count", "—")
        beat = f"{sr.get('max_beat_HS300', '—')}/5" if sr.get("max_beat_HS300") is not None else "—"
        de = f"{sr.get('max_deploy_excess', 0)*100:.1f}%" if sr.get("max_deploy_excess") is not None else "—"
        lines.append(f"| {sub} | {status} | {verdict} | {elapsed} | {gp} | {beat} | {de} |")
    lines.append("")

    # 8-row × columns comparison table
    lines.append("## H30 vs CSI500 Comparison")
    lines.append("")
    lines.append(f"| Sub-pipeline | Universe | Verdict | Gate-pass | Max beat_HS300 | Max deploy_excess |")
    lines.append(f"|-------------|----------|---------|-----------|----------------|-------------------|")
    for sub in ["h42", "h49b", "h50b", "h51b"]:
        for universe, metrics in [("H30", h30_metrics.get(sub, {})), ("CSI500", csi_results.get(sub, {}))]:
            verdict = metrics.get("verdict", "—") if isinstance(metrics, dict) else "—"
            gp = metrics.get("gate_pass_count", "—") if isinstance(metrics, dict) else "—"
            beat = metrics.get("max_beat_HS300", 0) if isinstance(metrics, dict) else 0
            # None → 0 (0 candidates = 0/5 beat_HS300)
            if beat is None:
                beat = 0
            beat_str = f"{beat}/5" if isinstance(beat, (int, float)) else str(beat)
            de = metrics.get("max_deploy_excess", "—") if isinstance(metrics, dict) else "—"
            de_str = f"{de*100:.1f}%" if isinstance(de, (int, float)) else str(de)
            lines.append(f"| {sub} | {universe} | {verdict} | {gp} | {beat_str} | {de_str} |")
    lines.append("")

    # Per-sub interpretation
    lines.append("## Per-Sub Interpretation")
    lines.append("")
    for sub in ["h42", "h49b", "h50b", "h51b"]:
        h30_m = h30_metrics.get(sub, {})
        csi_m = csi_results.get(sub, {})
        h30_beat = h30_m.get("max_beat_HS300", 0) if isinstance(h30_m, dict) else 0
        csi_beat = csi_m.get("max_beat_HS300", 0) if isinstance(csi_m, dict) else 0
        # None → 0 (0 candidates = 0/5 beat_HS300)
        if h30_beat is None: h30_beat = 0
        if csi_beat is None: csi_beat = 0
        h30_gp = h30_m.get("gate_pass_count", 0) if isinstance(h30_m, dict) else 0
        csi_gp = csi_m.get("gate_pass_count", 0) if isinstance(csi_m, dict) else 0

        if isinstance(csi_beat, (int, float)) and isinstance(h30_beat, (int, float)):
            if csi_beat > h30_beat:
                interp = f"↑ CSI500 lifted beat_HS300 from {h30_beat}/5 to {csi_beat}/5"
            elif csi_beat == h30_beat and csi_gp > h30_gp:
                interp = f"≈ CSI500 matched H30 beat_HS300 ({csi_beat}/5) but gained gate-pass ({csi_gp} vs {h30_gp})"
            elif csi_beat == h30_beat:
                interp = f"≈ CSI500 matched H30 at {csi_beat}/5 beat_HS300, gate-pass unchanged"
            else:
                interp = f"↓ CSI500 regressed from {h30_beat}/5 to {csi_beat}/5 beat_HS300"
        else:
            interp = "— could not compare (missing data)"
        lines.append(f"- **{sub}**: {interp}")
    lines.append("")

    # Aggregate verdict
    lines.append("## Aggregate H52f Verdict")
    lines.append("")
    lines.append(f"**{agg_verdict}**")
    lines.append("")

    # Next-step recommendation
    lines.append("## Next-Step Recommendation")
    lines.append("")
    if agg_verdict == "CSI500_BREAKTHROUGH":
        lines.append("CSI500 mid-cap universe has demonstrated superior factor purity — ")
        lines.append("at least one sub-pipeline candidate passed the gate with ≥2/5 beat_HS300_windows. ")
        lines.append("Recommend: **H53 — CSI500 Candidate Paper-Forward Monitoring** (phase B observation).")
    elif agg_verdict == "CSI500_IMPROVED":
        lines.append("CSI500 showed improvement over H30 in beat_HS300_windows but did not pass the gate. ")
        lines.append("Recommend: investigate wider CSI1000 universe or revisit PIT feature set.")
    elif agg_verdict == "CSI500_PARITY":
        lines.append("CSI500 matched H30's 1/5 beat_HS300 ceiling — no lift. ")
        lines.append("The structural finding hardens: beating HS300 with quality-value + sector + risk overlay ")
        lines.append("is not achievable in either HS300 or CSI500 universes under current PIT feature set. ")
        lines.append("Recommend: escalate to CSI1000 or accept paper-only as terminal state.")
    else:  # REGRESSION
        lines.append("CSI500 performed WORSE than H30. Universe expansion regressed performance. ")
        lines.append("Recommend: investigate why mid-cap data produced weaker signal; ")
        lines.append("CSI1000 expansion unlikely to help if CSI500 is worse than H30.")

    lines.append("")
    lines.append("## H48 Note")
    lines.append("")
    lines.append("H48 skipped as redundant — CSI500 data (H52c) is already on unified qfq, ")
    lines.append("so an H52f-H48 run would be byte-identical to H52f-H42.")
    lines.append("")

    # Provenance
    lines.append("## CSI500 Data Provenance")
    lines.append("")
    for key, path in CSI500_FILE_MAP.items():
        sha = csi_sha.get(key, "N/A")
        lines.append(f"- **{key}**: `{path.name}` — SHA256 `{sha[:32]}...`")
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════════════════════

def check_protected_mtimes() -> List[str]:
    """Verify protected library files haven't been touched. Returns mtimes dict."""
    mtimes = {}
    for path in PROTECTED_LIBS:
        mtimes[str(path)] = path.stat().st_mtime
    return mtimes


def verify_protected_unchanged(initial_mtimes: dict) -> List[str]:
    """Check that protected library mtimes haven't changed."""
    errors = []
    for path_str, initial_mtime in initial_mtimes.items():
        current_mtime = Path(path_str).stat().st_mtime
        if abs(current_mtime - initial_mtime) > 0.01:
            errors.append(f"PROTECTED FILE MODIFIED: {path_str} (mtime changed)")
    return errors


def main():
    parser = argparse.ArgumentParser(description="H52f — CSI500 Full Pipeline Rerun")
    parser.add_argument("--dry-run", action="store_true", help="Smoke test with minimal params")
    parser.add_argument("--sub", choices=["h42", "h49b", "h50b", "h51b"],
                        help="Run only one sub-pipeline (for smoke testing)")
    parser.add_argument("--force", action="store_true", help="Force re-run all subs (skip resumability)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Override output directory (default: backtest/runs/ + reports/)")
    parser.add_argument("--skip-audit", action="store_true",
                        help="Skip post-run audit (NOT recommended)")
    args = parser.parse_args()

    # Verify protected library mtimes BEFORE any imports
    print("=" * 70)
    print("H52f — CSI500 Full Pipeline Rerun")
    print("=" * 70)
    initial_mtimes = check_protected_mtimes()
    print(f"Protected library mtimes recorded: {len(initial_mtimes)} files")

    # Compute CSI500 sha256s once
    csi_sha = csi500_sha256_dict()
    print(f"CSI500 SHA256s computed:")
    for key, sha in csi_sha.items():
        print(f"  {key}: {sha[:16]}...")

    # Load H30 baselines for comparison
    h30_metrics = {}
    for sub in ["h42", "h49b", "h50b", "h51b"]:
        h30_data = load_h30_baseline(sub)
        h30_metrics[sub] = extract_metrics(h30_data)
        print(f"H30 {sub}: verdict={h30_metrics[sub]['verdict']}, "
              f"gate_pass={h30_metrics[sub]['gate_pass_count']}, "
              f"beat={h30_metrics[sub]['max_beat_HS300']}")

    # Determine output directories
    if args.output_dir:
        output_dir = args.output_dir
        report_dir = args.output_dir
    else:
        output_dir = OUT_DIR
        report_dir = REPORT_DIR

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    # Determine subs to run
    if args.sub:
        subs = [args.sub]
    else:
        subs = ["h42", "h49b", "h50b", "h51b"]

    # Sub-pipeline map
    sub_runners = {
        "h42": run_h42,
        "h49b": run_h49b,
        "h50b": run_h50b,
        "h51b": run_h51b,
    }

    # Output path map for resumability check
    sub_outputs = {
        "h42": output_dir / "fundamental_value_h52f_csi500_h42.json",
        "h49b": output_dir / "fundamental_value_h52f_csi500_h49b.json",
        "h50b": output_dir / "fundamental_value_h52f_csi500_h50b.json",
        "h51b": output_dir / "fundamental_value_h52f_csi500_h51b.json",
    }

    sub_results = []
    csi_results = {}
    t0_overall = time.monotonic()

    for sub in subs:
        print(f"\n{'─' * 60}")
        print(f"Sub-pipeline: {sub}")
        print(f"{'─' * 60}")

        # Resumability check
        if not args.force and not args.dry_run and sub_run_skippable(sub_outputs[sub], csi_sha):
            print(f"  SKIPPED — existing output matches CSI500 provenance sha256s")
            data = load_json(sub_outputs[sub])
            metrics = extract_metrics(data)
            csi_results[sub] = metrics
            sub_results.append({
                "sub": sub,
                "status": "SKIPPED",
                "verdict": metrics["verdict"],
                "elapsed_sec": 0,
                "audit_errors": [],
                "candidate_count": metrics["candidate_count"],
                "max_beat_HS300": metrics["max_beat_HS300"],
                "max_deploy_excess": metrics["max_deploy_excess"],
                "gate_pass_count": metrics["gate_pass_count"],
                "output_run": str(sub_outputs[sub]),
                "error": None,
            })
            continue

        # Run sub-pipeline
        runner = sub_runners[sub]
        result = runner(args.dry_run, output_dir, csi_sha)
        sub_results.append(result)

        if result["status"] in ("SUCCESS", "SKIPPED"):
            csi_results[sub] = {
                "verdict": result.get("verdict"),
                "gate_pass_count": result.get("gate_pass_count"),
                "max_beat_HS300": result.get("max_beat_HS300"),
                "max_deploy_excess": result.get("max_deploy_excess"),
                "candidate_count": result.get("candidate_count"),
            }

        # Print immediate result
        print(f"  Status: {result['status']}")
        print(f"  Elapsed: {result['elapsed_sec']:.1f}s")
        print(f"  Verdict: {result.get('verdict', 'N/A')}")
        print(f"  Gate pass: {result.get('gate_pass_count', 'N/A')}")
        print(f"  Max beat_HS300: {result.get('max_beat_HS300', 'N/A')}")
        print(f"  Audit errors: {len(result.get('audit_errors', []))}")

        # HARD FAIL on audit errors for production run
        audit_errs = result.get("audit_errors", [])
        if audit_errs and not args.skip_audit and not args.dry_run:
            print(f"\n  ❌ AUDIT FAILED — aborting pipeline")
            for err in audit_errs:
                print(f"    {err}")
            sys.exit(1)

        # Stop after first sub if single-sub mode
        if args.sub:
            break

    elapsed_total = time.monotonic() - t0_overall

    # Build master report (if we have at least one result)
    if sub_results and not args.dry_run:
        print(f"\n{'═' * 60}")
        print("Building master comparison report...")
        report_content = build_master_report(csi_sha, h30_metrics, csi_results, sub_results, elapsed_total)

        master_path = report_dir / "h52f_csi500_full_pipeline_master_report.md"
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_text(report_content, encoding="utf-8")
        print(f"Master report: {master_path}")

        # Print comparison summary
        print(f"\n{'═' * 60}")
        print("COMPARISON SUMMARY (H30 vs CSI500)")
        print(f"{'═' * 60}")
        print(f"{'Sub':<8} {'H30 beat':>10} {'CSI500 beat':>12} {'H30 gate':>10} {'CSI500 gate':>12}")
        print(f"{'─'*8} {'─'*10} {'─'*12} {'─'*10} {'─'*12}")
        for sub in ["h42", "h49b", "h50b", "h51b"]:
            h30_b = h30_metrics.get(sub, {}).get("max_beat_HS300", "—")
            csi_b = csi_results.get(sub, {}).get("max_beat_HS300", "—")
            h30_g = h30_metrics.get(sub, {}).get("gate_pass_count", "—")
            csi_g = csi_results.get(sub, {}).get("gate_pass_count", "—")
            h30_bs = f"{h30_b}/5" if isinstance(h30_b, (int, float)) else str(h30_b)
            csi_bs = f"{csi_b}/5" if isinstance(csi_b, (int, float)) else str(csi_b)
            print(f"{sub:<8} {h30_bs:>10} {csi_bs:>12} {str(h30_g):>10} {str(csi_g):>12}")

        agg_verdict = determine_aggregate_verdict({
            **{f"csi500_{sub}": r for sub, r in csi_results.items()},
            **{f"h30_{sub}": h30_metrics[sub] for sub in h30_metrics},
        })
        print(f"\nAggregate H52f Verdict: {agg_verdict}")

    # Verify protected library files unchanged
    print(f"\n{'─' * 60}")
    print("Protected file integrity check...")
    integrity_errors = verify_protected_unchanged(initial_mtimes)
    if integrity_errors:
        print("❌ PROTECTED FILE INTEGRITY VIOLATED:")
        for err in integrity_errors:
            print(f"  {err}")
    else:
        print("✅ All protected library files unchanged")

    print(f"\n{'═' * 60}")
    print(f"H52f pipeline complete. Total elapsed: {elapsed_total:.1f}s ({elapsed_total/60:.1f} min)")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    main()
