# H30 — Real PIT CSI300 Universe Source Task

You are working in `/Users/zhuosama/.hermes/virtual-trader`.

## Context

H28/H29 hardened the deployment gate. The current Qlib `instruments/csi300.txt` fallback is no longer trusted as clean PIT evidence because many rows use a floor date such as `2005-01-01` for stocks whose first local price appears much later.

Current validation status:

- Official `data/cn_pit/prices.csv`: `BLOCKED`
- H28 candidate `data/cn_pit/prices_h28_candidate.csv`: `BLOCKED`
- Blockers: `price_coverage`, `survivorship_bias`, `research_only`
- H28 deployment-period active price data: `297/300`
- Deployment-period NaN blockers on `2025-01-02`: `302132.SZ`, `600930.SS`, `603296.SS`
- Tests currently pass:
  - `146 passed`

Important constraints:

- Do not overwrite official files:
  - `data/cn_pit/universe.jsonl`
  - `data/cn_pit/universe_snapshots.jsonl`
  - `data/cn_pit/prices.csv`
- Only write candidate/report artifacts unless explicitly promoted later.
- Do not weaken data-quality gates.
- Do not use Qlib floor-date rows as clean PIT membership evidence.

## Goal

Find or produce a trustworthy point-in-time CSI300 historical constituents source and generate a candidate universe if possible.

## Required Work

1. Probe local environment:
   - Is `tushare` installed?
   - Is `TUSHARE_TOKEN` / `TUSHARE_API_TOKEN` / repo config token available?
   - Are there local cached CSI300 membership files outside `~/.qlib/.../csi300.txt`?
2. Preferred source: Tushare Pro `index_weight(index_code='399300.SZ')`.
   - If token/package exists, fetch `2020-01-01 -> 2026-05-18`.
   - Reject partial coverage if snapshot min/max does not cover the requested period.
   - Convert snapshots to candidate intervals.
3. Secondary sources:
   - Existing local vendor/CSV cache if present.
   - Official CSIndex announcement files only if they include enough history to reconstruct additions/removals; cite file/source in report.
4. If no trustworthy source is available:
   - Do not fabricate intervals from first price date.
   - Write a failure report explaining exactly what is missing.
5. If a trustworthy source is available, write only candidate artifacts:
   - `data/cn_pit/universe_h30_candidate.jsonl`
   - `data/cn_pit/universe_snapshots_h30_candidate.jsonl`
   - `data/cn_pit/validation_report_h30_candidate.json`
   - `reports/h30_real_pit_universe_report.md`

## Acceptance Criteria

- Candidate universe must have explicit source provider and source URL.
- Candidate validation must not overwrite existing validation reports unless it writes an H30-suffixed report.
- If the candidate still has blockers, the report must say `BLOCKED` and list blockers.
- If a candidate would clear survivorship, prove it with:
  - snapshot date coverage,
  - interval generation method,
  - a comparison against the three H29 blockers,
  - and a validation/backtest gate run using candidate files.

## Useful Commands

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_ingest_cn_pit_data.py tests/test_fundamental_pit_source.py tests/test_repair_cn_price_coverage.py tests/test_value_account.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate --prices-file data/cn_pit/prices_h28_candidate.csv --period-start 2025-01-01 --period-end 2026-05-18
```

## Expected Output

End with a concise summary:

- source found or not,
- candidate artifacts written,
- validation status,
- backtest deploy status if run,
- next blocker.

## Manual Terminal Path If Codex Sandbox Cannot Reach Providers

If Hermes/Codex cannot reach external APIs from the sandbox but the user's terminal can, run this from a normal terminal:

```bash
cd /Users/zhuosama/.hermes/virtual-trader

# Optional, only if not installed:
/Users/zhuosama/.hermes/hermes-agent/venv/bin/pip install tushare

# Required:
export TUSHARE_TOKEN='...'

/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py \
  --fetch-historical-universe \
  --start 2020-01-01 \
  --end 2026-05-18
```

Do not promote the fetched data blindly. After fetch, rerun validation/backtest gates and produce H30 candidate/report artifacts first.

If you export Tushare `index_weight` rows to CSV instead of letting the ingestion script write official files, build a candidate non-destructively:

```bash
cd /Users/zhuosama/.hermes/virtual-trader

/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/build_h30_universe_candidate.py \
  --snapshots /path/to/tushare_index_weight_399300.csv \
  --start 2020-01-01 \
  --end 2026-05-18
```

The CSV should include `trade_date` plus one of `con_code`, `ts_code`, `ticker`, `code`, or `symbol`; `weight` is optional. The builder rejects partial coverage and writes only H30-suffixed candidate artifacts.
