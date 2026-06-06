# H52f — CSI500 H42→H51b Full Pipeline Rerun

## Context

H52a/b/c/d/e all closed. H52e proved (in 11.6 s smoke) that H42 / H50b / H51b can swap CSI500 data via path injection without source-code edits — explicit CLI args for H42, monkey-patch + sha256-dict patches for H50b/H51b, three sub-runs all SMOKE_PASS with provenance correctly referencing CSI500 sha256s.

H52e left **one known harness gap** that smoke didn't trip but a full run will: the H51b smoke loaded `liquidity_h51a_daily_amount.csv` (H30 ADTV) instead of `liquidity_h52c_csi500_daily_amount.csv` (CSI500 ADTV) because the H52e harness didn't patch `H51A_ADTV`. Smoke params skipped the ADTV-cap branch so it didn't matter; H52f's full run with vol-scaled sizing + ADTV cap WILL exercise the ADTV path and corrupt the verdict if the wrong data flows in. **H52f harness MUST fix this.**

H52f is the H52 track's culmination: run the full search chain (H42 → H49b → H50b → H51b, four sub-pipelines; H48 skipped as redundant since CSI500 prices are already on unified qfq via H52c) with PRODUCTION search params on CSI500 data, and produce real verdicts plus a 5-way comparison vs the H30 chain's 1/5 ceiling.

The expected outcome is informative under any branch:

- **If CSI500 lifts `beat_HS300_windows` above the 1/5 H30 ceiling** in any sub-pipeline: validates the H45 PRD bet that mid-cap universe carries more factor purity; enter Phase B paper-only forward observation on the winning candidate.
- **If CSI500 stays at the same 1/5 ceiling across all 4 sub-pipelines**: the structural finding hardens — beating HS300 with quality-value + sector + risk overlay is not achievable in either HS300 or CSI500 universes under current PIT feature set. Escalate either to CSI1000 (next universe rung) or accept paper-only as terminal.

## Objective

Run four sub-pipelines (H42 / H49b / H50b / H51b search scripts) at production params against CSI500 data (H52a–d), producing per-sub run JSONs and reports. Fix the H51A_ADTV→CSI500-ADTV gap H52e left. Emit a master comparison report covering H30 vs CSI500 verdicts across all four sub-pipelines.

## Inputs

- `data/cn_pit/universe_h52a_csi500.jsonl` + `universe_snapshots_h52a_csi500.jsonl` (H52a)
- `data/cn_pit/sector_metadata_h52b_csi500.csv` (H52b)
- `data/cn_pit/prices_h52c_csi500_qfq.csv` + `liquidity_h52c_csi500_daily_amount.csv` (H52c)
- `data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl` (H52d)
- `scripts/h42_strategy_redesign_search.py` (READ-ONLY library)
- `scripts/h50b_quality_value_search.py` (READ-ONLY library)
- `scripts/h51b_risk_model_search.py` (READ-ONLY library)
- `backtest/experiments/fundamental_backtest.py` (READ-ONLY library)
- `scripts/h52e_csi500_framework_smoke.py` (READ-ONLY reference for path-injection patterns)
- Prior H30 run JSONs for comparison: `fundamental_value_h42_strategy_redesign_search.json`, `fundamental_value_h49b_sector_neutral_rs_search.json`, `fundamental_value_h50b_quality_value_search.json`, `fundamental_value_h51b_risk_model_search.json`

## Outputs

- `scripts/h52f_csi500_full_pipeline.py` — harness that drives all 4 sub-runs
- 4 sub-run JSONs:
  - `backtest/runs/fundamental_value_h52f_csi500_h42.json`
  - `backtest/runs/fundamental_value_h52f_csi500_h49b.json`
  - `backtest/runs/fundamental_value_h52f_csi500_h50b.json`
  - `backtest/runs/fundamental_value_h52f_csi500_h51b.json`
- 4 sub-run reports:
  - `reports/h52f_csi500_h42_report.md`
  - `reports/h52f_csi500_h49b_report.md`
  - `reports/h52f_csi500_h50b_report.md`
  - `reports/h52f_csi500_h51b_report.md`
- 1 master comparison report: `reports/h52f_csi500_full_pipeline_master_report.md`
- `tests/test_h52f_csi500_full_pipeline.py`
- `scripts/validate_hxx_artifacts.py` — register `h52f` (single artifact family covering the master report + provenance-asserts all 4 sub-runs)

## Hard Prohibitions

- Do NOT modify `scripts/h42_strategy_redesign_search.py`, `scripts/h50b_quality_value_search.py`, `scripts/h51b_risk_model_search.py`, `scripts/h52e_csi500_framework_smoke.py`, `backtest/experiments/fundamental_backtest.py`.
- Do NOT modify ANY input data file:
  - H28: `universe.jsonl`, `universe_snapshots.jsonl`, `fundamentals.jsonl`
  - H30: `universe_h30_candidate.jsonl`, `universe_snapshots_h30_candidate.jsonl`, `prices_h47_tushare_qfq_candidate.csv`, `sector_metadata_sw_l1.csv`, `fundamentals_h50a_pit_quality.jsonl`, `liquidity_h51a_daily_amount.csv`
  - H52a-d: `universe_h52a_csi500.jsonl`, `universe_snapshots_h52a_csi500.jsonl`, `sector_metadata_h52b_csi500.csv`, `prices_h52c_csi500_qfq.csv`, `liquidity_h52c_csi500_daily_amount.csv`, `fundamentals_h52d_csi500_pit_quality.jsonl`
- Do NOT modify any prior H42/H48/H49b/H50b/H51b/H52e run JSON or report.
- Do NOT modify production trading config; do not place live orders.
- No network: no Tushare, no yfinance.
- Do NOT author commits as `codex` or `claude-code`.
- Do NOT loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT skip the finally restore of monkey-patched module constants.
- Do NOT silently log sha256 audit failures — must raise hard.
- Do NOT change the acceptance gate (H42 verbatim) or the windows. The whole point is to compare CSI500 verdicts to H30 verdicts under IDENTICAL gate logic.

## Design Decisions (locked unless overridden before dispatch)

### D1. Sub-Pipeline Set (4 sub-runs; H48 skipped)

Run these four in sequence:

1. **h42** — `scripts/h42_strategy_redesign_search.py` (parameter redesign baseline)
2. **h49b** — `scripts/h49b_sector_neutral_rs_search.py` (sector-neutral RS)
3. **h50b** — `scripts/h50b_quality_value_search.py` (quality-value composite + scorer substitution)
4. **h51b** — `scripts/h51b_risk_model_search.py` (risk model overlay + scorer + sizing substitution)

**H48 explicitly skipped**: H48 was a price-source ablation on H42 (yfinance → unified Tushare qfq). CSI500 data (H52c) is already on unified qfq from the start, so an H52f-H48 run would be byte-identical to H52f-H42. Skip; document in master report.

If H49b script is not present in the repo (per earlier grep `scripts/h49b_sector_neutral_rs_search.py` exists), include it. If absent, surface BLOCKER.

### D2. Path Injection (inherit H52e patterns + fix H51A_ADTV gap + H49b CLI surface)

**Empirical CLI surface (verified by grep at brief-drafting time — Hermes MUST re-verify before dispatch):**

| Script | CLI args for INPUT paths | Constants needing monkey-patch |
|---|---|---|
| H42 | `--prices-file`, `--universe-file`, `--snapshots-file` | none |
| **H49b** | `--prices-file`, `--universe-file`, `--snapshots-file` (verified line 1417-1419) | `SECTOR_CSV` + INPUT_SHA256 dict (if H49b has one) |
| H50b | NONE (only stage/output/capital) | `H47_PRICES`, `H30_UNIVERSE`, `H30_SNAPSHOTS`, `SECTOR_CSV`, `H50A_JSONL`, `INPUT_SHA256` |
| H51b | NONE (only stage-b-limit/output/capital) | `H47_PRICES`, `H30_UNIVERSE`, `H30_SNAPSHOTS`, `SECTOR_CSV`, `H50A_JSONL`, `H51A_ADTV`, `H51B_INPUT_SHA256` |

**Defensive Rule (inherit H52e BLOCKER fix):** wherever a sub-script exposes a path CLI arg, H52f MUST use the CLI arg (NOT rely on monkey-patching the constant). Monkey-patching the constant before calling `main(argv)` MAY work due to Python's lazy name lookup in `default=CONSTANT`, but explicit CLI passing eliminates the dependency on argparse's binding-time semantics entirely. If ANY of H49b/H50b/H51b exposes path CLI args that this brief currently lists under "monkey-patch" (e.g., if H50b is refactored in the future to add `--prices-file`), the harness MUST switch to CLI for that path; monkey-patch becomes fallback for hardcoded-only constants. **Hermes MUST re-grep each sub-script's `add_argument` calls at brief-execution time and update the patch/CLI split accordingly. Document the discovered CLI surface in the harness source as a comment block.**

**H42 — explicit CLI args (full coverage):**

```python
h42_argv = [
    "--prices-file",     str(CSI500_PRICES),
    "--universe-file",   str(CSI500_UNIVERSE),
    "--snapshots-file",  str(CSI500_SNAPSHOTS),
    "--output-run",     str(OUT_DIR / "fundamental_value_h52f_csi500_h42.json"),
    "--output-report",  str(OUT_DIR / "h52f_csi500_h42_report.md"),
]
h42.main(h42_argv)   # NO monkey-patch needed
```

**H49b — CLI for paths + monkey-patch for SECTOR_CSV (hybrid):**

```python
h49b_patches = {
    "SECTOR_CSV":     CSI500_SECTOR,                    # no CLI; monkey-patch required
    "INPUT_SHA256":   csi500_sha256_dict(),             # if H49b has this dict; Hermes verifies via grep
}
h49b_argv = [
    "--prices-file",    str(CSI500_PRICES),             # H49b DOES expose this CLI arg → use it
    "--universe-file",  str(CSI500_UNIVERSE),
    "--snapshots-file", str(CSI500_SNAPSHOTS),
    "--output-run",     str(OUT_DIR / "fundamental_value_h52f_csi500_h49b.json"),
    "--output-report",  str(OUT_DIR / "h52f_csi500_h49b_report.md"),
]
captured = {k: getattr(h49b, k) for k in h49b_patches}
try:
    for k, v in h49b_patches.items(): setattr(h49b, k, v)
    h49b.main(h49b_argv)
finally:
    for k, v in captured.items(): setattr(h49b, k, v)
```

**H50b — monkey-patch only (no path CLI exposed):**

```python
h50b_patches = {
    "H47_PRICES":     CSI500_PRICES,
    "H30_UNIVERSE":   CSI500_UNIVERSE,
    "H30_SNAPSHOTS":  CSI500_SNAPSHOTS,
    "SECTOR_CSV":     CSI500_SECTOR,
    "H50A_JSONL":     CSI500_FUNDAMENTALS,    # H52d
    "INPUT_SHA256":   csi500_sha256_dict(),
}
# capture-patch-finally pattern; output via CLI
```

**H51b — monkey-patch only + ADTV fix:**

```python
h51b_patches = {
    "H47_PRICES":         CSI500_PRICES,
    "H30_UNIVERSE":       CSI500_UNIVERSE,
    "H30_SNAPSHOTS":      CSI500_SNAPSHOTS,
    "SECTOR_CSV":         CSI500_SECTOR,
    "H50A_JSONL":         CSI500_FUNDAMENTALS,
    "H51A_ADTV":          CSI500_ADTV,         # ← H52e gap closer
    "H51B_INPUT_SHA256":  csi500_sha256_dict(),
}
```

**Critical**: `csi500_sha256_dict()` MUST include the ADTV key with `file_sha256(CSI500_ADTV)` (= H52c liquidity sha256). H52e's harness arguably included this in the dict already; H52f's post-run audit (D4) verifies it actually applies under the production-params ADTV-cap code path that smoke skipped.

### D3. Production Search Params (per sub)

Use each sub-script's PRODUCTION defaults — NOT smoke defaults. Concretely:

- **h42**: no `--stage-a-limit`, default `--stage-b-limit 200`, default `--top-k 15` (matches H30 H42 run JSON; verify against `fundamental_value_h42_strategy_redesign_search.json` Stage A/B/C counts).
- **h49b**: production defaults (mirror H30 H49b — match its Stage A/B/C counts in the run JSON).
- **h50b**: production defaults (mirror H30 H50b).
- **h51b**: no `--stage-b-limit` (run all 18 risk combos), default `--capital`.

Comparing CSI500 verdicts to H30 verdicts is only meaningful if the param search space is IDENTICAL. Brief explicitly requires Hermes to load each H30 sub-run's JSON, read its `stage_a_count` / `stage_b_count` / `stage_c_count`, and reproduce the same counts in the CSI500 sub-run (modulo CSI500-data-driven differences in clean-deploy-candidate count after filtering, which is allowed and surfaced in the comparison).

### D4. Post-Run Audit (mandatory, per sub)

After EACH sub-run, immediately load the resulting JSON and assert:

1. `data_sources` block has the expected CSI500 sha256s (computed from H52a-d files at harness start). For h51b specifically: `data_sources.adtv_liquidity.sha256 == file_sha256(CSI500_ADTV)` — this is the H52e gap closer.
2. `inputs.*_file` paths (where present in the sub-script's run JSON) end with CSI500 file basenames.
3. The sub-run produced at least 1 candidate (smoke threshold; production should produce many).
4. `verdict` field is present and non-empty.

Failure → raise HARD with diagnostic showing actual vs expected sha256. Do NOT continue to the next sub-pipeline; do NOT mark H52f as complete.

### D5. Stage C Ranking (inherit H49b D6)

For each sub-run that exposes Stage C ranking control (h49b / h50b / h51b), use `beat_HS300_windows desc, deploy_excess desc tiebreaker` (the established convention from H49b D6). H42 uses its own default (Sharpe) — leave unchanged since modifying H42 is forbidden.

### D6. Master Comparison Report

`reports/h52f_csi500_full_pipeline_master_report.md` MUST include:

- **8-row × 5-column comparison table**: 4 sub-pipelines × (H30 verdict, H30 gate-pass, H30 max beat_HS300, H30 best deploy excess, **CSI500 verdict, CSI500 gate-pass, CSI500 max beat_HS300, CSI500 best deploy excess**). 4 sub-pipelines × 2 universes = 8 rows.
- One-line interpretation per sub-pipeline: did CSI500 lift the metric vs H30?
- Aggregate verdict: `CSI500_BREAKTHROUGH` (any sub passes gate AND beat_HS300 ≥ 2/5), `CSI500_IMPROVED` (any sub strictly improves beat_HS300 count vs H30 but doesn't pass gate), `CSI500_PARITY` (no improvement on either gate or beat_HS300), `CSI500_REGRESSION` (worse than H30).
- Project-level next-step recommendation derived from the aggregate verdict.

### D7. `beat_CSI500_windows` Diagnostic — DEFERRED

The plan doc mentioned `beat_CSI500_windows` as a diagnostic field. Implementing it requires the CSI500 index level as a column in the prices CSV (currently only HS300 is there). H52c does NOT include a CSI500 index column. Adding it would require modifying H52c (forbidden) or a separate ingestion + path injection layer.

H52f defers this. The master report records `beat_HS300_windows` only. If H52f's aggregate verdict is `CSI500_PARITY` or `CSI500_REGRESSION` and the user wants the CSI500-as-benchmark diagnostic to inform the next step, that becomes a separate H52g slice.

### D8. Wall Time Budget + Resumability

Per-sub wall estimate (CSI500 has 1074 vs H30's 481 tickers ≈ 2.2× larger):
- h42 production: H30 took ~16 min → CSI500 estimate **30-40 min**
- h49b production: H30 took ~16 min → estimate **30-40 min**
- h50b production: H30 took ~12 min → estimate **25-35 min**
- h51b production: H30 took ~5 min (only 18 combos) → estimate **8-12 min**

**Total H52f full run: 95-130 min (~1.5-2.2 hours).**

Each sub-run produces its own JSON+report independently. If one sub fails, the others should still have their outputs. The harness MUST process subs SEQUENTIALLY (not parallel — Tushare-free but local CPU constrained on multi-core overhead) and write each sub's outputs IMMEDIATELY upon completion (no buffering until end-of-pipeline).

**Per-sub resumability**: if a sub's output JSON already exists at harness start AND its provenance sha256s match the CSI500 file sha256s, the harness SKIPS the sub-run and just re-reads the existing JSON for the comparison report. This allows partial restarts. Set `--force` flag to override and re-run all subs.

## Provenance Block (per sub-run JSON, written by each sub-script's main()):

Each sub-script writes its own provenance (as in H30 runs). With H52f's patches applied, the provenance naturally reflects CSI500 sha256s (because patched paths point at CSI500 files, and patched INPUT_SHA256 dicts contain CSI500 sha256s). Audit hook (D4) verifies.

For the master report's provenance: a top-level `data_sources_csi500` block listing the 6 CSI500 file paths + sha256s, computed once at harness start.

## Acceptance Gate

Per sub-pipeline (inherit H42 gate verbatim, 9 conditions):
- `CANDIDATE_FOR_FORWARD_TRIAL` if at least one candidate passes all 9 H42 conditions
- `RESEARCH_ONLY` otherwise

H52f overall verdict (master report):
- `CSI500_BREAKTHROUGH` if any sub yields `CANDIDATE_FOR_FORWARD_TRIAL` AND that candidate has `beat_HS300_windows >= 2/5`
- `CSI500_IMPROVED` if no sub passes gate but max `beat_HS300_windows` across all CSI500 subs > 1/5
- `CSI500_PARITY` if max `beat_HS300_windows` == 1/5 (same as H30 ceiling)
- `CSI500_REGRESSION` if max `beat_HS300_windows` == 0/5 (worse than H30)

H52f closure checklist:

- [ ] All 4 sub-run JSONs + 4 sub-reports + master report exist.
- [ ] Each sub-JSON has valid `verdict` field.
- [ ] Each sub-JSON's provenance sha256s match CSI500 files (D4 audit hook PASSED post each sub).
- [ ] **h51b sub-run loaded H52c ADTV** (NOT H51a) — verifiable via h51b sub-JSON's `data_sources.adtv_liquidity.sha256 == file_sha256(CSI500_ADTV)`. This is the H52e gap closer; if violated, H52f is invalid.
- [ ] Library file mtimes unchanged: h42_search / h49b_search / h50b / h51b / fundamental_backtest / h52e_smoke.
- [ ] All 20+ protected data + run JSON files `git status --short` clean.
- [ ] H42 + H50b + H51b regression tests still pass after H52f module load.
- [ ] `validate_h52f` registered and passing.
- [ ] All 19 family validators PASS (18 existing + h52f).
- [ ] Master report contains 8-row × 5-col comparison table + per-sub interpretation + aggregate verdict + next-step recommendation.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Smoke Command

```bash
python scripts/h52f_csi500_full_pipeline.py --dry-run --sub h42 --output-dir /tmp/h52f_smoke
```

Expected smoke result:
- Exits 0.
- Validates harness wiring on one sub-pipeline at minimal params (`--stage-b-limit 3 --top-k 1`).
- Confirms ADTV patch applied for h51b path when invoked with `--sub h51b --dry-run`.
- Does NOT touch `backtest/runs/`, `reports/`, or run real backtests.

## Full Command

```bash
python scripts/h52f_csi500_full_pipeline.py
```

Runs all 4 sub-pipelines sequentially at production params. Wall ~95-130 min total.

Resume mode (if interrupted):
```bash
python scripts/h52f_csi500_full_pipeline.py    # detects existing sub-JSONs, skips completed ones
python scripts/h52f_csi500_full_pipeline.py --force   # forces re-run of all subs
```

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h52f
python scripts/validate_hxx_artifacts.py                                # all 19 artifacts (18 existing + h52f) must PASS
pytest tests/test_h52f_csi500_full_pipeline.py \
       tests/test_validate_hxx_artifacts.py \
       tests/test_h42_strategy_redesign_search.py \
       tests/test_h50b_quality_value_search.py \
       tests/test_h51b_risk_model_search.py -q
git status --short \
  scripts/h42_strategy_redesign_search.py \
  scripts/h50b_quality_value_search.py \
  scripts/h51b_risk_model_search.py \
  scripts/h52e_csi500_framework_smoke.py \
  backtest/experiments/fundamental_backtest.py \
  data/cn_pit/universe.jsonl \
  data/cn_pit/universe_snapshots.jsonl \
  data/cn_pit/fundamentals.jsonl \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/universe_snapshots_h30_candidate.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/sector_metadata_sw_l1.csv \
  data/cn_pit/fundamentals_h50a_pit_quality.jsonl \
  data/cn_pit/liquidity_h51a_daily_amount.csv \
  data/cn_pit/universe_h52a_csi500.jsonl \
  data/cn_pit/universe_snapshots_h52a_csi500.jsonl \
  data/cn_pit/sector_metadata_h52b_csi500.csv \
  data/cn_pit/prices_h52c_csi500_qfq.csv \
  data/cn_pit/liquidity_h52c_csi500_daily_amount.csv \
  data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl \
  backtest/runs/fundamental_value_h42_strategy_redesign_search.json \
  backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json \
  backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json \
  backtest/runs/fundamental_value_h50b_quality_value_search.json \
  backtest/runs/fundamental_value_h51b_risk_model_search.json
# Above MUST print nothing — 25 protected paths unchanged.
```

## Closure Note

- Append H52f row to `docs/strategy-optimization-sync.md` under new `## H52f — CSI500 Full Pipeline Snapshot` heading: per-sub verdicts + per-sub max beat_HS300_windows + per-sub best deploy excess + aggregate verdict + 8-row comparison summary + next-step recommendation.
- Flip `docs/agents/next-slices.md` H52f entry from `OPEN` (set so when H52e closed) to `DONE`.
- If aggregate verdict is `CSI500_BREAKTHROUGH`: add a new slice "H53 — CSI500 Candidate Paper-Forward Monitoring" as OPEN.
- If aggregate verdict is `CSI500_PARITY` or `CSI500_REGRESSION`: add a new entry to "Project-Level Findings" section in sync doc noting "Universe expansion from HS300 to CSI500 did not lift the multi-window robustness ceiling; next: consider CSI1000 expansion (~2-3 more months) or accept terminal paper-only state."

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- **(H52e gap closer verification)** Does the H52f harness include `H51A_ADTV` in the `h51b_patches` dict pointing at `CSI500_ADTV` (= H52c liquidity)? Verify by reading the h51b sub-JSON's `data_sources.adtv_liquidity.sha256` after the run completes and confirming it matches `file_sha256(CSI500_ADTV)`.
- **(production params verification)** Does each sub-run actually use production search params, not smoke defaults? Verify by comparing each CSI500 sub-run's `stage_a_count` / `stage_b_count` / `stage_c_count` to the corresponding H30 run's counts.
- Does the harness process subs SEQUENTIALLY and write each sub's outputs IMMEDIATELY (not buffered)? If one sub crashes, the others' outputs should still be on disk.
- Does the resumability skip-completed-subs logic correctly verify sha256s before skipping? (Risk: a stale sub-JSON from a previous run with a different data version could trick the harness into thinking the sub is "done".)
- Is the master report's aggregate verdict logic robust to edge cases (e.g., one sub fails entirely while others succeed)?
- Does the H42 sub-run skip the monkey-patch path (uses CLI only)? Regression risk from H52e: someone might add unnecessary patches for H42 that aren't needed and could mask wiring bugs.
- **(CLI surface defensive verification)** Did the harness re-grep each sub-script's `add_argument` calls at execution time and document the discovered CLI surface in source comments? If H49b/H50b/H51b are later refactored to add input-path CLI args, the harness MUST switch to CLI for those (NOT rely on monkey-patch alone). For each sub, confirm: if a path is exposed as CLI arg, it is passed via argv; if NOT exposed, it is monkey-patched. No silent drift.
- Are tests deterministic and free of network calls?
