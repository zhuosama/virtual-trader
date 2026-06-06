#!/usr/bin/env python3
"""Unit tests for value_account/account_manager.py

Covers: buy, sell, rebalance, BenchmarkTracker, serialization round-trip
"""

import json, math, os, sys, tempfile, unittest

sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader/value_account"))
from account_manager import (
    ValuePosition, ValueAccount, RebalanceScheduler, BenchmarkTracker,
)


class TestValueAccount(unittest.TestCase):
    """Tests for ValueAccount core operations."""

    def setUp(self):
        self.acct = ValueAccount(
            name="test", cash=500000.0, initial_capital=500000.0,
        )

    # ---- buy tests ----

    def test_buy_simple(self):
        trade = self.acct.buy("AAPL", "AAPL", "Apple Inc.", 150.0, 100, "2026-01-15")
        self.assertEqual(trade["status"], "success")
        self.assertEqual(trade["action"], "buy")
        # amount=15000, commission=max(15000*0.0003,5)=5, xfer=15000*0.00002=0.30 → total=15005.30
        self.assertAlmostEqual(trade["total_cost"], 15005.30, places=2)
        self.assertEqual(self.acct.position_count, 1)
        self.assertAlmostEqual(self.acct.cash, 500000 - 15005.30, places=2)

    def test_buy_zero_shares_rejected(self):
        trade = self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 0, "2026-01-15")
        self.assertEqual(trade["status"], "error")

    def test_buy_negative_price_rejected(self):
        trade = self.acct.buy("AAPL", "AAPL", "Apple", -10.0, 100, "2026-01-15")
        self.assertEqual(trade["status"], "error")

    def test_buy_insufficient_cash_adjusts(self):
        """Buying too many shares should auto-adjust to what cash allows."""
        # 200 shares at 3000 = 600k > 500k cash; should adjust down
        trade = self.acct.buy("AAPL", "AAPL", "Apple", 3000.0, 200, "2026-01-15")
        # Will be adjusted to affordable shares
        self.assertEqual(trade["status"], "success")
        self.assertLess(trade["shares"], 200)
        self.assertGreater(self.acct.cash, 0)

    def test_buy_insufficient_cash_rejected_if_zero(self):
        """If even 1 lot can't be bought, reject."""
        self.acct.cash = 100  # tiny cash
        trade = self.acct.buy("AAPL", "AAPL", "Apple", 5000.0, 100, "2026-01-15")
        self.assertEqual(trade["status"], "error")
        self.assertEqual(trade.get("reason"), "insufficient_cash")

    def test_buy_average_down(self):
        """Buying same ticker twice updates avg_cost correctly."""
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        self.acct.buy("AAPL", "AAPL", "Apple", 100.0, 200, "2026-01-20")
        self.assertEqual(self.acct.position_count, 1)
        pos = self.acct.positions["AAPL"]
        self.assertEqual(pos.shares, 300)
        # avg_cost = (150*100 + 100*200) / 300 = 35000/300 = 116.67
        self.assertAlmostEqual(pos.avg_cost, 116.6667, places=2)

    # ---- sell tests ----

    def test_sell_full(self):
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        trade = self.acct.sell("AAPL", 200.0, 100, "2026-01-30")
        self.assertEqual(trade["status"], "success")
        # net = 20000 - max(20000*0.0003,5)=6 - 20000*0.001=20 - 20000*0.00002=0.40 = 19973.60
        # pnl = 19973.60 - 15000 = 4973.60
        self.assertAlmostEqual(trade["pnl"], 4973.60, places=2)
        self.assertEqual(self.acct.position_count, 0)
        self.assertNotIn("AAPL", self.acct.positions)

    def test_sell_partial(self):
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        trade = self.acct.sell("AAPL", 200.0, 50, "2026-01-30")
        self.assertEqual(trade["status"], "success")
        self.assertEqual(self.acct.position_count, 1)
        self.assertEqual(self.acct.positions["AAPL"].shares, 50)

    def test_sell_nonexistent(self):
        trade = self.acct.sell("MISSING", 100.0, 10, "2026-01-15")
        self.assertIsNone(trade)

    def test_sell_invalid_params(self):
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        trade = self.acct.sell("AAPL", -1.0, 10, "2026-01-15")
        self.assertEqual(trade["status"], "error")

    def test_sell_updates_realized_pnl(self):
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        self.acct.sell("AAPL", 180.0, 100, "2026-01-30")
        self.assertGreater(self.acct.realized_pnl, 0)

    # ---- value methods ----

    def test_market_value_method(self):
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        mv = self.acct.market_value({"AAPL": 200.0})
        self.assertAlmostEqual(mv, 20000.0, places=2)

    def test_market_value_at_cost(self):
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        mv = self.acct.market_value_at_cost()
        self.assertAlmostEqual(mv, 15000.0, places=2)

    def test_total_value(self):
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        tv = self.acct.total_value({"AAPL": 200.0})
        expected = self.acct.cash + 20000.0
        self.assertAlmostEqual(tv, expected, places=2)

    def test_total_return(self):
        ret = self.acct.total_return({})
        self.assertAlmostEqual(ret, 0.0, places=4)
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        ret2 = self.acct.total_return({"AAPL": 180.0})
        # return = (cash + 180*100) / 500000 - 1
        expected = (self.acct.cash + 18000) / 500000 - 1
        self.assertAlmostEqual(ret2, expected, places=4)

    def test_update_prices(self):
        self.acct.update_prices({"AAPL": 200.0, "MSFT": 400.0})
        self.assertEqual(self.acct._last_prices, {"AAPL": 200.0, "MSFT": 400.0})

    # ---- derived properties ----

    def test_total_fees(self):
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        self.acct.sell("AAPL", 200.0, 100, "2026-01-30")
        fees = self.acct.total_fees_paid
        self.assertGreater(fees, 0)

    def test_win_rate(self):
        self.assertIsNone(self.acct.win_rate)

        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        self.acct.sell("AAPL", 200.0, 100, "2026-01-30")
        self.assertEqual(self.acct.win_rate, 1.0)

        self.acct.buy("MSFT", "MSFT", "Microsoft", 400.0, 50, "2026-02-01")
        self.acct.sell("MSFT", 350.0, 50, "2026-02-15")
        self.assertEqual(self.acct.win_rate, 0.5)

    def test_profit_factor(self):
        self.assertIsNone(self.acct.profit_factor)

        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15")
        self.acct.sell("AAPL", 200.0, 100, "2026-01-30")
        # One winning trade, no losers
        self.assertEqual(self.acct.profit_factor, float("inf"))


class TestRebalance(unittest.TestCase):
    """Tests for rebalance logic."""

    def setUp(self):
        self.acct = ValueAccount(
            name="test", cash=500000.0, initial_capital=500000.0,
        )
        # Pre-fill 3 positions
        self.acct.buy("AAPL", "AAPL", "Apple", 150.0, 200, "2026-01-15")
        self.acct.buy("MSFT", "MSFT", "Microsoft", 400.0, 100, "2026-01-15")
        self.acct.buy("GOOGL", "GOOGL", "Alphabet", 140.0, 300, "2026-01-15")
        # Values: AAPL=200*150=30000, MSFT=100*400=40000, GOOGL=300*140=42000
        # Total market = 112000, cash ≈ 388000, total ≈ 500000

    def test_rebalance_equal_weights(self):
        """Equal 3-way rebalance."""
        prices = {"AAPL": 150.0, "MSFT": 400.0, "GOOGL": 140.0}
        trades = self.acct.rebalance(
            {"AAPL": 0.25, "MSFT": 0.25, "GOOGL": 0.25},
            prices, "2026-02-01",
        )
        # Should have some trades
        self.assertGreater(len(trades), 0)
        self.assertIn("2026-02-01", self.acct.rebalance_dates)

    def test_rebalance_sell_excluded(self):
        """Rebalance removes a position not in target."""
        prices = {"AAPL": 150.0, "MSFT": 400.0, "GOOGL": 140.0}
        trades = self.acct.rebalance(
            {"AAPL": 0.5, "MSFT": 0.5},  # GOOGL excluded
            prices, "2026-02-01",
        )
        self.assertNotIn("GOOGL", self.acct.positions)

    def test_rebalance_within_tolerance_no_trade(self):
        """If weights are already close to target, no trades."""
        # Make all 3 equal value
        prices = {"AAPL": 150.0, "MSFT": 400.0, "GOOGL": 140.0}
        # First equalize via rebalance
        self.acct.rebalance(
            {"AAPL": 1/3, "MSFT": 1/3, "GOOGL": 1/3},
            prices, "2026-02-01",
        )
        # Clear rebalance dates and re-run — should be within tolerance
        self.acct.rebalance_dates = []
        trades = self.acct.rebalance(
            {"AAPL": 1/3, "MSFT": 1/3, "GOOGL": 1/3},
            prices, "2026-02-02",
        )
        self.assertEqual(len(trades), 0)  # No trades needed

    def test_rebalance_invalid_weights(self):
        with self.assertRaises(ValueError):
            self.acct.rebalance(
                {"AAPL": 0.6, "MSFT": 0.6},  # sum > 1
                {"AAPL": 150.0, "MSFT": 400.0}, "2026-02-01",
            )


class TestBenchmarkTracker(unittest.TestCase):
    """Tests for BenchmarkTracker."""

    def test_record_and_latest(self):
        bt = BenchmarkTracker("SPY")
        r = bt.record("2026-01-15", 510000, 500000, 10500, 10000)
        self.assertEqual(r["date"], "2026-01-15")
        self.assertAlmostEqual(r["account_return"], 0.02, places=4)

        latest = bt.latest()
        self.assertEqual(latest["date"], "2026-01-15")

    def test_summary_empty(self):
        bt = BenchmarkTracker()
        self.assertEqual(bt.summary(), {"status": "no_records"})

    def test_summary_multiple(self):
        bt = BenchmarkTracker()
        bt.record("2026-01-15", 510000, 500000, 10500, 10000)
        bt.record("2026-01-16", 515000, 500000, 10600, 10000)
        bt.record("2026-01-17", 508000, 500000, 10400, 10000)
        summary = bt.summary()
        self.assertEqual(summary["total_days"], 3)

    def test_to_dataframe(self):
        bt = BenchmarkTracker()
        bt.record("2026-01-15", 510000, 500000, 10500, 10000)
        df = bt.to_dataframe()
        self.assertEqual(len(df), 1)
        self.assertEqual(df[0]["account_value"], 510000)


class TestRebalanceScheduler(unittest.TestCase):
    """Tests for RebalanceScheduler."""

    def test_no_last_rebalance_returns_true(self):
        s = RebalanceScheduler("monthly")
        self.assertTrue(s.should_rebalance("2026-03-15"))

    def test_monthly_same_month(self):
        s = RebalanceScheduler("monthly", "2026-03-01")
        self.assertFalse(s.should_rebalance("2026-03-15"))

    def test_monthly_next_month(self):
        s = RebalanceScheduler("monthly", "2026-03-01")
        self.assertTrue(s.should_rebalance("2026-04-05"))

    def test_quarterly_within_quarter(self):
        s = RebalanceScheduler("quarterly", "2026-01-15")
        self.assertFalse(s.should_rebalance("2026-02-20"))

    def test_quarterly_next_quarter(self):
        s = RebalanceScheduler("quarterly", "2026-01-15")
        self.assertTrue(s.should_rebalance("2026-05-01"))

    def test_weekly_same_week(self):
        s = RebalanceScheduler("weekly", "2026-03-02")  # Monday W9
        self.assertFalse(s.should_rebalance("2026-03-03"))  # Same W9

    def test_invalid_frequency(self):
        with self.assertRaises(ValueError):
            RebalanceScheduler("daily")


class TestSerialization(unittest.TestCase):
    """Tests for to_dict / from_dict round-trip."""

    def test_round_trip_empty(self):
        acct = ValueAccount(name="test", cash=500000, initial_capital=500000)
        d = acct.to_dict()
        acct2 = ValueAccount.from_dict(d)
        self.assertEqual(acct2.name, "test")
        self.assertEqual(acct2.cash, 500000)

    def test_round_trip_with_positions(self):
        acct = ValueAccount(name="test", cash=500000, initial_capital=500000)
        acct.buy("AAPL", "AAPL", "Apple", 150.0, 100, "2026-01-15",
                 value_metrics={"roe": 45.0, "fcf_yield": 5.0})
        d = acct.to_dict()
        acct2 = ValueAccount.from_dict(d)
        self.assertEqual(acct2.position_count, 1)
        pos = acct2.positions["AAPL"]
        self.assertEqual(pos.roe, 45.0)
        self.assertEqual(pos.fcf_yield, 5.0)

    def test_round_trip_preserves_pnl_and_win_rate(self):
        """H18: round-trip must preserve realized_pnl, win_rate, profit_factor."""
        acct = ValueAccount(name="test", cash=500000, initial_capital=500000)

        # Execute 3 trades: 2 wins, 1 loss
        acct.buy("AAPL", "AAPL", "Apple", 100.0, 100, "2026-01-15")
        acct.sell("AAPL", 120.0, 100, "2026-02-01")  # win +2000

        acct.buy("MSFT", "MSFT", "Microsoft", 400.0, 50, "2026-02-15")
        acct.sell("MSFT", 440.0, 50, "2026-03-01")   # win +2000

        acct.buy("GOOGL", "GOOGL", "Alphabet", 140.0, 100, "2026-03-15")
        acct.sell("GOOGL", 130.0, 100, "2026-04-01")  # loss -1000

        # Snapshot invariants
        rp_before = acct.realized_pnl
        wr_before = acct.win_rate
        pf_before = acct.profit_factor
        trade_count_before = len(acct.trade_history)
        fee_before = acct.total_fees_paid

        # Round-trip
        d = acct.to_dict()
        acct2 = ValueAccount.from_dict(d)

        # All invariants preserved
        self.assertAlmostEqual(acct2.realized_pnl, rp_before, places=2,
            msg="realized_pnl must survive round-trip")
        self.assertEqual(acct2.win_rate, wr_before,
            msg="win_rate must survive round-trip")
        self.assertEqual(acct2.profit_factor, pf_before,
            msg="profit_factor must survive round-trip")
        self.assertEqual(len(acct2.trade_history), trade_count_before,
            msg="trade_history length must be preserved")
        self.assertAlmostEqual(acct2.total_fees_paid, fee_before, places=2,
            msg="total_fees_paid must survive round-trip")
        self.assertGreater(rp_before, 0,
            msg="sanity: realized_pnl should be positive (2 wins > 1 loss)")


if __name__ == "__main__":
    unittest.main()
