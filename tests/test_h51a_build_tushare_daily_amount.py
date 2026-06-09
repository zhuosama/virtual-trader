#!/usr/bin/env python3
"""Tests for H51a — Risk Model ADTV Data Ingestion."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h51a_build_tushare_daily_amount as h51a  # noqa: E402


class TestH51aTickerConversion(unittest.TestCase):
    def test_yahoo_to_tushare_ss(self):
        self.assertEqual(h51a.yahoo_to_tushare_code("600519.SS"), "600519.SH")

    def test_yahoo_to_tushare_sz(self):
        self.assertEqual(h51a.yahoo_to_tushare_code("000858.SZ"), "000858.SZ")

    def test_yahoo_to_tushare_invalid(self):
        with self.assertRaises(ValueError):
            h51a.yahoo_to_tushare_code("000001.XSHE")


class TestH51aDateHelpers(unittest.TestCase):
    def test_compact_date(self):
        self.assertEqual(h51a.compact_date("2023-10-01"), "20231001")

    def test_dashed_date(self):
        self.assertEqual(h51a.dashed_date("20231001"), "2023-10-01")
        self.assertEqual(h51a.dashed_date("2023-10-01"), "2023-10-01")

    def test_parse_iso_date(self):
        d = h51a.parse_iso_date("2023-10-01")
        self.assertEqual(d.year, 2023)
        self.assertEqual(d.month, 10)
        self.assertEqual(d.day, 1)


class TestH51aUniverseLoading(unittest.TestCase):
    def test_load_tickers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            rows = [
                {"ticker": "600519.SS", "weight": 5.0},
                {"ticker": "000858.SZ", "weight": 3.0},
                {"ticker": "600519.SS", "weight": 5.5},  # duplicate → deduped
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )
            tickers = h51a.load_universe_tickers(path)
            self.assertEqual(tickers, ["000858.SZ", "600519.SS"])

    def test_load_tickers_skips_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.jsonl"
            path.write_text(
                '{"ticker": "600519.SS"}\n\n{"ticker": ""}\n',
                encoding="utf-8",
            )
            tickers = h51a.load_universe_tickers(path)
            self.assertEqual(tickers, ["600519.SS"])


class TestH51aVolConversion(unittest.TestCase):
    """Verify that vol_shares = Tushare vol × 100 (手 → shares)."""

    def test_vol_conversion_factor(self):
        import pandas as pd
        from io import StringIO

        raw_csv = StringIO("""trade_date,ts_code,amount,vol
20240102,000001.SZ,1075742.252,1158366.45
20240103,000001.SZ,673673.614,733610.31
""")
        raw_df = pd.read_csv(raw_csv)
        normalized = h51a.normalize_daily_frame(raw_df, "000001.SZ")

        # vol_shares should be ~100× the raw Tushare vol
        self.assertAlmostEqual(normalized.iloc[0]["vol_shares"], 115836645.0, delta=1.0)
        self.assertAlmostEqual(normalized.iloc[1]["vol_shares"], 73361031.0, delta=1.0)

        # Source column
        self.assertEqual(normalized.iloc[0]["source"], "tushare:daily")

    def test_normalize_empty_frame(self):
        import pandas as pd
        df = pd.DataFrame()
        result = h51a.normalize_daily_frame(df, "000001.SZ")
        self.assertTrue(result.empty)
        self.assertEqual(
            list(result.columns),
            ["date", "ticker", "amount_rmb", "vol_shares", "source"],
        )

    def test_normalize_schema(self):
        import pandas as pd
        from io import StringIO

        raw_csv = StringIO("""trade_date,ts_code,amount,vol
20240102,000001.SZ,100.0,200.0
""")
        raw_df = pd.read_csv(raw_csv)
        normalized = h51a.normalize_daily_frame(raw_df, "000001.SZ")
        self.assertEqual(
            list(normalized.columns),
            ["date", "ticker", "amount_rmb", "vol_shares", "source"],
        )
        self.assertEqual(normalized.iloc[0]["date"], "2024-01-02")
        self.assertEqual(normalized.iloc[0]["ticker"], "000001.SZ")
        self.assertEqual(normalized.iloc[0]["amount_rmb"], 100000.0)  # 100 × 1000 (千元→RMB)
        self.assertEqual(normalized.iloc[0]["vol_shares"], 20000.0)  # 200 × 100
        self.assertEqual(normalized.iloc[0]["source"], "tushare:daily")


class TestH51aCache(unittest.TestCase):
    def test_cache_path_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            cp = h51a.cache_path_for("000001.SZ", raw_dir)
            self.assertEqual(cp.name, "000001.SZ.csv")

    def test_cache_path_for_ss(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            cp = h51a.cache_path_for("600519.SS", raw_dir)
            self.assertEqual(cp.name, "600519.SH.csv")

    def test_write_and_read_cache(self):
        import pandas as pd
        from io import StringIO

        raw_csv = StringIO("""trade_date,ts_code,amount,vol
20240102,000001.SZ,100.0,200.0
20240103,000001.SZ,150.0,250.0
20240131,000001.SZ,200.0,300.0
""")
        df = pd.read_csv(raw_csv)

        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            h51a.write_cache("000001.SZ", raw_dir, df)
            cp = h51a.cache_path_for("000001.SZ", raw_dir)
            self.assertTrue(cp.exists())

            # Read back with cache covering 2024-01-02 to 2024-01-31
            cached = h51a.read_cache("000001.SZ", raw_dir, "2024-01-02", "2024-01-31")
            self.assertIsNotNone(cached)
            self.assertEqual(len(cached), 3)

            # Cache should be insufficient for range entirely outside cache window
            cached2 = h51a.read_cache("000001.SZ", raw_dir, "2023-11-01", "2023-12-31")
            self.assertIsNone(cached2)

    def test_read_cache_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = h51a.read_cache("000001.SZ", Path(tmp), "2024-01-01", "2024-01-31")
            self.assertIsNone(result)


class TestH51aADTVGate(unittest.TestCase):
    def test_adtv_computability(self):
        import pandas as pd
        from io import StringIO

        # 30 consecutive trading days of data for one ticker
        # Window: Jan 10-31 → only days ≥10 are eval pairs
        # On eval_date Jan 10: lookback = 10 rows (days 1-10) → ADTV computable (≥10 non-NULL)
        # So ADTV should be 100% computable for all eval pairs in window
        rows = []
        for i in range(1, 31):
            rows.append(f"2024-01-{i:02d},000001.SZ,{1000000+i*10000},{200000+i*1000}")

        csv_str = "date,ticker,amount_rmb,vol_shares,source\n" + "\n".join(rows)
        df = pd.read_csv(StringIO(csv_str))

        # Window starts at day 10 — first 9 days excluded
        windows = {"test_window": ("2024-01-10", "2024-01-30")}
        result = h51a.compute_adtv_gate(df, windows)

        # From day 10 onward, each eval_date has ≥10 prior rows → 100%
        self.assertEqual(result["overall_pct"], 100.0)

    def test_adtv_empty(self):
        import pandas as pd
        df = pd.DataFrame(columns=["date", "ticker", "amount_rmb", "vol_shares", "source"])
        result = h51a.compute_adtv_gate(df, {"w": ("2024-01-01", "2024-01-31")})
        self.assertEqual(result["overall_pct"], 0.0)


class TestH51aCoverageBuild(unittest.TestCase):
    def test_coverage_structure(self):
        import pandas as pd
        from io import StringIO

        csv_str = """date,ticker,amount_rmb,vol_shares,source
2024-01-02,000001.SZ,100.0,20000.0,tushare:daily
2024-01-03,000001.SZ,150.0,25000.0,tushare:daily
"""
        df = pd.read_csv(StringIO(csv_str))
        tickers = ["000001.SZ", "000002.SZ"]
        failures = []
        adtv = h51a.compute_adtv_gate(df, {"cal_2024": ("2024-01-02", "2024-01-31")})

        cov = h51a.build_coverage(df, tickers, failures, adtv, "2026-05-23")

        self.assertEqual(cov["task"], "H51a")
        self.assertEqual(cov["universe_ticker_count"], 2)
        self.assertEqual(cov["ticker_coverage_count"], 1)
        self.assertEqual(cov["ticker_coverage_pct"], 50.0)
        self.assertEqual(cov["total_rows"], 2)
        self.assertIn("provenance", cov)
        self.assertEqual(cov["provenance"]["provider"], "tushare:daily")
        self.assertIn("snapshot_date", cov)
        self.assertEqual(cov["vol_unit"], "shares (absolute, ×100 from Tushare 手)")
        self.assertEqual(cov["columns"], ["date", "ticker", "amount_rmb", "vol_shares", "source"])
        self.assertEqual(len(cov["fetch_failures"]), 0)
        self.assertIn("gates", cov)
        self.assertIn("verdict", cov)


class TestH51aReportGeneration(unittest.TestCase):
    def test_report_contains_sections(self):
        import pandas as pd
        from io import StringIO

        csv_str = """date,ticker,amount_rmb,vol_shares,source
2024-01-02,000001.SZ,100.0,20000.0,tushare:daily
"""
        df = pd.read_csv(StringIO(csv_str))
        tickers = ["000001.SZ"]
        failures = [{"ticker": "000002.SZ", "ts_code": "000002.SZ", "reason": "timeout"}]
        adtv = h51a.compute_adtv_gate(df, {"cal_2024": ("2024-01-02", "2024-12-31")})
        cov = h51a.build_coverage(df, tickers, failures, adtv, "2026-05-23")

        report = h51a.build_report(cov)
        self.assertIn("H51a", report)
        self.assertIn("Coverage Summary", report)
        self.assertIn("Coverage Gates", report)
        self.assertIn("ADTV Computability", report)
        self.assertIn("Fetch Failures", report)
        self.assertIn("Vol Unit Conversion", report)
        self.assertIn("000002.SZ", report)  # failure ticker
        self.assertIn("timeout", report)


class TestH51aArtifactSelection(unittest.TestCase):
    """Validate that artifact specs for h51a are registered correctly."""

    def test_h51a_registered_in_specs(self):
        from scripts.validate_hxx_artifacts import artifact_specs
        specs = artifact_specs()
        self.assertIn("h51a", specs)
        spec = specs["h51a"]
        self.assertEqual(spec.name, "h51a")
        self.assertTrue(spec.json_path.name.endswith("_coverage_h51a.json"))
        self.assertTrue(spec.report_path.name.endswith("h51a_daily_amount_ingestion_report.md"))

    def test_h51a_validator_function(self):
        """Smoke: validator returns empty errors for a well-formed coverage JSON."""
        cov = {
            "task": "H51a",
            "snapshot_date": "2026-05-23",
            "provenance": {"provider": "tushare:daily", "source_url": "https://tushare.pro/", "snapshot_date": "2026-05-23"},
            "ticker_coverage_pct": 98.5,
            "avg_rows_per_ticker": 620.0,
            "total_rows": 300000,
            "verdict": "CANDIDATE_DATASET",
            "columns": ["date", "ticker", "amount_rmb", "vol_shares", "source"],
            "vol_unit": "shares (absolute, ×100 from Tushare 手)",
            "fetch_failures": [{"ticker": "X", "reason": "test"}],
            "fetch_failures_count": 1,
            "gates": {
                "ticker_coverage_ge_98pct": True,
                "avg_rows_per_ticker_ge_600": True,
                "adtv_computable_ge_95pct": True,
                "adtv_computable_per_window_ge_95pct": True,
                "fetch_failures_le_10": True,
            },
            "adtv_computability_per_window": {
                "cal_2024": {"start": "2024-01-02", "end": "2024-12-31", "ticker_date_pairs": 114655, "computable_pct": 100.0},
                "h1_2025": {"start": "2025-01-02", "end": "2025-06-30", "ticker_date_pairs": 55193, "computable_pct": 99.99},
                "h2_2025": {"start": "2025-07-01", "end": "2025-12-31", "ticker_date_pairs": 59273, "computable_pct": 99.98},
                "ytd_2026": {"start": "2026-01-02", "end": "2026-05-21", "ticker_date_pairs": 41860, "computable_pct": 100.0},
                "deploy_2025_2026": {"start": "2025-01-02", "end": "2026-05-21", "ticker_date_pairs": 156326, "computable_pct": 99.99},
            },
            "adtv_gate": {
                "overall_pct": 97.0,
                "per_window": {"cal_2024": 97.0, "h1_2025": 96.5},
            },
        }
        report = "# H51a\n\n## Coverage Summary\ntushare:daily\n\n## Coverage Gates\n\n## ADTV Computability by Window\n\n## Fetch Failures\n\n## Vol Unit Conversion\n\n## Verdict\nCANDIDATE_DATASET"
        from scripts.validate_hxx_artifacts import validate_h51a
        errors = validate_h51a(cov, report)
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_h51a_validator_rejects_missing_provenance(self):
        cov = {"task": "H51a", "verdict": "CANDIDATE_DATASET", "fetch_failures": []}
        report = ""
        from scripts.validate_hxx_artifacts import validate_h51a
        errors = validate_h51a(cov, report)
        self.assertTrue(len(errors) > 0)


class TestH51aAmountRmbUnitConversion(unittest.TestCase):
    """Edit 4.1 — verify ×1000 conversion from Tushare 千元 to RMB."""

    def test_amount_rmb_unit_conversion(self):
        """Synthesize a Tushare-shaped DataFrame; assert amount_rmb = amount × 1000."""
        import pandas as pd
        from io import StringIO

        raw_csv = StringIO("""trade_date,ts_code,amount,vol
20240102,000001.SZ,1075.742,1158366.45
20240103,000001.SZ,673.674,733610.31
""")
        raw_df = pd.read_csv(raw_csv)
        normalized = h51a.normalize_daily_frame(raw_df, "000001.SZ")

        # Tushare amount is in 千元 → × 1000 to get RMB
        self.assertAlmostEqual(normalized.iloc[0]["amount_rmb"], 1075742.0, delta=1.0)
        self.assertAlmostEqual(normalized.iloc[1]["amount_rmb"], 673674.0, delta=1.0)

        # vol conversion unchanged: 手 × 100 = shares
        self.assertAlmostEqual(normalized.iloc[0]["vol_shares"], 115836645.0, delta=1.0)

    def test_amount_rmb_implied_price_sanity(self):
        """Load actual CSV and verify median(amount_rmb / vol_shares) ∈ [0.5, 5000] RMB."""
        import pandas as pd

        csv_path = ROOT / "data/cn_pit/liquidity_h51a_daily_amount.csv"
        if not csv_path.exists():
            self.skipTest(f"CSV not found: {csv_path}")

        df = pd.read_csv(csv_path)
        # Filter non-NULL, positive rows
        mask = (df["vol_shares"] > 0) & (df["amount_rmb"] > 0)
        valid = df[mask]
        if len(valid) < 100:
            self.skipTest(f"only {len(valid)} valid price samples")

        implied = valid["amount_rmb"] / valid["vol_shares"]
        median = implied.median()
        self.assertGreater(median, 0.5, f"median implied price {median:.4f} < 0.5 RMB (units bug?)")
        self.assertLess(median, 5000.0, f"median implied price {median:.2f} > 5000 RMB (anomalous)")


if __name__ == "__main__":
    unittest.main()
