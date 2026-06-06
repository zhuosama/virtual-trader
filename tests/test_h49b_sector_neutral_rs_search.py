#!/usr/bin/env python3
"""Tests for H49b Sector-Neutral Relative Strength Search."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backtest" / "experiments"))

import validate_hxx_artifacts as hxx  # noqa: E402
from h49b_sector_neutral_rs_search import (  # noqa: E402
    load_sector_map,
    build_h49b_overlays,
    build_h49b_param_grid,
    H49bOverlay,
    H49bParams,
    SECTOR_MAX_WEIGHT_VALUES,
    MIN_SECTORS_VALUES,
    passes_h49b_overlay,
    compute_provenance,
)


class TestH49bSectorNeutralRSSearch(unittest.TestCase):
    def test_sector_map_loads_all_481_tickers(self):
        sm = load_sector_map()
        self.assertEqual(len(sm), 481)
        # Spot-check a few
        self.assertEqual(sm["000001.SZ"], "银行")
        self.assertEqual(sm["600519.SS"], "食品饮料")
        self.assertNotIn("__UNMAPPED__", sm.values())

    def test_sector_map_fails_on_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                load_sector_map(Path(tmp) / "nonexistent.csv")

    def test_h49b_overlays_include_all_h42_plus_4_new(self):
        ovs = build_h49b_overlays()
        names = {o.name for o in ovs}
        # H42 overlays (18)
        self.assertIn("none", names)
        self.assertIn("rel20_ge_0", names)
        self.assertIn("price_gt_ma20", names)
        # H49b new overlays
        self.assertIn("intra_sector_rs20", names)
        self.assertIn("intra_sector_rs60", names)
        self.assertIn("intra_sector_rs20_and_rel60_ge_0", names)
        self.assertIn("intra_sector_rs60_and_rel20_ge_0", names)
        # Total: 18 + 4 = 22
        self.assertEqual(len(ovs), 22)

    def test_intra_sector_rs_overlay_has_correct_fields(self):
        ovs = build_h49b_overlays()
        rs20 = next(o for o in ovs if o.name == "intra_sector_rs20")
        self.assertEqual(rs20.intra_sector_rs_window, 20)
        self.assertTrue(rs20.intra_sector_rs_top_quartile)
        self.assertIsNone(rs20.intra_sector_rs_and_rel_min)

        combo = next(o for o in ovs if o.name == "intra_sector_rs20_and_rel60_ge_0")
        self.assertEqual(combo.intra_sector_rs_window, 20)
        self.assertTrue(combo.intra_sector_rs_top_quartile)
        self.assertEqual(combo.intra_sector_rs_and_rel_min, 0.0)
        self.assertEqual(combo.intra_sector_rs_and_rel_window, 60)

    def test_param_grid_includes_sector_axes(self):
        grid = build_h49b_param_grid()
        # Should be H42 base * 5 * 3
        self.assertGreater(len(grid), 0)

        smw_values = set(p.sector_max_weight_pct for p in grid)
        ms_values = set(p.min_sectors_in_portfolio for p in grid)
        self.assertEqual(smw_values, set(SECTOR_MAX_WEIGHT_VALUES))
        self.assertEqual(ms_values, set(MIN_SECTORS_VALUES))

        # First element has cap=0.20, min_sect=1
        self.assertEqual(grid[0].sector_max_weight_pct, 0.20)
        self.assertEqual(grid[0].min_sectors_in_portfolio, 1)

    def test_h49b_overlay_to_h42_conversion(self):
        ov = H49bOverlay("rel20_ge_0", rel20_min=0.0)
        h42_ov = ov.to_h42_overlay()
        self.assertEqual(h42_ov.name, "rel20_ge_0")
        self.assertEqual(h42_ov.rel20_min, 0.0)

    def test_h49b_params_to_dict_includes_sector_fields(self):
        p = H49bParams(
            top_n=8, max_position_pct=0.08, stop_loss_pct=0.08,
            take_profit_pct=0.22, quality_filter=0.30, rebalance_freq_days=63,
            sector_max_weight_pct=0.25, min_sectors_in_portfolio=5,
        )
        d = p.to_dict()
        self.assertEqual(d["sector_max_weight_pct"], 0.25)
        self.assertEqual(d["min_sectors_in_portfolio"], 5)

    def test_provenance_has_all_three_sources(self):
        prov = compute_provenance()
        ds = prov["data_sources"]
        # Prices
        self.assertEqual(ds["prices"]["task"], "h47")
        self.assertTrue(ds["prices"]["file"].endswith("prices_h47_tushare_qfq_candidate.csv"))
        self.assertEqual(len(ds["prices"]["sha256"]), 64)
        # Sector metadata
        self.assertEqual(ds["sector_metadata"]["task"], "h49a")
        self.assertTrue(ds["sector_metadata"]["file"].endswith("sector_metadata_sw_l1.csv"))
        self.assertEqual(len(ds["sector_metadata"]["sha256"]), 64)
        self.assertEqual(ds["sector_metadata"]["snapshot_date"], "2026-05-23")
        self.assertEqual(ds["sector_metadata"]["provider"], "tushare:index_classify+index_member")
        # Universe
        self.assertTrue(ds["universe"]["file"].endswith("universe_h30_candidate.jsonl"))
        self.assertEqual(len(ds["universe"]["sha256"]), 64)

    def test_d6_scoring_ranks_beat_hs300_first(self):
        """Verify D6: primary sort by beat_HS300_windows desc."""
        from h49b_sector_neutral_rs_search import score_candidate_h49b

        # Candidate A: 3/5 beat windows, excess 0.05
        # Candidate B: 1/5 beat windows, excess 0.20
        a = {
            "passes_acceptance_gate": False,
            "gate_metrics": {
                "beat_hs300_windows": 3,
                "deploy_excess_return": 0.05,
                "positive_windows": 4,
                "unblocked_windows": 4,
                "execution_blocked": False,
                "warnings_count": 0,
                "deploy_streak": 2,
                "deploy_max_drawdown": -0.05,
                "deploy_trades": 30,
            },
            "deploy_window": {
                "metrics": {
                    "total_return": 0.10,
                    "sharpe_ratio": 1.2,
                    "excess_return": 0.05,
                }
            },
        }
        b = dict(a)
        b["gate_metrics"] = dict(a["gate_metrics"])
        b["gate_metrics"]["beat_hs300_windows"] = 1
        b["gate_metrics"]["deploy_excess_return"] = 0.20
        b["deploy_window"] = dict(a["deploy_window"])
        b["deploy_window"]["metrics"] = dict(a["deploy_window"]["metrics"])
        b["deploy_window"]["metrics"]["excess_return"] = 0.20

        score_a = score_candidate_h49b(a)
        score_b = score_candidate_h49b(b)
        # A should rank higher (lower score) because beat=3 > beat=1
        self.assertLess(score_a, score_b)

    def test_d6_tiebreaker_is_excess_return(self):
        """Verify D6: tiebreak by deploy_excess_return desc when beat equal."""
        from h49b_sector_neutral_rs_search import score_candidate_h49b

        def make_cand(beat, excess):
            return {
                "passes_acceptance_gate": False,
                "gate_metrics": {
                    "beat_hs300_windows": beat,
                    "deploy_excess_return": excess,
                    "positive_windows": 3,
                    "unblocked_windows": 3,
                    "execution_blocked": False,
                    "warnings_count": 0,
                    "deploy_streak": 2,
                    "deploy_max_drawdown": -0.05,
                    "deploy_trades": 30,
                },
                "deploy_window": {
                    "metrics": {
                        "total_return": 0.10,
                        "sharpe_ratio": 1.0,
                        "excess_return": excess,
                    }
                },
            }

        a = make_cand(2, 0.10)
        b = make_cand(2, 0.05)
        score_a = score_candidate_h49b(a)
        score_b = score_candidate_h49b(b)
        # A ranks higher (excess 0.10 > 0.05)
        self.assertLess(score_a, score_b)

    def test_h49b_artifact_selection(self):
        """Mirrors test_h42_artifact_selection pattern — validator for h49b."""
        checks = hxx.run_checks("h49b")
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "h49b")
        # Will fail until full run completes; test verifies code is wired
        # (pass/fail depends on artifacts existing)

    def test_registered_artifacts_include_h49b(self):
        """Verify h49b is in the artifact_specs dict."""
        specs = hxx.artifact_specs()
        self.assertIn("h49b", specs)
        spec = specs["h49b"]
        self.assertEqual(spec.name, "h49b")
        self.assertIn("h49b", str(spec.json_path))
        self.assertIn("h49b", str(spec.report_path))


if __name__ == "__main__":
    unittest.main()
