#!/usr/bin/env python3
"""H28: Unit tests for scripts/repair_cn_price_coverage.py.

Minimum coverage:
  - Checkpoint generation / active ticker edge cases
  - merge_candidate with reindex + out-of-range stats (M2)
  - candidate_safety_checks detects dropped columns & reduced nonnull
  - inspect_existing_candidate matches safety path (M6)
  - run_incremental_backfill uses candidate as base, handles middle gaps
  - analyze_coverage target period obeys CLI (M4)
  - fetch_missing_tickers fallback chain (yfinance → akshare)
  - manual_replacement_recommended blocked by column-but-NaN (M1+M5)

All fetch must mock — no real network.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

import pandas as pd
import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))

import repair_cn_price_coverage as repair


# --------------------------------------------------------------------------- helpers
def _tmp_data_dir():
    tmp = tempfile.mkdtemp(prefix="h28_repair_test_")
    cn_pit = Path(tmp) / "data" / "cn_pit"
    cn_pit.mkdir(parents=True, exist_ok=True)
    return cn_pit


def _write_universe(ddir: Path, rows):
    with open(ddir / "universe.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_prices_dated(ddir: Path, dates, tickers):
    """Write prices.csv with date column and ticker columns (price=10.0)."""
    cols = {"date": dates}
    for t in tickers:
        cols[t] = [10.0] * len(dates)
    pd.DataFrame(cols).to_csv(ddir / "prices.csv", index=False)


def _write_candidate_csv(path: Path, dates, tickers_to_prices):
    """Write a candidate CSV. tickers_to_prices = {ticker: [price, ...]}."""
    cols = {"date": dates}
    for t, vals in tickers_to_prices.items():
        cols[t] = vals
    pd.DataFrame(cols).to_csv(path, index=False)


def _make_prices_df(dates, tickers):
    cols = {"date": dates}
    for t in tickers:
        cols[t] = [10.0] * len(dates)
    df = pd.DataFrame(cols)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


# --------------------------------------------------------------------------- checkpoint tests
class TestGenerateCheckpoints:
    def test_generate_checkpoints_snaps_non_trading(self):
        """Jan 2 falls on a weekend → snaps to next trading day."""
        idx = pd.DatetimeIndex([
            "2021-12-31", "2022-01-03", "2022-01-04", "2022-01-05",
            "2022-12-30", "2023-01-03", "2023-01-04",
        ])
        cps = repair.generate_checkpoints(idx)
        cps_str = [d.isoformat() for d in cps]
        # Jan 2 2022 was a Sunday → should snap to Jan 3
        # Jan 2 2023 was a Monday → should snap to Jan 3 (ON if non-trading)
        assert "2022-01-03" in cps_str  # snapped from Jan 2
        assert cps[0].isoformat() == "2021-12-31"
        assert cps[-1].isoformat() == "2023-01-04"


# --------------------------------------------------------------------------- active_tickers_on edge cases
class TestActiveTickersOn:
    def test_active_tickers_on_handles_open_interval(self):
        """end_date="" is treated as active indefinitely."""
        univ = pd.DataFrame([{
            "ticker": "600519.SS",
            "effective_date": date(2005, 1, 1),
            "end_date": date(9999, 12, 31),
        }])
        active = repair.active_tickers_on(univ, date(2025, 1, 1))
        assert "600519.SS" in active

    def test_active_tickers_on_before_effective_returns_empty(self):
        univ = pd.DataFrame([{
            "ticker": "600519.SS",
            "effective_date": date(2020, 6, 1),
            "end_date": date(9999, 12, 31),
        }])
        active = repair.active_tickers_on(univ, date(2020, 1, 1))
        assert "600519.SS" not in active


# --------------------------------------------------------------------------- merge_candidate (M2)
class TestMergeCandidate:
    def test_merge_candidate_preserves_original_columns_and_counts(self):
        """After merging, all original columns remain and notna counts do not decrease."""
        dates = ["2025-01-02", "2025-01-03"]
        orig = _make_prices_df(dates, ["600519.SS", "000300.SS"])
        # new_data has 000001.SZ and fills a NaN in 600519.SS on 2025-01-03
        orig.loc["2025-01-03", "600519.SS"] = float("nan")
        new_dates = ["2025-01-02", "2025-01-03"]
        new_cols = {"date": new_dates, "000001.SZ": [11.0, 12.0], "600519.SS": [10.0, 10.5]}
        new_df = pd.DataFrame(new_cols)
        new_df["date"] = pd.to_datetime(new_df["date"])
        new_df.set_index("date", inplace=True)

        candidate, stats = repair.merge_candidate(orig, new_df, {"000001.SZ", "600519.SS"})

        # Original columns preserved
        assert "600519.SS" in candidate.columns
        assert "000300.SS" in candidate.columns
        # New column added
        assert "000001.SZ" in candidate.columns
        # Non-null count for 600519.SS should not decrease
        assert candidate["600519.SS"].notna().sum() >= orig["600519.SS"].notna().sum()
        # Stats
        assert "000001.SZ" in stats["added"]
        assert stats["in_range_nonnull_rows"]["000001.SZ"] == 2

    def test_merge_candidate_warns_on_out_of_range_dates(self):
        """New data has dates outside candidate range → out_of_range_rows recorded."""
        dates = ["2025-01-02", "2025-01-03"]
        orig = _make_prices_df(dates, ["600519.SS"])
        new_dates = ["2024-12-31", "2025-01-02", "2025-01-03", "2025-01-06"]
        new_cols = {"date": new_dates, "000001.SZ": [9.0, 10.0, 11.0, 12.0]}
        new_df = pd.DataFrame(new_cols)
        new_df["date"] = pd.to_datetime(new_df["date"])
        new_df.set_index("date", inplace=True)

        candidate, stats = repair.merge_candidate(orig, new_df, {"000001.SZ"})
        # 2 out-of-range rows (2024-12-31 and 2025-01-06)
        assert stats["out_of_range_rows"]["000001.SZ"] == 2
        assert stats["in_range_nonnull_rows"]["000001.SZ"] == 2


# --------------------------------------------------------------------------- candidate_safety_checks
class TestCandidateSafetyChecks:
    def test_candidate_safety_checks_detects_dropped_column(self):
        """Removing one column → passed=False, missing_existing_cols accurate."""
        dates = ["2025-01-02", "2025-01-03"]
        orig = _make_prices_df(dates, ["A.SS", "B.SS", "C.SZ"])
        cand = _make_prices_df(dates, ["A.SS", "B.SS"])  # missing C.SZ

        result = repair.candidate_safety_checks(orig, cand)
        assert result["passed"] is False
        assert "C.SZ" in result["missing_existing_cols"]

    def test_candidate_safety_checks_detects_reduced_nonnull(self):
        """Reducing non-null count in an existing column is detected."""
        dates = ["2025-01-02", "2025-01-03"]
        orig = _make_prices_df(dates, ["A.SS", "B.SS"])
        # Set B.SS partially NaN in candidate
        cand = orig.copy()
        cand.loc["2025-01-03", "B.SS"] = float("nan")

        result = repair.candidate_safety_checks(orig, cand)
        assert result["passed"] is False
        reduced = result["reduced_nonnull_existing_cols"]
        assert any("B.SS" in str(x) for x in reduced)


# --------------------------------------------------------------------------- inspect_existing_candidate ≅ safety (M6)
class TestInspectExistingMatchesSafety:
    def test_inspect_existing_candidate_matches_safety(self):
        """inspect_existing_candidate and candidate_safety_checks agree."""
        ddir = _tmp_data_dir()
        monkeypatch = pytest.MonkeyPatch()
        try:
            dates = ["2025-01-02", "2025-01-03"]
            orig = _make_prices_df(dates, ["A.SS", "B.SS"])

            cand_path = ddir / "candidate_test.csv"
            orig.reset_index().to_csv(cand_path, index=False)

            monkeypatch.setattr(repair, "CANDIDATE_CSV", cand_path)
            monkeypatch.setattr(repair, "load_prices",
                                lambda p: pd.read_csv(p, parse_dates=["date"], index_col="date"))

            info = repair.inspect_existing_candidate(orig)
            assert info is not None
            safety = repair.candidate_safety_checks(orig, repair.load_prices(cand_path))

            assert info["safety_passed"] == safety["passed"]
            assert info["missing_existing_cols"] == safety["missing_existing_cols"]
            assert info["reduced_nonnull_existing_cols"] == safety["reduced_nonnull_existing_cols"]
        finally:
            monkeypatch.undo()


# --------------------------------------------------------------------------- run_incremental_backfill
class TestRunIncrementalBackfill:
    def test_run_incremental_backfill_uses_candidate_as_base(self, monkeypatch):
        """H27 backfill starts from H26 candidate when available."""
        ddir = _tmp_data_dir()
        h26_path = ddir / "prices_h26_candidate.csv"
        _write_candidate_csv(h26_path,
            ["2025-01-02", "2025-01-03"],
            {"A.SS": [10.0, 10.0], "B.SS": [11.0, 11.0], "000300.SS": [4000.0, 4010.0]},
        )

        orig = _make_prices_df(["2025-01-02", "2025-01-03"], ["A.SS", "000300.SS"])
        univ = pd.DataFrame([{
            "ticker": "A.SS", "effective_date": date(2005, 1, 1), "end_date": date(9999, 12, 31),
        }, {
            "ticker": "B.SS", "effective_date": date(2005, 1, 1), "end_date": date(9999, 12, 31),
        }])

        monkeypatch.setattr(repair, "PRICES_FILE", ddir / "prices.csv")
        monkeypatch.setattr(repair, "CANDIDATE_CSV", h26_path)
        monkeypatch.setattr(repair, "fetch_missing_tickers", lambda *a, **kw: None)

        out_cov = ddir / "coverage_test.json"
        out_rpt = ddir / "report_test.md"
        out_csv = ddir / "prices_test_candidate.csv"

        info = repair.run_incremental_backfill(
            original=orig, universe=univ,
            start_date="2025-01-01", end_date="2026-05-18",
            batch_size=10, prefix="h27",
            base_path=h26_path,
            candidate_path=out_csv,
            coverage_json=out_cov,
            report_md=out_rpt,
        )
        # Should start from H26 candidate which has B.SS column
        assert info["base_path"] == str(h26_path)
        assert "B.SS" in info.get("new_tickers", []) or info["remaining_missing_columns"] == 0

    def test_run_incremental_backfill_never_recommends_when_middle_gap(self, monkeypatch):
        """First/last checkpoints have full coverage but middle has gap → no manual replacement."""
        ddir = _tmp_data_dir()
        _write_universe(ddir, [
            {"ticker": "A.SS", "effective_date": "2020-01-01", "end_date": "",
             "source_url": "x", "ingested_at": "2025-01-01T00:00:00Z"},
            {"ticker": "B.SS", "effective_date": "2022-01-01", "end_date": "2024-12-31",
             "source_url": "x", "ingested_at": "2025-01-01T00:00:00Z"},
        ])
        monkeypatch.setattr(repair, "UNIVERSE_FILE", ddir / "universe.jsonl")
        monkeypatch.setattr(repair, "PRICES_FILE", ddir / "prices.csv")

        # A.SS has full span, B.SS expired before 2025 — so at checkpoints B.SS not active
        _write_prices_dated(ddir,
            ["2020-01-02", "2022-07-01", "2025-01-02", "2026-05-18"],
            ["A.SS", "000300.SS"],
        )
        monkeypatch.setattr(repair, "CANDIDATE_CSV", ddir / "prices_cand.csv")
        monkeypatch.setattr(repair, "fetch_missing_tickers", lambda *a, **kw: None)

        orig = repair.load_prices(ddir / "prices.csv")
        univ = repair.load_universe(ddir / "universe.jsonl")
        info = repair.run_incremental_backfill(
            original=orig, universe=univ,
            start_date="2025-01-01", end_date="2026-05-18",
            batch_size=10, prefix="h27",
            base_path=ddir / "prices.csv",
            candidate_path=ddir / "out.csv",
            coverage_json=ddir / "cov.json",
            report_md=ddir / "rpt.md",
        )
        assert info["manual_replacement_recommended"] is False


# --------------------------------------------------------------------------- manual_replacement blocked by column-but-NaN (M1+M5)
class TestManualReplacementColumnNaN:
    def test_manual_replacement_blocked_by_column_nan(self, monkeypatch):
        """All columns present but one date has NaN → manual_replacement_recommended=False."""
        ddir = _tmp_data_dir()
        _write_universe(ddir, [
            {"ticker": "A.SS", "effective_date": "2020-01-01", "end_date": "",
             "source_url": "x", "ingested_at": "2025-01-01T00:00:00Z"},
        ])
        monkeypatch.setattr(repair, "UNIVERSE_FILE", ddir / "universe.jsonl")
        monkeypatch.setattr(repair, "PRICES_FILE", ddir / "prices.csv")
        monkeypatch.setattr(repair, "CANDIDATE_CSV", ddir / "prices_cand.csv")
        monkeypatch.setattr(repair, "fetch_missing_tickers", lambda *a, **kw: None)

        # Prices with A.SS column but NaN on 2025-01-02
        df = pd.DataFrame({
            "date": ["2020-01-02", "2025-01-02", "2026-05-18"],
            "A.SS": [10.0, float("nan"), 10.0],
            "000300.SS": [4000.0, 4100.0, 4200.0],
        })
        df.to_csv(ddir / "prices.csv", index=False)

        orig = repair.load_prices(ddir / "prices.csv")
        univ = repair.load_universe(ddir / "universe.jsonl")
        info = repair.run_incremental_backfill(
            original=orig, universe=univ,
            start_date="2020-01-01", end_date="2026-05-18",
            batch_size=10, prefix="h27",
            base_path=ddir / "prices.csv",
            candidate_path=ddir / "out.csv",
            coverage_json=ddir / "cov.json",
            report_md=ddir / "rpt.md",
        )
        assert info["manual_replacement_recommended"] is False

    def test_backfill_queues_column_nan_tickers(self, monkeypatch):
        """missing_before=[] but union_missing_data has tickers → fetch called."""
        ddir = _tmp_data_dir()
        _write_universe(ddir, [
            {"ticker": "A.SS", "effective_date": "2020-01-01", "end_date": "",
             "source_url": "x", "ingested_at": "2025-01-01T00:00:00Z"},
            {"ticker": "X.SZ", "effective_date": "2020-01-01", "end_date": "",
             "source_url": "x", "ingested_at": "2025-01-01T00:00:00Z"},
        ])
        monkeypatch.setattr(repair, "UNIVERSE_FILE", ddir / "universe.jsonl")
        monkeypatch.setattr(repair, "PRICES_FILE", ddir / "prices.csv")
        monkeypatch.setattr(repair, "CANDIDATE_CSV", ddir / "prices_cand.csv")

        fetch_calls = []
        def mock_fetch(tickers, start_date, end_date, allow_akshare=True):
            fetch_calls.append(list(tickers))
            return None
        monkeypatch.setattr(repair, "fetch_missing_tickers", mock_fetch)

        # All columns present, but X.SZ has NaN at one date
        df = pd.DataFrame({
            "date": ["2020-01-02", "2025-01-02"],
            "A.SS": [10.0, 10.0],
            "X.SZ": [10.0, float("nan")],
            "000300.SS": [4000.0, 4100.0],
        })
        df.to_csv(ddir / "prices.csv", index=False)

        orig = repair.load_prices(ddir / "prices.csv")
        univ = repair.load_universe(ddir / "universe.jsonl")
        repair.run_incremental_backfill(
            original=orig, universe=univ,
            start_date="2020-01-01", end_date="2026-05-18",
            batch_size=10, prefix="h27",
            base_path=ddir / "prices.csv",
            candidate_path=ddir / "out.csv",
            coverage_json=ddir / "cov.json",
            report_md=ddir / "rpt.md",
        )
        # X.SZ should be in the fetch queue
        all_fetched = [t for call in fetch_calls for t in call]
        assert "X.SZ" in all_fetched, f"Expected X.SZ in fetch queue, got {all_fetched}"


# --------------------------------------------------------------------------- analyze_coverage target period (M4)
class TestAnalyzeCoverageTargetPeriod:
    def test_analyze_coverage_target_period_obeys_cli(self):
        """Passing target_start/target_end changes the target_period in report."""
        dates = ["2024-01-02", "2024-06-03", "2025-01-02"]
        tickers = ["A.SS", "000300.SS"]
        prices = _make_prices_df(dates, tickers)
        univ = pd.DataFrame([{
            "ticker": "A.SS",
            "effective_date": date(2005, 1, 1),
            "end_date": date(9999, 12, 31),
        }])

        cps = repair.generate_checkpoints(prices.index)
        report = repair.analyze_coverage(
            univ, prices, cps,
            target_start=date(2024, 1, 1),
            target_end=date(2024, 12, 31),
        )
        assert report["target_period"]["requested"]["start"] == "2024-01-01"
        assert report["target_period"]["requested"]["end"] == "2024-12-31"

    def test_analyze_coverage_default_period(self):
        """Default target period is 2025-01-01 → 2026-05-18."""
        dates = ["2025-01-02", "2026-05-18"]
        tickers = ["A.SS", "000300.SS"]
        prices = _make_prices_df(dates, tickers)
        univ = pd.DataFrame([{
            "ticker": "A.SS",
            "effective_date": date(2005, 1, 1),
            "end_date": date(9999, 12, 31),
        }])
        cps = repair.generate_checkpoints(prices.index)
        report = repair.analyze_coverage(univ, prices, cps)
        assert report["target_period"]["requested"]["start"] == "2025-01-01"
        assert report["target_period"]["requested"]["end"] == "2026-05-18"


# --------------------------------------------------------------------------- fetch_missing_tickers fallback (mock)
class TestFetchMissingTickersFallback:
    def test_fetch_missing_tickers_yfinance_failure_falls_through_to_akshare(self, monkeypatch):
        """When yfinance fails, akshare path is attempted."""
        yf_called = []
        ak_called = []

        def mock_yf_download(tickers, start, end, **kw):
            yf_called.append(True)
            raise RuntimeError("yfinance network error")

        # Mock yfinance at the module level used inside fetch_missing_tickers
        import yfinance
        monkeypatch.setattr(yfinance, "download", mock_yf_download)

        # Mock akshare
        mock_ak_df = pd.DataFrame({
            "日期": pd.to_datetime(["2025-01-02", "2025-01-03"]),
            "收盘": [10.0, 11.0],
        })
        mock_ak_df = mock_ak_df.set_index("日期")

        mock_ak_module = MagicMock()
        def mock_ak_hist(symbol, period, start_date, end_date, adjust):
            ak_called.append(symbol)
            return mock_ak_df.reset_index()
        mock_ak_module.stock_zh_a_hist = mock_ak_hist

        with patch.dict("sys.modules", {"akshare": mock_ak_module}):
            result = repair.fetch_missing_tickers(
                ["000001.SZ"], "2025-01-01", "2025-12-31", allow_akshare=True,
            )

        assert yf_called, "yfinance should have been attempted"
        assert ak_called, "akshare should have been called after yfinance failure"


# --------------------------------------------------------------------------- output_paths validation (L)
class TestOutputPaths:
    def test_valid_prefixes(self):
        cov, rpt, csv = repair.output_paths("h26")
        assert "h26" in str(cov)
        cov, rpt, csv = repair.output_paths("h27")
        assert "h27" in str(cov)
        cov, rpt, csv = repair.output_paths("h30")
        assert "h30" in str(cov)

    def test_invalid_prefix_raises(self):
        with pytest.raises(ValueError, match="Unsupported output prefix"):
            repair.output_paths("h28")

    def test_case_insensitive(self):
        cov, rpt, csv = repair.output_paths("H26")
        assert "h26" in str(cov)
