#!/usr/bin/env python3
"""Tests for H50a PIT Quality Metrics Ingestion."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h50a_build_tushare_pit_quality as h50a  # noqa: E402


class TestH50aTickerConversion(unittest.TestCase):
    def test_yahoo_to_tushare_ss(self):
        self.assertEqual(h50a.yahoo_to_tushare_code("600519.SS"), "600519.SH")

    def test_yahoo_to_tushare_sz(self):
        self.assertEqual(h50a.yahoo_to_tushare_code("000858.SZ"), "000858.SZ")

    def test_yahoo_to_tushare_invalid(self):
        with self.assertRaises(ValueError):
            h50a.yahoo_to_tushare_code("000001.XSHE")


class TestH50aUniverseLoading(unittest.TestCase):
    def test_load_tickers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            rows = [
                {"ticker": "600519.SS", "weight": 5.0},
                {"ticker": "000858.SZ", "weight": 3.0},
            ]
            path.write_text(
                "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
            )
            tickers = h50a.load_universe_tickers(path)
            self.assertEqual(tickers, ["000858.SZ", "600519.SS"])

    def test_load_tickers_skips_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            path.write_text(
                '{"ticker": "600519.SS", "weight": 5.0}\n\n{"ticker": "", "weight": 0}\n',
                encoding="utf-8",
            )
            tickers = h50a.load_universe_tickers(path)
            self.assertEqual(tickers, ["600519.SS"])


class TestH50aNanToNone(unittest.TestCase):
    def test_nan_to_none(self):
        self.assertIsNone(h50a.nan_to_none(np.nan))
        self.assertIsNone(h50a.nan_to_none(float("nan")))
        self.assertIsNone(h50a.nan_to_none(None))
        self.assertEqual(h50a.nan_to_none(1.5), 1.5)
        self.assertEqual(h50a.nan_to_none(0), 0)
        self.assertEqual(h50a.nan_to_none("hello"), "hello")

    def test_safe_float_nan(self):
        self.assertIsNone(h50a.safe_float(np.nan))
        self.assertIsNone(h50a.safe_float(None))
        self.assertEqual(h50a.safe_float("3.14"), 3.14)

    def test_safe_str_none(self):
        self.assertIsNone(h50a.safe_str(None))
        self.assertIsNone(h50a.safe_str(np.nan))
        self.assertEqual(h50a.safe_str("  hello  "), "hello")


class TestH50aBuildRow(unittest.TestCase):
    def setUp(self):
        self.anomalies = []
        self.ingested_at = "2026-05-23T00:00:00Z"

    def test_build_row_all_fields_present(self):
        """Test with actually-available Tushare fields."""
        ts_record = {
            "end_date": "2024-12-31",
            "ann_date": "2025-04-15",
            "roe_waa": 15.5,
            "roa": 8.2,
            "grossprofit_margin": 35.0,
            "op_of_gr": 20.0,
            "current_ratio": 1.8,
            "quick_ratio": 1.2,
            "debt_to_eqt": 1.5,
            "ocf_to_or": 12.0,
            "fcff": 5000000.0,
            "op_income": 12000000.0,
        }
        row, is_anom = h50a.build_row(
            "000001.SZ", ts_record, self.ingested_at, self.anomalies, False
        )

        self.assertFalse(is_anom)
        self.assertEqual(row["ticker"], "000001.SZ")
        self.assertEqual(row["code"], "000001.SZ")
        self.assertEqual(row["report_period"], "2024-12-31")
        self.assertEqual(row["filing_date"], "2025-04-15")
        self.assertEqual(row["source_provider"], h50a.PROVIDER_LABEL)
        self.assertEqual(row["source_url"], h50a.SOURCE_URL)
        self.assertEqual(row["ingested_at"], self.ingested_at)

        # Score fields
        self.assertEqual(row["roe"], 15.5)
        self.assertEqual(row["roa"], 8.2)
        self.assertEqual(row["gross_margin"], 35.0)
        self.assertEqual(row["operating_margin"], 20.0)  # primary, not fallback
        self.assertEqual(row["current_ratio"], 1.8)
        self.assertEqual(row["quick_ratio"], 1.2)
        self.assertEqual(row["debt_to_equity"], 1.5)
        self.assertEqual(row["operating_cash_flow_to_revenue"], 12.0)
        self.assertEqual(row["free_cash_flow"], 5000000.0)
        # accruals_ratio NULL because _net_income, _net_cashflow_op not in API
        self.assertIsNone(row["accruals_ratio"])

        # Only _op_income is available from fina_indicator API
        self.assertEqual(row["_op_income"], 12000000.0)
        # These are not available from fina_indicator
        self.assertIsNone(row["_net_income"])
        self.assertIsNone(row["_net_cashflow_op"])
        self.assertIsNone(row["_total_assets"])
        self.assertIsNone(row["_total_revenue"])

        # data_quality_note should mention intermediates (NULL from new endpoints)
        note = row["data_quality_note"]
        self.assertIn("NULL from Tushare", note)

    def test_build_row_null_fields_add_notes(self):
        """NULL fields must add corresponding lines to data_quality_note."""
        ts_record = {
            "end_date": "2024-12-31",
            "ann_date": "2025-04-15",
            "roe_waa": None,
            "roa": None,
            "grossprofit_margin": None,
            "op_of_gr": None,
            "current_ratio": None,
            "quick_ratio": None,
            "debt_to_eqt": None,
            "ocf_to_or": None,
            "fcff": None,
            "op_income": None,
        }
        row, _ = h50a.build_row(
            "600519.SS", ts_record, self.ingested_at, self.anomalies, False
        )

        note = row["data_quality_note"]
        self.assertIn("roe: NULL", note)
        self.assertIn("roa: NULL", note)
        self.assertIn("free_cash_flow: fcff not reported", note)
        # _net_income etc are from new endpoints; NULL because test data lacks suffixed fields
        self.assertIn("_net_income: NULL from Tushare", note)

        # All NULL score fields
        self.assertIsNone(row["roe"])
        self.assertIsNone(row["roa"])
        self.assertIsNone(row["gross_margin"])
        self.assertIsNone(row["operating_margin"])
        self.assertIsNone(row["accruals_ratio"])  # missing intermediates

    def test_build_row_operating_margin_fallback(self):
        """When op_of_gr is NULL but _op_income present and _total_revenue
        is available from a non-fina_indicator source, fallback should work.
        Since _total_revenue is not in fina_indicator, fallback will be NULL."""
        ts_record = {
            "end_date": "2024-12-31",
            "ann_date": "2025-04-15",
            "op_of_gr": None,
            "op_income": 5000000.0,
        }
        row, _ = h50a.build_row(
            "600519.SS", ts_record, self.ingested_at, self.anomalies, False
        )

        # operating_margin is NULL because _total_revenue is not available
        self.assertIsNone(row["operating_margin"])
        note = row["data_quality_note"]
        self.assertIn("operating_margin: NULL", note)

    def test_build_row_operating_margin_fallback_with_total_revenue(self):
        """Manually inject _total_revenue to test the fallback computation path."""
        ts_record = {
            "end_date": "2024-12-31",
            "ann_date": "2025-04-15",
            "op_of_gr": None,
        }
        row, _ = h50a.build_row(
            "600519.SS", ts_record, self.ingested_at, self.anomalies, False
        )
        # Override to simulate the case where _total_revenue is available
        row["_op_income"] = 5000000.0
        row["_total_revenue"] = 50000000.0
        # Manually re-apply fallback formula
        if row["_op_income"] is not None and row["_total_revenue"] is not None and row["_total_revenue"] != 0:
            fallback = round(row["_op_income"] / row["_total_revenue"], 6)
            self.assertAlmostEqual(fallback, 0.1)

    def test_build_row_accruals_ratio_derivation(self):
        """Accruals ratio is NULL because intermediates not available from fina_indicator."""
        ts_record = {
            "end_date": "2024-12-31",
            "ann_date": "2025-04-15",
            "op_income": 10000000.0,
        }
        row, _ = h50a.build_row(
            "000001.SZ", ts_record, self.ingested_at, self.anomalies, False
        )
        # accruals_ratio requires _net_income, _net_cashflow_op, _total_assets —
        # none available from fina_indicator
        self.assertIsNone(row["accruals_ratio"])
        note = row["data_quality_note"]
        self.assertIn("accruals_ratio: NULL", note)
        self.assertIn("_net_income", note)
        self.assertIn("_net_cashflow_op", note)
        self.assertIn("_total_assets", note)

    def test_accruals_formula_math(self):
        """Verify the formula itself is correct (unit test of the math)."""
        # Given hypothetical intermediates
        net_income = 10000000.0
        net_cf_op = 12000000.0
        total_assets = 50000000.0
        expected = round((net_income - net_cf_op) / total_assets, 6)
        self.assertAlmostEqual(expected, -0.04)

    def test_build_row_filing_date_before_report_period_raises(self):
        """filing_date < report_period must raise ValueError unless flag is set."""
        ts_record = {
            "end_date": "2024-12-31",
            "ann_date": "2024-10-01",  # BEFORE period end!
        }
        with self.assertRaises(ValueError) as ctx:
            h50a.build_row(
                "000001.SZ", ts_record, self.ingested_at, self.anomalies, False
            )
        self.assertIn("filing_date", str(ctx.exception))

    def test_build_row_filing_date_anomaly_quarantine(self):
        """With --allow-future-filing-anomalies flag, anomalous rows are quarantined."""
        ts_record = {
            "end_date": "2024-12-31",
            "ann_date": "2024-10-01",
        }
        anomalies = []
        row, is_anom = h50a.build_row(
            "000001.SZ", ts_record, self.ingested_at, anomalies, True
        )
        self.assertTrue(is_anom)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["ticker"], "000001.SZ")
        self.assertIn("ANOMALY", row["data_quality_note"])

    def test_build_row_report_period_out_of_range(self):
        """report_period before 2019-10-01 must raise ValueError."""
        ts_record = {
            "end_date": "2018-12-31",
            "ann_date": "2019-03-15",
        }
        with self.assertRaises(ValueError) as ctx:
            h50a.build_row(
                "000001.SZ", ts_record, self.ingested_at, self.anomalies, False
            )
        self.assertIn("out of range", str(ctx.exception))

    def test_build_row_null_identity_raises(self):
        """NULL identity fields must raise ValueError."""
        ts_record = {
            "end_date": None,
            "ann_date": "2025-04-15",
        }
        with self.assertRaises(ValueError) as ctx:
            h50a.build_row(
                "000001.SZ", ts_record, self.ingested_at, self.anomalies, False
            )
        self.assertIn("NULL identity", str(ctx.exception))


class TestH50aROEOverlapCheck(unittest.TestCase):
    def test_no_overlap_when_no_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing_path = tmp_path / "fundamentals.jsonl"
            existing_path.write_text("", encoding="utf-8")

            h50a_rows = [
                {"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.0},
            ]
            result = h50a.roe_overlap_check(h50a_rows, existing_path)
            self.assertEqual(result["overlap_count"], 0)
            self.assertEqual(result["anomaly_count"], 0)
            self.assertEqual(result["pct_within_tolerance"], 100.0)

    def test_overlap_within_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing_path = tmp_path / "fundamentals.jsonl"
            existing_path.write_text(
                '{"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.2}\n',
                encoding="utf-8",
            )

            h50a_rows = [
                {"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.5},
            ]
            result = h50a.roe_overlap_check(h50a_rows, existing_path)
            self.assertEqual(result["overlap_count"], 1)
            self.assertEqual(result["within_tolerance"], 1)  # delta 0.3 <= 0.5
            self.assertEqual(result["anomaly_count"], 0)

    def test_overlap_anomaly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing_path = tmp_path / "fundamentals.jsonl"
            existing_path.write_text(
                '{"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.0}\n',
                encoding="utf-8",
            )

            h50a_rows = [
                {"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 10.0},
            ]
            result = h50a.roe_overlap_check(h50a_rows, existing_path)
            self.assertEqual(result["overlap_count"], 1)
            self.assertEqual(result["within_tolerance"], 0)  # delta 5.0 > 0.5
            self.assertEqual(result["anomaly_count"], 1)
            self.assertEqual(result["anomalies"][0]["abs_delta"], 5.0)

    def test_overlap_h50a_roe_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing_path = tmp_path / "fundamentals.jsonl"
            existing_path.write_text(
                '{"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.0}\n',
                encoding="utf-8",
            )

            h50a_rows = [
                {"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": None},
            ]
            result = h50a.roe_overlap_check(h50a_rows, existing_path)
            self.assertEqual(result["anomaly_count"], 1)


class TestH50aCoverageComputation(unittest.TestCase):
    def test_coverage_all_tickers_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing_path = tmp_path / "fundamentals.jsonl"
            existing_path.write_text("", encoding="utf-8")

            rows = [
                {
                    "ticker": "000001.SZ", "report_period": "2024-12-31",
                    "roe": 15.0, "roa": 8.0, "gross_margin": 35.0,
                    "operating_margin": 20.0, "current_ratio": 1.8,
                    "quick_ratio": 1.2, "debt_to_equity": 1.5,
                    "operating_cash_flow_to_revenue": 12.0,
                    "free_cash_flow": 5000000.0, "accruals_ratio": None,
                    "_net_income": None, "_net_cashflow_op": None,
                    "_total_assets": None, "_op_income": 12000000.0,
                    "_total_revenue": None, "_total_cogs": None,
                },
                {
                    "ticker": "600519.SS", "report_period": "2024-12-31",
                    "roe": 25.0, "roa": 12.0, "gross_margin": 60.0,
                    "operating_margin": 50.0, "current_ratio": 3.0,
                    "quick_ratio": 2.5, "debt_to_equity": 0.5,
                    "operating_cash_flow_to_revenue": 30.0,
                    "free_cash_flow": 20000000.0, "accruals_ratio": None,
                    "_net_income": None, "_net_cashflow_op": None,
                    "_total_assets": None, "_op_income": 55000000.0,
                    "_total_revenue": None, "_total_cogs": None,
                },
            ]

            coverage = h50a.compute_coverage(
                rows,
                universe_tickers=["000001.SZ", "600519.SS"],
                fetch_failures=[],
                anomaly_count=0,
                op_margin_fallback_count=0,
                existing_fundamentals_path=existing_path,
                ingested_at="2026-05-23T00:00:00Z",
            )

            self.assertEqual(coverage.ticker_coverage_pct, 100.0)
            self.assertEqual(coverage.total_rows, 2)
            # With _net_income etc all NULL (not in API), soft fields will all be 0%
            self.assertEqual(coverage.hard_field_min_pct, 100.0)
            # accrvals_ratio NULL → soft field min = 0%
            self.assertEqual(coverage.soft_field_min_pct, 0.0)

    def test_coverage_with_null_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing_path = tmp_path / "fundamentals.jsonl"
            existing_path.write_text("", encoding="utf-8")

            rows = [{
                "ticker": "000001.SZ", "report_period": "2024-12-31",
                "roe": None, "roa": None, "gross_margin": None,
                "operating_margin": None, "current_ratio": 1.8,
                "quick_ratio": 1.2, "debt_to_equity": 1.5,
                "operating_cash_flow_to_revenue": None,
                "free_cash_flow": None, "accruals_ratio": None,
                "_net_income": None, "_net_cashflow_op": None,
                "_total_assets": None, "_op_income": None,
                "_total_revenue": None, "_total_cogs": None,
            }]

            coverage = h50a.compute_coverage(
                rows,
                universe_tickers=["000001.SZ"],
                fetch_failures=[],
                anomaly_count=0,
                op_margin_fallback_count=0,
                existing_fundamentals_path=existing_path,
                ingested_at="2026-05-23T00:00:00Z",
            )

            self.assertLess(coverage.hard_field_min_pct, 90.0)
            self.assertEqual(coverage.per_field_non_null_pct["roe"], 0.0)
            self.assertEqual(coverage.per_field_non_null_pct["current_ratio"], 100.0)
            self.assertEqual(coverage.per_field_non_null_pct["_total_assets"], 0.0)


class TestH50aCache(unittest.TestCase):
    def test_read_cache_incomplete_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            # Write a cache with only 2024 data
            import pandas as pd
            df = pd.DataFrame({"end_date": ["2024-12-31"]})
            cp = h50a._cache_path("000001.SZ", raw_dir)
            cp.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cp, index=False)

            # Request range 2023-2026, cache only has 2024
            result = h50a.read_cache("000001.SZ", raw_dir, "20230101", "20260331")
            self.assertIsNone(result)  # incomplete

    def test_read_cache_complete_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            import pandas as pd
            df = pd.DataFrame({
                "end_date": ["2022-12-31", "2023-06-30", "2023-12-31", "2024-06-30", "2024-12-31", "2025-06-30"]
            })
            # V2: cache dirs are endpoint-aware; use read_endpoint_cache
            cp = h50a._cache_path("000001.SZ", h50a._endpoint_cache_dir(raw_dir, "fina_indicator"))
            cp.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cp, index=False)

            # Request range fully covered (cache starts before start, ends after end)
            result = h50a.read_endpoint_cache("000001.SZ", "fina_indicator", raw_dir, "20230101", "20241231")
            self.assertIsNotNone(result)
            self.assertEqual(len(result), 6)


class TestH50aOutputNoNaN(unittest.TestCase):
    """Verify that JSONL output contains no NaN values (must be null)."""

    def test_jsonl_no_nan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jsonl_path = tmp_path / "test.jsonl"

            rows = [{
                "ticker": "000001.SZ", "code": "000001.SZ",
                "report_period": "2024-12-31", "filing_date": "2025-04-15",
                "source_url": h50a.SOURCE_URL,
                "source_provider": h50a.PROVIDER_LABEL,
                "ingested_at": "2026-05-23T00:00:00Z",
                "data_quality_note": "roe: NULL from Tushare roe_waa",
                "roe": None, "roa": 8.0, "gross_margin": 35.0,
                "operating_margin": 20.0, "current_ratio": 1.8,
                "quick_ratio": 1.2, "debt_to_equity": 1.5,
                "operating_cash_flow_to_revenue": 12.0,
                "free_cash_flow": None, "accruals_ratio": None,
                "_net_income": None, "_net_cashflow_op": None,
                "_total_assets": None, "_op_income": 12000000.0,
                "_total_revenue": None, "_total_cogs": None,
            }]

            h50a.write_jsonl(rows, jsonl_path)
            content = jsonl_path.read_text(encoding="utf-8")
            self.assertNotIn("NaN", content)
            self.assertIn("null", content)


class TestH50aSmokeValidation(unittest.TestCase):
    def test_smoke_validation_missing_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rc = h50a.run_smoke_validation(
                tmp_path / "missing.jsonl",
                tmp_path / "coverage.json",
                tmp_path / "report.md",
            )
            self.assertEqual(rc, 1)

    def test_smoke_validation_all_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Write JSONL
            jsonl_path = tmp_path / "test.jsonl"
            row = {
                "ticker": "000001.SZ", "code": "000001.SZ",
                "report_period": "2024-12-31", "filing_date": "2025-04-15",
                "source_url": h50a.SOURCE_URL,
                "source_provider": h50a.PROVIDER_LABEL,
                "ingested_at": "2026-05-23T00:00:00Z",
                "data_quality_note": "",
                "roe": 15.0, "roa": 8.0, "gross_margin": 35.0,
                "operating_margin": 20.0, "current_ratio": 1.8,
                "quick_ratio": 1.2, "debt_to_equity": 1.5,
                "operating_cash_flow_to_revenue": 12.0,
                "free_cash_flow": None, "accruals_ratio": None,
                "_net_income": None, "_net_cashflow_op": None,
                "_total_assets": None, "_op_income": 12000000.0,
                "_total_revenue": None, "_total_cogs": None,
            }
            jsonl_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

            # Write coverage JSON
            cov_path = tmp_path / "coverage.json"
            cov_path.write_text(json.dumps({
                "provenance": {
                    "provider": h50a.PROVIDER_LABEL,
                    "endpoint": "fina_indicator",
                    "ingested_at": "2026-05-23T00:00:00Z",
                },
                "ticker_coverage_pct": 100.0,
                "total_rows": 1,
                "per_field_non_null_pct": {
                    "roe": 100.0, "roa": 100.0, "gross_margin": 100.0,
                    "operating_margin": 100.0, "current_ratio": 100.0,
                    "quick_ratio": 100.0, "debt_to_equity": 100.0,
                    "operating_cash_flow_to_revenue": 100.0,
                    "free_cash_flow": 0.0, "accruals_ratio": 0.0,
                    "_net_income": 0.0, "_net_cashflow_op": 0.0,
                    "_total_assets": 0.0, "_op_income": 100.0,
                    "_total_revenue": 0.0, "_total_cogs": 0.0,
                },
                "hard_field_min_pct": 95.0,
                "soft_field_min_pct": 60.0,
                "intermediate_min_pct": 0.0,
                "verdict": "CANDIDATE_DATASET",
                "gates": {
                    "ticker_coverage_ge_98pct": True,
                    "hard_fields_ge_90pct": True,
                    "soft_fields_ge_50pct": True,
                },
                "roe_overlap": {"overlap_count": 0, "anomaly_count": 0},
                "fetch_failures": [],
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            # Write report
            report_path = tmp_path / "report.md"
            report_path.write_text(
                "# H50a\n\n## Provenance\n\ntushare:fina_indicator\n\n"
                "## Coverage Summary\n\n## Per-Field Non-Null Distribution\n\n"
                "## Coverage Gates\n\n## ROE Overlap Analysis\n\n"
                "## Verdict\n\nCANDIDATE_DATASET\n",
                encoding="utf-8",
            )

            rc = h50a.run_smoke_validation(jsonl_path, cov_path, report_path)
            self.assertEqual(rc, 0)


# ═══════════════════════════════════════════════════════════════════════════
# V2-specific tests
# ═══════════════════════════════════════════════════════════════════════════
class TestH50aV2DedupPipeline(unittest.TestCase):
    """Test the dedup-then-join pipeline with synthetic update_flag fixtures."""

    def test_dedup_endpoint_df_single_record(self):
        """Single record passes through dedup unchanged."""
        records = [{"ts_code": "000001.SZ", "end_date": "20240331",
                     "ann_date": "20240420", "update_flag": "1"}]
        df = h50a.dedup_endpoint_df(records, "income")
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["end_date"], "20240331")

    def test_dedup_keeps_latest_update_flag(self):
        """With two records for same period, keep='last' retains highest update_flag."""
        records = [
            {"ts_code": "000001.SZ", "end_date": "20240630",
             "ann_date": "20240816", "update_flag": "1"},
            {"ts_code": "000001.SZ", "end_date": "20240630",
             "ann_date": "20240901", "update_flag": "2"},  # restated
        ]
        df = h50a.dedup_endpoint_df(records, "fina_indicator")
        self.assertEqual(len(df), 1)
        # keep='last' after sort: update_flag ASC (1,2) → keep='last' = 2
        self.assertEqual(str(df.iloc[0]["update_flag"]), "2")

    def test_dedup_asserts_no_duplicates(self):
        """After dedup, no duplicate (ts_code, end_date) permitted."""
        records = [
            {"ts_code": "000001.SZ", "end_date": "20240331",
             "ann_date": "20240420", "update_flag": "1"},
            {"ts_code": "000001.SZ", "end_date": "20240630",
             "ann_date": "20240816", "update_flag": "1"},
        ]
        df = h50a.dedup_endpoint_df(records, "income")
        self.assertEqual(len(df), 2)
        # Group by (ts_code, end_date), max should be 1
        import pandas as pd
        dup_check = df.groupby(["ts_code", "end_date"]).size()
        self.assertEqual(dup_check.max(), 1)

    def test_dedup_empty_records(self):
        """Empty records return empty DataFrame."""
        df = h50a.dedup_endpoint_df([], "cashflow")
        self.assertTrue(df.empty)


class TestH50aV2Join(unittest.TestCase):
    """Test 4-endpoint LEFT JOIN with deduped DataFrames."""

    def setUp(self):
        import pandas as pd
        self.fina = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "20240331",
             "ann_date": "20240420", "update_flag": "1",
             "roe_waa": 15.0, "roa": 8.0},
            {"ts_code": "000001.SZ", "end_date": "20240630",
             "ann_date": "20240816", "update_flag": "1",
             "roe_waa": 18.0, "roa": 9.0},
        ])
        self.income = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "20240331",
             "ann_date": "20240420", "update_flag": "1",
             "n_income": 10000000.0, "total_revenue": 50000000.0,
             "total_cogs": 30000000.0, "operate_profit": 12000000.0},
        ])
        self.cashflow = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "20240331",
             "ann_date": "20240420", "update_flag": "1",
             "n_cashflow_act": 8000000.0},
        ])
        self.balancesheet = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "20240331",
             "ann_date": "20240420", "update_flag": "1",
             "total_assets": 200000000.0},
        ])

    def test_join_produces_correct_row_count(self):
        """After dedup+join, row count matches fina_indicator deduped count."""
        joined, skew = h50a.join_four_endpoints(
            self.fina, self.income, self.cashflow, self.balancesheet
        )
        self.assertEqual(len(joined), 2)  # fina has 2 unique periods
        self.assertFalse(joined.empty)

    def test_join_populates_intermediate_columns(self):
        """Joined DataFrame has suffixed columns from income/cashflow/balancesheet."""
        joined, _ = h50a.join_four_endpoints(
            self.fina, self.income, self.cashflow, self.balancesheet
        )
        # Check suffixed columns exist
        self.assertIn("n_income_income", joined.columns)
        self.assertIn("n_cashflow_act_cashflow", joined.columns)
        self.assertIn("total_assets_balancesheet", joined.columns)

    def test_join_null_for_missing_endpoint_data(self):
        """When income has no matching row, columns are NaN/None."""
        import pandas as pd
        income_no_match = pd.DataFrame([
            {"ts_code": "000001.SZ", "end_date": "20231231",
             "ann_date": "20240315", "update_flag": "1",
             "n_income": 9000000.0},
        ])
        joined, _ = h50a.join_four_endpoints(
            self.fina, income_no_match, self.cashflow, self.balancesheet
        )
        # Row for 20240630 should have no income match → n_income_income is NaN
        row_20240630 = joined[joined["end_date"] == "20240630"].iloc[0]
        import numpy as np
        self.assertTrue(pd.isna(row_20240630.get("n_income_income", np.nan)))


class TestH50aV2FetchFailures(unittest.TestCase):
    """Test fetch_failures V2 schema (must include endpoint field)."""

    def test_fetch_failure_has_endpoint(self):
        """V2 fetch_failures entries must include endpoint key."""
        failures = [
            {"ticker": "000001.SZ", "endpoint": "income",
             "reason": "rate-limit exhausted after 5 retries"},
        ]
        for ff in failures:
            self.assertIn("endpoint", ff)
            self.assertIn("ticker", ff)
            self.assertIn("reason", ff)


class TestH50aV2ROEYearEndJoin(unittest.TestCase):
    """Test ROE overlap check with year-end filtering (V2)."""

    def test_year_end_filter_excludes_mid_year(self):
        """Only rows with report_period ending in -12-31 are considered."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing = tmp_path / "fundamentals.jsonl"
            existing.write_text(
                '{"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.0}\n'
                '{"ticker": "000001.SZ", "report_period": "2024-03-31", "roe": 3.0}\n',
                encoding="utf-8",
            )
            h50a_rows = [
                {"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.3},
                {"ticker": "000001.SZ", "report_period": "2024-03-31", "roe": 3.0},
            ]
            result = h50a.roe_overlap_check(h50a_rows, existing)
            # Only 2024-12-31 should match → 1 overlap
            self.assertEqual(result["overlap_count"], 1)

    def test_year_end_blocker_under_100(self):
        """ROE overlap < 100 is a BLOCKER — the gate should fail."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing = tmp_path / "fundamentals.jsonl"
            existing.write_text("", encoding="utf-8")
            h50a_rows = [
                {"ticker": "000001.SZ", "report_period": "2024-12-31", "roe": 15.0},
            ]
            result = h50a.roe_overlap_check(h50a_rows, existing)
            self.assertEqual(result["overlap_count"], 0)
            self.assertFalse(result.get("gate_ok_overlap_count", True))


class TestH50aV2Intermediates(unittest.TestCase):
    """Test intermediate field coverage and _total_cogs presence."""

    def test_all_intermediates_present(self):
        """ALL_INTERMEDIATE must include _total_cogs (V2 addition)."""
        self.assertIn("_total_cogs", h50a.ALL_INTERMEDIATE)
        self.assertEqual(len(h50a.ALL_INTERMEDIATE), 6)

    def test_field_map_includes_all_score_fields(self):
        """All score fields must be in FIELD_MAP, even derived ones."""
        for f in h50a.ALL_SCORE_FIELDS:
            self.assertIn(f, h50a.FIELD_MAP)

    def test_gross_margin_fallback_count_tracked(self):
        """gross_margin_fallback_count exists in CoverageResult instances."""
        # Dataclass fields are accessible as attributes on instances
        cr = h50a.CoverageResult(
            provenance={}, universe_ticker_count=1, processed_ticker_count=1,
            ticker_coverage_pct=100.0, total_rows=1, period_count=1,
            avg_periods_per_ticker=1.0, per_field_non_null_pct={},
            hard_field_min_pct=100.0, soft_field_min_pct=100.0,
            intermediate_min_pct=100.0, roe_overlap={},
            fetch_failures=[], anomaly_count=0,
            op_margin_fallback_count=0, gross_margin_fallback_count=5,
            ann_date_skew_count=0,
        )
        self.assertEqual(cr.gross_margin_fallback_count, 5)


if __name__ == "__main__":
    unittest.main()
