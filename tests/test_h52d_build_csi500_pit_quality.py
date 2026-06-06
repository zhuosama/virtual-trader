#!/usr/bin/env python3
"""Tests for H52d — CSI500 PIT Quality Metrics Ingestion."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h52d_build_csi500_pit_quality as h52d  # noqa: E402


class TestH52dTickerConversion(unittest.TestCase):
    def test_yahoo_to_tushare_ss(self):
        self.assertEqual(h52d.yahoo_to_ts_code("600519.SS"), "600519.SH")

    def test_yahoo_to_tushare_sz(self):
        self.assertEqual(h52d.yahoo_to_ts_code("000858.SZ"), "000858.SZ")

    def test_yahoo_to_tushare_invalid(self):
        with self.assertRaises(ValueError):
            h52d.yahoo_to_ts_code("000001.XSHE")


class TestH52dUniverseLoading(unittest.TestCase):
    def test_load_tickers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            rows = [
                {"ticker": "600519.SS", "weight": 5.0},
                {"ticker": "000858.SZ", "weight": 3.0},
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            tickers = h52d.load_h52a_tickers(path)
            self.assertEqual(tickers, ["000858.SZ", "600519.SS"])

    def test_load_tickers_skips_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            path.write_text(
                '{"ticker": "600519.SS", "weight": 5.0}\n\n{"ticker": "", "weight": 0}\n',
                encoding="utf-8",
            )
            tickers = h52d.load_h52a_tickers(path)
            self.assertEqual(tickers, ["600519.SS"])


class TestH52dNanToNone(unittest.TestCase):
    def test_nan_to_none(self):
        self.assertIsNone(h52d.nan_to_none(np.nan))
        self.assertIsNone(h52d.nan_to_none(float("nan")))
        self.assertIsNone(h52d.nan_to_none(None))
        self.assertEqual(h52d.nan_to_none(1.5), 1.5)
        self.assertEqual(h52d.nan_to_none(0), 0)
        self.assertEqual(h52d.nan_to_none("hello"), "hello")

    def test_safe_float_nan(self):
        self.assertIsNone(h52d.safe_float(np.nan))
        self.assertIsNone(h52d.safe_float(None))
        self.assertEqual(h52d.safe_float("3.14"), 3.14)

    def test_safe_str_none(self):
        self.assertIsNone(h52d.safe_str(None))
        self.assertIsNone(h52d.safe_str(np.nan))
        self.assertEqual(h52d.safe_str("  hello  "), "hello")


class TestH52dDedupJoin(unittest.TestCase):
    """Test dedup-then-join 5-step pipeline: no Cartesian explosion."""

    def test_dedup_single_endpoint_no_duplicates(self):
        records = [
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-03-01", "update_flag": "1"},
            {"ts_code": "000001.SZ", "end_date": "2024-03-31", "ann_date": "2024-04-30", "update_flag": "1"},
        ]
        result = h52d.dedup_endpoint_df(records, "test")
        self.assertEqual(len(result), 2)

    def test_dedup_removes_restated(self):
        """Restated quarter with higher update_flag should replace older."""
        records = [
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-03-01", "update_flag": "1"},
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-04-15", "update_flag": "2"},
        ]
        result = h52d.dedup_endpoint_df(records, "test")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["ann_date"], "2025-04-15")

    def test_dedup_assertion_on_residual_duplicates(self):
        """After dedup, no (ts_code, end_date) should duplicate."""
        records = [
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-03-01", "update_flag": "2"},
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-03-01", "update_flag": "2"},
        ]
        result = h52d.dedup_endpoint_df(records, "test")
        self.assertEqual(len(result), 1)

    def test_join_no_cartesian(self):
        """4 endpoints with a restated quarter each → no Cartesian explosion."""
        fi = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-03-01", "update_flag": "1", "roe_waa": 15.0},
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-04-01", "update_flag": "2", "roe_waa": 15.5},
            {"ts_code": "000001.SZ", "end_date": "2024-03-31", "ann_date": "2024-04-30", "update_flag": "1", "roe_waa": 14.0},
        ])
        income = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-03-05", "update_flag": "1", "n_income": 100.0},
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-04-05", "update_flag": "2", "n_income": 101.0},
            {"ts_code": "000001.SZ", "end_date": "2024-03-31", "ann_date": "2024-04-30", "update_flag": "1", "n_income": 90.0},
        ])
        cf = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-03-05", "update_flag": "1", "n_cashflow_act": 80.0},
            {"ts_code": "000001.SZ", "end_date": "2024-03-31", "ann_date": "2024-04-30", "update_flag": "1", "n_cashflow_act": 70.0},
        ])
        bs = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "2024-12-31", "ann_date": "2025-03-05", "update_flag": "1", "total_assets": 1000.0},
            {"ts_code": "000001.SZ", "end_date": "2024-03-31", "ann_date": "2024-04-30", "update_flag": "1", "total_assets": 950.0},
        ])

        joined, skew = h52d.join_four_endpoints(fi, income, cf, bs)
        # Should have 2 rows (one per quarter), NOT 2^4=16
        self.assertEqual(len(joined), 2)
        # Should use latest update for restated quarter
        row_dec = joined[joined["end_date"] == "2024-12-31"].iloc[0]
        self.assertEqual(row_dec["roe_waa"], 15.5)
        self.assertEqual(row_dec["n_income_income"], 101.0)


class TestH52dBuildRow(unittest.TestCase):
    def setUp(self):
        self.anomalies = []
        self.ingested_at = "2026-05-24T00:00:00Z"

    def test_build_row_basic(self):
        joined_row = {
            "end_date": "2024-12-31",
            "ann_date": "2025-04-15",
            "roe_waa": 15.5, "roa": 8.2,
            "grossprofit_margin": 35.0, "op_of_gr": 20.0,
            "current_ratio": 1.8, "quick_ratio": 1.2,
            "debt_to_eqt": 1.5, "ocf_to_or": 12.0,
            "fcff": 5000000.0,
            "op_income": 12000000.0,
            "n_income_income": 100.0,
            "n_cashflow_act_cashflow": 80.0,
            "total_assets_balancesheet": 1000.0,
            "total_revenue_income": 500.0,
            "total_cogs_income": 300.0,
        }
        row, is_anom = h52d.build_row(
            "000001.SZ", joined_row, None, self.ingested_at, self.anomalies, False
        )
        self.assertFalse(is_anom)
        self.assertEqual(row["ticker"], "000001.SZ")
        self.assertEqual(row["report_period"], "2024-12-31")
        self.assertEqual(row["filing_date"], "2025-04-15")
        self.assertEqual(row["roe"], 15.5)
        self.assertEqual(row["roa"], 8.2)
        self.assertEqual(row["gross_margin"], 35.0)
        self.assertEqual(row["operating_margin"], 20.0)
        self.assertEqual(row["accruals_ratio"], round((100.0 - 80.0) / 1000.0, 6))
        self.assertEqual(row["_net_income"], 100.0)
        self.assertEqual(row["_net_cashflow_op"], 80.0)
        self.assertEqual(row["_total_assets"], 1000.0)

    def test_accruals_ratio_raw_signed(self):
        """accruals_ratio is Sloan raw signed, NOT abs()."""
        joined_row = {
            "end_date": "2024-12-31", "ann_date": "2025-04-15",
            "roe_waa": 10.0, "roa": 5.0,
            "grossprofit_margin": 30.0, "op_of_gr": 15.0,
            "current_ratio": 2.0, "quick_ratio": 1.5,
            "debt_to_eqt": 1.0, "ocf_to_or": 10.0,
            "fcff": 1000000.0, "op_income": 5000000.0,
            "n_income_income": 50.0,
            "n_cashflow_act_cashflow": 120.0,  # CF > NI → negative accruals
            "total_assets_balancesheet": 10000.0,
            "total_revenue_income": 1000.0,
            "total_cogs_income": 700.0,
        }
        row, _ = h52d.build_row("000001.SZ", joined_row, None, self.ingested_at, self.anomalies, False)
        expected = round((50.0 - 120.0) / 10000.0, 6)
        self.assertEqual(row["accruals_ratio"], expected)
        self.assertLess(row["accruals_ratio"], 0)  # must be negative, not abs()

    def test_build_row_null_accruals(self):
        joined_row = {
            "end_date": "2024-12-31", "ann_date": "2025-04-15",
            "roe_waa": 15.5, "roa": 8.2,
            "grossprofit_margin": 35.0, "op_of_gr": 20.0,
            "current_ratio": 1.8, "quick_ratio": 1.2,
            "debt_to_eqt": 1.5, "ocf_to_or": 12.0,
            "fcff": None, "op_income": None,
            # Missing intermediates → accruals NULL
        }
        row, _ = h52d.build_row("000001.SZ", joined_row, None, self.ingested_at, self.anomalies, False)
        self.assertIsNone(row["accruals_ratio"])
        self.assertIn("accruals_ratio: NULL", row["data_quality_note"])

    def test_filing_date_before_report_period_raises(self):
        joined_row = {
            "end_date": "2024-12-31",
            "ann_date": "2024-01-15",  # filing before period end
            "roe_waa": 15.5, "roa": 8.2,
            "grossprofit_margin": 35.0, "op_of_gr": 20.0,
            "current_ratio": 1.8, "quick_ratio": 1.2,
            "debt_to_eqt": 1.5, "ocf_to_or": 12.0,
            "fcff": 5000000.0, "op_income": 12000000.0,
        }
        with self.assertRaises(ValueError):
            h52d.build_row("000001.SZ", joined_row, None, self.ingested_at, self.anomalies, False)

    def test_filing_date_before_report_period_quarantine(self):
        joined_row = {
            "end_date": "2024-12-31",
            "ann_date": "2024-01-15",
            "roe_waa": 15.5, "roa": 8.2,
            "grossprofit_margin": 35.0, "op_of_gr": 20.0,
            "current_ratio": 1.8, "quick_ratio": 1.2,
            "debt_to_eqt": 1.5, "ocf_to_or": 12.0,
            "fcff": 5000000.0, "op_income": 12000000.0,
        }
        row, is_anom = h52d.build_row(
            "000001.SZ", joined_row, None, self.ingested_at, self.anomalies, True
        )
        self.assertTrue(is_anom)
        self.assertEqual(len(self.anomalies), 1)

    def test_gross_margin_fallback(self):
        joined_row = {
            "end_date": "2024-12-31", "ann_date": "2025-04-15",
            "roe_waa": 15.5, "roa": 8.2,
            "grossprofit_margin": None,  # primary NULL → fallback
            "op_of_gr": 20.0,
            "current_ratio": 1.8, "quick_ratio": 1.2,
            "debt_to_eqt": 1.5, "ocf_to_or": 12.0,
            "fcff": 5000000.0, "op_income": 12000000.0,
            "total_revenue_income": 1000.0,
            "total_cogs_income": 700.0,
        }
        row, _ = h52d.build_row("000001.SZ", joined_row, None, self.ingested_at, self.anomalies, False)
        expected = round((1000.0 - 700.0) / 1000.0 * 100, 6)
        self.assertEqual(row["gross_margin"], expected)
        self.assertIn("gross_margin: fallback derived", row["data_quality_note"])

    def test_operating_margin_fallback(self):
        joined_row = {
            "end_date": "2024-12-31", "ann_date": "2025-04-15",
            "roe_waa": 15.5, "roa": 8.2,
            "grossprofit_margin": 35.0,
            "op_of_gr": None,  # primary NULL
            "current_ratio": 1.8, "quick_ratio": 1.2,
            "debt_to_eqt": 1.5, "ocf_to_or": 12.0,
            "fcff": 5000000.0, "op_income": 12000000.0,
            "total_revenue_income": 40000000.0,
        }
        row, _ = h52d.build_row("000001.SZ", joined_row, None, self.ingested_at, self.anomalies, False)
        expected = round(12000000.0 / 40000000.0, 6)
        self.assertEqual(row["operating_margin"], expected)
        self.assertIn("operating_margin: fallback", row["data_quality_note"])

    def test_op_income_fallback_from_operate_profit(self):
        joined_row = {
            "end_date": "2024-12-31", "ann_date": "2025-04-15",
            "roe_waa": 15.5, "roa": 8.2,
            "grossprofit_margin": 35.0,
            "op_of_gr": None,
            "current_ratio": 1.8, "quick_ratio": 1.2,
            "debt_to_eqt": 1.5, "ocf_to_or": 12.0,
            "fcff": 5000000.0,
            "op_income": None,  # primary NULL
            "operate_profit_income": 10000000.0,  # fallback from income endpoint
            "total_revenue_income": 40000000.0,
        }
        row, _ = h52d.build_row("000001.SZ", joined_row, None, self.ingested_at, self.anomalies, False)
        expected = round(10000000.0 / 40000000.0, 6)
        self.assertEqual(row["operating_margin"], expected)
        self.assertEqual(row["_op_income"], 10000000.0)

    def test_report_period_out_of_range_raises(self):
        joined_row = {
            "end_date": "2005-12-31",  # pre-2019
            "ann_date": "2006-04-30",
            "roe_waa": 10.0, "roa": 5.0,
            "grossprofit_margin": 30.0, "op_of_gr": 15.0,
            "current_ratio": 2.0, "quick_ratio": 1.5,
            "debt_to_eqt": 1.0, "ocf_to_or": 10.0,
            "fcff": 1000000.0, "op_income": 5000000.0,
        }
        with self.assertRaises(ValueError):
            h52d.build_row("000001.SZ", joined_row, None, self.ingested_at, self.anomalies, False)


class TestH52dCoverageComputation(unittest.TestCase):
    def test_coverage_gates(self):
        rows = [
            {
                "ticker": "000001.SZ", "report_period": "2024-12-31",
                "roe": 15.0, "roa": 8.0, "gross_margin": 30.0,
                "operating_margin": 20.0, "current_ratio": 2.0,
                "quick_ratio": 1.5, "debt_to_equity": 1.0,
                "operating_cash_flow_to_revenue": 10.0,
                "free_cash_flow": 100.0, "accruals_ratio": 0.05,
                "_net_income": 100.0, "_net_cashflow_op": 80.0,
                "_total_assets": 1000.0, "_op_income": 200.0,
                "_total_revenue": 500.0, "_total_cogs": 300.0,
                "code": "000001", "filing_date": "2025-04-01",
                "source_url": "", "source_provider": "", "ingested_at": "",
                "data_quality_note": "",
            }
        ]
        coverage = h52d.compute_coverage(
            rows, ["000001.SZ"], [], 0, 0, 0, 0, None, "2026-01-01T00:00:00Z"
        )
        self.assertEqual(coverage["universe_ticker_count"], 1)
        self.assertEqual(coverage["ticker_coverage_pct"], 100.0)
        self.assertEqual(coverage["total_rows"], 1)
        # All gates should pass for a perfect single-row scenario
        self.assertTrue(coverage["gates"]["ticker_coverage_ge_98pct"])
        self.assertTrue(coverage["gates"]["hard_fields_ge_85pct"])
        self.assertTrue(coverage["gates"]["soft_fields_ge_50pct"])
        self.assertTrue(coverage["gates"]["intermediates_ge_85pct"])
        self.assertTrue(coverage["gates"]["accruals_ratio_ge_50pct"])


class TestH52dROEOverlap(unittest.TestCase):
    def test_roe_overlap_year_end_only(self):
        """Only year-end periods are checked."""
        h52d_rows = [
            {"ticker": "000001.SZ", "report_period": "2024-03-31", "roe": 15.0},  # NOT year-end
            {"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.5},  # year-end
        ]
        with tempfile.TemporaryDirectory() as tmp:
            h50a_path = Path(tmp) / "h50a.jsonl"
            h50a_path.write_text(
                json.dumps({"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.5}) + "\n",
                encoding="utf-8",
            )
            result = h52d.roe_overlap_check(h52d_rows, h50a_path)
        self.assertEqual(result["overlap_count"], 1)
        self.assertEqual(result["pct_within_tolerance"], 100.0)
        self.assertEqual(result["anomaly_count"], 0)
        self.assertEqual(result["year_end_rows_available"], 1)

    def test_roe_overlap_detects_divergence(self):
        h52d_rows = [
            {"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.5},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            h50a_path = Path(tmp) / "h50a.jsonl"
            h50a_path.write_text(
                json.dumps({"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 20.0}) + "\n",
                encoding="utf-8",
            )
            result = h52d.roe_overlap_check(h52d_rows, h50a_path)
        self.assertEqual(result["overlap_count"], 1)
        self.assertEqual(result["pct_within_tolerance"], 0.0)
        self.assertEqual(result["anomaly_count"], 1)

    def test_roe_overlap_no_h50a_path(self):
        result = h52d.roe_overlap_check([], None)
        self.assertEqual(result["overlap_count"], 0)


class TestH52dArtifactSchema(unittest.TestCase):
    """Test that coverage JSON has all required fields."""

    def test_coverage_json_schema(self):
        coverage = h52d.compute_coverage(
            [], ["000001.SZ"], [], 0, 0, 0, 0, None, "2026-01-01T00:00:00Z"
        )
        for key in ["provenance", "universe_ticker_count", "ticker_coverage_pct",
                     "total_rows", "period_count", "per_field_non_null_pct",
                     "hard_field_min_pct", "soft_field_min_pct", "intermediate_min_pct",
                     "gates", "verdict"]:
            self.assertIn(key, coverage, f"missing {key}")

        prov = coverage["provenance"]
        self.assertEqual(prov["provider"], h52d.PROVIDER_LABEL)
        self.assertEqual(prov["axis"], "ticker")
        self.assertEqual(prov["universe_source"], "data/cn_pit/universe_h52a_csi500.jsonl")

        for field in h52d.ALL_SCORE_FIELDS + h52d.ALL_INTERMEDIATE:
            self.assertIn(field, coverage["per_field_non_null_pct"],
                          f"per_field_non_null_pct missing {field}")

        for gate in ["ticker_coverage_ge_98pct", "hard_fields_ge_85pct",
                      "soft_fields_ge_50pct", "intermediates_ge_85pct",
                      "accruals_ratio_ge_50pct", "h50a_overlap_ge_99pct"]:
            self.assertIn(gate, coverage["gates"], f"gates missing {gate}")


class TestH52dSmokeValidation(unittest.TestCase):
    """Test smoke validation logic."""

    def test_smoke_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)

            # Write a minimal JSONL
            rows = [{
                "ticker": "000001.SZ", "code": "000001.SZ",
                "report_period": "2024-12-31", "filing_date": "2025-04-15",
                "source_url": "https://tushare.pro", "source_provider": h52d.PROVIDER_LABEL,
                "ingested_at": "2026-01-01T00:00:00Z", "data_quality_note": "",
                "roe": 15.5, "roa": 8.2, "gross_margin": 35.0, "operating_margin": 20.0,
                "current_ratio": 1.8, "quick_ratio": 1.2, "debt_to_equity": 1.5,
                "operating_cash_flow_to_revenue": 12.0, "free_cash_flow": 5000000.0,
                "accruals_ratio": 0.05,
                "_net_income": 100.0, "_net_cashflow_op": 80.0, "_total_assets": 1000.0,
                "_op_income": 200.0, "_total_revenue": 500.0, "_total_cogs": 300.0,
            }]
            jsonl_path = tmp / "test.jsonl"
            h52d.write_jsonl(rows, jsonl_path)

            # Write minimal coverage
            coverage = h52d.compute_coverage(rows, ["000001.SZ"], [], 0, 0, 0, 0, None, "2026-01-01T00:00:00Z")
            cov_path = tmp / "test_coverage.json"
            h52d.write_coverage_json(coverage, cov_path)

            # Write minimal report
            report_path = tmp / "test_report.md"
            h52d.write_report(coverage, rows, report_path)

            # Run smoke validation
            result = h52d.run_smoke_validation(jsonl_path, cov_path, report_path)
            self.assertEqual(result, 0)


class TestH52dArtifactSelection(unittest.TestCase):
    """Mirrors h50a/h52a/h52b/h52c test_hxx_artifact_selection."""

    def test_artifact_selection_in_validate_hxx(self):
        """H52d must be registered in validate_hxx_artifacts.py."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import validate_hxx_artifacts as val
        specs = val.artifact_specs()
        self.assertIn("h52d", specs, "h52d not registered in artifact_specs()")
        spec = specs["h52d"]
        self.assertEqual(spec.name, "h52d")
        self.assertTrue(spec.json_path.match("*coverage*h52d*"),
                        f"coverage path {spec.json_path} should contain h52d")
        self.assertTrue(spec.report_path.match("*h52d*"),
                        f"report path {spec.report_path} should contain h52d")


if __name__ == "__main__":
    unittest.main()
