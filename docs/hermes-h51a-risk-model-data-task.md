# H51a — Risk Model ADTV Data Ingestion

## Context
H45 PRD direction #4 (Risk Model Overlay) requires a "liquidity participation cap" to constrain single-name position sizing to a fraction of its Average Daily Trading Volume (ADTV). The existing liquidity caches (`h33`, `h40`) only cover small sub-universes (15-28 tickers) for limited windows.
H51a will build the full liquidity dataset for the H30 universe to unblock the H51b risk model search.

## Objective
Ingest the `amount` (daily trading value, RMB) and `vol` (daily volume, shares — Tushare returns 手/100-share lots; must convert to absolute shares before persistence) fields from the Tushare `daily` endpoint for all 481 tickers in `data/cn_pit/universe_h30_candidate.jsonl`, covering `2023-10-01` to `2026-05-21`. The 3-month buffer before cal_2024 backtest window lets the H51b 20-day trailing ADTV computation be valid from the first 2024 trading day. Output a long-format CSV (one row per ticker × date).

## Inputs
- `data/cn_pit/universe_h30_candidate.jsonl`
- Tushare token (standard resolution)

## Outputs
- `scripts/h51a_build_tushare_daily_amount.py`
- `data/cn_pit/liquidity_h51a_daily_amount.csv` — long format, columns: `date` (YYYY-MM-DD), `ticker` (Yahoo format `000001.SZ`), `amount_rmb` (float), `vol_shares` (float; ×100 from Tushare 手), `source` (`"tushare:daily"`). NULL allowed only when Tushare returns NULL with reason in coverage JSON.
- `data/cn_pit/liquidity_coverage_h51a.json` — per-ticker row count, per-date ticker count, fetch_failures `[{ticker, reason}]`, provenance.
- `reports/h51a_daily_amount_ingestion_report.md`
- `tests/test_h51a_build_tushare_daily_amount.py`
- `scripts/validate_hxx_artifacts.py` — register `h51a` with `validate_h51a` asserting: `provenance.provider == "tushare:daily"`, snapshot_date present, ticker_coverage_pct field present, fetch_failures schema = `[{ticker, reason}]`, CSV columns match exact list above, vol unit is shares (not 手).
- Raw per-ticker cache at `data/cn_pit/raw/h51a_tushare_daily/<ts_code>.csv` (gitignored — add `data/cn_pit/raw/h51a_tushare_daily/` to `.gitignore` before the full run).

## Hard Prohibitions
- Do not modify any existing dataset, run JSON, or report.
- Do not modify `data/cn_pit/liquidity_h33_daily_amount.csv` or `data/cn_pit/liquidity_h40_h39_candidate_daily_amount.csv` (legacy small files still referenced by H33/H40 audits).
- Do not modify production trading config.
- Do not place live orders.
- Do not print or store the Tushare token.
- Do not author commits as `codex` or `claude-code`.
- Do not loosen `tests/test_validate_hxx_artifacts.py`.

## Coverage Acceptance
- `ticker_coverage_pct >= 0.98` — at least 472 of 481 universe tickers must have ≥1 row.
- Per-ticker row count ≥ 600 on average (between 2023-10-01 and 2026-05-21 there are ~640 A-share trading days; allow ~6% tolerance for halts / IPO-after-2023-Q4 / delistings).
- 20-day trailing ADTV computable for ≥ 95% of (ticker × eval_date) pairs across the H42 deploy/eval windows (cal_2024 / h1_2025 / h2_2025 / ytd_2026 / deploy_2025_2026). The script must compute this gate and surface in coverage JSON; otherwise H51b's liquidity cap will silently exclude tickers.
- `fetch_failures` count ≤ 10 (allow rare Tushare hiccups but not systematic failure).

## Rate-Limit / Retry / Cache Policy
- Per-ticker raw cache: skip fetch if cache file exists and date range fully covered.
- Tushare `daily` is a high-QPS endpoint but still rate-limited (typically 500 calls/min on paid tiers). Apply:
  - Exponential backoff with jitter on HTTP 429 / Tushare error_code: initial 2s, double each retry, 60s cap, max 5 retries per ticker, then add to `fetch_failures`.
  - Hard-cap base rate at 5 calls/sec across all calls.
- Failed ticker → log to `fetch_failures` in coverage JSON; do NOT abort the run.

## Commands
**Smoke Command**:
`python scripts/h51a_build_tushare_daily_amount.py --universe data/cn_pit/universe_h30_candidate.jsonl --limit 5 --start 20240101 --end 20240131 --output-csv /tmp/h51a_smoke.csv --output-coverage /tmp/h51a_cov.json --output-report /tmp/h51a_rep.md`

**Full Command**:
`python scripts/h51a_build_tushare_daily_amount.py`

**Verification**:
```bash
python scripts/validate_hxx_artifacts.py --artifact h51a
pytest tests/test_h51a_build_tushare_daily_amount.py tests/test_validate_hxx_artifacts.py -q
git status --short
```
(must show only the newly created files, no modifications to existing data)

## Closure Note
- Append H51a row to `docs/strategy-optimization-sync.md` (verdict, ticker coverage %, ADTV-computable %, fetch_failures count).
- Flip `docs/agents/next-slices.md` H51a entry from `OPEN` to `DONE`; flip H51b from `BLOCKED` to `OPEN`.
