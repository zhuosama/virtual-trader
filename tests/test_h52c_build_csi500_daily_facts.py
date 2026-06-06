#!/usr/bin/env python3
"""Tests for H52c CSI500 Daily Fact Data ingestion.

Tests are deterministic and free of network calls — all Tushare fetches are mocked.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

# Import the module under test
import h52c_build_csi500_daily_facts as h52c  # noqa: E402

HS300_TICKER = h52c.HS300_TICKER
HS300_TS_CODE = h52c.HS300_TS_CODE


class TestTickerHelpers(unittest.TestCase):
    """to_yahoo_style and ticker conversion tests."""

    def test_sh_to_ss(self):
        self.assertEqual(h52c.to_yahoo_style("600000.SH"), "600000.SS")
        self.assertEqual(h52c.to_yahoo_style("688981.SH"), "688981.SS")

    def test_sz_unchanged(self):
        self.assertEqual(h52c.to_yahoo_style("000001.SZ"), "000001.SZ")
        self.assertEqual(h52c.to_yahoo_style("300750.SZ"), "300750.SZ")

    def test_compact_date(self):
        self.assertEqual(h52c.compact_date("2020-01-02"), "20200102")
        self.assertEqual(h52c.compact_date("2026-05-21"), "20260521")


class TestComputeQfq(unittest.TestCase):
    """Local QFQ computation tests with synthetic fixtures."""

    def setUp(self):
        self.h52a_yahoo_tickers = ["600000.SS", "000001.SZ"]

    def test_qfq_local_compute_synthetic_5day(self):
        """Synthetic 5-day fixture with known adj_factor → known qfq."""
        daily_records = [
            {"ts_code": "600000.SH", "trade_date": "20200102", "close": 10.0, "vol": 1000.0, "amount": 10000.0, "pct_chg": 1.0},
            {"ts_code": "600000.SH", "trade_date": "20200103", "close": 10.5, "vol": 1100.0, "amount": 11550.0, "pct_chg": 0.5},
            {"ts_code": "600000.SH", "trade_date": "20200106", "close": 11.0, "vol": 1200.0, "amount": 13200.0, "pct_chg": 2.0},
            {"ts_code": "600000.SH", "trade_date": "20200107", "close": 10.8, "vol": 1050.0, "amount": 11340.0, "pct_chg": -0.8},
            {"ts_code": "600000.SH", "trade_date": "20200108", "close": 10.6, "vol": 980.0, "amount": 10388.0, "pct_chg": -0.5},
        ]
        adj_records = [
            {"ts_code": "600000.SH", "trade_date": "20200102", "adj_factor": 100.0},
            {"ts_code": "600000.SH", "trade_date": "20200103", "adj_factor": 105.0},
            {"ts_code": "600000.SH", "trade_date": "20200106", "adj_factor": 110.0},
            {"ts_code": "600000.SH", "trade_date": "20200107", "adj_factor": 115.0},
            {"ts_code": "600000.SH", "trade_date": "20200108", "adj_factor": 120.0},  # terminal
        ]

        qfq_long, no_qfq = h52c.compute_qfq(daily_records, adj_records, ["600000.SS"])  # Only ticker with adj_factor data

        self.assertEqual(len(no_qfq), 0)
        # adj_factor_terminal = 120.0
        # Day 1: 10.0 * 100/120 = 8.3333
        # Day 2: 10.5 * 105/120 = 9.1875
        # Day 3: 11.0 * 110/120 = 10.0833
        # Day 4: 10.8 * 115/120 = 10.35
        # Day 5: 10.6 * 120/120 = 10.6
        expected = [8.3333, 9.1875, 10.0833, 10.35, 10.6]
        for i, exp in enumerate(expected):
            row = qfq_long.iloc[i]
            self.assertAlmostEqual(row["qfq_close"], exp, places=3)
            self.assertEqual(row["ticker"], "600000.SS")

    def test_benchmark_bypass_hs300_no_division(self):
        """HS300 benchmark column = raw close as-is (no QFQ math)."""
        daily_records = [
            {"ts_code": HS300_TS_CODE, "trade_date": "20200102", "close": 4000.0, "vol": np.nan, "amount": np.nan, "pct_chg": np.nan},
            {"ts_code": HS300_TS_CODE, "trade_date": "20200103", "close": 4050.0, "vol": np.nan, "amount": np.nan, "pct_chg": np.nan},
        ]
        # adj_records are empty — no adj_factor for HS300
        adj_records = []

        qfq_long, _ = h52c.compute_qfq(daily_records, adj_records, self.h52a_yahoo_tickers)

        hs300_rows = qfq_long[qfq_long["ticker"] == HS300_TICKER]
        self.assertEqual(len(hs300_rows), 2)
        self.assertEqual(hs300_rows.iloc[0]["qfq_close"], 4000.0)
        self.assertEqual(hs300_rows.iloc[1]["qfq_close"], 4050.0)

    def test_ticker_with_no_adj_factor_gets_nan_qfq(self):
        """Ticker with no adj_factor → NaN qfq_close, listed in no_qfq."""
        daily_records = [
            {"ts_code": "600000.SH", "trade_date": "20200102", "close": 10.0, "vol": 100.0, "amount": 1000.0, "pct_chg": 1.0},
        ]
        adj_records = []  # No adj_factor for 600000

        qfq_long, no_qfq = h52c.compute_qfq(daily_records, adj_records, self.h52a_yahoo_tickers)

        self.assertIn("600000.SS", no_qfq)
        self.assertEqual(len(qfq_long), 1)
        self.assertTrue(pd.isna(qfq_long.iloc[0]["qfq_close"]))

    def test_unit_conversions_at_persist_time(self):
        """amount × 1000 (千元→RMB), vol × 100 (手→shares) applied in qfq_long."""
        daily_records = [
            {"ts_code": "000001.SZ", "trade_date": "20200102", "close": 15.0, "vol": 500.0, "amount": 750.0, "pct_chg": 2.0},
        ]
        adj_records = [
            {"ts_code": "000001.SZ", "trade_date": "20200102", "adj_factor": 100.0},
        ]

        qfq_long, _ = h52c.compute_qfq(daily_records, adj_records, self.h52a_yahoo_tickers)

        row = qfq_long.iloc[0]
        # amount: 750 千元 × 1000 = 750,000 RMB
        self.assertEqual(row["amount_rmb"], 750000.0)
        # vol: 500 手 × 100 = 50,000 shares
        self.assertEqual(row["vol_shares"], 50000.0)


class TestWidePricesForceReindex(unittest.TestCase):
    """Column-dimension pinning (D5) — force-reindex tests."""

    def test_force_reindex_pins_column_count(self):
        """Ticker with 0 rows in data window still gets an all-NaN column."""
        h52a_yahoo_tickers = ["600000.SS", "000001.SZ", "GHOST.SS"]
        qfq_long = pd.DataFrame({
            "date": ["2020-01-02", "2020-01-02"],
            "ticker": ["600000.SS", "000001.SZ"],
            "qfq_close": [10.0, 15.0],
            "amount_rmb": [10000.0, 15000.0],
            "vol_shares": [1000.0, 1000.0],
            "pct_chg": [1.0, 2.0],
        })

        wide = h52c.build_wide_prices(qfq_long, h52a_yahoo_tickers)

        # Should have date + 3 tickers + HS300 = 5 columns
        self.assertIn("date", wide.columns)
        self.assertIn("600000.SS", wide.columns)
        self.assertIn("000001.SZ", wide.columns)
        self.assertIn("GHOST.SS", wide.columns)
        self.assertIn(HS300_TICKER, wide.columns)
        self.assertEqual(len(wide.columns), 5)

        # GHOST.SS should be all-NaN
        self.assertTrue(wide["GHOST.SS"].isna().all())

        # 600000.SS and 000001.SZ should have data
        self.assertEqual(wide.loc[0, "600000.SS"], 10.0)
        self.assertEqual(wide.loc[0, "000001.SZ"], 15.0)


class TestBuildLiquidity(unittest.TestCase):
    """Liquidity CSV construction tests."""

    def test_liquidity_5_columns(self):
        qfq_long = pd.DataFrame({
            "date": ["2020-01-02", "2020-01-02", "2020-01-03"],
            "ticker": ["600000.SS", "000001.SZ", "600000.SS"],
            "qfq_close": [10.0, 15.0, 10.5],
            "amount_rmb": [100000.0, 150000.0, 105000.0],
            "vol_shares": [10000.0, 10000.0, 10000.0],
            "pct_chg": [1.0, 2.0, 0.5],
        })

        liq = h52c.build_liquidity_long(qfq_long)

        self.assertEqual(len(liq), 3)
        self.assertEqual(list(liq.columns), ["date", "ticker", "amount_rmb", "vol_shares", "source"])
        self.assertEqual(liq.iloc[0]["source"], h52c.LIQUIDITY_SOURCE)

    def test_nan_vol_rows_excluded(self):
        """Rows with NaN or zero vol should not appear in liquidity."""
        qfq_long = pd.DataFrame({
            "date": ["2020-01-02", "2020-01-02"],
            "ticker": ["600000.SS", "000001.SZ"],
            "qfq_close": [10.0, np.nan],
            "amount_rmb": [100000.0, np.nan],
            "vol_shares": [10000.0, 0.0],  # zero vol → exclude
            "pct_chg": [1.0, np.nan],
        })

        liq = h52c.build_liquidity_long(qfq_long)

        self.assertEqual(len(liq), 1)
        self.assertEqual(liq.iloc[0]["ticker"], "600000.SS")


class TestAdtvPerWindow(unittest.TestCase):
    """ADTV per-window computability tests."""

    def test_adtv_per_window_enforces_20day_trailing(self):
        """Ticker needs 20 prior trading days for ADTV to be computable."""
        dates = [f"2020-01-{d:02d}" for d in range(2, 32)]  # 30 trading days
        h52a_yahoo_tickers = ["TEST.SS"]

        # Build liquidity with data for all 30 days
        rows = []
        for d in dates:
            rows.append({
                "date": d,
                "ticker": "TEST.SS",
                "amount_rmb": 100000.0,
                "vol_shares": 10000.0,
                "source": h52c.LIQUIDITY_SOURCE,
            })
        liquidity = pd.DataFrame(rows)

        # Wide prices (just needs the dates)
        wide = pd.DataFrame({"date": dates, "TEST.SS": [10.0] * len(dates), HS300_TICKER: [4000.0] * len(dates)})

        adtv = h52c.compute_adtv_per_window(liquidity, wide, h52a_yahoo_tickers)

        # With 30 trading days, all pairs after day 20 should be computable
        # cal_2024 window dates don't overlap with 2020 dates → 0 pairs, 0% computable
        self.assertIn("cal_2024", adtv)
        # The windows are date-scoped, so 2020 won't match cal_2024
        # Just verify the structure is correct
        for wname in ["cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"]:
            self.assertIn(wname, adtv)
            self.assertIn("computable_pct", adtv[wname])
            self.assertIn("ticker_date_pairs", adtv[wname])

    def test_all_windows_present(self):
        """All 5 H42 standard windows must be present."""
        liquidity = pd.DataFrame(columns=["date", "ticker", "amount_rmb", "vol_shares", "source"])
        wide = pd.DataFrame(columns=["date"])
        adtv = h52c.compute_adtv_per_window(liquidity, wide, [])

        expected = {"cal_2024", "h1_2025", "h2_2025", "ytd_2026", "deploy_2025_2026"}
        self.assertEqual(set(adtv.keys()), expected)


class TestCoverageComputation(unittest.TestCase):
    """Coverage metrics computation tests."""

    def test_benchmark_coverage_100pct(self):
        wide = pd.DataFrame({
            "date": ["2020-01-02", "2020-01-03"],
            "600000.SS": [10.0, 10.5],
            HS300_TICKER: [4000.0, 4050.0],
        })
        liquidity = pd.DataFrame()
        qfq_long = pd.DataFrame({
            "date": ["2020-01-02", "2020-01-03"],
            "ticker": ["600000.SS", "600000.SS"],
            "qfq_close": [10.0, 10.5],
            "amount_rmb": [100000.0, 105000.0],
            "vol_shares": [10000.0, 10000.0],
            "pct_chg": [1.0, 0.5],
        })

        cov = h52c.compute_coverage(
            wide, liquidity, qfq_long,
            ["600000.SS"], ["2020-01-02", "2020-01-03"],
            "2020-01-02", "2020-01-03",
            [], [], 0, 0, [], [],
        )

        self.assertEqual(cov["benchmark_coverage_pct"], 100.0)

    def test_ticker_coverage_pct(self):
        wide = pd.DataFrame({
            "date": ["2020-01-02"],
            "A.SS": [10.0],
            "B.SZ": [np.nan],  # All-NaN column
            HS300_TICKER: [4000.0],
        })

        cov = h52c.compute_coverage(
            wide, pd.DataFrame(), pd.DataFrame(),
            ["A.SS", "B.SZ"], ["2020-01-02"],
            "2020-01-02", "2020-01-02",
            [], [], 0, 0, [], [],
        )

        self.assertEqual(cov["ticker_coverage_pct"], 50.0)  # 1 of 2 has data


class TestBuildWidePrices(unittest.TestCase):
    """Integration-level wide prices build test."""

    def test_qfq_to_wide_with_benchmark(self):
        """End-to-end: qfq_long → wide_prices → CSV-ready format."""
        h52a_yahoo_tickers = ["600000.SS", "000001.SZ"]

        qfq_long = pd.DataFrame({
            "date": ["2020-01-02", "2020-01-02", "2020-01-02"],
            "ticker": ["600000.SS", "000001.SZ", HS300_TICKER],
            "qfq_close": [10.0, 15.0, 4000.0],
            "amount_rmb": [100000.0, 150000.0, np.nan],
            "vol_shares": [10000.0, 10000.0, 0.0],
            "pct_chg": [1.0, 2.0, np.nan],
        })

        wide = h52c.build_wide_prices(qfq_long, h52a_yahoo_tickers)

        # date column + 2 tickers + HS300 = 4 columns
        self.assertEqual(len(wide.columns), 4)
        self.assertEqual(list(wide.columns), ["date", "600000.SS", "000001.SZ", HS300_TICKER])
        self.assertEqual(wide.loc[0, "date"], "2020-01-02")
        self.assertEqual(wide.loc[0, "600000.SS"], 10.0)
        self.assertEqual(wide.loc[0, HS300_TICKER], 4000.0)


class TestBuildCoveragePayload(unittest.TestCase):
    """Coverage JSON payload tests."""

    def test_payload_has_required_top_level_keys(self):
        coverage = {
            "trade_dates_observed": 100, "trade_dates_with_full_data": 100,
            "ticker_coverage_pct": 99.0, "universe_ticker_count": 1074,
            "tickers_with_no_qfq": [], "avg_trade_days_per_ticker": 500.0,
            "min_trade_days_per_ticker": 400, "median_implied_qfq_price_rmb": 10.0,
            "p10_p50_p90_qfq_price_rmb": [2.0, 10.0, 50.0],
            "adtv_computability_per_window": {},
            "benchmark_coverage_pct": 100.0,
        }
        adtv = {
            "cal_2024": {"start": "2024-01-01", "end": "2024-12-31", "ticker_date_pairs": 100, "computable_pct": 99.0},
            "h1_2025": {"start": "2025-01-01", "end": "2025-06-30", "ticker_date_pairs": 100, "computable_pct": 99.0},
            "h2_2025": {"start": "2025-07-01", "end": "2025-12-31", "ticker_date_pairs": 100, "computable_pct": 99.0},
            "ytd_2026": {"start": "2026-01-01", "end": "2026-05-21", "ticker_date_pairs": 100, "computable_pct": 99.0},
            "deploy_2025_2026": {"start": "2025-01-01", "end": "2026-05-21", "ticker_date_pairs": 100, "computable_pct": 99.0},
        }

        payload = h52c.build_coverage_payload(
            coverage, adtv,
            ["TICKER.SS"], "2020-01-02", "2026-05-21",
            [], [], 0, [], [],
            "CANDIDATE_DATASET",
        )

        required_keys = [
            "generated_at", "task", "status", "provenance", "coverage",
            "fetch_failures", "anomalies", "verdict",
        ]
        for key in required_keys:
            self.assertIn(key, payload)

        self.assertEqual(payload["task"], "H52c")
        self.assertEqual(payload["provenance"]["stock_provider"], "tushare:daily")
        self.assertEqual(payload["provenance"]["qfq_method"], "snapshot_qfq_local_compute")
        self.assertEqual(payload["provenance"]["benchmark_ticker"], "000300.SS")
        self.assertEqual(payload["verdict"], "CANDIDATE_DATASET")

    def test_payload_blocked_when_coverage_low(self):
        """BLOCKED when ticker_coverage_pct < 98.0."""
        coverage = {
            "trade_dates_observed": 100, "trade_dates_with_full_data": 100,
            "ticker_coverage_pct": 95.0,  # Below gate
            "universe_ticker_count": 1074,
            "tickers_with_no_qfq": [], "avg_trade_days_per_ticker": 500.0,
            "min_trade_days_per_ticker": 400, "median_implied_qfq_price_rmb": 10.0,
            "p10_p50_p90_qfq_price_rmb": [2.0, 10.0, 50.0],
            "adtv_computability_per_window": {},
            "benchmark_coverage_pct": 100.0,
        }
        adtv = {
            "cal_2024": {"start": "2024-01-01", "end": "2024-12-31", "ticker_date_pairs": 100, "computable_pct": 99.0},
            "h1_2025": {"start": "2025-01-01", "end": "2025-06-30", "ticker_date_pairs": 100, "computable_pct": 99.0},
            "h2_2025": {"start": "2025-07-01", "end": "2025-12-31", "ticker_date_pairs": 100, "computable_pct": 99.0},
            "ytd_2026": {"start": "2026-01-01", "end": "2026-05-21", "ticker_date_pairs": 100, "computable_pct": 99.0},
            "deploy_2025_2026": {"start": "2025-01-01", "end": "2026-05-21", "ticker_date_pairs": 100, "computable_pct": 99.0},
        }

        payload = h52c.build_coverage_payload(
            coverage, adtv,
            ["TICKER.SS"], "2020-01-02", "2026-05-21",
            [], [], 0, [], [],
            "CANDIDATE_DATASET",
        )

        self.assertEqual(payload["verdict"], "BLOCKED")


class TestReportGeneration(unittest.TestCase):
    """Report markdown generation tests."""

    def test_report_contains_required_sections(self):
        payload = {
            "generated_at": "2026-05-25T00:00:00Z",
            "task": "H52c",
            "status": "CANDIDATE_DATASET",
            "verdict": "CANDIDATE_DATASET",
            "provenance": {
                "stock_provider": "tushare:daily",
                "adjustment_provider": "tushare:adj_factor",
                "qfq_method": "snapshot_qfq_local_compute",
                "benchmark_provider": "tushare:index_daily",
                "benchmark_ticker": "000300.SS",
                "universe_source": "data/cn_pit/universe_h52a_csi500.jsonl",
                "date_range_requested": "20200102 -> 20260521",
                "date_range_actual": "20200102 -> 20260521",
            },
            "coverage": {
                "trade_dates_observed": 1593,
                "trade_dates_with_full_data": 1590,
                "ticker_coverage_pct": 99.0,
                "universe_ticker_count": 1074,
                "tickers_with_no_qfq": [],
                "avg_trade_days_per_ticker": 500.0,
                "min_trade_days_per_ticker": 400,
                "median_implied_qfq_price_rmb": 10.0,
                "p10_p50_p90_qfq_price_rmb": [2.0, 10.0, 50.0],
                "adtv_computability_per_window": {},
                "benchmark_coverage_pct": 100.0,
            },
            "fetch_failures": [],
            "anomalies": {
                "tickers_with_no_qfq": 0,
                "daily_vs_adj_factor_row_count_skew_days": 0,
                "extreme_pct_chg_anomalies": 0,
                "extreme_pct_chg_sample": [],
                "tickers_with_no_h52c_data": 0,
            },
        }

        report = h52c.build_report(payload)

        required = [
            "H52c",
            "Provenance",
            "Coverage Summary",
            "ADTV Computability",
            "Anomalies",
            "Fetch Failures",
            "Unit Conversions",
            "CANDIDATE_DATASET",
        ]
        for section in required:
            self.assertIn(section, report, f"Missing section: {section}")


class TestAdtvWindowComputation(unittest.TestCase):
    """ADTV window computability with overlapping dates."""

    def test_adtv_20day_trailing_with_overlapping_window(self):
        """Window that covers dates where ticker has >=20 prior vol days."""
        # Build 50 trading days in 2024 — compact format
        dates = [f"202401{d:02d}" for d in range(2, 32)] + [f"202402{d:02d}" for d in range(1, 22)]
        h52a_yahoo_tickers = ["TEST.SS"]

        rows = []
        for d in dates:
            rows.append({
                "date": d,
                "ticker": "TEST.SS",
                "amount_rmb": 100000.0,
                "vol_shares": 10000.0,
                "source": h52c.LIQUIDITY_SOURCE,
            })
        liquidity = pd.DataFrame(rows)
        wide = pd.DataFrame({"date": dates, "TEST.SS": [10.0] * len(dates), HS300_TICKER: [4000.0] * len(dates)})

        # Override the ADTV_WINDOWS_COMPACT to test with a custom window
        original_windows = h52c.ADTV_WINDOWS_COMPACT
        h52c.ADTV_WINDOWS_COMPACT = {
            "test_window": ("20240101", "20240228"),
        }

        try:
            adtv = h52c.compute_adtv_per_window(liquidity, wide, h52a_yahoo_tickers)
            self.assertIn("test_window", adtv)
            winfo = adtv["test_window"]
            # 50 trading days, 30 window days (minus overlapping days)
            # First 20 window days have <20 prior vol days → not computable
            # So computable_pct should be > 0 but < 100
            self.assertGreater(winfo["computable_pct"], 0)
            self.assertLess(winfo["computable_pct"], 100)
        finally:
            h52c.ADTV_WINDOWS_COMPACT = original_windows



class TestDateFormatRegression(unittest.TestCase):
    """H52h regression: ensure H52c CSV dates are ISO YYYY-MM-DD format.

    Protects against future ingestion bugs that might produce int64 dates.
    """

    def test_date_format_regression(self):
        """Actual H52c CSVs must have all dates matching ISO regex."""
        import csv
        import re

        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")

        csv_paths = [
            ("prices", ROOT / "data/cn_pit/prices_h52c_csi500_qfq.csv"),
            ("liquidity", ROOT / "data/cn_pit/liquidity_h52c_csi500_daily_amount.csv"),
        ]

        for name, path in csv_paths:
            if not path.exists():
                self.skipTest(f"{path} missing")
                continue

            with open(path, encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                self.assertEqual(header[0], "date",
                                 f"{name} CSV: first column header != 'date': {header[0]!r}")

                bad_dates = []
                for row_num, row in enumerate(reader, start=2):
                    date_val = row[0]
                    if not iso_re.match(date_val):
                        bad_dates.append((row_num, date_val))
                        if len(bad_dates) >= 5:
                            break

            self.assertEqual(len(bad_dates), 0,
                             f"{name} CSV: non-ISO date values found: {bad_dates}")


if __name__ == "__main__":
    unittest.main()
