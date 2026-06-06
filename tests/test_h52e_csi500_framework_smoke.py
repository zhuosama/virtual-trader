#!/usr/bin/env python3
"""Tests for H52e CSI500 Framework Smoke Test harness.

Tests are deterministic and free of network calls — all sub-script calls are mocked.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

import h52e_csi500_framework_smoke as h52e  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────

def _temp_file(content: str, suffix: str = ".csv") -> Path:
    """Write content to a temp file and return its Path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


# ═══════════════════════════════════════════════════════════════════════════
# Test: sha256 computation
# ═══════════════════════════════════════════════════════════════════════════

class TestSha256Computation(unittest.TestCase):
    """Verify sha256 hex digest computation."""

    def test_file_sha256_deterministic(self):
        f1 = _temp_file("hello world\n")
        f2 = _temp_file("hello world\n")
        try:
            self.assertEqual(h52e.file_sha256(f1), h52e.file_sha256(f2))
        finally:
            f1.unlink(missing_ok=True)
            f2.unlink(missing_ok=True)

    def test_file_sha256_different(self):
        f1 = _temp_file("hello world\n")
        f2 = _temp_file("goodbye world\n")
        try:
            self.assertNotEqual(h52e.file_sha256(f1), h52e.file_sha256(f2))
        finally:
            f1.unlink(missing_ok=True)
            f2.unlink(missing_ok=True)

    def test_sha256_64_char_hex(self):
        f = _temp_file("test")
        try:
            digest = h52e.file_sha256(f)
            self.assertEqual(len(digest), 64)
            int(digest, 16)
        finally:
            f.unlink(missing_ok=True)

    def test_empty_file(self):
        f = _temp_file("")
        try:
            expected = hashlib.sha256(b"").hexdigest()
            self.assertEqual(h52e.file_sha256(f), expected)
        finally:
            f.unlink(missing_ok=True)


class TestCsi500Sha256Dict(unittest.TestCase):
    """Verify csi500_sha256_dict() structure."""

    def test_has_all_keys(self):
        d = h52e.csi500_sha256_dict()
        expected_keys = {"prices", "universe", "universe_snapshots",
                         "sector_metadata", "fundamentals", "adtv_liquidity"}
        self.assertEqual(set(d.keys()), expected_keys)

    def test_all_64_char_hex(self):
        d = h52e.csi500_sha256_dict()
        for k, v in d.items():
            with self.subTest(key=k):
                self.assertEqual(len(v), 64, f"sha256 for {k} is {len(v)} chars")
                int(v, 16)


# ═══════════════════════════════════════════════════════════════════════════
# Test: capture-patch-finally round-trip
# ═══════════════════════════════════════════════════════════════════════════

class TestCapturePatchFinally(unittest.TestCase):
    """Verify finally-block restoration of monkey-patched module constants."""

    @classmethod
    def setUpClass(cls):
        import h50b_quality_value_search as h50b
        import h51b_risk_model_search as h51b
        cls.h50b = h50b
        cls.h51b = h51b

    def test_h50b_patch_restore_after_exception(self):
        patches = {
            "H47_PRICES": Path("/tmp/fake_csi500_prices.csv"),
            "H30_UNIVERSE": Path("/tmp/fake_csi500_universe.jsonl"),
            "H30_SNAPSHOTS": Path("/tmp/fake_csi500_snapshots.jsonl"),
            "SECTOR_CSV": Path("/tmp/fake_csi500_sector.csv"),
            "H50A_JSONL": Path("/tmp/fake_csi500_fundamentals.jsonl"),
            "INPUT_SHA256": {"test": "abc123"},
        }
        captured = {k: getattr(self.h50b, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(self.h50b, k, v)
            raise RuntimeError("simulated mid-run crash")
        except RuntimeError:
            pass
        finally:
            for k, v in captured.items():
                setattr(self.h50b, k, v)

        for k in patches:
            self.assertEqual(getattr(self.h50b, k), captured[k],
                             f"{k} not restored after exception in finally")

    def test_h50b_patch_restore_on_success(self):
        patches = {
            "H47_PRICES": Path("/tmp/fake_csi500_prices.csv"),
            "H30_UNIVERSE": Path("/tmp/fake_csi500_universe.jsonl"),
            "H30_SNAPSHOTS": Path("/tmp/fake_csi500_snapshots.jsonl"),
            "SECTOR_CSV": Path("/tmp/fake_csi500_sector.csv"),
            "H50A_JSONL": Path("/tmp/fake_csi500_fundamentals.jsonl"),
            "INPUT_SHA256": {"test": "abc123"},
        }
        captured = {k: getattr(self.h50b, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(self.h50b, k, v)
            self.assertEqual(self.h50b.H47_PRICES, patches["H47_PRICES"])
        finally:
            for k, v in captured.items():
                setattr(self.h50b, k, v)

        for k in patches:
            self.assertEqual(getattr(self.h50b, k), captured[k],
                             f"{k} not restored after finally")

    def test_h51b_patch_restore_after_exception(self):
        patches = {
            "H47_PRICES": Path("/tmp/fake_csi500_prices.csv"),
            "H30_UNIVERSE": Path("/tmp/fake_csi500_universe.jsonl"),
            "H30_SNAPSHOTS": Path("/tmp/fake_csi500_snapshots.jsonl"),
            "SECTOR_CSV": Path("/tmp/fake_csi500_sector.csv"),
            "H50A_JSONL": Path("/tmp/fake_csi500_fundamentals.jsonl"),
            "H51A_ADTV": Path("/tmp/fake_csi500_adtv.csv"),
            "H51B_INPUT_SHA256": {"test": "xyz789"},
        }
        captured = {k: getattr(self.h51b, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(self.h51b, k, v)
            raise RuntimeError("simulated crash")
        except RuntimeError:
            pass
        finally:
            for k, v in captured.items():
                setattr(self.h51b, k, v)

        for k in patches:
            self.assertEqual(getattr(self.h51b, k), captured[k],
                             f"{k} not restored after exception in finally")

    def test_post_restore_no_cross_contamination(self):
        """After all tests, H50b/H51b should have original constants."""
        # Original H50b H47_PRICES should still point to H30 data
        self.assertIn("prices_h47_tushare_qfq_candidate", str(self.h50b.H47_PRICES))
        self.assertIn("prices_h47_tushare_qfq_candidate", str(self.h51b.H47_PRICES))
        # Original INPUT_SHA256 should have H30 sha256s
        self.assertEqual(len(self.h50b.INPUT_SHA256["prices"]), 64)


# ═══════════════════════════════════════════════════════════════════════════
# Test: audit hooks
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditH42(unittest.TestCase):
    """Verify H42 post-run audit hook."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.run_path = Path(self.tmpdir) / "test_h42.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_audit_pass_with_csi500_paths(self):
        data = {
            "verdict": "RESEARCH_ONLY",
            "inputs": {
                "prices_file": "/data/prices_h52c_csi500_qfq.csv",
                "universe_file": "/data/universe_h52a_csi500.jsonl",
                "snapshots_file": "/data/universe_snapshots_h52a_csi500.jsonl",
            }
        }
        self.run_path.write_text(json.dumps(data))
        errors = h52e.audit_h42_run(self.run_path)
        self.assertEqual(errors, [])

    def test_audit_fail_on_h30_path(self):
        data = {
            "verdict": "RESEARCH_ONLY",
            "inputs": {
                "prices_file": "/data/prices_h38_candidate.csv",
                "universe_file": "/data/universe_h30_candidate.jsonl",
                "snapshots_file": "/data/universe_snapshots_h30_candidate.jsonl",
            }
        }
        self.run_path.write_text(json.dumps(data))
        errors = h52e.audit_h42_run(self.run_path)
        self.assertEqual(len(errors), 3)


class TestAuditH50b(unittest.TestCase):
    """Verify H50b post-run audit hook using synthetic CSI500 files."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        # Create synthetic CSI500 files with known content
        cls.tmp_prices = Path(cls.tmpdir) / "prices_h52c_csi500_qfq.csv"
        cls.tmp_sector = Path(cls.tmpdir) / "sector_metadata_h52b_csi500.csv"
        cls.tmp_fund = Path(cls.tmpdir) / "fundamentals_h52d_csi500_pit_quality.jsonl"
        cls.tmp_universe = Path(cls.tmpdir) / "universe_h52a_csi500.jsonl"
        cls.tmp_snaps = Path(cls.tmpdir) / "universe_snapshots_h52a_csi500.jsonl"

        cls.tmp_prices.write_text("csi500 prices test data\n")
        cls.tmp_sector.write_text("csi500 sector test data\n")
        cls.tmp_fund.write_text("csi500 fundamentals test data\n")
        cls.tmp_universe.write_text("csi500 universe test data\n")
        cls.tmp_snaps.write_text("csi500 snapshots test data\n")

        cls.csi_sha = {
            "prices": h52e.file_sha256(cls.tmp_prices),
            "sector_metadata": h52e.file_sha256(cls.tmp_sector),
            "fundamentals": h52e.file_sha256(cls.tmp_fund),
            "universe": h52e.file_sha256(cls.tmp_universe),
            "universe_snapshots": h52e.file_sha256(cls.tmp_snaps),
        }

        # Save original file map
        cls._orig_file_map = h52e.CSI500_FILE_MAP.copy()

        # Replace with temp files
        h52e.CSI500_FILE_MAP.clear()
        h52e.CSI500_FILE_MAP.update({
            "prices": cls.tmp_prices,
            "sector_metadata": cls.tmp_sector,
            "fundamentals": cls.tmp_fund,
            "universe": cls.tmp_universe,
            "universe_snapshots": cls.tmp_snaps,
            "adtv_liquidity": h52e.CSI500_ADTV,  # keep real path unused
        })

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        # Restore original file map
        h52e.CSI500_FILE_MAP.clear()
        h52e.CSI500_FILE_MAP.update(cls._orig_file_map)

    def setUp(self):
        self.run_path = Path(self.tmpdir) / "test_h50b.json"
    def test_audit_pass_with_csi500_sha256s(self):
        """Audit passes when sha256s match and dynamic file fields end with CSI500 basenames."""
        data = {
            "verdict": "RESEARCH_ONLY",
            "data_sources": {
                "prices": {"task": "h52c", "file": "prices_h52c_csi500_qfq.csv",
                           "sha256": self.csi_sha["prices"]},
                "sector_metadata": {"task": "h52b", "file": "sector_metadata_h52b_csi500.csv",
                                    "sha256": self.csi_sha["sector_metadata"]},
                "fundamentals": {"task": "h52d",
                                 "file": "data/cn_pit/fundamentals_h50a_pit_quality.jsonl",
                                 "sha256": self.csi_sha["fundamentals"]},
                "universe": {"file": "universe_h52a_csi500.jsonl",
                             "sha256": self.csi_sha["universe"]},
                "universe_snapshots": {"file": "universe_snapshots_h52a_csi500.jsonl",
                                       "sha256": self.csi_sha["universe_snapshots"]},
            }
        }
        self.run_path.write_text(json.dumps(data))
        errors = h52e.audit_h50b_run(self.run_path)
        self.assertEqual(errors, [], f"Unexpected audit errors: {errors}")

    def test_audit_fail_on_wrong_sha256(self):
        """Audit fails when sha256 doesn't match CSI500 file (all 5 wrong)."""
        data = {
            "verdict": "RESEARCH_ONLY",
            "data_sources": {
                "prices": {"task": "h47", "file": "prices_h52c_csi500_qfq.csv",
                           "sha256": "b" * 64},
                "sector_metadata": {"task": "h49a", "file": "sector_metadata_h52b_csi500.csv",
                                    "sha256": "d" * 64},
                "fundamentals": {"task": "h50a", "file": "x",
                                 "sha256": "e" * 64},
                "universe": {"file": "universe_h52a_csi500.jsonl",
                             "sha256": "c" * 64},
                "universe_snapshots": {"file": "universe_snapshots_h52a_csi500.jsonl",
                                       "sha256": "f" * 64},
            }
        }
        self.run_path.write_text(json.dumps(data))
        errors = h52e.audit_h50b_run(self.run_path)
        # 5 sha256 mismatches (file fields for dynamic entries are correct)
        self.assertEqual(len(errors), 5)

    def test_audit_fail_on_wrong_file_basename(self):
        """Audit fails when dynamic file basenames don't end with CSI500."""
        data = {
            "verdict": "RESEARCH_ONLY",
            "data_sources": {
                "prices": {"file": "prices_h47_tushare_qfq_candidate.csv",
                           "sha256": self.csi_sha["prices"]},
                "sector_metadata": {"file": "sector_metadata_sw_l1.csv",
                                    "sha256": self.csi_sha["sector_metadata"]},
                "fundamentals": {"file": "x", "sha256": self.csi_sha["fundamentals"]},
                "universe": {"file": "universe_h30_candidate.jsonl",
                             "sha256": self.csi_sha["universe"]},
                "universe_snapshots": {"file": "universe_snapshots_h30_candidate.jsonl",
                                       "sha256": self.csi_sha["universe_snapshots"]},
            }
        }
        self.run_path.write_text(json.dumps(data))
        errors = h52e.audit_h50b_run(self.run_path)
        # 4 dynamic file fields wrong (fundamentals is hardcoded, not checked)
        self.assertEqual(len(errors), 4)

class TestAuditH51b(unittest.TestCase):
    """Verify H51b post-run audit hook."""

    def test_audit_raises_keyerror_on_missing_adtv(self):
        """When adtv_liquidity is missing from data_sources, audit fails."""
        data = {
            "verdict": "RESEARCH_ONLY",
            "data_sources": {
                "prices": {"file": "x", "sha256": "a" * 64},
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(data))
            f.flush()
            run_path = Path(f.name)
        try:
            errors = h52e.audit_h51b_run(run_path)
            self.assertGreater(len(errors), 0)
        finally:
            run_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# Test: harness wiring with mocked sub-scripts
# ═══════════════════════════════════════════════════════════════════════════

class TestHarnessWiring(unittest.TestCase):
    """Verify harness produces valid JSONs via mocked sub-script calls."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(str(self.tmpdir), ignore_errors=True)

    def _write_mock_json(self, run_path, data):
        """Helper: write mock JSON to expected run path."""
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_text(json.dumps(data))

    def test_run_h42_smoke_success(self):
        """Mocked H42 produces valid JSON with correct paths."""
        minimal_json = {
            "verdict": "RESEARCH_ONLY",
            "inputs": {
                "prices_file": str(h52e.CSI500_PRICES),
                "universe_file": str(h52e.CSI500_UNIVERSE),
                "snapshots_file": str(h52e.CSI500_SNAPSHOTS),
            },
            "top_candidates_multi_window": [{"beat_HS300_windows": 2}]
        }
        run_path = self.tmpdir / "fundamental_value_h52e_csi500_smoke_h42.json"

        with patch("h42_strategy_redesign_search.main") as mock_main:
            mock_main.side_effect = lambda: self._write_mock_json(run_path, minimal_json)
            result = h52e.run_h42_smoke(self.tmpdir)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["verdict"], "RESEARCH_ONLY")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["top_beat_hs300_windows"], 2)
        self.assertEqual(result["injection_method"], "explicit_cli_args")
        self.assertEqual(result["audit_errors"], [])

    def test_run_h50b_smoke_success(self):
        """Mocked H50b produces valid JSON with correct data_sources."""
        csi_sha = h52e.csi500_sha256_dict()
        minimal_json = {
            "verdict": "RESEARCH_ONLY",
            "data_sources": {
                "prices": {"file": h52e.CSI500_PRICES.name,
                           "sha256": csi_sha["prices"]},
                "sector_metadata": {"file": h52e.CSI500_SECTOR.name,
                                    "sha256": csi_sha["sector_metadata"]},
                "fundamentals": {"file": h52e.CSI500_FUNDAMENTALS.name,
                                 "sha256": csi_sha["fundamentals"]},
                "universe": {"file": h52e.CSI500_UNIVERSE.name,
                             "sha256": csi_sha["universe"]},
                "universe_snapshots": {"file": h52e.CSI500_SNAPSHOTS.name,
                                       "sha256": csi_sha["universe_snapshots"]},
            },
            "top_candidates_multi_window": [{"beat_HS300_windows": 4}]
        }
        run_path = self.tmpdir / "fundamental_value_h52e_csi500_smoke_h50b.json"

        with patch("h50b_quality_value_search.main") as mock_main:
            mock_main.side_effect = lambda: self._write_mock_json(run_path, minimal_json)
            result = h52e.run_h50b_smoke(self.tmpdir)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["verdict"], "RESEARCH_ONLY")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["top_beat_hs300_windows"], 4)
        self.assertEqual(result["injection_method"], "monkey_patch_paths")
        self.assertEqual(result["audit_errors"], [])

    def test_run_h51b_smoke_success(self):
        """Mocked H51b produces valid JSON with correct data_sources."""
        csi_sha = h52e.csi500_sha256_dict()
        minimal_json = {
            "verdict": "RESEARCH_ONLY",
            "data_sources": {
                "prices": {"file": h52e.CSI500_PRICES.name,
                           "sha256": csi_sha["prices"]},
                "sector_metadata": {"file": h52e.CSI500_SECTOR.name,
                                    "sha256": csi_sha["sector_metadata"]},
                "fundamentals": {"file": h52e.CSI500_FUNDAMENTALS.name,
                                 "sha256": csi_sha["fundamentals"]},
                "adtv_liquidity": {"file": h52e.CSI500_ADTV.name,
                                   "sha256": csi_sha["adtv_liquidity"]},
                "universe": {"file": h52e.CSI500_UNIVERSE.name,
                             "sha256": csi_sha["universe"]},
            },
            "top_candidates_multi_window": [{"beat_HS300_windows": 3}]
        }
        run_path = self.tmpdir / "fundamental_value_h52e_csi500_smoke_h51b.json"

        with patch("h51b_risk_model_search.main") as mock_main:
            mock_main.side_effect = lambda: self._write_mock_json(run_path, minimal_json)
            result = h52e.run_h51b_smoke(self.tmpdir)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["verdict"], "RESEARCH_ONLY")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["top_beat_hs300_windows"], 3)
        self.assertEqual(result["injection_method"], "monkey_patch_paths")

    def test_h42_exception_captured(self):
        with patch("h42_strategy_redesign_search.main") as mock_main:
            mock_main.side_effect = ValueError("synthetic error")
            result = h52e.run_h42_smoke(self.tmpdir)

        self.assertEqual(result["status"], "FAILED")
        self.assertIn("ValueError", result["error"])

    def test_h50b_finally_restores_after_exception(self):
        import h50b_quality_value_search as h50b_mod
        orig_prices = h50b_mod.H47_PRICES

        with patch("h50b_quality_value_search.main") as mock_main:
            mock_main.side_effect = ValueError("crash")
            h52e.run_h50b_smoke(self.tmpdir)

        self.assertEqual(h50b_mod.H47_PRICES, orig_prices,
                         "H50b H47_PRICES not restored after exception!")

    def test_h42_no_run_json_error(self):
        """When mock doesn't write JSON, audit reports missing (status=SUCCESS, audit catches it)."""
        with patch("h42_strategy_redesign_search.main") as mock_main:
            mock_main.side_effect = lambda: None  # doesn't write JSON
            result = h52e.run_h42_smoke(self.tmpdir)

        # No exception → status SUCCESS, but audit hook catches missing JSON
        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(any("Run JSON not found" in e for e in result["audit_errors"]))


# ═══════════════════════════════════════════════════════════════════════════
# Test: report generation
# ═══════════════════════════════════════════════════════════════════════════

class TestReportGeneration(unittest.TestCase):
    def test_generates_three_sections(self):
        results = [
            {"sub": "h42", "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "candidate_count": 5, "top_beat_hs300_windows": 2,
             "elapsed_sec": 120.5, "audit_errors": [],
             "injection_method": "explicit_cli_args",
             "output_run": "/tmp/h42.json", "error": None},
            {"sub": "h50b", "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "candidate_count": 3, "top_beat_hs300_windows": 4,
             "elapsed_sec": 180.0, "audit_errors": [],
             "injection_method": "monkey_patch_paths",
             "output_run": "/tmp/h50b.json", "error": None},
            {"sub": "h51b", "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "candidate_count": 2, "top_beat_hs300_windows": 3,
             "elapsed_sec": 210.0, "audit_errors": [],
             "injection_method": "monkey_patch_paths",
             "output_run": "/tmp/h51b.json", "error": None},
        ]
        report = h52e.generate_unified_report(results, 510.5, Path("/tmp"))
        self.assertIn("H42", report)
        self.assertIn("H50B", report)
        self.assertIn("H51B", report)
        self.assertIn("SMOKE_PASS", report)
        self.assertIn("510.5s", report)

    def test_generates_blocked_on_failure(self):
        results = [
            {"sub": "h42", "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "candidate_count": 0, "top_beat_hs300_windows": None,
             "elapsed_sec": 10.0, "audit_errors": [],
             "injection_method": "explicit_cli_args",
             "output_run": "/tmp/h42.json", "error": None},
            {"sub": "h50b", "status": "FAILED", "verdict": None,
             "candidate_count": 0, "top_beat_hs300_windows": None,
             "elapsed_sec": 5.0, "audit_errors": ["Run JSON not found"],
             "injection_method": "monkey_patch_paths",
             "output_run": "/tmp/h50b.json", "error": "ValueError: crash"},
            {"sub": "h51b", "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "candidate_count": 0, "top_beat_hs300_windows": None,
             "elapsed_sec": 15.0, "audit_errors": [],
             "injection_method": "monkey_patch_paths",
             "output_run": "/tmp/h51b.json", "error": None},
        ]
        report = h52e.generate_unified_report(results, 30.0, Path("/tmp"))
        self.assertIn("BLOCKED", report)
        self.assertIn("ValueError", report)

    def test_with_audit_errors(self):
        results = [
            {"sub": "h42", "status": "AUDIT_FAILED", "verdict": "RESEARCH_ONLY",
             "candidate_count": 2, "top_beat_hs300_windows": 1,
             "elapsed_sec": 60.0, "audit_errors": ["prices sha256 mismatch"],
             "injection_method": "explicit_cli_args",
             "output_run": "/tmp/h42.json", "error": None},
            {"sub": "h50b", "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "candidate_count": 1, "top_beat_hs300_windows": 0,
             "elapsed_sec": 30.0, "audit_errors": [],
             "injection_method": "monkey_patch_paths",
             "output_run": "/tmp/h50b.json", "error": None},
            {"sub": "h51b", "status": "SUCCESS", "verdict": "RESEARCH_ONLY",
             "candidate_count": 0, "top_beat_hs300_windows": None,
             "elapsed_sec": 45.0, "audit_errors": [],
             "injection_method": "monkey_patch_paths",
             "output_run": "/tmp/h51b.json", "error": None},
        ]
        report = h52e.generate_unified_report(results, 135.0, Path("/tmp"))
        self.assertIn("BLOCKED", report)
        self.assertIn("prices sha256 mismatch", report)
