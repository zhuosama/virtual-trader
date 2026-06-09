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
        self.assertEqual(result.sources_used["600519.SS"], "static:in_memory")
        self.assertEqual(result.cache_hit_ratio, 0.0)
        self.assertEqual(result.missing_symbols, [])

    def test_fallback_records_tried_sources_and_missing_symbols(self):
        from backtest.market_data import FallbackMarketDataProvider, ProviderResult, StaticPriceProvider

        class EmptyProvider:
            name = "empty"

            def precheck(self):
                return None

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

        # New semantics: first data-bearing provider wins.
        # EmptyProvider returned empty → fallback to StaticPriceProvider → data found.
        # INFRA_ERROR because 000300.SS is missing from StaticPriceProvider.
        self.assertEqual(result.status, "INFRA_ERROR")
        self.assertIn("empty", result.sources_tried)
        self.assertIn("static:in_memory", result.sources_tried)
        self.assertEqual(result.sources_used["600519.SS"], "static:in_memory")
        self.assertEqual(result.missing_symbols, ["000300.SS"])
        # New fields
        self.assertEqual(result.fallback_chain, ["empty", "static:in_memory"])
        self.assertEqual(result.fallback_reason, "empty-data: empty")
        self.assertEqual(result.sources_used.get("__selected"), "static:in_memory")

    def test_cache_metadata_marks_stale_adjusted_data(self):
        from backtest.market_data import CacheEntryMeta, is_cache_fresh

        fetched = datetime(2026, 5, 1, tzinfo=timezone.utc)
        fresh_today = datetime(2026, 5, 4, tzinfo=timezone.utc)
        stale_today = datetime(2026, 5, 8, tzinfo=timezone.utc)

        meta = CacheEntryMeta(
            symbol="600519.SS",
            provider="akshare:stock_zh_a_hist_qfq",
            frequency="daily",
            adjustment="qfq",
            start="2026-04-01",
            end="2026-04-30",
            data_fetched_at=fetched.isoformat(),
        )

        self.assertTrue(is_cache_fresh(meta, fresh_today))
        self.assertFalse(is_cache_fresh(meta, stale_today))

    # ── Task 3.3 new test cases ──────────────────────────────────────

    def test_fallback_all_precheck_ok_primary_returns_data(self):
        """3.3 case 1: All providers precheck OK, primary returns data."""
        from backtest.market_data import FallbackMarketDataProvider, StaticPriceProvider

        index = pd.to_datetime(["2026-04-20"])
        primary_prices = pd.DataFrame({"600519.SS": [100.0]}, index=index)
        secondary_prices = pd.DataFrame({"000858.SZ": [50.0]}, index=index)

        provider = FallbackMarketDataProvider([
            StaticPriceProvider(primary_prices),
            StaticPriceProvider(secondary_prices),
        ])
        result = provider.get_close_prices(
            ["600519.SS"],
            "2026-04-20",
            "2026-04-20",
        )

        self.assertEqual(result.fallback_chain, ["static:in_memory"])
        self.assertEqual(result.sources_used.get("__selected"), "static:in_memory")
        self.assertIsNone(result.fallback_reason)
        self.assertEqual(result.precheck_log, [])

    def test_fallback_primary_precheck_fails_secondary_succeeds(self):
        """3.3 case 2: Primary precheck fails, secondary succeeds."""
        from backtest.market_data import (
            FallbackMarketDataProvider,
            ProviderResult,
            StaticPriceProvider,
            LoaderBlockedError,
        )

        class FailingPrecheckProvider:
            name = "failing-provider"

            def precheck(self):
                raise LoaderBlockedError("failing-provider", "test token missing")

            def get_close_prices(self, tickers, start, end):
                return ProviderResult(status="OK", prices=pd.DataFrame())

        index = pd.to_datetime(["2026-04-20"])
        secondary_prices = pd.DataFrame({"600519.SS": [100.0]}, index=index)

        provider = FallbackMarketDataProvider([
            FailingPrecheckProvider(),
            StaticPriceProvider(secondary_prices),
        ])
        result = provider.get_close_prices(
            ["600519.SS"],
            "2026-04-20",
            "2026-04-20",
        )

        self.assertEqual(
            result.fallback_chain, ["failing-provider", "static:in_memory"]
        )
        self.assertEqual(result.sources_used.get("__selected"), "static:in_memory")
        self.assertEqual(result.fallback_reason, "precheck-blocked: failing-provider")
        self.assertEqual(len(result.precheck_log), 1)
        self.assertIn("precheck-blocked=failing-provider", result.precheck_log[0])
        self.assertIn("test token missing", result.precheck_log[0])

    def test_fallback_all_providers_fail_precheck(self):
        """3.3 case 3: All providers fail precheck → LoaderBlockedError raised."""
        from backtest.market_data import FallbackMarketDataProvider, LoaderBlockedError

        class AlwaysBlockedProvider:
            name = "blocked-1"

            def precheck(self):
                raise LoaderBlockedError("blocked-1", "no token")

            def get_close_prices(self, tickers, start, end):
                raise NotImplementedError

        class AlsoBlockedProvider:
            name = "blocked-2"

            def precheck(self):
                raise LoaderBlockedError("blocked-2", "no library")

            def get_close_prices(self, tickers, start, end):
                raise NotImplementedError

        provider = FallbackMarketDataProvider([
            AlwaysBlockedProvider(),
            AlsoBlockedProvider(),
        ])
        with self.assertRaises(LoaderBlockedError) as ctx:
            provider.get_close_prices(
                ["600519.SS"],
                "2026-04-20",
                "2026-04-20",
            )
        self.assertEqual(ctx.exception.provider, "fallback")
        self.assertIn("all providers exhausted", ctx.exception.reason)

    def test_fallback_primary_empty_secondary_has_data(self):
        """3.3 case 4: Primary precheck OK but returns empty, secondary returns data."""
        from backtest.market_data import FallbackMarketDataProvider, ProviderResult, StaticPriceProvider

        class EmptyResultProvider:
            name = "empty-provider"

            def precheck(self):
                return None

            def get_close_prices(self, tickers, start, end):
                return ProviderResult(
                    status="INFRA_ERROR",
                    prices=pd.DataFrame(),
                    sources_tried=["empty-provider"],
                    missing_symbols=list(tickers),
                )

        index = pd.to_datetime(["2026-04-20"])
        secondary_prices = pd.DataFrame({"600519.SS": [100.0]}, index=index)

        provider = FallbackMarketDataProvider([
            EmptyResultProvider(),
            StaticPriceProvider(secondary_prices),
        ])
        result = provider.get_close_prices(
            ["600519.SS"],
            "2026-04-20",
            "2026-04-20",
        )

        self.assertEqual(result.fallback_reason, "empty-data: empty-provider")
        self.assertEqual(result.fallback_chain, ["empty-provider", "static:in_memory"])
        self.assertEqual(result.missing_symbols, [])
        self.assertEqual(result.status, "OK")


if __name__ == "__main__":
    unittest.main()
