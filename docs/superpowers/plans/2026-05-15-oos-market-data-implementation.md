# OOS Market Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build deterministic, multi-source OOS evidence for Hermes strategy audits, including separate current/proposed simulation where v1 can safely model the proposal.

**Architecture:** Add focused backtest modules for market data, OOS window selection, and a close-price strategy simulator. Keep `backtest_engine.py` backward compatible by injecting providers instead of replacing the legacy yfinance path. Coordinator builds `oos_backtest` evidence before `audit_layer.review()` and treats unsupported strategy diffs as explicit infrastructure/simulation failures rather than clean approvals.

**Tech Stack:** Python standard library, `unittest`, existing pandas/numpy dependency path in `backtest/backtest_engine.py`, optional runtime providers for yfinance/AKShare/BaoStock/Tushare.

---

## Critical Scope Notes

`strategies/active.json` contains many prose rules such as `entry.condition` and `exit.take_profit`. The v1 simulator must not pretend to interpret those strings. It may only model explicit numeric fields using close-price data.

Supported v1 numeric diffs:

- `parameters.breakout_lookback`
- `parameters.take_profit_pct`
- `parameters.stop_loss_pct`
- `parameters.trailing_stop_pct`
- `parameters.time_stop_days`
- `parameters.first_take_profit_pct`
- `parameters.first_take_profit_ratio`
- `parameters.trailing_stop_for_remaining`
- `parameters.max_single_position`
- `parameters.min_single_position`
- `parameters.base_position_target`
- `parameters.position_floor`
- `parameters.min_single_batch`
- `parameters.max_single_batch`
- `rules.position_sizing.max_single_position`
- `rules.position_sizing.min_single_position`
- `rules.position_sizing.initial_position`
- `rules.position_sizing.total_position_limit`
- `rules.position_sizing.total_position_floor`

Unsupported v1 diffs:

- Any prose path such as `rules.entry.condition`, `rules.entry.filters`, `rules.exit.take_profit`, `rules.exit.stop_loss`, or `rules.commodity_stock_rules`.
- Fundamental filters without structured historical data: `min_roe`, `min_dividend_yield`, `max_debt_ratio`, `max_pe_percentile`, `min_turnover_billion`.
- Volume/turnover/sector rules until the provider returns normalized OHLCV and sector series: `volume_ratio_threshold`, `turnover_rate_min`, `sector_rise_threshold`, `sector_consecutive_days`.
- MACD/RSI fields in v1 unless the worker explicitly adds close-derived indicator tests in the same task.

Unsupported diffs must return:

```json
{
  "status": "INFRA_ERROR",
  "reason": "UNSUPPORTED_STRATEGY_DIFF",
  "unsupported_paths": ["rules.entry.condition"]
}
```

Historical impact estimate from `strategies/changelog.json`:

- Across 28 changelog entries, about 22 are strategy-ish or review-adjacent.
- About 8 look numeric enough for v1 close-price simulation, roughly 36% of strategy-ish history.
- Among direct `parameter_adjustment` and `strategy_upgrade` entries, about 8 of 13 are numeric-ish and 5 of 13 are prose/structural, so expect roughly 38% unsupported if future proposals resemble the backfilled history.
- Operator guidance: strategy-maintainer proposals intended for auto audit should concentrate on the supported numeric paths listed in this section. Prose rule rewrites should ship with manual review notes and should be expected to block auto-merge in v1.

## File Structure

- Create `backtest/market_data.py`: provider interface, provider result metadata, symbol conversion, static provider, cache provider, fallback provider, optional live provider adapters.
- Create `backtest/oos_window.py`: pure OOS window selection from changelog and trading calendar.
- Create `backtest/strategy_simulator.py`: supported diff detection, in-memory proposal application, close-price simulation, metric calculation, evidence assembly helpers.
- Modify `backtest/backtest_engine.py`: provider injection into `fetch_prices()` and `run_backtest()`.
- Modify `agents/coordinator.py`: build OOS evidence and pass it to `audit_layer.review()` instead of `{}`.
- Modify `tests/audit_layer/test_oos_backtest.py`: remove live-network dependency with `StaticPriceProvider`.
- Create `tests/audit_layer/test_market_data_provider.py`: symbol conversion, fallback metadata, cache freshness behavior.
- Create `tests/audit_layer/test_oos_window.py`: trading-day OOS window selection.
- Create `tests/audit_layer/test_strategy_simulator.py`: supported numeric diffs and unsupported prose diffs.
- Modify `tests/audit_layer/test_coordinator_audit_flow.py`: assert coordinator sends structured current/proposed OOS evidence.

Do not touch record-synthesis files in this worktree. They appear as separate untracked work.

### Task 1: Market Data Provider Core

**Files:**
- Create: `backtest/market_data.py`
- Test: `tests/audit_layer/test_market_data_provider.py`

- [ ] **Step 1: Write failing tests for symbol conversion, static provider, and fallback metadata**

Add this file:

```python
import os
import sys
import unittest
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


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
        prices = pd.DataFrame({"600519.SS": [100.0, 101.0], "000300.SS": [4000.0, 4010.0]}, index=index)
        result = StaticPriceProvider(prices).get_close_prices(["600519.SS", "000300.SS"], "2026-04-20", "2026-04-21")

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
                return ProviderResult(status="INFRA_ERROR", prices=pd.DataFrame(), sources_tried=["empty"], missing_symbols=list(tickers))

        index = pd.to_datetime(["2026-04-20", "2026-04-21"])
        prices = pd.DataFrame({"600519.SS": [100.0, 101.0]}, index=index)
        provider = FallbackMarketDataProvider([EmptyProvider(), StaticPriceProvider(prices)])
        result = provider.get_close_prices(["600519.SS", "000300.SS"], "2026-04-20", "2026-04-21")

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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m unittest tests.audit_layer.test_market_data_provider
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backtest.market_data'`.

- [ ] **Step 3: Implement provider core**

Create `backtest/market_data.py`:

```python
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import pandas as pd


@dataclass
class ProviderResult:
    status: str
    prices: pd.DataFrame
    sources_tried: List[str] = field(default_factory=list)
    sources_used: Dict[str, str] = field(default_factory=dict)
    missing_symbols: List[str] = field(default_factory=list)
    cache_hit_ratio: float = 0.0
    cache_oldest_age_days: Optional[int] = None
    adjustment: str = "qfq"
    reason: Optional[str] = None


@dataclass
class CacheEntryMeta:
    symbol: str
    provider: str
    frequency: str
    adjustment: str
    start: str
    end: str
    data_fetched_at: str


def to_plain_code(ticker: str) -> str:
    return ticker.split(".")[0]


def to_tushare_symbol(ticker: str) -> str:
    code = to_plain_code(ticker)
    if ticker.endswith(".SS"):
        return f"{code}.SH"
    if ticker.endswith(".SZ"):
        return f"{code}.SZ"
    raise ValueError(f"unsupported ticker for tushare: {ticker}")


def to_baostock_symbol(ticker: str) -> str:
    code = to_plain_code(ticker)
    if ticker.endswith(".SS"):
        return f"sh.{code}"
    if ticker.endswith(".SZ"):
        return f"sz.{code}"
    raise ValueError(f"unsupported ticker for baostock: {ticker}")


def cache_key(symbol: str, provider: str, frequency: str = "daily", adjustment: str = "qfq") -> str:
    safe_symbol = symbol.replace(".", "_")
    return f"{provider}_{safe_symbol}_{frequency}_{adjustment}"


def _parse_dt(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_cache_fresh(meta: CacheEntryMeta, today: Optional[datetime] = None) -> bool:
    today = today or datetime.now(timezone.utc)
    fetched_at = _parse_dt(meta.data_fetched_at)
    age_days = (today - fetched_at).days
    if meta.adjustment in ("qfq", "hfq"):
        return age_days <= 5
    return age_days <= 20


class MarketDataProvider:
    name = "base"

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        raise NotImplementedError


class StaticPriceProvider(MarketDataProvider):
    name = "static"

    def __init__(self, prices: pd.DataFrame):
        self._prices = prices.copy()
        self._prices.index = pd.to_datetime(self._prices.index)

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        wanted = list(tickers)
        frame = self._prices.loc[(self._prices.index >= pd.Timestamp(start)) & (self._prices.index <= pd.Timestamp(end))]
        available = [t for t in wanted if t in frame.columns and not frame[t].dropna().empty]
        missing = [t for t in wanted if t not in available]
        out = frame[available].copy() if available else pd.DataFrame(index=frame.index)
        return ProviderResult(
            status="OK" if not missing and not out.empty else "INFRA_ERROR",
            reason=None if not missing and not out.empty else "NO_PRICE_DATA",
            prices=out,
            sources_tried=[self.name],
            sources_used={t: self.name for t in available},
            missing_symbols=missing,
        )


class CachedPriceProvider(MarketDataProvider):
    name = "cache"

    def __init__(self, cache_dir: str, adjustment: str = "qfq", today: Optional[datetime] = None):
        self.cache_dir = cache_dir
        self.adjustment = adjustment
        self.today = today

    def _paths(self, symbol: str):
        key = cache_key(symbol, self.name, "daily", self.adjustment)
        return os.path.join(self.cache_dir, f"{key}.csv"), os.path.join(self.cache_dir, f"{key}.meta.json")

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        frames = []
        sources_used = {}
        missing = []
        ages = []
        for symbol in tickers:
            csv_path, meta_path = self._paths(symbol)
            if not os.path.exists(csv_path) or not os.path.exists(meta_path):
                missing.append(symbol)
                continue
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = CacheEntryMeta(**json.load(f))
            fetched_at = _parse_dt(meta.data_fetched_at)
            today = self.today or datetime.now(timezone.utc)
            ages.append((today - fetched_at).days)
            if not is_cache_fresh(meta, today):
                missing.append(symbol)
                continue
            series = pd.read_csv(csv_path, parse_dates=["date"]).set_index("date")["close"].rename(symbol)
            window = series.loc[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]
            if window.empty:
                missing.append(symbol)
                continue
            frames.append(window)
            sources_used[symbol] = self.name
        prices = pd.concat(frames, axis=1) if frames else pd.DataFrame()
        total = len(list(tickers))
        hit_ratio = len(sources_used) / total if total else 0.0
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices,
            sources_tried=[self.name],
            sources_used=sources_used,
            missing_symbols=missing,
            cache_hit_ratio=hit_ratio,
            cache_oldest_age_days=max(ages) if ages else None,
            adjustment=self.adjustment,
        )


class FallbackMarketDataProvider(MarketDataProvider):
    name = "fallback"

    def __init__(self, providers: List[MarketDataProvider]):
        self.providers = providers

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        wanted = list(tickers)
        remaining = list(wanted)
        combined = []
        sources_tried = []
        sources_used = {}
        cache_hits = []
        cache_ages = []
        adjustment = "qfq"

        for provider in self.providers:
            if not remaining:
                break
            result = provider.get_close_prices(remaining, start, end)
            sources_tried.extend(result.sources_tried or [provider.name])
            adjustment = result.adjustment or adjustment
            if result.cache_hit_ratio:
                cache_hits.append(result.cache_hit_ratio)
            if result.cache_oldest_age_days is not None:
                cache_ages.append(result.cache_oldest_age_days)
            if not result.prices.empty:
                combined.append(result.prices)
                sources_used.update(result.sources_used)
            remaining = [symbol for symbol in wanted if symbol not in sources_used]

        prices = pd.concat(combined, axis=1) if combined else pd.DataFrame()
        if not prices.empty:
            prices = prices.loc[:, [c for c in wanted if c in prices.columns]]
        missing = [symbol for symbol in wanted if symbol not in sources_used]
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices,
            sources_tried=list(dict.fromkeys(sources_tried)),
            sources_used=sources_used,
            missing_symbols=missing,
            cache_hit_ratio=max(cache_hits) if cache_hits else 0.0,
            cache_oldest_age_days=max(cache_ages) if cache_ages else None,
            adjustment=adjustment,
        )


def _optional_import(module_name: str):
    try:
        return __import__(module_name)
    except ImportError:
        return None


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def get_close_prices(self, tickers: Iterable[str], start: str, end: str) -> ProviderResult:
        yf = _optional_import("yfinance")
        if yf is None:
            return ProviderResult(status="INFRA_ERROR", reason="PROVIDER_UNAVAILABLE", prices=pd.DataFrame(), sources_tried=[self.name], missing_symbols=list(tickers))
        data = yf.download(list(tickers), start=start, end=end, progress=False)
        prices = data["Close"] if "Close" in data else pd.DataFrame()
        if isinstance(prices, pd.Series):
            prices = prices.to_frame(list(tickers)[0])
        available = [t for t in tickers if t in prices.columns and not prices[t].dropna().empty]
        missing = [t for t in tickers if t not in available]
        return ProviderResult(
            status="OK" if not missing and not prices.empty else "INFRA_ERROR",
            reason=None if not missing and not prices.empty else "NO_PRICE_DATA",
            prices=prices[available] if available else pd.DataFrame(),
            sources_tried=[self.name],
            sources_used={t: self.name for t in available},
            missing_symbols=missing,
            adjustment="qfq",
        )
```

- [ ] **Step 4: Run provider tests**

Run:

```bash
python3 -m unittest tests.audit_layer.test_market_data_provider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/market_data.py tests/audit_layer/test_market_data_provider.py
git commit -m "feat: add market data provider core"
```

### Task 2: Backtest Provider Injection

**Files:**
- Modify: `backtest/backtest_engine.py`
- Modify: `tests/audit_layer/test_oos_backtest.py`

- [ ] **Step 1: Rewrite OOS tests to use `StaticPriceProvider`**

Replace `tests/audit_layer/test_oos_backtest.py` with:

```python
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


def static_provider():
    from backtest.market_data import StaticPriceProvider

    idx = pd.to_datetime([
        "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17",
        "2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23",
        "2026-04-24", "2026-04-27", "2026-04-28", "2026-04-29",
        "2026-04-30", "2026-05-01",
    ])
    return StaticPriceProvider(pd.DataFrame({
        "601088.SS": [30, 30, 30, 30, 30, 30.5, 31, 31, 31, 31, 31, 31, 31, 31],
        "600519.SS": [1800, 1800, 1800, 1800, 1801, 1802, 1803, 1804, 1805, 1806, 1807, 1808, 1809, 1810],
        "000300.SS": [4000, 4010, 4020, 4030, 4040, 4050, 4060, 4070, 4080, 4090, 4100, 4110, 4120, 4130],
    }, index=idx))


class TestOOSBacktest(unittest.TestCase):
    def test_oos_window_filters_trades(self):
        from backtest.backtest_engine import run_backtest

        trades_by_date = {
            "2026-04-15": [{"account": "main", "code": "601088", "action": "buy", "shares": 100, "price": 30.0}],
            "2026-04-22": [{"account": "main", "code": "600519", "action": "buy", "shares": 50, "price": 1800.0}],
            "2026-05-01": [{"account": "main", "code": "601088", "action": "sell", "shares": 100, "price": 31.0}],
        }
        df, accounts, prices = run_backtest(
            trades_by_date,
            account_filter="all",
            oos_start="2026-04-20",
            oos_end="2026-04-30",
            price_provider=static_provider(),
        )
        main_positions = accounts["main"].positions
        self.assertIn("600519", main_positions)
        self.assertNotIn("601088", main_positions)
        self.assertGreater(len(df), 0)

    def test_backwards_compat_no_oos_with_injected_provider(self):
        from backtest.backtest_engine import run_backtest

        trades_by_date = {
            "2026-04-15": [{"account": "main", "code": "601088", "action": "buy", "shares": 100, "price": 30.0}],
        }
        df, accounts, prices = run_backtest(trades_by_date, account_filter="all", price_provider=static_provider())
        self.assertIn("601088", accounts["main"].positions)
        self.assertGreater(len(df), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m unittest tests.audit_layer.test_oos_backtest
```

Expected: FAIL with `TypeError: run_backtest() got an unexpected keyword argument 'price_provider'`.

- [ ] **Step 3: Add provider injection to backtest engine**

In `backtest/backtest_engine.py`, change `fetch_prices()` and `run_backtest()` signatures and body:

```python
def fetch_prices(tickers, start, end, provider=None):
    """Download historical close prices via provider or legacy yfinance."""
    print(f"  Fetching {len(tickers)} tickers from {start} to {end}...")
    if provider is not None:
        result = provider.get_close_prices(tickers, start, end)
        if result.status != "OK":
            raise RuntimeError(
                f"price provider failed: status={result.status} "
                f"reason={result.reason} missing={result.missing_symbols}"
            )
        prices = result.prices
    else:
        data = yf.download(tickers, start=start, end=end, progress=False)
        prices = data["Close"]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame(tickers[0])
    print(f"  Got {len(prices)} trading days, {prices.shape[1]} tickers")
    return prices
```

Change:

```python
def run_backtest(trades_by_date, account_filter="all", oos_start=None, oos_end=None):
```

to:

```python
def run_backtest(trades_by_date, account_filter="all", oos_start=None, oos_end=None, price_provider=None):
```

Change:

```python
prices = fetch_prices(tickers, start, end)
```

to:

```python
prices = fetch_prices(tickers, start, end, provider=price_provider)
```

- [ ] **Step 4: Run OOS tests**

Run:

```bash
python3 -m unittest tests.audit_layer.test_oos_backtest
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/backtest_engine.py tests/audit_layer/test_oos_backtest.py
git commit -m "feat: inject market data provider into backtest"
```

### Task 3: OOS Window Selection

**Files:**
- Create: `backtest/oos_window.py`
- Test: `tests/audit_layer/test_oos_window.py`

- [ ] **Step 1: Write failing tests for trading-day windows**

Add `tests/audit_layer/test_oos_window.py`:

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


class TestOOSWindow(unittest.TestCase):
    def test_uses_last_strategy_change_and_counts_trading_days(self):
        from backtest.oos_window import compute_oos_window

        changelog = [
            {"date": "2026-04-12", "account": "main", "change_type": "init"},
            {"date": "2026-04-22", "account": "main", "change_type": "parameter_adjustment"},
            {"date": "2026-04-24", "account": "lab", "change_type": "strategy_upgrade"},
        ]
        calendar = [f"2026-04-{d:02d}" for d in range(20, 31)] + [f"2026-05-{d:02d}" for d in range(1, 32)]
        calendar = [d for d in calendar if d not in {"2026-04-25", "2026-04-26", "2026-05-02", "2026-05-03"}]

        result = compute_oos_window(changelog, "main", "2026-05-31", calendar)

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["start"], "2026-04-23")
        self.assertEqual(result["trading_days"], 30)
        self.assertEqual(result["basis_entry_date"], "2026-04-22")

    def test_rejects_less_than_twenty_usable_days(self):
        from backtest.oos_window import compute_oos_window

        changelog = [{"date": "2026-04-22", "account": "main", "change_type": "parameter_adjustment"}]
        calendar = ["2026-04-23", "2026-04-24", "2026-04-27"]

        result = compute_oos_window(changelog, "main", "2026-04-30", calendar)

        self.assertEqual(result["status"], "INFRA_ERROR")
        self.assertEqual(result["reason"], "INSUFFICIENT_OOS_DAYS")
        self.assertEqual(result["trading_days"], 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m unittest tests.audit_layer.test_oos_window
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backtest.oos_window'`.

- [ ] **Step 3: Implement `compute_oos_window()`**

Create `backtest/oos_window.py`:

```python
STRATEGY_CHANGE_TYPES = {"strategy", "parameter_adjustment", "strategy_upgrade", "param", "init"}


def _entry_account(entry):
    return entry.get("account") or entry.get("strategy")


def _is_strategy_entry(entry):
    change_type = entry.get("change_type")
    if change_type in STRATEGY_CHANGE_TYPES:
        return True
    if "change" in entry:
        return True
    return False


def compute_oos_window(changelog, account, today, trading_calendar):
    account_entries = [
        entry for entry in changelog
        if _entry_account(entry) in {account, "both"} and entry.get("date")
    ]
    if not account_entries:
        return {"status": "INFRA_ERROR", "reason": "NO_CHANGELOG_BASIS", "trading_days": 0}

    earliest = min(entry["date"] for entry in account_entries)
    strategy_entries = [entry for entry in account_entries if _is_strategy_entry(entry)]
    basis = max((entry["date"] for entry in strategy_entries), default=earliest)

    usable = [
        day for day in sorted(trading_calendar)
        if day > basis and day <= today and day >= earliest
    ]
    if len(usable) < 20:
        return {
            "status": "INFRA_ERROR",
            "reason": "INSUFFICIENT_OOS_DAYS",
            "start": usable[0] if usable else None,
            "end": usable[-1] if usable else None,
            "trading_days": len(usable),
            "basis_entry_date": basis,
        }
    window = usable[:30]
    return {
        "status": "OK",
        "start": window[0],
        "end": window[-1],
        "trading_days": len(window),
        "basis_entry_date": basis,
    }
```

- [ ] **Step 4: Run OOS window tests**

Run:

```bash
python3 -m unittest tests.audit_layer.test_oos_window
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/oos_window.py tests/audit_layer/test_oos_window.py
git commit -m "feat: compute oos audit windows"
```

### Task 4: Strategy Simulator v1

**Files:**
- Create: `backtest/strategy_simulator.py`
- Test: `tests/audit_layer/test_strategy_simulator.py`

- [ ] **Step 1: Write failing tests for numeric and unsupported diffs**

Add `tests/audit_layer/test_strategy_simulator.py`:

```python
import copy
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.expanduser("~/.hermes/virtual-trader"))


def prices():
    idx = pd.to_datetime([f"2026-04-{d:02d}" for d in range(1, 31) if d not in (4, 5, 11, 12, 18, 19, 25, 26)])
    values = list(range(100, 100 + len(idx)))
    return pd.DataFrame({
        "600519.SS": values,
        "000858.SZ": [100 + i * 0.5 for i in range(len(idx))],
        "000300.SS": [4000 + i * 5 for i in range(len(idx))],
    }, index=idx)


def strategy():
    return {
        "version": "1.0.0",
        "parameters": {
            "breakout_lookback": 3,
            "take_profit_pct": 15,
            "stop_loss_pct": 7,
            "max_single_position": 0.10,
            "min_single_position": 0.04,
            "base_position_target": 0.65,
            "time_stop_days": 7,
        },
        "rules": {
            "position_sizing": {
                "initial_position": 0.10,
                "max_single_position": 0.10,
                "total_position_limit": 0.8,
            }
        }
    }


class TestStrategySimulator(unittest.TestCase):
    def test_supported_numeric_diff_changes_metrics(self):
        from backtest.strategy_simulator import build_oos_evidence

        current = strategy()
        proposal = {
            "account": "main",
            "diff": [
                {"path": "main_strategy.parameters.take_profit_pct", "old": 15, "new": 3}
            ],
        }
        watchlist = {"stocks": [
            {"code": "600519", "name": "贵州茅台", "tag": "main"},
            {"code": "000858", "name": "五粮液", "tag": "main"},
        ]}
        window = {"status": "OK", "start": "2026-04-01", "end": "2026-04-30", "trading_days": len(prices())}
        evidence = build_oos_evidence(current, proposal, watchlist, prices(), window)

        self.assertEqual(evidence["status"], "OK")
        self.assertIn("current", evidence)
        self.assertIn("proposed", evidence)
        self.assertNotEqual(evidence["current"]["total_ret"], evidence["proposed"]["total_ret"])

    def test_unsupported_prose_diff_returns_infra_error(self):
        from backtest.strategy_simulator import build_oos_evidence

        proposal = {
            "account": "main",
            "diff": [
                {"path": "main_strategy.rules.entry.condition", "old": "MA20", "new": "MA20 + commodity"}
            ],
        }
        evidence = build_oos_evidence(strategy(), proposal, {"stocks": []}, prices(), {"status": "OK", "start": "2026-04-01", "end": "2026-04-30", "trading_days": 20})

        self.assertEqual(evidence["status"], "INFRA_ERROR")
        self.assertEqual(evidence["reason"], "UNSUPPORTED_STRATEGY_DIFF")
        self.assertEqual(evidence["unsupported_paths"], ["main_strategy.rules.entry.condition"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m unittest tests.audit_layer.test_strategy_simulator
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backtest.strategy_simulator'`.

- [ ] **Step 3: Implement simulator**

Create `backtest/strategy_simulator.py`:

```python
from __future__ import annotations

import copy
import math
from typing import Dict, List

import numpy as np
import pandas as pd


SUPPORTED_PARAMETER_FIELDS = {
    "breakout_lookback",
    "take_profit_pct",
    "stop_loss_pct",
    "trailing_stop_pct",
    "time_stop_days",
    "first_take_profit_pct",
    "first_take_profit_ratio",
    "trailing_stop_for_remaining",
    "max_single_position",
    "min_single_position",
    "base_position_target",
    "position_floor",
    "min_single_batch",
    "max_single_batch",
}

SUPPORTED_RULE_FIELDS = {
    "max_single_position",
    "min_single_position",
    "initial_position",
    "total_position_limit",
    "total_position_floor",
}


def _field_name(path: str) -> str:
    return path.split(".")[-1]


def unsupported_diff_paths(diff: List[Dict]) -> List[str]:
    unsupported = []
    for item in diff:
        path = item.get("path", "")
        field = _field_name(path)
        if ".parameters." in path and field in SUPPORTED_PARAMETER_FIELDS and isinstance(item.get("new"), (int, float)):
            continue
        if ".rules.position_sizing." in path and field in SUPPORTED_RULE_FIELDS and isinstance(item.get("new"), (int, float)):
            continue
        unsupported.append(path)
    return unsupported


def apply_supported_diff(strategy: Dict, diff: List[Dict]) -> Dict:
    updated = copy.deepcopy(strategy)
    updated.setdefault("parameters", {})
    updated.setdefault("rules", {}).setdefault("position_sizing", {})
    for item in diff:
        path = item["path"]
        field = _field_name(path)
        if ".parameters." in path:
            updated["parameters"][field] = item["new"]
        elif ".rules.position_sizing." in path:
            updated["rules"]["position_sizing"][field] = item["new"]
    return updated


def code_to_ticker(code: str) -> str:
    if code.startswith(("6", "5")):
        return f"{code}.SS"
    return f"{code}.SZ"


def _watchlist_codes(watchlist: Dict, account: str) -> List[str]:
    rows = watchlist.get("stocks", [])
    tag = "main" if account == "main" else "lab"
    codes = [row["code"] for row in rows if row.get("tag") in {tag, account} and row.get("status") not in {"stopped_out"}]
    return codes[:8]


def _metrics(equity: pd.Series) -> Dict:
    returns = equity.pct_change().dropna()
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) else 0.0
    sharpe = float(returns.mean() / returns.std() * np.sqrt(252)) if len(returns) > 1 and returns.std() > 0 else 0.0
    peak = equity.expanding().max()
    max_dd = float(((equity - peak) / peak).min()) if len(equity) else 0.0
    return {"total_ret": total_ret, "sharpe": sharpe, "max_dd": max_dd}


def simulate_strategy(strategy: Dict, watchlist: Dict, prices: pd.DataFrame, account: str) -> Dict:
    params = strategy.get("parameters", {})
    sizing = strategy.get("rules", {}).get("position_sizing", {})
    capital = 1_000_000.0 if account == "main" else 300_000.0
    cash = capital
    positions = {}
    entry_prices = {}
    entry_days = {}
    equity = []
    breakout = int(params.get("breakout_lookback", 20))
    take_profit = float(params.get("take_profit_pct", 15)) / 100
    stop_loss = float(params.get("stop_loss_pct", 7)) / 100
    time_stop = int(params.get("time_stop_days", 20))
    initial_position = float(sizing.get("initial_position", params.get("max_single_position", 0.10)))
    max_single = float(sizing.get("max_single_position", params.get("max_single_position", initial_position)))
    target_weight = min(initial_position, max_single)
    codes = _watchlist_codes(watchlist, account)
    tickers = [code_to_ticker(code) for code in codes if code_to_ticker(code) in prices.columns]

    for i, (dt, row) in enumerate(prices.iterrows()):
        for ticker in list(positions):
            px = row.get(ticker)
            if pd.isna(px):
                continue
            ret = px / entry_prices[ticker] - 1
            held = i - entry_days[ticker]
            if ret >= take_profit or ret <= -stop_loss or held >= time_stop:
                cash += positions.pop(ticker) * px
                entry_prices.pop(ticker, None)
                entry_days.pop(ticker, None)

        if i >= breakout:
            for ticker in tickers:
                if ticker in positions:
                    continue
                px = row.get(ticker)
                if pd.isna(px):
                    continue
                recent_high = prices[ticker].iloc[i - breakout:i].max()
                if px > recent_high:
                    budget = capital * target_weight
                    if cash >= budget and budget > 0:
                        shares = math.floor(budget / px / 100) * 100
                        if shares > 0:
                            positions[ticker] = shares
                            entry_prices[ticker] = px
                            entry_days[ticker] = i
                            cash -= shares * px

        total = cash
        for ticker, shares in positions.items():
            px = row.get(ticker)
            total += shares * (px if not pd.isna(px) else entry_prices[ticker])
        equity.append(total)

    equity_series = pd.Series(equity, index=prices.index)
    return {"equity": equity_series, "metrics": _metrics(equity_series)}


def build_oos_evidence(current_strategy: Dict, proposal: Dict, watchlist: Dict, prices: pd.DataFrame, window: Dict, data_meta: Dict | None = None) -> Dict:
    if window.get("status") != "OK":
        return {"status": "INFRA_ERROR", "reason": window.get("reason", "BAD_OOS_WINDOW"), "window": window}
    diff = proposal.get("diff", [])
    unsupported = unsupported_diff_paths(diff)
    if unsupported:
        return {"status": "INFRA_ERROR", "reason": "UNSUPPORTED_STRATEGY_DIFF", "unsupported_paths": unsupported}
    account = proposal.get("account", "main")
    proposed_strategy = apply_supported_diff(current_strategy, diff)
    current = simulate_strategy(current_strategy, watchlist, prices, account)
    proposed = simulate_strategy(proposed_strategy, watchlist, prices, account)
    return {
        "status": "OK",
        "window": {"start": window["start"], "end": window["end"], "trading_days": window["trading_days"]},
        "current": current["metrics"],
        "proposed": proposed["metrics"],
        "data": data_meta or {},
    }
```

- [ ] **Step 4: Run simulator tests**

Run:

```bash
python3 -m unittest tests.audit_layer.test_strategy_simulator
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/strategy_simulator.py tests/audit_layer/test_strategy_simulator.py
git commit -m "feat: add close price strategy simulator"
```

### Task 5: Coordinator OOS Evidence Integration

**Files:**
- Modify: `agents/coordinator.py`
- Modify: `tests/audit_layer/test_coordinator_audit_flow.py`

- [ ] **Step 1: Extend coordinator audit-flow test**

In `tests/audit_layer/test_coordinator_audit_flow.py`, add an assertion after the mocked `audit_layer.review()` call:

```python
review_kwargs = mock_review.call_args.kwargs
oos = review_kwargs["oos_backtest"]
self.assertIn(oos["status"], {"OK", "INFRA_ERROR"})
self.assertNotEqual(oos, {})
if oos["status"] == "OK":
    self.assertIn("current", oos)
    self.assertIn("proposed", oos)
    self.assertIn("window", oos)
```

If the existing test uses positional args, normalize it with:

```python
review_kwargs = mock_review.call_args.kwargs
```

because `coordinator.py` already calls `audit_layer.review()` with keyword arguments.

- [ ] **Step 2: Run coordinator test to verify failure**

Run:

```bash
python3 -m unittest tests.audit_layer.test_coordinator_audit_flow
```

Expected: FAIL because `oos_backtest` is still `{}`.

- [ ] **Step 3: Add coordinator helper methods**

In `agents/coordinator.py`, add helpers near `_audit_strategy_adjustments()`:

```python
    def _build_oos_backtest_evidence(self, maintainer, proposal, review_report):
        try:
            from backtest.market_data import FallbackMarketDataProvider, YFinanceProvider
            from backtest.oos_window import compute_oos_window
            from backtest.strategy_simulator import build_oos_evidence, code_to_ticker
        except Exception as exc:
            return {"status": "INFRA_ERROR", "reason": "OOS_IMPORT_FAILED", "error": str(exc)}

        changelog = getattr(maintainer, "changelog", [])
        account = proposal.get("account", "main")
        calendar = self._derive_trading_calendar()
        window = compute_oos_window(changelog, account, datetime.now().strftime("%Y-%m-%d"), calendar)
        if window.get("status") != "OK":
            return window

        watchlist = self._read_json(os.path.join(self.data_dir, "market-data", "watchlist.json"), {"stocks": []})
        tickers = [
            code_to_ticker(row["code"])
            for row in watchlist.get("stocks", [])
            if row.get("code") and row.get("tag") in {account, "main" if account == "main" else "lab"}
        ]
        tickers.append("000300.SS")
        provider = FallbackMarketDataProvider([YFinanceProvider()])
        price_result = provider.get_close_prices(sorted(set(tickers)), window["start"], window["end"])
        if price_result.status != "OK":
            return {
                "status": "INFRA_ERROR",
                "reason": price_result.reason or "NO_PRICE_DATA",
                "sources_tried": price_result.sources_tried,
                "missing_symbols": price_result.missing_symbols,
            }

        strategy_key = f"{account}_strategy"
        current_strategy = maintainer.strategies.get(strategy_key)
        if not current_strategy:
            return {"status": "INFRA_ERROR", "reason": "STRATEGY_NOT_FOUND", "strategy_key": strategy_key}

        return build_oos_evidence(
            current_strategy,
            proposal,
            watchlist,
            price_result.prices,
            window,
            data_meta={
                "sources_tried": price_result.sources_tried,
                "sources_used": price_result.sources_used,
                "missing_symbols": price_result.missing_symbols,
                "cache_hit_ratio": price_result.cache_hit_ratio,
                "cache_oldest_age_days": price_result.cache_oldest_age_days,
                "adjustment": price_result.adjustment,
            },
        )

    def _read_json(self, path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default

    def _derive_trading_calendar(self):
        days = set()
        trades_root = os.path.join(self.data_dir, "trades")
        for root, _, files in os.walk(trades_root):
            for name in files:
                if name.endswith(".json"):
                    days.add(name[:-5])
        if not days:
            today = datetime.now()
            return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(90, -1, -1)]
        ordered = sorted(days)
        start = datetime.strptime(ordered[0], "%Y-%m-%d")
        end = datetime.now()
        calendar = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                calendar.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return calendar
```

Ensure the top import includes `timedelta`:

```python
from datetime import datetime, timedelta
```

- [ ] **Step 4: Pass evidence into audit review**

In `_audit_strategy_adjustments()`, replace:

```python
            oos_backtest={},
```

with:

```python
            oos_backtest=self._build_oos_backtest_evidence(maintainer, proposal, review_report),
```

- [ ] **Step 5: Run coordinator test**

Run:

```bash
python3 -m unittest tests.audit_layer.test_coordinator_audit_flow
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agents/coordinator.py tests/audit_layer/test_coordinator_audit_flow.py
git commit -m "feat: pass oos evidence into audit review"
```

### Task 6: Full Audit Test Verification

**Files:**
- No intended source edits unless tests expose an integration bug.

- [ ] **Step 1: Run focused audit tests**

Run:

```bash
python3 -m unittest \
  tests.audit_layer.test_market_data_provider \
  tests.audit_layer.test_oos_backtest \
  tests.audit_layer.test_oos_window \
  tests.audit_layer.test_strategy_simulator \
  tests.audit_layer.test_coordinator_audit_flow \
  tests.audit_layer.test_review_integration \
  tests.audit_layer.test_overfitting_auditor
```

Expected: PASS.

- [ ] **Step 2: Run existing audit suite without live OOS network dependency**

Run:

```bash
python3 -m unittest \
  tests.audit_layer.test_coordinator_audit_flow \
  tests.audit_layer.test_quorum \
  tests.audit_layer.test_overfitting_auditor \
  tests.audit_layer.test_no_bypass \
  tests.audit_layer.test_audit_subagent \
  tests.audit_layer.test_risk_auditor \
  tests.audit_layer.test_review_integration \
  tests.audit_layer.test_audit_backfill \
  tests.audit_layer.test_strategy_maintainer_split \
  tests.audit_layer.test_cost_execution_auditor \
  tests.audit_layer.test_audit_log \
  tests.audit_layer.test_oos_backtest \
  tests.audit_layer.test_market_data_provider \
  tests.audit_layer.test_oos_window \
  tests.audit_layer.test_strategy_simulator
```

Expected: PASS.

- [ ] **Step 3: Compile touched Python files**

Run:

```bash
python3 -m py_compile \
  backtest/market_data.py \
  backtest/oos_window.py \
  backtest/strategy_simulator.py \
  backtest/backtest_engine.py \
  agents/coordinator.py
```

Expected: exits 0.

- [ ] **Step 4: Inspect worktree status**

Run:

```bash
git status --short
```

Expected: only intentionally changed source/test files from this plan, plus pre-existing unrelated untracked files that are not staged.

## Plan Self-Review

Spec coverage:

- Multi-source provider layer: Task 1.
- Cache adjustment/freshness metadata: Task 1.
- Backtest provider injection: Task 2.
- OOS window owner: Task 3.
- Separate current/proposed strategy simulation: Task 4.
- Unsupported prose diff safety behavior: Task 4.
- Coordinator audit evidence plumbing: Task 5.
- Verification commands: Task 6.

Known v1 limitation:

- The simulator uses close prices only. Numeric rules requiring fundamentals, volume, turnover, sector state, commodity prices, or prose interpretation are intentionally unsupported and return `INFRA_ERROR`.
