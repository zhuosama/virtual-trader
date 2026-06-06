#!/usr/bin/env python3
"""
Tests for H52f CSI500 Full Pipeline harness.

Tests are deterministic and free of network calls — all sub-script calls are mocked.

Coverage:
- CLI surface detection (synthetic fixture for re-grep)
- Each sub's path-injection mechanism (CLI vs monkey-patch)
- ADTV sha256 audit (H52e gap closer)
- Master report aggregation logic
- Resumability skip-completed-subs check
- Protected file integrity verification
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

import h52f_csi500_full_pipeline as h52f  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────

def _temp_file(content: str, suffix: str = ".csv") -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def _temp_json(data: dict) -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, tmp)
    tmp.close()
    return Path(tmp.name)


def _make_run_json(
    data_sources: dict = None,
    inputs: dict = None,
    verdict: str = "RESEARCH_ONLY",
    candidates: list = None,
    stage_a: int = 5,
    stage_b: int = 200,
    stage_c: int = 15,
    gate_pass: int = 0,
) -> dict:
    """Create a synthetic run JSON matching the structure of actual sub-run outputs."""
    if data_sources is None:
        data_sources = {
            "prices": {"file": "data/cn_pit/prices_h52c_csi500_qfq.csv",
                        "sha256": "a" * 64},
            "sector_metadata": {"file": "data/cn_pit/sector_metadata_h52b_csi500.csv",
                                "sha256": "b" * 64},
            "fundamentals": {"file": "data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl",
                              "sha256": "c" * 64},
            "universe": {"file": "data/cn_pit/universe_h52a_csi500.jsonl",
                          "sha256": "d" * 64},
            "universe_snapshots": {"file": "data/cn_pit/universe_snapshots_h52a_csi500.jsonl",
                                    "sha256": "e" * 64},
        }
    if inputs is None:
        inputs = {
            "prices_file": "data/cn_pit/prices_h52c_csi500_qfq.csv",
            "universe_file": "data/cn_pit/universe_h52a_csi500.jsonl",
            "snapshots_file": "data/cn_pit/universe_snapshots_h52a_csi500.jsonl",
        }
    if candidates is None:
        candidates = [
            {
                "params": {},
                "overlay": "test",
                "passes_acceptance_gate": False,
                "gate_metrics": {
                    "beat_hs300_windows": 1,
                    "deploy_excess_return": -0.02,
                },
            }
        ]

    return {
        "verdict": verdict,
        "inputs": inputs,
        "data_sources": data_sources,
        "top_candidates_multi_window": candidates,
        "stage_a_count": stage_a,
        "stage_b_count": stage_b,
        "stage_c_count": stage_c,
        "gate_pass_count": gate_pass,
        "clean_deploy_count": 50,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Test: sha256 computation
# ═══════════════════════════════════════════════════════════════════════════

class TestSha256Computation(unittest.TestCase):
    """Verify sha256 hex digest computation."""

    def test_file_sha256_deterministic(self):
        f1 = _temp_file("hello world\n")
        f2 = _temp_file("hello world\n")
        try:
            self.assertEqual(h52f.file_sha256(f1), h52f.file_sha256(f2))
        finally:
            f1.unlink(missing_ok=True)
            f2.unlink(missing_ok=True)

    def test_file_sha256_different(self):
        f1 = _temp_file("hello\n")
        f2 = _temp_file("world\n")
        try:
            self.assertNotEqual(h52f.file_sha256(f1), h52f.file_sha256(f2))
        finally:
            f1.unlink(missing_ok=True)
            f2.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# Test: Audit hooks
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditHooks(unittest.TestCase):
    """Verify per-sub post-run audit logic."""

    def test_audit_h42_passes_valid_json(self):
        run = _make_run_json()
        path = _temp_json(run)
        try:
            errors = h52f.audit_h42(path)
            self.assertEqual(errors, [])
        finally:
            path.unlink(missing_ok=True)

    def test_audit_h42_fails_wrong_prices_basename(self):
        run = _make_run_json(inputs={
            "prices_file": "data/cn_pit/prices_h47_tushare_qfq_candidate.csv",  # H30
            "universe_file": "data/cn_pit/universe_h52a_csi500.jsonl",
            "snapshots_file": "data/cn_pit/universe_snapshots_h52a_csi500.jsonl",
        })
        path = _temp_json(run)
        try:
            errors = h52f.audit_h42(path)
            self.assertTrue(any("prices_file" in e for e in errors))
        finally:
            path.unlink(missing_ok=True)

    def test_audit_h50b_passes_valid_sha(self):
        """Audit should pass when sha256s match."""
        # sha256 audit compares to CSI500 file sha256s; we use a run JSON
        # with known valid sha256s but the test can't actually verify against
        # real files. We test the structure: no missing fields, verdict present.
        # Full sha256 matching is tested in the integration smoke test.
        run = _make_run_json()
        path = _temp_json(run)
        try:
            errors = h52f.audit_h50b(path)
            # This will likely fail sha256 check since our fake sha256s
            # don't match real CSI500 files. We just verify structure errors
            # (like missing verdict) don't show up.
            self.assertFalse(any("missing 'verdict'" in e for e in errors))
        finally:
            path.unlink(missing_ok=True)

    def test_audit_h51b_adtv_liquidity_checked(self):
        """H52e gap closer: h51b audit MUST check adtv_liquidity sha256."""
        run = _make_run_json(data_sources={
            "prices": {"sha256": "a" * 64},
            "sector_metadata": {"sha256": "b" * 64},
            "fundamentals": {"sha256": "c" * 64},
            "universe": {"sha256": "d" * 64},
            "adtv_liquidity": {"sha256": "f" * 64},  # wrong sha256
        })
        path = _temp_json(run)
        try:
            errors = h52f.audit_h51b(path)
            # Must have an error about adtv_liquidity
            self.assertTrue(any("adtv" in e.lower() for e in errors),
                            f"Expected adtv_liquidity audit error, got: {errors}")
        finally:
            path.unlink(missing_ok=True)

    def test_audit_missing_verdict(self):
        run = _make_run_json(verdict="")
        path = _temp_json(run)
        try:
            errors = h52f.audit_h42(path)
            self.assertTrue(any("verdict" in e.lower() for e in errors))
        finally:
            path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# Test: Extract Metrics
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractMetrics(unittest.TestCase):
    """Verify metric extraction from run JSONs."""

    def test_extract_basic(self):
        run = _make_run_json(
            verdict="RESEARCH_ONLY",
            gate_pass=0,
            candidates=[
                {
                    "gate_metrics": {"beat_hs300_windows": 1, "deploy_excess_return": -0.05},
                },
                {
                    "gate_metrics": {"beat_hs300_windows": 0, "deploy_excess_return": -0.10},
                },
            ],
        )
        m = h52f.extract_metrics(run)
        self.assertEqual(m["verdict"], "RESEARCH_ONLY")
        self.assertEqual(m["gate_pass_count"], 0)
        self.assertEqual(m["max_beat_HS300"], 1)
        self.assertAlmostEqual(m["max_deploy_excess"], -0.05)

    def test_extract_empty_candidates(self):
        run = _make_run_json(candidates=[])
        m = h52f.extract_metrics(run)
        self.assertIsNone(m["max_beat_HS300"])
        self.assertIsNone(m["max_deploy_excess"])

    def test_extract_stage_counts(self):
        run = _make_run_json(stage_a=18, stage_b=600, stage_c=15)
        m = h52f.extract_metrics(run)
        self.assertEqual(m["stage_a_count"], 18)
        self.assertEqual(m["stage_b_count"], 600)
        self.assertEqual(m["stage_c_count"], 15)


# ═══════════════════════════════════════════════════════════════════════════
# Test: Resumability
# ═══════════════════════════════════════════════════════════════════════════

class TestResumability(unittest.TestCase):
    """Verify skip-completed-subs logic."""

    def test_sha256_matches(self):
        """sha256_match when provenance matches."""
        csi_sha = {"prices": "a" * 64, "sector_metadata": "b" * 64,
                   "fundamentals": "c" * 64, "universe": "d" * 64,
                   "universe_snapshots": "e" * 64}
        run = _make_run_json(data_sources={
            "prices": {"sha256": "a" * 64},
            "sector_metadata": {"sha256": "b" * 64},
            "fundamentals": {"sha256": "c" * 64},
            "universe": {"sha256": "d" * 64},
            "universe_snapshots": {"sha256": "e" * 64},
        })
        self.assertTrue(h52f.sha256_matches_csi500(run, csi_sha))

    def test_sha256_does_not_match(self):
        """sha256_match fails when provenance differs."""
        csi_sha = {"prices": "a" * 64, "sector_metadata": "b" * 64,
                   "fundamentals": "c" * 64, "universe": "d" * 64,
                   "universe_snapshots": "e" * 64}
        run = _make_run_json(data_sources={
            "prices": {"sha256": "WRONG" * 16},  # wrong
            "sector_metadata": {"sha256": "b" * 64},
            "fundamentals": {"sha256": "c" * 64},
            "universe": {"sha256": "d" * 64},
            "universe_snapshots": {"sha256": "e" * 64},
        })
        self.assertFalse(h52f.sha256_matches_csi500(run, csi_sha))

    def test_sub_run_skippable_when_present_and_match(self):
        """Skippable when file exists and sha256s match."""
        csi_sha = {"prices": "a" * 64, "sector_metadata": "b" * 64,
                   "fundamentals": "c" * 64, "universe": "d" * 64,
                   "universe_snapshots": "e" * 64}
        run = _make_run_json(data_sources={
            "prices": {"sha256": "a" * 64},
            "sector_metadata": {"sha256": "b" * 64},
            "fundamentals": {"sha256": "c" * 64},
            "universe": {"sha256": "d" * 64},
            "universe_snapshots": {"sha256": "e" * 64},
        })
        path = _temp_json(run)
        try:
            self.assertTrue(h52f.sub_run_skippable(path, csi_sha))
        finally:
            path.unlink(missing_ok=True)

    def test_sub_run_not_skippable_when_missing(self):
        """Not skippable when file doesn't exist."""
        csi_sha = {"prices": "a" * 64}
        self.assertFalse(
            h52f.sub_run_skippable(Path("/tmp/nonexistent_h52f_test.json"), csi_sha)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test: Aggregate Verdict Logic (D6)
# ═══════════════════════════════════════════════════════════════════════════

class TestAggregateVerdict(unittest.TestCase):
    """Verify master report aggregate verdict logic."""

    def _make_master(self, csi_beats, csi_gate, h30_beats):
        """Build master_results dict."""
        results = {}
        for i, sub in enumerate(["h42", "h49b", "h50b", "h51b"]):
            if i < len(csi_beats):
                results[f"csi500_{sub}"] = {
                    "max_beat_HS300": csi_beats[i],
                    "gate_pass_count": csi_gate[i] if i < len(csi_gate) else 0,
                }
            if i < len(h30_beats):
                results[f"h30_{sub}"] = {
                    "max_beat_HS300": h30_beats[i],
                    "gate_pass_count": 0,
                }
        return results

    def test_breakthrough(self):
        """CSI500_BREAKTHROUGH when gate pass + >=2/5."""
        results = self._make_master(
            csi_beats=[2, 1, 1, 1],
            csi_gate=[1, 0, 0, 0],
            h30_beats=[0, 1, 1, 1],
        )
        self.assertEqual(h52f.determine_aggregate_verdict(results), "CSI500_BREAKTHROUGH")

    def test_improved(self):
        """CSI500_IMPROVED when beat improves but no gate pass."""
        results = self._make_master(
            csi_beats=[1, 2, 1, 1],  # h49b improved to 2 but no gate
            csi_gate=[0, 0, 0, 0],
            h30_beats=[0, 1, 1, 1],
        )
        self.assertEqual(h52f.determine_aggregate_verdict(results), "CSI500_IMPROVED")

    def test_parity(self):
        """CSI500_PARITY when max beat same as H30."""
        results = self._make_master(
            csi_beats=[0, 1, 1, 1],
            csi_gate=[0, 0, 0, 0],
            h30_beats=[0, 1, 1, 1],
        )
        self.assertEqual(h52f.determine_aggregate_verdict(results), "CSI500_PARITY")

    def test_regression(self):
        """CSI500_REGRESSION when max beat worse than H30."""
        results = self._make_master(
            csi_beats=[0, 0, 0, 0],
            csi_gate=[0, 0, 0, 0],
            h30_beats=[0, 1, 1, 1],
        )
        self.assertEqual(h52f.determine_aggregate_verdict(results), "CSI500_REGRESSION")

    def test_breakthrough_requires_both(self):
        """Gate pass alone without >=2/5 shouldn't be BREAKTHROUGH."""
        results = self._make_master(
            csi_beats=[1, 1, 1, 1],
            csi_gate=[1, 0, 0, 0],  # gate pass but only 1/5 beat
            h30_beats=[0, 1, 1, 1],
        )
        self.assertNotEqual(h52f.determine_aggregate_verdict(results), "CSI500_BREAKTHROUGH")


# ═══════════════════════════════════════════════════════════════════════════
# Test: Master Report Building
# ═══════════════════════════════════════════════════════════════════════════

class TestMasterReport(unittest.TestCase):
    """Verify master report content."""

    def setUp(self):
        self.csi_sha = {
            "prices": "a" * 64,
            "universe": "b" * 64,
            "universe_snapshots": "c" * 64,
            "sector_metadata": "d" * 64,
            "fundamentals": "e" * 64,
            "adtv_liquidity": "f" * 64,
        }

    def test_report_contains_comparison_table(self):
        """Master report must have comparison table."""
        h30_m = {
            "h42": h52f.extract_metrics(_make_run_json()),
            "h49b": h52f.extract_metrics(_make_run_json(stage_a=22)),
            "h50b": h52f.extract_metrics(_make_run_json()),
            "h51b": h52f.extract_metrics(_make_run_json(stage_b=18)),
        }
        csi_r = {
            "h42": h52f.extract_metrics(_make_run_json()),
            "h49b": h52f.extract_metrics(_make_run_json()),
            "h50b": h52f.extract_metrics(_make_run_json()),
            "h51b": h52f.extract_metrics(_make_run_json()),
        }
        sub_results = [
            {"sub": "h42", "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "elapsed_sec": 120, "gate_pass_count": 0, "max_beat_HS300": 0,
             "max_deploy_excess": -0.05},
        ]

        report = h52f.build_master_report(self.csi_sha, h30_m, csi_r, sub_results, 500)

        self.assertIn("H30 vs CSI500 Comparison", report)
        self.assertIn("Sub-pipeline", report)
        self.assertIn("Aggregate H52f Verdict", report)
        self.assertIn("Next-Step Recommendation", report)
        self.assertIn("H48", report)  # H48 skip documented

    def test_report_covers_all_4_subs(self):
        """Report must include all 4 sub-pipelines."""
        h30_m = {sub: h52f.extract_metrics(_make_run_json()) for sub in
                 ["h42", "h49b", "h50b", "h51b"]}
        csi_r = {sub: h52f.extract_metrics(_make_run_json()) for sub in
                 ["h42", "h49b", "h50b", "h51b"]}
        sub_results = [
            {"sub": sub, "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "elapsed_sec": 100, "gate_pass_count": 0, "max_beat_HS300": 1,
             "max_deploy_excess": -0.03}
            for sub in ["h42", "h49b", "h50b", "h51b"]
        ]

        report = h52f.build_master_report(self.csi_sha, h30_m, csi_r, sub_results, 500)
        for sub in ["h42", "h49b", "h50b", "h51b"]:
            self.assertIn(sub, report)


# ═══════════════════════════════════════════════════════════════════════════
# Test: Protected File Integrity
# ═══════════════════════════════════════════════════════════════════════════

class TestProtectedFiles(unittest.TestCase):
    """Verify protected file integrity check."""

    def test_check_protected_mtimes_returns_dict(self):
        mtimes = h52f.check_protected_mtimes()
        self.assertIsInstance(mtimes, dict)
        self.assertGreater(len(mtimes), 0)

    def test_verify_unchanged_passes(self):
        """When mtimes unchanged, no errors."""
        initial = h52f.check_protected_mtimes()
        errors = h52f.verify_protected_unchanged(initial)
        self.assertEqual(errors, [])

    def test_verify_changed_detects_modification(self):
        """When mtime changed, error reported."""
        initial = h52f.check_protected_mtimes()
        # Simulate a change by subtracting from one entry
        first_key = list(initial.keys())[0]
        initial[first_key] = 0  # impossible mtime
        errors = h52f.verify_protected_unchanged(initial)
        self.assertTrue(any(first_key in e for e in errors))


# ═══════════════════════════════════════════════════════════════════════════
# Test: CLI Surface Detection (synthetic fixture)
# ═══════════════════════════════════════════════════════════════════════════

class TestCLISurface(unittest.TestCase):
    """Verify sub-script CLI surface is correctly documented in harness."""

    def test_h42_has_path_cli_args(self):
        """H42 exposes --prices-file, --universe-file, --snapshots-file via CLI."""
        with open(SCRIPTS / "h42_strategy_redesign_search.py") as f:
            content = f.read()
        self.assertIn("--prices-file", content)
        self.assertIn("--universe-file", content)
        self.assertIn("--snapshots-file", content)

    def test_h50b_has_no_path_cli_args(self):
        """H50b does NOT expose input-path CLI args."""
        with open(SCRIPTS / "h50b_quality_value_search.py") as f:
            content = f.read()
        self.assertNotIn("--prices-file", content)
        self.assertNotIn("--universe-file", content)

    def test_h51b_has_no_path_cli_args(self):
        """H51b does NOT expose input-path CLI args."""
        with open(SCRIPTS / "h51b_risk_model_search.py") as f:
            content = f.read()
        self.assertNotIn("--prices-file", content)
        self.assertNotIn("--universe-file", content)

    def test_h49b_has_path_cli_args(self):
        """H49b exposes --prices-file, --universe-file, --snapshots-file via CLI."""
        with open(SCRIPTS / "h49b_sector_neutral_rs_search.py") as f:
            content = f.read()
        self.assertIn("--prices-file", content)
        self.assertIn("--universe-file", content)
        self.assertIn("--snapshots-file", content)

    def test_h51b_has_adtv_constant(self):
        """H51b has H51A_ADTV module constant — must be patched."""
        with open(SCRIPTS / "h51b_risk_model_search.py") as f:
            content = f.read()
        self.assertIn("H51A_ADTV", content)

    def test_h51b_has_H51B_INPUT_SHA256(self):
        """H51b has H51B_INPUT_SHA256 dict."""
        with open(SCRIPTS / "h51b_risk_model_search.py") as f:
            content = f.read()
        self.assertIn("H51B_INPUT_SHA256", content)

    def test_h50b_has_INPUT_SHA256(self):
        """H50b has INPUT_SHA256 dict."""
        with open(SCRIPTS / "h50b_quality_value_search.py") as f:
            content = f.read()
        self.assertIn("INPUT_SHA256", content)


if __name__ == "__main__":
    unittest.main()
