# Next Workflow Slices

These are the next independently-grabbable slices after H43.

## H44 — Artifact Consistency Validator

Type: AFK

Status: DONE

What to build:

Create a reusable validator for Hxx run JSON and Markdown reports. It should compare key counts, verdicts, gate-pass values, top-candidate metrics, and required artifact paths.

Acceptance criteria:

- Validates H42 JSON/report consistency.
- Fails on mismatched verdict, gate count, or candidate metrics.
- Can be extended to H39-H41 without rewriting the core.

Blocked by: Unblocked (H43 closed)

## H45 — Next Alpha PRD

Type: HITL

Status: DONE

What to build:

Write a PRD for the next alpha source instead of another local parameter grid. Candidate directions include sector/risk model, quality-score redesign, benchmark-relative objective, and production-grade price data requirements.

Acceptance criteria:

- Defines non-goals and deployment gates.
- Includes data requirements.
- Includes experiment design and OOS split.
- Explicitly rejects parameter-only tuning as the primary path.

Blocked by: Unblocked (H43 closed)

## H46 — Paper-Only Forward Monitor

Type: AFK

Status: DONE

What to build:

Create a paper-only forward monitor for rejected-but-interesting candidates such as H39/H42 best candidates. It must not place live orders or alter production config.

Acceptance criteria:

- Writes daily/weekly paper metrics.
- Tracks HS300 excess return, drawdown, trade count, losing streak, and gate status.
- Labels output as research-only.
- Registers any new JSON/Markdown artifact family in `scripts/validate_hxx_artifacts.py` before final closure.

Blocked by: Unblocked (H43-H45 closed)

## H47 — Production Price Rebuild

Type: HITL

Status: DONE

What to build:

Rebuild the full price matrix from one consistent adjusted provider, such as Tushare `pro_bar`, then rerun H42-or-later gates.

Acceptance criteria:

- Uses one consistent adjustment methodology.
- Includes HS300 benchmark from the same provider family.
- Produces a coverage report.
- Does not overwrite H38 research data.
- Registers any new JSON/Markdown artifact family in `scripts/validate_hxx_artifacts.py` before final closure.

Blocked by: Unblocked (H43-H45 closed), data-source decision

## H49a — SW L1 Industry Classification Ingestion

Type: HITL

Status: DONE

What to build:

Ingest Shenwan L1 industry classification from Tushare for every ticker in `data/cn_pit/universe_h30_candidate.jsonl`. Single snapshot, snapshot_date recorded for audit. See `docs/hermes-h49a-sw-industry-ingestion-task.md`.

Acceptance criteria:

- Uses Tushare `index_classify(level=L1, src=SW2021)` + `index_member` only.
- Writes `data/cn_pit/sector_metadata_sw_l1.csv`, `data/cn_pit/sector_coverage_h49a.json`, `reports/h49a_sw_industry_ingestion_report.md`.
- Mapped coverage ≥ 98%; unmapped tickers have reason recorded.
- Does not overwrite any H30/H38/H47 input artifact.
- Registers `h49a` in `scripts/validate_hxx_artifacts.py`.

Blocked by: Unblocked (H48 closed)

## H49b — Sector-Neutral Relative Strength Search

Type: AFK

Status: DONE

What to build:

Run a benchmark-relative-strength search with sector-neutral selection, using H47 unified-qfq prices + H49a SW L1 sector metadata. Acceptance gate follows H42, with explicit emphasis on `beat_HS300_windows` (the metric where H42/H48 failed). Brief to be drafted after H49a closes.

Acceptance criteria:

- Reuses H42 search framework where possible; no parameter-only re-grid over prior overlay families.
- Adds sector-aware overlays: per-sector top_k, sector_max_weight cap, sector-neutral equal-weight buckets.
- Reports an explicit H42/H48 vs H49b comparison.

Blocked by: H49a

## H50a — PIT Quality Metrics Ingestion

Type: HITL

Status: DONE (V2: CANDIDATE_DATASET, 100% ticker, 85.9% hard, 93.3% accruals)

What to build:

Ingest PIT-safe per-filing-period quality metrics from Tushare `fina_indicator` for every ticker in `data/cn_pit/universe_h30_candidate.jsonl`. Adds profitability (ROE/ROA/margins), balance-sheet (current/quick/D-to-E), and cash-flow fields (OCF/revenue, FCF, accruals) to a parallel PIT fundamentals file. Existing `fundamentals.jsonl` untouched. See `docs/hermes-h50a-pit-quality-metrics-ingestion-task.md`.

Acceptance criteria:

- Uses Tushare `fina_indicator` only.
- Writes `data/cn_pit/fundamentals_h50a_pit_quality.jsonl`, `data/cn_pit/fundamentals_coverage_h50a.json`, `reports/h50a_pit_quality_ingestion_report.md`.
- Ticker coverage ≥ 98%; hard-field coverage ≥ 90%; cash-flow ≥ 50%.
- ROE overlap vs existing fundamentals.jsonl differs by ≤ 0.5 pp for ≥ 95% of overlap rows (larger diffs documented).
- Does not modify `data/cn_pit/fundamentals.jsonl` or any H30/H38/H47/H49a input artifact.
- Registers `h50a` in `scripts/validate_hxx_artifacts.py`.

Blocked by: Unblocked (H49b closed)

## H50b — Quality-Value Composite Redesign Search

Type: AFK

Status: DONE

What to build:

Replace the current 2-feature `ValueScore` (ROE + D/E only) with a 4-component composite (profitability, balance-sheet, cash-flow, PIT-safe valuation) using H50a data. Each candidate must surface component-level contribution per held ticker. Search uses H42 framework + H47 prices + H49a sectors + new H50a fundamentals. Acceptance gate follows H42 verbatim; rank by `beat_HS300_windows` per H49b D6 pattern. Brief to be drafted after H50a closes.

Acceptance criteria:

- Reuses H42 search framework; does not modify it.
- New `ValueScore` exposes per-component scores for each selected ticker in the run JSON.
- Reports an explicit H42/H48/H49b vs H50b comparison, with `beat_HS300_windows` delta as the headline metric.
- Search grid is narrower than H42 (the alpha signal is the new variable, not the parameter sweep).

Blocked by: H50a

## H48 — Unified-QFQ H42 Strategy Rerun

Type: AFK

Status: DONE

What to build:

Rerun the H42 redesign search verbatim against the H47 unified-qfq price matrix and report whether any candidate flips from RESEARCH_ONLY to passing the H42 gate. See `docs/hermes-h48-unified-qfq-h42-rerun-task.md`.

Acceptance criteria:

- Uses `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (provenance proved in run JSON).
- Reuses `scripts/h42_strategy_redesign_search.py` without modifying its search logic, gate thresholds, or window definitions.
- Does not overwrite H42 originals or any H38–H47 input artifact.
- Registers `h48` in `scripts/validate_hxx_artifacts.py` with a price-source provenance check.
- Reports an explicit H42 vs H48 verdict comparison.

Blocked by: Unblocked (H47 closed)

## H51a — Risk Model ADTV Data Ingestion

Type: AFK

Status: DONE

What to build:

Ingest daily trading value (amount_rmb) and volume (vol_shares, ×100 from Tushare 手) from Tushare `daily` endpoint for all 481 H30 universe tickers, covering 2023-10-01 → 2026-05-21. Outputs a long-format CSV, coverage JSON, and report. See `docs/hermes-h51a-risk-model-data-task.md`.

Acceptance criteria:

- Uses Tushare `daily` endpoint only.
- Ticker coverage ≥ 98%; avg rows/ticker ≥ 600; ADTV computable ≥ 95% across H42 windows; fetch_failures ≤ 10.
- Writes `data/cn_pit/liquidity_h51a_daily_amount.csv`, `data/cn_pit/liquidity_coverage_h51a.json`, `reports/h51a_daily_amount_ingestion_report.md`.
- Does not modify any existing dataset, run JSON, or report.
- Registers `h51a` in `scripts/validate_hxx_artifacts.py`.

Blocked by: Unblocked (H50b closed)

## H51b — Risk Model Overlay Search

Type: AFK

Status: DONE

What to build:

Run a risk-model overlay search that constrains single-name position sizing using the H51a ADTV dataset (liquidity participation cap), adds volatility-scaled targets, and enforces a minimum active name constraint. Uses H42 search framework + H47 prices + H49a sectors + H50a fundamentals + H51a liquidity.

Acceptance criteria:

- Reuses H42 search framework where possible.
- Adds liquidity cap overlay (`max_position_frac_of_adtv`).
- Reports explicit H42/H48/H49b/H50b vs H51b comparison.
- Registers `h51b` in `scripts/validate_hxx_artifacts.py` before closure.

Blocked by: H51a

## H52a — CSI500 PIT Universe History

Type: HITL

Status: DONE

What to build:

Ingest historical monthly CSI500 (`000905.SH`) constituent weights via Tushare `index_weight` axis-flipped by `trade_date` (~90 API calls vs ~10k under per-ticker loop). See `docs/hermes-h52a-csi500-universe-history-task.md`. Schema pinned to `universe_h30_candidate.jsonl` shape for downstream backtester compatibility.

Acceptance criteria:

- Total snapshots ≥ 80; min members/snapshot ≥ 480; unique tickers ≥ 700.
- Schema of both jsonl files matches H30 reference shape exactly.
- Does not modify any H28/H30/H47/H49a/H50a/H51a artifact.
- Registers `h52a` in `scripts/validate_hxx_artifacts.py`.

Blocked by: Unblocked (H30 exhausted; decision 2026-05-24 per strategy-optimization-sync.md)

## H52b — CSI500 SW L1 Sector Metadata

Type: HITL

Status: DONE

What to build:
 
Ingest SW L1 sector classification for the H52a CSI500 universe pool (analogous to H49a but for CSI500 tickers). One-snapshot fetch from Tushare `index_classify` + `index_member`.

Acceptance criteria:

- ≥95% of H52a unique tickers mapped to a SW L1 industry.
- Schema matches `sector_metadata_sw_l1.csv` exactly.
- Registers `h52b` in `scripts/validate_hxx_artifacts.py`.

Blocked by: Unblocked (H52a done)

## H52c — CSI500 Daily Fact Data (Prices + ADTV)

Type: HITL

Status: DONE

What to build:

Two-endpoint axis-flip per `trade_date` (~1500 days × 2 = ~3000 calls): `daily` for raw close+vol+amount; `adj_factor` for daily复权因子. Local join → compute qfq close. Persists both H47-style prices CSV (qfq close per trade_date × CSI500 ticker) and H51a-style liquidity CSV (long-format amount_rmb + vol_shares).

Acceptance criteria:

- 99%+ trade-date coverage 2020-01-02 → 2026-05-21 for the H52a universe.
- Median implied price ∈ [0.5, 5000] RMB (sanity unit check, mirrors H51a V2).
- Per-window ADTV computable ≥ 95% across H42 windows.
- Registers `h52c` in `scripts/validate_hxx_artifacts.py`.

Blocked by: Unblocked (H52a and H52b both complete)

## H52d — CSI500 PIT Fundamentals

Type: HITL

Status: DONE (V2 — per-ticker axis after Tushare financial endpoint限制; 1.5 calls/sec rate, 0 fetch_failures, 17/17 validators; see sync doc)

What to build:

4-endpoint axis-flip per `period` (~26 quarters × 4 = ~104 calls): `fina_indicator` + `income` + `cashflow` + `balancesheet`. Apply H50a V2 dedup-then-join 5-step pipeline (sort + drop_duplicates + assert + LEFT JOIN + len-assert) per period response.

Acceptance criteria:

- Ticker coverage ≥ 98% over H52a unique tickers.
- Hard fields ≥ 85%; soft (cash-flow) fields ≥ 50%; intermediates ≥ 85%.
- accruals_ratio non-null ≥ 50%.
- Registers `h52d` in `scripts/validate_hxx_artifacts.py`.

Blocked by: H52c (H52a and H52b complete; H52c is now DONE — H52d is unblocked)

## H52e — CSI500 Search Framework Smoke Test

Type: AFK

Status: DONE (SMOKE_PASS 2026-05-24, 11.6s wall, all 3 subs RESEARCH_ONLY, sha256 audit PASS, 18/18 validators)

What to build:

Wire H52a-d datasets through existing H42 search + H50b scorer + H51b sizing scripts with ZERO logic changes — verify schema compatibility and end-to-end pipeline health on CSI500. Single smoke run, top-k=1, stage-b-limit=3. No production run.

Acceptance criteria:

- H42 / H50b / H51b scripts load CSI500 data without raising.
- One smoke backtest completes; result JSON has `verdict` field.
- No modification to H42, H50b, H51b scripts.

Blocked by: H52c, H52d (H52a and H52b complete)

## H52f — CSI500 H42→H51b Full Pipeline Rerun

Type: AFK

Status: DONE (2026-05-24 — CSI500_REGRESSION verdict)

What to build:

Execute the full H42→H48→H49b→H50b→H51b search chain on the CSI500 universe (data from H52a-d). Acceptance gate stays HS300-only (H42 verbatim, `beat_HS300_windows`); `beat_CSI500_windows` is diagnostic-only field in run JSON.

Acceptance criteria:

- 4 sub-runs produce H52f-prefixed JSON + report artifacts. ✅
- Each sub-run carries `data_sources` provenance with H52a-d sha256. ✅
- Comparison report shows H30 vs CSI500 across all 4 sub-pipelines. ✅
- H52e gap closer: h51b adtv_liquidity sha256 == file_sha256(CSI500_ADTV). ✅

Result: CSI500_REGRESSION — all subs produced 0 clean deploy candidates (deploy blockers prevent all trades). H30 ceiling (1/5 beat_HS300) was not matched. Universe expansion regressed performance.

Blocked by: ~~H52e~~ (now complete)

## H52g — CSI500 Zero-Candidate Diagnostic

Type: AFK

Status: DONE (2026-05-24 — ROOT_CAUSE_IDENTIFIED: int64 date format in CSI500 prices)

What to build:

Deep-trace diagnostic to identify why CSI500 produces 0 clean deploy candidates. Runs one H50b-wired backtest on H30 and CSI500 side-by-side via direct `run_fundamental_backtest` call (no search wrapper). Tests 6 hypotheses (H_A–H_F) plus date format check.

Acceptance criteria:

- ✅ H30 baseline can_deploy=True (n_sells=41, n_days=332) — harness sanity check
- ✅ CSI500 baseline can_deploy=False (n_sells=0, n_days=0) — confirms H52f
- ✅ All 6 hypotheses PASS — CSI500 data structurally sound
- ✅ Root cause: int64 date format (20200102) vs string dates (2020-01-02) — pd.to_datetime() misinterprets
- ✅ H42-baseline sub-trace: H28 fundamentals trap CONFIRMED
- ✅ First divergence: price_coverage.ok (h30=true, csi500=false)
- ✅ 20/20 validators PASS; 15 protected paths unchanged

Result: CSI500_REGRESSION from H52f is INVALID — driven entirely by date format bug. CSI500 true alpha is UNKNOWN until date format is fixed.

Blocked by: H52f (complete)

## H52h — Fix CSI500 Price Date Format + Re-Run H52f

Type: AFK

Status: DONE (2026-05-25 — PHASE_2_REAL_DATA_FLOW_CONFIRMED)

What to build:

1. Convert CSI500 price CSV date column from int64 (20200102) to ISO string (2020-01-02). Keep all other columns identical.
2. Verify H52c coverage report still passes.
3. Re-run H52f pipeline on corrected CSI500 data.
4. Compare true CSI500 alpha vs H30 baseline.

Acceptance criteria:

- CSI500 prices CSV has string dates, all values identical. ✅
- H52c coverage report still CANDIDATE_DATASET. ✅
- H52f re-run produces at least one sub-run with >0 clean deploy candidates (or at least n_sells > 0 for all subs).
- H30 baseline comparison remains valid.
- No modification to engine or search scripts.

Result: Phase 1 fix successful (int→ISO dates, 1076/5 columns confirmed). Phase 2 H52e re-run shows 332 trading days, 30 H51b trades, 7 rebalances — was 0 before fix. H52f full pipeline re-run deferred to H52j.

Blocked by: ~~H52g~~ (complete)

## H52j — CSI500 H42→H51b Full Pipeline Re-Run (post-H52h date fix)

Type: AFK

Status: **CANCELLED (2026-05-26)** — H52 universe-expansion line KILLED. See `docs/strategy-optimization-sync.md` § "H52 Universe-Expansion Line — KILLED" for postmortem and reasoning. Do NOT re-open without a new Research Charter justifying the expansion.

Original plan (preserved for record):

Re-dispatch H52f on the fixed CSI500 data. Reuses H52f's design: 4-sub-run pipeline (H42→H49b→H50b→H51b) with exhaustive search grids over CSI500 universe. Brief reuses H52f design verbatim; harness is identical to H52f. Expected wall: ~95-130 min.

Blocked by: ~~H52h~~ (complete) — but slice is CANCELLED.

## Project-Level Findings (H52g Closure)

H52g diagnostic revealed the CSI500_REGRESSION verdict was driven by a **data format bug**, not genuine alpha deficit: CSI500 prices CSV stores dates as int64 (20200102) but the engine's `pd.to_datetime()` interprets them as nanoseconds → all prices land in 1970 → zero trades. ALL 6 structural hypotheses (H_A–H_F) PASS, confirming CSI500 data is sound under H50b wiring. The H28 fundamentals trap was independently confirmed as a known wiring issue (not the root cause). H52h should fix the date format and re-run H52f to obtain a valid CSI500 alpha read.

Next options:
1. **H52h: Fix date format** — convert int64 dates to ISO string, re-run H52f
2. **CSI1000 expansion** — investigate whether the ~2200-ticker CSI1000 universe carries different factor purity (requires H52h first to validate the CSI expansion methodology)
3. **Revisit gate thresholds** — the 9-condition H42 gate may be too strict for mid/small-cap universes
