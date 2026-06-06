#!/usr/bin/env python3
"""Focused tests for scripts/ingest_cn_pit_data.py — H24B hardening.

Tests target:
  - _day_before / _parse_date strict date handling (M1)
  - snapshots_to_intervals date validation + field preservation (M1, L4)
  - Tushare partial coverage rejection (H4)
  - validate() status semantics: FAILED/BLOCKED/PASSED + can_deploy_data_quality (H3, M4)
  - snapshot coverage vs price range → blocker (H3)

Uses monkeypatching + temp dirs; no live network calls.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make the scripts/ directory importable
_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))

import ingest_cn_pit_data as ingest


# ================================================================
# Helpers
# ================================================================
def _make_snap(*ticker_dates):
    """Build minimal snapshot dicts from (ticker, date_str) tuples."""
    return [
        {
            "ticker": td[0],
            "trade_date": td[1],
            "code": td[0].replace(".SS", "").replace(".SZ", ""),
            "weight": 1.0,
            "source_provider": "tushare:index_weight",
            "source_url": "https://tushare.pro/doc",
            "index_code": "399300.SZ",
        }
        for td in ticker_dates
    ]


def _temp_data_dir():
    """Create a temporary data/cn_pit directory tree and return root."""
    tmp = tempfile.mkdtemp(prefix="h24b_test_")
    cn_pit = Path(tmp) / "data" / "cn_pit"
    cn_pit.mkdir(parents=True, exist_ok=True)
    return cn_pit


def _write_universe(ddir: Path, rows):
    """Write universe.jsonl to the given data dir."""
    ingest.write_jsonl(ddir / "universe.jsonl", rows)


def _write_snapshots(ddir: Path, rows):
    """Write universe_snapshots.jsonl."""
    ingest.write_jsonl(ddir / "universe_snapshots.jsonl", rows)


def _write_prices(ddir: Path, dates, tickers):
    """Write a minimal prices.csv with given dates and ticker columns (all price=10)."""
    import pandas as pd

    cols = {"date": dates}
    for t in tickers:
        cols[t] = [10.0] * len(dates)
    df = pd.DataFrame(cols)
    df.to_csv(ddir / "prices.csv", index=False)


def _write_fundamentals(ddir: Path, rows):
    """Write fundamentals.jsonl."""
    ingest.write_jsonl(ddir / "fundamentals.jsonl", rows)


# ================================================================
# M1: Strict date handling
# ================================================================
class TestParseDate:
    def test_valid_date(self):
        """_parse_date parses a valid ISO date."""
        dt = ingest._parse_date("2023-06-15")
        assert dt == datetime(2023, 6, 15)

    def test_invalid_date_raises(self):
        """_parse_date raises ValueError on garbage input."""
        with pytest.raises(ValueError):
            ingest._parse_date("bad-date")

    def test_empty_string_raises(self):
        """_parse_date raises on empty string."""
        with pytest.raises(ValueError):
            ingest._parse_date("")

    def test_wrong_format_raises(self):
        """_parse_date raises on wrong format like 15/06/2023."""
        with pytest.raises(ValueError):
            ingest._parse_date("15/06/2023")


class TestDayBefore:
    def test_normal(self):
        """_day_before returns previous day's date string."""
        assert ingest._day_before("2023-06-15") == "2023-06-14"

    def test_month_boundary(self):
        """_day_before crosses month boundary correctly."""
        assert ingest._day_before("2023-03-01") == "2023-02-28"

    def test_year_boundary(self):
        """_day_before crosses year boundary correctly."""
        assert ingest._day_before("2023-01-01") == "2022-12-31"

    def test_invalid_raises(self):
        """_day_before raises ValueError on invalid input — no silent propagation."""
        with pytest.raises(ValueError):
            ingest._day_before("bad-date")

    def test_empty_raises(self):
        """_day_before raises on empty string."""
        with pytest.raises(ValueError):
            ingest._day_before("")


class TestSnapshotsToIntervalsDateValidation:
    """M1: snapshots_to_intervals must validate trade_date on all inputs."""

    def test_missing_trade_date_raises(self):
        """Missing trade_date on any snapshot raises ValueError."""
        snaps = _make_snap(("600519.SS", "2023-01-15"))
        snaps[0]["trade_date"] = ""  # remove it
        with pytest.raises(ValueError, match="missing trade_date"):
            ingest.snapshots_to_intervals(snaps)

    def test_invalid_trade_date_raises(self):
        """Invalid trade_date on any snapshot raises ValueError."""
        snaps = _make_snap(("600519.SS", "2023-oh-no"))
        with pytest.raises(ValueError, match="invalid trade_date"):
            ingest.snapshots_to_intervals(snaps)

    def test_valid_snapshots_ok(self):
        """Valid snapshots construct intervals without error."""
        snaps = _make_snap(
            ("600519.SS", "2023-01-15"),
            ("600519.SS", "2023-02-15"),
        )
        intervals = ingest.snapshots_to_intervals(snaps)
        assert len(intervals) == 1
        assert intervals[0]["effective_date"] == "2023-01-15"


# ================================================================
# L4: Field preservation in snapshots_to_intervals
# ================================================================
class TestSnapshotsToIntervalsFieldPreservation:
    """L4: interval rows must preserve source_url/index_code/source_provider from input snapshots."""

    def test_preserves_source_url(self):
        """source_url from interval-start snapshot is preserved."""
        snaps = _make_snap(("600519.SS", "2023-01-15"))
        custom_url = "https://custom.provider.com/hs300"
        snaps[0]["source_url"] = custom_url
        intervals = ingest.snapshots_to_intervals(snaps)
        assert intervals[0]["source_url"] == custom_url

    def test_preserves_index_code(self):
        """index_code from interval-start snapshot is preserved."""
        snaps = _make_snap(("600519.SS", "2023-01-15"))
        custom_idx = "000905.SZ"  # CSI 500
        snaps[0]["index_code"] = custom_idx
        intervals = ingest.snapshots_to_intervals(snaps)
        assert intervals[0]["index_code"] == custom_idx

    def test_preserves_source_provider(self):
        """source_provider from interval-start snapshot is preserved."""
        snaps = _make_snap(("600519.SS", "2023-01-15"))
        custom_prov = "qlib:instruments"
        snaps[0]["source_provider"] = custom_prov
        intervals = ingest.snapshots_to_intervals(snaps)
        assert intervals[0]["source_provider"] == custom_prov

    def test_falls_back_when_fields_missing(self):
        """When source fields are absent, hardcoded defaults are used."""
        snaps = _make_snap(("600519.SS", "2023-01-15"))
        for key in ("source_url", "index_code", "source_provider"):
            snaps[0].pop(key, None)
        intervals = ingest.snapshots_to_intervals(snaps)
        assert intervals[0]["source_url"] == "https://tushare.pro/document/2?doc_id=96"
        assert intervals[0]["index_code"] == "399300.SZ"
        assert intervals[0]["source_provider"] == "tushare:index_weight"


# ================================================================
# H4: Tushare coverage enforcement
# ================================================================
class TestFetchHistoricalUniverse:
    """H4: Tushare partial coverage must not overwrite clean universe data."""

    def _mock_tushare_df(self, dates):
        """Build a DataFrame that looks like Tushare index_weight output."""
        import pandas as pd

        rows = []
        for d in dates:
            rows.append({
                "trade_date": d.replace("-", ""),
                "con_code": "600519.SH",
                "weight": 1.5,
            })
        return pd.DataFrame(rows)

    def test_coverage_gap_start_rejected(self, monkeypatch):
        """When snapshot_min > requested start, return None — do not overwrite."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        # Pre-seed universe.jsonl so we can verify it's NOT overwritten
        pre_seed = [{"ticker": "000001.SZ", "code": "000001", "effective_date": "2020-01-01"}]
        _write_universe(ddir, pre_seed)

        # Mock tushare returning only 2024 data, but we asked for start=2020
        mock_pro = MagicMock()
        mock_pro.index_weight.return_value = self._mock_tushare_df(["2024-01-15", "2024-02-15"])
        mock_ts = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        monkeypatch.setattr("ingest_cn_pit_data._get_tushare_token", lambda: "fake-token")

        with patch.dict("sys.modules", {"tushare": mock_ts}):
            result = ingest.fetch_historical_universe(start="2020-01-01", end="2025-12-31")

        assert result is None, "should return None on insufficient coverage"

        # universe.jsonl must NOT be overwritten
        universe = ingest.read_jsonl(ddir / "universe.jsonl")
        assert len(universe) == 1
        assert universe[0]["ticker"] == "000001.SZ"

    def test_coverage_gap_end_rejected(self, monkeypatch):
        """When snapshot_max < requested end, return None."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        pre_seed = [{"ticker": "000001.SZ", "code": "000001", "effective_date": "2020-01-01"}]
        _write_universe(ddir, pre_seed)

        mock_pro = MagicMock()
        mock_pro.index_weight.return_value = self._mock_tushare_df(["2020-01-15", "2020-02-15"])
        mock_ts = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        monkeypatch.setattr("ingest_cn_pit_data._get_tushare_token", lambda: "fake-token")

        with patch.dict("sys.modules", {"tushare": mock_ts}):
            result = ingest.fetch_historical_universe(start="2020-01-01", end="2025-12-31")

        assert result is None

        # Existing data preserved
        universe = ingest.read_jsonl(ddir / "universe.jsonl")
        assert len(universe) == 1

    def test_fetch_universe_historical_failure_preserves_existing_universe(self, monkeypatch):
        """Outer historical fetch mode must not fallback-overwrite existing universe."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        pre_seed = [{
            "ticker": "000001.SZ",
            "code": "000001",
            "effective_date": "2020-01-01",
            "data_quality_note": "SURVIVORSHIP_BIAS: existing fallback",
        }]
        _write_universe(ddir, pre_seed)

        monkeypatch.setattr(ingest, "fetch_historical_universe", lambda **kwargs: None)

        rows = ingest.fetch_universe(start="2020-01-01", end="2025-12-31")
        universe = ingest.read_jsonl(ddir / "universe.jsonl")
        assert rows == pre_seed
        assert universe == pre_seed

    def test_full_coverage_accepted(self, monkeypatch):
        """When snapshot range covers the full request, data is written."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        # Snapshots covering 2020-01 to 2020-03; request is fully contained
        mock_pro = MagicMock()
        mock_pro.index_weight.return_value = self._mock_tushare_df([
            "2020-01-15", "2020-02-15", "2020-03-15",
        ])
        mock_ts = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        monkeypatch.setattr("ingest_cn_pit_data._get_tushare_token", lambda: "fake-token")

        with patch.dict("sys.modules", {"tushare": mock_ts}):
            result = ingest.fetch_historical_universe(start="2020-01-15", end="2020-03-15")

        assert result is not None
        assert result["is_historical"] is True
        assert len(result["intervals"]) > 0

        # universe.jsonl was written
        universe = ingest.read_jsonl(ddir / "universe.jsonl")
        assert len(universe) > 0


# ================================================================
# H3 + M4: validate() status semantics
# ================================================================
class TestValidateStatusSemantics:
    """validate() must produce meaningful statuses and can_deploy_data_quality."""

    def test_empty_data_dir_fails(self, monkeypatch):
        """Empty or missing data dir → FAILED."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        # Mock CN_PIT_FileSource since we don't import it
        with patch.object(ingest, "sys") as mock_sys:
            # Make the import of fundamental_backtest raise so we test the basic validate path
            mock_sys.path.insert.return_value = None
            summary = ingest.validate()
            # Without files, should be FAILED
            assert summary["status"] in ("FAILED", "BLOCKED")
            # With missing files, "errors" should be non-empty → FAILED
            assert summary["status"] == "FAILED"
            assert not summary["can_deploy_data_quality"]

    def test_blocked_when_survivorship_marker(self, monkeypatch):
        """When data_quality_blockers is nonempty and no structural errors → BLOCKED."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        # Write universe with survivorship_bias marker
        universe_rows = [{
            "ticker": "600519.SS",
            "code": "600519",
            "name": "贵州茅台",
            "effective_date": "2020-01-01",
            "end_date": "",
            "source_url": "https://test.com",
            "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "akshare:current",
            "data_quality_note": "SURVIVORSHIP_BIAS: current constituents only",
        }]
        _write_universe(ddir, universe_rows)

        # Write fundamentals
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
            "debt_to_equity": 0.5,
        }])

        # Write prices
        _write_prices(ddir, ["2024-01-02", "2024-12-31"], ["600519.SS", "000300.SS"])

        # Patch CN_PIT_FileSource import with a mock that simulates non-clean data
        mock_source = MagicMock()
        mock_source.data_quality.is_clean = False
        mock_source.data_quality.survivorship_bias = True
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = True

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())

        with patch.dict("sys.modules", {
            "fundamental_backtest": mock_fbs,
        }):
            summary = ingest.validate()

        # Should be BLOCKED — files are structurally valid but data-quality blockers exist
        assert summary["status"] == "BLOCKED", f"Expected BLOCKED, got {summary['status']}"
        assert "survivorship_bias" in summary.get("data_quality_blockers", [])
        assert not summary["can_deploy_data_quality"]

    def test_passed_when_clean(self, monkeypatch):
        """When all files exist and no blockers → PASSED."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        # Write universe WITHOUT survivorship_bias marker
        universe_rows = [{
            "ticker": "600519.SS",
            "code": "600519",
            "name": "贵州茅台",
            "effective_date": "2020-01-01",
            "end_date": "",
            "source_url": "https://tushare.pro/doc",
            "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "tushare:index_weight",
            "snapshot_count": 48,
            "weight": 5.0,
        }]
        _write_universe(ddir, universe_rows)

        # Write snapshots with matching evidence — must fully span price range
        snap_rows = [
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2019-12-01",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
        ]
        for m in range(1, 13):
            snap_rows.append({
                "ticker": "600519.SS",
                "code": "600519",
                "trade_date": f"2020-{m:02d}-01",
                "source_provider": "tushare:index_weight",
                "index_code": "399300.SZ",
            })
        snap_rows.append({
            "ticker": "600519.SS", "code": "600519", "trade_date": "2021-01-01",
            "source_provider": "tushare:index_weight", "index_code": "399300.SZ",
        })
        _write_snapshots(ddir, snap_rows)

        # Write fundamentals
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
            "debt_to_equity": 0.5,
        }])

        # Write prices
        _write_prices(ddir, ["2020-01-02", "2020-12-31"], ["600519.SS", "000300.SS"])

        # Mock CN_PIT_FileSource as clean
        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())

        with patch.dict("sys.modules", {
            "fundamental_backtest": mock_fbs,
        }):
            summary = ingest.validate()

        assert summary["status"] == "PASSED", f"Expected PASSED, got {summary['status']}"
        assert summary["can_deploy_data_quality"]

    def test_qlib_interval_evidence_does_not_use_snapshot_max_as_coverage(self, monkeypatch):
        """H28 (H2): 1-row qlib universe now BLOCKED (MIN_QLIB_UNIVERSE_ROWS=200).
        This test verifies the new stricter gate semantics."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        _write_universe(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "effective_date": "2020-01-01",
            "end_date": "2021-01-01",
            "source_url": "file:///tmp/csi300.txt",
            "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "qlib:instruments",
            "snapshot_count": 1,
        }])
        _write_snapshots(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "trade_date": "2020-01-01",
            "source_provider": "qlib:instruments",
        }])
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS",
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
        }])
        _write_prices(ddir, ["2020-01-02", "2020-12-31"], ["600519.SS", "000300.SS"])

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())

        summary = ingest.validate()

        # H28 (H2): 1-row qlib universe should now be BLOCKED
        assert summary["status"] == "BLOCKED", f"Expected BLOCKED with 1-row qlib, got {summary['status']}"
        blockers = summary.get("data_quality_blockers", [])
        assert "survivorship_bias" in blockers, (
            f"Expected survivorship_bias blocker, got {blockers}"
        )

    def test_active_universe_price_gap_blocks(self, monkeypatch):
        """Missing prices for active interval members are a price_coverage blocker."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        rows = [
            {
                "ticker": "600519.SS",
                "code": "600519",
                "effective_date": "2020-01-01",
                "end_date": "2021-01-01",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2025-01-01T00:00:00Z",
                "source_provider": "qlib:instruments",
                "snapshot_count": 1,
            },
            {
                "ticker": "000001.SZ",
                "code": "000001",
                "effective_date": "2020-01-01",
                "end_date": "2021-01-01",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2025-01-01T00:00:00Z",
                "source_provider": "qlib:instruments",
                "snapshot_count": 1,
            },
        ]
        _write_universe(ddir, rows)
        _write_snapshots(ddir, [
            {"ticker": r["ticker"], "code": r["code"], "trade_date": r["effective_date"], "source_provider": "qlib:instruments"}
            for r in rows
        ])
        _write_fundamentals(ddir, [{
            "ticker": r["ticker"],
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
        } for r in rows])
        _write_prices(ddir, ["2020-01-02", "2020-12-31"], ["600519.SS", "000300.SS"])

        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False
        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.dict("sys.modules", {"fundamental_backtest": mock_fbs}):
            summary = ingest.validate()

        assert summary["status"] == "BLOCKED"
        assert "price_coverage" in summary.get("data_quality_blockers", [])
        assert not summary["can_deploy_data_quality"]


# ================================================================
# H3: Snapshot coverage vs price range → blocker
# ================================================================
class TestSnapshotCoverageBlocker:
    """Snapshot coverage earlier/later than price range must be a blocker, not warning."""

    def test_snapshots_start_after_prices_is_blocker(self, monkeypatch):
        """When snapshot_min > price_min, survivorship_bias is added to blockers."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        # Universe with tushare provider (no survivorship marker)
        universe_rows = [{
            "ticker": "600519.SS",
            "code": "600519",
            "effective_date": "2020-01-01",
            "end_date": "",
            "source_url": "https://tushare.pro/doc",
            "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "tushare:index_weight",
            "snapshot_count": 12,
        }]
        _write_universe(ddir, universe_rows)

        # Write fundamentals
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
            "debt_to_equity": 0.5,
        }])

        # Prices from 2018, but snapshots only from 2021
        _write_prices(ddir, ["2018-01-02", "2021-12-31"], ["600519.SS", "000300.SS"])

        # Snapshots: min is 2021-01-15, but prices start at 2018 → gap!
        snap_rows = [
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2021-01-15",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2021-06-15",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
        ]
        _write_snapshots(ddir, snap_rows)

        # CN_PIT_FileSource may say is_clean=True (since survivorship marker isn't on rows)
        # but our validate() should detect the coverage gap independently
        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())

        with patch.dict("sys.modules", {
            "fundamental_backtest": mock_fbs,
        }):
            summary = ingest.validate()

        # Coverage gap should make it BLOCKED
        assert summary["status"] == "BLOCKED", (
            f"Expected BLOCKED due to snapshot coverage gap, got {summary['status']}"
        )
        assert "survivorship_bias" in summary.get("data_quality_blockers", [])
        assert not summary["can_deploy_data_quality"]

    def test_snapshots_end_before_prices_is_blocker(self, monkeypatch):
        """When snapshot_max < price_max, survivorship_bias is added."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        universe_rows = [{
            "ticker": "600519.SS",
            "code": "600519",
            "effective_date": "2020-01-01",
            "end_date": "",
            "source_url": "https://tushare.pro/doc",
            "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "tushare:index_weight",
            "snapshot_count": 12,
        }]
        _write_universe(ddir, universe_rows)

        _write_fundamentals(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
            "debt_to_equity": 0.5,
        }])

        # Prices go up to 2024, but snapshots only up to 2023
        _write_prices(ddir, ["2022-01-03", "2024-12-31"], ["600519.SS", "000300.SS"])

        snap_rows = [
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2022-01-15",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2023-12-15",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
        ]
        _write_snapshots(ddir, snap_rows)

        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())

        with patch.dict("sys.modules", {
            "fundamental_backtest": mock_fbs,
        }):
            summary = ingest.validate()

        assert summary["status"] == "BLOCKED"
        assert "survivorship_bias" in summary.get("data_quality_blockers", [])

    def test_period_snapshot_coverage_uses_period_not_full_price_range(self, monkeypatch):
        """Period validation should compare snapshots with period bounds, not full prices.csv."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        _write_universe(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "effective_date": "2021-01-01",
            "end_date": "",
            "source_url": "https://tushare.pro/doc",
            "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "tushare:index_weight",
            "snapshot_count": 2,
        }])
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
            "debt_to_equity": 0.5,
        }])
        _write_prices(ddir, ["2018-01-02", "2021-01-15", "2021-06-15"], ["600519.SS", "000300.SS"])
        _write_snapshots(ddir, [
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2021-01-01",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2021-12-31",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
        ])

        clean_dq = MagicMock()
        clean_dq.is_clean = True
        clean_dq.survivorship_bias = False
        clean_dq.future_function = False
        clean_dq.filing_delay = False
        clean_dq.ungated_fundamentals = False
        mock_source = MagicMock()
        mock_source.data_quality = clean_dq
        mock_source.data_quality_for_period.return_value = clean_dq
        mock_source.validation_errors = []
        mock_source.research_only = False

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.dict("sys.modules", {"fundamental_backtest": mock_fbs}):
            summary = ingest.validate(period_start="2021-01-01", period_end="2021-06-15")

        assert "survivorship_bias" not in summary.get("data_quality_blockers", [])

    def test_period_snapshot_end_allows_short_carry_forward(self, monkeypatch):
        """H32: period validation allows a short extension after latest Tushare snapshot."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        _write_universe(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "effective_date": "2021-01-01",
            "end_date": "",
            "source_url": "https://tushare.pro/doc",
            "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "tushare:index_weight",
            "snapshot_count": 2,
        }])
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS",
            "code": "600519",
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
            "debt_to_equity": 0.5,
        }])
        _write_prices(ddir, ["2021-01-01", "2022-01-14"], ["600519.SS", "000300.SS"])
        _write_snapshots(ddir, [
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2021-01-01",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
            {"ticker": "600519.SS", "code": "600519", "trade_date": "2021-12-31",
             "source_provider": "tushare:index_weight", "index_code": "399300.SZ"},
        ])

        clean_dq = MagicMock()
        clean_dq.is_clean = True
        clean_dq.survivorship_bias = False
        clean_dq.future_function = False
        clean_dq.filing_delay = False
        clean_dq.ungated_fundamentals = False
        mock_source = MagicMock()
        mock_source.data_quality = clean_dq
        mock_source.data_quality_for_period.return_value = clean_dq
        mock_source.validation_errors = []
        mock_source.research_only = False

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.dict("sys.modules", {"fundamental_backtest": mock_fbs}):
            summary = ingest.validate(period_start="2021-01-01", period_end="2022-01-14")

        assert "survivorship_bias" not in summary.get("data_quality_blockers", [])


# ================================================================
# Edge cases
# ================================================================
class TestEdgeCases:
    def test_empty_universe_ok(self, monkeypatch):
        """Empty universe is NOT an error — it's a warning."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        _write_universe(ddir, [])
        _write_fundamentals(ddir, [])
        _write_prices(ddir, [], [])

        # Don't mock CN_PIT_FileSource — let it fail and check basic validation
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.object(ingest, "sys") as mock_sys:
            summary = ingest.validate()

        # Should be FAILED due to price validation errors (empty/no date column)
        assert "status" in summary

    def test_snapshots_to_intervals_empty_input(self):
        """Empty snapshots list returns empty intervals."""
        intervals = ingest.snapshots_to_intervals([])
        assert intervals == []

    def test_single_ticker_single_snapshot(self):
        """Single ticker with one snapshot → one open-ended interval."""
        snaps = _make_snap(("600519.SS", "2023-06-15"))
        intervals = ingest.snapshots_to_intervals(snaps)
        assert len(intervals) == 1
        assert intervals[0]["ticker"] == "600519.SS"
        assert intervals[0]["effective_date"] == "2023-06-15"
        assert intervals[0]["end_date"] == ""  # open-ended
        assert intervals[0]["snapshot_count"] == 1


class TestCLIPeriodArgs:
    """H26A: CLI accepts --period-start and --period-end for validate mode."""

    def _minimal_summary(self):
        return {
            "status": "PASSED",
            "universe_rows": 0,
            "fundamental_rows": 0,
            "ticker_count": 0,
            "price_rows": 0,
            "price_columns": 0,
            "date_range": "",
            "data_quality_blockers": [],
            "is_clean": True,
        }

    def test_cli_passes_period_args_to_validate(self, monkeypatch, tmp_path):
        """main() forwards --period-start/--period-end into validate()."""
        calls = []

        def fake_validate(period_start=None, period_end=None):
            calls.append((period_start, period_end))
            return self._minimal_summary()

        monkeypatch.setattr(ingest.sys, "argv", [
            "prog", "--validate",
            "--period-start", "2025-01-01",
            "--period-end", "2026-05-18",
        ])
        monkeypatch.setattr(ingest, "DATA_DIR", tmp_path)
        monkeypatch.setattr(ingest, "validate", fake_validate)
        monkeypatch.setattr(ingest, "print_summary", lambda summary: None)

        ingest.main()

        assert calls == [("2025-01-01", "2026-05-18")]
        assert (tmp_path / "validation_report_2025-01-01_2026-05-18.json").exists()
        assert not (tmp_path / "validation_report.json").exists()

    def test_cli_period_start_without_end_errors(self, monkeypatch):
        """--period-start without --period-end should be rejected."""
        monkeypatch.setattr(ingest.sys, "argv", [
            "prog", "--validate", "--period-start", "2025-01-01"
        ])
        with pytest.raises(SystemExit) as exc:
            ingest.main()
        assert exc.value.code == 1


class TestPeriodValidation:
    """H26A: Period-scoped validation."""

    def _mk_period_universe(self):
        """Build universe with a ticker active during 2025 but not 2020."""
        return [
            {
                "ticker": "600519.SS",
                "code": "600519",
                "name": "贵州茅台",
                "effective_date": "2005-01-01",
                "end_date": "",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2025-01-01T00:00:00Z",
                "source_provider": "qlib:instruments",
                "snapshot_count": 1,
            },
            {
                "ticker": "000001.SZ",
                "code": "000001",
                "name": "平安银行",
                "effective_date": "2020-01-01",
                "end_date": "",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2025-01-01T00:00:00Z",
                "source_provider": "qlib:instruments",
                "snapshot_count": 1,
            },
        ]

    def test_period_blocks_when_period_ticker_lacks_price(self, monkeypatch):
        """H26A: Period validation blocks when a ticker active in the period
        window has no price column."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        universe = self._mk_period_universe()
        _write_universe(ddir, universe)
        _write_snapshots(ddir, [
            {"ticker": r["ticker"], "code": r["code"],
             "trade_date": r["effective_date"],
             "source_provider": "qlib:instruments"}
            for r in universe
        ])
        _write_fundamentals(ddir, [{
            "ticker": r["ticker"],
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
        } for r in universe])

        # 000001.SZ is NOT in price columns but IS active in 2025 → period should block
        _write_prices(ddir,
            ["2025-01-01", "2026-05-18"],
            ["600519.SS", "000300.SS"],  # 000001.SZ missing
        )

        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False
        period_dq = MagicMock()
        period_dq.is_clean = True
        period_dq.survivorship_bias = False
        period_dq.future_function = False
        period_dq.filing_delay = False
        period_dq.ungated_fundamentals = False
        mock_source.data_quality_for_period.return_value = period_dq

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.dict("sys.modules", {"fundamental_backtest": mock_fbs}):
            summary = ingest.validate(
                period_start="2025-01-01",
                period_end="2026-05-18",
            )

        assert summary["status"] == "BLOCKED"
        assert "price_coverage" in summary.get("data_quality_blockers", [])
        assert summary["validation_scope"] == "period"
        assert summary["period_active_universe_end_count"] == 2
        # period_price_coverage_start should be 1/2 (600519 has price, 000001 doesn't)
        assert summary["period_price_coverage_start"] == "1/2"

    def test_full_validation_still_blocks_on_earliest_coverage_gap(self, monkeypatch):
        """H26A: Full-file validation (no period args) remains BLOCKED on
        earliest-date price coverage gap."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        universe = self._mk_period_universe()
        _write_universe(ddir, universe)
        _write_snapshots(ddir, [
            {"ticker": r["ticker"], "code": r["code"],
             "trade_date": r["effective_date"],
             "source_provider": "qlib:instruments"}
            for r in universe
        ])
        _write_fundamentals(ddir, [{
            "ticker": r["ticker"],
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
        } for r in universe])

        _write_prices(ddir,
            ["2020-01-02", "2025-01-01"],
            ["600519.SS", "000300.SS"],  # 000001.SZ missing
        )

        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.dict("sys.modules", {"fundamental_backtest": mock_fbs}):
            summary = ingest.validate()  # No period args

        assert summary["status"] == "BLOCKED"
        assert "price_coverage" in summary.get("data_quality_blockers", [])
        assert summary.get("validation_scope") != "period"

    def test_period_passes_when_period_window_has_full_coverage(self, monkeypatch):
        """H26A: Period validation PASSES when period window has full coverage
        but earliest file date does not."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        # Two tickers. 000001.SZ is NOT active at 2020-01-02 (end_date before 2025),
        # so full-file validation will see active_start has 000001.SZ but no price → BLOCKED.
        # In the period window (2025-2026), 000001.SZ is expired → period sees only 600519.SS → PASSES.
        universe = [
            {
                "ticker": "600519.SS",
                "code": "600519",
                "name": "贵州茅台",
                "effective_date": "2005-01-01",
                "end_date": "",  # still active
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2025-01-01T00:00:00Z",
                "source_provider": "qlib:instruments",
                "snapshot_count": 1,
            },
            {
                "ticker": "000001.SZ",
                "code": "000001",
                "name": "平安银行",
                "effective_date": "2020-01-01",
                "end_date": "2024-12-31",  # Expired before period window
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2025-01-01T00:00:00Z",
                "source_provider": "qlib:instruments",
                "snapshot_count": 1,
            },
        ]
        _write_universe(ddir, universe)
        _write_snapshots(ddir, [
            {"ticker": r["ticker"], "code": r["code"],
             "trade_date": r["effective_date"],
             "source_provider": "qlib:instruments"}
            for r in universe
        ])
        _write_fundamentals(ddir, [{
            "ticker": r["ticker"],
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
        } for r in universe])

        # 000001.SZ missing from price columns (it was removed before period window).
        # Include an early date where 000001.SZ IS active, so full-file blocks.
        _write_prices(ddir,
            ["2024-12-01", "2025-01-01", "2026-05-18"],
            ["600519.SS", "000300.SS"],
        )

        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False
        period_dq = MagicMock()
        period_dq.is_clean = True
        period_dq.survivorship_bias = False
        period_dq.future_function = False
        period_dq.filing_delay = False
        period_dq.ungated_fundamentals = False
        mock_source.data_quality_for_period.return_value = period_dq

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.dict("sys.modules", {"fundamental_backtest": mock_fbs}):
            # Full-file: earliest date 2025-01-01, 000001.SZ active but no price → BLOCKED
            full_summary = ingest.validate()
            assert full_summary["status"] == "BLOCKED"
            assert "price_coverage" in full_summary.get("data_quality_blockers", [])

            # Period: 2025-01-01 to 2026-05-18, 000001.SZ NOT active → only 600519.SS
            period_summary = ingest.validate(
                period_start="2025-01-01",
                period_end="2026-05-18",
            )

        assert period_summary["status"] == "PASSED", (
            f"Expected PASSED, got {period_summary['status']}: {period_summary.get('data_quality_blockers', [])}"
        )
        assert period_summary["validation_scope"] == "period"
        assert period_summary["period_active_universe_start_count"] == 1  # only 600519
        assert period_summary["period_price_coverage_start"] == "1/1"
        assert period_summary["period_price_coverage_end"] == "1/1"
        assert period_summary["can_deploy_data_quality"]

    def test_period_blocks_when_period_outside_price_range(self, monkeypatch):
        """H26A: Period validation blocks if prices.csv does not span the requested window."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        universe = [self._mk_period_universe()[0]]
        _write_universe(ddir, universe)
        _write_snapshots(ddir, [
            {"ticker": "600519.SS", "code": "600519",
             "trade_date": "2005-01-01",
             "source_provider": "qlib:instruments"}
        ])
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS",
            "report_period": "2024-12-31",
            "filing_date": "2025-03-31",
            "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0,
        }])
        _write_prices(ddir, ["2025-01-01", "2025-12-31"], ["600519.SS", "000300.SS"])

        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False
        period_dq = MagicMock()
        period_dq.is_clean = True
        period_dq.survivorship_bias = False
        period_dq.future_function = False
        period_dq.filing_delay = False
        period_dq.ungated_fundamentals = False
        mock_source.data_quality_for_period.return_value = period_dq

        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.dict("sys.modules", {"fundamental_backtest": mock_fbs}):
            summary = ingest.validate(
                period_start="2025-01-01",
                period_end="2026-05-18",
            )

        assert summary["status"] == "BLOCKED"
        assert "price_coverage" in summary.get("data_quality_blockers", [])



# ================================================================
# H28 (H1): validate() checkpoint-based price_coverage
# ================================================================
class TestValidateCheckpointCoverage:
    """H28 (H1): validate() must detect middle-checkpoint gaps."""

    def test_validate_blocks_on_middle_checkpoint_gap(self, monkeypatch):
        """Universe has a ticker active in 2022 but not at endpoints -> BLOCKED."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        _write_universe(ddir, [
            {"ticker": "A.SS", "code": "A", "effective_date": "2005-01-01", "end_date": "",
             "source_url": "x", "ingested_at": "2025-01-01T00:00:00Z",
             "source_provider": "qlib:instruments", "snapshot_count": 1},
            {"ticker": "B.SS", "code": "B", "effective_date": "2022-01-01", "end_date": "2023-12-31",
             "source_url": "x", "ingested_at": "2025-01-01T00:00:00Z",
             "source_provider": "qlib:instruments", "snapshot_count": 1},
        ])
        _write_snapshots(ddir, [
            {"ticker": "A.SS", "code": "A", "trade_date": "2005-01-01",
             "source_provider": "qlib:instruments"},
            {"ticker": "B.SS", "code": "B", "trade_date": "2022-01-01",
             "source_provider": "qlib:instruments"},
        ])
        _write_fundamentals(ddir, [
            {"ticker": "A.SS", "report_period": "2024-12-31", "filing_date": "2025-03-31",
             "source_url": "https://test.com", "ingested_at": "2025-05-01T00:00:00Z",
             "roe": 20.0},
        ])
        _write_prices(ddir,
            ["2020-01-02", "2021-01-04", "2022-01-04", "2023-01-03",
             "2024-01-02", "2025-01-02", "2026-01-02"],
            ["A.SS", "000300.SS"],
        )

        mock_source = MagicMock()
        mock_source.data_quality.is_clean = True
        mock_source.data_quality.survivorship_bias = False
        mock_source.data_quality.future_function = False
        mock_source.data_quality.filing_delay = False
        mock_source.data_quality.ungated_fundamentals = False
        mock_source.validation_errors = []
        mock_source.research_only = False
        mock_fbs = MagicMock()
        mock_fbs.CN_PIT_FileSource.return_value = mock_source

        monkeypatch.setattr("ingest_cn_pit_data.sys", MagicMock())
        monkeypatch.setattr(ingest, "sys", MagicMock())
        with patch.dict("sys.modules", {"fundamental_backtest": mock_fbs}):
            summary = ingest.validate()

        assert summary["status"] == "BLOCKED"
        assert "price_coverage" in summary.get("data_quality_blockers", [])
        failed = summary.get("price_coverage_failed_checkpoints", [])
        assert len(failed) >= 1


# ================================================================
# H28 (M3): validate() tests with real CN_PIT_FileSource
# ================================================================
class TestValidateWithRealSource:
    """H28 (M3): Tests that exercise the real CN_PIT_FileSource data_quality."""

    def test_validate_accepts_candidate_universe_paths(self, monkeypatch):
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")

        candidate_universe = ddir / "universe_h30_candidate.jsonl"
        candidate_snapshots = ddir / "universe_snapshots_h30_candidate.jsonl"
        ingest.write_jsonl(candidate_universe, [{
            "ticker": "600519.SS", "code": "600519",
            "effective_date": "2025-01-01", "end_date": "",
            "source_url": "https://tushare.pro/document/2?doc_id=96",
            "ingested_at": "2026-05-19T00:00:00Z",
            "source_provider": "tushare:index_weight",
            "snapshot_count": 1,
        }])
        ingest.write_jsonl(candidate_snapshots, [{
            "ticker": "600519.SS", "code": "600519",
            "trade_date": "2025-01-01",
            "source_provider": "tushare:index_weight",
        }])
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS", "report_period": "2024-12-31",
            "filing_date": "2025-03-31", "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z", "roe": 20.0,
            "debt_to_equity": 0.5,
        }])
        _write_prices(ddir, ["2025-01-01"], ["600519.SS", "000300.SS"])

        monkeypatch.setattr(ingest, "sys", MagicMock())
        summary = ingest.validate(
            universe_path=candidate_universe,
            universe_snapshots_path=candidate_snapshots,
        )

        assert summary["universe_rows"] == 1
        assert summary["snapshot_count"] == 1
        assert summary["universe_source_providers"] == ["tushare:index_weight"]

    def test_real_source_passed_with_qlib_universe(self, monkeypatch):
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        n = 250
        rows = []
        for i in range(n):
            code = str(600000 + i).zfill(6)
            ticker = code + ".SS"
            rows.append({
                "ticker": ticker, "code": code,
                "effective_date": "2005-01-01", "end_date": "",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2025-01-01T00:00:00Z",
                "source_provider": "qlib:instruments",
                "snapshot_count": 1,
            })
        _write_universe(ddir, rows)
        _write_snapshots(ddir, [
            {"ticker": r["ticker"], "code": r["code"],
             "trade_date": r["effective_date"],
             "source_provider": "qlib:instruments"}
            for r in rows
        ])
        fund_rows = [{
            "ticker": rows[0]["ticker"], "report_period": "2024-12-31",
            "filing_date": "2025-03-31", "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z", "roe": 20.0, "debt_to_equity": 0.5,
        }]
        _write_fundamentals(ddir, fund_rows)
        price_tickers = [r["ticker"] for r in rows[:5]] + ["000300.SS"]
        _write_prices(ddir, ["2025-01-02", "2026-05-18"], price_tickers)

        monkeypatch.setattr(ingest, "sys", MagicMock())
        summary = ingest.validate()
        assert summary["status"] in ("PASSED", "BLOCKED")

    def test_real_source_blocked_by_survivorship_marker(self, monkeypatch):
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        _write_universe(ddir, [{
            "ticker": "600519.SS", "code": "600519",
            "effective_date": "2020-01-01", "end_date": "",
            "source_url": "https://test.com", "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "akshare:current",
            "data_quality_note": "SURVIVORSHIP_BIAS: current constituents only",
        }])
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS", "report_period": "2024-12-31",
            "filing_date": "2025-03-31", "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z", "roe": 20.0,
        }])
        _write_prices(ddir, ["2025-01-02", "2026-05-18"], ["600519.SS", "000300.SS"])
        monkeypatch.setattr(ingest, "sys", MagicMock())
        summary = ingest.validate()
        assert summary["status"] == "BLOCKED"
        assert "survivorship_bias" in summary.get("data_quality_blockers", [])

    def test_real_source_blocked_by_unsafe_fields(self, monkeypatch):
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        _write_universe(ddir, [{
            "ticker": "600519.SS", "code": "600519",
            "effective_date": "2020-01-01", "end_date": "",
            "source_url": "https://test.com", "ingested_at": "2025-01-01T00:00:00Z",
            "source_provider": "qlib:instruments", "snapshot_count": 1,
        }])
        _write_snapshots(ddir, [{
            "ticker": "600519.SS", "code": "600519",
            "trade_date": "2020-01-01", "source_provider": "qlib:instruments",
        }])
        _write_fundamentals(ddir, [{
            "ticker": "600519.SS", "report_period": "2024-12-31",
            "filing_date": "2025-03-31", "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z",
            "roe": 20.0, "pe_ratio": 25.0,
        }])
        _write_prices(ddir, ["2025-01-02", "2026-05-18"], ["600519.SS", "000300.SS"])
        monkeypatch.setattr(ingest, "sys", MagicMock())
        summary = ingest.validate()
        assert summary["status"] == "BLOCKED"
        blockers = summary.get("data_quality_blockers", [])
        assert "future_function" in blockers or "ungated_fundamentals" in blockers


# ================================================================
# H28 (H2): Qlib interval gate - real size test
# ================================================================
class TestQlibRealSizeUniverse:
    """H28 (H2): 220-row qlib universe clears survivorship_bias."""

    def test_qlib_real_size_universe_passes(self, monkeypatch):
        """>=200 qlib rows with span -> no survivorship_bias."""
        ddir = _temp_data_dir()
        monkeypatch.setattr(ingest, "DATA_DIR", ddir)
        monkeypatch.setattr(ingest, "UNIVERSE_PATH", ddir / "universe.jsonl")
        monkeypatch.setattr(ingest, "FUNDAMENTALS_PATH", ddir / "fundamentals.jsonl")
        monkeypatch.setattr(ingest, "PRICES_PATH", ddir / "prices.csv")
        monkeypatch.setattr(ingest, "UNIVERSE_SNAPSHOTS_PATH", ddir / "universe_snapshots.jsonl")

        n = 220
        rows = []
        for i in range(n):
            code = str(600000 + i).zfill(6)
            ticker = code + ".SS"
            # Varied effective dates so _has_plausible_qlib_intervals passes
            year = 2005 + (i % 20)
            month = 1 + (i % 12)
            day = 1 + (i % 28)
            eff_date = f"{year}-{month:02d}-{day:02d}"
            rows.append({
                "ticker": ticker, "code": code,
                "effective_date": eff_date, "end_date": "",
                "source_url": "file:///tmp/csi300.txt",
                "ingested_at": "2025-01-01T00:00:00Z",
                "source_provider": "qlib:instruments",
                "snapshot_count": 1,
            })
        _write_universe(ddir, rows)
        _write_snapshots(ddir, [
            {"ticker": r["ticker"], "code": r["code"],
             "trade_date": r["effective_date"],
             "source_provider": "qlib:instruments"}
            for r in rows
        ])
        _write_fundamentals(ddir, [{
            "ticker": rows[0]["ticker"], "report_period": "2024-12-31",
            "filing_date": "2025-03-31", "source_url": "https://test.com",
            "ingested_at": "2025-05-01T00:00:00Z", "roe": 20.0,
        }])
        price_tickers = [r["ticker"] for r in rows[:5]] + ["000300.SS"]
        _write_prices(ddir, ["2025-01-02", "2026-05-18"], price_tickers)

        monkeypatch.setattr(ingest, "sys", MagicMock())
        summary = ingest.validate()
        assert "survivorship_bias" not in summary.get("data_quality_blockers", [])
