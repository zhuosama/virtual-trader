#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression: _account_equity_series must treat performance_history <acct>_pct
as PERCENT (e.g. -0.78 == -0.78%), not as a fraction (-78%).

Compounding percent-as-fraction fabricates a ~-90% drawdown on real data, which
makes G4 protections halt ALL autonomous buys the moment canary/live runs the
gate chain against real perf history (invisible in shadow, where executed==0).
"""

import json
import os
import sys
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))

from coordinator import MultiAgentCoordinator  # noqa: E402
from execution_model import ExecutionModel  # noqa: E402


class TestAccountEquitySeries(unittest.TestCase):

    def test_pct_treated_as_percent_not_fraction(self):
        data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(data_dir, "strategies"))
        perf = [
            {"date": "2026-05-01", "main_pct": -0.78, "lab_pct": 0.1},
            {"date": "2026-05-04", "main_pct": 0.30, "lab_pct": -0.2},
            {"date": "2026-05-05", "main_pct": -0.50, "lab_pct": 0.4},
        ]
        with open(os.path.join(data_dir, "strategies", "performance_history.json"), "w") as f:
            json.dump(perf, f)

        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.data_dir = data_dir

        series = c._account_equity_series("main")
        dd = ExecutionModel._drawdown_from_equity(series)
        # Daily moves of -0.78%, +0.30%, -0.50% → max drawdown magnitude must be
        # well under 2%, NOT the ~-78% that percent-as-fraction compounding yields.
        self.assertIsNotNone(dd)
        self.assertGreater(dd, -0.02, f"fabricated drawdown {dd} (pct treated as fraction)")


if __name__ == "__main__":
    unittest.main()
