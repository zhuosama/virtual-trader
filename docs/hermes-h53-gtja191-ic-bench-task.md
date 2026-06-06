# Hermes H53 Task — gtja191 Zoo IC Bench on OHLV-Unlocked CSI300

Date: 2026-05-31
Status: DRAFT — ready for Hermes dispatch
Owner: claude-code (PM) / Hermes (executor)

## Context

A1 spike (2026-05-30, `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike.md`) tested 10 gtja191 factors on H47 close-only matrix → **SIGNAL_NEGATIVE** because 9/10 factors required OHLCV columns. ENGINE-OHLV-V1 PR (2026-05-31, `docs/superpowers/plans/2026-05-30-ohlv-supplemental-engine-pr.md`) added a supplemental OHLV layer. H53 re-tests the **full gtja191 zoo (~191 factors)** with multi-column data unlocked.

## Objective

One sentence: identify all gtja191 factors that achieve `|mean_ic| > 0.03 AND IR > 0.5` on the H47 CSI300 universe over the H28 baseline period (2025-01-01 → 2026-05-18), using the post-ENGINE-OHLV-V1 OHLV supplement.

## Charter Reference (MANDATORY)

- **Charter:** `docs/research-charter-v1.md` (v1.0-DRAFT)
- **Question (decision-grade Y/N):** Do ≥3 gtja191 factors (from upstream HKUDS/Vibe-Trading @ commit `bfcf848826750d5f74d0daa636eaffe02b894fad`) achieve `|mean_ic| > 0.03 AND IR > 0.5` on H47 CSI300 universe (2025-01-01 → 2026-05-18) when computed against the post-ENGINE-OHLV-V1 OHLV supplement?
- **Threshold:** `|mean_ic| > 0.03 AND IR > 0.5` per factor; ≥3 factors must pass. **Inherited from spike-tier PROPOSED_THRESHOLD** (parent spec Q3 — Charter v2 retrofit pending). All results tagged `RESEARCH_ONLY-PERMANENT` until Charter v2 formalizes factor-level thresholds.
- **Budget:** `max_wall_hours = 8`, `max_revisions = 1`. Slice budget cost: **1 of 6 Charter §3 slices**.
- **kill_when:** "If <3 factors pass thresholds → write KILLED postmortem under `docs/strategy-optimization-sync.md`, return slice budget to Charter pool, recommend next Charter §5 hypothesis. If ≥3 factors pass → escalate to H54 with proposed composite design (NOT auto-promote to active.json — H54 is a separate Hxx)."

## Inputs

- `data/cn_pit/ohlv_h47_supplement.csv` — **REQUIRED full ingestion** before H53 starts. Currently smoke-state (200 rows × 10 tickers). H53 Task 1 = trigger full ingestion (730 ticker × ~1500 trading days ≈ 1M rows).
- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` — Charter §2 frozen close-only matrix (sha `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc`), READ-ONLY.
- `data/cn_pit/universe.jsonl` — HS300 H47 PIT universe, READ-ONLY.
- `data/cn_pit/metadata.json` — verify post-Task-1 `ohlv_layer.rows` ≥ 700,000 + `ohlv_layer.sha256` matches actual file. READ-ONLY for existing 8 keys.
- Upstream gtja191 factor definitions — fetch via `gh api repos/HKUDS/Vibe-Trading/contents/zoo/gtja191` at commit `bfcf848826750d5f74d0daa636eaffe02b894fad` (same pin as A1 spike, confirmed in `docs/strategy-optimization-sync.md § A1 Spike`).

## Outputs

- `backtest/factors/gtja191/` — new directory; per-factor Python module (191 files OR single registry file, executor's call)
- `backtest/factors/MANIFEST.sha256` — per-factor sha256 + upstream commit SHA + borrowed date (per parent spec §5.5)
- `backtest/factors/UPSTREAM_DIFF.md` — local schema adapter notes (column name mappings, any factor that required local adaptation)
- `backtest/runs/h53_gtja191_ic_bench.json` — full IC results JSON (one entry per factor: factor_id, mean_ic, std_ic, ir, n_obs, valid_pct, status, columns_used)
- `reports/h53_gtja191_ic_bench_report.md` — Markdown report (decision, top-10 by IR, per-family analysis, comparison to spike trio, composite candidates if ≥3 pass)
- `THIRD_PARTY_NOTICES.md` — UPDATE to fill the upstream commit SHA placeholder seeded by A2 PR Task 6.3
- `docs/strategy-optimization-sync.md` — APPEND new H53 section per Hxx convention

## Hard Prohibitions

### Always Applicable (Standard Boilerplate — copied verbatim from `AGENTS.md`)

- **No data fabrication**: do NOT add, modify, "complete", or "round up" rows in any protected artifact (anything under `data/cn_pit/`, any prior Hxx run JSON, any prior Hxx report). If a gap/NaN/missing row is encountered, SURFACE as a finding and STOP — do NOT silently patch. Original verdicts (e.g., `CANDIDATE_DATASET 99.91%`) are authoritative.
- **No source provenance forgery**: borrowed factor files must retain upstream copyright header + commit SHA reference in `THIRD_PARTY_NOTICES.md`. Hand-implemented adapters cite formula source.
- **Symmetric restore**: any optional file modification (monkey-patch, runtime patch, backup) MUST use `try/finally`.
- **Original ingestion verdicts immutable**: do NOT modify any prior Hxx run JSON, any prior `validation_report*.json`, any A1/close-only/R1 spike report.
- **Exit-code is not acceptance**: every Acceptance Gate criterion must be physically verifiable (file exists, numerical assertion holds, sha256 matches).
- **Modification reporting**: final response MUST enumerate every file created or modified.
- **No silent workarounds**: missing tokens, missing endpoints, schema mismatches → STOP and surface. Do not invent workarounds. (cf. A1 spike Hermes correctly BLOCKED on alphabetical fallback; close-only spike Hermes correctly surfaced scipy missing.)
- **sha256 audit hooks**: pre + post for any data mutation. Audits raise hard on mismatch, never silent log.

### Task-Specific Prohibitions

- Do **NOT** modify `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (Charter §2 frozen, sha `34f3e38f...`)
- Do **NOT** modify `data/cn_pit/prices.csv` (H28 baseline, sha `5efc8ec7...`)
- Do **NOT** modify `data/cn_pit/universe.jsonl`, `fundamentals.jsonl`, `universe_snapshots.jsonl`, or any existing key in `metadata.json` (the new `ohlv_layer` key may be re-written if full ingestion replaces smoke data; ALL OTHER keys verbatim)
- Do **NOT** modify `agents/audit_layer.py`, `strategies/active.json`, `backtest/backtest_engine.py`, `backtest/oos_window.py`, `backtest/market_data.py` (post-ENGINE-OHLV-V1+fix frozen)
- Do **NOT** install new pip packages (numpy + pandas + scipy already available)
- Do **NOT** promote any factor to active.json or strategies/proposals/ — H53 is RESEARCH ONLY
- Do **NOT** run any backtest that depends on `agents/coordinator.py` or `strategies/active.json` — H53 is factor-level IC only
- Do **NOT** override Charter §5 hypothesis #4 thresholds without explicit Charter v2 PR (use PROPOSED_THRESHOLD tagging)

## Task Breakdown

### Task 1 — OHLV Full Ingestion (~1-2 wall hours, deferred from ENGINE-OHLV-V1)

**Budget**: 90 wall-minutes (Akshare typically 1-2 ticker/sec, fallback YFinance can rate-limit)

- [ ] Verify pre-state: `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` sha = `34f3e38f...`, `prices.csv` sha = `5efc8ec7...`, 5 baseline frozen file shas all unchanged from baseline anchor.
- [ ] Trigger full ingestion: `python3 scripts/ingest_cn_pit_ohlv.py --fetch-ohlv` (no --limit-tickers, default --start 2020-01-02 --end 2026-05-18)
- [ ] Monitor stderr for `[WARN provider]` lines — Akshare network instability is expected (post-fix, per-ticker failures are now visible). Fallback chain auto-promotes to YFinance which is more reliable.
- [ ] Post-state: ohlv_h47_supplement.csv should have ≥700,000 rows (730 tickers × ~1500 trading days, allowing 20-30% per-ticker failures); `metadata.json.ohlv_layer.missing_pairs` if populated by ingestion script (currently NOT — known follow-up gap; OK if missing).
- [ ] Re-verify 5 baseline-frozen sha unchanged; `metadata.json` 8 existing keys verbatim.

**Kill if**: ANY of 5 baseline sha changes (= protected artifact violation → revert + STOP), OR ohlv_h47_supplement.csv ends up <300K rows (= massive failure across both Akshare + YFinance → escalate as engine issue, NOT H53 problem).

### Task 2 — gtja191 Borrow + Adapter (~60 wall-minutes)

- [ ] Fetch all 191 gtja191 factor files from upstream HKUDS/Vibe-Trading @ commit `bfcf848826750d5f74d0daa636eaffe02b894fad` (use `gh api` or `curl`; same pin as A1)
- [ ] Per-file sha256 logged to `backtest/factors/MANIFEST.sha256`
- [ ] Update `THIRD_PARTY_NOTICES.md` with the actual commit SHA (replacing the `<pending — fork & pin in A1 PR>` placeholder from A2 PR Task 6.3)
- [ ] Identify column requirements per factor: parse formulas, build dependency map of column names (open/high/low/close/volume/amount/vwap)
- [ ] For factors using columns NOT in local OHLCV (likely `vwap` if upstream uses it but local doesn't), write to `unsupported.md` and exclude from IC bench (mark as `STATUS=UNSUPPORTED_COLUMN`)
- [ ] Build `backtest/factors/gtja191/` directory: option A = 191 individual .py files (mirrors upstream); option B = single `factors.py` with all 191 functions. Executor's call — A is more auditable, B is faster to write.
- [ ] Schema adapter: write `backtest/factors/UPSTREAM_DIFF.md` documenting any local column rename or adapter pattern

### Task 3 — IC Bench (~90 wall-minutes for 191 factors × CSI300)

- [ ] Load OHLV panel from `ohlv_h47_supplement.csv` (long format) → pivot to wide per-column DataFrames (open/high/low/close/volume/amount)
- [ ] Load close panel from `prices_h47_tushare_qfq_candidate.csv` (already wide)
- [ ] For each gtja191 factor (in MANIFEST.sha256):
  - Compute factor values over period 2025-01-01 → 2026-05-18
  - Compute forward 1-day return from close panel: `returns = close.pct_change().shift(-1)`
  - Cross-sectional Pearson rank IC per date
  - Aggregate: mean_ic, std_ic, ir, n_obs, valid_pct, rolling 60d IR mean+last
- [ ] Compute-fail handling: if factor raises, write row with status=COMPUTE_FAILED + exception class; continue. valid_pct < 30% → status=COMPUTE_THIN.
- [ ] Output `backtest/runs/h53_gtja191_ic_bench.json` (191 rows + summary block)

### Task 4 — Decision + Report (~60 wall-minutes)

- [ ] Apply threshold: |mean_ic| > 0.03 AND IR > 0.5 per factor
- [ ] Count passing factors:
  - **≥3 pass** → outcome `SIGNAL_POSITIVE_PROPOSED`; recommend H54 brief for composite design
  - **1-2 pass** → outcome `SIGNAL_PARTIAL`; recommend deeper spike on the 1-2 candidates
  - **0 pass** → outcome `SIGNAL_NEGATIVE`; recommend kill of cross-sectional rank hypothesis (even with OHLV, A-share intraday signals may not extract IR>0.5 at single-factor granularity)
- [ ] Write `reports/h53_gtja191_ic_bench_report.md`:
  - Decision
  - Top-10 factors by IR
  - Per-family analysis (gtja191 categories: technical, volume, volatility, reversal — group by formula signature)
  - Comparison to spike trio (A1: gtja191_010 IR=-0.028; close-only: rev_1d IR=0.242; R1: composite IR=0.272 → does OHLV unlock raise the ceiling above 0.5?)
  - Composite candidates (top-3 by IR + low pairwise IC correlation — pre-compute correlation matrix; recommend H54 only if mean correlation < 0.7)
  - Time spent vs 8h budget

### Task 5 — Append H53 Postmortem to strategy-optimization-sync.md (~15 wall-minutes)

- [ ] APPEND new section `## H53 — gtja191 Zoo IC Bench (OHLV-unlocked) — <verdict> (2026-05-31)`
- [ ] Mirror structure of preceding 3 spike postmortems (Verdict / Original question / What was delivered / Sunk cost / Root cause / What we keep / What we throw away / Lessons codified / Next move)
- [ ] Next move section: if SIGNAL_POSITIVE_PROPOSED → cite H54 brief plan; if SIGNAL_PARTIAL → cite deeper-spike plan; if SIGNAL_NEGATIVE → cite kill rationale + suggest next Charter §5 hypothesis

## Smoke Command (executor: skip — H53 has no spike-grade smoke distinct from Task 3)

Sanity check before full Task 3 IC bench:
```bash
python3 -c "
import pandas as pd
df = pd.read_csv('data/cn_pit/ohlv_h47_supplement.csv')
print('rows:', len(df))
print('tickers:', df.ticker.nunique())
print('date range:', df.date.min(), '→', df.date.max())
print('columns present:', sorted(df.columns))
"
```

## Full Command (executor)

Task 1 + 2 + 3 + 4 + 5 in single dispatch (8h wall budget).

## Acceptance Gates (ALL must hold for H53 to be marked COMPLETE)

| # | Check | Expected |
|---|---|---|
| H53-AG-1 | 5 baseline frozen file shas unchanged | per `/tmp/protected_shas_before_task6_smoke.txt` |
| H53-AG-2 | `data/cn_pit/metadata.json` 8 existing keys verbatim preserved (ohlv_layer may be re-written by Task 1 — only its content changes) | json diff |
| H53-AG-3 | OHLV ingestion complete: ohlv_h47_supplement.csv ≥ 300K rows (allowing 50% per-ticker failure tolerance) | `wc -l` |
| H53-AG-4 | gtja191 MANIFEST.sha256 has ≥150 entries with upstream commit SHA `bfcf848826750d5f74d0daa636eaffe02b894fad` | grep + count |
| H53-AG-5 | h53_gtja191_ic_bench.json has ≥150 rows with non-empty status field (UNSUPPORTED_COLUMN / COMPUTE_FAILED / COMPUTE_THIN / OK acceptable) | json count |
| H53-AG-6 | Report includes per-family analysis + comparison to spike trio + composite candidates | grep check |
| H53-AG-7 | strategy-optimization-sync.md has new H53 section with verdict | tail check |
| H53-AG-8 | 229+ unit tests still pass (no regression from any incidental change) | `python3 -m unittest discover tests/audit_layer/` |

## Rollback SOP

**Code layer**: `git revert <H53 merge sha>` would restore everything except the OHLV ingestion data (full ohlv_h47_supplement.csv). Acceptable since OHLV data is regenerable.

**Artifact layer**: 
- `backtest/factors/gtja191/`: safe to delete (new dir, no downstream deps until H54)
- `backtest/runs/h53_gtja191_ic_bench.json`: keep on disk, mark deprecated in `data/cn_pit/metadata.json.deprecated_runs` (per A2 PR rollback pattern)
- `strategy-optimization-sync.md § H53` postmortem: keep (history is immutable per AGENTS.md)

## Time Budget

| Task | Estimate (wall) |
|---|---|
| 1. OHLV full ingestion | 90 min |
| 2. gtja191 borrow + adapter | 60 min |
| 3. IC bench (191 factors) | 90 min |
| 4. Decision + report | 60 min |
| 5. Postmortem append | 15 min |
| **Total** | **5h 15min** (vs 8h budget) |

Hermes historical 5-10x faster than estimate. Realistic Hermes wall: **2-3h actual**.

## Hermes Scope Confirmation (per Charter §6)

H53 is **within Hermes scope**:
- Bulk I/O ✅ (factor fetch from upstream, IC compute, report drafting)
- Markdown drafting ✅ (postmortem, report)
- sha256 audit ✅ (factor manifest)
- Read-only diffs ✅ (existing PIT artifacts)
- **NOT** running strategy acceptance verdict (H42 9-condition gate) — RESEARCH ONLY tagging
- **NOT** writing new strategy scripts — factor IC is research, not strategy
- **NOT** monkey-patching — adapters are explicit modules in `backtest/factors/`
- **NOT** modifying protected data artifacts

## Charter §3 Budget Impact

Consuming **1 of 6 Charter slices**. After H53: 5 slices remain in Charter v1 budget.
