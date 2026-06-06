# H50a — PIT Quality Metrics Ingestion

## Context

H42, H48, H49b all closed `RESEARCH_ONLY` with `beat_HS300_windows` max at 1/5 across all top-15 candidates in every run. The H49b post-mortem ("Sector neutrality matched H48 parity, did not improve") says the binding constraint is the **alpha signal**, not portfolio structure. H45 PRD direction #2 (Quality-Value Composite Redesign) is the next investigative track.

The current `ValueScore` (`backtest/experiments/fundamental_backtest.py:710`) is built for 5 components (roe, fcf_yield, debt_to_equity, dividend_yield, PE/PB valuation), but `data/cn_pit/fundamentals.jsonl` only stores `roe` and `debt_to_equity`. The other fields silently default to `None` and the score formula degrades to a 2-feature signal. Per the H28 baseline note inside the existing fundamentals file:

> PE/PB/div_yield/market_cap/fcf_yield: intentionally OMITTED — yfinance snapshots are CURRENT values and NOT PIT-SAFE.

H45 PRD §Data Requirements lines 36–37 forbids re-using those non-PIT fields. H50a fills the gap with a **PIT-safe** source: Tushare `fina_indicator`, which exposes per-filing-period financial ratios.

H50a is the data slice. It produces an additive PIT fundamentals file enriched with profitability, balance-sheet, cash-flow, and accruals fields. H50b (the Quality-Value search) is blocked on this output.

## Objective

Ingest PIT-safe per-filing-period quality metrics from Tushare `fina_indicator` for every ticker in `data/cn_pit/universe_h30_candidate.jsonl`, write an enriched fundamentals file alongside the existing one, and register the H50a artifact family in the validator.

## V2 Revision Note (2026-05-23)

V1 dispatched cleanly but verdict was `BLOCKED`: Tushare `fina_indicator` is a **derived ratios endpoint only** — it does not expose raw line items (`n_income`, `n_cashflow_act`, `total_assets`, `total_revenue`). Result: `accruals_ratio` derived 0% of the time and the soft-field gate failed at 0% < 50%. V2 adds three sibling Tushare endpoints (`income`, `cashflow`, `balancesheet`) to source the raw intermediates, plus one targeted fallback derivation for `gross_margin`, and tightens the ROE overlap join key. The existing V1 `fina_indicator` raw cache stays intact and is re-used.

## Inputs

- `data/cn_pit/universe_h30_candidate.jsonl` (481 unique tickers)
- `data/cn_pit/fundamentals.jsonl` (existing PIT fundamentals; 300 tickers × ~3 periods; ROE + D/E only). Read-only — used to confirm overlap and filing_date format.
- Tushare token via the same lookup chain the existing scripts use (env → `scripts/.tushare_token` → `~/.tushare.token` → `agents/config.yaml` → tushare built-in)
- Tushare endpoints (four; each called per ticker per period range; same auth and retry policy):
  - `fina_indicator` — derived financial ratios (ROE/ROA/margins/liquidity/cash-flow ratios)
  - `income` — income statement raw line items (revenue, cogs, operate_profit, n_income, total_revenue)
  - `cashflow` — cash-flow statement (n_cashflow_act / operating cash flow)
  - `balancesheet` — balance sheet (total_assets)
- V1 raw cache at `data/cn_pit/raw/h50a_tushare_fina_indicator/` (481 files, ~13 cols each). **Do not rebuild or delete.** V2 reads it as-is; only the three new endpoints fetch fresh.

## Outputs

- `scripts/h50a_build_tushare_pit_quality.py` — ingestion script with smoke flags. Model after `scripts/h49a_build_tushare_sw_industry.py` (token discovery + raw-cache + retry).
- `data/cn_pit/fundamentals_h50a_pit_quality.jsonl` — one row per (ticker, report_period). Schema in next section.
- `data/cn_pit/fundamentals_coverage_h50a.json` — coverage report with provenance, mapped_count, missing-field histogram, period coverage per ticker.
- `reports/h50a_pit_quality_ingestion_report.md`
- `tests/test_h50a_build_tushare_pit_quality.py`
- `scripts/validate_hxx_artifacts.py` — register `h50a` with `validate_h50a` that enforces provenance + per-field coverage threshold.
- Raw per-ticker per-endpoint cache layout (each subdirectory gitignored):
  - `data/cn_pit/raw/h50a_tushare_fina_indicator/<ts_code>.csv` (V1 cache — re-used, do not delete)
  - `data/cn_pit/raw/h50a_tushare_income/<ts_code>.csv` (new)
  - `data/cn_pit/raw/h50a_tushare_cashflow/<ts_code>.csv` (new)
  - `data/cn_pit/raw/h50a_tushare_balancesheet/<ts_code>.csv` (new)
  - `.gitignore` must cover all four (V1 covers `h50a_tushare_fina_indicator/`; V2 must add the three new patterns or a wildcard `data/cn_pit/raw/h50a_tushare_*/`).

Do not modify `data/cn_pit/fundamentals.jsonl`. The new file is additive; downstream (H50b) joins on `ticker + report_period`.

## Schema (jsonl row)

Each row must include:

**Identity / provenance fields:**

| Field | Source / Tushare key | Notes |
|---|---|---|
| `ticker` | input (Yahoo format `000001.SZ`) | join key with prices + sector |
| `code` | input (Tushare format `000001.SZ`) | upstream Tushare key |
| `report_period` | `fina_indicator.end_date` (YYYY-MM-DD) | quarter end |
| `filing_date` | `fina_indicator.ann_date` (YYYY-MM-DD) | **PIT gate**: only visible when as_of >= this |
| `source_url` | Tushare doc URL | audit |
| `source_provider` | `"tushare:fina_indicator"` | provenance |
| `ingested_at` | UTC ISO | when fetched |
| `data_quality_note` | string (possibly multi-line) | reason for any NULL or any fallback used |

**Final score-component fields (consumed by H50b):**

| Field | Tushare key (pinned) | Fallback policy | Notes |
|---|---|---|---|
| `roe` | `roe_waa` (weighted-average ROE, annualized) | none — if `roe_waa` NULL, field is NULL with reason | **Pinned to `roe_waa`** (not `roe` / `roe_dt` / `roe_avg`). Rationale: most stable across periods, closest semantic match to H28 baseline ("annualized ROE matched to filing dates"). Cross-check vs H28 is best-effort, not a correctness gate. |
| `roa` | `roa` | none | annualized |
| `gross_margin` | `fina_indicator.grossprofit_margin` (primary) | if primary NULL: derive `(_total_revenue - _total_cogs) / _total_revenue * 100`; if either intermediate NULL, field NULL with reason listing which intermediate(s) were missing | % |
| `operating_margin` | `op_of_gr` (primary) | if `op_of_gr` is NULL: compute `_op_income / _total_revenue` (intermediates below) and write `"operating_margin: fallback formula _op_income / _total_revenue used because op_of_gr NULL"` to `data_quality_note`. If `_op_income` OR `_total_revenue` also NULL → field NULL with reason listing which intermediate(s) were missing. | % |
| `current_ratio` | `current_ratio` | none | balance sheet |
| `quick_ratio` | `quick_ratio` | none | balance sheet |
| `debt_to_equity` | `debt_to_eqt` | none | overlaps with existing fundamentals.jsonl |
| `operating_cash_flow_to_revenue` | `ocf_to_or` | none | cash-flow quality |
| `free_cash_flow` | `fcff` | none — if NULL, field NULL with reason `"free_cash_flow: fcff not reported by Tushare fina_indicator for this ticker/period"` | not always populated; covered by 50% soft gate |
| `accruals_ratio` | **derived**: `(n_income - n_cashflow_act) / total_assets` | requires all three intermediates (see next table) non-NULL; else field NULL with reason listing which intermediate(s) were missing | cash-flow conversion proxy |

**Intermediate Tushare fields (pulled across four endpoints to enable derivations; persisted in the JSONL so the derivations are auditable):**

| Field | Source endpoint | Tushare key (pinned) | Used by | Notes |
|---|---|---|---|---|
| `_net_income` | `income` | `n_income` (净利润) | `accruals_ratio` numerator | V1 mis-pinned to `fina_indicator` where this key does not exist; V2 sources from `income` |
| `_net_cashflow_op` | `cashflow` | `n_cashflow_act` (经营活动产生的现金流量净额) | `accruals_ratio` numerator | V2: from `cashflow` endpoint |
| `_total_assets` | `balancesheet` | `total_assets` | `accruals_ratio` denominator | V2: from `balancesheet`; do not silently substitute with any other key |
| `_op_income` | `fina_indicator` (primary) → `income.operate_profit` (fallback) | `op_income` (fina_indicator) then `operate_profit` (income) | `operating_margin` fallback numerator | V1 cache returned `op_income` at 100% non-null from `fina_indicator`; keep that as primary. If V2 sees a row where `fina_indicator.op_income` is NULL, fall back to `income.operate_profit` |
| `_total_revenue` | `income` | `total_revenue` (营业总收入) | `operating_margin` fallback denominator AND `gross_margin` fallback denominator | V2: from `income` |
| `_total_cogs` | `income` | `total_cogs` (营业总成本) | `gross_margin` fallback numerator (revenue − cogs) | NEW in V2; enables `gross_margin` to derive when `fina_indicator.grossprofit_margin` is NULL |

Intermediate field names start with `_` to mark them as audit-only; H50b will not consume them as score components. All six intermediates are fetched on every row regardless of whether the derivation eventually uses them — this keeps the JSONL shape uniform and makes back-of-envelope re-derivation possible without re-pulling from Tushare.

**Join semantics across endpoints (critical):**

A-share financial statements regularly receive **会计差错更正 / 追溯调整 (errata + retrospective restatements)**, so Tushare's `income` / `cashflow` / `balancesheet` / `fina_indicator` endpoints often return **multiple rows for the same `(ts_code, end_date)`** distinguished by `update_flag` (`'1'` = original, `'2'`+ = restated) and/or a later `ann_date`. Joining four endpoints on `(ts_code, end_date)` **without prior dedup** triggers a Cartesian-product explosion: one (ticker, quarter) with 2 versions in each of the 4 endpoints → 2^4 = 16 phantom rows. **This must not happen.** It would silently corrupt the H50b downstream data pool.

**Dedup-then-join pipeline (required, in this exact order):**

1. For each of the four endpoints independently, sort its DataFrame by `(ts_code, end_date, ann_date ASC, update_flag ASC)`.
2. `drop_duplicates(subset=['ts_code', 'end_date'], keep='last')` — keeps the latest filing for each (ticker, quarter). The `keep='last'` semantics depend on the sort in step 1; do not skip the sort.
3. After dedup, assert per-endpoint: `df.groupby(['ts_code', 'end_date']).size().max() == 1`. Raise on violation with the offending (ts_code, end_date, count) tuple.
4. `LEFT JOIN` the four deduped DataFrames on `(ts_code, end_date)`. The driver is `fina_indicator` (its row set defines JSONL row count).
5. After the join, assert `len(joined) == len(fina_indicator_deduped)`. A mismatch means the dedup failed — abort with diagnostic.

**ann_date reconciliation post-join:**

For each joined row, the four endpoints may carry different `ann_date` values (Tushare publishes restated statements at different times even when conceptually filed together):

- Compute `filing_date = MAX(ann_date)` across all four endpoints' contributions to that row.
- If `MAX(ann_date) - MIN(ann_date) > 7 days`, record the per-endpoint ann_date tuple in `data_quality_note` (e.g., `"ann_date_skew: fina_indicator=2024-04-30, income=2024-04-30, cashflow=2024-04-30, balancesheet=2024-10-31 — restated balance sheet detected"`).
- This is a real-world signal, not an error; preserve as audit metadata.

**Coverage tracking:** if a Tushare endpoint returns no row for a particular `(ts_code, end_date)` that exists in `fina_indicator`, all intermediates sourced from that endpoint are NULL for that row (with reason noted). The `fina_indicator` row stays — JSONL row count is driven by `fina_indicator`'s coverage.

**NULL handling (applies to every field above):**

- Any field that Tushare returns as `NaN` / `None` / missing must be serialized to JSON `null` (NOT `NaN`, which is non-standard JSON and will crash H50b's `json.load`). The script must explicitly convert `numpy.nan` / `pandas.NA` → Python `None` before `json.dumps`. Implementation tip: `df.replace({np.nan: None}).to_dict(...)` or a `dict` comprehension with `None if pd.isna(v) else v`.
- Every NULL field must add a corresponding line to `data_quality_note` describing the cause. Multi-cause notes concatenated by `; `.
- **Do not invent fields.** All required + intermediate fields above must come from Tushare `fina_indicator`. If a needed Tushare key is unavailable, document the gap in the coverage report and surface a smoke-time error.
- **Do not back-fill** with cross-period averaging or sector means.
- **Do not pull market-derived valuation fields** (PE/PB/dividend yield/market cap) — those remain banned per H45 PRD §Data Requirements.

**Inline data-quality assertions (must run during ingestion, fail-loud, not deferred to review):**

- `filing_date >= report_period` for every row. Tushare cannot legitimately announce a financial report before the period ended; any violation indicates Tushare upstream noise. Script must raise on the first such row (do not silently drop) and surface the offending (ticker, report_period, ann_date) in the error message. Add a `--allow-future-filing-anomalies` CLI flag for the rare case where the reviewer decides to accept and quarantine such rows into a separate `data/cn_pit/raw/h50a_anomalies.jsonl`; without the flag, abort.
- All identity fields (`ticker`, `code`, `report_period`, `filing_date`) must be non-NULL. NULL → abort.
- `report_period` parseable as date and falls within `[2019-10-01, today]`. Out-of-range → abort.

## Hard Prohibitions

- Do not modify `data/cn_pit/fundamentals.jsonl` (preserve H28 baseline file; H50a writes an additive parallel file).
- Do not modify `data/cn_pit/universe_h30_candidate.jsonl` or any H30/H38/H47/H49a artifact.
- Do not modify `backtest/experiments/fundamental_backtest.py` or `scripts/h42_strategy_redesign_search.py`.
- Do not modify production trading config.
- Do not place live orders.
- Do not print or store the Tushare token.
- Do not author commits as `codex` or `claude-code`.
- Do not use yfinance, eastmoney, sina, or any non-Tushare source for the new fields.
- Do not back-fill missing fields. If Tushare returns NULL, the JSONL row records NULL with a documented reason.
- Do not loosen `tests/test_validate_hxx_artifacts.py`.
- V2-specific: do NOT delete, rebuild, or modify the V1 `data/cn_pit/raw/h50a_tushare_fina_indicator/` cache. V2 reads it as-is. The 481 files there are the V1 ingestion's recorded state and must remain immutable.
- V2-specific: do NOT silently drop the hard-field gate to <85%. If Tushare data is even worse than V1 measured, surface the failure clearly and stop.

## Period Range

Fetch `start_date=20191001` (Q4 2019) → `end_date=20260331` (latest quarter that has filed before today). This covers every backtest window in use (cal_2024, h1_2025, h2_2025, ytd_2026, deploy_2025_2026) with a 1-year prior buffer for as-of-date computation.

## Smoke Command

```bash
python scripts/h50a_build_tushare_pit_quality.py \
  --universe data/cn_pit/universe_h30_candidate.jsonl \
  --limit 5 \
  --start 20240101 \
  --end 20240630 \
  --raw-dir /tmp/h50a_raw_smoke \
  --output-jsonl /tmp/h50a_quality_smoke.jsonl \
  --output-coverage /tmp/h50a_coverage_smoke.json \
  --output-report /tmp/h50a_report_smoke.md
```

Expected smoke result:

- Exits 0.
- Fetches a 5-ticker × 2-quarter sample **from all four endpoints** (so the smoke exercises the new income/cashflow/balancesheet path; do not let `--limit 5` skip the new endpoints).
- Smoke coverage JSON must show all six intermediates with non-null counts > 0 (proves the new endpoint wiring works end-to-end).
- Writes disposable artifacts to `/tmp/`.
- Does not touch `data/cn_pit/` or `reports/`.

## Full Command

```bash
python scripts/h50a_build_tushare_pit_quality.py
```

Expected full result:

- Exits 0.
- Fetches all 481 universe tickers × all periods in the H50a date range.
- Writes `data/cn_pit/fundamentals_h50a_pit_quality.jsonl`, coverage JSON, and Markdown report.
- Per-ticker raw cache files live in `data/cn_pit/raw/h50a_tushare_fina_indicator/` (one per ticker, makes the script resumable).

Wall clock estimate: 15–40 min depending on Tushare rate limits and cache hits.

**Rate-limit / retry policy (required, not optional — applies identically to all four endpoints):**

- Per-(ticker, endpoint) raw cache layout (see Outputs). For each endpoint, if the ticker's cache file is present AND covers the requested date range, skip the fetch. V2 inherits the V1 `fina_indicator` cache verbatim (481 files) and only needs to fetch the three new endpoints fresh.
- All four endpoints (`fina_indicator`, `income`, `cashflow`, `balancesheet`) are rate-limited (Tushare typical: 500 calls/min paid, lower free). The script must apply the SAME backoff policy uniformly:
  - Catch HTTP 429 / Tushare `error_code` indicating rate-limit → **exponential backoff with jitter**: initial 2s sleep, doubling each retry, 60s cap, max 5 retries per (ticker, endpoint), then mark that (ticker, endpoint) as failed in coverage JSON (do not silently drop).
  - Hard-cap base call rate at 5 calls/sec ACROSS all endpoints combined (a single counter, not per-endpoint — Tushare bills total calls).
  - On any non-rate-limit error (network, schema mismatch), log and continue to the next (ticker, endpoint); do NOT abort the whole run for one failure.
- `fetch_failures` in coverage JSON becomes a list of `{ticker, endpoint, reason}` tuples (not just `{ticker, reason}` — V2 must surface which endpoint failed). Cross-endpoint resumability: re-running V2 should only re-attempt failed (ticker, endpoint) pairs.
- Fetch order per ticker: `fina_indicator` (already cached for all 481 in V1) → `income` → `cashflow` → `balancesheet`. Cache one endpoint fully before starting the next so a mid-run interruption leaves at most one endpoint partial.

## Coverage Acceptance

- `ticker_coverage_pct >= 0.98` — at least 98% of universe tickers must have ≥1 row in the output.
- `period_count >= 16` per ticker on average (over the 6.5-year window, ~26 quarters expected; tolerate IPO-after-2019 and delisted-before-now reductions).

**Per-field coverage gates (V2-revised thresholds with rationale):**

- **Hard fields** (`roe`, `roa`, `gross_margin`, `current_ratio`, `debt_to_equity`) — each must be non-null in ≥ **85%** of all rows.
  - V1 measured: roe 98.9%, roa 85.9%, gross_margin 85.7%, current_ratio 88.9%, debt_to_equity 99.2%. The V1 90% threshold was set without empirical basis; observed Tushare `fina_indicator` ratio coverage runs 85–99% with the binding floor at gross_margin / roa around 85.7%. V2's gross_margin fallback derivation should push gross_margin above 90%, but roa and current_ratio remain at Tushare's native ratio coverage.
  - V2 keeps the **85% floor as the gate** to align with empirical Tushare coverage. Any field that lands below 85% in the V2 run is a real BLOCKER (not a threshold-calibration issue).
- **Soft (cash-flow) fields** (`operating_cash_flow_to_revenue`, `free_cash_flow`, `accruals_ratio`) — each must be non-null in ≥ **50%** of all rows.
  - V1 measured: ocf_to_or 99.8%, fcff 85.3%, accruals_ratio 0% (no intermediates available). V2 should bring accruals_ratio to ~85% (driven by the floor of `_net_income` / `_net_cashflow_op` / `_total_assets` coverage from the three new endpoints).
- **Intermediates** (V2-new) — each of `_net_income`, `_net_cashflow_op`, `_total_assets`, `_op_income`, `_total_revenue`, `_total_cogs` must be non-null in ≥ **85%** of all rows. If any intermediate lands below 85%, that's a Tushare endpoint coverage issue that must be surfaced in the coverage JSON under `intermediate_coverage_anomalies` and the binding derived score-field (e.g., accruals_ratio for `_net_income`/`_net_cashflow_op`/`_total_assets`) is expected to land at or below the intermediate floor.

**ROE overlap cross-check (V2-revised join semantics):**

V1 reported "100% within tolerance" but on **0 overlap rows** — vacuously true. Root cause: existing `fundamentals.jsonl` stores annual filings (e.g., `report_period = "2023-12-31"`) while H50a fetches all quarterly periods (e.g., `"2023-03-31"`, `"2023-06-30"`, `"2023-09-30"`, `"2023-12-31"`). The naive (ticker, report_period) join only matches the calendar year-end period.

V2 must compare ROE on the year-end periods only:

- Subset H50a rows to `report_period` matching pattern `YYYY-12-31`.
- INNER JOIN to existing `fundamentals.jsonl` on (ticker, report_period).
- Expect ~300 (tickers) × ~3 (annual filings) = ~900 overlap rows. If overlap row count is < 100, the join is still broken — surface as a BLOCKER, do not silently let "100% within tolerance" stand on a small denominator.
- Compute `|H28_roe - H50a_roe_waa|`. Gate: ≤ 0.5 pp delta for ≥ 95% of overlap rows. Larger deltas listed in `roe_overlap_anomalies` with `(ticker, period, H28_roe, H50a_roe)` tuples — these are not necessarily wrong (different data vintage / different ROE methodology: H28 used THS annual, H50a uses Tushare `roe_waa`) but need surfacing for human review.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h50a
pytest tests/test_h50a_build_tushare_pit_quality.py tests/test_validate_hxx_artifacts.py -q
python scripts/validate_ledger_consistency.py --strict
git status --short \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/fundamentals.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/sector_metadata_sw_l1.csv \
  backtest/experiments/fundamental_backtest.py \
  scripts/h42_strategy_redesign_search.py
```

Last command must print nothing.

Add `data/cn_pit/raw/h50a_tushare_*/` to `.gitignore` (wildcard covers all four endpoint cache directories: `fina_indicator`, `income`, `cashflow`, `balancesheet`; the raw caches should not enter git history).

## Acceptance Gate

- [ ] PIT sources limited to Tushare: `fina_indicator` (ratios) + `income` + `cashflow` + `balancesheet` (raw line items for intermediates). No other provider.
- [ ] All schema fields present in every output row (NULL allowed only when Tushare returns NULL, with documented reason).
- [ ] `data/cn_pit/fundamentals.jsonl` untouched (H28 baseline preserved).
- [ ] V1 `data/cn_pit/raw/h50a_tushare_fina_indicator/` cache untouched (481 files preserve V1 state).
- [ ] Coverage thresholds met: ticker ≥98%, hard fields ≥85%, soft (cash-flow) fields ≥50%, intermediates ≥85% each.
- [ ] `accruals_ratio` non-null in ≥ 50% of rows (the V1 blocker; V2's main success criterion).
- [ ] ROE overlap: ≥100 overlap rows (proves year-end join works) AND ≤0.5 pp delta for ≥95% of overlap rows.
- [ ] `h50a` registered in `scripts/validate_hxx_artifacts.py`; `validate_h50a` enforces all of the above.
- [ ] Tests cover: schema validation (with 6 intermediates), NULL-with-reason rule, ROE year-end cross-check, coverage threshold violation behavior, fetch_failures schema (now `{ticker, endpoint, reason}`), cross-endpoint resumability.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Report Contents

`reports/h50a_pit_quality_ingestion_report.md` must include:

- One-line objective.
- Provenance block (provider, endpoint, date range, snapshot timestamp).
- Coverage summary: per-ticker, per-period, per-field tables.
- Field-level NULL distribution (which fields are most often NULL; suggests which H50b score components have weakest data).
- ROE overlap analysis: count of rows in overlap, distribution of |H28 ROE − H50a ROE|, list of anomalies.
- Top 10 tickers by row count and bottom 10 by row count (sanity for newly-listed or delisted names).
- Verdict: `CANDIDATE_DATASET` if coverage passes; otherwise list blockers.
- Note: H50a is a data slice, not a strategy promotion.

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- Was the inline `filing_date >= report_period` assertion actually wired into the ingestion loop (not just left to the reviewer to spot)?
- Are NULL fields consistently accompanied by a reason note in `data_quality_note`?
- Are `NaN` → `None` conversions applied before `json.dumps` (no `NaN` strings in the JSONL)?
- Does the script honor the rate-limit policy (exponential backoff with jitter, max 5 retries, 5-call/sec base cap)?
- Are the `_net_income` / `_net_cashflow_op` / `_total_assets` intermediates actually persisted in every row where `accruals_ratio` is non-NULL (so the derivation can be audited later)?
- Could the ROE overlap cross-check hide a systematic data-vintage drift (e.g., Tushare restates older periods)?
- Are tests deterministic and free of network calls?

## Closure Note

Record final verdict in `docs/strategy-optimization-sync.md`. State the next dependency: H50b Quality-Value composite redesign is unblocked once H50a is closed. The H50b search will fold ROE/ROA/margins/liquidity/cash-flow into a redesigned `ValueScore` and run a constrained grid (smaller than H42 because the alpha signal is the new variable, not the param sweep).
