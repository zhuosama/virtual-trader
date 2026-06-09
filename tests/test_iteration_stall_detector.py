#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S4 fix: surface a stalled self-iteration loop.

After the 5/29 key rotation the audit LLM path is healthy again, but the
strategy_maintainer can sit at NO_CHANGES for weeks while the main account
underperforms the benchmark — and that month-long stall went unnoticed
because NO_CHANGES (unlike BLOCKED/PENDING_RETRY/AUTO_REJECT) emitted no
warning. detect_iteration_stall is a pure helper the post-market workflow
calls to raise that warning.
"""

import os
import sys
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from strategy_maintainer import detect_iteration_stall  # noqa: E402


def _perf(main_pct, hs_pct, n):
    return [{"main_pct": main_pct, "hs300_pct": hs_pct} for _ in range(n)]


class TestDetectIterationStall(unittest.TestCase):

    def test_alerts_when_stalled_and_underperforming(self):
        decisions = ["NO_CHANGES"] * 10
        perf = _perf(-0.10, 0.20, 10)  # main loses, benchmark gains
        msg = detect_iteration_stall(decisions, perf, min_stall_days=10, lookback=10)
        self.assertIsNotNone(msg)
        self.assertIn("停滞", msg)

    def test_no_alert_when_a_recent_change_happened(self):
        decisions = ["NO_CHANGES"] * 9 + ["AUTO_REJECT"]
        perf = _perf(-0.10, 0.20, 10)
        self.assertIsNone(
            detect_iteration_stall(decisions, perf, min_stall_days=10, lookback=10)
        )

    def test_no_alert_when_outperforming(self):
        decisions = ["NO_CHANGES"] * 10
        perf = _perf(0.30, 0.05, 10)  # main beats benchmark -> stall is fine
        self.assertIsNone(
            detect_iteration_stall(decisions, perf, min_stall_days=10, lookback=10)
        )

    def test_no_alert_when_insufficient_history(self):
        decisions = ["NO_CHANGES"] * 5
        perf = _perf(-0.10, 0.20, 5)
        self.assertIsNone(
            detect_iteration_stall(decisions, perf, min_stall_days=10, lookback=10)
        )

    def test_ignores_empty_decision_strings(self):
        # Older workflow records with no audit_decision must not break counting.
        decisions = [""] * 3 + ["NO_CHANGES"] * 10
        perf = _perf(-0.05, 0.10, 10)
        msg = detect_iteration_stall(decisions, perf, min_stall_days=10, lookback=10)
        self.assertIsNotNone(msg)


if __name__ == "__main__":
    unittest.main()
