#!/usr/bin/env python3
"""Tests for H47 Tushare qfq price rebuild helpers."""

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h47_build_tushare_qfq_prices as h47  # noqa: E402


class TestH47BuildTushareQfqPrices(unittest.TestCase):
    def test_symbol_conversion(self):
        self.assertEqual(h47.to_tushare_code("600519.SS"), "600519.SH")
        self.assertEqual(h47.to_tushare_code("000858.SZ"), "000858.SZ")
        self.assertEqual(h47.to_yahoo_style("600519.SH"), "600519.SS")

    def test_load_universe_tickers_filters_by_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            rows = [
                {"ticker": "600519.SS", "effective_date": "2020-01-01", "end_date": ""},
                {"ticker": "000001.SZ", "effective_date": "2018-01-01", "end_date": "2019-01-01"},
                {"ticker": "000858.SZ", "effective_date": "2025-01-01", "end_date": "2025-12-31"},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            tickers = h47.load_universe_tickers(path, "2024-01-01", "2024-12-31")

            self.assertEqual(tickers, ["600519.SS"])

    def test_build_price_matrix_includes_benchmark(self):
        frames = {
            "600519.SS": pd.DataFrame({"date": ["2025-01-02", "2025-01-03"], "close": [100.0, 101.0]}),
            "000858.SZ": pd.DataFrame({"date": ["2025-01-02"], "close": [50.0]}),
        }
        benchmark = pd.DataFrame({"date": ["2025-01-02", "2025-01-03"], "close": [4000.0, 4010.0]})

        matrix = h47.build_price_matrix(frames, benchmark)

        self.assertEqual(list(matrix.columns), ["date", "000858.SZ", "600519.SS", "000300.SS"])
        self.assertEqual(len(matrix), 2)
        self.assertEqual(float(matrix.loc[0, "000300.SS"]), 4000.0)

    def test_coverage_detects_missing_data(self):
        matrix = pd.DataFrame({
            "date": ["2025-01-02", "2025-01-03"],
            "600519.SS": [100.0, 101.0],
            "000858.SZ": [pd.NA, pd.NA],
            "000300.SS": [4000.0, 4010.0],
        })

        coverage = h47.coverage_for_matrix(matrix, ["600519.SS", "000858.SZ"], "2025-01-01", "2025-01-10")

        self.assertFalse(coverage["ok"])
        self.assertEqual(coverage["missing_columns"], [])
        self.assertEqual(coverage["missing_data_columns"], ["000858.SZ"])
        self.assertTrue(coverage["benchmark_present"])

    def test_coverage_detects_partial_active_period_gap(self):
        matrix = pd.DataFrame({
            "date": ["2025-01-02", "2025-01-03", "2025-01-06"],
            "600519.SS": [100.0, 101.0, pd.NA],
            "000300.SS": [4000.0, 4010.0, 4020.0],
        })
        required = {"600519.SS": {"start": "2025-01-02", "end": "2025-01-06"}}

        coverage = h47.coverage_for_matrix(
            matrix, ["600519.SS"], "2025-01-01", "2025-01-10", required
        )

        self.assertFalse(coverage["ok"])
        self.assertEqual(coverage["missing_data_columns"], ["600519.SS"])
        self.assertEqual(coverage["partial_coverage"][0]["covered_days"], 2)

    def test_cache_must_cover_required_window(self):
        frame = pd.DataFrame({"date": ["2025-01-02", "2025-01-10"], "close": [1.0, 2.0]})

        self.assertTrue(h47.cache_covers_required_window(frame, "2025-01-02", "2025-01-10"))
        self.assertFalse(h47.cache_covers_required_window(frame, "2025-01-02", "2026-05-21"))

    def test_report_contains_provider_and_safety(self):
        matrix = pd.DataFrame({
            "date": ["2025-01-02"],
            "600519.SS": [100.0],
            "000300.SS": [4000.0],
        })
        payload = h47.build_payload(
            matrix,
            ["600519.SS"],
            [h47.FetchResult("600519.SS", 1, "2025-01-02", "2025-01-02", h47.STOCK_PROVIDER, False)],
            "2025-01-01",
            "2025-01-10",
        )

        report = h47.build_report(payload)

        self.assertIn("tushare:pro_bar:qfq", report)
        self.assertIn("**Benchmark adjustment:** published_index_level", report)
        self.assertIn("Did not overwrite H38 research prices", report)
        self.assertIn("**CANDIDATE_DATASET**", report)

    def test_protected_prior_artifact_rejected(self):
        self.assertTrue(h47.is_protected_prior_artifact(Path("reports/h46_paper_forward_monitor_report.md")))
        self.assertFalse(h47.is_protected_prior_artifact(Path("reports/h47_tushare_qfq_price_rebuild_report.md")))


if __name__ == "__main__":
    unittest.main()
