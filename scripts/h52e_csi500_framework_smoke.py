#!/usr/bin/env python3
"""H52e — CSI500 Search Framework Smoke Test

Integration test proving H42/H50b/H51b search scripts can swap CSI500 data
via path injection without source-code edits.

Usage:
    python scripts/h52e_csi500_framework_smoke.py           # full run
    python scripts/h52e_csi500_framework_smoke.py --dry-run # imports + sha256 only
    python scripts/h52e_csi500_framework_smoke.py --output-dir /tmp/h52e  # alt dir
"""

import argparse
import hashlib
import json
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
sys.path.insert(0, str(PROJECT_ROOT))  # for h51b's _sys.modules lookup

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

# ── Default outputs ─────────────────────────────────────────────────────
OUT_DIR = PROJECT_ROOT / "backtest" / "runs"
REPORT_DIR = PROJECT_ROOT / "reports"

H42_RUN_OUT = OUT_DIR / "fundamental_value_h52e_csi500_smoke_h42.json"
H42_REPORT_OUT = REPORT_DIR / "h52e_smoke_h42_partial.md"
H50B_RUN_OUT = OUT_DIR / "fundamental_value_h52e_csi500_smoke_h50b.json"
H50B_REPORT_OUT = REPORT_DIR / "h52e_smoke_h50b_partial.md"
H51B_RUN_OUT = OUT_DIR / "fundamental_value_h52e_csi500_smoke_h51b.json"
H51B_REPORT_OUT = REPORT_DIR / "h52e_smoke_h51b_partial.md"

UNIFIED_REPORT = REPORT_DIR / "h52e_csi500_framework_smoke_report.md"


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
    """Recompute sha256 dict for CSI500 files. Keys match H50b INPUT_SHA256 / H51b H51B_INPUT_SHA256."""
    return {key: file_sha256(path) for key, path in CSI500_FILE_MAP.items()}


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def json_safe(obj):
    """Make an object JSON-serializable (Path → str)."""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj


# ═══════════════════════════════════════════════════════════════════════════
# Audit hook — verifies provenance references CSI500 (D1 mandate)
# ═══════════════════════════════════════════════════════════════════════════

def audit_h42_run(run_path: Path) -> List[str]:
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
    return errors


def audit_h50b_run(run_path: Path) -> List[str]:
    """Post-run audit for H50b: verify data_sources sha256s match CSI500 files."""
    errors = []
    data = load_json(run_path)
    ds = data.get("data_sources", {})
    csi_sha = csi500_sha256_dict()

    # Sha256 checks (proves CSI500 data was actually read)
    sha_checks = {
        "prices": "prices",
        "sector_metadata": "sector_metadata",
        "fundamentals": "fundamentals",
        "universe": "universe",
        "universe_snapshots": "universe_snapshots",
    }
    for ds_key, sha_key in sha_checks.items():
        entry = ds.get(ds_key, {})
        actual_sha = entry.get("sha256", "")
        expected_sha = csi_sha[sha_key]
        if actual_sha != expected_sha:
            errors.append(
                f"H50b audit: data_sources.{ds_key}.sha256 = {actual_sha} "
                f"!= expected CSI500 sha256 = {expected_sha}"
            )

    # File basename checks — only for fields that use dynamic .name (not hardcoded)
    # H50b hardcodes file for "fundamentals" as literal "data/cn_pit/fundamentals_h50a_pit_quality.jsonl"
    dynamic_checks = {
        "prices": "prices_h52c_csi500_qfq.csv",
        "sector_metadata": "sector_metadata_h52b_csi500.csv",
        "universe": "universe_h52a_csi500.jsonl",
        "universe_snapshots": "universe_snapshots_h52a_csi500.jsonl",
    }
    for ds_key, expected_basename in dynamic_checks.items():
        entry = ds.get(ds_key, {})
        actual_file = entry.get("file", "")
        if not actual_file.endswith(expected_basename):
            errors.append(
                f"H50b audit: data_sources.{ds_key}.file = {actual_file!r} "
                f"does not end with {expected_basename!r}"
            )
    return errors


def audit_h51b_run(run_path: Path) -> List[str]:
    """Post-run audit for H51b: verify data_sources sha256s match CSI500 files."""
    errors = []
    data = load_json(run_path)
    ds = data.get("data_sources", {})
    csi_sha = csi500_sha256_dict()

    # Sha256 checks (proves CSI500 data was actually read)
    sha_checks = {
        "prices": "prices",
        "sector_metadata": "sector_metadata",
        "fundamentals": "fundamentals",
        "adtv_liquidity": "adtv_liquidity",
        "universe": "universe",
    }
    for ds_key, sha_key in sha_checks.items():
        entry = ds.get(ds_key, {})
        actual_sha = entry.get("sha256", "")
        expected_sha = csi_sha[sha_key]
        if actual_sha != expected_sha:
            errors.append(
                f"H51b audit: data_sources.{ds_key}.sha256 = {actual_sha} "
                f"!= expected CSI500 sha256 = {expected_sha}"
            )

    # File basename checks — only for fields that use dynamic .name (not hardcoded)
    # H51b hardcodes file for "fundamentals" and "adtv_liquidity" as literal strings
    dynamic_checks = {
        "prices": "prices_h52c_csi500_qfq.csv",
        "sector_metadata": "sector_metadata_h52b_csi500.csv",
        "universe": "universe_h52a_csi500.jsonl",
    }
    for ds_key, expected_basename in dynamic_checks.items():
        entry = ds.get(ds_key, {})
        actual_file = entry.get("file", "")
        if not actual_file.endswith(expected_basename):
            errors.append(
                f"H51b audit: data_sources.{ds_key}.file = {actual_file!r} "
                f"does not end with {expected_basename!r}"
            )
    return errors


# ═══════════════════════════════════════════════════════════════════════════
# Sub-run functions
# ═══════════════════════════════════════════════════════════════════════════

def run_h42_smoke(output_dir: Path) -> dict:
    """H42: explicit CLI args via sys.argv patch — NO monkey-patch for paths (BLOCKER fix)."""
    import h42_strategy_redesign_search as h42

    result = {
        "sub": "h42",
        "status": "PENDING",
        "verdict": None,
        "elapsed_sec": 0.0,
        "audit_errors": [],
        "candidate_count": 0,
        "top_beat_hs300_windows": None,
        "injection_method": "explicit_cli_args",
        "error": None,
        "output_run": str(output_dir / "fundamental_value_h52e_csi500_smoke_h42.json"),
    }

    run_out = output_dir / "fundamental_value_h52e_csi500_smoke_h42.json"
    report_out = output_dir / "h52e_smoke_h42_partial.md"

    argv = [
        "h42_strategy_redesign_search.py",
        "--prices-file", str(CSI500_PRICES),
        "--universe-file", str(CSI500_UNIVERSE),
        "--snapshots-file", str(CSI500_SNAPSHOTS),
        "--stage-a-limit", "1",
        "--stage-b-limit", "3",
        "--top-k", "1",
        "--output-run", str(run_out),
        "--output-report", str(report_out),
    ]

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

    # Post-run audit
    if run_out.exists():
        audit_errors = audit_h42_run(run_out)
        result["audit_errors"] = audit_errors
        if audit_errors:
            result["status"] = "AUDIT_FAILED"
            for err in audit_errors:
                print(f"  [AUDIT FAIL] {err}")
        else:
            # Extract metadata from run JSON
            data = load_json(run_out)
            result["verdict"] = data.get("verdict", "")
            candidates = data.get("top_candidates_multi_window", [])
            result["candidate_count"] = len(candidates)
            if candidates:
                best = candidates[0]
                result["top_beat_hs300_windows"] = best.get("beat_HS300_windows", None)
    else:
        result["audit_errors"].append("Run JSON not found — sub-script did not produce output")

    return result


def run_h50b_smoke(output_dir: Path) -> dict:
    """H50b: monkey-patch path constants + INPUT_SHA256, output via CLI."""
    import h50b_quality_value_search as h50b

    result = {
        "sub": "h50b",
        "status": "PENDING",
        "verdict": None,
        "elapsed_sec": 0.0,
        "audit_errors": [],
        "candidate_count": 0,
        "top_beat_hs300_windows": None,
        "injection_method": "monkey_patch_paths",
        "error": None,
        "output_run": str(output_dir / "fundamental_value_h52e_csi500_smoke_h50b.json"),
    }

    run_out = output_dir / "fundamental_value_h52e_csi500_smoke_h50b.json"
    report_out = output_dir / "h52e_smoke_h50b_partial.md"
    csi_sha = csi500_sha256_dict()

    h50b_patches = {
        "H47_PRICES": CSI500_PRICES,
        "H30_UNIVERSE": CSI500_UNIVERSE,
        "H30_SNAPSHOTS": CSI500_SNAPSHOTS,
        "SECTOR_CSV": CSI500_SECTOR,
        "H50A_JSONL": CSI500_FUNDAMENTALS,
        "INPUT_SHA256": csi_sha,
    }
    argv = [
        "h50b_quality_value_search.py",
        "--stage-a-limit", "1",
        "--stage-b-limit", "3",
        "--top-k", "1",
        "--output-run", str(run_out),
        "--output-report", str(report_out),
    ]

    captured = {k: getattr(h50b, k) for k in h50b_patches}
    t0 = time.monotonic()
    try:
        for k, v in h50b_patches.items():
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

    # Post-run audit
    if run_out.exists():
        audit_errors = audit_h50b_run(run_out)
        result["audit_errors"] = audit_errors
        if audit_errors:
            result["status"] = "AUDIT_FAILED"
            for err in audit_errors:
                print(f"  [AUDIT FAIL] {err}")
        else:
            data = load_json(run_out)
            result["verdict"] = data.get("verdict", "")
            candidates = data.get("top_candidates_multi_window", [])
            result["candidate_count"] = len(candidates)
            if candidates:
                best = candidates[0]
                result["top_beat_hs300_windows"] = best.get("beat_HS300_windows", None)
    else:
        result["audit_errors"].append("Run JSON not found — sub-script did not produce output")

    return result


def run_h51b_smoke(output_dir: Path) -> dict:
    """H51b: monkey-patch path constants + H51B_INPUT_SHA256, output via CLI."""
    import h51b_risk_model_search as h51b

    result = {
        "sub": "h51b",
        "status": "PENDING",
        "verdict": None,
        "elapsed_sec": 0.0,
        "audit_errors": [],
        "candidate_count": 0,
        "top_beat_hs300_windows": None,
        "injection_method": "monkey_patch_paths",
        "error": None,
        "output_run": str(output_dir / "fundamental_value_h52e_csi500_smoke_h51b.json"),
    }

    run_out = output_dir / "fundamental_value_h52e_csi500_smoke_h51b.json"
    report_out = output_dir / "h52e_smoke_h51b_partial.md"
    csi_sha = csi500_sha256_dict()

    h51b_patches = {
        "H47_PRICES": CSI500_PRICES,
        "H30_UNIVERSE": CSI500_UNIVERSE,
        "H30_SNAPSHOTS": CSI500_SNAPSHOTS,
        "SECTOR_CSV": CSI500_SECTOR,
        "H50A_JSONL": CSI500_FUNDAMENTALS,
        "H51A_ADTV": CSI500_ADTV,
        "H51B_INPUT_SHA256": csi_sha,
    }
    argv = [
        "h51b_risk_model_search.py",
        "--stage-b-limit", "3",
        "--output-run", str(run_out),
        "--output-report", str(report_out),
        "--capital", "500000",
    ]

    captured = {k: getattr(h51b, k) for k in h51b_patches}
    t0 = time.monotonic()
    try:
        for k, v in h51b_patches.items():
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

    # Post-run audit
    if run_out.exists():
        audit_errors = audit_h51b_run(run_out)
        result["audit_errors"] = audit_errors
        if audit_errors:
            result["status"] = "AUDIT_FAILED"
            for err in audit_errors:
                print(f"  [AUDIT FAIL] {err}")
        else:
            data = load_json(run_out)
            result["verdict"] = data.get("verdict", "")
            candidates = data.get("top_candidates_multi_window", [])
            result["candidate_count"] = len(candidates)
            if candidates:
                best = candidates[0]
                result["top_beat_hs300_windows"] = best.get("beat_HS300_windows", None)
    else:
        result["audit_errors"].append("Run JSON not found — sub-script did not produce output")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Report
# ═══════════════════════════════════════════════════════════════════════════

def generate_unified_report(results: List[dict], elapsed_total: float, output_dir: Path) -> str:
    """Generate unified markdown report from 3 sub-run results."""
    lines = [
        "# H52e — CSI500 Framework Smoke Test Report",
        "",
        f"**Generated**: {datetime.now(timezone.utc).isoformat()}",
        f"**Total wall time**: {elapsed_total:.1f}s",
        f"**Output directory**: {output_dir}",
        "",
        "---",
        "",
    ]

    overall_pass = all(r["status"] == "SUCCESS" for r in results)

    for r in results:
        status_icon = "✅" if r["status"] == "SUCCESS" else ("⚠️" if r["status"] == "AUDIT_FAILED" else "❌")
        lines.append(f"## {status_icon} {r['sub'].upper()} Sub-Smoke")
        lines.append("")
        lines.append(f"| Field | Value |")
        lines.append(f"|---|---|")
        lines.append(f"| Status | {r['status']} |")
        lines.append(f"| Injection method | {r['injection_method']} |")
        lines.append(f"| Verdict | {r.get('verdict', 'N/A')} |")
        lines.append(f"| Candidate count | {r['candidate_count']} |")
        lines.append(f"| Top beat_HS300_windows | {r.get('top_beat_hs300_windows', 'N/A')} |")
        lines.append(f"| Elapsed (s) | {r['elapsed_sec']} |")
        lines.append(f"| Audit errors | {len(r['audit_errors'])} |")
        lines.append(f"| Output JSON | {r.get('output_run', 'N/A')} |")
        if r.get("error"):
            lines.append(f"| Error | {r['error']} |")
        if r["audit_errors"]:
            lines.append("")
            lines.append("### Audit Errors")
            for err in r["audit_errors"]:
                lines.append(f"- {err}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"## Overall: {'SMOKE_PASS ✅' if overall_pass else 'BLOCKED ❌'}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Regression test helper
# ═══════════════════════════════════════════════════════════════════════════

def run_regression_tests() -> Tuple[bool, str]:
    """Run H42/H50b/H51b regression tests to verify no cross-contamination."""
    test_files = " ".join([
        "tests/test_h42_strategy_redesign_search.py",
        "tests/test_h50b_quality_value_search.py",
        "tests/test_h51b_risk_model_search.py",
    ])
    cmd = f"cd {PROJECT_ROOT} && python3 -m pytest {test_files} -q --tb=short 2>&1"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    return result.returncode == 0, result.stdout + result.stderr


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="H52e CSI500 Framework Smoke Test")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only verify imports + sha256 + path patches; no real backtests")
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR,
                        help=f"Output directory for run JSONs (default: {OUT_DIR})")
    parser.add_argument("--skip-regression", action="store_true",
                        help="Skip H42/H50b/H51b regression tests at end")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("H52e — CSI500 Framework Smoke Test")
    print(f"  Output dir: {output_dir}")
    print(f"  Mode: {'DRY-RUN' if args.dry_run else 'FULL'}")
    print("=" * 70)

    # Step 1: Verify imports + sha256
    print("\n[1/2] Verifying imports and sha256 computation...")
    try:
        import h42_strategy_redesign_search as h42  # noqa: F811
        import h50b_quality_value_search as h50b  # noqa: F811
        import h51b_risk_model_search as h51b  # noqa: F811
        print("  All 3 sub-scripts importable ✅")
    except ImportError as e:
        print(f"  IMPORT FAILED: {e}")
        sys.exit(1)

    csi_sha = csi500_sha256_dict()
    print(f"  CSI500 sha256 dict: {len(csi_sha)} keys computed ✅")
    for k in ["prices", "universe", "universe_snapshots", "sector_metadata", "fundamentals", "adtv_liquidity"]:
        assert k in csi_sha, f"Missing sha256 key: {k}"
        assert len(csi_sha[k]) == 64, f"sha256 for {k} is not 64 hex chars: {len(csi_sha[k])}"
    print("  All 6 sha256 keys valid (64-char hex) ✅")

    # Verify all 6 CSI500 files exist
    for key, path in CSI500_FILE_MAP.items():
        if not path.exists():
            print(f"  MISSING CSI500 file: {path}")
            sys.exit(1)
    print("  All 6 CSI500 data files on disk ✅")

    # Verify 4 library files mtime capture
    lib_files = {
        "h42": PROJECT_ROOT / "scripts/h42_strategy_redesign_search.py",
        "h50b": PROJECT_ROOT / "scripts/h50b_quality_value_search.py",
        "h51b": PROJECT_ROOT / "scripts/h51b_risk_model_search.py",
        "fundamental_backtest": PROJECT_ROOT / "backtest/experiments/fundamental_backtest.py",
    }
    mtimes_before = {k: f.stat().st_mtime for k, f in lib_files.items()}
    print(f"  Library file mtimes captured: 4 files ✅")

    if args.dry_run:
        print("\n[Dry-run complete — no real backtests executed]")
        sys.exit(0)

    # Step 2: Run 3 sub-smokes
    print("\n[2/2] Running 3 sub-smokes...")
    results = []
    t_total_start = time.monotonic()

    # H42
    print("\n  ▶ H42 sub-smoke (explicit CLI args, no monkey-patch)...")
    r_h42 = run_h42_smoke(output_dir)
    results.append(r_h42)
    print(f"  ◀ H42: {r_h42['status']} | verdict={r_h42['verdict']} | candidates={r_h42['candidate_count']} | {r_h42['elapsed_sec']}s")

    # H50b
    print("\n  ▶ H50b sub-smoke (monkey-patch paths + sha256)...")
    r_h50b = run_h50b_smoke(output_dir)
    results.append(r_h50b)
    print(f"  ◀ H50b: {r_h50b['status']} | verdict={r_h50b['verdict']} | candidates={r_h50b['candidate_count']} | {r_h50b['elapsed_sec']}s")

    # H51b
    print("\n  ▶ H51b sub-smoke (monkey-patch paths + sha256)...")
    r_h51b = run_h51b_smoke(output_dir)
    results.append(r_h51b)
    print(f"  ◀ H51b: {r_h51b['status']} | verdict={r_h51b['verdict']} | candidates={r_h51b['candidate_count']} | {r_h51b['elapsed_sec']}s")

    t_total = round(time.monotonic() - t_total_start, 1)

    # Step 3: Generate unified report
    print(f"\n[3] Generating unified report ({UNIFIED_REPORT})...")
    report_md = generate_unified_report(results, t_total, output_dir)
    UNIFIED_REPORT.write_text(report_md, encoding="utf-8")
    print(f"  Report written: {UNIFIED_REPORT}")

    # Step 4: Regression tests
    if not args.skip_regression:
        print("\n[4] Running H42/H50b/H51b regression tests...")
        reg_ok, reg_output = run_regression_tests()
        if reg_ok:
            print("  Regression tests: ALL PASS ✅")
        else:
            print(f"  Regression tests: FAILED ❌")
            # Print last 20 lines of output
            for line in reg_output.strip().split("\n")[-20:]:
                print(f"    {line}")
    else:
        reg_ok = True
        print("\n[4] Regression tests: SKIPPED (--skip-regression)")

    # Step 5: Verify library file mtimes unchanged
    print("\n[5] Verifying library file mtimes unchanged...")
    mtimes_ok = True
    for k, f in lib_files.items():
        after = f.stat().st_mtime
        if after != mtimes_before[k]:
            print(f"  MTIME CHANGED: {k} ({f.name}) — was {mtimes_before[k]}, now {after} ❌")
            mtimes_ok = False
        else:
            print(f"  {k}: unchanged ✅")
    if mtimes_ok:
        print("  All 4 library file mtimes preserved ✅")

    # Step 6: Summary
    print("\n" + "=" * 70)
    all_ok = all(r["status"] == "SUCCESS" for r in results)
    overall = "SMOKE_PASS" if all_ok and reg_ok and mtimes_ok else "BLOCKED"

    reasons = []
    if not all_ok:
        for r in results:
            if r["status"] != "SUCCESS":
                reasons.append(f"{r['sub']}: {r['status']} ({'; '.join(r['audit_errors'][:2])})")
    if not reg_ok:
        reasons.append("regression tests failed")
    if not mtimes_ok:
        reasons.append("library file mtimes changed")

    print(f"Overall: {overall}")
    if reasons:
        print(f"Reasons: {'; '.join(reasons)}")
    print(f"Total wall time: {t_total}s")
    print(f"Report: {UNIFIED_REPORT}")
    print("=" * 70)

    # Print per-sub details
    for r in results:
        print(f"\n{r['sub'].upper()}: verdict={r['verdict']} | "
              f"candidates={r['candidate_count']} | "
              f"top_beat_HS300={r.get('top_beat_hs300_windows')} | "
              f"sha256_audit={'PASS' if not r['audit_errors'] else 'FAIL'} | "
              f"wall={r['elapsed_sec']}s | "
              f"injection={r['injection_method']}")

    # MEDIUM finding
    print("\n[MEDIUM] All three sub-script main() use 'def main() -> int' without argv parameter.")
    print("  sys.argv patching was required for all three (h42, h50b, h51b).")
    print("  Consider adding 'def main(argv: List[str] = None) -> int' in future refactors.")

    return 0 if overall == "SMOKE_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
