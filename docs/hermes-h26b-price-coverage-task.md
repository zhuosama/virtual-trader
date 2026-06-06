# H26B — CN PIT Price Coverage Repair Plan

## Context

The historical HS300 universe is now sourced from Qlib instruments:

- `data/cn_pit/universe.jsonl`: 900 interval rows, `source_provider=qlib:instruments`
- `data/cn_pit/universe_snapshots.jsonl`: 900 evidence rows
- `CN_PIT_FileSource` static and period data quality are clean for the 2025-2026 target window

Remaining full-file validation blocker:

- `price_coverage`
- current `prices.csv` covers 300 active names at the end of the file, but only 252 of 327 active names on 2020-01-02

We need a conservative price repair workflow. Do not overwrite `data/cn_pit/prices.csv` unless a candidate file is strictly better and has been validated.

## Goal

Build a reproducible, non-destructive workflow to identify and optionally fill missing historical price coverage for active HS300 members in the current full data range.

## Files You Own

Prefer a separate helper script and report:

- `scripts/repair_cn_price_coverage.py` or `scripts/analyze_cn_price_coverage.py`
- `reports/h26_price_coverage_report.md`
- optional candidate output under `data/cn_pit/`:
  - `prices_h26_candidate.csv`
  - `price_coverage_h26.json`

Avoid editing `scripts/ingest_cn_pit_data.py`; H26A owns that file. If you need a tiny shared helper, stop and report instead of causing a conflict.

## Required Behavior

1. Analyze price coverage from existing local files only:

   - read `universe.jsonl`
   - read `prices.csv`
   - identify active universe at:
     - first price date
     - last price date
     - yearly starts between first and last
     - target deployment period `2025-01-01 -> 2026-05-18`
   - report missing active tickers for each checkpoint

2. Produce machine-readable coverage output:

   ```json
   {
     "price_date_range": "...",
     "checkpoints": [
       {
         "date": "2020-01-02",
         "active_universe": 327,
         "covered": 252,
         "missing_count": 75,
         "missing_tickers": [...]
       }
     ],
     "target_period": {
       "start": {"covered": 300, "active_universe": 300},
       "end": {"covered": 300, "active_universe": 300}
     }
   }
   ```

3. Add a safe optional fetch mode if feasible:

   - fetch only missing historical tickers
   - merge into `prices_h26_candidate.csv`, not `prices.csv`
   - preserve all existing columns and dates
   - do not drop benchmark `000300.SS`
   - do not reduce non-null counts for any existing column
   - print a clear comparison: existing coverage vs candidate coverage

4. If live/network fetching fails or data source does not provide missing tickers:

   - leave current `prices.csv` untouched
   - still produce the analysis JSON + Markdown report
   - list exact missing tickers and recommended next source (Qlib full CN price bundle, Tushare daily, BaoStock, or local vendor CSV)

## Suggested CLI

Use one command for local analysis:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/repair_cn_price_coverage.py --analyze
```

Optional non-destructive fetch:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/repair_cn_price_coverage.py --fetch-missing --start 2020-01-01 --end 2026-05-18
```

Network may be restricted. If fetch fails because of network or provider limits, report it and keep analysis artifacts.

## Acceptance Checks

Run from `/Users/zhuosama/.hermes/virtual-trader`:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m py_compile scripts/repair_cn_price_coverage.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/repair_cn_price_coverage.py --analyze
```

Expected:

- report shows first-date gap around `252/327` with 75 missing tickers
- report shows target period coverage `300/300`
- existing `data/cn_pit/prices.csv` is unchanged unless explicitly proven safe and reviewed

## Final Output

Report:

- files changed
- exact commands run and pass/fail
- whether any candidate CSV was created
- full coverage gap summary
- target period coverage summary
- recommended next data source to clear full-file `price_coverage`
