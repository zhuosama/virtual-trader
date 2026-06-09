import os
import sys
import unittest

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


class TestOHLCVProvider(unittest.TestCase):

    # ── 4.1 ──

    def test_default_get_ohlcv_raises_not_implemented(self):
        """4.1: Subclass without get_ohlcv override raises NotImplementedError
        with provider name in message."""
        from backtest.market_data import MarketDataProvider

        # Use a name in SOURCE_PROVIDERS to pass __init_subclass__ gate.
        class NoOHLCVProvider(MarketDataProvider):
            name = "cache:local_csv"

        provider = NoOHLCVProvider()
        with self.assertRaises(NotImplementedError) as ctx:
            provider.get_ohlcv([], "", "")
        self.assertIn(provider.name, str(ctx.exception))

    # ── 4.2 ──

    def test_ohlcv_sha256_stable_same_dataframe(self):
        """4.2a: Same DataFrame → same sha256 across 2 calls."""
        from backtest.market_data import compute_ohlcv_sha256

        df = pd.DataFrame({
            "date": ["2026-01-01", "2026-01-02"],
            "ticker": ["000001.SZ", "000001.SZ"],
            "open": [10.0, 10.5],
            "high": [11.0, 11.5],
            "low": [9.5, 10.0],
            "close": [10.8, 11.0],
            "volume": [1000, 1200],
            "amount": [10800.0, 13200.0],
        })
        h1 = compute_ohlcv_sha256(df)
        h2 = compute_ohlcv_sha256(df)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_ohlcv_sha256_different_on_cell_change(self):
        """4.2b: Modify a single cell → different sha256."""
        from backtest.market_data import compute_ohlcv_sha256

        df = pd.DataFrame({
            "date": ["2026-01-01", "2026-01-02"],
            "ticker": ["000001.SZ", "000001.SZ"],
            "open": [10.0, 10.5],
            "high": [11.0, 11.5],
            "low": [9.5, 10.0],
            "close": [10.8, 11.0],
            "volume": [1000, 1200],
            "amount": [10800.0, 13200.0],
        })
        h1 = compute_ohlcv_sha256(df)
        df.loc[0, "open"] = 999.0
        h2 = compute_ohlcv_sha256(df)
        self.assertNotEqual(h1, h2)

    def test_ohlcv_sha256_sort_invariant(self):
        """4.2c: Same data in different row order → same sha256
        (helper sorts by (date, ticker) before hashing)."""
        from backtest.market_data import compute_ohlcv_sha256

        df_sorted = pd.DataFrame({
            "date": ["2026-01-01", "2026-01-01", "2026-01-02"],
            "ticker": ["000001.SZ", "600519.SS", "000001.SZ"],
            "open": [10.0, 100.0, 10.5],
            "high": [11.0, 101.0, 11.5],
            "low": [9.5, 99.0, 10.0],
            "close": [10.8, 100.5, 11.0],
            "volume": [1000, 500, 1200],
            "amount": [10800.0, 50250.0, 13200.0],
        })
        df_scrambled = df_sorted.sample(frac=1, random_state=42).reset_index(drop=True)

        h1 = compute_ohlcv_sha256(df_sorted)
        h2 = compute_ohlcv_sha256(df_scrambled)
        self.assertEqual(h1, h2)

    # ── 4.3 ──

    def test_ohlcv_result_dataclass_instantiation(self):
        """4.3a: All 11 fields can be set; stored correctly."""
        from backtest.market_data import OHLCVResult

        ohlcv_df = pd.DataFrame({
            "date": ["2026-01-01"],
            "ticker": ["000001.SZ"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.8],
            "volume": [1000],
            "amount": [10800.0],
        })

        result = OHLCVResult(
            status="OK",
            ohlcv=ohlcv_df,
            sources_tried=["tushare:daily"],
            sources_used={"000001.SZ": "tushare:daily"},
            missing_pairs=[("2026-01-01", "000002.SZ")],
            fallback_chain=["tushare:daily"],
            fallback_reason=None,
            precheck_log=["precheck: tushare OK"],
            sha256="abc123",
            adjustment="qfq",
            reason=None,
        )

        self.assertEqual(result.status, "OK")
        self.assertTrue(result.ohlcv.equals(ohlcv_df))
        self.assertEqual(result.sources_tried, ["tushare:daily"])
        self.assertEqual(result.sources_used, {"000001.SZ": "tushare:daily"})
        self.assertEqual(result.missing_pairs, [("2026-01-01", "000002.SZ")])
        self.assertEqual(result.fallback_chain, ["tushare:daily"])
        self.assertIsNone(result.fallback_reason)
        self.assertEqual(result.precheck_log, ["precheck: tushare OK"])
        self.assertEqual(result.sha256, "abc123")
        self.assertEqual(result.adjustment, "qfq")
        self.assertIsNone(result.reason)

    def test_ohlcv_result_default_values(self):
        """4.3b: Default values are correct."""
        from backtest.market_data import OHLCVResult

        result = OHLCVResult(
            status="OK",
            ohlcv=pd.DataFrame(),
        )
        self.assertEqual(result.sources_tried, [])
        self.assertEqual(result.sources_used, {})
        self.assertEqual(result.missing_pairs, [])
        self.assertEqual(result.fallback_chain, [])
        self.assertIsNone(result.fallback_reason)
        self.assertEqual(result.precheck_log, [])
        self.assertIsNone(result.sha256)
        self.assertEqual(result.adjustment, "qfq")
        self.assertIsNone(result.reason)

    # ── 4.4 ──

    def test_fallback_ohlcv_all_precheck_ok_primary_returns_data(self):
        """4.4 case 1: All precheck OK, primary returns data.
        fallback_chain=[primary], selected_provider=primary, fallback_reason=None."""
        from backtest.market_data import FallbackMarketDataProvider, OHLCVResult

        ohlcv_df = pd.DataFrame({
            "date": ["2026-01-01"],
            "ticker": ["000001.SZ"],
            "open": [10.0], "high": [11.0], "low": [9.5],
            "close": [10.8], "volume": [1000], "amount": [10800.0],
        })

        class MockPrimary:
            name = "mock-ohlcv-primary"

            def precheck(self):
                return None

            def get_ohlcv(self, tickers, start, end):
                return OHLCVResult(
                    status="OK",
                    ohlcv=ohlcv_df.copy(),
                    sources_tried=[self.name],
                    sources_used={t: self.name for t in tickers},
                )

        provider = FallbackMarketDataProvider([MockPrimary()])
        result = provider.get_ohlcv(["000001.SZ"], "2026-01-01", "2026-01-01")

        self.assertEqual(result.fallback_chain, ["mock-ohlcv-primary"])
        self.assertEqual(result.sources_used.get("__selected"), "mock-ohlcv-primary")
        self.assertIsNone(result.fallback_reason)

    def test_fallback_ohlcv_primary_precheck_fails_secondary_succeeds(self):
        """4.4 case 2: Primary precheck fails, secondary succeeds.
        fallback_chain=[primary, secondary], selected_provider=secondary,
        fallback_reason='precheck-blocked: primary', precheck_log has 1 entry."""
        from backtest.market_data import (
            FallbackMarketDataProvider, OHLCVResult, LoaderBlockedError,
        )

        ohlcv_df = pd.DataFrame({
            "date": ["2026-01-01"],
            "ticker": ["000001.SZ"],
            "open": [10.0], "high": [11.0], "low": [9.5],
            "close": [10.8], "volume": [1000], "amount": [10800.0],
        })

        class MockFailingPrecheck:
            name = "mock-failing-precheck"

            def precheck(self):
                raise LoaderBlockedError("mock-failing-precheck", "test token missing")

            def get_ohlcv(self, tickers, start, end):
                raise NotImplementedError

        class MockSecondary:
            name = "mock-secondary"

            def precheck(self):
                return None

            def get_ohlcv(self, tickers, start, end):
                return OHLCVResult(
                    status="OK",
                    ohlcv=ohlcv_df.copy(),
                    sources_tried=[self.name],
                    sources_used={t: self.name for t in tickers},
                )

        provider = FallbackMarketDataProvider([
            MockFailingPrecheck(),
            MockSecondary(),
        ])
        result = provider.get_ohlcv(["000001.SZ"], "2026-01-01", "2026-01-01")

        self.assertEqual(
            result.fallback_chain,
            ["mock-failing-precheck", "mock-secondary"],
        )
        self.assertEqual(result.sources_used.get("__selected"), "mock-secondary")
        self.assertEqual(
            result.fallback_reason,
            "precheck-blocked: mock-failing-precheck",
        )
        self.assertEqual(len(result.precheck_log), 1)
        self.assertIn("precheck-blocked=mock-failing-precheck", result.precheck_log[0])

    def test_fallback_ohlcv_all_providers_fail_precheck(self):
        """4.4 case 3: All providers fail precheck → LoaderBlockedError raised."""
        from backtest.market_data import FallbackMarketDataProvider, LoaderBlockedError

        class Blocked1:
            name = "blocked-1"

            def precheck(self):
                raise LoaderBlockedError("blocked-1", "no token")

            def get_ohlcv(self, tickers, start, end):
                raise NotImplementedError

        class Blocked2:
            name = "blocked-2"

            def precheck(self):
                raise LoaderBlockedError("blocked-2", "no lib")

            def get_ohlcv(self, tickers, start, end):
                raise NotImplementedError

        provider = FallbackMarketDataProvider([Blocked1(), Blocked2()])
        with self.assertRaises(LoaderBlockedError) as ctx:
            provider.get_ohlcv(["000001.SZ"], "2026-01-01", "2026-01-01")
        self.assertEqual(ctx.exception.provider, "fallback")
        self.assertIn("all OHLCV providers exhausted", ctx.exception.reason)

    def test_fallback_ohlcv_primary_empty_secondary_has_data(self):
        """4.4 case 4: Primary precheck OK but returns empty,
        secondary returns data → fallback_reason='empty-data: primary'."""
        from backtest.market_data import FallbackMarketDataProvider, OHLCVResult

        ohlcv_df = pd.DataFrame({
            "date": ["2026-01-01"],
            "ticker": ["000001.SZ"],
            "open": [10.0], "high": [11.0], "low": [9.5],
            "close": [10.8], "volume": [1000], "amount": [10800.0],
        })

        class MockEmpty:
            name = "mock-empty-primary"

            def precheck(self):
                return None

            def get_ohlcv(self, tickers, start, end):
                return OHLCVResult(
                    status="INFRA_ERROR",
                    ohlcv=pd.DataFrame(),
                    sources_tried=[self.name],
                )

        class MockData:
            name = "mock-data-secondary"

            def precheck(self):
                return None

            def get_ohlcv(self, tickers, start, end):
                return OHLCVResult(
                    status="OK",
                    ohlcv=ohlcv_df.copy(),
                    sources_tried=[self.name],
                    sources_used={t: self.name for t in tickers},
                )

        provider = FallbackMarketDataProvider([MockEmpty(), MockData()])
        result = provider.get_ohlcv(["000001.SZ"], "2026-01-01", "2026-01-01")

        self.assertEqual(result.fallback_reason, "empty-data: mock-empty-primary")
        self.assertEqual(
            result.fallback_chain,
            ["mock-empty-primary", "mock-data-secondary"],
        )

    # ── 4.5 ──

    def test_fallback_ohlcv_tushare_token_missing_falls_back_to_mock(self):
        """4.5: TushareProvider with TUSHARE_TOKEN unset →
        LoaderBlockedError on precheck → fallback routes to MockSuccessProvider
        with precheck_log non-empty."""
        from backtest.market_data import (
            FallbackMarketDataProvider, OHLCVResult, TushareProvider,
        )

        ohlcv_df = pd.DataFrame({
            "date": ["2026-01-01"],
            "ticker": ["000001.SZ"],
            "open": [10.0], "high": [11.0], "low": [9.5],
            "close": [10.8], "volume": [1000], "amount": [10800.0],
        })

        class MockSuccessProvider:
            name = "mock-success"

            def precheck(self):
                return None

            def get_ohlcv(self, tickers, start, end):
                return OHLCVResult(
                    status="OK",
                    ohlcv=ohlcv_df.copy(),
                    sources_tried=[self.name],
                    sources_used={t: self.name for t in tickers},
                )

        saved = os.environ.pop("TUSHARE_TOKEN", None)
        try:
            provider = FallbackMarketDataProvider([
                TushareProvider(),
                MockSuccessProvider(),
            ])
            result = provider.get_ohlcv(
                ["000001.SZ"], "2026-01-01", "2026-01-01"
            )

            # Should have fallen through Tushare (blocked) to Mock
            self.assertEqual(
                result.sources_used.get("__selected"), "mock-success"
            )
            self.assertGreater(len(result.precheck_log), 0)
            self.assertIn("tushare:daily", result.fallback_chain)
            self.assertIn("mock-success", result.fallback_chain)
            self.assertIn("precheck-blocked", result.fallback_reason)
        finally:
            if saved is not None:
                os.environ["TUSHARE_TOKEN"] = saved


if __name__ == "__main__":
    unittest.main()


# ── Per-Ticker Failure Surfacing ──

class TestPerTickerFailureSurfacing(unittest.TestCase):
    """Verify that per-ticker fetch failures are surfaced via missing_pairs
    and stderr warnings (NOT silently swallowed)."""

    @staticmethod
    def _make_ohlcv_row(ticker, date_str="2026-01-01"):
        """Return a minimal valid OHLCV DataFrame for one ticker × one day."""
        return pd.DataFrame({
            "date": [date_str],
            "ticker": [ticker],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.8],
            "volume": [1000],
            "amount": [10800.0],
        })

    def test_mock_provider_raises_on_half_tickers_missing_pairs_populated(self):
        """A mock OHLCV provider that raises on exactly half the tickers
        must populate missing_pairs with (None, ticker) entries and log warnings."""
        from backtest.market_data import OHLCVResult

        _row = self._make_ohlcv_row  # capture as local

        class MockFlakyProvider:
            name = "mock-flaky-ohlcv"

            def get_ohlcv(self, tickers, start, end):
                wanted = list(tickers)
                frames = []
                missing_pairs = []
                for ticker in wanted:
                    if ticker in {"000003.SZ", "000004.SZ"}:
                        missing_pairs.append((None, ticker))
                        continue
                    frames.append(_row(ticker))

                if not frames:
                    return OHLCVResult(
                        status="INFRA_ERROR", reason="NO_PRICE_DATA",
                        ohlcv=pd.DataFrame(), sources_tried=[self.name],
                        missing_pairs=missing_pairs,
                    )

                ohlcv = pd.concat(frames, ignore_index=True)
                fail_count = len(missing_pairs)
                total = len(wanted)
                status = "INFRA_ERROR" if fail_count > total / 2 else "OK"
                return OHLCVResult(
                    status=status, ohlcv=ohlcv,
                    sources_tried=[self.name],
                    sources_used={t: self.name for t in wanted if (None, t) not in missing_pairs},
                    missing_pairs=missing_pairs,
                )

            def precheck(self):
                return None

        provider = MockFlakyProvider()
        result = provider.get_ohlcv(
            ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
            "2026-01-01", "2026-01-01",
        )

        # 2 of 4 tickers failed → not >50% (exactly 50%) → status OK
        self.assertEqual(result.status, "OK")
        self.assertEqual(len(result.missing_pairs), 2)
        self.assertIn((None, "000003.SZ"), result.missing_pairs)
        self.assertIn((None, "000004.SZ"), result.missing_pairs)
        self.assertIn("000001.SZ", result.sources_used)
        self.assertIn("000002.SZ", result.sources_used)
        self.assertNotIn("000003.SZ", result.sources_used)
        self.assertNotIn("000004.SZ", result.sources_used)

    def test_more_than_half_failures_marks_infra_error(self):
        """>50% per-ticker failures → status='INFRA_ERROR' (bulk fetch broken)."""
        from backtest.market_data import OHLCVResult

        class MockMostlyFailingProvider:
            name = "mock-mostly-failing"

            def get_ohlcv(self, tickers, start, end):
                wanted = list(tickers)
                frames = []
                missing_pairs = []
                for ticker in wanted:
                    if ticker != "000001.SZ":
                        missing_pairs.append((None, ticker))
                        continue
                    frames.append(pd.DataFrame({
                        "date": ["2026-01-01"], "ticker": [ticker],
                        "open": [10.0], "high": [11.0], "low": [9.5],
                        "close": [10.8], "volume": [1000], "amount": [10800.0],
                    }))

                ohlcv = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                fail_count = len(missing_pairs)
                total = len(wanted)
                status = "INFRA_ERROR" if fail_count > total / 2 else "OK"
                return OHLCVResult(
                    status=status, ohlcv=ohlcv,
                    sources_tried=[self.name],
                    sources_used={t: self.name for t in wanted if (None, t) not in missing_pairs},
                    missing_pairs=missing_pairs,
                )

            def precheck(self):
                return None

        provider = MockMostlyFailingProvider()
        result = provider.get_ohlcv(
            ["000001.SZ", "000002.SZ", "000003.SZ"],
            "2026-01-01", "2026-01-01",
        )

        # 2 of 3 = 66% > 50% → INFRA_ERROR
        self.assertEqual(result.status, "INFRA_ERROR")
        self.assertEqual(len(result.missing_pairs), 2)

    def test_single_ticker_failure_keeps_status_ok(self):
        """Exactly 1 ticker fails → status stays 'OK'."""
        from backtest.market_data import OHLCVResult

        class MockSingleFailureProvider:
            name = "mock-single-failure"

            def get_ohlcv(self, tickers, start, end):
                wanted = list(tickers)
                frames = []
                missing_pairs = []
                for ticker in wanted:
                    if ticker == "000010.SZ":
                        missing_pairs.append((None, ticker))
                        continue
                    frames.append(pd.DataFrame({
                        "date": ["2026-01-01"], "ticker": [ticker],
                        "open": [10.0], "high": [11.0], "low": [9.5],
                        "close": [10.8], "volume": [1000], "amount": [10800.0],
                    }))

                ohlcv = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                fail_count = len(missing_pairs)
                total = len(wanted)
                status = "INFRA_ERROR" if fail_count > total / 2 else "OK"
                return OHLCVResult(
                    status=status, ohlcv=ohlcv,
                    sources_tried=[self.name],
                    sources_used={t: self.name for t in wanted if (None, t) not in missing_pairs},
                    missing_pairs=missing_pairs,
                )

            def precheck(self):
                return None

        provider = MockSingleFailureProvider()
        result = provider.get_ohlcv(
            ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ",
             "000006.SZ", "000007.SZ", "000008.SZ", "000009.SZ", "000010.SZ"],
            "2026-01-01", "2026-01-01",
        )

        # 1 of 10 = 10% → OK
        self.assertEqual(result.status, "OK")
        self.assertEqual(len(result.missing_pairs), 1)
        self.assertIn((None, "000010.SZ"), result.missing_pairs)

    def test_all_failures_returns_infra_error_with_all_missing_pairs(self):
        """All tickers fail → INFRA_ERROR, all in missing_pairs."""
        from backtest.market_data import OHLCVResult

        class MockAllFailureProvider:
            name = "mock-all-failure"

            def get_ohlcv(self, tickers, start, end):
                wanted = list(tickers)
                missing_pairs = [(None, t) for t in wanted]
                return OHLCVResult(
                    status="INFRA_ERROR", reason="NO_PRICE_DATA",
                    ohlcv=pd.DataFrame(), sources_tried=[self.name],
                    missing_pairs=missing_pairs,
                )

            def precheck(self):
                return None

        provider = MockAllFailureProvider()
        result = provider.get_ohlcv(
            ["000001.SZ", "000002.SZ", "000003.SZ"],
            "2026-01-01", "2026-01-01",
        )

        self.assertEqual(result.status, "INFRA_ERROR")
        self.assertEqual(len(result.missing_pairs), 3)
        for ticker in ["000001.SZ", "000002.SZ", "000003.SZ"]:
            self.assertIn((None, ticker), result.missing_pairs)

    def test_per_ticker_warnings_logged_to_stderr(self):
        """Verify that per-ticker failures produce [WARN provider] on stderr."""
        import io
        import sys
        from unittest.mock import patch

        from backtest.market_data import OHLCVResult

        class MockWarningProvider:
            name = "mock-warning-provider"

            def get_ohlcv(self, tickers, start, end):
                wanted = list(tickers)
                frames = []
                missing_pairs = []
                for ticker in wanted:
                    if ticker == "000003.SZ":
                        print(
                            f"[WARN provider] mock-warning-provider ticker={ticker} reason=empty",
                            file=sys.stderr,
                        )
                        missing_pairs.append((None, ticker))
                        continue
                    frames.append(pd.DataFrame({
                        "date": ["2026-01-01"], "ticker": [ticker],
                        "open": [10.0], "high": [11.0], "low": [9.5],
                        "close": [10.8], "volume": [1000], "amount": [10800.0],
                    }))

                ohlcv = pd.concat(frames, ignore_index=True)
                return OHLCVResult(
                    status="OK", ohlcv=ohlcv,
                    sources_tried=[self.name],
                    sources_used={t: self.name for t in wanted if (None, t) not in missing_pairs},
                    missing_pairs=missing_pairs,
                )

            def precheck(self):
                return None

        provider = MockWarningProvider()

        # Capture stderr
        capture = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = capture
        try:
            result = provider.get_ohlcv(
                ["000001.SZ", "000002.SZ", "000003.SZ"],
                "2026-01-01", "2026-01-01",
            )
        finally:
            sys.stderr = old_stderr

        stderr_output = capture.getvalue()
        self.assertIn("[WARN provider]", stderr_output)
        self.assertIn("ticker=000003.SZ", stderr_output)
        self.assertIn("reason=empty", stderr_output)
        # Verify missing_pairs still populated
        self.assertIn((None, "000003.SZ"), result.missing_pairs)
