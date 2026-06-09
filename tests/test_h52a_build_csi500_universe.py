#!/usr/bin/env python3
"""Tests for H52a — CSI500 PIT Universe History."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h52a_build_csi500_universe as h52a  # noqa: E402


class TestH52aTickerConversion(unittest.TestCase):
    def test_ticker_from_ts_code_ss(self):
        self.assertEqual(h52a.ticker_from_ts_code("600519.SH"), "600519.SS")

    def test_ticker_from_ts_code_sz(self):
        self.assertEqual(h52a.ticker_from_ts_code("000858.SZ"), "000858.SZ")

    def test_ticker_from_ts_code_bare_ss(self):
        self.assertEqual(h52a.ticker_from_ts_code("600519"), "600519.SS")

    def test_ticker_from_ts_code_bare_sz(self):
        self.assertEqual(h52a.ticker_from_ts_code("002028"), "002028.SZ")

    def test_code_from_ticker(self):
        self.assertEqual(h52a.code_from_ticker("600519.SS"), "600519")
        self.assertEqual(h52a.code_from_ticker("000858.SZ"), "000858")


class TestH52aDateHelpers(unittest.TestCase):
    def test_compact_date(self):
        self.assertEqual(h52a.compact_date("2024-01-31"), "20240131")

    def test_dashed_date(self):
        self.assertEqual(h52a.dashed_date("20240131"), "2024-01-31")
        self.assertEqual(h52a.dashed_date("2024-01-31"), "2024-01-31")

    def test_parse_iso_date(self):
        d = h52a.parse_iso_date("2024-01-31")
        self.assertEqual(d.year, 2024)
        self.assertEqual(d.month, 1)
        self.assertEqual(d.day, 31)


class TestH52aIntervalReconstruction(unittest.TestCase):
    """Test D3 membership-interval reconstruction."""

    def test_contiguous_membership_single_interval(self):
        """Ticker present in all snapshots → one interval."""
        snapshots = [
            {"ticker": "000001.SZ", "trade_date": "2024-01-31", "weight": 0.5},
            {"ticker": "000001.SZ", "trade_date": "2024-02-29", "weight": 0.6},
            {"ticker": "000001.SZ", "trade_date": "2024-03-29", "weight": 0.7},
        ]
        intervals = h52a.snapshots_to_intervals_h52a(snapshots)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["effective_date"], "2024-01-31")
        self.assertEqual(intervals[0]["end_date"], "")
        self.assertAlmostEqual(intervals[0]["weight"], 0.6, places=4)
        self.assertEqual(intervals[0]["snapshot_count"], 3)

    def test_present_absent_present_two_intervals(self):
        """Ticker appears, disappears, reappears → two intervals."""
        snapshots = [
            {"ticker": "000001.SZ", "trade_date": "2024-01-31", "weight": 0.5},
            # absent on 2024-02-29 (not in snapshots)
            {"ticker": "000001.SZ", "trade_date": "2024-03-29", "weight": 0.7},
            {"ticker": "000002.SZ", "trade_date": "2024-01-31", "weight": 0.3},  # filler ticker so all_dates has Feb
            {"ticker": "000002.SZ", "trade_date": "2024-02-29", "weight": 0.4},
            {"ticker": "000002.SZ", "trade_date": "2024-03-29", "weight": 0.5},
        ]
        intervals = h52a.snapshots_to_intervals_h52a(snapshots)

        # Should have 3 intervals: 2 for ticker 000001, 1 for 000002
        i1 = [i for i in intervals if i["ticker"] == "000001.SZ"]
        self.assertEqual(len(i1), 2)

        i1_sorted = sorted(i1, key=lambda i: i["effective_date"])
        self.assertEqual(i1_sorted[0]["effective_date"], "2024-01-31")
        self.assertEqual(i1_sorted[0]["end_date"], "2024-02-29")
        self.assertEqual(i1_sorted[0]["snapshot_count"], 1)

        self.assertEqual(i1_sorted[1]["effective_date"], "2024-03-29")
        self.assertEqual(i1_sorted[1]["end_date"], "")
        self.assertEqual(i1_sorted[1]["snapshot_count"], 1)

    def test_mean_weight_computation(self):
        """D3: weight = mean across interval snapshots."""
        snapshots = [
            {"ticker": "000001.SZ", "trade_date": "2024-01-31", "weight": 0.2},
            {"ticker": "000001.SZ", "trade_date": "2024-02-29", "weight": 0.4},
            {"ticker": "000001.SZ", "trade_date": "2024-03-29", "weight": 0.9},
        ]
        intervals = h52a.snapshots_to_intervals_h52a(snapshots)
        self.assertAlmostEqual(intervals[0]["weight"], 0.5, places=4)


class TestH52aNormalise(unittest.TestCase):
    def test_normalise_snapshot_rows(self):
        raw = [
            {"con_code": "000001.SZ", "weight": 0.5},
            {"con_code": "600519.SH", "weight": 1.2},
        ]
        valid, anomalies = h52a.normalise_snapshot_rows(raw, "2024-01-31")
        self.assertEqual(len(valid), 2)
        self.assertEqual(len(anomalies), 0)
        self.assertEqual(valid[0]["ticker"], "000001.SZ")
        self.assertEqual(valid[0]["index_code"], "000905.SH")
        self.assertEqual(valid[0]["source_provider"], "tushare:index_weight")
        self.assertEqual(valid[1]["ticker"], "600519.SS")

    def test_normalise_nan_weight(self):
        """NaN weights → recorded as anomalies, not present in valid."""
        import math
        raw = [
            {"con_code": "000001.SZ", "weight": float("nan")},
            {"con_code": "600519.SH", "weight": 1.2},
        ]
        valid, anomalies = h52a.normalise_snapshot_rows(raw, "2024-01-31")
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["ticker"], "000001.SZ")
        self.assertIsNone(anomalies[0]["raw_weight"])

    def test_normalise_empty_ts_code_skipped(self):
        raw = [
            {"con_code": "", "weight": 0.5},
            {"con_code": "600519.SH", "weight": 1.2},
        ]
        valid, _ = h52a.normalise_snapshot_rows(raw, "2024-01-31")
        self.assertEqual(len(valid), 1)


class TestH52aSchemaConsistency(unittest.TestCase):
    """Verify output row schemas match H30 reference shape exactly."""

    def test_universe_schema_matches_h30(self):
        """H52a universe row fields match universe_h30_candidate.jsonl."""
        h30_ref = {
            "ticker", "code", "name", "effective_date", "end_date",
            "source_url", "ingested_at", "index_code", "weight",
            "source_provider", "snapshot_count",
        }
        # Build a synthetic universe row through interval reconstruction
        snapshots = [
            {"ticker": "000001.SZ", "trade_date": "2024-01-31", "weight": 0.5},
        ]
        intervals = h52a.snapshots_to_intervals_h52a(snapshots)
        row = intervals[0]
        self.assertEqual(set(row.keys()), h30_ref)

    def test_snapshot_schema_matches_h30(self):
        """H52a snapshot row fields match universe_snapshots_h30_candidate.jsonl."""
        h30_ref = {
            "index_code", "con_code", "code", "ticker", "trade_date",
            "weight", "source_provider", "source_url", "ingested_at", "source_row",
        }
        raw = [{"con_code": "000001.SZ", "weight": 0.5}]
        valid, _ = h52a.normalise_snapshot_rows(raw, "2024-01-31")
        row = valid[0]
        self.assertEqual(set(row.keys()), h30_ref)


class TestH52aProvenance(unittest.TestCase):
    """Verify provenance fields in coverage JSON."""

    def test_provenance_fields(self):
        snapshots = [
            {"ticker": "000001.SZ", "trade_date": "2024-01-31", "weight": 0.5},
        ]
        intervals = h52a.snapshots_to_intervals_h52a(snapshots)
        dates = sorted(set(s["trade_date"] for s in snapshots))
        coverage = h52a.write_coverage_json(
            Path(tempfile.gettempdir()) / "test_h52a_coverage.json",
            snapshots, intervals, [], [], dates,
        )
        self.assertEqual(coverage["provenance"]["provider"], "tushare:index_weight")
        self.assertEqual(coverage["provenance"]["index_code"], "000905.SH")
        self.assertEqual(coverage["provenance"]["snapshot_cadence"], "monthly_last_trading_day")
        self.assertIn("index_weight", coverage["provenance"]["endpoints_used"])
        self.assertIn("trade_cal", coverage["provenance"]["endpoints_used"])


class TestH52aDataQuality(unittest.TestCase):
    """Test data quality assertion behavior."""

    def test_missing_trade_date_aborts(self):
        with self.assertRaises(ValueError):
            h52a.snapshots_to_intervals_h52a([
                {"ticker": "000001.SZ", "weight": 0.5},
            ])

    def test_invalid_trade_date_aborts(self):
        with self.assertRaises(ValueError):
            h52a.snapshots_to_intervals_h52a([
                {"ticker": "000001.SZ", "trade_date": "not-a-date", "weight": 0.5},
            ])


if __name__ == "__main__":
    unittest.main()
