#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2 fix: honest position reporting in the pre-market output.

The pre-market report used to print only the plan's `total_position` ceiling
(e.g. "目标仓位: 55%") while the actual deployed target_weights summed to ~15%.
That is the exact "displayed value != executed value" defect AGENTS.md §
Success Honesty forbids. _generate_approved_output must additionally surface
the *actual* per-account deployed weight and warn when it falls materially
short of the ceiling.

Built via __new__ to avoid coordinator __init__ I/O + agent construction.
"""

import os
import sys
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from coordinator import MultiAgentCoordinator  # noqa: E402


def _coord():
    return MultiAgentCoordinator.__new__(MultiAgentCoordinator)


MARKET = {"market_tone": "neutral", "sector_strength": [], "risk_signals": []}
PLAN = {"position_sizing": {"total_position": 0.55}, "actions": []}
VALID = {"decision": "APPROVED", "warnings": []}


class TestHonestPositionDisplay(unittest.TestCase):

    def test_shows_actual_deployed_alongside_ceiling(self):
        tw = {
            "main": {"600900": 0.08, "000333": 0.08, "000651": 0.08},  # 0.24
            "lab": {"300124": 0.16},                                    # 0.16
        }
        out = _coord()._generate_approved_output(MARKET, PLAN, VALID, tw)
        self.assertIn("55", out)            # ceiling still shown
        self.assertIn("实际部署", out)       # actual deployment line added
        self.assertIn("24", out)            # main deployed ~24%
        self.assertIn("16", out)            # lab deployed ~16%

    def test_warns_when_deployed_far_below_ceiling(self):
        tw = {"main": {"600900": 0.08, "000333": 0.08, "000651": 0.08},
              "lab": {"300124": 0.16}}
        out = _coord()._generate_approved_output(MARKET, PLAN, VALID, tw)
        # gap 0.55 - 0.24 = 0.31 > 5pp -> idle-cash warning
        self.assertIn("⚠️", out)
        self.assertIn("闲置", out)

    def test_no_idle_warning_when_deployed_near_ceiling(self):
        tw = {"main": {"a": 0.30, "b": 0.22}, "lab": {"c": 0.53}}  # 0.52 / 0.53
        out = _coord()._generate_approved_output(MARKET, PLAN, VALID, tw)
        self.assertIn("实际部署", out)
        self.assertNotIn("闲置", out)

    def test_backward_compatible_without_target_weights(self):
        # Old 3-arg call must keep working and not emit the deployment line.
        out = _coord()._generate_approved_output(MARKET, PLAN, VALID)
        self.assertIn("目标仓位", out)
        self.assertNotIn("实际部署", out)


if __name__ == "__main__":
    unittest.main()
