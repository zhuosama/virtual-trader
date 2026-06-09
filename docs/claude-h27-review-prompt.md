# Claude H27 Read-Only Review Prompt

You are reviewing `/Users/zhuosama/.hermes/virtual-trader`.

Review scope:

- `scripts/ingest_cn_pit_data.py`
- `scripts/repair_cn_price_coverage.py`
- `tests/test_ingest_cn_pit_data.py`
- `backtest/experiments/fundamental_backtest.py`
- `data/cn_pit/validation_report.json`
- `data/cn_pit/validation_report_2025-01-01_2026-05-18.json`
- `data/cn_pit/price_coverage_h26.json`
- `reports/h26_price_coverage_report.md`
- any H27 files if present

Mode: read-only audit. Do not edit files.

Context:

- H25 imported Qlib CSI300 historical universe as interval rows.
- H26 added period-scoped validation and non-destructive price coverage analysis.
- Full validation should remain `BLOCKED` due to `price_coverage`.
- Period validation for `2025-01-01 -> 2026-05-18` should be `PASSED`.
- H27 is intended to incrementally backfill missing historical price columns into a candidate CSV without replacing `prices.csv`.

Focus questions:

1. Can full-file `price_coverage` still be falsely cleared?
2. Can period validation falsely pass when the requested period is outside the price date range or has missing active ticker columns?
3. Does period validation accidentally overwrite or mislabel the full validation report?
4. Does `repair_cn_price_coverage.py` correctly handle open-ended universe intervals, non-trading target dates, candidate CSVs, and missing-column vs NaN-data semantics?
5. Can a candidate price CSV drop or degrade existing columns without being detected?
6. If H27 files exist, can H27 recommend manual replacement of `prices.csv` before candidate coverage is actually complete?
7. Are tests strong enough, or are there mock-only / placeholder paths that fail to protect behavior?

Output format:

Use this exact structure:

```
Findings

HIGH
- H1. <title>
  <file:line>
  <rationale>

MEDIUM
- M1. ...

LOW
- L1. ...

NIT
- N1. ...

Focus question answers
1. ...
2. ...

Verdict: APPROVE or REQUEST_CHANGES
```

If there are no findings for a severity section, omit that section.
