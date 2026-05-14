import unittest
from datetime import date
import sys, os
sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))

class TestOOSBacktest(unittest.TestCase):
    def test_oos_window_filters_trades(self):
        """传 oos_start/oos_end 时只跑该窗口内的交易"""
        from backtest.backtest_engine import run_backtest
        trades_by_date = {
            "2026-04-15": [{"account": "main", "code": "601088", "action": "buy", "shares": 100, "price": 30.0}],
            "2026-04-22": [{"account": "main", "code": "600519", "action": "buy", "shares": 50, "price": 1800.0}],
            "2026-05-01": [{"account": "main", "code": "601088", "action": "sell", "shares": 100, "price": 31.0}],
        }
        df, accounts, prices = run_backtest(
            trades_by_date,
            account_filter="all",
            oos_start="2026-04-20",
            oos_end="2026-04-30",
        )
        main_positions = accounts["main"].positions
        self.assertIn("600519", main_positions)
        self.assertNotIn("601088", main_positions)

    def test_backwards_compat_no_oos(self):
        """不传 oos_start/oos_end 时行为不变"""
        from backtest.backtest_engine import run_backtest
        trades_by_date = {
            "2026-04-15": [{"account": "main", "code": "601088", "action": "buy", "shares": 100, "price": 30.0}],
        }
        df, accounts, prices = run_backtest(trades_by_date, account_filter="all")
        self.assertIn("601088", accounts["main"].positions)

if __name__ == "__main__":
    unittest.main()
