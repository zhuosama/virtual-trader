# H22 — Historical HS300 Universe Ingestion

## Goal

Replace the current-HS300 universe approximation with point-in-time historical constituent intervals for the CN value backtest.

Current state:
- `scripts/ingest_cn_pit_data.py` fetches current HS300 constituents.
- `data/cn_pit/universe.jsonl` contains `SURVIVORSHIP_BIAS` notes.
- `CN_PIT_FileSource` now correctly blocks deployment when those notes are present.

Target state:
- `data/cn_pit/universe.jsonl` is built from dated historical HS300 constituent/weight snapshots.
- Each row has a real membership interval: `effective_date` and `end_date`.
- `DataQuality.survivorship_bias` may become `false` only if the historical snapshots cover the requested backtest period without fallback to current constituents.

## References

- Tushare `index_weight`: https://tushare.pro/document/2?doc_id=96
  - Interface: `pro.index_weight(index_code='399300.SZ', start_date='YYYYMMDD', end_date='YYYYMMDD')`
  - Output fields: `index_code`, `con_code`, `trade_date`, `weight`
  - Monthly data; source is public index-company data.
- AKShare CSI latest constituents and weights:
  - https://akshare.akfamily.xyz/data/index/index.html
  - `index_stock_cons_csindex(symbol="000300")`
  - `index_stock_cons_weight_csindex(symbol="000300")`
  - Treat these as latest-snapshot fallback only, not historical PIT.
- Qlib PIT design:
  - https://qlib.org.cn/en/latest/advanced/PIT.html
  - Use as design reference for date-gated data and instruments.

## Implementation Scope

Own these files unless absolutely necessary:
- `scripts/ingest_cn_pit_data.py`
- `backtest/experiments/fundamental_backtest.py`
- `tests/test_fundamental_pit_source.py`
- Optional new tests under `tests/`
- Generated output under `data/cn_pit/`

Do not revert unrelated files.

## Requirements

### 1. Add historical universe fetch mode

Add a CLI mode such as:

```bash
python scripts/ingest_cn_pit_data.py --fetch-historical-universe --start 2020-01-01 --end 2026-05-18
```

Preferred provider:
- Tushare Pro `index_weight`.
- Read token from `TUSHARE_TOKEN`, `TUSHARE_API_TOKEN`, or a local config if already used in the repo.

If no usable Tushare token exists:
- Do not pretend current constituents are PIT-clean.
- Either fail the historical fetch with a clear message, or write fallback current data with `SURVIVORSHIP_BIAS` and `research_only`.

### 2. Snapshot to interval conversion

Fetch monthly HS300 constituent/weight snapshots across the requested date range.

Write optional raw snapshots:
- `data/cn_pit/universe_snapshots.jsonl`

Then convert snapshots to interval rows in `data/cn_pit/universe.jsonl`.

Each row must include:
- `ticker`: normalized yfinance ticker, e.g. `600519.SS`, `000001.SZ`
- `code`: six-digit stock code
- `effective_date`: first snapshot trade date where the stock is present in the current continuous membership segment
- `end_date`: last date before the next snapshot where the stock is absent, or empty if still active at the last snapshot
- `source_url`: Tushare doc URL or provider source URL
- `ingested_at`
- `index_code`
- `weight`: weight from the interval start snapshot if available
- `source_provider`: e.g. `tushare:index_weight`
- `snapshot_count` or equivalent audit field

Interval rules:
- If a ticker appears in consecutive snapshots, keep one continuous interval.
- If it disappears and later reappears, create a new interval.
- `end_date` should be the day before the next snapshot date where absence is first observed.

### 3. Data quality semantics

`DataQuality.survivorship_bias=false` is allowed only when:
- Historical snapshots came from a dated historical provider.
- No row contains `SURVIVORSHIP_BIAS`.
- The snapshot date range covers the backtest start/end sufficiently.
- No current-constituent fallback was used.

If any fallback, approximation, or insufficient coverage is detected:
- `survivorship_bias=true`
- `research_only=true`
- `validation_report.json.data_quality_blockers` includes `survivorship_bias`

### 4. Validation

Extend `validate()` in `scripts/ingest_cn_pit_data.py` to report:
- universe interval count
- raw snapshot count, if present
- min/max snapshot date
- min/max membership effective_date
- number of open intervals
- whether historical coverage covers the price/backtest date range
- any quality blockers

Validation may be `PASSED` for file structure while still blocking deployment due data quality.

### 5. Tests

Add focused unit tests with synthetic snapshots:
- Snapshot-to-interval conversion merges consecutive appearances.
- Disappear/reappear creates two intervals.
- Current-only fallback keeps `survivorship_bias=true`.
- `CN_PIT_FileSource.get_universe(as_of_date)` uses historical intervals correctly.
- Backtest deploy gate stays blocked when historical coverage is insufficient.

### 6. Verification commands

Run:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m py_compile backtest/experiments/fundamental_backtest.py scripts/ingest_cn_pit_data.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_fundamental_pit_source.py tests/test_value_account.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate
```

If a Tushare token is available, also run the historical fetch and then rerun validation.

## Acceptance Criteria

Report back with:
- Whether Tushare token was available.
- Number of historical snapshots fetched.
- Snapshot date range.
- Number of universe intervals written.
- `validation_report.json` summary.
- Backtest deploy gate result.

Deployment remains blocked unless true historical membership intervals are present and coverage is sufficient.
