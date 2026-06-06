# H26A — Period-Scoped CN PIT Validation

## Context

The CN PIT pipeline now uses Qlib `csi300.txt` interval data for the historical HS300 universe:

- `data/cn_pit/universe.jsonl`: 900 interval rows, `source_provider=qlib:instruments`
- `data/cn_pit/universe_snapshots.jsonl`: 900 matching evidence rows
- `data/cn_pit/prices.csv`: current price file starts at 2020-01-02 and covers 300 columns + date

Full-file validation is still `BLOCKED` because the earliest price date has incomplete active-universe price coverage:

- 2020-01-02 active universe: 327
- available price columns among active names: 252
- blocker: `price_coverage`

However, the target deployment window `2025-01-01 -> 2026-05-18` has full active coverage:

- 2025-01-01 active universe: 300
- 2026-05-18 active universe: 300
- price coverage: 300/300 at both ends
- `CN_PIT_FileSource.data_quality_for_period("2025-01-01", "2026-05-18")` is clean

## Goal

Add a period-scoped validation mode to `scripts/ingest_cn_pit_data.py` so full-data validation can remain blocked while a specific backtest/deployment window can be explicitly validated as clean.

## Files You Own

- `scripts/ingest_cn_pit_data.py`
- `tests/test_ingest_cn_pit_data.py`
- optionally `reports/h26_period_validation_report.md`

Do not edit data files except by running the validation script if needed. Do not modify `backtest/experiments/fundamental_backtest.py` unless a test proves it is required.

## Required Behavior

1. Extend `validate()` to accept optional `period_start` and `period_end`.

2. Add CLI flags for validate-period behavior:

   ```bash
   python scripts/ingest_cn_pit_data.py --validate --period-start 2025-01-01 --period-end 2026-05-18
   ```

   Keep existing `--start` / `--end` semantics for fetch modes. Do not silently repurpose them for validate unless you keep backward compatibility and document it clearly in help text.

3. When period bounds are provided:

   - include these summary fields:
     - `validation_scope`: `"period"`
     - `period_start`
     - `period_end`
     - `period_active_universe_start_count`
     - `period_active_universe_end_count`
     - `period_price_coverage_start`
     - `period_price_coverage_end`
   - compute price coverage against active universe at `period_start` and `period_end`, not only at the first/last date in `prices.csv`
   - call `CN_PIT_FileSource.data_quality_for_period(period_start, period_end)` for the data-quality flags
   - add `price_coverage` blocker if any active ticker at period start/end lacks a price column

4. When no period bounds are provided:

   - preserve current full-file validation behavior
   - `python scripts/ingest_cn_pit_data.py --validate` should continue to be `BLOCKED` with `price_coverage` for current data

5. Do not let a clean period overwrite or hide full-file data quality status. The validation report should state its scope.

## Acceptance Checks

Run these commands from `/Users/zhuosama/.hermes/virtual-trader`:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m py_compile scripts/ingest_cn_pit_data.py tests/test_ingest_cn_pit_data.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_ingest_cn_pit_data.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate --period-start 2025-01-01 --period-end 2026-05-18
```

Expected:

- full validate: `status=BLOCKED`, blocker includes `price_coverage`
- period validate: `status=PASSED`, `can_deploy_data_quality=true`, no data-quality blockers
- period summary reports `period_price_coverage_start=300/300` and `period_price_coverage_end=300/300`

## Tests To Add

Add focused tests in `tests/test_ingest_cn_pit_data.py`:

- period validation can pass when full file starts with a price coverage gap
- period validation blocks when a period active ticker lacks a price column
- full validation still blocks on earliest-date coverage gap
- CLI parsing accepts `--period-start` and `--period-end`

Keep tests offline. Use temp dirs and monkeypatch existing module globals.

## Final Output

Report:

- files changed
- exact commands run and pass/fail
- full validate status
- period validate status
- any remaining risk
