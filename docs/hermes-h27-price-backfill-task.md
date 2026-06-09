# H27 — Incremental Price Backfill Candidate

## Context

H26 established the current state:

- Full validation remains `BLOCKED` because `prices.csv` lacks early historical columns.
- 2020-01-02 active universe coverage: `252/327`, missing 75 columns.
- 2021-01-04 active universe coverage: `274/315`, missing 41 columns.
- Target deployment window `2025-01-01 -> 2026-05-18` passes: `300/300`.
- Existing non-destructive candidate: `data/cn_pit/prices_h26_candidate.csv`
  - adds 3 columns: `000415.SZ`, `000423.SZ`, `000629.SZ`
  - preserves every original `prices.csv` column
  - does not reduce existing non-null counts

The local Qlib path currently has only:

- `~/.qlib/qlib_data/cn_data/instruments/csi300.txt`

It does **not** currently have `~/.qlib/qlib_data/cn_data/features/`, so do not assume Qlib binary OHLCV is available. Probe it, but fall back to free source fetchers if absent.

## Goal

Continue H26 into an H27 incremental candidate backfill workflow:

1. Treat `prices.csv` as immutable.
2. Treat `prices_h26_candidate.csv` as the current working candidate if it exists.
3. Recompute missing active-universe columns against the working candidate.
4. Attempt to fetch remaining missing columns in batches.
5. Merge successful fetches into a new candidate file.
6. Produce a report explaining what improved, what remains missing, and whether candidate is eligible for manual replacement review.

## Files You Own

Prefer editing/adding these files:

- `scripts/repair_cn_price_coverage.py`
- optional `scripts/h27_price_backfill.py`
- `reports/h27_price_backfill_report.md`
- `data/cn_pit/price_coverage_h27.json`
- `data/cn_pit/prices_h27_candidate.csv`
- tests if you add script-level helpers

Do **not** overwrite:

- `data/cn_pit/prices.csv`
- `data/cn_pit/universe.jsonl`
- `data/cn_pit/fundamentals.jsonl`

## Required Behavior

### 1. Candidate-Aware Analysis

Add a way to analyze an arbitrary price file, either:

```bash
python scripts/repair_cn_price_coverage.py --analyze --prices-file data/cn_pit/prices_h26_candidate.csv --output-prefix h27
```

or a dedicated H27 script with equivalent behavior.

The analysis must report:

- active universe at checkpoints
- column coverage
- data coverage
- missing columns
- column-but-NaN counts
- target period coverage

### 2. Incremental Candidate Merge

The H27 backfill should start from:

1. `prices_h26_candidate.csv` if present
2. otherwise `prices.csv`

Then write:

- `prices_h27_candidate.csv`

Never overwrite `prices.csv`.

Candidate safety checks:

- all original `prices.csv` columns are present
- all original `prices.csv` non-null counts are unchanged or higher
- benchmark `000300.SS` is present
- row count and date range match original unless explicitly justified in report
- new candidate columns must have at least one non-null value

### 3. Fetch Strategy

Probe sources in this order:

1. Local Qlib features directory if present:
   - `~/.qlib/qlib_data/cn_data/features/`
   - if absent, report `qlib_features_missing=true` and continue
2. yfinance, batched and retry-safe
3. AKShare individual fetch
4. BaoStock if installed/available

Important:

- Network may be flaky. Keep partial wins.
- Batch size should be configurable.
- Continue after per-ticker failures.
- Record provider, row count, first/last date, and failure reason per ticker.

### 4. Replacement Gate

Do not replace `prices.csv`.

Instead, compute:

- `candidate_full_validate_ready: true/false`
- `remaining_missing_columns`
- `remaining_column_nan`
- `manual_replacement_recommended: true/false`

Manual replacement can only be recommended if:

- full-file active start/end column coverage is complete
- original columns/non-null counts are preserved
- no structural validation failures are introduced

## Acceptance Commands

Run from `/Users/zhuosama/.hermes/virtual-trader`:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m py_compile scripts/repair_cn_price_coverage.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/repair_cn_price_coverage.py --analyze
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/repair_cn_price_coverage.py --backfill-missing --start 2020-01-01 --end 2026-05-18 --batch-size 10
```

If you implement a separate H27 script, adapt commands but run equivalent checks.

Also run:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_ingest_cn_pit_data.py tests/test_fundamental_pit_source.py tests/test_value_account.py
```

## Expected Output

Final response must include:

- files changed
- commands run and pass/fail
- whether Qlib features were present
- number of missing columns before H27
- number of new columns added in H27
- remaining missing columns after H27
- whether `prices.csv` remained unchanged
- whether manual replacement is recommended

