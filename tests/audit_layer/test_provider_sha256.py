import sys
import os
import unittest

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


class TestProviderSha256(unittest.TestCase):
    def test_same_dataframe_yields_same_sha256(self):
        """4.3(a): Same DataFrame yields identical sha256 across two calls (stability)."""
        from backtest.market_data import compute_prices_sha256

        index = pd.to_datetime(["2026-04-20", "2026-04-21"])
        prices = pd.DataFrame(
            {"600519.SS": [100.0, 101.0], "000858.SZ": [50.0, 51.0]},
            index=index,
        )

        h1 = compute_prices_sha256(prices)
        h2 = compute_prices_sha256(prices)

        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)  # sha256 hex digest

    def test_modified_cell_yields_different_sha256(self):
        """4.3(b): Changing a single cell produces a different sha256."""
        from backtest.market_data import compute_prices_sha256

        index = pd.to_datetime(["2026-04-20", "2026-04-21"])
        prices = pd.DataFrame(
            {"600519.SS": [100.0, 101.0], "000858.SZ": [50.0, 51.0]},
            index=index,
        )

        h_before = compute_prices_sha256(prices)
        prices.loc["2026-04-21", "600519.SS"] = 999.0
        h_after = compute_prices_sha256(prices)

        self.assertNotEqual(h_before, h_after)

    def test_checksum_mismatch_error_message_format(self):
        """4.3(c): ChecksumMismatchError raises with the expected message format."""
        from backtest.market_data import ChecksumMismatchError

        expected = "a" * 64
        actual = "b" * 64
        context = "ingest_smoke_test"

        with self.assertRaises(ChecksumMismatchError) as cm:
            raise ChecksumMismatchError(expected, actual, context)

        msg = str(cm.exception)
        self.assertIn("[BLOCKER:checksum]", msg)
        self.assertIn("context=ingest_smoke_test", msg)
        self.assertIn(f"expected={expected[:12]}...", msg)
        self.assertIn(f"actual={actual[:12]}...", msg)


if __name__ == "__main__":
    unittest.main()
