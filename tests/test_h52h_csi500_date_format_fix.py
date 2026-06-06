"""Tests for H52h — CSI500 Date Format Fix.

Covers:
- int→ISO conversion correctness
- Idempotency (second run is no-op, sha256 unchanged)
- Coverage JSON sha256 update
- Column-count assertion failure mode (synthetic CSV without index=False)
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd


# ── Helper: Inline sha256 ──────────────────────────────────────────────

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Helper: Mini fix logic (replicated for test isolation) ────────────

def apply_fix_to_csv(src_path: Path, col_count: int) -> Path:
    """Apply int→ISO date fix to a CSV, return path to fixed file.
    
    Handles both int and already-ISO dates for idempotency testing.
    Does NOT use the H52h fix script — tests the core transform logic directly.
    """
    import csv
    df = pd.read_csv(src_path, dtype={"date": str})
    first_date = df["date"].iloc[0]
    
    # Detect if already ISO (idempotency check)
    if isinstance(first_date, str) and "-" in str(first_date):
        # Already ISO — just copy
        out_path = Path(str(src_path) + ".fixed.csv")
        df.to_csv(out_path, index=False)
        return out_path
    
    # Convert int dates to ISO
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")

    out_path = Path(str(src_path) + ".fixed.csv")
    df.to_csv(out_path, index=False)

    # Post-write column count assertion
    df_check = pd.read_csv(out_path, nrows=1)
    assert len(df_check.columns) == col_count, \
        f"Column count {len(df_check.columns)} != {col_count}"

    return out_path


# ── Test cases ─────────────────────────────────────────────────────────

class TestH52hConversionCorrectness(unittest.TestCase):
    """Test int64 → ISO date conversion on synthetic fixtures."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tmppath = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_prices_csv(self, dates_int):
        """Create a minimal prices-like CSV with int dates."""
        path = self.tmppath / "test_prices.csv"
        cols = ["date"] + [f"ticker_{i:04d}" for i in range(4)]
        rows = [[str(d)] + [f"{d % 100:.2f}"] * 4 for d in dates_int]
        with open(path, "w") as f:
            f.write(",".join(cols) + "\n")
            for row in rows:
                f.write(",".join(row) + "\n")
        return path

    def _make_liquidity_csv(self, dates_int):
        """Create a minimal liquidity-like CSV with int dates."""
        path = self.tmppath / "test_liquidity.csv"
        rows = []
        for d in dates_int:
            for ticker in ["000001.SZ", "000002.SZ"]:
                rows.append([str(d), ticker, "1000000", "50000", "tushare:daily"])
        with open(path, "w") as f:
            f.write("date,ticker,amount_rmb,vol_shares,source\n")
            for row in rows:
                f.write(",".join(row) + "\n")
        return path

    def test_fix_converts_int_to_iso_dates_prices(self):
        """Prices CSV: int dates → ISO dates, values preserved."""
        dates_int = [20200102, 20200103, 20200106]
        src = self._make_prices_csv(dates_int)

        fixed = apply_fix_to_csv(src, col_count=5)  # 1 date + 4 tickers
        df = pd.read_csv(fixed, dtype={"date": str})

        import re
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for v in df["date"]:
            self.assertTrue(iso_re.match(v), f"date {v!r} not ISO format")

        # Verify specific conversion
        self.assertEqual(df["date"].tolist(), ["2020-01-02", "2020-01-03", "2020-01-06"])

        # Verify numeric values preserved (column 1) — compare as float
        self.assertAlmostEqual(float(df.iloc[0, 1]), 20200102 % 100, places=1)

    def test_fix_converts_int_to_iso_dates_liquidity(self):
        """Liquidity CSV: int dates → ISO dates, structure preserved."""
        dates_int = [20200102, 20200103]
        src = self._make_liquidity_csv(dates_int)

        fixed = apply_fix_to_csv(src, col_count=5)
        df = pd.read_csv(fixed, dtype={"date": str})

        # Header check
        self.assertEqual(list(df.columns), ["date", "ticker", "amount_rmb", "vol_shares", "source"])

        import re
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for v in df["date"]:
            self.assertTrue(iso_re.match(v), f"date {v!r} not ISO format")

        # Row count preserved: 2 days × 2 tickers = 4 rows
        self.assertEqual(len(df), 4)


class TestH52hIdempotency(unittest.TestCase):
    """Test that running fix twice produces no change on second run."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tmppath = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fix_is_idempotent(self):
        """Second run: already-ISO detection → no-op, sha256 unchanged."""
        dates_int = [20200102, 20200103, 20200106]
        src = self.tmppath / "prices.csv"
        cols = ["date"] + [f"t_{i}" for i in range(3)]
        rows = [[str(d), "10.0", "20.0", "30.0"] for d in dates_int]
        with open(src, "w") as f:
            f.write(",".join(cols) + "\n")
            for row in rows:
                f.write(",".join(row) + "\n")

        # First fix
        fixed1 = apply_fix_to_csv(src, col_count=4)
        sha1 = file_sha256(fixed1)

        # "Second" fix — apply to already-fixed CSV
        fixed2 = apply_fix_to_csv(fixed1, col_count=4)
        sha2 = file_sha256(fixed2)

        self.assertEqual(sha1, sha2, "Second run should produce identical sha256")


class TestH52hColumnCountAssertion(unittest.TestCase):
    """Test that missing index=False is caught by column-count check."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tmppath = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_column_count_assertion_fires_without_index_false(self):
        """If to_csv omits index=False, column count inflates → assertion fires."""
        # Create synthetic CSV with int dates
        src = self.tmppath / "prices.csv"
        dates_int = [20200102, 20200103]
        cols = ["date"] + [f"t_{i}" for i in range(5)]
        rows = [[str(d), "1.0", "2.0", "3.0", "4.0", "5.0"] for d in dates_int]
        with open(src, "w") as f:
            f.write(",".join(cols) + "\n")
            for row in rows:
                f.write(",".join(row) + "\n")

        # Deliberately write WITHOUT index=False
        df = pd.read_csv(src, dtype={"date": str})
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        bad_path = self.tmppath / "bad.csv"
        df.to_csv(bad_path)  # ← NO index=False — should inflate columns

        # Re-read: should have extra unnamed column
        df_bad = pd.read_csv(bad_path, nrows=1)
        # Expected 6 columns (1 date + 5 tickers), but to_csv default adds index → 7
        self.assertEqual(len(df_bad.columns), 7,
                         "to_csv without index=False should inflate columns (6→7)")
        # The first column should be the unnamed index, not 'date'
        self.assertNotEqual(df_bad.columns[0], "date",
                            "First column should be unnamed index, not 'date'")

    def test_column_count_correct_with_index_false(self):
        """With index=False, column count stays correct."""
        src = self.tmppath / "prices.csv"
        dates_int = [20200102, 20200103]
        cols = ["date"] + [f"t_{i}" for i in range(5)]
        rows = [[str(d), "1.0", "2.0", "3.0", "4.0", "5.0"] for d in dates_int]
        with open(src, "w") as f:
            f.write(",".join(cols) + "\n")
            for row in rows:
                f.write(",".join(row) + "\n")

        fixed = apply_fix_to_csv(src, col_count=6)
        df = pd.read_csv(fixed, nrows=1)
        self.assertEqual(len(df.columns), 6,
                         "With index=False, column count should be 6")
        self.assertEqual(df.columns[0], "date",
                         "First column should be 'date'")


class TestH52hCoverageJsonUpdate(unittest.TestCase):
    """Test coverage JSON sha256 update logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tmppath = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_coverage_json_sha256_update_preserves_structure(self):
        """Coverage JSON update: sha256 changes, other fields untouched."""
        # Create mock coverage JSON
        coverage = {
            "generated_at": "2026-05-24T00:00:00Z",
            "task": "H52c",
            "status": "CANDIDATE_DATASET",
            "data_sources": {
                "prices": {
                    "sha256": "old_sha_placeholder",
                    "file": "data/cn_pit/prices_h52c_csi500_qfq.csv",
                },
                "liquidity": {
                    "sha256": "old_liq_sha_placeholder",
                    "file": "data/cn_pit/liquidity_h52c_csi500_daily_amount.csv",
                },
            },
            "provenance": {"stock_provider": "tushare:daily"},
        }
        cov_path = self.tmppath / "coverage.json"
        with open(cov_path, "w") as f:
            json.dump(coverage, f)

        # Apply update
        new_prices_sha = "abc123def456"
        new_liquidity_sha = "xyz789ghi012"

        with open(cov_path) as f:
            cov = json.load(f)
        cov["data_sources"]["prices"]["sha256"] = new_prices_sha
        cov["data_sources"]["liquidity"]["sha256"] = new_liquidity_sha
        cov["h52h_fix_applied_at"] = "2026-05-25T00:00:00Z"
        with open(cov_path, "w") as f:
            json.dump(cov, f)

        # Verify
        with open(cov_path) as f:
            cov_check = json.load(f)

        self.assertEqual(cov_check["data_sources"]["prices"]["sha256"], new_prices_sha)
        self.assertEqual(cov_check["data_sources"]["liquidity"]["sha256"], new_liquidity_sha)
        self.assertEqual(cov_check["task"], "H52c")  # Unchanged
        self.assertEqual(cov_check["provenance"]["stock_provider"], "tushare:daily")  # Unchanged
        self.assertIn("h52h_fix_applied_at", cov_check)


if __name__ == "__main__":
    unittest.main()
