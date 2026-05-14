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
- `CachedPriceProvider`: reads and writes normalized daily close CSVs under a local cache directory, keyed by symbol, date range, frequency, provider, and adjustment type.
- `AkshareProvider`: optional runtime provider for A-share daily history.
- `BaoStockProvider`: optional runtime provider for A-share daily history.
- `TushareProvider`: optional runtime provider used only when a token is configured.
- `YFinanceProvider`: legacy fallback provider, preserving current behavior.
- `FallbackMarketDataProvider`: tries providers in order, combines results, and records source metadata.
- `ProviderResult`: wraps the normalized price frame plus metadata such as sources tried, sources used, missing symbols, cache hit ratio, and cache age.

The default provider chain will be:

1. Local cache.
2. Tushare, if `TUSHARE_TOKEN` or Hermes config provides a token.
3. AKShare, if installed.
4. BaoStock, if installed.
5. yfinance.

This order favors reproducibility, then configured quality, then free A-share public sources, then the existing fallback.

Add `backtest/oos_window.py` as the owner of OOS window selection. It exposes a pure function:

```python
def compute_oos_window(changelog, account, today, trading_calendar):
    """Return {start, end, trading_days, basis_entry_date} or an INFRA_ERROR reason."""
```

The window is selected from changelog state and trading-calendar state:

- Find the earliest changelog entry date for the account to establish the historical data floor.
- Find the last `strategy`-class changelog entry or explicit strategy-maintainer review marker for the same account.
- Start the OOS window after that last strategy review/change date, never before the historical data floor.
- Count trading days, not calendar days.
- Prefer 30 trading days; if fewer than 20 usable trading days are available, return an explicit insufficient-window status and the coordinator must not auto-merge the proposal.

Add `backtest/strategy_simulator.py` for the first strategy-vs-strategy OOS comparison. This is intentionally smaller than a full production execution engine, but it must generate separate current and proposed OOS results rather than replaying the same historical trades twice.

The simulator inputs are:

- Current strategy config from `strategies/active.json`.
- Proposed strategy config produced by applying the proposal diff in memory.
- Watchlist universe from `market-data/watchlist.json`.
- OOS price data from the provider chain.
- Account capital and simple position sizing parameters from the strategy config.

The simulator outputs synthetic daily equity curves and metrics for current and proposed strategies over the same OOS window. This keeps the Overfitting Auditor's Sharpe and drawdown hard rules meaningful in v1.

## Symbol Normalization

The provider layer will centralize symbol conversion:

- Engine format: `600519.SS`, `000858.SZ`, `000300.SS`.
- Plain A-share code: `600519`, `000858`, `000300`.
- Tushare format: `600519.SH`, `000858.SZ`.
- BaoStock format: `sh.600519`, `sz.000858`.
- AKShare stock history: plain code such as `600519`; index handling uses the relevant AKShare index endpoint or a provider-specific mapping.
- Eastmoney/Tencent style market prefixes when needed: Shanghai `1.<code>` or `sh<code>`, Shenzhen `0.<code>` or `sz<code>`.

If a symbol cannot be mapped, the provider reports it as missing instead of silently returning an empty price table.

Index and ETF symbols must be explicit, not inferred loosely:

- HS300 benchmark in engine format remains `000300.SS`.
- CSI 300 ETF examples such as `510300` must map through the Shanghai ETF path.
- Shenzhen ETF examples such as `159915` must map through the Shenzhen ETF path.
- Unsupported indices or funds become `missing_symbols` with the attempted provider formats recorded.

## OOS Audit Flow

Coordinator strategy adjustment flow becomes:

1. `strategy_maintainer.generate_strategy_adjustments()` creates candidate adjustments.
2. `strategy_maintainer.propose()` persists a proposal with no live strategy write.
3. Coordinator asks `compute_oos_window()` for the OOS window.
4. Coordinator fetches OOS prices through the provider chain.
5. Coordinator runs `strategy_simulator` twice: current strategy vs proposed strategy, using the same universe, same prices, and same OOS window.
6. Coordinator passes the evidence into `audit_layer.review(oos_backtest=...)`.
7. Only `AUTO_MERGE` review decisions may call `commit_approved()`.

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
    "cache_oldest_age_days": 2,
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

The evidence object must never set `current` and `proposed` to the same replay output. If the simulator cannot produce separate current/proposed equity curves, the evidence status is `INFRA_ERROR` with a reason such as `SIMULATION_UNAVAILABLE`.

## Backtest Engine Changes

Keep the current engine as the behavioral core. Make only narrow edits:

- `fetch_prices(tickers, start, end, provider=None)` uses the injected provider when present.
- `run_backtest(..., price_provider=None)` passes the provider through.
- Existing CLI behavior still works without provider configuration.
- Tests use `StaticPriceProvider` and never call live data services.

Existing trade replay remains useful for reporting, regression checks, and backward compatibility. It is not sufficient for Overfitting Auditor evidence because it replays already-executed trades and cannot compare current strategy behavior against proposed strategy behavior.

The first implementation must include a minimal strategy-vs-strategy simulator:

- Apply proposal diffs to an in-memory copy of the current strategy.
- Use the same OOS window, universe, prices, initial capital, and fee assumptions for both versions.
- Generate candidate buy/sell signals from the strategy's configured parameters and rules. For v1 this may be a coarse rule implementation, such as MA breakout/momentum/stop-loss/take-profit/position-sizing thresholds already present in `active.json`.
- Produce separate daily equity curves and metrics for `current` and `proposed`.
- If a proposal changes a rule the simulator cannot model, return `INFRA_ERROR` with `reason="UNSUPPORTED_STRATEGY_DIFF"` rather than fabricating clean evidence.

This keeps hard reject #2 and #3 in `agents/audit_prompts/overfitting_auditor.md` active and meaningful.

## Cache Freshness

Cache correctness is part of audit evidence. Cached data must include metadata:

- `symbol`
- `provider`
- `frequency`
- `adjustment`: one of `qfq`, `hfq`, or `none`
- `start`
- `end`
- `data_fetched_at`

The cache key must include at least provider, symbol, frequency, and adjustment type. Date coverage may be stored in per-symbol files, but reads must verify that the requested date range is fully covered.

Default freshness policy:

- If cached adjusted daily data is older than 5 days, refetch before using it for audit evidence.
- If cached unadjusted daily data is older than 20 days, refetch before using it for audit evidence.
- If provider metadata reports a corporate-action sensitive field or an adjustment mismatch, refetch.
- Tests may override freshness with deterministic fixtures.

The evidence object's `data` section must include `cache_hit_ratio` and `cache_oldest_age_days`. If stale cache is used because all live providers fail, the evidence status must be no stronger than `INFRA_ERROR` unless the caller explicitly requested offline replay mode.

## Error Handling

- Missing optional packages do not fail import; the provider is marked unavailable.
- Network/API failures are captured per provider and surfaced in metadata.
- Providers use bounded retry with backoff for rate-limited public sources such as AKShare/Eastmoney/Sina/Tencent-backed endpoints.
- Empty price frames are treated as data failures, not valid zero-day backtests.
- Partial symbol coverage is allowed only if benchmark and all traded symbols needed for the selected window are present.
- Unsupported strategy diffs are treated as simulation failures, not clean approvals.
- OOS windows with fewer than 20 usable trading days are treated as insufficient evidence.
- The audit layer receives structured failure evidence so reviewers can distinguish strategy weakness from infrastructure failure.

## Testing

Unit tests:

- OOS window tests use `StaticPriceProvider` and pass offline.
- `FallbackMarketDataProvider` tries providers in order and records source metadata.
- Empty provider responses become `INFRA_ERROR`.
- Symbol conversion covers Shanghai, Shenzhen, ChiNext, STAR, and HS300 benchmark mappings.
- Cache keys include adjustment type and freshness metadata.
- Stale adjusted cache entries trigger refetch or explicit stale-data evidence.
- `compute_oos_window()` picks trading-day windows from changelog state and rejects fewer than 20 usable days.
- `strategy_simulator` produces different current/proposed metrics when a parameter diff changes signal behavior.
- Unsupported strategy diffs become `INFRA_ERROR`, not identical current/proposed evidence.
- Backward compatibility: `run_backtest()` without provider still uses the legacy yfinance path.

Integration tests:

- Coordinator audit-flow test asserts `audit_layer.review()` receives non-empty `oos_backtest`.
- Coordinator-to-audit test uses `StaticPriceProvider` to produce real current/proposed evidence without live network calls.
- Live provider smoke tests are optional and skipped unless dependencies and tokens are configured.

## Non-Goals

- Do not rewrite the backtest engine.
- Do not install or require every market-data dependency by default.
- Do not expose private proposal IDs, reviewer traces, or raw trading data on the public site.
- Do not auto-merge when data collection fails.
- Do not claim statistical significance from 20-30 trading-day OOS windows; the Overfitting Auditor's Sharpe rule remains a heuristic threshold.

## Success Criteria

- OOS tests no longer depend on live Yahoo/A-share network responses.
- Coordinator passes structured OOS evidence into `audit_layer.review()`.
- OOS evidence contains separate current and proposed metrics generated over the same window.
- Existing CLI backtest usage remains compatible.
- The data source used for each symbol is recorded and inspectable.
- Cache adjustment type and age are visible in evidence.
- OOS window selection has one pure, tested owner.
- Data failure blocks auto-merge instead of being mistaken for a clean audit.
