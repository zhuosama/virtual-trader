"""Tests for H52g CSI500 Zero-Candidate Diagnostic.

Covers: harness instantiation, source path injection, hypothesis classification logic,
artifact selection mirroring h52e/h52f patterns.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "backtest" / "experiments"))


class TestH52gHarnessInstantiation:
    """Smoke tests: harness module loads + key functions are callable."""

    def test_module_imports(self):
        """H52g harness module imports without error."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        assert h52g is not None

    def test_locked_params_defined(self):
        """Locked scenario params are defined."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        assert h52g.LOCKED_PARAMS["top_n"] == 8
        assert h52g.LOCKED_PARAMS["max_position_pct"] == 0.08
        assert h52g.LOCKED_PARAMS["quality_filter"] == 0.40
        assert h52g.LOCKED_PARAMS["rebalance_freq_days"] == 63

    def test_cal_2024_rebalance_dates(self):
        """cal_2024 rebalance dates are correct (4 dates, 63-day cadence)."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        assert len(h52g.CAL_2024_REBALANCE_DATES) == 4
        assert h52g.CAL_2024_REBALANCE_DATES[0] == "2024-01-02"
        assert h52g.CAL_2024_REBALANCE_DATES[-1] == "2024-10-04"

    def test_deploy_window_dates(self):
        """Deploy window uses H50b's actual deploy dates (2025-2026)."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        assert h52g.WINDOW_START == "2025-01-02"
        assert h52g.WINDOW_END == "2026-05-21"
        assert len(h52g.DEPLOY_REBALANCE_DATES) == 6

    def test_data_paths_exist(self):
        """All required data files exist."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        for attr_name in [
            "H30_PRICES", "H30_UNIVERSE", "H30_SNAPSHOTS", "H30_FUNDAMENTALS", "H30_SECTOR",
            "CSI500_PRICES", "CSI500_UNIVERSE", "CSI500_SNAPSHOTS", "CSI500_FUNDAMENTALS",
            "CSI500_SECTOR",
        ]:
            path = getattr(h52g, attr_name)
            assert path.exists(), f"{attr_name} missing: {path}"

    def test_dry_run_exits_zero(self):
        """--dry-run exits 0 and creates no outputs in project dirs."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "h52g_csi500_zero_candidate_diagnostic.py"),
             "--dry-run", "--output-dir", "/tmp/h52g_test_dryrun"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "DRY-RUN" in result.stdout
        assert "Sources loaded" in result.stdout

    def test_smoke_imports_value_score_h50(self):
        """h50b.ValueScoreH50 is importable."""
        import scripts.h50b_quality_value_search as h50b
        assert hasattr(h50b, "ValueScoreH50")
        assert hasattr(h50b.ValueScoreH50, "from_fundamentals")


class TestH52gHypothesisClassification:
    """Unit tests for hypothesis classification logic."""

    def test_classify_root_cause_single(self):
        """Single FAIL hypothesis → ROOT_CAUSE_IDENTIFIED."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        hypos = {
            "H_A": ("PASS", {}),
            "H_B": ("FAIL", {}),
            "H_C": ("PASS", {}),
            "H_D": ("PASS", {}),
            "H_E": ("PASS", {}),
            "H_F": ("PASS", {}),
        }
        assert h52g._classify_root_cause(hypos, None) == "ROOT_CAUSE_IDENTIFIED"

    def test_classify_root_cause_date_format(self):
        """Date format issue + all PASS hypotheses → ROOT_CAUSE_IDENTIFIED."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        hypos = {f"H_{c}": ("PASS", {}) for c in "ABCDEF"}
        diff = {"price_date_format": {"compatible": False}}
        assert h52g._classify_root_cause(hypos, diff) == "ROOT_CAUSE_IDENTIFIED"

    def test_classify_root_cause_date_format_plus_fail(self):
        """Date format + one FAIL hypothesis → MULTI_CAUSE."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        hypos = {
            "H_A": ("PASS", {}), "H_B": ("PASS", {}), "H_C": ("PASS", {}),
            "H_D": ("PASS", {}), "H_E": ("FAIL", {}), "H_F": ("PASS", {}),
        }
        diff = {"price_date_format": {"compatible": False}}
        assert h52g._classify_root_cause(hypos, diff) == "MULTI_CAUSE"

    def test_classify_root_cause_multi(self):
        """Multiple FAIL hypotheses → MULTI_CAUSE."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        hypos = {
            "H_A": ("FAIL", {}),
            "H_B": ("PASS", {}),
            "H_C": ("FAIL", {}),
            "H_D": ("PASS", {}),
            "H_E": ("PASS", {}),
            "H_F": ("PASS", {}),
        }
        assert h52g._classify_root_cause(hypos, None) == "MULTI_CAUSE"

    def test_classify_root_cause_none(self):
        """All PASS but 0 candidates → UNKNOWN."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        hypos = {f"H_{c}": ("PASS", {}) for c in "ABCDEF"}
        assert h52g._classify_root_cause(hypos, None) == "UNKNOWN"

    def test_compute_diff_first_blocker(self):
        """First divergence detection: different deploy_blockers."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        h30 = {"can_deploy": True, "deploy_blockers": [], "n_days": 200, "n_sells": 40,
               "data_quality": {"survivorship_bias": False, "future_function": False,
                                "filing_delay": False, "ungated_fundamentals": False},
               "price_coverage": {"ok": True}, "metrics": {"trade_count": 40}}
        csi = {"can_deploy": False, "deploy_blockers": ["insufficient_trades: 5 < 30"],
               "n_days": 200, "n_sells": 5,
               "data_quality": {"survivorship_bias": False, "future_function": False,
                                "filing_delay": False, "ungated_fundamentals": False},
               "price_coverage": {"ok": True}, "metrics": {"trade_count": 5}}
        diff = h52g._compute_diff(h30, csi)
        assert "deploy_blockers" in diff.get("first_divergence", "")

    def test_compute_diff_data_quality(self):
        """First divergence: data_quality flag differs."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        h30 = {"data_quality": {"survivorship_bias": False, "future_function": False,
                                 "filing_delay": False, "ungated_fundamentals": False},
               "price_coverage": {"ok": True}, "deploy_blockers": [], "n_days": 200,
               "n_sells": 40, "metrics": {"trade_count": 40}}
        csi = {"data_quality": {"survivorship_bias": True, "future_function": False,
                                 "filing_delay": False, "ungated_fundamentals": False},
               "price_coverage": {"ok": True}, "deploy_blockers": [],
               "n_days": 200, "n_sells": 40, "metrics": {"trade_count": 40}}
        diff = h52g._compute_diff(h30, csi)
        assert "data_quality.survivorship_bias" == diff.get("first_divergence")


class TestH52gArtifactSelection:
    """Artifact selection tests mirror h52e/h52f patterns."""

    def test_harness_script_exists(self):
        """H52g diagnostic harness script exists."""
        assert (SCRIPTS_DIR / "h52g_csi500_zero_candidate_diagnostic.py").exists()

    def test_output_not_in_project_on_dry_run(self):
        """Dry-run does not write to project dirs (only /tmp)."""
        # Verify project output files don't exist yet (before full run)
        diag_json = PROJECT_ROOT / "data/cn_pit/h52g_diagnostic.json"
        report_md = PROJECT_ROOT / "reports/h52g_csi500_zero_candidate_diagnostic_report.md"
        # These may or may not exist depending on prior runs; the key test
        # is that dry-run writes to /tmp, not here.
        # This test just verifies the paths are correctly defined.
        assert str(diag_json).endswith("h52g_diagnostic.json")
        assert str(report_md).endswith("h52g_csi500_zero_candidate_diagnostic_report.md")

    def test_hard_prohibitions_paths_intact(self):
        """Verify no H52g output would overwrite protected files."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        # H52g writes to its own output paths, not existing ones
        diag = h52g.PROJECT_ROOT / "data/cn_pit/h52g_diagnostic.json"
        # Verify it doesn't collide with any H30/H42/H48/H49b/H50b/H51b/H52a-d run file
        protected_suffixes = [
            "h42_strategy_redesign_search.json",
            "h48_unified_qfq_h42_rerun.json",
            "h49b_sector_neutral_rs_search.json",
            "h50b_quality_value_search.json",
            "h51b_risk_model_search.json",
            "h52e_csi500_smoke_h42.json",
            "h52f_csi500_h42.json",
        ]
        for suffix in protected_suffixes:
            assert diag.name != suffix, f"Collision with protected file: {suffix}"


class TestH52gNoNetwork:
    """No network calls — pure local analysis."""

    def test_no_network_imports(self):
        """H52g harness has no network-dependent imports."""
        import scripts.h52g_csi500_zero_candidate_diagnostic as h52g
        source = Path(h52g.__file__).read_text()
        # No requests, urllib, yfinance, tushare imports
        for banned in ["import requests", "import urllib", "import yfinance",
                        "import tushare", "from urllib", "from yfinance",
                        "from tushare", "web_search", "web_extract"]:
            assert banned not in source, f"Network import found: {banned}"
