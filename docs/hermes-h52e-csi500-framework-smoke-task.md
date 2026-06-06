# H52e — CSI500 Search Framework Smoke Test

## Context

H52a/b/c/d closed; the full CSI500 data foundation is on disk (1074 universe + sector + qfq prices + ADTV + PIT fundamentals). H52e is the **first integration test** that proves the existing H30 search infrastructure (H42 / H50b / H51b scripts) can swap CSI500 data in via **path constants + sha256 dicts** with ZERO source-code changes.

If H52e passes, H52f (the full H42→H51b chain on CSI500 with real verdicts) is unblocked. If H52e finds a schema/contract incompatibility, we fix the H52e harness or the data shape, NOT the H30 scripts (they remain immutable).

H52e is the equivalent of a "build-and-link test" for a data-source upgrade — it does not produce strategy verdicts (smoke params guarantee RESEARCH_ONLY), but verifies the wiring.

## Objective

Run three minimal smoke backtests on CSI500 data (one each for H42 search, H50b quality-value search, H51b risk model search), using monkey-patched path constants + sha256 dicts so the existing H30 scripts run unchanged. Verify all three complete, produce valid JSON with a `verdict` field, and write provenance that references H52a-d (not H30).

## Inputs

- `data/cn_pit/universe_h52a_csi500.jsonl` + `universe_snapshots_h52a_csi500.jsonl` (H52a)
- `data/cn_pit/sector_metadata_h52b_csi500.csv` + `sector_coverage_h52b.json` (H52b)
- `data/cn_pit/prices_h52c_csi500_qfq.csv` + `liquidity_h52c_csi500_daily_amount.csv` (H52c)
- `data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl` (H52d)
- `scripts/h42_strategy_redesign_search.py` (READ-ONLY library)
- `scripts/h50b_quality_value_search.py` (READ-ONLY library)
- `scripts/h51b_risk_model_search.py` (READ-ONLY library)
- `backtest/experiments/fundamental_backtest.py` (READ-ONLY library)

## Outputs

- `scripts/h52e_csi500_framework_smoke.py` — harness that drives all three sub-smokes
- `backtest/runs/fundamental_value_h52e_csi500_smoke_h42.json` — H42 search smoke result
- `backtest/runs/fundamental_value_h52e_csi500_smoke_h50b.json` — H50b smoke result
- `backtest/runs/fundamental_value_h52e_csi500_smoke_h51b.json` — H51b smoke result
- `reports/h52e_csi500_framework_smoke_report.md` — unified report (3 sub-runs)
- `tests/test_h52e_csi500_framework_smoke.py`
- `scripts/validate_hxx_artifacts.py` — register `h52e` with `validate_h52e` checker

## Hard Prohibitions

- Do NOT modify `scripts/h42_strategy_redesign_search.py` (search logic, gate thresholds, overlay families, path constants, INPUT_SHA256 if any).
- Do NOT modify `scripts/h50b_quality_value_search.py` (scorer logic, path constants, INPUT_SHA256 dict, ValueScoreH50, monkey-patch installer).
- Do NOT modify `scripts/h51b_risk_model_search.py` (sizing logic, path constants, H51B_INPUT_SHA256 dict, monkey-patch installer).
- Do NOT modify `backtest/experiments/fundamental_backtest.py`.
- Do NOT modify any input artifact:
  - H28: `universe.jsonl`, `universe_snapshots.jsonl`, `fundamentals.jsonl`
  - H30: `universe_h30_candidate.jsonl`, `universe_snapshots_h30_candidate.jsonl`, `prices_h47_tushare_qfq_candidate.csv`, `sector_metadata_sw_l1.csv`, `fundamentals_h50a_pit_quality.jsonl`, `liquidity_h51a_daily_amount.csv`
  - H52a/b/c/d: `universe_h52a_csi500.jsonl`, `universe_snapshots_h52a_csi500.jsonl`, `sector_metadata_h52b_csi500.csv`, `prices_h52c_csi500_qfq.csv`, `liquidity_h52c_csi500_daily_amount.csv`, `fundamentals_h52d_csi500_pit_quality.jsonl`
- Do NOT modify prior run JSONs / reports: `fundamental_value_h42_*.json`, `fundamental_value_h48_*.json`, `fundamental_value_h49b_*.json`, `fundamental_value_h50b_*.json`, `fundamental_value_h51b_*.json` and their corresponding reports.
- Do NOT modify production trading config; do not place live orders.
- No network: no Tushare, no yfinance, no anything.
- Do NOT author commits as `codex` or `claude-code`.
- Do NOT loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT skip the finally-block restore of monkey-patched module constants. Cross-test pollution would silently corrupt H30-flavored runs elsewhere.

## Design Decisions

### D1. Path Injection — Explicit CLI First, Monkey-Patch Only as Fallback

**Empirical CLI surface (confirmed by grep on the 3 sub-scripts):**

| Script | Path CLI args exposed | Path constants without CLI |
|---|---|---|
| H42 | `--prices-file`, `--universe-file`, `--snapshots-file` | none |
| H50b | none (only `--output-run`, `--output-report`, `--stage-*`, `--top-k`, `--capital`, `--top-overlays`) | `H47_PRICES`, `H30_UNIVERSE`, `H30_SNAPSHOTS`, `SECTOR_CSV`, `H50A_JSONL` |
| H51b | none (only `--output-run`, `--output-report`, `--stage-b-limit`, `--capital`) | `H47_PRICES`, `H30_UNIVERSE`, `H30_SNAPSHOTS`, `SECTOR_CSV`, `H50A_JSONL`, `H51A_ADTV` |

**Hard rule (defensive against argparse eager-binding subtleties):** wherever a sub-script exposes a path CLI argument, H52e MUST pass the CSI500 path via the explicit CLI argument array. Monkey-patching the underlying module constant is ONLY a fallback for path constants without a CLI fallback (the 5–6 H50b/H51b constants above) and for hardcoded internals like `INPUT_SHA256` / `H51B_INPUT_SHA256` dicts.

The rationale: `default=DEFAULT_CONSTANT` in `parser.add_argument` does evaluate the symbol at parser-construction time. If `parser.add_argument()` is called inside `main()` (as in H42), the symbol is looked up from the module global namespace AT THAT MOMENT, so a prior monkey-patch DOES propagate. But this lazy-evaluation guarantee is fragile across refactors and easy to lose silently. Explicit CLI passing eliminates the dependency on Python's name-lookup timing entirely.

**Per-sub-script injection plan:**

```python
# H42 — full CLI coverage; no monkey-patch needed for paths
h42_argv = [
    "--prices-file",    str(CSI500_PRICES),
    "--universe-file",  str(CSI500_UNIVERSE),
    "--snapshots-file", str(CSI500_SNAPSHOTS),
    "--stage-a-limit", "1",
    "--stage-b-limit", "3",
    "--top-k",         "1",
    "--output-run",    str(OUT_DIR / "fundamental_value_h52e_csi500_smoke_h42.json"),
    "--output-report", str(OUT_DIR / "h52e_smoke_h42_partial.md"),
]
h42.main(h42_argv)   # NO monkey-patch needed for H42

# H50b — path constants must monkey-patch; output paths go via CLI
h50b_patches = {
    "H47_PRICES":     CSI500_PRICES,
    "H30_UNIVERSE":   CSI500_UNIVERSE,
    "H30_SNAPSHOTS":  CSI500_SNAPSHOTS,
    "SECTOR_CSV":     CSI500_SECTOR,
    "H50A_JSONL":     CSI500_FUNDAMENTALS,
    "INPUT_SHA256":   csi500_sha256_dict(),   # always required: hardcoded internal dict
}
h50b_argv = [
    "--stage-a-limit", "1",
    "--stage-b-limit", "3",
    "--top-k",         "1",
    "--output-run",    str(OUT_DIR / "fundamental_value_h52e_csi500_smoke_h50b.json"),
    "--output-report", str(OUT_DIR / "h52e_smoke_h50b_partial.md"),
]
captured = {k: getattr(h50b, k) for k in h50b_patches}
try:
    for k, v in h50b_patches.items():
        setattr(h50b, k, v)
    h50b.main(h50b_argv)
finally:
    for k, v in captured.items():
        setattr(h50b, k, v)

# H51b — same as H50b + H51A_ADTV + H51B_INPUT_SHA256 (NOT INPUT_SHA256; check the actual name)
h51b_patches = {
    "H47_PRICES":         CSI500_PRICES,
    "H30_UNIVERSE":       CSI500_UNIVERSE,
    "H30_SNAPSHOTS":      CSI500_SNAPSHOTS,
    "SECTOR_CSV":         CSI500_SECTOR,
    "H50A_JSONL":         CSI500_FUNDAMENTALS,
    "H51A_ADTV":          CSI500_ADTV,
    "H51B_INPUT_SHA256":  csi500_sha256_dict(),
}
h51b_argv = [
    "--stage-b-limit",  "3",
    "--output-run",     str(OUT_DIR / "fundamental_value_h52e_csi500_smoke_h51b.json"),
    "--output-report",  str(OUT_DIR / "h52e_smoke_h51b_partial.md"),
    "--capital",        "500000",
]
captured = {k: getattr(h51b, k) for k in h51b_patches}
try:
    for k, v in h51b_patches.items():
        setattr(h51b, k, v)
    h51b.main(h51b_argv)
finally:
    for k, v in captured.items():
        setattr(h51b, k, v)
```

**Post-run audit hook (mandatory, catches both "patch didn't take" and "argparse eager-bound" failure modes):**

After EACH sub-run, immediately load the resulting JSON and assert:

1. `data_sources.prices.sha256` (or equivalent provenance field) equals `file_sha256(CSI500_PRICES)`.
2. `data_sources.sector_metadata.sha256` equals `file_sha256(CSI500_SECTOR)`.
3. For H50b: `data_sources.fundamentals.sha256` equals `file_sha256(CSI500_FUNDAMENTALS)`.
4. For H51b: `data_sources.adtv_liquidity.sha256` equals `file_sha256(CSI500_ADTV)`.
5. `inputs.prices_file` (or equivalent path field) string ends with one of `prices_h52c_csi500_qfq.csv` / `universe_h52a_csi500.jsonl` / etc.

If any assertion fails, raise immediately with a diagnostic showing actual vs expected sha256 — do NOT mark the sub-run as SUCCESS and silently corrupt the H52e verdict.

**Sub-script main() signature requirement:** each sub-script's `main()` MUST accept an optional `argv: List[str]` parameter (most argparse-based scripts do via `parser.parse_args(argv)` rather than `parser.parse_args()`). If any sub-script's `main()` doesn't accept `argv` (uses bare `sys.argv` directly), H52e harness instead manipulates `sys.argv` via `unittest.mock.patch.object(sys, 'argv', [...])` for the duration of the call. This is itself ugly; if encountered, surface as a MEDIUM finding and consider whether the sub-script needs a minimal one-line fix to accept `argv` (which would technically modify the protected H30 script — BLOCKER — so prefer the sys.argv patch).

### D2. SHA256 Dict Synchronization

H50b and H51b have hardcoded `INPUT_SHA256` (and `H51B_INPUT_SHA256`) dicts that match H30/H47/H49a/H50a/H51a file sha256s. Their internal provenance check raises if actual file sha256 doesn't match the dict.

H52e harness MUST recompute sha256 for the 5-6 CSI500 files and replace the dict atomically with the path patches. The keys (e.g., `"sector_metadata"`, `"fundamentals"`) stay the same; only values change.

```python
def csi500_sha256_dict():
    return {
        "universe":         file_sha256(CSI500_UNIVERSE),
        "universe_snapshots": file_sha256(CSI500_SNAPSHOTS),
        "prices":           file_sha256(CSI500_PRICES),
        "sector_metadata":  file_sha256(CSI500_SECTOR),
        "fundamentals":     file_sha256(CSI500_FUNDAMENTALS),
        "adtv":             file_sha256(CSI500_ADTV),    # only for H51b
    }
```

### D3. ValueScoreH50 + Sizing Patches (reuse existing installers)

H50b and H51b already install their own ValueScoreH50 / sizing monkey-patches via internal installer functions (called from their `main()`). H52e does NOT re-install these — calling each script's `main()` triggers the script's own installer chain. The only H52e contribution is the **path/sha256 patches** layered on top.

CRITICAL: H52e's finally block runs AFTER each sub-script's main() completes — which means the sub-script's own finally has already restored ValueScore / run_fundamental_backtest. H52e only needs to restore the path constants it patched, NOT the scorer/sizing.

### D4. Smoke Parameters (minimal, fast, RESEARCH_ONLY guaranteed)

Each sub-run:
- `--stage-a-limit 1` (1 overlay screened)
- `--stage-b-limit 3` (3 param combos)
- `--top-k 1` (1 candidate to Stage C)
- Default windows (cal_2024 + h1_2025 + h2_2025 + ytd_2026 + deploy — needed for multi-window robustness)

Total per-sub wall ~2-4 min. Three sub-runs ~10-12 min total.

Expected verdict per sub: **RESEARCH_ONLY** (smoke params won't pass any gate; the point is the pipeline completes).

### D5. No "Full" Mode

H52e is smoke-only. There is no `--full` flag. H52f is the full-pipeline counterpart (separate Hxx with proper gate evaluation).

### D6. Output Namespace Isolation

All H52e outputs use the `h52e_csi500_smoke_` prefix. They do NOT collide with H42/H50b/H51b's H30 outputs at any path. Original H30 run JSONs and reports stay immutable.

### D7. Validator + Tests

`validate_h52e` checks:
- All 3 sub-JSONs exist.
- Each has `verdict` field (string, not null).
- Each has `data_sources` (or equivalent provenance) with sha256s matching the actual CSI500 file sha256s.
- Each sub-JSON's `inputs.prices_file` (or equivalent path field) ends with one of the H52c/H52a paths (proves CSI500 not H30 was used).
- Unified report exists with section per sub-run.

Tests:
- Synthetic mini-fixture verifying path patches restore in finally (capture before, patch, run dummy function, finally restore, assert post == before).
- sha256 dict computation correctness.
- 3 sub-runs produce 3 valid JSON files (mock the sub-script `main()` calls with stubs that write minimal valid JSON to verify the harness wiring).

## Provenance Block (in each sub-run JSON, written by each sub-script's main()):

The H42/H50b/H51b scripts already write provenance. With H52e's patches in place, the sub-scripts naturally write CSI500 sha256s (because the patched paths point at CSI500 files). The H52e harness does NOT post-process the JSONs; it lets the sub-scripts write their own provenance.

**The audit hook:** if any sub-JSON's provenance still has H30/H47/H49a/H50a/H51a sha256s (which means our patches didn't take effect), `validate_h52e` fails the run.

## Acceptance Gate

H52e closure checklist:

- [ ] All 4 outputs exist (3 sub-JSONs + unified report + harness script).
- [ ] All 3 sub-JSONs have valid `verdict` field (RESEARCH_ONLY expected; any value other than empty string passes).
- [ ] Each sub-JSON's provenance references CSI500 sha256s (NOT H30/H47/H49a/H50a/H51a) — **harness asserts this immediately after each sub-run per D1's post-run audit hook; failure raises hard, not silently logs**.
- [ ] H42 sub-run invocation uses explicit `--prices-file` / `--universe-file` / `--snapshots-file` CLI args (NOT monkey-patch) — verifiable from harness source.
- [ ] H50b / H51b sub-run invocations: paths via monkey-patch (no CLI alternative); output paths via CLI (`--output-run`, `--output-report`).
- [ ] `h42_strategy_redesign_search.py`, `h50b_quality_value_search.py`, `h51b_risk_model_search.py`, `fundamental_backtest.py` mtimes unchanged from before H52e dispatch.
- [ ] All 12 protected input files (H28 ×3 + H30 ×6 + H52a-d ×6 minus some overlap) `git status --short` clean.
- [ ] All prior H42/H48/H49b/H50b/H51b run JSONs untouched.
- [ ] H42 + H50b + H51b regression tests (`pytest tests/test_h42_*.py tests/test_h50b_*.py tests/test_h51b_*.py`) all green after H52e module load (proves H52e didn't pollute global state).
- [ ] `validate_h52e` registered and passing.
- [ ] All 18 family validators PASS (17 existing + h52e).
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Smoke Command

```bash
python scripts/h52e_csi500_framework_smoke.py --dry-run --output-dir /tmp/h52e_smoke
```

Expected smoke result (dry-run only patches paths + validates schemas, does NOT actually run backtests):
- Exits 0.
- Confirms all 3 sub-scripts importable.
- Confirms path patches succeed for all 3.
- Confirms sha256 dict computation works.
- Does NOT touch `backtest/runs/`, `reports/`, or run real backtests.

## Full Command

```bash
python scripts/h52e_csi500_framework_smoke.py
```

Runs all 3 sub-smokes sequentially. Wall ~10-12 min.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h52e
python scripts/validate_hxx_artifacts.py                                # all 18 artifacts must PASS
pytest tests/test_h52e_csi500_framework_smoke.py \
       tests/test_validate_hxx_artifacts.py \
       tests/test_h42_strategy_redesign_search.py \
       tests/test_h50b_quality_value_search.py \
       tests/test_h51b_risk_model_search.py -q
git status --short \
  scripts/h42_strategy_redesign_search.py \
  scripts/h50b_quality_value_search.py \
  scripts/h51b_risk_model_search.py \
  backtest/experiments/fundamental_backtest.py \
  data/cn_pit/universe.jsonl \
  data/cn_pit/universe_snapshots.jsonl \
  data/cn_pit/fundamentals.jsonl \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/sector_metadata_sw_l1.csv \
  data/cn_pit/fundamentals_h50a_pit_quality.jsonl \
  data/cn_pit/liquidity_h51a_daily_amount.csv \
  data/cn_pit/universe_h52a_csi500.jsonl \
  data/cn_pit/sector_metadata_h52b_csi500.csv \
  data/cn_pit/prices_h52c_csi500_qfq.csv \
  data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl \
  data/cn_pit/liquidity_h52c_csi500_daily_amount.csv \
  backtest/runs/fundamental_value_h42_strategy_redesign_search.json \
  backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json \
  backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json \
  backtest/runs/fundamental_value_h50b_quality_value_search.json \
  backtest/runs/fundamental_value_h51b_risk_model_search.json
# All 21 paths above MUST print nothing.
```

## Closure Note

- Append H52e row to `docs/strategy-optimization-sync.md` under new `## H52e — CSI500 Framework Smoke Snapshot` heading: pipeline health summary, per-sub-run verdict + row count + exception count, harness file count, regression test status.
- Flip `docs/agents/next-slices.md` H52e entry from `OPEN` to `DONE`; flip H52f from `BLOCKED` to `OPEN`.

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- Does the path-swap monkey-patch correctly restore ALL captured constants in the finally block, even when the sub-script raises an exception mid-run?
- Does the INPUT_SHA256 dict recomputation use the SAME hash function (SHA256, hex digest) as H50b/H51b's internal validator?
- **(BLOCKER fix verification)** For H42, is the harness using EXPLICIT CLI arg passing (`--prices-file`, `--universe-file`, `--snapshots-file`) and NOT relying on monkey-patching the path constants? Defensive principle: argparse `default=CONSTANT` evaluation timing is subtle; explicit CLI eliminates the dependency.
- For H50b / H51b (paths NOT exposed as CLI args), are the patched module-level constants actually picked up by `main()`? Verify by checking the JSON's provenance block reflects CSI500 sha256s after each sub-run via the post-run audit hook (D1).
- Is the post-run audit hook (D1) actually invoked after each sub-run and does it raise hard on sha256 mismatch (not just log and continue)?
- Does the H42 sub-smoke produce a comparable Stage A/B/C structure to the H50b/H51b sub-smokes? (H42 has different CLI args; ensure the parameter mapping is consistent.)
- Are the H42 + H50b + H51b regression tests in the verification block actually testing process-local restoration, or are they trivially passing because they don't touch the patched globals?
- Are tests deterministic and free of network calls?
