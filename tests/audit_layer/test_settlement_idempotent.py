"""P1: settlement must be idempotent — running twice produces same result."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    def _fake_market_run(self, args, capture_output=True, timeout=15):
        if "sh000300" in args[-1]:
            parts = [""] * 38
            parts[32] = "1.23"
            return SimpleNamespace(stdout=("~".join(parts) + ";").encode("gbk"))
        parts = [""] * 38
        parts[2] = "600900"
        parts[3] = "27.00"
        return SimpleNamespace(stdout=("~".join(parts) + ";").encode("gbk"))

    def test_no_duplicate_on_double_run(self):
        """Running settlement twice → only one entry for today in performance_history."""
        from coordinator import MultiAgentCoordinator

        tmpdir = self._make_env()
        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.agents = {}

        with patch("subprocess.run", side_effect=self._fake_market_run):
            c.run_settlement()
            result = c.run_settlement()

        self.assertEqual(result["ledger_validation"]["status"], "pass")
        self.assertTrue(result["ledger_validation_passed"])

        perf_path = os.path.join(tmpdir, "strategies", "performance_history.json")
        with open(perf_path) as f:
            perf = json.load(f)

        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        today_entries = [e for e in perf if e.get('date') == today]
        self.assertEqual(len(today_entries), 1,
                         f"Expected 1 entry for {today}, got {len(today_entries)}")

    def test_settlement_preserves_full_performance_history(self):
        """Settlement must upsert today's row without truncating historical entries."""
        from coordinator import MultiAgentCoordinator

        tmpdir = self._make_env()
        perf_path = os.path.join(tmpdir, "strategies", "performance_history.json")
        historical = [
            {
                "date": f"2026-04-{day:02d}",
                "main_pct": 0.1,
                "lab_pct": 0.2,
                "hs300_pct": 0.3,
                "main_beat": False,
                "lab_beat": False,
            }
            for day in range(1, 32)
        ]
        with open(perf_path, "w") as f:
            json.dump(historical, f)

        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.agents = {}

        with patch("subprocess.run", side_effect=self._fake_market_run):
            c.run_settlement()

        with open(perf_path) as f:
            perf = json.load(f)

        self.assertGreaterEqual(len(perf), len(historical) + 1)
        for entry in historical:
            self.assertIn(entry["date"], {row.get("date") for row in perf})

    def test_hs300_fetch_failure_does_not_write_placeholder_zero(self):
        """Benchmark API failure must be explicit, not an unverified hs300_pct=0."""
        from coordinator import MultiAgentCoordinator

        tmpdir = self._make_env()

        def fake_run(args, capture_output=True, timeout=15):
            if "sh000300" in args[-1]:
                raise TimeoutError("benchmark unavailable")
            return self._fake_market_run(args, capture_output=capture_output, timeout=timeout)

        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.agents = {}

        with patch("subprocess.run", side_effect=fake_run):
            result = c.run_settlement()

        perf_path = os.path.join(tmpdir, "strategies", "performance_history.json")
        with open(perf_path) as f:
            perf = json.load(f)

        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        entry = next(e for e in perf if e.get("date") == today)
        self.assertIsNone(entry.get("hs300_pct"))
        self.assertEqual(entry.get("benchmark_note"), "api_unavailable")
        self.assertIsNone(entry.get("main_beat"))
        self.assertIsNone(entry.get("lab_beat"))
        self.assertEqual(result["ledger_validation"]["status"], "fail")
        self.assertFalse(result["ledger_validation_passed"])
        self.assertIn("INV-4", result["degraded_reason"])

    def test_degraded_retry_preserves_verified_hs300_for_same_day(self):
        """A retry with benchmark API failure must not erase a verified same-day benchmark."""
        from coordinator import MultiAgentCoordinator

        tmpdir = self._make_env()

        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.agents = {}

        with patch("subprocess.run", side_effect=self._fake_market_run):
            c.run_settlement()

        def failing_benchmark(args, capture_output=True, timeout=15):
            if "sh000300" in args[-1]:
                raise TimeoutError("benchmark unavailable")
            return self._fake_market_run(args, capture_output=capture_output, timeout=timeout)

        with patch("subprocess.run", side_effect=failing_benchmark):
            c.run_settlement()

        perf_path = os.path.join(tmpdir, "strategies", "performance_history.json")
        with open(perf_path) as f:
            perf = json.load(f)

        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        entry = next(e for e in perf if e.get("date") == today)
        self.assertEqual(entry.get("hs300_pct"), 1.23)
        self.assertEqual(entry.get("benchmark_source"), "tencent_api")
        self.assertNotIn("benchmark_note", entry)
        self.assertIsNotNone(entry.get("main_beat"))
        self.assertIsNotNone(entry.get("lab_beat"))

    def test_account_write_failure_preserves_existing_json(self):
        """A partial account write failure must not corrupt the existing account file."""
        from coordinator import MultiAgentCoordinator

        tmpdir = self._make_env()
        main_path = os.path.join(tmpdir, "accounts", "main.json")
        with open(main_path) as f:
            before = json.load(f)

        original_dump = json.dump

        def fail_main_account_dump(obj, fp, *args, **kwargs):
            if obj.get("id") == "main" and os.path.basename(fp.name).startswith("main.json"):
                fp.write("{")
                raise RuntimeError("simulated partial write")
            return original_dump(obj, fp, *args, **kwargs)

        c = MultiAgentCoordinator.__new__(MultiAgentCoordinator)
        c.data_dir = tmpdir
        c.agents = {}

        with patch("subprocess.run", side_effect=self._fake_market_run):
            with patch("json.dump", side_effect=fail_main_account_dump):
                with self.assertRaises(RuntimeError):
                    c.run_settlement()

        with open(main_path) as f:
            after = json.load(f)
        self.assertEqual(after, before)


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
