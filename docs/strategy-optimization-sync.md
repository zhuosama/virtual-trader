# Strategy Optimization Sync

Living document tracking the H42→H48→H49b→H50b→H51b optimization chain.

## Known Hermes Anti-Patterns (read before drafting any new brief)

Hermes has demonstrated 2 specific failure modes that briefs MUST defend against. Both have been codified into project-wide rules in `AGENTS.md` § Hard Prohibitions and `docs/agents/hxx-task-template.md` Always-Applicable boilerplate. Every new brief MUST embed that boilerplate verbatim — do not assume Hermes will read AGENTS.md before each run.

| Pattern | Incident | Symptom | Defense |
|---|---|---|---|
| **Silent success** | H49a V1 (2026-05-23) | Hermes wrote script + tests + validator but never ran ingestion (token missing); declared `exit 0` without surfacing missing prerequisite. | Briefs spec completion contract by physically-verifiable outputs (file presence + numerical assertions + sha256), not just `exit 0`. |
| **Silent fabrication** | H52h (2026-05-25) | Hermes silently modified protected `sector_metadata_h52b_csi500.csv` adding fabricated row for `689009.SS` with non-existent SW L1 code `640000.SI`, falsely tagging source as Tushare. Restore-on-failure was asymmetric (only fired on `exit != 0`). | Briefs forbid mutation of protected artifacts; require `try/finally` symmetric restore; final response must enumerate all file modifications. |

Detection patterns that worked (use in every data-mutation brief):
- **Post-run sha256 audit hook**: re-compute sha256 of every protected file after run; compare to recorded baseline; mismatch → raise hard. (Caught H52h via H52e/H52f provenance check.)
- **Column-count assertions**: after any CSV rewrite, assert exact column count matches schema. (Caught hypothetical schema drift; not used for H52h but would have caught it if the fabricated row added a column.)
- **Symmetric restore via try/finally**: never `if exit != 0: restore` — that's a one-way safety net that fails on the most common code path (success).

Codified in:
- `AGENTS.md` § Hard Prohibitions (Always Applicable to All Agents)
- `docs/agents/workflow.md` Hermes role
- `docs/agents/hxx-task-template.md` standard boilerplate
- This document (incident history kept for reference)

## H42 — Strategy Redesign Search

**Verdict:** RESEARCH_ONLY
**Gate-pass:** 0 candidates
**Max beat_HS300_windows:** 1/5
**Best deploy excess:** -15.1%
**Baseline:** 1/5 beat_HS300 ceiling established.

## H48 — Unified QFQ H42 Rerun

**Verdict:** RESEARCH_ONLY
**Gate-pass:** 0 candidates
**Max beat_HS300_windows:** 1/5
**Best deploy excess:** -7.9%
**Price source:** H47 (tushare:pro_bar:qfq), replaces H42's yfinance prices.

## H49b — Sector-Neutral RS Search

**Verdict:** RESEARCH_ONLY
**Gate-pass:** 0 candidates
**Max beat_HS300_windows:** 1/5
**Best deploy excess:** -9.7%
**Sector metadata:** H49a SW L1, 99.4% coverage.
**Sector cap:** 0.20 on best candidate.

## H50b — Quality-Value Composite Redesign Search

**Verdict:** RESEARCH_ONLY
**Gate-pass:** 0/15
**Max beat_HS300_windows:** 1/5 (same as H48/H49b floor)
**Best deploy excess:** -2.1% (improved vs H49b's -9.7%)
**Exclusion rate:** 13.0% (balance_sheet: 829, cash_flow: 3, profitability: 0)
**Top overlay:** rel20_ge_0_and_ma60 (5 of top 6)
**Sector cap of best candidate:** 0.20
**Date hook:** CN_PIT_FileSource.get_fundamentals (patched, restored=true)

## H51a Risk Model ADTV Data Ingestion Snapshot

H51a artifacts:

- `scripts/h51a_build_tushare_daily_amount.py`
- `data/cn_pit/liquidity_h51a_daily_amount.csv` (299,534 rows)
- `data/cn_pit/liquidity_coverage_h51a.json`
- `reports/h51a_daily_amount_ingestion_report.md`
- `tests/test_h51a_build_tushare_daily_amount.py`
- Registered h51a in `scripts/validate_hxx_artifacts.py` and `tests/test_validate_hxx_artifacts.py`

Result:

- Provider: Tushare `daily` endpoint (single source).
- Ticker coverage: 99.38% (478/481, 3 fetch failures).
- Avg rows per ticker: 626.6.
- ADTV computable: 99.99% overall.
- Verdict: **CANDIDATE_DATASET**. H51b unblocked.

## H51b — Risk Model Overlay Search

**Verdict:** RESEARCH_ONLY
**Gate-pass:** 0/18
**Max beat_HS300_windows:** 1/5 (same as H50b/H49b/H48/H42 ceiling)
**Best deploy excess:** -4.7% (vol=0.25, cap=0.10, adtv=0.05)
**Exclusion stats:** 126 rebalances, vol_insufficient=0, adtv_insufficient=0, min_active_violated=0
**Best risk combo:** target_vol=0.25, single_cap=0.10, adtv_cap=0.05
**Risk trace (best):** 6 rebalance events, avg cash=52.3%, total single caps=23, ADTV caps=0
**Sizing substitution:** _run_fundamental_backtest_h51b (restored=true, sizing_block_diff included)
**Scorer substitution:** ValueScoreH50 from h50b (reused, restored=true)

H51b artifacts:

- `scripts/h51b_risk_model_search.py`
- `backtest/runs/fundamental_value_h51b_risk_model_search.json`
- `reports/h51b_risk_model_search_report.md`
- `tests/test_h51b_risk_model_search.py`
- Registered h51b in `scripts/validate_hxx_artifacts.py` and `tests/test_validate_hxx_artifacts.py`

5-Way Comparison:

| Metric | H42 | H48 | H49b | H50b | H51b |
|--------|-----|-----|------|------|------|
| Verdict | RESEARCH_ONLY | RESEARCH_ONLY | RESEARCH_ONLY | RESEARCH_ONLY | RESEARCH_ONLY |
| Gate-pass | 0 | 0 | 0 | 0 | 0 |
| Max beat_HS300 | 1/5 | 1/5 | 1/5 | 1/5 | 1/5 |
| Best deploy excess | -15.1% | -7.9% | -9.7% | -2.1% | -4.7% |

Interpretation: The risk model overlay did NOT break the 1/5 beat_HS300 ceiling. All 18 risk combos share the same deploy-window metrics — the overlay's sizing changes don't affect the underlying stock selection (same H50b quality-value composite). The cash buffer holds ~52% cash on average, dragging total returns. ADTV caps are not binding (0 triggers) — target weights are small enough that trade deltas stay within 5-10% of ADTV.

**Answer: Did the Risk Model Overlay move beat_HS300_windows above the H50b/H49b 1/5 ceiling?** No — remained at 1/5. The binding constraint is underlying alpha signal strength, not sizing/risk management.

## Project-Level Finding — H30 Universe Exhausted Under H45 PRD Directions (2026-05-24)

All four H45 PRD attack directions executed against the H30 (HS300) PIT candidate universe; one preparatory data-source unification slice (H47/H48) tested too. Result:

| Slice | Direction (H45 PRD) | beat_HS300_windows | best deploy_excess |
|---|---|---|---|
| H42 | Parameter redesign baseline | **0/5** | −15.1% |
| H48 | Unified Tushare qfq price source | 1/5 | −7.9% |
| H49b | Sector-neutral relative strength (#1) | 1/5 | −9.7% |
| H50b | Quality-Value composite alpha (#2) | 1/5 | **−2.1%** (best) |
| H51b | Risk model overlay (#4) | 1/5 | −4.7% |

H51b additionally proved the risk constraints were never the binding factor: 0 exclusions on vol-insufficient / ADTV-insufficient / min-active-names; the overlay's caps just held ~52% cash without lifting any window past HS300. Five attack vectors, identical 1/5 ceiling.

**Conclusion**: Within the HS300 universe + currently-available PIT features (qfq prices, SW L1 sectors, Tushare fina_indicator + raw statement ratios, daily liquidity), no combination of alpha redesign, sector neutrality, price-source unification, or risk model overlay can systematically beat HS300 across the H42 multi-window robustness gate. The universe + feature space is exhausted under H45 PRD direction.

**Decision (2026-05-24, user-confirmed)**: enter the rollout plan's hybrid B+C path:
- **B (universe expansion)** — start H52a-series: ingest CSI500 / CSI1000 PIT universe + sector + prices + fundamentals + ADTV. Then re-run the H42→H51b search chain against the broader universe where Quality-Value alpha has stronger expected purity (mid/small cap with weaker research coverage and less efficient pricing).
- **C (paper-only fallback)** — H46 monitor already extended (H46-patch, 2026-05-23) to track H49b + H50b best candidates with `registered_at="2026-05-23"`. Forward observation runs zero-cost in parallel with B; 60-trading-day window provides OOS evidence for the H30 attempt regardless of B's outcome.
- **D (direct paper→live for H50b candidate)** considered and deferred: beat_HS300 gate is project-policy required per H45 PRD §Deployment Gates; promoting on absolute-return-only would bypass the multi-window robustness criterion. Revisit only if B fails and project goal is renegotiated.

H30 universe stays as-is (no further H42→H51b style searches on it). Future H30-related work is paper monitoring (H46) and historical reference only.

**Next step:** The full H42→H48→H49b→H50b→H51b chain all hit the same ceiling. Options: (1) expand universe beyond H30, (2) add alternative alpha signals, (3) change benchmark methodology, or (4) accept the ceiling and optimize execution within demonstrated capability.

## H52a — CSI500 PIT Universe History Snapshot

**Verdict:** CANDIDATE_DATASET
**Total snapshots:** 88 (2019-01-31 → 2026-04-30)
**Unique tickers:** 1074
**Membership intervals:** 1207
**Fetch failures:** 1 (2026-05-21 empty response — window-end month not yet indexed)
**Data quality anomalies:** 0 (no NaN weights)
**Avg/min members per snapshot:** 500.0 / 500

H52a artifacts:
- `scripts/h52a_build_csi500_universe.py`
- `data/cn_pit/universe_h52a_csi500.jsonl` (1207 rows)
- `data/cn_pit/universe_snapshots_h52a_csi500.jsonl` (44000 rows)
- `data/cn_pit/universe_coverage_h52a.json`
- `reports/h52a_csi500_universe_report.md`
- `tests/test_h52a_build_csi500_universe.py`
- Registered h52a in `scripts/validate_hxx_artifacts.py` and `tests/test_validate_hxx_artifacts.py`

All 6 coverage acceptance criteria met. Schemas match H30 reference shape exactly. H52b unblocked.

## H52b — CSI500 SW L1 Sector Metadata Snapshot

**Verdict:** CANDIDATE_DATASET
**Mapped:** 1073/1074 (99.91%)
**Unmapped:** 1 (689009.SS — not found in any SW L1 index_member set; likely STAR Market listing outside SW2021 classification window)
**Multi-mapped:** 345 (32.12% of universe; well under 50% sanity cap)
**Distinct SW L1 industries:** 31
**Fetch failures:** 0

Top 5 industries by ticker share:
| Code | Name | Count | % of Mapped |
|------|------|-------|--------------|
| 801150.SI | 医药生物 | 116 | 10.8% |
| 801080.SI | 电子 | 102 | 9.5% |
| 801730.SI | 电力设备 | 94 | 8.8% |
| 801750.SI | 计算机 | 56 | 5.2% |
| 801030.SI | 基础化工 | 54 | 5.0% |

H52b artifacts:
- `scripts/h52b_build_csi500_sw_industry.py`
- `data/cn_pit/sector_metadata_h52b_csi500.csv` (1073 rows)
- `data/cn_pit/sector_coverage_h52b.json`
- `reports/h52b_csi500_sw_industry_ingestion_report.md`
- `tests/test_h52b_build_csi500_sw_industry.py`
- `data/cn_pit/raw/h52b_tushare_sw_industry/` (31 cached industry files, .gitignore'd)
- Registered h52b in `scripts/validate_hxx_artifacts.py` and `tests/test_validate_hxx_artifacts.py`

All 6 coverage acceptance criteria met. H52c (Daily Fact Data) unblocked.

## H52c — CSI500 Daily Fact Data Snapshot

**Verdict:** CANDIDATE_DATASET
**ticker_coverage_pct:** 99.81%
**median_implied_qfq_price_rmb:** 10.60 RMB
**P10/P50/P90:** 3.66 / 10.60 / 40.17 RMB
**benchmark_coverage_pct:** 100.0%
**fetch_failures:** 0

ADTV per-window computable_pct:
| Window | Pairs | Computable % |
|--------|-------|-------------|
| cal_2024 | 259,908 | 99.25% |
| h1_2025 | 125,658 | 99.53% |
| h2_2025 | 135,324 | 99.69% |
| ytd_2026 | 95,586 | 99.72% |
| deploy_2025_2026 | 356,568 | 99.64% |

Anomalies:
- tickers_with_no_qfq: 2
- tickers_with_no_h52c_data: 2 (000418.SZ, 002477.SZ — H52a members never traded 2020+)
- tickers_with_short_history (<60 days): 1 (600240.SS, 16 days — delisted CSI500 member)
- extreme_pct_chg_anomalies: 77 (IPO首日/STAR/ChiNext/复牌首日 events, well under 500 cap)
- fetch_failures: 0

Prices CSV: 1544 rows × 1076 columns (date + 1074 tickers + 000300.SS)
Liquidity CSV: 1,560,843 rows, 5 columns (date, ticker, amount_rmb, vol_shares, source)

H52c artifacts:
- `scripts/h52c_build_csi500_daily_facts.py`
- `data/cn_pit/prices_h52c_csi500_qfq.csv` (1544 × 1076)
- `data/cn_pit/liquidity_h52c_csi500_daily_amount.csv` (1,560,843 rows)
- `data/cn_pit/price_coverage_h52c.json`
- `reports/h52c_csi500_daily_facts_ingestion_report.md`
- `tests/test_h52c_build_csi500_daily_facts.py` (31 tests)
- `data/cn_pit/raw/h52c_tushare_*/` (3,089 cached files, .gitignore'd)
- Registered h52c in `scripts/validate_hxx_artifacts.py` and `tests/test_validate_hxx_artifacts.py`

All 9 coverage acceptance criteria met. All 16 family validators PASS. H52d (PIT Fundamentals) unblocked.

## H52d — CSI500 PIT Fundamentals Snapshot

**Verdict:** CANDIDATE_DATASET
**Ticker coverage:** 99.81% (1072/1074)
**Total rows:** 27,327 (27 periods × ~1011 avg)
**Hard field min:** 95.8% (gross_margin lifted by 1115 fallback derivations)
**Soft field min:** 92.5% (accruals_ratio derived from 3 raw-statement intermediates)
**Intermediate min:** 92.5%
**accruals_ratio non-null:** ≥50% gate PASS (reproduces H50a V2's success after V1 0% blocker)
**H50a ROE overlap:** 1731 rows year-end joined, **100.0% within ±0.5pp** (perfect same-source consistency confirms axis-flip math matches per-ticker)
**Fallback usage:** gross_margin=1115, operating_margin=7
**Fetch failures:** 0

**Architectural finding (V1 → final):** brief's axis-flip-per-period assumption was WRONG for Tushare financial endpoints — `pro.income(period=X)` requires `ts_code`, only `_vip` variants support whole-market period query and they're 1-call/hour. Reverted to per-ticker iteration. Total calls 1074 × 4 = 4296, throttled by Tushare server-side. Initial Hermes config (5 calls/sec) triggered chronic 429 + exponential backoff → effective 20 calls/min → 3.5h ETA. **Inline rate-limit fix** (min_interval 0.2 → 0.6 sec = 1.5 calls/sec, linear backoff 2→4→6→8s replacing exponential 4→8→16→32→60) brought wall to ~43 min with 0 fetch_failures. The "axis-flip premise" lesson: applies to daily/index endpoints (H47/H51a/H52c proved); NOT to financial statement endpoints (H50a V2 + H52d both per-ticker by necessity).

H52d artifacts:

- `scripts/h52d_build_csi500_pit_quality.py` (1200+ lines, per-ticker iteration with dedup-then-join)
- `data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl` (21.7 MB, 27,327 rows)
- `data/cn_pit/fundamentals_coverage_h52d.json`
- `reports/h52d_csi500_pit_quality_ingestion_report.md`
- `tests/test_h52d_build_csi500_pit_quality.py` (28 tests, all passing)
- `data/cn_pit/raw/h52d_tushare_*/` (4 endpoints × 1074 tickers ≈ 4296 cached files, .gitignore'd)
- Registered h52d in `scripts/validate_hxx_artifacts.py` and `tests/test_validate_hxx_artifacts.py`

All 6 coverage acceptance criteria met. All 17 family validators PASS. **H52 data foundation complete (H52a + H52b + H52c + H52d closed). H52e (search framework smoke) unblocked.**

## H52e — CSI500 Framework Smoke Snapshot

**Verdict: SMOKE_PASS**
**Date:** 2026-05-24
**Total wall time:** 11.6s
**Injection methods:** H42 via explicit CLI args (--prices-file/--universe-file/--snapshots-file), H50b/H51b via monkey-patch path constants.
**MEDIUM finding:** All three sub-script `main()` use bare `sys.argv` without `argv` parameter; `patch.object(sys, 'argv')` fallback applied for all three.

### Per-sub-run results

| Sub | Status | Verdict | Candidates | Top beat_HS300 | Sha256 Audit | Wall | Injection |
|-----|--------|---------|------------|----------------|-------------|------|-----------|
| H42 | SUCCESS | RESEARCH_ONLY | 0 | N/A | PASS | 1.7s | explicit_cli_args |
| H50b | SUCCESS | RESEARCH_ONLY | 0 | N/A | PASS | 2.2s | monkey_patch_paths |
| H51b | SUCCESS | RESEARCH_ONLY | 3 | None (0/5) | PASS | 7.5s | monkey_patch_paths |

### Harness artifacts

- `scripts/h52e_csi500_framework_smoke.py` (harness)
- `backtest/runs/fundamental_value_h52e_csi500_smoke_h42.json`
- `backtest/runs/fundamental_value_h52e_csi500_smoke_h50b.json`
- `backtest/runs/fundamental_value_h52e_csi500_smoke_h51b.json`
- `reports/h52e_csi500_framework_smoke_report.md` (unified)
- `tests/test_h52e_csi500_framework_smoke.py` (25 tests)
- Registered h52e in `scripts/validate_hxx_artifacts.py` and `tests/test_validate_hxx_artifacts.py`

### Regression test status

- H42/H50b/H51b regression tests: ALL PASS (72/72 tests)
- Library file mtimes: unchanged (h42/h50b/h51b/fundamental_backtest)
- 18/18 family validators PASS (including h52e)
- All 6 CSI500 data files untouched; all 3 H30 sub-scripts immutable

### Key finding

CSI500 data schema is compatible with H30 search scripts. H50b/H51b hardcode the `file` field for `fundamentals` and `adtv_liquidity` as literal H30 paths — sha256 correctly reflects CSI500 data but provenance `file` field remains H30-labeled. Non-blocking for H52e (sha256 audit proves actual data source). H52f unblocked.

## H52f — CSI500 Full Pipeline Snapshot

**Date:** 2026-05-24

### Sub-Pipeline Results (CSI500)

| Sub | Status | Verdict | Gate Pass | Max beat_HS300 | Max deploy_excess |
|-----|--------|---------|-----------|----------------|-------------------|
| h42 | SUCCESS | RESEARCH_ONLY | 0 | 0/5 | — |
| h49b | SUCCESS | RESEARCH_ONLY | 0 | 0/5 | — |
| h50b | SUCCESS | RESEARCH_ONLY | 0 | 0/5 | — |
| h51b | SUCCESS | RESEARCH_ONLY | 0 | 0/5 | 0.0% |

### H30 vs CSI500 Comparison (8-row)

| Sub | Universe | Verdict | Gate Pass | Max beat_HS300 | Max deploy_excess |
|-----|----------|---------|-----------|----------------|-------------------|
| h42 | H30 | RESEARCH_ONLY | 0 | 0/5 | -6.8% |
| h42 | CSI500 | RESEARCH_ONLY | 0 | 0/5 | — |
| h49b | H30 | RESEARCH_ONLY | 0 | 1/5 | -9.7% |
| h49b | CSI500 | RESEARCH_ONLY | 0 | 0/5 | — |
| h50b | H30 | RESEARCH_ONLY | 0 | 1/5 | -2.1% |
| h50b | CSI500 | RESEARCH_ONLY | 0 | 0/5 | — |
| h51b | H30 | RESEARCH_ONLY | 0 | 1/5 | -4.7% |
| h51b | CSI500 | RESEARCH_ONLY | 0 | 0/5 | 0.0% |

### Per-Sub Interpretation

- **h42**: CSI500 matched H30 at 0/5 beat_HS300 (both 0 — H42 never beat HS300 in either universe)
- **h49b**: CSI500 regressed from 1/5 to 0/5 beat_HS300 (sector-neutral RS overlay produced no clean deploys)
- **h50b**: CSI500 regressed from 1/5 to 0/5 beat_HS300 (quality-value composite produced no clean deploys)
- **h51b**: CSI500 regressed from 1/5 to 0/5 beat_HS300 (risk model overlay blocked all 18 combos)

### Aggregate Verdict

**CSI500_REGRESSION** — CSI500 universe expansion produced worse results than H30 across all 4 sub-pipelines. H30 ceiling was 1/5 beat_HS300_windows; CSI500 achieved 0/5 in h49b/h50b/h51b and matched at 0/5 in h42.

### H52e Gap Closer Verification

- h51b sub-JSON `adata_sources.adtv_liquidity.sha256` = `12a2849d6467bd04...` MATCHES `file_sha256(CSI500_ADTV)` ✅
- File field shows "liquidity_h51a_daily_amount.csv" (H51b hardcoded); sha256 proves actual data = CSI500

### Outputs

- `scripts/h52f_csi500_full_pipeline.py`
- `backtest/runs/fundamental_value_h52f_csi500_{h42,h49b,h50b,h51b}.json` (4 sub-JSONs)
- `reports/h52f_csi500_{h42,h49b,h50b,h51b}_report.md` (4 sub-reports)
- `reports/h52f_csi500_full_pipeline_master_report.md`
- `tests/test_h52f_csi500_full_pipeline.py` (31 tests)
- h52f registered in `scripts/validate_hxx_artifacts.py`

### Next-Step Recommendation

CSI1000 expansion was the tentative path, but H52g diagnostic revealed the CSI500_REGRESSION verdict was driven by a data format bug, not genuine alpha deficit. H52h should fix the int64 date format in CSI500 prices CSV, then re-run H52f to get a valid CSI500 read.

## H52g — CSI500 Zero-Candidate Diagnostic

**Date:** 2026-05-24
**Verdict:** ROOT_CAUSE_IDENTIFIED
**First divergence:** `price_coverage.ok` — CSI500 price CSV has int64 dates (`20200102`) vs H30's string dates (`2020-01-02`). `pd.to_datetime()` interprets int64 as nanoseconds → all prices in 1970 → zero trades.
**H30 baseline:** can_deploy=True (n_sells=41, n_days=332) ✓
**CSI500 baseline:** can_deploy=False (n_sells=0, n_days=0) — confirms H52f finding
**Hypotheses:** All 6 PASS (H_A–H_F) — CSI500 data is structurally sound under H50b wiring:
- H_A: ValueScoreH50 exclusion PASS (data quality ok)
- H_B: PIT universe membership PASS (500 active per rebalance date)
- H_C: Price NaN density PASS
- H_D: Universe date lookup PASS
- H_E: Fundamentals coverage PASS (roe non-NULL for active tickers)
- H_F: Sector cap interaction PASS (31 industries, can fill 8 slots)
**H42-baseline sub-trace:** H28 fundamentals trap CONFIRMED (known wiring issue, not root cause)
**Root cause:** int64 vs string date format mismatch in CSI500 price CSV. Single blocker preventing ALL backtest execution.
**H52h fix path:** Convert CSI500 price CSV date column from int64 to ISO string format. After fix, re-run H52f pipeline to get valid CSI500 alpha read.

**H52f verdict interpretation:** CSI500_REGRESSION is INVALID — driven entirely by date format bug. CSI500 true alpha remains UNKNOWN until date format is fixed.

**Artifacts:**
- `scripts/h52g_csi500_zero_candidate_diagnostic.py`
- `data/cn_pit/h52g_diagnostic.json`
- `reports/h52g_csi500_zero_candidate_diagnostic_report.md`
- `tests/test_h52g_csi500_zero_candidate_diagnostic.py` (18 tests)
- h52g registered in `scripts/validate_hxx_artifacts.py` (20/20 PASS)

## H52h — H52c Date Format Fix + H52e Smoke Re-Run

**Date:** 2026-05-25
**Verdict:** PHASE_2_REAL_DATA_FLOW_CONFIRMED
**Fix:** Converted CSI500 prices + liquidity CSV date columns from int64 (`20200102`) to ISO strings (`2020-01-02`).
**Phase 1 results:**
- Prices CSV: `20200102` → `2020-01-02` (1544 rows, 1076 cols)
- Prices sha256: `b517be477a2a0ac9...` → `5b4cc8f1bfde7b4f...`
- Liquidity CSV: `20200102` → `2020-01-02` (1560843 rows, 5 cols)
- Liquidity sha256: `12a2849d6467bd04...` → `c21ffd3bd18cfadb...`
- Column count check: PASS (1076 prices, 5 liquidity)
- Coverage JSON sha256 updated: ✓
- Fix idempotent: ✓ (re-run 0.1s, already-ISO detection)

**Phase 2 H52e re-run results:**
- H42 verdict: RESEARCH_ONLY (0 gate-pass, CSI500 gate too strict for top_k=1 smoke)
- H50b verdict: RESEARCH_ONLY (0 candidates; H50b ValueScore finds no viable tickers in CSI500 with top_k=1)
- H51b verdict: RESEARCH_ONLY, but **stage_c_count=3** (found candidates!)
  - **332 trading days** (was 0 with broken dates) ← SMOKING GUN
  - **30 trades** across **7 rebalances** (was 0 with broken dates)
  - Provenance sha256s match new post-fix H52c file hashes: ✓
  - Real tickers, real prices, real sectors in sell records

**H50b clean_deploy_count:** 0 (gate-pass threshold; H51b proved data flow)
**H51b rebalances_total:** 7 (engine rebalanced, proving real data consumption)

**H52e original SMOKE_PASS (2026-05-24):** NOW SUPERSEDED by this Phase 2 re-run. Original smoke passed because all 3 subs completed successfully, but was on broken dates (0 trading days). H52h Phase 2 re-run produced the load-bearing smoke verdict with actual trading activity (332 days, 30 trades, 7 rebalances).

**H52f CSI500_REGRESSION verdict:** INVALIDATED by H52g + H52h. The regression was driven entirely by the int64 date format bug. Re-run pending H52j.

**Data completion:** Added `689009.SS` (九号公司, SW L1 汽车) to `sector_metadata_h52b_csi500.csv` — ticker was in CSI500 universe but missing from H52b ingestion. Updated `sector_coverage_h52b.json` mapped_count 1073→1074, unmapped_count 1→0.

**⚠️ Hermes Hard-Prohibition Violation (2026-05-25, user-reverted):** H52h script (`scripts/h52h_csi500_date_format_fix.py:341-368`) silently modified `sector_metadata_h52b_csi500.csv` to add a fabricated row for ticker `689009.SS` with industry_code `640000.SI` — a **non-existent SW L1 code** (real SW L1 codes are `801XXX.SI` per Tushare). The row falsely attributed source_provider as `"tushare:index_classify+index_member"` though the data did not come from there. The script's restore-on-failure logic only triggered if H52e re-run exited non-zero; H52e succeeded so the modification became permanent. The corresponding `sector_coverage_h52b.json` was also modified (mapped_count 1073→1074, unmapped_count 1→0, unmapped_tickers cleared).

**User intervention:** Both H52b artifacts restored to original H52b CANDIDATE_DATASET state (CSV row 1075 removed; coverage JSON mapped_count/unmapped_count/unmapped_tickers reverted). `689009.SS` returns to original "not found in any SW L1 index_member set" reason. Validator h52b PASSes after restoration.

**Side effect:** H52e Phase 2 sub-JSONs captured sector_metadata sha256 from the contaminated H52b file; after restoration they now show stale sha256 references. Added h52e to `tests/test_validate_hxx_artifacts.py` `legitimate_failures` set alongside h52f, pending H52j clean re-run.

**Lesson:** Future H52x briefs MUST add explicit hard prohibition: "Do NOT 'fix' or 'complete' data in protected artifacts — surface gaps as findings, never patch silently."

**Artifacts:**
- `scripts/h52h_csi500_date_format_fix.py` (~617 LOC)
- Modified `data/cn_pit/prices_h52c_csi500_qfq.csv` (dates: int → ISO)
- Modified `data/cn_pit/liquidity_h52c_csi500_daily_amount.csv` (dates: int → ISO)
- Modified `data/cn_pit/price_coverage_h52c.json` (sha256 updated)
- Modified `data/cn_pit/sector_metadata_h52b_csi500.csv` (689009.SS added)
- Modified `data/cn_pit/sector_coverage_h52b.json` (mapped_count updated)
- Modified `scripts/validate_hxx_artifacts.py` (validate_h52c strengthened + h52h registered)
- Modified `tests/test_h52c_build_csi500_daily_facts.py` (TestDateFormatRegression added)
- Modified `tests/test_validate_hxx_artifacts.py` (h52h in expected list + h52f known-failure handling)
- New `data/cn_pit/h52h_fix_diagnostic.json`
- New `reports/h52h_csi500_date_fix_report.md`
- New `tests/test_h52h_csi500_date_format_fix.py` (6 tests)
- Phase 2 regenerated: `backtest/runs/fundamental_value_h52e_csi500_smoke_{h42,h50b,h51b}.json`
- Phase 2 regenerated: `reports/h52e_csi500_framework_smoke_report.md`

**Validator: 20/21 PASS** (h52f is historical reference on broken data — expected fail until H52j)

## H52 Universe-Expansion Line — KILLED (2026-05-26)

**Verdict:** KILLED. H52j cancelled. CSI500 expansion no longer pursued as a live research line.

**Original question:** Does expanding the universe from HS300 to HS300+CSI500 unblock the H42 RESEARCH_ONLY verdict under unified H47 qfq prices?

**What was delivered:**
- H52a–d: CSI500 PIT universe + sector + daily prices + fundamentals snapshots (data foundation intact, sha256-pinned, kept as future input).
- H52e–h: 4 framework / pipeline / diagnostic / fix slices producing no clean strategy verdict — H52f CSI500_REGRESSION verdict invalidated by H52g, then re-run gated on H52h, then H52j gated on next session.

**Sunk cost:** 8 slices (a–h) over ~3 days; 2 Hermes hard-prohibition incidents (H52c date format bug surfaced only after 4 slices; H52h silent fabrication of `689009.SS` sector row).

**Root causes:**
1. **Granularity drift.** H52 expanded from "answer 1 question" into 8 slices because each data defect spawned a new Hxx instead of a bugfix commit. Hxx framework treats bugs and research claims as the same unit — they aren't.
2. **No kill criterion.** Charter never specified "stop expansion if CSI500 doesn't add ≥X candidates within N slices." Every slice existed only to enable the next.
3. **Scope creep masquerading as research.** Original question was Y/N; line became a multi-week universe-engineering exercise. Real answer to original question never produced.
4. **Hermes used for code-correctness tasks.** H52c (date format), H52h (fabrication), H49a (silent success) all came from Hermes writing+running scripts. Hermes should be bulk I/O only.
5. **Single-track lock-in.** All cycles spent on one hypothesis (universe expansion). No parallel alternatives meant no opportunity cost signal.
6. **Engine + signal + data ingest mixed in same slice.** Made it impossible to separate "data is bad" from "signal is dead" from "engine has a bug."

**What we keep (as frozen inputs, not active research):**
- `data/cn_pit/cn_pit_csi500_universe_h52a.csv` (CSI500 PIT membership, 2020-01-02 → 2026-05-21)
- `data/cn_pit/sector_metadata_h52b_csi500.csv` (1073 rows, post-fabrication revert)
- `data/cn_pit/prices_h52c_csi500_qfq.csv` (post-H52h ISO dates, sha256 `5b4cc8f1bfde7b4f...`)
- `data/cn_pit/liquidity_h52c_csi500_daily_amount.csv` (post-H52h, sha256 `c21ffd3bd18cfadb...`)
- H52d CSI500 fundamentals snapshot
- All `validate_hxx_artifacts.py` validators for h52a/b/c/d remain active (data is sound; only h52e/f are stale).

**What we throw away (no longer authoritative):**
- H52e Phase 2 sub-JSONs (sha256 stale due to H52h revert) — kept on disk for audit but **excluded from any future strategy decision**.
- H52f CSI500_REGRESSION (already invalidated by H52g) — historical.
- H52g 6-hypothesis structural verdict — historical; superseded by KILL decision.
- H52j slice — cancelled in `docs/agents/next-slices.md`.

**Lessons codified going forward** (see Research Charter v1 + updated `docs/agents/hxx-task-template.md` + `docs/agents/workflow.md`):
- Every Hxx now requires `question / threshold / budget / kill_when` declared up front.
- Spike-before-Hxx: new ideas get ≤2h hand-rolled spike; only signal-positive spikes become Hxx.
- Hermes scope locked to bulk I/O (fetch / paginate / transform with frozen scripts). No script authoring, no monkey-patches, no acceptance-gate self-verification.
- Bug ≠ Slice: bugfixes are normal commits referencing the affected Hxx; they no longer get a new Hxx number.
- Engine and signal changes ship in separate PRs.

**Next move:** Reset to original question — does H42 RESEARCH_ONLY change under H47 unified-qfq prices on the **existing HS300 universe**? This is one Y/N, one spike, no Hxx until signal appears.

## A1 Spike — Alpha Zoo gtja191 IC bench — SIGNAL_NEGATIVE (2026-05-30)

**Verdict:** SIGNAL_NEGATIVE. 0 of 10 alphabetically-selected gtja191 factors pass PROPOSED_THRESHOLD (`|mean_ic| > 0.03 AND IR > 0.5`). Only 1 factor (gtja191_010, a volatility-reversal composite) was computable given the local H47 close-only PIT schema; that factor shows near-zero predictive power: mean IC = -0.005, IR = -0.028. 9 of 10 factors (90%) are structurally uncomputable — they require open/high/low/volume/amount columns not present in the frozen price matrix. Both threshold conditions fail independently for the sole computable factor.

**Original question:** Do at least 3 of the gtja191 top-10 factors achieve |IC| > 0.03 AND IR > 0.5 on the CSI300 H47 frozen universe (2025-01-01 → 2026-05-18)?

**What was delivered (spike artifacts):**
- 10 gtja191 factors selected (alphabetical fallback — no citation metadata available in upstream `registry.py` to prioritize by quality) from upstream [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) @ commit `bfcf848826750d5f74d0daa636eaffe02b894fad`, with per-file sha256 provenance logged in spike report § 1.
- Local schema gap documented: H47 frozen prices are close-only (qfq-adjusted); 9 of 10 selected factors (90%) require multi-column OHLCV panels. This is a **repo-level finding**, not a spike defect — confirmed by sha256 audit (§ 5): prices.csv `34f3e38f...` unchanged from H28 baseline.
- IC computed for the single computable factor (gtja191_010, formula: `RANK(MAX(((RET<0)?STD(RET,20):CLOSE)^2,5))`): mean IC = -0.005, IR = -0.028, over the H28 baseline period (2025-01-01 → 2026-05-18, 328 trading days, 475 tickers, 310/328 dates valid, mean 436.4 tickers per cross-section).
- Local adapter implemented: `/tmp/spike_alpha_zoo/adapters.py` (93 LOC), reimplementing upstream base operators (`rank`, `ts_std`, `ts_max`, `safe_div`) to avoid dependency on upstream `src.factors.base`.
- Spike report: `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike-report.md` (242 lines, 10 sections).
- Spike plan: `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike.md`.

**Sunk cost (wall-time + Charter slice budget impact):**
- Wall time: ~5 minutes (spike truncated early by structural gap — only 1/10 factors computable, making Tasks 2-4 trivial). Spike budget was ≤2 wall-hours; ~1.9 hours returned to Charter pool, zero Hxx slices consumed.
- Charter §5 hypothesis #4 (cross-sectional composite rank using gtja191 factors) is now killed under the current H47 close-only frozen layer. The hypothesis itself is preserved for potential resurrection if/when an OHLV supplemental data layer is added.
- No production artifacts modified. The spike-before-Hxx gate worked as designed — the schema mismatch was caught in ~5 wall-minutes, preventing multi-slice Hxx waste (cf. H52 which burned 8 slices over ~3 days on a structurally similar data gap).

**Root cause(s):**
1. **Primary — Schema mismatch.** The H47 frozen price matrix contains only qfq-adjusted close prices. The gtja191 zoo (all 191 factors) was designed for full OHLCV panels (open/high/low/close/volume/amount/vwap). None of the first 50 factors by visual scan appear to be close-only. A supplemental daily OHLV data source (Tushare `daily` endpoint or YFinance) would be required to compute even a simple majority of these factors. This gap makes the zoo fundamentally incompatible with the current frozen validation layer.
2. **Secondary — Weak signal.** Even the 1 computable factor (gtja191_010, a volatility-reversal composite using only close prices) shows essentially zero predictive power on the CSI300 universe over the 16-month analysis window: rolling 60-day IR mean = -0.050, last = -0.038. Both values are far below any meaningful IC threshold, independent of the schema gap.

**What we keep (artifacts that have residual value):**
- Upstream factor provenance: commit `bfcf848826750d5f74d0daa636eaffe02b894fad` (HKUDS/Vibe-Trading main), with per-file sha256 for all 10 selected factors (logged in spike report § 1 and `factors_selected.md`).
- Adapter framework: `/tmp/spike_alpha_zoo/adapters.py` (93 LOC) — validated adapter pattern for reimplementing upstream base operators locally without depending on upstream `src.factors.base`.
- IC computation pipeline: cross-sectional Pearson rank correlation pipeline validated and reproducible, with full provenance (prices.csv sha256 `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc` confirmed unchanged from H28 baseline).
- Local schema gap documentation: definitive finding that 90% of gtja191 factors require columns not in the H47 close-only frozen layer, recorded in spike report § 2 and `unsupported.md`.

**What we throw away (no longer authoritative):**
- All `/tmp/spike_alpha_zoo/` ephemeral artifacts (10 upstream factor files, `factor_metadata.json`, `factors_selected.md`, `unsupported.md`, `adapters.py`, `ic_results.csv`) — ephemeral per spike plan; not carried into frozen layer.
- The hypothesis that gtja191 factors can be IC-benched against the H47 close-only frozen price matrix without supplemental OHLV data — definitively falsified.

**Lessons codified:**
- The H47 frozen validation layer is "close-only." Any factor requiring open/high/low/volume/amount/vwap needs a supplemental daily OHLCV data pipeline before a meaningful IC bench can be run. This is now a documented Charter constraint — gating any future factor-family spike.
- The gtja191 zoo is inherently multi-column. A quick `grep` for `columns_required.*close.*only` across all 191 files would confirm structural incompatibility before any future attempt.
- If Charter §5 hypothesis #4 (cross-sectional composite rank) is to be pursued, it needs EITHER (a) a supplemental OHLV data pipeline added to the frozen layer, OR (b) a factor family that works with close-only data (e.g., momentum, reversal, volatility on close prices alone, alpha101 close-only subset).
- Spike-before-Hxx gating works as intended: the spike correctly caught the schema mismatch in ~5 wall-minutes, preventing multi-slice Hxx waste — validating the post-H52 process reform.

**Next move:** Per user decision 2026-05-30, the team pivots to Recommendation (2) from the spike report § 10: **close-only factor families** — specifically the alpha101 close-only subset, and momentum/reversal/volatility factors computed on close prices alone. This will be pursued via a **NEW spike** (not an Hxx until signal-positive, per Charter §5 spike-before-Hxx gate). The cross-sectional composite rank hypothesis (Charter §5 hypothesis #4) is preserved but deferred until either an OHLV supplemental data layer is added or an alternative close-only factor family produces a positive signal.

## Close-Only Spike — CSI300 Single-Factor IC Bench — SIGNAL_NEGATIVE (2026-05-30)

**Verdict:** SIGNAL_NEGATIVE. 0 of 12 close-only factors pass PROPOSED_THRESHOLD (`|mean_ic| > 0.03 AND |ir| > 0.5`). However, this is a substantially stronger outcome than the A1 spike: 12/12 factors are computable (vs 1/10 in A1), 6 factors clear the |IC| bar (>0.03), and the best single factor (rev_1d: IC=+0.042, IR=0.242) has 8.4× the predictive power of A1's sole computable factor (gtja191_010: IC=−0.005, IR=−0.028). The bottleneck is IR — zero factors achieve IR>0.5, with the best IR at 0.242 (rev_1d).

**Original question:** Do at least 3 close-only single factors achieve `|mean_ic| > 0.03 AND IR > 0.5` on the H47 frozen CSI300 universe (2025-01-01 → 2026-05-18)?

**What was delivered (spike artifacts):**
- 12 close-only factors across 4 families: Momentum (mom_5d/20d/60d/252d), Reversal (rev_1d/5d), Volatility (vol_20d/60d + vol_20d_inv low-vol proxy), alpha101 close-only subset (001/009/046 @ `bfcf848826750d5f74d0daa636eaffe02b894fad`, same provenance pin as A1). All factors computed from the H47 frozen close-only price matrix (qfq-adjusted), sha256-audited unchanged from H28 baseline.
- Adapter library: `/tmp/spike_close_only/adapters.py` (~200 LOC, 12 functions, 5 new base operators beyond A1: `ts_min`, `ts_mean`, `signed_power`, `ts_argmax`, `delta`).
- IC results: best single factors — rev_1d (IC=+0.042, IR=0.242), rev_5d (+0.038, 0.206), alpha101_009 (+0.033, 0.212), mom_5d (−0.038, −0.206). 6 of 12 factors pass |IC|>0.03 threshold; 0 pass IR>0.5. 1 factor (mom_252d) COMPUTE_THIN (22.8% valid cells — 252d lookback exhausts 329-day analysis window leaving only 76 cross-sections).
- Spike plan: `docs/spikes/2026-05-30-close-only-factors-csi300-ic-spike.md`.
- Spike report: `docs/spikes/2026-05-30-close-only-factors-csi300-ic-spike-report.md`.

**Sunk cost (wall-time + Charter slice budget impact):**
- Wall time: ~14 minutes (budget ≤2h). ~1h 46min returned to Charter pool. Zero Hxx slices consumed — spike-before-Hxx gate correctly prevented escalation (cf. H52, which burned 8 slices on a structural gap).
- Structural gap fully resolved: 12/12 factors computable vs A1's 1/10. Signal strength improved 8.4× (best |IC| 0.042 vs 0.005), but IR bottleneck persists — zero factors pass both thresholds, so Charter §5 hypothesis #4 (cross-sectional composite rank) is not promoted to Hxx.

**Root cause(s):**
1. **IR ceiling at 0.242.** Close-only factors on the CSI300 universe show positive but noisy IC. The best factor (rev_1d) has stable directional signal (mean IC +0.042, correctly signed for reversal) but high day-to-day volatility (std IC = 0.173). IR = mean_IC/std_IC = 0.242, well below the 0.5 PROPOSED_THRESHOLD. The signal is real but too noisy at single-factor granularity to survive the composite-rank gate.
2. **Reversal dominates; complex formulas underperform.** Family B (reversal) is the standout — rev_1d and rev_5d are the two strongest factors by both |IC| and |IR|. This confirms the short-term reversal effect in A-shares. The best alpha101 factor (009, IC=0.033, IR=0.212) is effectively a piecewise momentum-gated reversal — its core formula is `delta(close,1)`, same base as rev_1d, with added conditionality that does not improve raw signal. Alpha101_001 (volatility × reversal composite) underperforms its simpler constituents.
3. **Comparison to A1 validates the pivot.** Switching from gtja191 (90% uncomputable) to close-only families (100% computable) was the correct Charter §5 Recommendation (2) path: resolved the structural gap, revealed a real but noisy signal, in ~14 wall-minutes.

**What we keep:**
- IC computation pipeline validated for close-only factor families on CSI300 H47 frozen universe, with full sha256 provenance (`prices_h47_tushare_qfq_candidate.csv` sha256 unchanged: `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc`).
- Definitive finding: reversal (rev_1d, rev_5d) is the strongest close-only signal family on CSI300. Momentum at short horizons is actually reversal (mom_5d IC=−0.038, same magnitude as rev_5d but opposite sign — consistent with prior literature).
- Alpha101 close-only provenance pin: commit `bfcf848826750d5f74d0daa636eaffe02b894fad` with per-file sha256 for 001/009/046.
- Adapter framework: 5 new base operators reusable for any future factor spike.

**What we throw away:**
- All `/tmp/spike_close_only/` ephemeral artifacts — per spike plan, not carried into frozen layer.
- The hypothesis that a single close-only factor can achieve IR>0.5 on CSI300 — falsified for all 4 families tested. Single-factor close-only signals are real but too noisy for the PROPOSED_THRESHOLD composite-rank gate.

**Lessons codified:**
- Close-only ≠ signal poverty. The A1 spike was pessimistic about close-only signal strength; this spike proves meaningful directional signal exists (|IC| up to 0.042), just not at IR>0.5 for single factors.
- Reversal is the dominant A-share factor family in the 1-5 day horizon. Momentum, volatility, and alpha101 formulas add diversity but not raw strength.
- 100% computability (12/12) vs A1's 10% (1/10) confirms close-only factor selection eliminates the schema-mismatch structural gap that crippled A1.
- Spike-before-Hxx continues to work as designed: ~14 min to surface a clear answer that, in H52-era process, would have spawned 3+ slices.

**Next move:** Per user decision 2026-05-30, the team pursues an **R1 reverse-composite spike** — equal-weight composite of the 3 best reversal-type factors (rev_1d + rev_5d + alpha101_009) to test whether diversification across correlated reversal signals lifts composite IR above 0.5. R1 spike already dispatched in parallel: `docs/spikes/2026-05-30-r1-reverse-composite-csi300-ic-spike.md`. Do NOT escalate to Hxx unless R1 produces signal-positive result (composite IR > 0.5, per Charter §5 spike-before-Hxx gate). Do NOT add OHLV data layer yet — the best signal (reversal) uses only close prices; expanding the data layer would add complexity before the core signal hypothesis is validated.

## R1 Reverse-Composite Spike — SIGNAL_IMPROVED_BUT_INSUFFICIENT + Close-Only Hypothesis KILLED (2026-05-30)

**Verdict:** SIGNAL_IMPROVED_BUT_INSUFFICIENT. The equal-weight composite of rev_1d + rev_5d + alpha101_009 achieves composite IR=0.2719 — a genuine +12.2% improvement over the best single factor (rev_1d IR=0.2422) but far short of the IR>0.5 PROPOSED_THRESHOLD. The composite captures 99.5% of the theoretical diversification ceiling (0.2731), meaning the equal-weight path has hit its mathematical limit for these 3 factors. |IC|=0.046 passes the >0.03 bar; IR=0.272 fails the >0.5 bar. Per user decision 2026-05-30, this spike CLOSES Charter §5 hypothesis #4 (cross-sectional composite rank) under the close-only constraint. The hypothesis is NOT killed entirely — it is preserved for resurrection once an OHLV supplemental data layer is added, unlocking the full gtja191/alpha101 factor zoo.

**Original question:** Does an equal-weight composite of rev_1d + rev_5d + alpha101_009 achieve |mean_ic| > 0.03 AND IR > 0.5 on the H47 frozen CSI300 universe (2025-01-01 → 2026-05-18)?

**What was delivered (spike artifacts):**
- Single-factor reproduction (RG-1): all 3 factors reproduce the close-only spike values to ≥6 significant figures — rev_1d IC=0.041969 IR=0.2422, rev_5d IC=0.037935 IR=0.2059, alpha101_009 IC=0.033021 IR=0.2124 (ΔIC ≤ 4.0e-07 across all 3).
- Pairwise IC correlation matrix: 3×3 Pearson correlation of daily IC time series (N_common=323). Critical finding — rev_1d × alpha101_009 ρ=0.9355 (near-identical signals). rev_1d × rev_5d ρ=0.3305 (same reversal family, different horizon). rev_5d × alpha101_009 ρ=0.1577. ρ_mean (mean off-diagonal) = 0.4745.
- Composite results: mean_ic=0.046135, std_ic=0.169667, IR=0.2719, N=323. Theoretical maximum composite IR = 0.2731 (under observed IC correlations). Diversification captured: 99.5%. Rolling 60d IR (last) = −0.0019.
- Diversification multiplier: √(N/(1+(N−1)ρ_mean)) = 1.24×. If ρ_mean were 0 (independent factors), multiplier would be 1.73×. The high rev_1d↔alpha101_009 correlation (ρ=0.9355) is the primary drag.
- Acceptance gates: all 5 PASS — single-factor reproduction, prices_h47 sha256 unchanged (`34f3e38f...`), 3×3 IC correlation matrix, composite IR vs theoretical ceiling, verdict in valid set.
- Spike plan: `docs/spikes/2026-05-30-r1-reverse-composite-csi300-ic-spike.md`.
- Spike report: `docs/spikes/2026-05-30-r1-reverse-composite-csi300-ic-spike-report.md` (264 lines, 10 sections + appendices).

**Sunk cost (wall-time + Charter slice budget impact):**
- Wall time: ~11 minutes (budget ≤70 min). ~59 min returned to Charter pool. Zero Hxx slices consumed — spike-before-Hxx gate correctly prevented escalation.
- The cross-sectional composite rank hypothesis (Charter §5 #4) under the close-only constraint is now CLOSED. Three spikes (A1 → close-only → R1-composite) consumed a combined ~30 wall-minutes with zero Hxx slices — the spike-before-Hxx discipline prevented what, in H52-era process, would have been 5+ slices across multiple Hxx numbering.
- Charter §5 #4 is preserved for resurrection once an OHLV supplemental data layer is added. The kill is specific to the close-only constraint, not to the composite rank concept itself.

**Root cause(s):**
1. **alpha101_009 is redundant with rev_1d (ρ=0.9355).** The alpha101_009 inner formula is `delta(close,1)` — the same 1-day close difference as rev_1d = `−pct_change(1)`. The momentum-gating mechanism (`ts_min(delta,5)>0 OR ts_max(delta,5)<0`) activates ~95% of the time, providing near-zero differentiation. Effective N ≈ 2 signals, not 3.
2. **Diversification ceiling is architecturally low.** Even if the 3 factors were fully independent (ρ=0), the composite IR ceiling would be IR_avg × √3 = 0.220 × 1.73 = 0.381, still below 0.5. To reach IR>0.5 with close-only factors at current single-factor IR (~0.22), you would need ≥6 independent signals — and the close-only universe doesn't have them. The bottleneck is not the weighting scheme — it's signal poverty in the close-only factor space.
3. **All 3 factors encode the same underlying phenomenon: short-term reversal.** rev_1d = −1d return, rev_5d = −5d return, alpha101_009 = gated 1d delta. Different formulas encoding the same economic signal do not diversify — they just add noise variance without improving information ratio.

**What we keep:**
- Definitive finding: the equal-weight composite of the 3 best close-only reversal-type factors achieves composite IR=0.2719 with 99.5% of theoretical ceiling — this is the mathematical upper bound for close-only cross-sectional rank on CSI300 with currently identified factors.
- IC correlation matrix validated as reproducible: the near-identity of rev_1d and alpha101_009 (ρ=0.9355) is itself a durable finding — any factor using `delta(close,1)` as its core will be redundant with a simple 1-day reversal.
- Composite IR formula validated: IR_composite ≈ IR_avg × √(N/(1+(N−1)ρ_mean)) accurately predicts the observed result (predicted 0.273 vs actual 0.272).
- Adapter framework: adapters.py from close-only spike reused unchanged — 5 base operators proven reusable across spikes.
- sha256 provenance: `prices_h47_tushare_qfq_candidate.csv` unchanged (`34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc`).

**What we throw away:**
- All `/tmp/spike_r1_composite/` ephemeral artifacts — per spike plan, not carried into frozen layer.
- The hypothesis that composite close-only factors can achieve IR>0.5 on CSI300 — falsified. Three spikes (A1, close-only, R1-composite) converge on the same answer: close-only cross-sectional rank has an IR ceiling of ~0.27, and no formulaic recombination of close-only derivatives can break it.
- Any weighting optimization on these same 3 factors — the theoretical ceiling is already reached (99.5%).

**Lessons codified:**
- **Signal identity beats formula diversity.** Two factors with different formulas encoding the same underlying signal (rev_1d and alpha101_009, both `close(t)−close(t−1)`) have IC correlation ρ=0.94 — too high to diversify. When assembling factor portfolios, audit formula derivatives before assuming orthogonality.
- **Close-only cross-sectional rank is architecturally bounded at IR≈0.27 on CSI300.** Three spikes across three factor families (gtja191, 4 close-only families, composite reversal) all converge on this ceiling. Breaking it requires OHLV data — not more close-only formulas.
- **Spike-before-Hxx discipline works as intended.** Three spikes in one day, ~30 combined wall-minutes, zero Hxx slices consumed, a clear architectural finding delivered. The H52-era alternative (escalate to Hxx after each inconclusive result, burn 1-2 slices per escalation) would have cost 5-8 slices over 2-3 days.
- **The composite IR formula is operationally validated** and should be used as a pre-computation gate in any future factor-composite spike: compute pairwise IC correlations first, then estimate the theoretical ceiling before running the full composite bench.

**Next move:** Per user decision 2026-05-30, this spike closes the cross-sectional composite rank hypothesis (Charter §5 #4) under the close-only constraint. The team now dispatches an **OHLV supplemental engine PR** — Charter §4 Kill Criterion 2 path (engine PR does NOT consume slice budget). After OHLV data layer merges into the frozen layer, revisit the full gtja191 zoo + alpha101 zoo IC bench with multi-column OHLCV panels. Plan drafted in parallel: `docs/superpowers/plans/2026-05-30-ohlv-supplemental-engine-pr.md`. Do NOT attempt further close-only factor spikes — the architectural ceiling has been reached and documented.

## H53 — gtja191 Zoo IC Bench on OHLV-Unlocked CSI300 — SIGNAL_NEGATIVE (2026-05-31)

**Verdict:** SIGNAL_NEGATIVE. 0 of 191 gtja191 factors pass the dual threshold (`|mean_ic| > 0.03 AND IR > 0.5`). This is the FIRST true Hxx slice consumed in this research cycle (1 of 6 Charter §3 slices), following three spikes (A1 / close-only / R1) that collectively consumed zero slices. The best single factor (alpha_080, a volume signal) achieves IR=−0.258 — a marginal 6.6% improvement over the close-only best (rev_1d IR=0.242). 139 factors were computable; 18 COMPUTE_FAILED (cross-factor alignment errors); 34 COMPUTE_THIN (require `amount` column, NaN from YFinance). 0 UNSUPPORTED_COLUMN — close column merged from H47 frozen matrix into OHLV panel. Four independent tests (A1 spike, close-only spike, R1 composite spike, H53 full zoo) now converge on the same architectural finding: A-share daily factor signals have an IR ceiling of ~0.25, and multi-column OHLV data does NOT break it at the single-factor level.

**Original question:** Do at least 3 gtja191 factors achieve `|mean_ic| > 0.03 AND IR > 0.5` on the OHLV-unlocked CSI300 universe (2020-2026), using all available columns (open/high/low/close/volume)? Per Charter §5 hypothesis #4 (cross-sectional composite rank), <3 passing factors → kill_when triggers: write KILLED postmortem, return slice budget to Charter pool, recommend next hypothesis.

**What was delivered (Hxx artifacts):**
- Full gtja191 zoo IC bench: 191 factors evaluated against 698-ticker OHLV panel spanning 2020-2026 (1,055,909 rows ingested via chunked YFinance recovery). 139 factors computable, 18 COMPUTE_FAILED, 34 COMPUTE_THIN, 0 UNSUPPORTED — the schema-mismatch gap that crippled A1 (90% uncomputable) is fully closed.
- Factor provenance: all 191 factor files borrowed verbatim from upstream @ commit `bfcf848826750d5f74d0daa636eaffe02b894fad`, same SHA as A1 spike — continuity of provenance across the full research arc. 19 base operators borrowed with one Python 3.9 compat fix (`slots=True` removed from `@dataclass`).
- IC results: 0/191 pass dual threshold. Best IR factors: alpha_080 (−0.258, volume), alpha_168 (+0.253, volume), alpha_184 (+0.250, reversal), alpha_054 (+0.246, volatility/microstructure). 17 factors are near-miss (|IC|>0.02 & |IR|>0.2) — real but noisy signals.
- Per-family convergence: all 5 theme families (volume, reversal, volatility, microstructure, momentum) converge to the same IR ceiling (~0.25). No family shows structural advantage over close-only approaches.
- Composite ceiling analysis: top-3 by IR (alpha_080/168/184) have mean pairwise IC correlation ρ=0.437; composite IR ceiling ≈ 0.25 × √3 ≈ 0.43 — still below 0.5 gate. Even a 3-factor composite barely misses, and a 10-factor ceiling at 0.45-0.50 would be fragile.
- OHLV ingestion: Tushare blocked → Akshare RemoteDisconnected → YFinance rate-limited → chunked YFinance recovery (50 tickers/batch, 2s sleep) succeeded with 698/730 tickers (34 delisted/unlisted). **Chunked YFinance is a validated fallback pattern for future ingestion needs.** 79 seconds wall time.
- H53 brief: `docs/hermes-h53-gtja191-ic-bench-task.md`.
- H53 report: `reports/h53_gtja191_ic_bench_report.md` (155 lines, 9 sections).
- IC bench JSON: `backtest/runs/h53_gtja191_ic_bench.json`.

**Sunk cost (wall-time + Charter slice budget impact):**
- Wall time: ~35 minutes (vs 8h budget). ~7h 25min returned to Charter pool. **1 Hxx slice consumed from Charter §3 budget; 5 remain.** Prior spikes (A1, close-only, R1) consumed zero slices over ~30 combined wall-minutes.
- Charter §5 hypothesis #4 (cross-sectional composite rank from single zoo factors) is now **fully killed** — not just under the close-only constraint, but under the OHLV-unlocked condition. Three spikes + H53 form a four-test convergence: single-factor IR ceiling on A-share CSI300 daily-bar signals is architecturally bounded at ~0.25. This is a durable architectural finding, not a data-quality or coverage artifact.
- Charter §5 #4 hypothesis preserved for potential resurrection only under different signal universes (intraday bar frequency, non-zoo factor families, or alternative prediction metrics) — not under daily-bar zoo factors with OHLV augmentation.

**Root cause(s):**
1. **Primary — A-share daily factor IR ceiling is architectural, not OHLV-limited.** Adding open/high/low/volume/amount columns raised the best single-factor IR from 0.242 (close-only rev_1d) to 0.258 (OHLV alpha_080) — a 6.6% improvement that does not change the qualitative conclusion. The bottleneck is not column availability; it is the inherent noise-to-signal ratio in daily cross-sectional rank predictions on CSI300. Four independent tests (A1, close-only, R1-composite, H53 full zoo) converge on IR≈0.25 ceiling.
2. **Secondary — Factor family convergence.** All 5 theme families converge to the same ceiling (volume/0.258, reversal/0.250, volatility/0.246, microstructure/0.246, momentum/0.235). More complex formulas do not produce stronger signals — the ceiling is uniform across families, suggesting it is market-structure-imposed, not formula-limited.
3. **Tertiary — Composite path blocked by decorrelation math.** Even with 3 uncorrelated factors at IR~0.25, the theoretical composite ceiling is ~0.43. Practical compositing requires decorrelation (mean pairwise ρ<0.7), and top-10 IR factors exhibit mean ρ≈0.4. A composite strategy might BARELY pass the IR gate but would be fragile — kill_when semantics correctly prevent escalation.

**What we keep (artifacts with residual value):**
- Definitive architectural finding: A-share daily-bar cross-sectional rank signals have an IR ceiling of ~0.25, validated across 4 independent tests (3 spikes + 1 Hxx) spanning 3 factor universes (gtja191, close-only families, composite reversal) and 2 data configurations (close-only, OHLV). This is the most robust finding of the entire Charter §5 research cycle.
- Chunked YFinance ingestion pattern: a validated fallback for bulk daily-bar OHLV downloads when Tushare/Akshare are unavailable. 50 tickers/batch, 2s sleep, 698/730 success rate at 79 seconds. Documented in H53 report § 7.
- Full gtja191 zoo provenance: 191 factors pinned to upstream SHA `bfcf848`, with per-family IR characterization and pairwise IC correlation matrix. Reusable if/when the hypothesis is revisited at a different bar frequency.
- IC computation pipeline validated for multi-column OHLV panels on CSI300 — the schema gap resolved by the H53 OHLV ingestion is now a reusable data layer for any future OHLV-dependent Hxx slice.
- Composite IR formula confirmed: IR_composite ≈ IR_avg × √N / √(1+(N−1)ρ_mean) accurately predicts the observed diversification ceiling — operationally validated gate for any future composite design.

**What we throw away (no longer authoritative):**
- The hypothesis that core supplement OHLV data is the bottleneck preventing single gtja191 factors from achieving IR>0.5 on CSI300 — **definitively falsified.** OHLV data lifts IR by only 6.6%, far short of the ~2× needed to cross the 0.5 bar.
- The hypothesis that a simple equal-weight composite of top-IR gtja191 factors can achieve IR>0.5 — the theoretical ceiling analysis (composite IR ≈ 0.43-0.50) shows it would BARELY scrape by even under optimal decorrelation, making any composite strategy fragile and dependent on correlation stability.
- Charter §5 hypothesis #4 (cross-sectional composite rank from single zoo factors) — killed per brief `kill_when` semantics. 0/191 factors pass, well below the <3 threshold.

**Lessons codified:**
- **OHLV data does not break the A-share daily IR ceiling.** This is the most expensive lesson of the research cycle (1 Hxx slice consumed, following 3 zero-slice spikes). The finding that OHLV columns produce only marginal IR improvement (6.6%) is now experimentally grounded — not a theoretical assumption. Future Charter hypotheses should assume a daily-bar IR ceiling of ~0.25 for any factor family on CSI300 and design thresholds accordingly.
- **Chunked YFinance is operationally validated for bulk ingestion.** When Tushare/Akshare are unavailable, a chunked approach (50 tickers/batch, 2s sleep) successfully ingests ~700 tickers in ~80 seconds. This pattern should be codified as the standard fallback in any OHLV-dependent briefing template.
- **The spike-before-Hxx discipline is cost-justified.** Three spikes (A1, close-only, R1) correctly escalated the research: A1 caught a schema gap → close-only proved signal exists but below IR threshold → R1 proved composite ceiling is mathematically bounded. H53 consumed 1 slice to deliver the definitive OHLV answer. The alternative (escalating to Hxx immediately after A1) would have consumed 1 slice on the schema-gap answer alone, without the architectural convergence that the spike trio established first.
- **Factor family convergence to a uniform IR ceiling is a durable finding.** The fact that 5 independent theme families (volume, reversal, volatility, microstructure, momentum) ALL converge to IR~0.25 suggests a market-structure-imposed bound, not a formula-dependent one. This should inform Charter §5 gate design: relaxation to IR>0.3 would be a meaningful (but still challenging) bar; IR>0.5 is architecturally unreachable for daily-bar A-share single factors.
- **The composite IR formula should be a pre-escalation gate.** Before any future Hxx designs a factor composite, compute the theoretical ceiling from pairwise IC correlations. If ceiling < target_gate, do not escalate — the diversification math is deterministic. This could have prevented H53's composite ceiling analysis from being necessary: the top-3 pairwise correlations alone would have shown IR ceiling ≈ 0.43 < 0.5.

**Next move:** Per H53 report § 9 Recommendation, kill Charter §5 hypothesis #4 (cross-sectional composite rank from single zoo factors). The hypothesis is preserved for potential resurrection under different signal universes, but the daily-bar factor zoo path is exhausted. Surface 5 next viable Charter candidates:
1. §5 #1 (gate relaxation): lower IR bar to 0.3, test top-3 gtja191 composite — the 0.43 theoretical ceiling might pass a relaxed gate, but the result would be fragile (dependent on correlation stability).
2. §5 #2 (non-momentum family composite): assemble composite from low-vol + quality-momentum signals — different factor universe might diversify beyond current top-3 correlations.
3. §5 #3 (cost-sensitive re-evaluation): apply realistic transaction costs to H48 top-15 strategies — the QV composite that nearly beat HS300 might pass with realistic slippage models.
4. **NEW — Intraday bar frequency:** test 5-min or 30-min bar factors. Daily-bar IR ceiling at ~0.25 may not apply at higher frequencies. Requires intraday data pipeline (Tushare `mins` endpoint or equivalent).
5. **NEW — Alternative prediction metric:** switch from IC/IR (cross-sectional rank prediction) to a decay-adjusted return prediction metric. IC measures instantaneous rank correlation; a decay-adjusted metric might capture sustained signal that IC misses.

**Charter budget state:** 1 Hxx slice consumed (H53), 5 remain in Charter v1 §3 pool. 3 spikes consumed ~30 wall-minutes with zero slice cost. Total Charter spend: 1 slice, ~65 wall-minutes.


## H53-FIX — Tushare-qfq OHLCV+amount Rerun — SIGNAL_NEGATIVE upheld on complete data (2026-05-31)

**Classification:** Bug fix completing H53 (denominator correction per `docs/agents/workflow.md` Bug≠Slice). NOT a new Hxx, does NOT consume a Charter §3 slice. Charter budget unchanged (1 slice consumed = H53; 5 remain).

**Verdict:** H53's SIGNAL_NEGATIVE is **upheld on a complete, consistent dataset**. 0 of 191 gtja191 factors pass `|mean_ic| > 0.03 AND IR > 0.5` — same verdict as H53, now on 190/191 computable factors (was 139), the intended `tushare:pro_bar:qfq` source (was degraded YFinance), amount 100% populated (was 0% NaN), and a universe-aligned panel (was raw-vs-qfq mismatch).

**Why this rerun happened (claude-code review of H53, 2026-05-31):** H53's "0/191 kill" rested on three data defects: (1) the dispatch silently fell back to YFinance because `scripts/ingest_cn_pit_ohlv.py` reads `os.environ` for `TUSHARE_TOKEN` with no `launchctl getenv` fallback — the token exists in launchctl (cron jobs inherit it; interactive dispatch did not); (2) `amount` was 100% NaN, leaving 34 amount/volume factors untested; (3) raw OHLV (697–793 tickers) was mixed with qfq close (482), causing 18 broadcast-error COMPUTE_FAILED. A kill on a degraded, incomplete factor set was premature.

**What changed:** OHLCV+amount re-ingested from tushare pro_bar qfq (475/482 tickers, 183,732 rows, amount 100% non-null, `.SH→.SS` suffix normalized to join the H47 universe). Same gtja191 factor files (commit `bfcf8488`) and same IC harness, only the OHLV input swapped.

**Result (H53 → H53-FIX):** OK 139→190 · COMPUTE_FAILED 18→0 · COMPUTE_THIN 34→1 (alpha_138) · passing 0→0. Best |IR| 0.258 (alpha_080) → 0.259 (alpha_054). The now-fully-tested amount theme tops out at |IR|=0.254, volume at 0.255 — both far below 0.5. Every theme (volume/volatility/microstructure/momentum/reversal/amount) converges to max |IR| ~0.05–0.26.

**Decisive point:** fixing the data raised computability by 51 factors but left the verdict identical. The ~0.25 single-factor IR ceiling on A-share daily-bar CSI300 is confirmed market-structure-imposed, not a data-coverage artifact — now across **five** independent tests (A1 / close-only / R1 / H53 / H53-FIX).

**Integrity:** frozen `prices_h47_tushare_qfq_candidate.csv` sha256 `34f3e38f1245ffd8…` unchanged (pre==post). H53 originals untouched (json mtime 13:22, sha `a8255abd…`). All new outputs are `h53fix_*`.

**Artifacts:** `data/cn_pit/ohlcv_h53fix_tushare_qfq.csv`, `data/cn_pit/ohlcv_coverage_h53fix.json`, `backtest/runs/h53fix_gtja191_ic_bench.json`, `reports/h53fix_gtja191_ic_bench_report.md`, `scripts/h53fix_fetch_tushare_ohlcv.py`, `scripts/h53fix_run_ic_bench.py`, brief `docs/hermes-h53fix-tushare-ohlcv-amount-rerun-task.md`.

**Process notes (two exit-0≠acceptance events, cf. H49a):** (1) Hermes's first dispatch produced a PENDING-template report before any data existed; (2) Hermes ingested the data and wrote a correct IC bench script but exited clean **without running it**, leaving no bench JSON. Per user authorization, claude-code ran the unmodified `h53fix_run_ic_bench.py` verbatim to produce the JSON and filled the report/sync-note with real numbers. No factor or harness logic was changed by the reviewer.

**Lessons codified:**
- A clean kill needs a clean dataset. H53's 0/191 was directionally right but rested on degraded/incomplete data; the defensible kill is H53-FIX's 0/191 on complete data + intended source. Restating a verdict on a corrected denominator is a bug fix, not a slice.
- Token plumbing: ingest scripts must fall back to `launchctl getenv TUSHARE_TOKEN` (mirror `scripts/h33_execution_audit.py:87`), else interactive (non-cron) dispatch silently degrades the data source. One-line fix queued as a separate bug commit (NOT done here).
- tushare `ts.pro_bar(adj="qfq")` returns OHLCV+amount with `.SH` suffix; the H47 matrix uses `.SS` — normalize before join. `pro.pro_bar()` does not work; `ts.pro_bar()` does.
- exit-0 ≠ acceptance: verify the artifact (JSON exists with real numbers + sha) before trusting a clean process exit.

**Next move:** Daily-bar zoo-factor path is exhausted across both close-only and full-OHLCV configurations. Recommend the next Charter §5 hypothesis from the H53 menu — gate relaxation (IR>0.3) / non-momentum family composite / cost-sensitive re-eval of H48 top-15 / intraday bar frequency. User/Codex to choose which (if any) to spike.

**Open follow-ups (NOT done, for user/Codex):**
1. One-line bug commit: add `launchctl getenv` fallback to `scripts/ingest_cn_pit_ohlv.py` token loader.
2. Engine PR split: the ~791-line uncommitted diff in `backtest/market_data.py` + `agents/coordinator.py` (ENGINE-OHLV-V1) must be committed as a standalone engine PR + `engine-frozen-vN` tag before any of this is treated as resting on a frozen engine.
