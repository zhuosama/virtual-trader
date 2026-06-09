# H29 — Universe/Price Data Alignment Task

You are working in `/Users/zhuosama/.hermes/virtual-trader`.

## Context

H28 filled all missing price columns into `data/cn_pit/prices_h28_candidate.csv`, but stricter validation now blocks deployment because some active universe members have price columns but no non-null price at validation endpoints.

Current facts:

- Official `data/cn_pit/prices.csv` remains unchanged.
- H28 candidate has 0 missing active columns.
- H28 candidate still has active price-data gaps:
  - Full start `2020-01-02`: `301/327` active tickers have non-null prices.
  - Deployment period start `2025-01-02`: `297/300` active tickers have non-null prices.
- Target-period NaN tickers on `2025-01-02`:
  - `302132.SZ` first non-null price in H28 candidate: `2025-02-10`
  - `600930.SS` first non-null price in H28 candidate: `2025-07-16`
  - `603296.SS` first non-null price in H28 candidate: `2025-10-09`
- Their Qlib universe rows currently show impossible early intervals such as `effective_date=2005-01-01`, suggesting bad universe intervals or static fallback evidence.

## Goal

Diagnose and fix, without weakening gates, the mismatch between active universe intervals and available PIT price data.

## Required Work

1. Build a diagnostic report listing every active ticker with column-but-NaN data at each checkpoint in `data/cn_pit/price_coverage_h28.json`.
2. For each ticker, classify the likely cause:
   - `pre_listing_or_bad_universe_interval`
   - `vendor_price_gap`
   - `suspension_or_non_trading`
   - `unknown`
3. For the three deployment-period blockers (`302132.SZ`, `600930.SS`, `603296.SS`), verify whether the Qlib interval is valid historical CSI300 membership evidence. Use local files first; use external data only if available and cite the source in the report.
4. Do not “repair” universe effective dates using first price date alone unless the report explicitly labels the result research-only. Listing date is not index inclusion date.
5. If a trustworthy PIT source is found, generate candidate artifacts only:
   - `data/cn_pit/universe_h29_candidate.jsonl` or `data/cn_pit/prices_h29_candidate.csv`
   - `data/cn_pit/price_coverage_h29.json`
   - `reports/h29_universe_price_alignment_report.md`
6. Keep official files unchanged unless explicitly promoted later:
   - do not overwrite `data/cn_pit/prices.csv`
   - do not overwrite `data/cn_pit/universe.jsonl`

## Acceptance Criteria

- `scripts/ingest_cn_pit_data.py --validate --prices-file data/cn_pit/prices_h28_candidate.csv --period-start 2025-01-01 --period-end 2026-05-18` remains `BLOCKED` unless all active endpoint prices are non-null and universe evidence is truly PIT-safe.
- Backtest deploy gate remains blocked while price data coverage is `297/300`.
- The report clearly separates:
  - “price columns filled”
  - “non-null active price coverage”
  - “historical universe validity”
- Add or update focused tests for any new repair logic.

## Current Verification Commands

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_ingest_cn_pit_data.py tests/test_fundamental_pit_source.py tests/test_value_account.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate --prices-file data/cn_pit/prices_h28_candidate.csv --period-start 2025-01-01 --period-end 2026-05-18
```
