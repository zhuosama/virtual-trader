#!/usr/bin/env python3
"""Tests for point-in-time fundamentals source."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = os.path.expanduser("~/.hermes/virtual-trader")
sys.path.insert(0, os.path.join(ROOT, "backtest", "experiments"))

from fundamental_backtest import (  # noqa: E402
    CN_PIT_FileSource,
    FundamentalRecord,
    run_fundamental_backtest,
)


def write_jsonl(path, rows):
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestFundamentalRecord(unittest.TestCase):
    def test_visible_only_on_or_after_filing_date(self):
        rec = FundamentalRecord("600519.SS", filing_date="2025-04-20")
        self.assertFalse(rec.is_visible_as_of("2025-04-19"))
        self.assertTrue(rec.is_visible_as_of("2025-04-20"))
        self.assertTrue(rec.is_visible_as_of("2025-04-21"))

    def test_missing_filing_date_is_not_visible(self):
        rec = FundamentalRecord("600519.SS")
        self.assertFalse(rec.is_visible_as_of("2025-04-20"))


class TestCNPITFileSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        write_jsonl(os.path.join(self.root, "universe.jsonl"), [
            {
                "ticker": "600519.SS",
                "effective_date": "2025-01-01",
                "end_date": "",
                "source_url": "https://www.csindex.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight",
                "snapshot_count": 3,
            },
            {
                "ticker": "000858.SZ",
                "effective_date": "2025-06-01",
                "end_date": "2025-12-31",
                "source_url": "https://www.csindex.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight",
                "snapshot_count": 2,
            },
        ])
        write_jsonl(os.path.join(self.root, "universe_snapshots.jsonl"), [
            {
                "ticker": "600519.SS",
                "trade_date": "2025-01-01",
                "source_provider": "tushare:index_weight",
            },
            {
                "ticker": "600519.SS",
                "trade_date": "2025-06-01",
                "source_provider": "tushare:index_weight",
            },
            {
                "ticker": "000858.SZ",
                "trade_date": "2025-06-01",
                "source_provider": "tushare:index_weight",
            },
            # H24A: add late snapshot to cover period-aware gate
            {"ticker": "600519.SS", "trade_date": "2025-07-15",
             "source_provider": "tushare:index_weight"},
            {"ticker": "000858.SZ", "trade_date": "2025-07-15",
             "source_provider": "tushare:index_weight"},
        ])
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
            {
                "ticker": "600519.SS",
                "report_period": "2024-12-31",
                "filing_date": "2025-04-20",
                "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "roe": 30.0,
                "debt_to_equity": 0.2,
            },
            {
                "ticker": "600519.SS",
                "report_period": "2023-12-31",
                "filing_date": "2024-04-20",
                "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "roe": 25.0,
                "debt_to_equity": 0.3,
            },
            {
                "ticker": "000858.SZ",
                "report_period": "2024-12-31",
                "filing_date": "2025-04-25",
                "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "roe": 22.0,
                "debt_to_equity": 0.4,
            },
        ])
        dates = pd.bdate_range("2025-01-01", periods=140)
        prices = pd.DataFrame({
            "date": dates,
            "600519.SS": [100 + i * 0.03 for i in range(len(dates))],
            "000858.SZ": [80 + i * 0.02 for i in range(len(dates))],
            "000300.SS": [4000 + i for i in range(len(dates))],
        })
        prices.to_csv(os.path.join(self.root, "prices.csv"), index=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_source_has_no_data_quality_flags(self):
        source = CN_PIT_FileSource(self.root)
        self.assertFalse(source.research_only)
        self.assertTrue(source.data_quality.is_clean)
        self.assertEqual(source.validation_errors, [])

    def test_universe_is_point_in_time_and_price_universe_is_union(self):
        source = CN_PIT_FileSource(self.root)
        self.assertEqual(source.get_universe("2025-05-01"), ["600519.SS"])
        self.assertEqual(
            source.get_universe("2025-06-15"),
            ["000858.SZ", "600519.SS"],
        )
        self.assertEqual(source.get_universe("2026-01-01"), ["600519.SS"])
        self.assertEqual(
            source.get_price_universe("2025-01-01", "2025-12-31"),
            ["000858.SZ", "600519.SS"],
        )

    def test_candidate_universe_paths_are_supported(self):
        alt_universe = os.path.join(self.root, "universe_candidate.jsonl")
        alt_snapshots = os.path.join(self.root, "universe_snapshots_candidate.jsonl")
        write_jsonl(alt_universe, [
            {
                "ticker": "600000.SS",
                "effective_date": "2025-01-01",
                "end_date": "",
                "source_url": "https://tushare.pro/document/2?doc_id=96",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight",
                "snapshot_count": 1,
            }
        ])
        write_jsonl(alt_snapshots, [
            {
                "ticker": "600000.SS",
                "trade_date": "2025-01-01",
                "source_provider": "tushare:index_weight",
            }
        ])

        source = CN_PIT_FileSource(
            self.root,
            universe_path=alt_universe,
            universe_snapshots_path=alt_snapshots,
        )

        self.assertEqual(source.get_universe("2025-05-01"), ["600000.SS"])

    def test_fundamentals_are_gated_by_filing_date(self):
        source = CN_PIT_FileSource(self.root)
        before = source.get_fundamentals(["600519.SS"], "2025-04-19")
        self.assertEqual(before["600519.SS"]["report_period"], "2023-12-31")

        after = source.get_fundamentals(["600519.SS"], "2025-04-20")
        self.assertEqual(after["600519.SS"]["report_period"], "2024-12-31")
        self.assertEqual(after["600519.SS"]["roe"], 30.0)

    def test_invalid_source_stays_research_only(self):
        with tempfile.TemporaryDirectory() as bad_root:
            write_jsonl(os.path.join(bad_root, "universe.jsonl"), [
                {"ticker": "600519.SS", "effective_date": "2025-01-01"}
            ])
            source = CN_PIT_FileSource(bad_root)
            self.assertTrue(source.research_only)
            self.assertFalse(source.data_quality.is_clean)
            self.assertTrue(source.validation_errors)

    def test_survivorship_note_blocks_deployment(self):
        universe_path = os.path.join(self.root, "universe.jsonl")
        with open(universe_path) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        rows[0]["data_quality_note"] = "SURVIVORSHIP_BIAS: current constituents only"
        write_jsonl(universe_path, rows)

        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.research_only)
        self.assertTrue(source.data_quality.survivorship_bias)
        self.assertFalse(source.data_quality.is_clean)

        result = run_fundamental_backtest(
            source,
            start_date="2025-01-01",
            end_date="2025-07-15",
            capital=500000,
            top_n=2,
            max_position_pct=0.10,
            rebalance_freq_days=30,
        )
        self.assertIn("data_quality: survivorship_bias=true", result.deploy_blockers)
        self.assertIn("research_only: no deployment permitted", result.deploy_blockers)
        self.assertFalse(result.can_deploy)

    def test_backtest_with_clean_source_has_no_data_quality_blockers(self):
        source = CN_PIT_FileSource(self.root)
        result = run_fundamental_backtest(
            source,
            start_date="2025-01-01",
            end_date="2025-07-15",
            capital=500000,
            top_n=2,
            max_position_pct=0.10,
            rebalance_freq_days=30,
        )
        self.assertFalse(any(b.startswith("data_quality:") for b in result.deploy_blockers))
        self.assertNotIn("research_only: no deployment permitted", result.deploy_blockers)

    def test_backtest_hs300_return_uses_last_valid_benchmark_value(self):
        prices = pd.DataFrame({
            "date": pd.bdate_range("2025-05-01", periods=5),
            "600519.SS": [100, 101, 102, 103, 104],
            "000858.SZ": [80, 81, 82, 83, 84],
            "000300.SS": [4000, 4040, 4080, 4100, None],
        })
        prices.to_csv(os.path.join(self.root, "prices.csv"), index=False)

        source = CN_PIT_FileSource(self.root)
        result = run_fundamental_backtest(
            source,
            start_date="2025-05-01",
            end_date="2025-05-07",
            capital=500000,
            top_n=2,
            max_position_pct=0.10,
            rebalance_freq_days=30,
        )

        self.assertAlmostEqual(result.metrics["hs300_return"], 0.025)
        self.assertFalse(pd.isna(result.metrics["hs300_return"]))


# ================================================================
# H24A: Data Quality Gate Hardening Tests (M2)
# ================================================================
class TestH24ADataQualityGate(unittest.TestCase):
    """H24A M2: false-deploy tests for data quality gate hardening."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _make_source(self, universe_rows, snapshot_rows=None, fund_rows=None,
                     price_rows=None):
        """Helper to create CN_PIT_FileSource with controlled data."""
        write_jsonl(os.path.join(self.root, "universe.jsonl"), universe_rows)
        if snapshot_rows is not None:
            write_jsonl(os.path.join(self.root, "universe_snapshots.jsonl"), snapshot_rows)
        else:
            write_jsonl(os.path.join(self.root, "universe_snapshots.jsonl"), [
                {"ticker": r["ticker"], "trade_date": r["effective_date"],
                 "source_provider": r.get("source_provider", "tushare:index_weight")}
                for r in universe_rows
                if r.get("source_provider") in ("tushare:index_weight", "qlib:instruments")
            ])
        if fund_rows is not None:
            write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), fund_rows)
        else:
            write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
                {"ticker": r["ticker"], "report_period": "2020-12-31",
                 "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                 "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0}
                for r in universe_rows
            ])
        if price_rows is not None:
            pd.DataFrame(price_rows).to_csv(
                os.path.join(self.root, "prices.csv"), index=False)
        else:
            pd.DataFrame({
                "date": ["2020-01-01", "2020-06-30"],
                "600519.SS": [100.0, 101.0],
                "000300.SS": [4000, 4010],
            }).to_csv(os.path.join(self.root, "prices.csv"), index=False)

    def test_bogus_snapshot_unrelated_ticker_does_not_clear_bias(self):
        """M2: snapshot with ticker X, universe row for ticker Y → blocked."""
        self._make_source(
            universe_rows=[{
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-01-01", "end_date": "",
                "source_url": "https://tushare.pro/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight", "snapshot_count": 1,
            }],
            snapshot_rows=[{
                "ticker": "000001.SZ",
                "trade_date": "2020-01-01",
                "source_provider": "tushare:index_weight",
            }],
        )
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias,
                        "Bogus ticker in snapshot must not clear bias")
        self.assertFalse(source.data_quality.is_clean)

    def test_snapshot_count_zero_blocks(self):
        """M2: snapshot_count=0 even with matching evidence → blocked."""
        self._make_source(
            universe_rows=[{
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-01-01", "end_date": "",
                "source_url": "https://tushare.pro/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight", "snapshot_count": 0,
            }],
            snapshot_rows=[{
                "ticker": "600519.SS", "trade_date": "2020-01-01",
                "source_provider": "tushare:index_weight",
            }],
        )
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias,
                        "snapshot_count=0 must block deployment")
        self.assertFalse(source.data_quality.is_clean)

    def test_tushare_snapshot_range_too_short_blocks_deploy(self):
        """M2: tushare evidence dates shorter than backtest window → blocked."""
        self._make_source(
            universe_rows=[{
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-03-15", "end_date": "",
                "source_url": "https://tushare.pro/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight", "snapshot_count": 1,
            }],
            snapshot_rows=[{
                "ticker": "600519.SS", "trade_date": "2020-03-15",
                "source_provider": "tushare:index_weight",
            }],
            price_rows={"date": ["2020-01-01", "2020-06-30"],
                         "600519.SS": [100.0, 101.0],
                         "000300.SS": [4000, 4010]},
        )
        source = CN_PIT_FileSource(self.root)
        # Structural check: matching evidence → clean
        self.assertFalse(source.data_quality.survivorship_bias,
                         "Structural: matching evidence clears bias")
        # Period-aware: snapshot at 2020-03-15, but window starts 2020-01-01
        dq = source.data_quality_for_period("2020-01-01", "2020-06-30")
        self.assertTrue(dq.survivorship_bias,
                        "Period-aware: evidence doesn't cover start_date → blocked")

    def test_qlib_no_active_member_at_end_blocks(self):
        """M2: qlib interval with member that ends before backtest end_date."""
        self._make_source(
            universe_rows=[{
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-01-01", "end_date": "2020-03-31",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "qlib:instruments", "snapshot_count": 1,
            }],
            snapshot_rows=[{
                "ticker": "600519.SS", "trade_date": "2020-01-01",
                "source_provider": "qlib:instruments",
            }],
        )
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias,
                       "H28: 1-row qlib BLOCKED by MIN_QLIB_UNIVERSE_ROWS=200")
        # But period-aware: end_date is 2020-03-31, window ends 2020-06-30
        dq = source.data_quality_for_period("2020-01-01", "2020-06-30")
        self.assertTrue(dq.survivorship_bias,
                        "qlib interval cut short → no active member at end_date")

    def test_qlib_no_active_member_at_start_blocks(self):
        """M2: qlib interval starts after backtest start_date."""
        self._make_source(
            universe_rows=[{
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-06-01", "end_date": "",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "qlib:instruments", "snapshot_count": 1,
            }],
            snapshot_rows=[{
                "ticker": "600519.SS", "trade_date": "2020-06-01",
                "source_provider": "qlib:instruments",
            }],
        )
        source = CN_PIT_FileSource(self.root)
        dq = source.data_quality_for_period("2020-01-01", "2020-06-30")
        self.assertTrue(dq.survivorship_bias,
                        "qlib interval starts after start_date → blocked")

    def test_mixed_providers_blocks(self):
        """M2: mix of accepted + unknown providers → survivorship_bias=True."""
        self._make_source(
            universe_rows=[
                {
                    "ticker": "600519.SS", "code": "600519",
                    "effective_date": "2020-01-01", "end_date": "",
                    "source_url": "https://tushare.pro/",
                    "ingested_at": "2026-05-19T00:00:00Z",
                    "source_provider": "tushare:index_weight", "snapshot_count": 1,
                },
                {
                    "ticker": "000858.SZ", "code": "000858",
                    "effective_date": "2020-01-01", "end_date": "",
                    "source_url": "https://example.com/",
                    "ingested_at": "2026-05-19T00:00:00Z",
                    "source_provider": "unknown:source", "snapshot_count": 5,
                },
            ],
            snapshot_rows=[
                {"ticker": "600519.SS", "trade_date": "2020-01-01",
                 "source_provider": "tushare:index_weight"},
                {"ticker": "000858.SZ", "trade_date": "2020-01-01",
                 "source_provider": "unknown:source"},
            ],
        )
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias,
                        "Mixed providers must block deployment")
        self.assertFalse(source.data_quality.is_clean)

    def test_unsafe_valuation_fields_block_deployment(self):
        """M3: pe_ratio/pb_ratio/dividend_yield/market_cap/fcf_yield → blocked."""
        self._make_source(
            universe_rows=[{
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-01-01", "end_date": "",
                "source_url": "https://tushare.pro/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight", "snapshot_count": 1,
            }],
            fund_rows=[{
                "ticker": "600519.SS", "report_period": "2020-12-31",
                "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "roe": 30.0, "debt_to_equity": 0.2,
                "pe_ratio": 20.0, "pb_ratio": 5.0,
                "dividend_yield": 2.0, "market_cap": 2_000_000_000,
                "fcf_yield": 5.0,
            }],
        )
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.future_function,
                        "Unsafe valuation fields → future_function=true")
        self.assertTrue(source.data_quality.ungated_fundamentals,
                        "Unsafe valuation fields → ungated_fundamentals=true")
        self.assertTrue(source.research_only,
                        "Unsafe fields → research_only=true")
        self.assertFalse(source.data_quality.is_clean)

    def test_missing_prices_csv_raises(self):
        """L1: CN_PIT_FileSource.get_price_history raises when prices.csv missing."""
        self._make_source(
            universe_rows=[{
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-01-01", "end_date": "",
                "source_url": "https://tushare.pro/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight", "snapshot_count": 1,
            }],
        )
        # Remove prices.csv
        os.remove(os.path.join(self.root, "prices.csv"))
        source = CN_PIT_FileSource(self.root)
        with self.assertRaises(FileNotFoundError) as ctx:
            source.get_price_history(["600519.SS"], "2020-01-01", "2020-06-30")
        self.assertIn("No live yfinance fallback", str(ctx.exception))



# ================================================================
# H22: Historical HS300 Universe Ingestion Tests
# ================================================================
class TestH22SnapshotToInterval(unittest.TestCase):
    """Tests for snapshot-to-interval conversion logic."""

    def _make_snapshots(self, ticker_date_pairs):
        """Helper: build synthetic snapshot list from (ticker, YYYY-MM-DD) pairs."""
        snapshots = []
        for ticker, date in ticker_date_pairs:
            code = ticker.replace(".SS", "").replace(".SZ", "")
            snapshots.append({
                "index_code": "399300.SZ",
                "con_code": f"{code}.SZ",
                "code": code,
                "ticker": ticker,
                "trade_date": date,
                "weight": 1.0,
                "source_provider": "tushare:index_weight",
            })
        return snapshots

    def test_consecutive_appearances_merge_to_one_interval(self):
        """H22: consecutive monthly snapshots → one continuous interval."""
        # 600519 appears in Jan, Feb, Mar (consecutive at monthly cadence)
        snapshots = self._make_snapshots([
            ("600519.SS", "2024-01-15"), ("600519.SS", "2024-02-15"), ("600519.SS", "2024-03-15"),
            ("000858.SZ", "2024-01-15"),  # reference ticker to create monthly grid
        ])
        from scripts.ingest_cn_pit_data import snapshots_to_intervals
        intervals = [i for i in snapshots_to_intervals(snapshots) if i["ticker"] == "600519.SS"]
        self.assertEqual(len(intervals), 1, "consecutive appearances must merge to ONE interval")
        self.assertEqual(intervals[0]["effective_date"], "2024-01-15")
        self.assertEqual(intervals[0]["end_date"], "", "still active at last snapshot → empty end_date")
        self.assertEqual(intervals[0]["snapshot_count"], 3)

    def test_disappear_reappear_creates_two_intervals(self):
        """H22: gap in snapshots → two separate intervals."""
        # 600519: Jan present, Feb ABSENT, Mar present
        snapshots = self._make_snapshots([
            ("600519.SS", "2024-01-15"), ("600519.SS", "2024-03-15"),
            ("000858.SZ", "2024-01-15"), ("000858.SZ", "2024-02-15"), ("000858.SZ", "2024-03-15"),
        ])
        from scripts.ingest_cn_pit_data import snapshots_to_intervals
        intervals = [i for i in snapshots_to_intervals(snapshots) if i["ticker"] == "600519.SS"]
        self.assertEqual(len(intervals), 2, "gap → two intervals")
        # First interval: 2024-01-15 → day before next absent snapshot
        self.assertEqual(intervals[0]["effective_date"], "2024-01-15")
        self.assertEqual(intervals[0]["end_date"], "2024-02-14")  # day before 2024-02-15
        self.assertEqual(intervals[0]["snapshot_count"], 1)
        # Second interval: 2024-03-15 → still active
        self.assertEqual(intervals[1]["effective_date"], "2024-03-15")
        self.assertEqual(intervals[1]["end_date"], "")
        self.assertEqual(intervals[1]["snapshot_count"], 1)

    def test_ended_stock_has_closed_interval(self):
        """H22: stock that disappears before last snapshot → closed end_date."""
        # 600519 only in Jan, global dates go Jan/Feb/Mar
        snapshots = self._make_snapshots([
            ("600519.SS", "2024-01-15"),
            ("000858.SZ", "2024-01-15"), ("000858.SZ", "2024-02-15"), ("000858.SZ", "2024-03-15"),
        ])
        from scripts.ingest_cn_pit_data import snapshots_to_intervals
        intervals = [i for i in snapshots_to_intervals(snapshots) if i["ticker"] == "600519.SS"]
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["effective_date"], "2024-01-15")
        # Next global date is 2024-02-15 → end_date = 2024-02-14
        self.assertEqual(intervals[0]["end_date"], "2024-02-14")
        self.assertNotEqual(intervals[0]["end_date"], "")

    def test_empty_snapshots_returns_empty(self):
        from scripts.ingest_cn_pit_data import snapshots_to_intervals
        self.assertEqual(snapshots_to_intervals([]), [])

    def test_multiple_disappear_reappear(self):
        """H22: stock that appears, disappears, reappears, disappears → 3 intervals."""
        snapshots = self._make_snapshots([
            ("600519.SS", "2024-01-15"),
            ("600519.SS", "2024-04-15"),  # gap: Feb, Mar absent
            ("600519.SS", "2024-06-15"),  # gap: May absent
            ("000858.SZ", "2024-01-15"), ("000858.SZ", "2024-02-15"),
            ("000858.SZ", "2024-03-15"), ("000858.SZ", "2024-04-15"),
            ("000858.SZ", "2024-05-15"), ("000858.SZ", "2024-06-15"),
        ])
        from scripts.ingest_cn_pit_data import snapshots_to_intervals
        intervals = sorted(
            [i for i in snapshots_to_intervals(snapshots) if i["ticker"] == "600519.SS"],
            key=lambda x: x["effective_date"],
        )
        self.assertEqual(len(intervals), 3)
        self.assertEqual(intervals[0]["effective_date"], "2024-01-15")
        self.assertEqual(intervals[0]["end_date"], "2024-02-14")
        self.assertEqual(intervals[1]["effective_date"], "2024-04-15")
        self.assertEqual(intervals[1]["end_date"], "2024-05-14")
        self.assertEqual(intervals[2]["effective_date"], "2024-06-15")
        self.assertEqual(intervals[2]["end_date"], "")


class TestH22SurvivorshipBiasGuard(unittest.TestCase):
    """H22: survivorship_bias must NEVER be falsely cleared."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_only_fallback_keeps_survivorship_bias(self):
        """H22: current-constituent universe.jsonl → survivorship_bias=true."""
        write_jsonl(os.path.join(self.root, "universe.jsonl"), [
            {
                "ticker": "600519.SS",
                "effective_date": "2020-01-01",
                "end_date": "",
                "source_url": "https://www.csindex.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z",
                "data_quality_note": "SURVIVORSHIP_BIAS: current constituents only",
            },
        ])
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
            {
                "ticker": "600519.SS", "report_period": "2020-12-31",
                "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0,
            },
        ])
        pd.DataFrame({"date": ["2020-01-01", "2020-01-02"], "600519.SS": [100.0, 101.0], "000300.SS": [4000, 4010]}).to_csv(
            os.path.join(self.root, "prices.csv"), index=False)

        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias,
                        "Current-only universe must report survivorship_bias=true")
        self.assertTrue(source.research_only)
        self.assertFalse(source.data_quality.is_clean)

    def test_period_aware_gate_blocks_window_beyond_snapshot_coverage(self):
        """H24A: single snapshot does NOT imply clean deployability for arbitrary windows.

        The structural data_quality may be clean, but the period-aware gate
        must block a backtest window that extends beyond snapshot evidence range."""
        write_jsonl(os.path.join(self.root, "universe.jsonl"), [
            {
                "ticker": "600519.SS",
                "code": "600519",
                "effective_date": "2020-01-15",
                "end_date": "",
                "source_url": "https://tushare.pro/document/2?doc_id=96",
                "ingested_at": "2026-05-19T00:00:00Z",
                "index_code": "399300.SZ",
                "weight": 4.5,
                "source_provider": "tushare:index_weight",
                "snapshot_count": 72,
            },
        ])
        write_jsonl(os.path.join(self.root, "universe_snapshots.jsonl"), [
            {
                "ticker": "600519.SS",
                "trade_date": "2020-01-15",
                "source_provider": "tushare:index_weight",
            },
        ])
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
            {
                "ticker": "600519.SS", "report_period": "2020-12-31",
                "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0,
            },
        ])
        pd.DataFrame({"date": ["2020-01-01", "2020-06-30"],
                       "600519.SS": [100.0, 101.0],
                       "000300.SS": [4000, 4010]}).to_csv(
            os.path.join(self.root, "prices.csv"), index=False)

        source = CN_PIT_FileSource(self.root)
        # Structural quality may be clean (provider matches, evidence exists)
        self.assertFalse(source.data_quality.survivorship_bias,
                         "Single snapshot with matching evidence → structural survivorship_bias=false")
        # But period-aware: snapshot at 2020-01-15 doesn't cover a window
        # that starts at 2020-01-01 (before evidence) or ends at 2020-06-30 (after)
        dq_period = source.data_quality_for_period("2020-01-01", "2020-06-30")
        self.assertTrue(dq_period.survivorship_bias,
                        "Period-aware gate must block: snapshot range too short for window")
        # Period-aware for a narrow window that IS covered should pass
        dq_narrow = source.data_quality_for_period("2020-01-15", "2020-01-15")
        self.assertFalse(dq_narrow.survivorship_bias,
                         "Narrow window within snapshot range should pass")
        # H32: live windows may extend shortly after the latest official snapshot.
        dq_carry = source.data_quality_for_period("2020-01-15", "2020-02-20")
        self.assertFalse(dq_carry.survivorship_bias,
                         "Short latest-snapshot carry-forward window should pass")
        dq_too_far = source.data_quality_for_period("2020-01-15", "2020-03-15")
        self.assertTrue(dq_too_far.survivorship_bias,
                        "Carry-forward beyond grace window should remain blocked")

    def test_historical_provider_without_snapshot_file_stays_blocked(self):
        """H22: interval rows alone are not enough evidence to clear bias."""
        write_jsonl(os.path.join(self.root, "universe.jsonl"), [
            {
                "ticker": "600519.SS",
                "code": "600519",
                "effective_date": "2020-01-15",
                "end_date": "",
                "source_url": "https://tushare.pro/document/2?doc_id=96",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "tushare:index_weight",
                "snapshot_count": 72,
            },
        ])
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
            {
                "ticker": "600519.SS", "report_period": "2020-12-31",
                "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0,
            },
        ])
        pd.DataFrame({"date": ["2020-01-01", "2020-01-02"], "600519.SS": [100.0, 101.0], "000300.SS": [4000, 4010]}).to_csv(
            os.path.join(self.root, "prices.csv"), index=False)

        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias)
        self.assertTrue(source.research_only)
        self.assertFalse(source.data_quality.is_clean)


class TestH22DeployGate(unittest.TestCase):
    """H22: backtest deploy gate with historical universe coverage."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_pit_data(self, universe_rows, fundamental_rows):
        write_jsonl(os.path.join(self.root, "universe.jsonl"), universe_rows)
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), fundamental_rows)
        dates = pd.bdate_range("2024-01-01", periods=200)
        prices = pd.DataFrame({
            "date": dates,
            "600519.SS": [100 + i * 0.03 for i in range(len(dates))],
            "000858.SZ": [80 + i * 0.02 for i in range(len(dates))],
            "000300.SS": [4000 + i for i in range(len(dates))],
        })
        prices.to_csv(os.path.join(self.root, "prices.csv"), index=False)
        # Generate snapshot evidence that spans the full data range
        # so period-aware gate won't falsely block deploy tests
        last_date = dates[-1].strftime("%Y-%m-%d")
        far_date = "2025-12-31"  # ensure period coverage for backtest windows
        snapshot_rows = []
        for row in universe_rows:
            if row.get("source_provider") == "tushare:index_weight":
                snapshot_rows.append({
                    "ticker": row["ticker"],
                    "trade_date": row["effective_date"],
                    "source_provider": "tushare:index_weight",
                })
                snapshot_rows.append({
                    "ticker": row["ticker"],
                    "trade_date": last_date,
                    "source_provider": "tushare:index_weight",
                })
                snapshot_rows.append({
                    "ticker": row["ticker"],
                    "trade_date": far_date,
                    "source_provider": "tushare:index_weight",
                })
            elif row.get("source_provider") == "qlib:instruments":
                snapshot_rows.append({
                    "ticker": row["ticker"],
                    "trade_date": row["effective_date"],
                    "source_provider": "qlib:instruments",
                })
        write_jsonl(os.path.join(self.root, "universe_snapshots.jsonl"), snapshot_rows)

    def test_historical_intervals_allow_deployment_when_enough_data(self):
        """H22: clean historical universe → deploy gate passes."""
        self._write_pit_data(
            universe_rows=[
                {
                    "ticker": "600519.SS", "code": "600519",
                    "effective_date": "2024-01-01", "end_date": "",
                    "source_url": "https://tushare.pro/", "ingested_at": "2026-05-19T00:00:00Z",
                    "source_provider": "tushare:index_weight", "snapshot_count": 20,
                },
                {
                    "ticker": "000858.SZ", "code": "000858",
                    "effective_date": "2024-01-01", "end_date": "",
                    "source_url": "https://tushare.pro/", "ingested_at": "2026-05-19T00:00:00Z",
                    "source_provider": "tushare:index_weight", "snapshot_count": 20,
                },
            ],
            fundamental_rows=[
                {
                    "ticker": "600519.SS", "report_period": "2023-12-31",
                    "filing_date": "2024-04-20", "source_url": "https://www.cninfo.com.cn/",
                    "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0, "debt_to_equity": 0.2,
                },
                {
                    "ticker": "000858.SZ", "report_period": "2023-12-31",
                    "filing_date": "2024-04-25", "source_url": "https://www.cninfo.com.cn/",
                    "ingested_at": "2026-05-19T00:00:00Z", "roe": 25.0, "debt_to_equity": 0.3,
                },
            ],
        )
        source = CN_PIT_FileSource(self.root)
        self.assertFalse(source.research_only)
        self.assertFalse(source.data_quality.survivorship_bias)

        result = run_fundamental_backtest(
            source, start_date="2024-06-01", end_date="2024-12-31",
            capital=500000, top_n=2, max_position_pct=0.30,
        )
        # Data quality should be clear (though trades may be insufficient)
        self.assertFalse(any(b.startswith("data_quality:") for b in result.deploy_blockers))

    def test_current_only_fallback_blocks_deployment(self):
        """H22: current-only with SURVIVORSHIP_BIAS → deploy gate blocked."""
        self._write_pit_data(
            universe_rows=[
                {
                    "ticker": "600519.SS", "code": "600519",
                    "effective_date": "2024-01-01", "end_date": "",
                    "source_url": "https://www.csindex.com.cn/", "ingested_at": "2026-05-19T00:00:00Z",
                    "data_quality_note": "SURVIVORSHIP_BIAS: current constituents only",
                },
            ],
            fundamental_rows=[
                {
                    "ticker": "600519.SS", "report_period": "2023-12-31",
                    "filing_date": "2024-04-20", "source_url": "https://www.cninfo.com.cn/",
                    "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0, "debt_to_equity": 0.2,
                },
            ],
        )
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.research_only)
        self.assertTrue(source.data_quality.survivorship_bias)

        result = run_fundamental_backtest(
            source, start_date="2024-06-01", end_date="2024-12-31",
            capital=500000, top_n=2, max_position_pct=0.30,
        )
        self.assertIn("data_quality: survivorship_bias=true", result.deploy_blockers)
        self.assertIn("research_only: no deployment permitted", result.deploy_blockers)
        self.assertFalse(result.can_deploy)


# ================================================================
# H23: Qlib CSI300 Instruments Fallback Tests
# ================================================================
class TestH23QlibSymbolNormalization(unittest.TestCase):
    """H23: Qlib symbol normalization tests."""

    def test_normalize_sh_prefix(self):
        from scripts.ingest_cn_pit_data import _normalize_qlib_symbol
        self.assertEqual(_normalize_qlib_symbol("SH600519"), "600519.SS")
        self.assertEqual(_normalize_qlib_symbol("SH600036"), "600036.SS")

    def test_normalize_sz_prefix(self):
        from scripts.ingest_cn_pit_data import _normalize_qlib_symbol
        self.assertEqual(_normalize_qlib_symbol("SZ000001"), "000001.SZ")
        self.assertEqual(_normalize_qlib_symbol("SZ000858"), "000858.SZ")

    def test_normalize_dot_sh_suffix(self):
        from scripts.ingest_cn_pit_data import _normalize_qlib_symbol
        self.assertEqual(_normalize_qlib_symbol("600519.SH"), "600519.SS")

    def test_normalize_dot_sz_passthrough(self):
        from scripts.ingest_cn_pit_data import _normalize_qlib_symbol
        self.assertEqual(_normalize_qlib_symbol("000001.SZ"), "000001.SZ")

    def test_normalize_yfinance_format_passthrough(self):
        from scripts.ingest_cn_pit_data import _normalize_qlib_symbol
        self.assertEqual(_normalize_qlib_symbol("600519.SS"), "600519.SS")
        self.assertEqual(_normalize_qlib_symbol("000858.SZ"), "000858.SZ")

    def test_normalize_empty(self):
        from scripts.ingest_cn_pit_data import _normalize_qlib_symbol
        self.assertEqual(_normalize_qlib_symbol(""), "")

    def test_qlib_market_detection(self):
        from scripts.ingest_cn_pit_data import _qlib_symbol_market
        self.assertEqual(_qlib_symbol_market("SH600519"), "SSE")
        self.assertEqual(_qlib_symbol_market("SZ000001"), "SZSE")
        self.assertEqual(_qlib_symbol_market("600519.SS"), "SSE")
        self.assertEqual(_qlib_symbol_market("000001.SZ"), "SZSE")


class TestH23QlibInstrumentParsing(unittest.TestCase):
    """H23: Parse Qlib instruments file format."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_qlib_file(self, lines):
        path = os.path.join(self.tmp.name, "csi300.txt")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return Path(path)

    def test_parse_tab_format(self):
        from scripts.ingest_cn_pit_data import _parse_qlib_instruments
        path = self._write_qlib_file([
            "SH600519\t2020-01-01\t2099-12-31",
        ])
        rows = _parse_qlib_instruments(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "600519.SS")
        self.assertEqual(rows[0]["code"], "600519")
        self.assertEqual(rows[0]["effective_date"], "2020-01-01")
        self.assertEqual(rows[0]["end_date"], "")  # 2099-12-31 → empty
        self.assertEqual(rows[0]["source_provider"], "qlib:instruments")
        self.assertEqual(rows[0]["snapshot_count"], 1)
        self.assertEqual(rows[0]["qlib_symbol"], "SH600519")
        self.assertEqual(rows[0]["qlib_market"], "SSE")

    def test_parse_whitespace_format(self):
        from scripts.ingest_cn_pit_data import _parse_qlib_instruments
        path = self._write_qlib_file([
            "SH600519  2020-01-01  2099-12-31",
            "SZ000001   2020-06-01  2023-12-31",
        ])
        rows = _parse_qlib_instruments(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ticker"], "600519.SS")
        self.assertEqual(rows[1]["ticker"], "000001.SZ")
        self.assertEqual(rows[1]["end_date"], "2023-12-31")  # real closed interval

    def test_skip_header_row(self):
        from scripts.ingest_cn_pit_data import _parse_qlib_instruments
        path = self._write_qlib_file([
            "symbol start_date end_date",
            "SH600519 2020-01-01 2099-12-31",
        ])
        rows = _parse_qlib_instruments(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "600519.SS")

    def test_convert_2099_to_empty_end_date(self):
        from scripts.ingest_cn_pit_data import _parse_qlib_instruments
        path = self._write_qlib_file([
            "SH600519 2020-01-01 2099-12-31",
            "SZ000001 2019-06-01 2999-12-31",
            "SZ000002 2018-01-01 9999-12-31",
        ])
        rows = _parse_qlib_instruments(path)
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertEqual(r["end_date"], "")

    def test_real_closed_interval_preserved(self):
        from scripts.ingest_cn_pit_data import _parse_qlib_instruments
        path = self._write_qlib_file([
            "SZ000001 2019-06-01 2023-12-31",
        ])
        rows = _parse_qlib_instruments(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["end_date"], "2023-12-31")

    def test_empty_file_returns_empty(self):
        from scripts.ingest_cn_pit_data import _parse_qlib_instruments
        path = self._write_qlib_file([])
        rows = _parse_qlib_instruments(path)
        self.assertEqual(rows, [])

    def test_missing_file_returns_empty(self):
        from scripts.ingest_cn_pit_data import _parse_qlib_instruments
        path = Path(self.tmp.name) / "nonexistent.txt"
        rows = _parse_qlib_instruments(path)
        self.assertEqual(rows, [])

    def test_evidence_rows_generation(self):
        from scripts.ingest_cn_pit_data import _parse_qlib_instruments, _qlib_rows_to_evidence
        path = self._write_qlib_file([
            "SH600519 2020-01-01 2099-12-31",
            "SZ000001 2020-06-01 2023-12-31",
        ])
        instrument_rows = _parse_qlib_instruments(path)
        evidence = _qlib_rows_to_evidence(instrument_rows)
        self.assertEqual(len(evidence), 2)
        self.assertEqual(evidence[0]["trade_date"], "2020-01-01")
        self.assertEqual(evidence[0]["source_provider"], "qlib:instruments")
        self.assertEqual(evidence[0]["qlib_symbol"], "SH600519")
        self.assertEqual(evidence[1]["trade_date"], "2020-06-01")


class TestH23MissingQlibNoOverwrite(unittest.TestCase):
    """H23: missing Qlib file must not overwrite existing universe."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data" / "cn_pit"
        self.data_dir.mkdir(parents=True)
        # Pre-populate universe.jsonl
        self.original_rows = [
            {"ticker": "600519.SS", "code": "600519",
             "effective_date": "2020-01-01", "end_date": "",
             "source_url": "https://www.csindex.com.cn/",
             "ingested_at": "2026-05-19T00:00:00Z",
             "data_quality_note": "SURVIVORSHIP_BIAS: current constituents only",
             "source_provider": ""},
        ]
        write_jsonl(str(self.data_dir / "universe.jsonl"), self.original_rows)

    def tearDown(self):
        # Restore global paths that import_qlib_universe uses
        import scripts.ingest_cn_pit_data as ingest
        ingest.DATA_DIR = ingest.VT_DIR / "data" / "cn_pit"
        ingest.UNIVERSE_PATH = ingest.DATA_DIR / "universe.jsonl"
        ingest.UNIVERSE_SNAPSHOTS_PATH = ingest.DATA_DIR / "universe_snapshots.jsonl"
        self.tmp.cleanup()

    def test_missing_qlib_file_preserves_universe(self):
        """H23: import_qlib_universe with nonexistent file → returns None, no overwrite."""
        import scripts.ingest_cn_pit_data as ingest
        # Point DATA_DIR at our temp
        ingest.DATA_DIR = self.data_dir
        ingest.UNIVERSE_PATH = self.data_dir / "universe.jsonl"
        ingest.UNIVERSE_SNAPSHOTS_PATH = self.data_dir / "universe_snapshots.jsonl"

        qlib_dir = str(Path(self.tmp.name) / "nonexistent_qlib")
        result = ingest.import_qlib_universe(qlib_dir=qlib_dir, market="csi300")
        self.assertIsNone(result)

        # universe.jsonl must be unchanged
        self.assertTrue((self.data_dir / "universe.jsonl").exists())
        with open(self.data_dir / "universe.jsonl") as f:
            lines = [line for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertIn("SURVIVORSHIP_BIAS", row["data_quality_note"])

        # universe_snapshots.jsonl must NOT have been created
        self.assertFalse((self.data_dir / "universe_snapshots.jsonl").exists())


class TestH23CNPITFileSourceQlibEvidence(unittest.TestCase):
    """H23: CN_PIT_FileSource accepts qlib:instruments evidence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_pit_with_provider(
        self,
        provider,
        snapshot_count=1,
        snapshot_provider=None,
        snapshot_ticker="600519.SS",
        snapshot_date="2020-01-01",
    ):
        write_jsonl(os.path.join(self.root, "universe.jsonl"), [
            {
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-01-01", "end_date": "",
                "source_url": "https://example.com/", "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": provider, "snapshot_count": snapshot_count,
            },
        ])
        write_jsonl(os.path.join(self.root, "universe_snapshots.jsonl"), [
            {
                "ticker": snapshot_ticker, "trade_date": snapshot_date,
                "source_provider": snapshot_provider or provider,
            },
        ])
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
            {
                "ticker": "600519.SS", "report_period": "2020-12-31",
                "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0,
            },
        ])
        pd.DataFrame({"date": ["2020-01-01", "2020-01-02"],
                       "600519.SS": [100.0, 101.0],
                       "000300.SS": [4000, 4010]}).to_csv(
            os.path.join(self.root, "prices.csv"), index=False)

    def test_qlib_instruments_evidence_accepted(self):
        """H28 (H2): 1-row qlib now BLOCKED — MIN_QLIB_UNIVERSE_ROWS=200."""
        self._write_pit_with_provider("qlib:instruments", snapshot_count=1)
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias,
                       "H28: 1-row qlib should be blocked (MIN_QLIB_UNIVERSE_ROWS=200)")
        self.assertTrue(source.research_only)  # H28: is_clean=False due to MIN_QLIB_UNIVERSE_ROWS
        self.assertFalse(source.data_quality.is_clean)

    def test_static_multi_row_qlib_universe_blocked(self):
        """H29: static all-same Qlib intervals are not PIT universe evidence."""
        rows = [
            {
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2005-01-01", "end_date": "2026-05-19",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "qlib:instruments", "snapshot_count": 1,
            },
            {
                "ticker": "603296.SS", "code": "603296",
                "effective_date": "2005-01-01", "end_date": "2026-05-19",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "qlib:instruments", "snapshot_count": 1,
            },
        ]
        write_jsonl(os.path.join(self.root, "universe.jsonl"), rows)
        write_jsonl(os.path.join(self.root, "universe_snapshots.jsonl"), [
            {"ticker": row["ticker"], "trade_date": row["effective_date"], "source_provider": "qlib:instruments"}
            for row in rows
        ])
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
            {
                "ticker": row["ticker"], "report_period": "2020-12-31",
                "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0,
            }
            for row in rows
        ])
        pd.DataFrame({
            "date": ["2020-01-01", "2020-01-02"],
            "600519.SS": [100.0, 101.0],
            "603296.SS": [50.0, 51.0],
            "000300.SS": [4000, 4010],
        }).to_csv(os.path.join(self.root, "prices.csv"), index=False)

        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias)
        self.assertTrue(source.research_only)

    def test_qlib_floor_interval_with_late_first_price_blocked(self):
        """H29: Qlib floor-date intervals conflict with much later first price."""
        rows = [
            {
                "ticker": "603296.SS", "code": "603296",
                "effective_date": "2005-01-01", "end_date": "2026-05-19",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "qlib:instruments", "snapshot_count": 1,
            }
        ]
        for i in range(199):
            code = f"{600000 + i:06d}"
            rows.append({
                "ticker": f"{code}.SS", "code": code,
                "effective_date": f"2010-01-{(i % 28) + 1:02d}",
                "end_date": "2026-05-19",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "qlib:instruments", "snapshot_count": 1,
            })
        write_jsonl(os.path.join(self.root, "universe.jsonl"), rows)
        write_jsonl(os.path.join(self.root, "universe_snapshots.jsonl"), [
            {
                "ticker": row["ticker"],
                "trade_date": row["effective_date"],
                "source_provider": "qlib:instruments",
            }
            for row in rows
        ])
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
            {
                "ticker": "603296.SS", "report_period": "2020-12-31",
                "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0,
            },
        ])
        pd.DataFrame({
            "date": ["2020-01-02", "2020-02-20", "2025-10-09"],
            "603296.SS": [None, None, 51.0],
            "000300.SS": [4000, 4010, 4100],
        }).to_csv(os.path.join(self.root, "prices.csv"), index=False)

        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias)
        self.assertTrue(source.research_only)

    def test_tushare_evidence_still_accepted(self):
        """H23: tushare:index_weight evidence should still work."""
        self._write_pit_with_provider("tushare:index_weight", snapshot_count=3)
        source = CN_PIT_FileSource(self.root)
        self.assertFalse(source.data_quality.survivorship_bias)
        self.assertFalse(source.research_only)
        self.assertTrue(source.data_quality.is_clean)

    def test_unknown_provider_blocked(self):
        """H23: unknown provider → survivorship_bias remains true."""
        self._write_pit_with_provider("unknown:source", snapshot_count=5)
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias,
                        "Unknown provider must NOT clear survivorship_bias")
        self.assertTrue(source.research_only)
        self.assertFalse(source.data_quality.is_clean)

    def test_mismatched_snapshot_evidence_blocked(self):
        """H23: snapshot evidence must match provider, ticker, and effective_date."""
        cases = [
            {"snapshot_provider": "tushare:index_weight"},
            {"snapshot_ticker": "000001.SZ"},
            {"snapshot_date": "2020-02-01"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.tearDown()
                self.setUp()
                self._write_pit_with_provider("qlib:instruments", snapshot_count=1, **kwargs)
                source = CN_PIT_FileSource(self.root)
                self.assertTrue(source.data_quality.survivorship_bias)
                self.assertTrue(source.research_only)
                self.assertFalse(source.data_quality.is_clean)

    def test_qlib_without_snapshot_file_blocked(self):
        """H23: qlib:instruments rows without snapshot file → still blocked."""
        write_jsonl(os.path.join(self.root, "universe.jsonl"), [
            {
                "ticker": "600519.SS", "code": "600519",
                "effective_date": "2020-01-01", "end_date": "",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2026-05-19T00:00:00Z",
                "source_provider": "qlib:instruments", "snapshot_count": 1,
            },
        ])
        write_jsonl(os.path.join(self.root, "fundamentals.jsonl"), [
            {
                "ticker": "600519.SS", "report_period": "2020-12-31",
                "filing_date": "2021-04-20", "source_url": "https://www.cninfo.com.cn/",
                "ingested_at": "2026-05-19T00:00:00Z", "roe": 30.0,
            },
        ])
        pd.DataFrame({"date": ["2020-01-01"], "600519.SS": [100.0], "000300.SS": [4000]}).to_csv(
            os.path.join(self.root, "prices.csv"), index=False)
        source = CN_PIT_FileSource(self.root)
        self.assertTrue(source.data_quality.survivorship_bias,
                        "No snapshot evidence file → survivorship_bias=true")
        self.assertTrue(source.research_only)

    def test_qlib_deploy_gate_passes(self):
        """H28 (H2): 1-row qlib now BLOCKED by MIN_QLIB_UNIVERSE_ROWS=200."""
        self._write_pit_with_provider("qlib:instruments", snapshot_count=1)
        source = CN_PIT_FileSource(self.root)
        result = run_fundamental_backtest(
            source, start_date="2020-01-01", end_date="2020-01-02",
            capital=500000, top_n=1, max_position_pct=0.30,
        )
        self.assertTrue(any(b.startswith("data_quality:") for b in result.deploy_blockers),
                       "H28: 1-row qlib should have data_quality blockers")
        # With 1-row qlib + MIN_QLIB_UNIVERSE_ROWS=200, research_only IS expected
        self.assertIn("research_only: no deployment permitted", result.deploy_blockers)


if __name__ == "__main__":
    unittest.main()
