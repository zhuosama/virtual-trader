# H52d — CSI500 PIT Quality Metrics Ingestion (Axis-Flip per period)

## Context

H52a/b/c built the CSI500 universe (1074 tickers), sector metadata (99.91% mapped, 31 SW L1 industries), and daily fact data (1075 cols × 1544 rows qfq prices, 88 MB long-format liquidity, 9/9 gates pass). H52d completes the H52 data foundation by ingesting PIT fundamentals — the CSI500 analogue of H50a V2.

H50a V2 was per-ticker fetch (481 H30 tickers × 4 endpoints = ~1924 calls). H52d applies the H52 architectural shift: **axis-flip by `period`** so each call fetches the entire A-share market for one quarter end. Call count collapses to **~104** (4 endpoints × 26 quarters) — a 18× reduction. Total wall ~30 sec pure network.

The H50a V2 dedup-then-join 5-step pipeline (sort by (ts_code, end_date, ann_date ASC, update_flag ASC) → drop_duplicates(keep='last') → assert per-endpoint size==1 → LEFT JOIN → assert len match) is non-negotiable and applies identically here — axis flip changes the query shape, not the dedup semantics. A naive LEFT JOIN without dedup at the per-period level still triggers 2^4 = 16-fold row inflation per restated ticker-quarter.

## Objective

Ingest PIT-safe per-filing-period quality metrics from 4 Tushare endpoints (`fina_indicator`, `income`, `cashflow`, `balancesheet`) for every H52a CSI500 unique ticker, covering 2019-10-01 → 2026-03-31. Output a parallel PIT fundamentals JSONL file with 10 score-component fields + 6 audit intermediates per (ticker, report_period) row. Persists the H50a V2 schema verbatim so downstream H52f search can swap the source path and reuse all scoring logic without modification.

## Inputs

- `data/cn_pit/universe_h52a_csi500.jsonl` (1074 unique tickers; H52a output)
- Tushare token (standard chain)
- Tushare endpoints (all 4 use the same `period=YYYYMMDD` axis):
  - `fina_indicator` — derived financial ratios (ROE/ROA/margins/liquidity/cash-flow ratios)
  - `income` — income statement raw line items (revenue, cogs, operate_profit, n_income, total_revenue)
  - `cashflow` — cash-flow statement (n_cashflow_act)
  - `balancesheet` — balance sheet (total_assets)
- H50a V2 reference for cross-validation: `data/cn_pit/fundamentals_h50a_pit_quality.jsonl` + `fundamentals_coverage_h50a.json` (read-only; used for ROE overlap sanity check)
- `data/cn_pit/fundamentals.jsonl` (H28 baseline, read-only — NOT used for ROE check; H50a is the better comparator since both are Tushare-sourced)

## Outputs

- `scripts/h52d_build_csi500_pit_quality.py` — ingestion script
- `data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl` — **PINNED schema matches H50a V2's `fundamentals_h50a_pit_quality.jsonl` exactly** (10 score fields + 6 intermediates + identity/provenance fields). Downstream H52f loads this file with the SAME loader code as H50a (file path swap only).
- `data/cn_pit/fundamentals_coverage_h52d.json`
- `reports/h52d_csi500_pit_quality_ingestion_report.md`
- `tests/test_h52d_build_csi500_pit_quality.py`
- `scripts/validate_hxx_artifacts.py` — register `h52d` with `validate_h52d` checker.
- Raw cache (gitignored): `data/cn_pit/raw/h52d_tushare_{fina_indicator,income,cashflow,balancesheet}/<period_YYYYMMDD>.csv` (one file per (endpoint, period) = 104 files total). Add wildcard `data/cn_pit/raw/h52d_tushare_*/` to `.gitignore` BEFORE the full run.

## Hard Prohibitions

- Do NOT modify `data/cn_pit/fundamentals.jsonl` (H28 baseline; SHA256-protected per memory).
- Do NOT modify `data/cn_pit/fundamentals_h50a_pit_quality.jsonl` or `fundamentals_coverage_h50a.json` (H50a artifacts; H30 pipeline depends on them stable).
- Do NOT modify `data/cn_pit/universe_h52a_csi500.jsonl` or `universe_snapshots_h52a_csi500.jsonl` (H52a output).
- Do NOT modify `data/cn_pit/sector_metadata_h52b_csi500.csv` or `sector_coverage_h52b.json` (H52b output).
- Do NOT modify `data/cn_pit/prices_h52c_csi500_qfq.csv` or `liquidity_h52c_csi500_daily_amount.csv` or `price_coverage_h52c.json` (H52c output).
- Do NOT modify SHA256-protected H28 baselines (universe.jsonl, universe_snapshots.jsonl).
- Do NOT modify any H30/H47/H49a/H50a/H51a artifact.
- Do NOT use yfinance / eastmoney / sina / any non-Tushare source.
- Do NOT back-fill missing fields with cross-period averaging or sector means.
- Do NOT pull market-derived valuation fields (PE/PB/dividend yield/market cap) — banned per H45 PRD.
- Do NOT modify production trading config; do not place live orders.
- Do NOT print or store the Tushare token value.
- Do NOT author commits as codex or claude-code.
- Do NOT loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT skip the dedup-then-join 5-step pipeline. Naive LEFT JOIN without per-endpoint dedup triggers Cartesian explosion (2^4 = 16-fold inflation per restated quarter).
- Do NOT use per-ticker queries — H52d's whole point is per-period axis-flip. Per-ticker would defeat the architecture.

## Design Decisions (locked unless overridden before dispatch)

### D1. Axis-Flip Architecture (per period, 4 endpoints, **with mandatory pagination loop**)

Tushare's whole-market financial-statement endpoints (`income`, `cashflow`, `balancesheet`) cap responses at **5000 rows per call**. CN A-share total exceeds **5300 tickers** today. A single `pro.income(period=X)` call SILENTLY TRUNCATES — the response looks fine but is missing ~300 stocks. Filtering to the 1074 H52a universe from a truncated set will randomly miss tickers and quietly fail the 98% coverage gate.

**Mandatory pagination loop per (endpoint, period) — non-negotiable:**

```python
PAGE_SIZE = 5000  # Tushare hard cap for these endpoints

def fetch_full_market(endpoint_fn, period):
    """Paginated full-market fetch. Concat all pages until a partial page signals exhaustion."""
    pages = []
    offset = 0
    while True:
        df = endpoint_fn(period=period, limit=PAGE_SIZE, offset=offset)
        if df is None or df.empty:
            break
        pages.append(df)
        if len(df) < PAGE_SIZE:
            break  # last page (partial); stop
        offset += PAGE_SIZE
    return pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()

for period in periods:
    if not all_cached(period):
        df_fi = fetch_full_market(pro.fina_indicator, period)   # ~3000-4500 rows; usually 1 page
        df_in = fetch_full_market(pro.income,         period)   # ~5300 rows; 2 pages required
        df_cf = fetch_full_market(pro.cashflow,       period)   # ~5300 rows; 2 pages required
        df_bs = fetch_full_market(pro.balancesheet,   period)   # ~5300 rows; 2 pages required
        save_raw_caches(period, df_fi, df_in, df_cf, df_bs)
```

**Defensive assertion after each pagination loop completes:** `assert len(df_in) > 5000` (or whatever threshold catches "exactly 5000 = suspected truncation"). The loop's `len(df) < PAGE_SIZE` exit condition correctly handles partial pages, but Hermes must also assert the final concat shape looks like a full A-share market (> 5100 rows for the three statement endpoints; fina_indicator may be smaller since not all firms file ratios). If the final shape suggests truncation, add to `fetch_failures` with reason `"pagination_exhaustion_suspected"`.

**Raw cache structure with pagination:** save the CONCATENATED full-market DataFrame per (endpoint, period), NOT the individual pages. One file per (endpoint, period) preserves the resumable-cache contract. Inside the saved CSV, include a `_page_count` column or sidecar metadata file recording how many pages went into the concat, for audit.

**Updated call count:** 4 × 26 × ~2 pages = **~208 API calls** (the 3 statement endpoints need 2 pages each per period; fina_indicator usually 1). 5 calls/sec hard cap → **~42 sec pure network**. Still trivial vs the per-ticker baseline (~1924 calls); pagination doubles the call count but stays well under H52a's 90 / H52b's 31 / H52c's 3187.

Total: **~208 API calls** (4 endpoints × 26 periods × ~2 pages average). Resumable per-(endpoint, period) concatenated cache.

### D2. Filter to H52a Universe (post-fetch)

After each raw response is loaded:

```python
h52a_ts_codes = load_h52a_ts_codes()   # 1074 tickers in Tushare format
df_filtered = df_raw[df_raw.ts_code.isin(h52a_ts_codes)]
```

Filter happens AFTER caching the raw response (cache preserves the full-market response for audit). Filter is in-memory only.

### D3. Dedup-then-Join 5-Step Pipeline (CRITICAL — copied verbatim from H50a V2)

A-share financial statements receive 会计差错更正 / 追溯调整, so per-period responses often contain multiple rows for the same `(ts_code, end_date)` distinguished by `update_flag` / `ann_date`. Naive LEFT JOIN of 4 endpoints without dedup → 2^4 = 16 phantom rows per restated ticker-quarter. **This must not happen.**

For each period, execute in this exact order:

1. For each endpoint's filtered DataFrame: sort by `(ts_code, end_date, ann_date ASC, update_flag ASC)`.
2. `drop_duplicates(subset=['ts_code', 'end_date'], keep='last')`.
3. Assert per-endpoint: `df.groupby(['ts_code', 'end_date']).size().max() == 1`. Raise on violation with offending tuple.
4. LEFT JOIN four deduped DataFrames on `(ts_code, end_date)`, with `fina_indicator` as the driver (its row set defines per-period JSONL row count).
5. Assert `len(joined) == len(fina_indicator_deduped_for_this_period)`. Mismatch → abort with diagnostic.

**Post-join `ann_date` reconciliation:** `filing_date = MAX(ann_date)` across the 4 endpoints. If `MAX - MIN > 7 days`, record per-endpoint ann_date tuple in `data_quality_note` (e.g., `"ann_date_skew: fi=2024-04-30, in=2024-04-30, cf=2024-04-30, bs=2024-10-31 — restated balance sheet detected"`).

### D4. Field Pins (PINNED from H50a V2; do NOT improvise)

**Final score-component fields:**

| Field | Tushare key | Fallback | Notes |
|---|---|---|---|
| `roe` | `fina_indicator.roe_waa` | none | weighted-average ROE, pinned (NOT `roe` / `roe_dt` / `roe_avg`) |
| `roa` | `fina_indicator.roa` | none | |
| `gross_margin` | `fina_indicator.grossprofit_margin` (primary) | if NULL: `(_total_revenue - _total_cogs) / _total_revenue * 100` | derived fallback (H50a V2 lifted gross_margin coverage from 85.7% to 99.2%) |
| `operating_margin` | `fina_indicator.op_of_gr` (primary) | if NULL: `_op_income / _total_revenue * 100` | fallback per H50a V2 |
| `current_ratio` | `fina_indicator.current_ratio` | none | |
| `quick_ratio` | `fina_indicator.quick_ratio` | none | |
| `debt_to_equity` | `fina_indicator.debt_to_eqt` | none | |
| `operating_cash_flow_to_revenue` | `fina_indicator.ocf_to_or` | none | |
| `free_cash_flow` | `fina_indicator.fcff` | none | softer; not always reported |
| `accruals_ratio` | **derived**: `(_net_income - _net_cashflow_op) / _total_assets` | requires all 3 intermediates non-NULL; else field NULL with reason | Sloan anomaly: **raw signed value, do NOT take abs()** |

**Intermediate audit-only fields (underscore-prefixed; persisted in every row regardless of derivation use):**

| Field | Source endpoint | Tushare key |
|---|---|---|
| `_net_income` | `income` | `n_income` |
| `_net_cashflow_op` | `cashflow` | `n_cashflow_act` |
| `_total_assets` | `balancesheet` | `total_assets` (PINNED; not `t_assets`) |
| `_op_income` | `fina_indicator` (primary) → `income.operate_profit` (fallback) | `op_income` (fina_indicator) then `operate_profit` (income) |
| `_total_revenue` | `income` | `total_revenue` |
| `_total_cogs` | `income` | `total_cogs` |

### D5. NULL / NaN Handling

- `numpy.nan` / `pandas.NA` / missing → Python `None` BEFORE `json.dumps`. NO `NaN` strings in JSONL (H50a V1 reminder: pandas serializes NaN as the string `"NaN"` which breaks downstream `json.load`).
- Every NULL field MUST add a corresponding line to `data_quality_note` (multi-cause joined by `; `).
- Implementation tip: `df.replace({np.nan: None}).to_dict(orient='records')` or per-row comprehension with `None if pd.isna(v) else v`.

### D6. Inline Data-Quality Assertions (fail-loud during ingestion)

- `filing_date >= report_period` for every joined row. First violation → raise with `(ticker, end_date, ann_date)`. Provide `--allow-future-filing-anomalies` flag that quarantines such rows to `data/cn_pit/raw/h52d_anomalies.jsonl` instead of aborting.
- Identity fields (`ticker`, `code`, `report_period`, `filing_date`) non-NULL → else abort.
- `report_period` parseable as date AND falls within `[2019-10-01, today]`.

### D7. Rate-Limit / Retry / Cache Policy (4 endpoints unified, pagination-aware)

- Per-(endpoint, period) **concatenated** raw cache: skip the entire D1 pagination loop if cache file exists AND non-empty. (The cache file stores the merged full-market DataFrame; not per-page splits.)
- HTTP 429 / Tushare error_code rate-limit → exponential backoff with jitter (initial 2s, doubling, 60s cap, max 5 retries **per page within the pagination loop**) → then mark `{endpoint, period, offset, reason}` in `fetch_failures` and break the pagination loop early.
- Hard-cap base rate at 5 calls/sec ACROSS all endpoints AND all pages combined (single counter; Tushare bills total).
- Per-page failure: retry the same page up to 5 times; persistent failure → record failure tuple, abort the period's pagination loop for that endpoint, log a `"pagination_partial"` warning in coverage JSON. Do NOT save a partial cache file (incomplete data must not look complete).
- Fetch order per period: `fina_indicator` → `income` → `cashflow` → `balancesheet` (so a mid-run interruption leaves at most one endpoint partial per period).
- Page exhaustion detection: ANY page returning exactly `PAGE_SIZE` rows + the next page returning empty is normal. ANY page returning exactly `PAGE_SIZE` with `offset > 0` where the loop continues is also normal. The pathological case (which the brief's defensive assertion catches) is a single-page response of exactly 5000 rows that DIDN'T trigger pagination — likely caused by a coding bug forgetting the `limit + offset` parameters.

## Provenance Block (in `fundamentals_coverage_h52d.json`; validate_h52d enforces)

```json
{
  "provenance": {
    "provider": "tushare:fina_indicator+income+cashflow+balancesheet",
    "endpoints": ["fina_indicator", "income", "cashflow", "balancesheet"],
    "axis": "period",
    "pagination": "limit+offset",
    "page_size": 5000,
    "universe_source": "data/cn_pit/universe_h52a_csi500.jsonl",
    "date_range": "20191001 -> 20260331",
    "snapshot_timestamp": "<UTC ISO>"
  },
  "universe_ticker_count": 1074,
  "processed_ticker_count": <int>,
  "ticker_coverage_pct": <float>,
  "total_rows": <int>,
  "period_count": <int, expect 26>,
  "avg_periods_per_ticker": <float>,
  "per_field_non_null_pct": {
    "roe": <float>, "roa": <float>, "gross_margin": <float>, "operating_margin": <float>,
    "current_ratio": <float>, "quick_ratio": <float>, "debt_to_equity": <float>,
    "operating_cash_flow_to_revenue": <float>, "free_cash_flow": <float>, "accruals_ratio": <float>,
    "_net_income": <float>, "_net_cashflow_op": <float>, "_total_assets": <float>,
    "_op_income": <float>, "_total_revenue": <float>, "_total_cogs": <float>
  },
  "hard_field_min_pct": <float>,
  "soft_field_min_pct": <float>,
  "intermediate_min_pct": <float>,
  "gates": {
    "ticker_coverage_ge_98pct": <bool>,
    "hard_fields_ge_85pct": <bool>,
    "soft_fields_ge_50pct": <bool>,
    "intermediates_ge_85pct": <bool>,
    "accruals_ratio_ge_50pct": <bool>,
    "h50a_overlap_ge_99pct": <bool>
  },
  "op_margin_fallback_count": <int>,
  "gross_margin_fallback_count": <int>,
  "ann_date_skew_count": <int>,
  "h50a_overlap": {
    "overlap_count": <int>,
    "pct_within_tolerance": <float>,
    "anomaly_count": <int>,
    "anomalies": [{"ticker": "...", "report_period": "...", "h50a_roe": <float>, "h52d_roe": <float>}]
  },
  "fetch_failures": [{"endpoint": "...", "period": "...", "reason": "..."}],
  "anomalies_quarantined": <int>,
  "verdict": "CANDIDATE_DATASET | BLOCKED"
}
```

`validate_h52d` asserts:
- `provenance.provider == "tushare:fina_indicator+income+cashflow+balancesheet"`
- `provenance.axis == "period"`
- `provenance.pagination == "limit+offset"` (proves pagination loop wired; absent value → BLOCKER fix not applied)
- `provenance.universe_source == "data/cn_pit/universe_h52a_csi500.jsonl"` (regression hook — must NOT silently use H30 universe)
- `universe_ticker_count == 1074`
- All 6 `gates.*` are `true`
- `period_count >= 24` (allow ~2-period tolerance below ~26 expected)
- `fetch_failures count <= 5` (axis-flip should have near-zero failure)
- All 16 fields present in `per_field_non_null_pct` (10 score + 6 intermediate)
- JSONL row count matches `total_rows`
- Sampled JSONL row has all 16 score+intermediate fields as either valid numbers or `null` (no `NaN` strings)
- **Per-period defensive check**: for at least 5 sampled periods, the cached `income` / `cashflow` / `balancesheet` raw files each have > 5100 rows OR sidecar metadata showing `page_count >= 2`. A single-page response of exactly 5000 rows fails this assertion (pagination loop missing).

## Coverage Acceptance

`CANDIDATE_DATASET` if ALL of:
- `ticker_coverage_pct >= 98.0`
- Hard fields (roe, roa, gross_margin, current_ratio, debt_to_equity) each ≥ **85%** non-null
- Soft fields (ocf_to_revenue, fcff, accruals_ratio) each ≥ **50%** non-null
- Each of 6 intermediates ≥ **85%** non-null
- `accruals_ratio` non-null ≥ **50%** (the V1 H50a blocker before V2 added the 3 raw-statement endpoints; H52d must repeat the V2 success)
- H50a ROE overlap: ≥99% match on year-end periods within ±0.5pp (since both H52d and H50a use same Tushare `roe_waa` source, expected ~100% match modulo Tushare's overnight restatements)
- `fetch_failures count <= 5`

`BLOCKED` otherwise — surface specific failing assertion + numerical value. Do NOT silently lower thresholds.

## H50a ROE Overlap Cross-Check (diagnostic; ≥99% gate)

Since both H50a (H30 universe) and H52d (CSI500 universe) source ROE from Tushare `fina_indicator.roe_waa`, overlap rows should match identically. Procedure:

1. Subset H52d rows where `report_period` ends in `-12-31` (year-end only).
2. INNER JOIN to H50a fundamentals on `(ticker, report_period)`.
3. Expect overlap ~50-200 rows (tickers that crossed between HS300 and CSI500 historically).
4. Compute `|h50a_roe - h52d_roe|`. Gate: ≥ 99% within ±0.5pp.
5. If overlap row count < 30 (too few to be a meaningful check), log "overlap insufficient — gate skipped" in coverage JSON and mark gate as `null` (not `false`). Validate_h52d treats `null` here as pass.

The diagnostic catches drift between H50a's per-ticker fetch and H52d's per-period axis-flip fetch. They should be identical mathematically; any drift suggests a code-level mistake.

## Smoke Command

```bash
python scripts/h52d_build_csi500_pit_quality.py \
  --universe data/cn_pit/universe_h52a_csi500.jsonl \
  --start 20231001 --end 20240630 \
  --raw-dir /tmp/h52d_raw_smoke \
  --output-jsonl /tmp/h52d_jsonl.jsonl \
  --output-coverage /tmp/h52d_cov.json \
  --output-report /tmp/h52d_rep.md
```

Expected smoke result:
- Exits 0.
- Fetches 3 periods (2023-12-31, 2024-03-31, 2024-06-30) × 4 endpoints = 12 calls.
- Smoke coverage JSON shows all 6 intermediates with non-null counts > 0 (proves all 4 endpoints wired).
- Sample JSONL row has 16 score+intermediate fields populated correctly.
- Does NOT touch `data/cn_pit/`, `reports/`, or any production path.

## Full Command

```bash
python scripts/h52d_build_csi500_pit_quality.py
```

~22 seconds pure network + Tushare throttling overhead; total wall ~3-8 min including smoke + tests + full + verification + closure write.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h52d
python scripts/validate_hxx_artifacts.py                                # all 17 artifacts (16 existing + h52d) must PASS
pytest tests/test_h52d_build_csi500_pit_quality.py tests/test_validate_hxx_artifacts.py -q
git status --short \
  data/cn_pit/fundamentals.jsonl \
  data/cn_pit/fundamentals_h50a_pit_quality.jsonl \
  data/cn_pit/fundamentals_coverage_h50a.json \
  data/cn_pit/universe_h52a_csi500.jsonl \
  data/cn_pit/sector_metadata_h52b_csi500.csv \
  data/cn_pit/prices_h52c_csi500_qfq.csv \
  data/cn_pit/liquidity_h52c_csi500_daily_amount.csv \
  data/cn_pit/universe.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/liquidity_h51a_daily_amount.csv
# Last command MUST print nothing — 10 protected files unchanged.

# Sanity check (inline):
python3 -c "
import json
rows_seen = 0
with open('data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl') as f:
    for line in f:
        r = json.loads(line)
        rows_seen += 1
        if rows_seen == 1:
            # Confirm 16-field schema + no NaN strings
            required = {'ticker','code','report_period','filing_date','source_url','source_provider','ingested_at',
                        'roe','roa','gross_margin','operating_margin','current_ratio','quick_ratio',
                        'debt_to_equity','operating_cash_flow_to_revenue','free_cash_flow','accruals_ratio',
                        '_net_income','_net_cashflow_op','_total_assets','_op_income','_total_revenue','_total_cogs'}
            missing = required - set(r.keys())
            assert not missing, f'missing fields in first row: {missing}'
            for k, v in r.items():
                assert v != 'NaN' and str(v).lower() != 'nan', f'NaN string in {k}: {v!r}'
            print(f'first row schema OK; {len(r)} fields present')
print(f'total rows: {rows_seen}')
"
```

## Acceptance Gate

- [ ] All 4 outputs exist (script, JSONL, coverage JSON, report).
- [ ] All 6 coverage gates pass (ticker / hard / soft / intermediates / accruals / H50a overlap).
- [ ] JSONL schema exactly matches H50a V2's `fundamentals_h50a_pit_quality.jsonl` (16 fields + 7 identity/provenance = 23 fields per row).
- [ ] H50a + H30 + H52a-c files all untouched.
- [ ] `validate_h52d` registered and passing.
- [ ] All 17 family validators PASS.
- [ ] Tests cover: per-period axis-flip fetch (4 endpoints), **pagination loop correctness** (synthetic 11000-row fixture across 3 pages: 5000 + 5000 + 1000 → assert concat yields 11000), dedup-then-join pipeline (synthetic update_flag fixture verifying no Cartesian explosion), NaN-to-None conversion, accruals_ratio raw-signed inversion (no abs), inline filing_date assertion behavior, H50a overlap join logic.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Closure Note

- Append H52d row to `docs/strategy-optimization-sync.md` under new `## H52d — CSI500 PIT Fundamentals Snapshot` heading: verdict, ticker_coverage_pct, per-field summary (hard/soft/intermediate min), accruals_ratio %, H50a overlap match %, fetch_failures count, fallback usage counts.
- Flip `docs/agents/next-slices.md` H52d entry to DONE; flip H52e (search framework smoke) from BLOCKED to OPEN (since all H52a-d dependencies now complete).

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- Is the per-period axis-flip actually fetching 4 endpoints per period, or has Hermes silently regressed to per-ticker (negating the architectural shift)?
- **Is the pagination loop actually present in `pro.income` / `pro.cashflow` / `pro.balancesheet` calls?** A single-page call without `limit + offset` parameters silently truncates at 5000 rows when CN A-share total is ~5300 — randomly losing ~300 stocks per period and silently failing the 98% coverage gate. The defensive `> 5100 rows OR page_count >= 2` assertion catches this; verify it actually fires under a truncation simulation.
- Is the dedup-then-join 5-step pipeline applied to EACH period response (post-concat) BEFORE the final pivot? Naive `pd.concat` of 4 endpoints' DataFrames + LEFT JOIN at the end would still Cartesian-explode if any period has restated rows.
- Is the H52a universe filter applied AFTER caching the raw response (so raw cache has full market data for audit)?
- Does `accruals_ratio` use raw signed value (Sloan), NOT `abs()`?
- Are NaN-to-None conversions applied at JSONL serialization time (no `NaN` strings in output)?
- Does the H50a ROE overlap check correctly handle the case where overlap_count < 30 (graceful degradation, not silent pass)?
- Does the ROE overlap check actually use `year-end periods only` (not all quarters)?
- Are tests deterministic and free of network calls?
