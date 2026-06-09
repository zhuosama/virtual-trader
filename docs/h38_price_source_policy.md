# H38+ Price Source Policy

## Current Decision

H38+ research runs use `data/cn_pit/prices_h38_candidate.csv` as a research-only continuation of the H30 candidate price matrix.

- Stock columns continue H30's `yfinance auto_adjust=True` methodology.
- HS300 benchmark gaps may be patched from Tushare `index_daily` when the adjusted Yahoo benchmark column is missing for a specific date.
- This dataset is acceptable for shadow reruns, diagnostics, and research gates.
- This dataset is not sufficient by itself for production/live deployment.

## Why

H30 was built from adjusted Yahoo prices. Mixing raw Tushare `daily.close` into stock columns after 2026-05-18 would create an adjustment-methodology break in the same time series.

The benchmark patch is narrower: it fills a missing index close needed for HS300/excess-return calculations and does not affect stock trade prices.

## Production Requirement

Before any live deployment, rebuild the whole price matrix from one consistent production-grade source:

1. Tushare `pro_bar` or equivalent fully adjusted A-share OHLCV.
2. HS300 benchmark from the same provider family.
3. A full coverage report over the exact backtest/live-trial window.
4. A fresh H42-or-later rerun against that rebuilt matrix.

Until then, H38+ artifacts should be labeled `research_only` or `shadow usable`, not `production deployable`.

## Loader Registry (ENGINE-LOADER-V1)

As of 2026-05-30, the provider layer in `backtest/market_data.py` has been hardened with a formal loader registry. This section documents the contracts that the loader registry enforces.

### Enumerated Providers

All data providers MUST register their name in the `SOURCE_PROVIDERS` frozen set at the top of `backtest/market_data.py`:

```python
SOURCE_PROVIDERS: frozenset[str] = frozenset({
    "tushare:pro_bar:qfq",
    "tushare:daily",
    "akshare:stock_zh_a_hist",
    "akshare:stock_zh_a_hist_qfq",
    "baostock:query_history_k_data_plus",
    "yfinance:download",
    "static:in_memory",
    "cache:local_csv",
})
```

Any concrete `MarketDataProvider` subclass asserts `cls.name in SOURCE_PROVIDERS` at import-time via `__init_subclass__`. A new provider (e.g. `mootdx`) MUST be added to this enumeration before the class definition.

### Precheck Contract (STOP-on-missing)

Every concrete provider implements `precheck() -> None`. If a provider cannot function (missing token, missing library, missing endpoint), it MUST raise `LoaderBlockedError(provider=..., reason=...)`. The `FallbackMarketDataProvider` calls `precheck()` on every provider in the chain before attempting data fetch — precheck failure is a valid fallback signal and is logged to `precheck_log`.

When ALL providers in the chain fail (all precheck fail or all return empty data), `FallbackMarketDataProvider` raises hard — no silent `INFRA_ERROR` return path.

### Fallback Chain Semantics

`FallbackMarketDataProvider.get_close_prices` iterates providers in order, calling `precheck()` first. If precheck passes, it attempts data fetch. The result carries:
- `fallback_chain` — all provider names attempted, in order
- `selected_provider` — the one that actually returned data
- `fallback_reason` — why fallback was triggered (if any)
- `precheck_log` — one-line summary per precheck attempt

### sha256 Audit Hook

After assembling the final prices DataFrame, `compute_prices_sha256()` generates a stable sha256. The checksum is embedded in `ProviderResult.sources_used["__sha256"]` for downstream audit. `ChecksumMismatchError` is raised on mismatch — never silently logged.

### Related Plan

Full specification: `docs/superpowers/plans/2026-05-30-loader-registry-engine-pr.md`

## OHLV Supplemental Layer (ENGINE-OHLV-V1)

As of 2026-05-30, a supplemental OHLV daily-bar layer has been added under `data/cn_pit/ohlv_h47_supplement.csv`. This layer coexists with the H47 close-only matrix (which remains the authoritative close-price source) and provides open/high/low/volume/amount columns for any factor requiring multi-column data.

### Output File

| Property | Value |
|----------|-------|
| File path | `data/cn_pit/ohlv_h47_supplement.csv` |
| Format | long format |
| Columns | `date, ticker, open, high, low, volume, amount` |
| Universe | HS300 H47 PIT membership (from `universe.jsonl`) |

### Provider Chain

The OHLV layer uses the same `FallbackMarketDataProvider` chain introduced in ENGINE-LOADER-V1:

```
TushareProvider (daily endpoint) → AkshareProvider → YFinanceProvider
```

- **TushareProvider** (`tushare:daily`): native OHLCV from the `daily` endpoint; preferred primary source.
- **AkshareProvider** (`akshare:stock_zh_a_hist`): `stock_zh_a_hist(symbol, adjust="qfq")` per ticker.
- **YFinanceProvider** (`yfinance:download`): `yf.download(ticker, interval="1d")`. **Caveat**: YFinance does not provide `amount` (成交额) — this column will be `NaN` when YFinance is the selected provider.

Precheck semantics and STOP-on-exhausted behavior are identical to the close-price path documented in the Loader Registry section above.

### Joining with H47 Close

Research code joins the OHLV supplement to the H47 close matrix on `(date, ticker)`:

```python
df_combined = df_close.merge(df_ohlv, on=["date", "ticker"])
```

The close column in the combined DataFrame can be validated against `prices_h47_tushare_qfq_candidate.csv` close values — if they diverge, the H47 close value is authoritative.

### sha256 Audit

The OHLV layer carries a stable sha256 checksum in `metadata.json`:

```json
"ohlv_layer": {
    "sha256": "<computed>",
    "fallback_chain": [...],
    "selected_provider": "tushare:daily",
    ...
}
```

- Checksum is computed by `compute_ohlcv_sha256()` in `backtest/market_data.py` (sorts by `(date, ticker)` before hashing, making row order invariant).
- The validation path (`scripts/ingest_cn_pit_ohlv.py --validate`) recomputes and compares — mismatch is a hard error, never silently logged.

### Related Plan

Full specification: `docs/superpowers/plans/2026-05-30-ohlv-supplemental-engine-pr.md`
