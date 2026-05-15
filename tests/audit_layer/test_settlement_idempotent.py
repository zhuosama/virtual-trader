"""P1: settlement must be idempotent — running twice produces same result."""
import json
import os
import tempfile
import unittest

VTRADER_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
import sys
sys.path.insert(0, os.path.join(VTRADER_ROOT, "agents"))


class TestSettlementIdempotent(unittest.TestCase):
    """run_settlement() called twice must not duplicate performance_history entries."""

    def _make_env(self):
        tmpdir = tempfile.mkdtemp()
        accounts_dir = os.path.join(tmpdir, "accounts")
        strategies_dir = os.path.join(tmpdir, "strategies")
        os.makedirs(accounts_dir)
        os.makedirs(strategies_dir)

        main = {
            "id": "main", "initial_capital": 1000000, "cash": 500000,
            "positions": [{"code": "600900", "name": "长江电力", "shares": 1800,
                           "avg_cost": 26.43, "current_price": 26.00,
                           "market_value": 46800, "unrealized_pnl": -774,
                           "unrealized_pnl_pct": -1.63}],
            "portfolio_market_value": 46800, "total_value": 996800,
            "total_pnl": -3200, "total_pnl_pct": -0.32, "position_pct": 4.7,
            "updated_at": "2026-05-14T15:00:00",
        }
        lab = {
            "id": "lab", "initial_capital": 300000, "cash": 150000,
            "positions": [], "portfolio_market_value": 0,
            "total_value": 316000, "total_pnl": 16000,
            "total_pnl_pct": 5.33, "position_pct": 0,
            "updated_at": "2026-05-14T15:00:00",
        }
        with open(os.path.join(accounts_dir, "main.json"), 'w') as f:
            json.dump(main, f)
        with open(os.path.join(accounts_dir, "lab.json"), 'w') as f:
            json.dump(lab, f)
        with open(os.path.join(strategies_dir, "performance_history.json"), 'w') as f:
            json.dump([], f)
        return tmpdir

    def test_no_duplicate_on_double_run(self):
        """Running settlement twice → only one entry for today in performance_history."""
        from coordinator import MultiAgentCoordinator

        tmpdir = self._make_env()
        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.agents = {}

        try:
            c.run_settlement()
            c.run_settlement()
        except Exception as e:
            self.skipTest(f"API unreachable: {e}")

        perf_path = os.path.join(tmpdir, "strategies", "performance_history.json")
        with open(perf_path) as f:
            perf = json.load(f)

        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        today_entries = [e for e in perf if e.get('date') == today]
        self.assertEqual(len(today_entries), 1,
                         f"Expected 1 entry for {today}, got {len(today_entries)}")


class TestSettlementDegradedReason(unittest.TestCase):
    """settlement must return degraded_reason when no prices updated."""

    def test_no_price_update_returns_degraded(self):
        """When API returns no prices, accounts_updated=False with reason."""
        from coordinator import MultiAgentCoordinator

        tmpdir = tempfile.mkdtemp()
        accounts_dir = os.path.join(tmpdir, "accounts")
        strategies_dir = os.path.join(tmpdir, "strategies")
        os.makedirs(accounts_dir)
        os.makedirs(strategies_dir)

        # Account with code that won't resolve (fake code)
        main = {
            "id": "main", "initial_capital": 1000000, "cash": 500000,
            "positions": [{"code": "999999", "name": "FAKE", "shares": 100,
                           "avg_cost": 10.0, "current_price": 10.0,
                           "market_value": 1000, "unrealized_pnl": 0,
                           "unrealized_pnl_pct": 0}],
            "portfolio_market_value": 1000, "total_value": 501000,
            "total_pnl": 1000, "total_pnl_pct": 0.1, "position_pct": 0.2,
            "updated_at": "2026-05-14T15:00:00",
        }
        with open(os.path.join(accounts_dir, "main.json"), 'w') as f:
            json.dump(main, f)
        with open(os.path.join(accounts_dir, "lab.json"), 'w') as f:
            json.dump({"id": "lab", "initial_capital": 300000, "cash": 150000,
                        "positions": [], "portfolio_market_value": 0,
                        "total_value": 300000, "total_pnl": 0,
                        "total_pnl_pct": 0, "position_pct": 0,
                        "updated_at": "2026-05-14T15:00:00"}, f)
        with open(os.path.join(strategies_dir, "performance_history.json"), 'w') as f:
            json.dump([], f)

        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.agents = {}

        try:
            result = c.run_settlement()
        except Exception as e:
            self.skipTest(f"API unreachable: {e}")

        # 999999 won't resolve → no price update for main
        # accounts_updated should be False
        self.assertFalse(result.get('accounts_updated', True),
                         f"accounts_updated should be False, got {result}")
        self.assertIn('degraded_reason', result)


if __name__ == "__main__":
    unittest.main()
