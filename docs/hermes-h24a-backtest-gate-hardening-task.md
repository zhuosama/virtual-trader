# H24A — Backtest-Side Data Quality Gate Hardening

## Goal

Address Claude review findings on the backtest/data-source side:

- HIGH H1: evidence must match `provider + ticker + effective_date`, not just exist.
- HIGH H2: data quality must be evaluated against the requested backtest window.
- HIGH H5: remove/replace false-clean tests that accept fake evidence.
- MEDIUM M2: add false-deploy tests for insufficient coverage, mixed providers, snapshot_count=0, bogus snapshots.
- MEDIUM M3: block unsafe current valuation fields in CN PIT fundamentals.
- LOW L1: do not fall back to live `yfinance` prices when `prices.csv` is missing.
- LOW L2: validate/normalize dates instead of raw string comparison where practical.
- LOW L3: treat snapshot_count as weak evidence unless matched by actual evidence rows and period coverage.

## Ownership

Own these files:
- `backtest/experiments/fundamental_backtest.py`
- `tests/test_fundamental_pit_source.py`
- Optional new tests under `tests/` if useful

Do not edit:
- `scripts/ingest_cn_pit_data.py` unless absolutely necessary. Hermes-B owns it.
- Existing data files unless required for a smoke check.

Do not revert unrelated changes.

## Required Implementation

### 1. Period-aware data quality

Add a period-aware method, e.g.:

```python
def data_quality_for_period(self, start_date: str, end_date: str) -> DataQuality:
    ...
```

Default `DataSource` can return `self.data_quality`.

`CN_PIT_FileSource` must recompute survivorship quality for the requested period:

- Historical evidence is accepted only when every `universe.jsonl` row has:
  - accepted provider: `tushare:index_weight` or `qlib:instruments`
  - positive `snapshot_count`
  - a matching evidence row in `universe_snapshots.jsonl`
  - match key: `(source_provider, normalized ticker, effective_date)`
- For `tushare:index_weight`, snapshot/evidence date range must cover `start_date` and `end_date`.
- For `qlib:instruments`, interval coverage must cover the requested backtest window:
  - there must be historical interval data starting on/before `start_date`
  - there must be at least one active universe member at `start_date` and at `end_date`
  - no fallback/current-only rows may be mixed in
- Any unknown/mixed provider, missing evidence row, mismatched evidence row, or explicit `SURVIVORSHIP_BIAS` marker keeps `survivorship_bias=True`.

`run_fundamental_backtest()` must use the period-aware method for deployment gating:

```python
dq = data_source.data_quality_for_period(start_date, end_date)
```

If the source lacks that method, use `data_source.data_quality`.

### 2. Unsafe fundamental fields

For `CN_PIT_FileSource`, treat these fields in `fundamentals.jsonl` as unsafe unless a future explicit PIT-safe provider is added:

- `pe_ratio`
- `pb_ratio`
- `dividend_yield`
- `market_cap`
- `fcf_yield`

If any are present and non-null:

- do not silently feed them into scoring as clean PIT data,
- set `future_function=True` and/or `ungated_fundamentals=True` through data quality,
- keep `research_only=True`.

Do not break US_EDGAR_Source behavior unless clearly necessary.

### 3. No live price fallback for CN PIT files

`CN_PIT_FileSource.get_price_history()` should raise a clear exception if `prices.csv` is missing.

Do not call `yf.download()` from `CN_PIT_FileSource`.

### 4. Tests

Add/update tests covering:

- bogus snapshot file with unrelated ticker does not clear bias,
- mismatched provider does not clear bias,
- mismatched date does not clear bias,
- `snapshot_count=0` blocks,
- Tushare snapshot range shorter than requested backtest window blocks deploy,
- Qlib interval history with no active member at start or end blocks deploy,
- mixed accepted and unknown providers blocks,
- unsafe valuation fields in CN PIT fundamentals block deployment,
- missing `prices.csv` raises instead of live downloading,
- previous false-clean test `test_historical_intervals_without_survivorship_marker_may_be_clean` is either corrected or replaced so a single fake snapshot does not imply clean deployability.

## Verification

Run:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m py_compile backtest/experiments/fundamental_backtest.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_fundamental_pit_source.py tests/test_value_account.py
```

If script-side changes from Hermes-B are already present, full test suite is fine too.

## Report Back

Report:
- files changed,
- which Claude findings were addressed,
- tests run and results,
- any remaining risks.
