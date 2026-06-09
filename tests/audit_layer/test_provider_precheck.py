import builtins
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


class TestProviderPrecheck(unittest.TestCase):

    def test_tushare_precheck_raises_when_token_missing(self):
        """Unset TUSHARE_TOKEN -> precheck raises LoaderBlockedError with token message."""
        from backtest.market_data import LoaderBlockedError, TushareProvider

        saved = os.environ.pop("TUSHARE_TOKEN", None)
        try:
            with self.assertRaises(LoaderBlockedError) as ctx:
                TushareProvider().precheck()
            self.assertIn("TUSHARE_TOKEN env var missing", str(ctx.exception))
        finally:
            if saved is not None:
                os.environ["TUSHARE_TOKEN"] = saved

    def test_akshare_precheck_raises_when_module_missing(self):
        """Patch out akshare module -> precheck raises LoaderBlockedError."""
        from backtest.market_data import AkshareProvider, LoaderBlockedError

        real_import = builtins.__import__
        saved = sys.modules.pop("akshare", None)

        def _block_akshare(name, *args, **kwargs):
            if name == "akshare":
                raise ImportError("No module named 'akshare'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = _block_akshare
        try:
            with self.assertRaises(LoaderBlockedError):
                AkshareProvider().precheck()
        finally:
            builtins.__import__ = real_import
            if saved is not None:
                sys.modules["akshare"] = saved

    def test_tushare_precheck_ok_when_token_set(self):
        """Set TUSHARE_TOKEN=test -> precheck returns None (no raise)."""
        from backtest.market_data import TushareProvider

        saved = os.environ.get("TUSHARE_TOKEN")
        os.environ["TUSHARE_TOKEN"] = "test"
        try:
            result = TushareProvider().precheck()
            self.assertIsNone(result)
        finally:
            if saved is None:
                os.environ.pop("TUSHARE_TOKEN", None)
            else:
                os.environ["TUSHARE_TOKEN"] = saved


if __name__ == "__main__":
    unittest.main()
