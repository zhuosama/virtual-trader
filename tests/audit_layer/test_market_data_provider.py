import os
import sys
import unittest
from datetime import datetime, timezone

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


class TestMarketDataProvider(unittest.TestCase):
    def test_symbol_conversion_for_a_share_etf_and_index(self):
        from backtest.market_data import to_plain_code, to_tushare_symbol, to_baostock_symbol

        self.assertEqual(to_plain_code("600519.SS"), "600519")
        self.assertEqual(to_tushare_symbol("600519.SS"), "600519.SH")
        self.assertEqual(to_baostock_symbol("600519.SS"), "sh.600519")
        self.assertEqual(to_plain_code("000858.SZ"), "000858")
        self.assertEqual(to_tushare_symbol("000858.SZ"), "000858.SZ")
        self.assertEqual(to_baostock_symbol("000858.SZ"), "sz.000858")
        self.assertEqual(to_plain_code("510300.SS"), "510300")
        self.assertEqual(to_plain_code("000300.SS"), "000300")

    def test_static_provider_returns_provider_result(self):
        from backtest.market_data import StaticPriceProvider

        index = pd.to_datetime(["2026-04-20", "2026-04-21"])
        prices = pd.DataFrame(
            {"600519.SS": [100.0, 101.0], "000300.SS": [4000.0, 4010.0]},
            index=index,
        )
        result = StaticPriceProvider(prices).get_close_prices(
            ["600519.SS", "000300.SS"],
            "2026-04-20",
            "2026-04-21",
        )

        self.assertEqual(result.status, "OK")
        self.assertEqual(result.prices.shape, (2, 2))
        self.assertEqual(result.sources_used["600519.SS"], "static")
        self.assertEqual(result.cache_hit_ratio, 0.0)
        self.assertEqual(result.missing_symbols, [])

    def test_fallback_records_tried_sources_and_missing_symbols(self):
        from backtest.market_data import FallbackMarketDataProvider, ProviderResult, StaticPriceProvider

        class EmptyProvider:
            name = "empty"

            def get_close_prices(self, tickers, start, end):
                return ProviderResult(
                    status="INFRA_ERROR",
                    prices=pd.DataFrame(),
                    sources_tried=["empty"],
                    missing_symbols=list(tickers),
                )

        index = pd.to_datetime(["2026-04-20", "2026-04-21"])
        prices = pd.DataFrame({"600519.SS": [100.0, 101.0]}, index=index)
        provider = FallbackMarketDataProvider([EmptyProvider(), StaticPriceProvider(prices)])
        result = provider.get_close_prices(
            ["600519.SS", "000300.SS"],
            "2026-04-20",
            "2026-04-21",
        )

        self.assertEqual(result.status, "INFRA_ERROR")
        self.assertIn("empty", result.sources_tried)
        self.assertIn("static", result.sources_tried)
        self.assertEqual(result.sources_used["600519.SS"], "static")
        self.assertEqual(result.missing_symbols, ["000300.SS"])

    def test_cache_metadata_marks_stale_adjusted_data(self):
        from backtest.market_data import CacheEntryMeta, is_cache_fresh

        fetched = datetime(2026, 5, 1, tzinfo=timezone.utc)
        fresh_today = datetime(2026, 5, 4, tzinfo=timezone.utc)
        stale_today = datetime(2026, 5, 8, tzinfo=timezone.utc)

        meta = CacheEntryMeta(
            symbol="600519.SS",
            provider="akshare",
            frequency="daily",
            adjustment="qfq",
            start="2026-04-01",
            end="2026-04-30",
            data_fetched_at=fetched.isoformat(),
        )

        self.assertTrue(is_cache_fresh(meta, fresh_today))
        self.assertFalse(is_cache_fresh(meta, stale_today))


if __name__ == "__main__":
    unittest.main()
