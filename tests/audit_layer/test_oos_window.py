import sys
import unittest
from pathlib import Path

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE_ROOT))


class TestOOSWindow(unittest.TestCase):
    def test_uses_last_strategy_change_and_counts_trading_days(self):
        from backtest.oos_window import compute_oos_window

        changelog = [
            {"date": "2026-04-12", "account": "main", "change_type": "init"},
            {"date": "2026-04-22", "account": "main", "change_type": "parameter_adjustment"},
            {"date": "2026-04-24", "account": "lab", "change_type": "strategy_upgrade"},
        ]
        calendar = [f"2026-04-{d:02d}" for d in range(20, 31)] + [
            f"2026-05-{d:02d}" for d in range(1, 32)
        ]
        calendar = [
            d
            for d in calendar
            if d not in {"2026-04-25", "2026-04-26", "2026-05-02", "2026-05-03"}
        ]

        result = compute_oos_window(changelog, "main", "2026-05-31", calendar)

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["start"], "2026-04-23")
        self.assertEqual(result["trading_days"], 30)
        self.assertEqual(result["basis_entry_date"], "2026-04-22")

    def test_rejects_less_than_twenty_usable_days(self):
        from backtest.oos_window import compute_oos_window

        changelog = [
            {"date": "2026-04-22", "account": "main", "change_type": "parameter_adjustment"}
        ]
        calendar = ["2026-04-23", "2026-04-24", "2026-04-27"]

        result = compute_oos_window(changelog, "main", "2026-04-30", calendar)

        self.assertEqual(result["status"], "INFRA_ERROR")
        self.assertEqual(result["reason"], "INSUFFICIENT_OOS_DAYS")
        self.assertEqual(result["trading_days"], 3)


if __name__ == "__main__":
    unittest.main()
