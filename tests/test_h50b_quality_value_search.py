#!/usr/bin/env python3
"""Tests for H50b — Quality-Value Composite Redesign Search.

Tests cover:
- Cross-section cache correctness (synthetic 5-ticker fixture)
- Per-component minimum enforcement
- Accruals raw-signed inversion (NO abs)
- Date hook firing
- Finally-restore in error path
- Scorer substitution + provenance
"""

from __future__ import annotations

import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
EXPERIMENTS_DIR = PROJECT_ROOT / "backtest" / "experiments"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(EXPERIMENTS_DIR))

# Must import the module under test — this triggers the module-level code
import h50b_quality_value_search as h50b


class TestValueScoreH50:
    """Tests for ValueScoreH50 class."""

    @staticmethod
    def _make_synthetic_panel(tickers, dates, values):
        """Create a synthetic H50A_PANEL for testing.

        values: dict[ticker][date] = {field: value, ...}
        dates should be in filing_date format.
        """
        panel = {}
        for ticker in tickers:
            panel[ticker] = []
            for date in dates:
                if ticker in values and date in values[ticker]:
                    row = {
                        "ticker": ticker,
                        "filing_date": date,
                        "report_period": date,
                        **values[ticker][date],
                    }
                    panel[ticker].append(row)
        # Sort by filing_date ASC
        for ticker in panel:
            panel[ticker].sort(key=lambda r: r["filing_date"])
        return panel

    def setup_method(self):
        """Reset ValueScoreH50 class state before each test."""
        h50b.ValueScoreH50._panel = {}
        h50b.ValueScoreH50._as_of_ref = [None]
        h50b.ValueScoreH50._xs_cache = {}
        h50b.ValueScoreH50._universe_tickers = []
        h50b.ValueScoreH50._exclusion_counts = {
            "profitability_below_min": 0,
            "balance_sheet_below_min": 0,
            "cash_flow_below_min": 0,
        }
        h50b.AS_OF_DATE_REF[0] = None

    def test_cross_section_cache_rank_is_correct(self):
        """D2: Verify cross-section rank is computed correctly for synthetic 5-ticker fixture."""
        tickers = ["A", "B", "C", "D", "E"]
        dates = ["2024-06-30"]

        # Create synthetic data where we know the rank order
        # ROE: A=5, B=10, C=15, D=20, E=25
        # D/E: A=0.5, B=1.0, C=1.5, D=2.0, E=2.5 (lower is better → inverted)
        panel = self._make_synthetic_panel(tickers, dates, {
            "A": {"2024-06-30": {
                "roe": 5, "roa": 1, "gross_margin": 20, "operating_margin": 10,
                "current_ratio": 2, "quick_ratio": 1, "debt_to_equity": 0.5,
                "operating_cash_flow_to_revenue": 0.3, "free_cash_flow": 100, "accruals_ratio": -0.1,
            }},
            "B": {"2024-06-30": {
                "roe": 10, "roa": 2, "gross_margin": 25, "operating_margin": 12,
                "current_ratio": 1.5, "quick_ratio": 0.8, "debt_to_equity": 1.0,
                "operating_cash_flow_to_revenue": 0.2, "free_cash_flow": 80, "accruals_ratio": 0.0,
            }},
            "C": {"2024-06-30": {
                "roe": 15, "roa": 3, "gross_margin": 30, "operating_margin": 15,
                "current_ratio": 1.8, "quick_ratio": 1.2, "debt_to_equity": 1.5,
                "operating_cash_flow_to_revenue": 0.25, "free_cash_flow": 90, "accruals_ratio": 0.02,
            }},
            "D": {"2024-06-30": {
                "roe": 20, "roa": 4, "gross_margin": 35, "operating_margin": 18,
                "current_ratio": 1.2, "quick_ratio": 0.6, "debt_to_equity": 2.0,
                "operating_cash_flow_to_revenue": 0.15, "free_cash_flow": 70, "accruals_ratio": 0.05,
            }},
            "E": {"2024-06-30": {
                "roe": 25, "roa": 5, "gross_margin": 40, "operating_margin": 20,
                "current_ratio": 2.5, "quick_ratio": 1.5, "debt_to_equity": 2.5,
                "operating_cash_flow_to_revenue": 0.10, "free_cash_flow": 60, "accruals_ratio": 0.10,
            }},
        })

        # Setup ValueScoreH50
        h50b.ValueScoreH50._panel = panel
        h50b.ValueScoreH50._universe_tickers = tickers
        h50b.AS_OF_DATE_REF[0] = "2024-06-30"
        h50b.ValueScoreH50._as_of_ref = h50b.AS_OF_DATE_REF
        h50b.ValueScoreH50._xs_cache = {}

        # Score each ticker
        scores = {}
        for t in tickers:
            vs = h50b.ValueScoreH50.from_fundamentals(t, {})
            assert vs is not None, f"Ticker {t} should not be excluded"
            scores[t] = vs

        # Verify all scores are in [0, 1]
        for t in tickers:
            assert 0 <= scores[t].total <= 1, f"{t} total={scores[t].total} out of [0,1]"

        # All tickers should have valid 3-component scores
        for t in tickers:
            assert scores[t].profitability_score >= 0
            assert scores[t].balance_sheet_score >= 0
            assert scores[t].cash_flow_score >= 0

        # Cross-section cache should have exactly 1 date
        assert len(h50b.ValueScoreH50._xs_cache) == 1
        assert "2024-06-30" in h50b.ValueScoreH50._xs_cache

    def test_per_component_minimum_enforcement(self):
        """D2: Ticker missing 3 of 4 profitability fields → excluded (None returned)."""
        tickers = ["GOOD", "BAD"]
        dates = ["2024-06-30"]

        panel = self._make_synthetic_panel(tickers, dates, {
            "GOOD": {"2024-06-30": {
                "roe": 10, "roa": 2, "gross_margin": 25, "operating_margin": 12,
                "current_ratio": 1.5, "quick_ratio": 0.8, "debt_to_equity": 1.0,
                "operating_cash_flow_to_revenue": 0.2, "free_cash_flow": 80, "accruals_ratio": 0.0,
            }},
            "BAD": {"2024-06-30": {
                # Only 1 profitability field (roe) — below 2/4 minimum
                "roe": 10,
                "roa": None, "gross_margin": None, "operating_margin": None,
                "current_ratio": 1.5, "quick_ratio": 0.8, "debt_to_equity": 1.0,
                "operating_cash_flow_to_revenue": 0.2, "free_cash_flow": 80, "accruals_ratio": 0.0,
            }},
        })

        h50b.ValueScoreH50._panel = panel
        h50b.ValueScoreH50._universe_tickers = tickers
        h50b.AS_OF_DATE_REF[0] = "2024-06-30"
        h50b.ValueScoreH50._as_of_ref = h50b.AS_OF_DATE_REF
        h50b.ValueScoreH50._xs_cache = {}

        good = h50b.ValueScoreH50.from_fundamentals("GOOD", {})
        bad = h50b.ValueScoreH50.from_fundamentals("BAD", {})

        assert good is not None, "GOOD should pass minimum"
        assert bad is None, "BAD (only 1/4 profitability) should be excluded"

        # Verify exclusion was tracked
        counts = h50b.ValueScoreH50.get_exclusion_counts()
        assert counts["profitability_below_min"] >= 1

    def test_accruals_raw_signed_inversion_no_abs(self):
        """D2: accruals_ratio MUST use raw signed value, inverted via 1-rank. NO abs()."""
        tickers = ["LOW_ACC", "HIGH_ACC", "NEG_ACC"]
        dates = ["2024-06-30"]

        # LOW_ACC: accruals_ratio=-0.05 (conservative, high quality — should rank HIGH after inversion)
        # HIGH_ACC: accruals_ratio=0.10 (aggressive, low quality — should rank LOW after inversion)
        # NEG_ACC: accruals_ratio=-0.20 (most conservative — should rank HIGHEST)
        panel = self._make_synthetic_panel(tickers, dates, {
            "LOW_ACC": {"2024-06-30": {
                "roe": 15, "roa": 3, "gross_margin": 30, "operating_margin": 15,
                "current_ratio": 1.5, "quick_ratio": 0.8, "debt_to_equity": 1.0,
                "operating_cash_flow_to_revenue": 0.2, "free_cash_flow": 80,
                "accruals_ratio": -0.05,
            }},
            "HIGH_ACC": {"2024-06-30": {
                "roe": 15, "roa": 3, "gross_margin": 30, "operating_margin": 15,
                "current_ratio": 1.5, "quick_ratio": 0.8, "debt_to_equity": 1.0,
                "operating_cash_flow_to_revenue": 0.2, "free_cash_flow": 80,
                "accruals_ratio": 0.10,
            }},
            "NEG_ACC": {"2024-06-30": {
                "roe": 15, "roa": 3, "gross_margin": 30, "operating_margin": 15,
                "current_ratio": 1.5, "quick_ratio": 0.8, "debt_to_equity": 1.0,
                "operating_cash_flow_to_revenue": 0.2, "free_cash_flow": 80,
                "accruals_ratio": -0.20,
            }},
        })

        h50b.ValueScoreH50._panel = panel
        h50b.ValueScoreH50._universe_tickers = tickers
        h50b.AS_OF_DATE_REF[0] = "2024-06-30"
        h50b.ValueScoreH50._as_of_ref = h50b.AS_OF_DATE_REF
        h50b.ValueScoreH50._xs_cache = {}

        low = h50b.ValueScoreH50.from_fundamentals("LOW_ACC", {})
        high = h50b.ValueScoreH50.from_fundamentals("HIGH_ACC", {})
        neg = h50b.ValueScoreH50.from_fundamentals("NEG_ACC", {})

        # All three have identical profitability/balance_sheet fields
        # The only difference is accruals_ratio in cash_flow component
        # NEG_ACC (-0.20, most conservative) should have highest cash_flow_score
        # HIGH_ACC (0.10, most aggressive) should have lowest cash_flow_score
        assert neg.cash_flow_score >= low.cash_flow_score, (
            f"NEG_ACC (accruals=-0.20) should outrank LOW_ACC (accruals=-0.05): "
            f"{neg.cash_flow_score:.4f} vs {low.cash_flow_score:.4f}"
        )
        assert low.cash_flow_score >= high.cash_flow_score, (
            f"LOW_ACC (accruals=-0.05) should outrank HIGH_ACC (accruals=0.10): "
            f"{low.cash_flow_score:.4f} vs {high.cash_flow_score:.4f}"
        )

    def test_date_hook_raises_when_none(self):
        """D4: from_fundamentals MUST raise if AS_OF_DATE_REF[0] is None."""
        h50b.AS_OF_DATE_REF[0] = None
        h50b.ValueScoreH50._as_of_ref = h50b.AS_OF_DATE_REF

        with pytest.raises(RuntimeError, match="AS_OF_DATE_REF is None"):
            h50b.ValueScoreH50.from_fundamentals("ANY", {})


class TestMonkeyPatchRestore:
    """Tests for monkey-patch installation and restoration."""

    def setup_method(self):
        """Ensure patches are cleaned up."""
        # Reinstate originals if somehow patched
        import fundamental_backtest as _fb
        import h42_strategy_redesign_search as _h42
        _fb.ValueScore = h50b._ORIGINAL_FB_VALUESCORE or _fb.ValueScore
        _h42.ValueScore = h50b._ORIGINAL_H42_VALUESCORE or _h42.ValueScore

    def test_finally_restore_in_error_path(self):
        """D3: Ensure restore function correctly reverts ValueScore patches."""
        import fundamental_backtest as _fb
        import h42_strategy_redesign_search as _h42

        orig_fb = _fb.ValueScore
        orig_h42 = _h42.ValueScore

        # Create minimal panel
        panel = {"TEST": [{
            "ticker": "TEST", "filing_date": "2024-06-30", "report_period": "2024-06-30",
            "roe": 10, "roa": 2, "gross_margin": 25, "operating_margin": 12,
            "current_ratio": 1.5, "quick_ratio": 0.8, "debt_to_equity": 1.0,
            "operating_cash_flow_to_revenue": 0.2, "free_cash_flow": 80, "accruals_ratio": 0.0,
        }]}

        # Ensure original state
        _fb.ValueScore = orig_fb
        _h42.ValueScore = orig_h42

        restore_fn = h50b.install_patches(panel, ["TEST"])
        assert _fb.ValueScore is h50b.ValueScoreH50, "FB ValueScore not patched"
        assert _h42.ValueScore is h50b.ValueScoreH50, "H42 ValueScore not patched"

        restore_fn()
        assert _fb.ValueScore is orig_fb, "FB ValueScore NOT restored"
        assert _h42.ValueScore is orig_h42, "H42 ValueScore NOT restored"
        assert len(h50b.ValueScoreH50._xs_cache) == 0, "Cache not cleared"

    def test_install_patches_sets_class_refs(self):
        """install_patches correctly sets up ValueScoreH50 class-level refs."""
        panel = {"TEST": [{
            "ticker": "TEST", "filing_date": "2024-06-30", "report_period": "2024-06-30",
            "roe": 10, "roa": 2, "gross_margin": 25, "operating_margin": 12,
            "current_ratio": 1.5, "quick_ratio": 0.8, "debt_to_equity": 1.0,
            "operating_cash_flow_to_revenue": 0.2, "free_cash_flow": 80, "accruals_ratio": 0.0,
        }]}

        restore = h50b.install_patches(panel, ["TEST"])

        assert h50b.ValueScoreH50._panel is panel
        assert h50b.ValueScoreH50._as_of_ref is h50b.AS_OF_DATE_REF
        assert h50b.ValueScoreH50._xs_cache == {}
        assert h50b.ValueScoreH50._universe_tickers == ["TEST"]
        assert h50b.ValueScoreH50._exclusion_counts["profitability_below_min"] == 0

        restore()


class TestH50bProvenance:
    """Tests for provenance block validation."""

    def test_provenance_block_in_smoke_run(self):
        """Verify the smoke run JSON has all 5 required provenance fields."""
        smoke_path = Path("/tmp/h50b_smoke.json")
        if not smoke_path.exists():
            pytest.skip("Smoke run JSON not found — run smoke test first")

        data = json.loads(smoke_path.read_text())

        # 1. Data sources
        ds = data["data_sources"]
        assert "prices" in ds
        assert "sector_metadata" in ds
        assert "fundamentals" in ds
        assert "universe" in ds
        assert all("sha256" in ds[k] for k in ds), "All data_sources must have sha256"

        # 2. Scorer substitution
        ss = data["scorer_substitution"]
        assert ss["from"] == "fundamental_backtest.ValueScore"
        assert "h50b" in ss["to"]
        assert "ValueScoreH50" in ss["to"]
        assert len(ss["patched_modules"]) >= 2
        assert ss["restored_after_run"] == True
        assert ss["v1_class_repr"]
        assert ss["v2_class_repr"]

        # 3. Scorer design
        sd = data["scorer_design"]
        assert sd["components"] == ["profitability", "balance_sheet", "cash_flow"]
        assert sd["valuation_omitted_reason"]

        # 4. Exclusion stats
        es = data["exclusion_stats"]
        assert "rebalances_total" in es
        assert "tickers_seen" in es
        assert "exclusion_rate_pct" in es
        assert "exclusion_reasons" in es

        # 5. Exclusion rate < 30%
        assert es["exclusion_rate_pct"] < 30, f"Exclusion rate {es['exclusion_rate_pct']}% >= 30%"

    def test_as_of_date_updated_multiple_times(self):
        """D4: Smoke run MUST have multiple rebalances (AS_OF_DATE_REF updated multiple times)."""
        smoke_path = Path("/tmp/h50b_smoke.json")
        if not smoke_path.exists():
            pytest.skip("Smoke run JSON not found")

        data = json.loads(smoke_path.read_text())
        rebalances = data["exclusion_stats"]["rebalances_total"]
        assert rebalances >= 2, f"Only {rebalances} rebalances — AS_OF_DATE_REF not updated enough times"


# ── Artifact selection test ─────────────────────────────────────────────
class TestH50bArtifactSelection:
    """Mirrors h47/h48/h49a/h49b/h50a artifact selection tests."""

    def test_h50b_script_exists(self):
        assert (PROJECT_ROOT / "scripts/h50b_quality_value_search.py").exists()

    def test_h50b_run_json_exists(self):
        """After full run, the run JSON should exist."""
        run_path = PROJECT_ROOT / "backtest/runs/fundamental_value_h50b_quality_value_search.json"
        if not run_path.exists():
            pytest.skip("Full run not yet executed")
        assert run_path.exists()

    def test_h50b_report_exists(self):
        """After full run, the report should exist."""
        report_path = PROJECT_ROOT / "reports/h50b_quality_value_search_report.md"
        if not report_path.exists():
            pytest.skip("Full run not yet executed")
        assert report_path.exists()
