#!/usr/bin/env python3
"""Tests for the H53-FIX cross-family composite paper-only forward IC monitor.

Guards the research-only / no-fabrication contract and verifies the monitor
reproduces the registration baseline on the committed data panel.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h53fix_ic_forward_monitor as mon  # noqa: E402


class TestH53FixICForwardMonitor(unittest.TestCase):
    def test_snapshot_reproduces_registration_baseline(self):
        snap = mon.compute_snapshot()
        self.assertNotIn("error", snap)
        base = mon.BASELINE
        # Composite IR / ρ̄ must match the spike baseline to 3 dp on the
        # committed panel (proves the monitor recomputes, not hardcodes).
        self.assertAlmostEqual(snap["composite_ir"], base["composite_ir"], places=3)
        self.assertAlmostEqual(snap["rho_mean"], base["rho_mean"], places=3)
        self.assertEqual(snap["n_common_dates"], base["n_common_dates"])

    def test_four_full_coverage_legs(self):
        snap = mon.compute_snapshot()
        self.assertEqual(len(snap["legs"]), 4)
        for leg in snap["legs"]:
            self.assertNotIn("error", leg)
            self.assertGreaterEqual(leg["n_obs"], 300)

    def test_below_threshold_signal_negative(self):
        snap = mon.compute_snapshot()
        self.assertFalse(snap["passes_threshold"])
        self.assertLess(abs(snap["composite_ir"]), 0.5)

    def test_payload_is_research_only_and_paper_only(self):
        payload = mon.build_payload()
        self.assertEqual(payload["status"], "RESEARCH_ONLY")
        self.assertTrue(payload["paper_only"])

    def test_no_fabricated_pnl_fields(self):
        """A factor-IC monitor must NOT carry strategy P&L it does not have."""
        payload = mon.build_payload()
        latest = payload["latest"]
        for forbidden in ("total_return", "excess_return", "max_drawdown",
                           "trade_count", "pnl"):
            self.assertNotIn(forbidden, latest)
        self.assertIn("do_not_fabricate_pnl_or_drawdown", payload["hard_prohibitions"])
        self.assertIn("do_not_promote_to_active_json", payload["hard_prohibitions"])

    def test_promotion_blockers_present(self):
        payload = mon.build_payload()
        self.assertTrue(payload["promotion_blockers"])
        joined = " ".join(payload["promotion_blockers"]).lower()
        self.assertIn("signal_negative", joined.replace(" ", "_"))


if __name__ == "__main__":
    unittest.main()
