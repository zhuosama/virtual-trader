# OOS Market Data Provider Design

Date: 2026-05-15
Branch: `codex-audit-coordinator-workflow`

## Purpose

Hermes Virtual Trader now routes strategy changes through `audit_layer.review()` before `commit_approved()` may write `strategies/active.json` and `strategies/changelog.json`. The remaining gap is that coordinator-level review currently passes an empty `oos_backtest` object, so the audit layer can enforce process but cannot yet evaluate real out-of-sample evidence.

This design adds a small market-data provider layer for OOS backtests. The goal is to make strategy proposals testable before audit, while keeping the existing backtest engine behavior compatible and deterministic in tests.

## Current State

`backtest/backtest_engine.py` fetches prices directly through `yfinance` in `fetch_prices()`. This is fragile for A-share symbols and made the existing OOS tests fail when Yahoo returned zero usable trading days for symbols such as `601088.SS`, `600519.SS`, and `000300.SS`.

The repository README says the system should use Tencent Finance, Sina Finance, and yfinance fallback, but the backtest code does not implement that provider chain. Local reference notes also mention AKShare as an available A-share data option.

## External Patterns

The design follows these patterns from existing projects:

- AKShare provides Python interfaces for A-share historical data such as `stock_zh_a_hist`, commonly backed by public Chinese finance data sources.
- Tushare provides more structured market data APIs and pro-bar/history endpoints, but requires an account token and may have points or rate constraints.
- BaoStock offers free China stock market historical K-line access via `query_history_k_data_plus`.
- Microsoft Qlib separates data preparation/storage from strategy and exchange simulation, and recommends preparing higher-quality data instead of relying blindly on Yahoo-collected data.
- Backtrader keeps strategies separate from data feed implementations, including CSV, Yahoo, and Pandas data feed options.

Sources:

- https://github.com/akfamily/akshare
- https://www.tushare.pro/document/1?doc_id=109
- https://www.tushare.pro/document/1?doc_id=230
- https://pypi.org/project/baostock/
- https://github.com/microsoft/qlib/blob/main/README.md
- https://www.backtrader.com/docu/datafeed/

## Proposed Architecture

Add `backtest/market_data.py` with a narrow provider interface:

```python
class MarketDataProvider:
    name = "provider-name"

    def get_close_prices(self, tickers, start, end):
        """Return a pandas DataFrame indexed by date with one close column per yfinance-style ticker."""
```

The returned DataFrame will use the current engine's ticker names, such as `600519.SS`, `000858.SZ`, and `000300.SS`, so the simulation code does not need to understand each provider's symbol format.

Provider implementations:

- `StaticPriceProvider`: deterministic in-memory provider for unit tests.
- `CachedPriceProvider`: reads and writes normalized daily close CSVs under a local cache directory.
- `AkshareProvider`: optional runtime provider for A-share daily history.
- `BaoStockProvider`: optional runtime provider for A-share daily history.
- `TushareProvider`: optional runtime provider used only when a token is configured.
- `YFinanceProvider`: legacy fallback provider, preserving current behavior.
- `FallbackMarketDataProvider`: tries providers in order, combines results, and records source metadata.

The default provider chain will be:

1. Local cache.
2. Tushare, if `TUSHARE_TOKEN` or Hermes config provides a token.
3. AKShare, if installed.
4. BaoStock, if installed.
5. yfinance.

This order favors reproducibility, then configured quality, then free A-share public sources, then the existing fallback.

## Symbol Normalization

The provider layer will centralize symbol conversion:

- Engine format: `600519.SS`, `000858.SZ`, `000300.SS`.
- Plain A-share code: `600519`, `000858`, `000300`.
- Tushare format: `600519.SH`, `000858.SZ`.
- BaoStock format: `sh.600519`, `sz.000858`.
- AKShare stock history: plain code such as `600519`; index handling uses the relevant AKShare index endpoint or a provider-specific mapping.

If a symbol cannot be mapped, the provider reports it as missing instead of silently returning an empty price table.

## OOS Audit Flow

Coordinator strategy adjustment flow becomes:

1. `strategy_maintainer.generate_strategy_adjustments()` creates candidate adjustments.
2. `strategy_maintainer.propose()` persists a proposal with no live strategy write.
3. Coordinator runs OOS backtest evidence generation before audit.
4. Coordinator passes the evidence into `audit_layer.review(oos_backtest=...)`.
5. Only `AUTO_MERGE` review decisions may call `commit_approved()`.

The OOS evidence object should include:

```json
{
  "status": "OK",
  "window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "current": {"sharpe": 0.5, "max_dd": -0.02, "total_ret": 0.03},
  "proposed": {"sharpe": 0.6, "max_dd": -0.018, "total_ret": 0.04},
  "data": {
    "sources_tried": ["cache", "tushare", "akshare"],
    "sources_used": {"600519.SS": "akshare", "000300.SS": "cache"},
    "missing_symbols": [],
    "cache_hit_ratio": 0.4,
    "adjustment": "qfq"
  }
}
```

If data is unavailable, the evidence object should be explicit:

```json
{
  "status": "INFRA_ERROR",
  "reason": "NO_PRICE_DATA",
  "sources_tried": ["cache", "akshare", "baostock", "yfinance"],
  "missing_symbols": ["600519.SS"]
}
```

Infrastructure/data failures must not become `AUTO_MERGE`.

## Backtest Engine Changes

Keep the current engine as the behavioral core. Make only narrow edits:

- `fetch_prices(tickers, start, end, provider=None)` uses the injected provider when present.
- `run_backtest(..., price_provider=None)` passes the provider through.
- Existing CLI behavior still works without provider configuration.
- Tests use `StaticPriceProvider` and never call live data services.

The first implementation does not need to simulate a fully separate proposed strategy engine. It should produce reliable price-backed OOS evidence for the existing trade replay path, then leave deeper current-vs-proposed strategy simulation as a follow-up if the proposal format needs it.

## Error Handling

- Missing optional packages do not fail import; the provider is marked unavailable.
- Network/API failures are captured per provider and surfaced in metadata.
- Empty price frames are treated as data failures, not valid zero-day backtests.
- Partial symbol coverage is allowed only if benchmark and all traded symbols needed for the selected window are present.
- The audit layer receives structured failure evidence so reviewers can distinguish strategy weakness from infrastructure failure.

## Testing

Unit tests:

- OOS window tests use `StaticPriceProvider` and pass offline.
- `FallbackMarketDataProvider` tries providers in order and records source metadata.
- Empty provider responses become `INFRA_ERROR`.
- Symbol conversion covers Shanghai, Shenzhen, ChiNext, STAR, and HS300 benchmark mappings.
- Backward compatibility: `run_backtest()` without provider still uses the legacy yfinance path.

Integration tests:

- Coordinator audit-flow test asserts `audit_layer.review()` receives non-empty `oos_backtest`.
- Live provider smoke tests are optional and skipped unless dependencies and tokens are configured.

## Non-Goals

- Do not rewrite the backtest engine.
- Do not install or require every market-data dependency by default.
- Do not expose private proposal IDs, reviewer traces, or raw trading data on the public site.
- Do not auto-merge when data collection fails.

## Success Criteria

- OOS tests no longer depend on live Yahoo/A-share network responses.
- Coordinator passes structured OOS evidence into `audit_layer.review()`.
- Existing CLI backtest usage remains compatible.
- The data source used for each symbol is recorded and inspectable.
- Data failure blocks auto-merge instead of being mistaken for a clean audit.
