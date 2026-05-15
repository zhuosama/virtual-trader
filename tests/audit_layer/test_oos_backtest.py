import os
import sys
import unittest

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OLD_ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path[:] = [p for p in sys.path if p not in {ROOT, OLD_ROOT}]
sys.path.insert(0, ROOT)
for module_name in list(sys.modules):
    if module_name == "backtest" or module_name.startswith("backtest."):
        del sys.modules[module_name]


def import_run_backtest():
    sys.path[:] = [p for p in sys.path if p not in {ROOT, OLD_ROOT}]
    sys.path.insert(0, ROOT)
    for module_name in list(sys.modules):
        if module_name == "backtest" or module_name.startswith("backtest."):
            del sys.modules[module_name]
    from backtest.backtest_engine import run_backtest

    return run_backtest


def static_provider():
    from backtest.market_data import StaticPriceProvider

    idx = pd.to_datetime([
        "2026-04-14",
        "2026-04-15",
        "2026-04-16",
        "2026-04-17",
        "2026-04-20",
        "2026-04-21",
        "2026-04-22",
        "2026-04-23",
        "2026-04-24",
        "2026-04-27",
        "2026-04-28",
        "2026-04-29",
        "2026-04-30",
        "2026-05-01",
    ])
    return StaticPriceProvider(
        pd.DataFrame(
            {
                "601088.SS": [30, 30, 30, 30, 30, 30.5, 31, 31, 31, 31, 31, 31, 31, 31],
                "600519.SS": [
                    1800,
                    1800,
                    1800,
                    1800,
                    1801,
                    1802,
                    1803,
                    1804,
                    1805,
                    1806,
                    1807,
                    1808,
                    1809,
                    1810,
                ],
                "000300.SS": [
                    4000,
                    4010,
                    4020,
                    4030,
                    4040,
                    4050,
                    4060,
                    4070,
                    4080,
                    4090,
                    4100,
                    4110,
                    4120,
                    4130,
                ],
            },
            index=idx,
        )
    )


class TestOOSBacktest(unittest.TestCase):
    def test_oos_window_filters_trades(self):
        run_backtest = import_run_backtest()

        trades_by_date = {
            "2026-04-15": [
                {"account": "main", "code": "601088", "action": "buy", "shares": 100, "price": 30.0}
            ],
            "2026-04-22": [
                {"account": "main", "code": "600519", "action": "buy", "shares": 50, "price": 1800.0}
            ],
            "2026-05-01": [
                {"account": "main", "code": "601088", "action": "sell", "shares": 100, "price": 31.0}
            ],
        }
        df, accounts, prices = run_backtest(
            trades_by_date,
            account_filter="all",
            oos_start="2026-04-20",
            oos_end="2026-04-30",
            price_provider=static_provider(),
        )
        main_positions = accounts["main"].positions
        self.assertIn("600519", main_positions)
        self.assertNotIn("601088", main_positions)
        self.assertGreater(len(df), 0)

    def test_backwards_compat_no_oos_with_injected_provider(self):
        run_backtest = import_run_backtest()

        trades_by_date = {
            "2026-04-15": [
                {"account": "main", "code": "601088", "action": "buy", "shares": 100, "price": 30.0}
            ],
        }
        df, accounts, prices = run_backtest(
            trades_by_date,
            account_filter="all",
            price_provider=static_provider(),
        )
        self.assertIn("601088", accounts["main"].positions)
        self.assertGreater(len(df), 0)


if __name__ == "__main__":
    unittest.main()
