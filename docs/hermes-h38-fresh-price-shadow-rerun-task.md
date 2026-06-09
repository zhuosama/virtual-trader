# H38 — Fresh PIT Price Refresh + Shadow Rerun

## Background

H37 is blocked because the H35 shadow account has a terminal streak of 5 losing sells:

- current H37 report: `reports/h37_forward_shadow_monitor_report.md`
- current H35 state: `value_account/reports/h35_shadow_state.json`
- current H35 trade log: `value_account/logs/h35_shadow_trades.jsonl`
- current candidate prices: `data/cn_pit/prices_h30_candidate.csv`
- current price max date: `2026-05-18`

Current date for this task: `2026-05-22`. The practical target is to refresh through the latest fully closed China trading day, expected `2026-05-21`.

Do not alter production ledgers or live strategies. This task is research/paper only.

## Goal

Use existing local credentials/config for Tushare, without printing secrets, to extend the H30 candidate price matrix past `2026-05-18`, then rerun the H35 shadow account against the fresh candidate data using separate H38 output files.

## Non-Destructive Rules

Do not modify:

- `accounts/*.json`
- `trades/**`
- `reports/daily/**`
- `strategies/active.json`
- `data/cn_pit/prices.csv`
- existing H35/H36/H37 canonical output files
- `value_account/h34_shadow_account_config.json`

Allowed new/updated H38 artifacts:

- `data/cn_pit/prices_h38_candidate.csv`
- `data/cn_pit/price_coverage_h38.json`
- `reports/h38_price_refresh_report.md`
- `value_account/logs/h38_shadow_trades.jsonl`
- `value_account/reports/h38_shadow_state.json`
- `value_account/reports/h38_shadow_daily_report.md`
- `backtest/runs/fundamental_value_h38_shadow_run.json`
- `reports/h38_shadow_rerun_report.md`

## Tasks

1. Inspect `data/cn_pit/prices_h30_candidate.csv`, `data/cn_pit/universe_h30_candidate.jsonl`, and `data/cn_pit/universe_snapshots_h30_candidate.jsonl`.
2. Determine the current max date in the H30 candidate price file.
3. Fetch daily close prices from Tushare for the existing price columns from the day after that max date through the latest fully closed trading day <= `2026-05-21`.
4. Write `prices_h38_candidate.csv` by extending H30. Preserve all existing rows and columns. Do not overwrite H30.
5. Validate H38 coverage for the active PIT universe across `2025-01-01` through the H38 max date. At minimum, check start/end/checkpoint active-universe price coverage and report any missing columns or column-but-NaN cases.
6. Write `price_coverage_h38.json` and `h38_price_refresh_report.md` with:
   - source/provider used,
   - old max date,
   - new max date,
   - rows added,
   - columns count,
   - missing columns count,
   - column-but-NaN count,
   - whether H38 is usable for a shadow rerun.
7. If H38 price data extends beyond `2026-05-18` and coverage is usable, rerun:

```bash
python scripts/h35_shadow_account_executor.py \
  --end <h38_max_date> \
  --prices-file data/cn_pit/prices_h38_candidate.csv \
  --universe-file data/cn_pit/universe_h30_candidate.jsonl \
  --snapshots-file data/cn_pit/universe_snapshots_h30_candidate.jsonl \
  --trade-log value_account/logs/h38_shadow_trades.jsonl \
  --state-file value_account/reports/h38_shadow_state.json \
  --report-file value_account/reports/h38_shadow_daily_report.md \
  --run-file backtest/runs/fundamental_value_h38_shadow_run.json
```

8. Compare H38 vs H35:
   - total return,
   - Sharpe,
   - max drawdown,
   - closed trades,
   - terminal losing sell streak,
   - whether the first fresh closed sell after `2026-05-18` is profitable,
   - blockers/warnings,
   - whether the shadow account remains BLOCKED or naturally unblocks.
9. Write `reports/h38_shadow_rerun_report.md`.

## Acceptance

- H38 must not overwrite H30/H35/H36/H37 canonical artifacts.
- If Tushare fetch fails or returns partial data, write reports with `Status: BLOCKED` and explain the exact blocker.
- If H38 candidate is created, it must preserve all H30 columns and have no reduced non-null counts in pre-existing rows.
- If H38 rerun is performed, it must use separate H38 output paths only.
- Final response should include exact commands run, artifacts written, and the H38 verdict.
