# H52g — CSI500 Zero-Candidate Diagnostic

## Context

H52f closed `CSI500_REGRESSION` per brief's classification logic — but the underlying signal is **not "CSI500 alpha is weaker"**, it's **"the H42 backtest engine rejected ALL CSI500 candidates"**. Specifically:

- h42 sub-run: 7424 stage_b backtests executed → **0 clean_deploy candidates** survived the per-window data-quality filter
- h49b / h50b sub-runs: same pattern, 0 clean_deploy
- h51b: 18 risk combos × 0 rebalances triggered

`backtest/experiments/fundamental_backtest.py:967-975` runs two deploy_blockers checks:
- `n_days >= MIN_TRADING_DAYS (=126)` — portfolio active for ≥6 months
- `n_sells >= MIN_TRADE_COUNT (=30)` — ≥30 closed sells in the window

H30 H42 cleared both easily (clean_deploy_count=96). CSI500 H42 has 0. Before launching Plan B (CSI1000 expansion / paper-only terminal), we need to know WHICH filter fires and WHY on CSI500 data.

H52g is a 1-backtest deep trace, not a search. Goal: identify the first failing condition and propose H52h fix path.

## Objective

Run ONE identical H42 backtest on H30 data and CSI500 data side-by-side via direct `run_fundamental_backtest` call (no search wrapper). Compare BacktestResult fields. Identify the first deploy_blocker / data_quality_meta value that differs. Output diagnostic JSON + root-cause report.

## Inputs

- CSI500 data foundation: `data/cn_pit/universe_h52a_csi500.jsonl`, `universe_snapshots_h52a_csi500.jsonl`, `prices_h52c_csi500_qfq.csv`, `sector_metadata_h52b_csi500.csv` (sector ignored for h42 baseline; included for consistency)
- H30 reference data: `data/cn_pit/universe_h30_candidate.jsonl`, `universe_snapshots_h30_candidate.jsonl`, `prices_h47_tushare_qfq_candidate.csv`
- `backtest/experiments/fundamental_backtest.py` (READ-ONLY; engine to invoke)
- `scripts/h42_strategy_redesign_search.py` (READ-ONLY; reference for ValueScore + selection helpers)
- H30 H42 run JSON (`backtest/runs/fundamental_value_h42_strategy_redesign_search.json`) — for "known working" verification

## Outputs

- `scripts/h52g_csi500_zero_candidate_diagnostic.py` — diagnostic script (~200-300 LOC; direct engine invocation, no search)
- `data/cn_pit/h52g_diagnostic.json` — paired BacktestResult dumps (H30 vs CSI500) + diff + root-cause classification
- `reports/h52g_csi500_zero_candidate_diagnostic_report.md` — narrative with hypothesis testing + fix recommendation
- `tests/test_h52g_csi500_zero_candidate_diagnostic.py`
- `scripts/validate_hxx_artifacts.py` — register `h52g`

## Hard Prohibitions

- Do NOT modify `backtest/experiments/fundamental_backtest.py` (engine source of truth).
- Do NOT modify `scripts/h42_strategy_redesign_search.py` or any H49b/H50b/H51b/H52e/H52f script.
- Do NOT modify ANY input data file (H28 ×3 + H30 ×6 + H52a-d ×6).
- Do NOT modify any prior Hxx run JSON or report (H42/H48/H49b/H50b/H51b/H52e/H52f).
- Do NOT modify production trading config; do not place live orders.
- No network: pure local data analysis.
- Do NOT author commits as `codex` or `claude-code`.
- Do NOT loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT propose a fix that mutates the engine without explicit user sign-off via a follow-up H52h brief — H52g is diagnostic only.

## Design Decisions

### D1. Single Backtest Scenario — H50b baseline (NOT H42)

**Critical (BLOCKER fix from review):** the diagnostic MUST run on the **H50b baseline**, not H42 baseline. Reasoning:

- H42 native code path uses `ValueScore.from_fundamentals` reading `data/cn_pit/fundamentals.jsonl` (H28 baseline, **H30-only ticker set, NO CSI500 fundamentals**).
- If we run H42 on CSI500 universe via path injection, ValueScore returns `None` for 100% of CSI500 tickers (no matching fundamentals) → 0 candidates → trivial false positive that masks the real root cause.
- This wiring trap explains H52f h42 sub-run's 0 candidates trivially, but tells us nothing about H50b/H51b's 0 candidates (those DID correctly mount H52d CSI500 fundamentals via monkey-patch).

Therefore the diagnostic MUST reproduce the H50b environment:
- Apply H50b's `ValueScoreH50` monkey-patch installer (the same one H50b's main() runs internally)
- Mount H52d CSI500 fundamentals (`fundamentals_h52d_csi500_pit_quality.jsonl`)
- Run the backtest in the same wired state as H52f's h50b sub-run produced 0 candidates in

**Locked scenario:**

- **Scorer**: `ValueScoreH50` (from h50b module, monkey-patched into both `fundamental_backtest.ValueScore` and `h42_strategy_redesign_search.ValueScore`)
- **Fundamentals source**: H52d (`fundamentals_h52d_csi500_pit_quality.jsonl`) for CSI500 baseline; H50a (`fundamentals_h50a_pit_quality.jsonl`) for H30 baseline
- **Overlay**: `"rel20_ge_0_and_ma60"` (H50b's empirically best overlay on H30 — chosen so a known-clean H30 candidate exists to compare against)
- **Params** (matched to H50b H30 best candidate from `fundamental_value_h50b_quality_value_search.json` top_candidates_multi_window[0]):
  - `top_n=8`, `max_position_pct=0.08`, `stop_loss_pct=0.08`, `take_profit_pct=0.25`, `quality_filter=0.40`, `rebalance_freq_days=63`, `sector_max_weight_pct=0.20`
- **Sector data**: SW L1 (H49a for H30 baseline; H52b for CSI500 baseline)
- **Deploy window**: `cal_2024` (2024-01-01 → 2024-12-31; 252 trading days)
- **Capital**: 500000 (H50b default)

These EXACT params produced `clean_deploy=True` on H30 in the H50b H30 run (we can verify against H30 H50b run JSON). If CSI500 produces `can_deploy=false` under identical params + scorer + sector + fundamentals path mounting, the divergence is purely the CSI500 data shape — exactly what we want to diagnose.

**Why we keep H42 baseline as a SANITY-CHECK sub-trace, not the primary diagnostic:** the harness optionally runs a secondary H42-baseline trace as a hypothesis confirmer for "H28 fundamentals trap" — expected to show ValueScore returns None for all CSI500 tickers (the trivial bug). If this hypothesis confirms, document but explicitly NOTE this is a known wiring trap, NOT the H50b root cause. The H50b trace is the load-bearing diagnostic.

### D2. Direct Engine Invocation (with H50b's scorer + fundamentals mounted)

The harness MUST replicate the H50b runtime state before invoking the engine:

1. **Apply H50b's ValueScoreH50 patch**: import `h50b_quality_value_search` and call its scorer installer (or replicate it inline — same monkey-patch pattern used in H50b's main():
   `fundamental_backtest.ValueScore = h50b.ValueScoreH50`
   `h42_strategy_redesign_search.ValueScore = h50b.ValueScoreH50`)
2. **Mount the correct fundamentals path** for each baseline:
   - H30 baseline: `h50b.H50A_JSONL = data/cn_pit/fundamentals_h50a_pit_quality.jsonl` (H50a; default)
   - CSI500 baseline: `h50b.H50A_JSONL = data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl` (H52d; via monkey-patch)
3. **Construct sources**: `CN_PIT_FileSource(prices_path, universe_path, snapshots_path)` for each baseline.
4. **Call `run_fundamental_backtest(source, params, overlay, window_start, window_end, capital)`** → captures BacktestResult for each.
5. **Serialize both BacktestResults** (data_quality, deploy_blockers, can_deploy, n_days via `equity_curve` length, n_sells via trades count, metrics dict) to the diagnostic JSON.
6. **In a `finally` block**: restore ValueScore to original; restore H50A_JSONL to original. Required so this diagnostic harness doesn't pollute subsequent test runs.

Avoids the entire search pipeline — directly observes engine behavior on identical inputs, under the same scorer + fundamentals wiring H50b uses.

### D3. Comparison + Diff

The diagnostic JSON includes a `diff` block:

```json
{
  "h30":   {"can_deploy": true,  "deploy_blockers": [],          "n_days": 244, "n_sells": 47, "data_quality_meta": {...}},
  "csi500":{"can_deploy": false, "deploy_blockers": ["..."],     "n_days": 0,   "n_sells": 0,  "data_quality_meta": {...}},
  "diff":  {
    "first_divergence": "data_quality_meta.universe_coverage_pct",
    "h30_value": 1.0,
    "csi500_value": 0.42,
    "candidate_root_cause": "PIT membership snapshot interval too sparse for cal_2024 window"
  }
}
```

The `first_divergence` field identifies the EARLIEST point in the BacktestResult pipeline where the two diverge. Classification table:

| First Divergence Location | Likely Root Cause | Fix Path |
|---|---|---|
| `data_quality.universe_coverage_pct` low | PIT membership snapshots too sparse vs backtest dates | H52h: align snapshot cadence OR ffill membership |
| `data_quality.price_coverage_pct` low | CSI500 prices CSV has too many NaN cells for active members | H52h: investigate H52c qfq compute fallout |
| `deploy_blockers: [insufficient_trading_days]` | Portfolio entered cash too often | H52h: investigate selection-step ValueScore exclusion rate |
| `deploy_blockers: [insufficient_trades]` | Too few rebalances triggered closing | H52h: investigate rebalance loop |
| `metrics.trades == 0` | NO selection happened at all | H52h: investigate ValueScore.from_fundamentals returning None for all CSI500 tickers |
| Other | Surface specific finding | TBD H52h scope |

### D4. Hypothesis-Driven Trace

In addition to the headline divergence, the report tests these specific hypotheses (each as a section). All hypotheses are evaluated under the H50b baseline (ValueScoreH50 + H52d fundamentals) per D1; H_A specifically targets `ValueScoreH50`, NOT the original `ValueScore`.

1. **H_A: ValueScoreH50 exclusion rate** — for CSI500 cal_2024 rebalance dates, how many tickers does `ValueScoreH50.from_fundamentals` (loaded from H52d) return non-None for? Compare to H30 (ValueScoreH50 loaded from H50a). If H50b H30 had ~300/481 (63%) eligible but CSI500 has <100/1074 (<10%), ValueScoreH50's quality_filter=0.40 threshold + per-component minimum (≥ceil(N/2) sub-fields per component) is over-filtering CSI500 — likely because H52d's hard-field coverage (95.8%) and soft-field coverage (92.5%) shift the cross-sectional score distribution vs H50a (similar coverage but different ticker base).

2. **H_B: PIT universe membership** — for each rebalance date in cal_2024, count `len(active_members(snapshot_date))` from snapshots. CSI500 has 88 monthly snapshots (~12.4-day intervals) vs H30 has 125 (~14.8-day intervals — H30 has DENSER coverage); both are roughly monthly. If CSI500's nearest snapshot before a rebalance date is ≥4 weeks old, the active member list may be stale. Verify via direct snapshot-date lookup vs cal_2024's 4 rebalance dates (2024-01-02, 2024-04-04, 2024-07-04, 2024-10-04).

3. **H_C: Price NaN density** — for the selected top_n tickers at each rebalance, count price NaN cells in the next 63 days (rebalance window). H52c had 1075 cols; if a CSI500 ticker was inactive during 2024 (delisted before 2024 or IPO after rebalance date), its column is all-NaN and the position computes 0 returns / no trade closes. Threshold: if >50% NaN within the rebalance window for any selected ticker, flag.

4. **H_D: Universe-rebalance date alignment** — `h42_strategy_redesign_search.py` uses `CN_PIT_FileSource`'s snapshot lookup; verify the lookup correctly returns CSI500 active membership for each cal_2024 rebalance date. The check: for each rebalance date, call `source.get_active_universe(rebal_date)` and assert len > 0 (CSI500 should have ~500 active members).

5. **H_E: H52d fundamentals coverage at rebalance dates** — for each cal_2024 rebalance date, count how many CSI500 active members have a `(ticker, latest filing_date <= rebalance_date)` row in H52d. H52d has 27,327 rows × 27 periods × 1074 tickers (avg 25 periods per ticker). Verify per rebalance date that ≥80% of active CSI500 members have a non-NULL `roe_waa` field available. If <80%, H52d coverage gaps explain the exclusion.

6. **H_F (new): Sector cap interaction** — H50b's sector_max_weight_pct=0.20 cap restricts any single SW L1 industry to ≤20% of portfolio. With CSI500 1074 tickers across 31 SW L1 industries (avg ~35 tickers/industry) and H52b 32.1% multi-mapped, the cap MAY systematically reject portfolio configurations. Check: how many top-8 candidate sets at each cal_2024 rebalance date can be assembled subject to the sector cap? If 0 valid configurations exist at any rebalance date, the cap+universe-distribution is the blocker.

Each hypothesis gets PASS/FAIL with a numeric finding. The hypothesis that explains the divergence is the root cause.

### D5. Root-Cause Classification

The report's verdict is one of:
- `ROOT_CAUSE_IDENTIFIED` — single hypothesis confirmed; H52h fix path proposed
- `MULTI_CAUSE` — multiple hypotheses contribute; H52h needs to address all
- `UNKNOWN` — none of H_A–H_E confirmed; need broader investigation (H52g-V2 or interactive debugging)

## Acceptance Gate

- [ ] Diagnostic JSON exists with both h30 and csi500 BacktestResult dumps.
- [ ] `first_divergence` field populated.
- [ ] Report has H_A–H_E hypothesis sections with PASS/FAIL + numbers.
- [ ] Root-cause verdict written (ROOT_CAUSE_IDENTIFIED / MULTI_CAUSE / UNKNOWN).
- [ ] H52h fix-path recommendation (1-2 paragraphs) included.
- [ ] H30 baseline BacktestResult shows `can_deploy=true` (sanity check that the diagnostic harness reproduces H50b's known H30 best-candidate behavior; if H30 fails, the harness itself is broken or the picked params don't match H50b's actual best candidate).
- [ ] CSI500 baseline BacktestResult shows `can_deploy=false` (confirms H52f h50b sub-run finding under controlled conditions).
- [ ] Optional H42-baseline sanity sub-trace, if executed, confirms the "H28 fundamentals trap" hypothesis (ValueScore returns None for ~100% CSI500 tickers when reading H28 fundamentals) and explicitly documents this as a KNOWN wiring trap NOT the H50b root cause.
- [ ] H_F sector cap interaction hypothesis is evaluated even if H_A/H_B/H_C/H_D/H_E identify the root cause first (the cap is a sufficient blocker under some universe distributions; should be checked regardless).
- [ ] No library file mtimes changed (engine + h42 + h49b/50b/51b/52e/52f all unchanged).
- [ ] Tests cover: harness instantiation, source path injection, hypothesis classification logic.
- [ ] validate_h52g registered.
- [ ] All 20 family validators PASS (19 existing + h52g).
- [ ] No unresolved BLOCKER/HIGH/MEDIUM findings.

## Smoke Command

```bash
python scripts/h52g_csi500_zero_candidate_diagnostic.py --dry-run --output-dir /tmp/h52g_smoke
```

Expected smoke result:
- Exits 0.
- Loads both data sources without exception.
- Validates that source_h30 + source_csi500 both construct correctly.
- Does NOT run actual backtests.
- /tmp output only.

## Full Command

```bash
python scripts/h52g_csi500_zero_candidate_diagnostic.py
```

Wall ~10-20 min (two single-window backtests + 5 hypothesis checks; no search loop).

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h52g
python scripts/validate_hxx_artifacts.py                                # all 20 artifacts must PASS
pytest tests/test_h52g_csi500_zero_candidate_diagnostic.py tests/test_validate_hxx_artifacts.py -q
git status --short \
  scripts/h42_strategy_redesign_search.py \
  scripts/h49b_sector_neutral_rs_search.py \
  scripts/h50b_quality_value_search.py \
  scripts/h51b_risk_model_search.py \
  scripts/h52e_csi500_framework_smoke.py \
  scripts/h52f_csi500_full_pipeline.py \
  backtest/experiments/fundamental_backtest.py \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/universe_h52a_csi500.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/prices_h52c_csi500_qfq.csv \
  backtest/runs/fundamental_value_h52f_csi500_h42.json \
  backtest/runs/fundamental_value_h52f_csi500_h49b.json \
  backtest/runs/fundamental_value_h52f_csi500_h50b.json \
  backtest/runs/fundamental_value_h52f_csi500_h51b.json
# Must print nothing — 15 protected paths unchanged.
```

## Closure Note

- Append H52g row to `docs/strategy-optimization-sync.md` under new `## H52g — CSI500 Zero-Candidate Diagnostic Snapshot` heading: first_divergence + root-cause + H52h fix path recommendation + 1-sentence interpretation of whether CSI500_REGRESSION verdict from H52f should be revised.
- Flip `docs/agents/next-slices.md`: add new H52h slice as OPEN if root-cause is ROOT_CAUSE_IDENTIFIED; if UNKNOWN, add H52g-V2 OPEN instead.

## Review Prompt

Use `docs/agents/review-prophet-template.md`. Focus areas:

- **(BLOCKER fix verification)** Is the diagnostic running on the H50b baseline (ValueScoreH50 + H52d fundamentals), NOT pure H42 baseline (which would trivially fail via the H28 fundamentals trap and mask the real root cause)? Verify the harness source mounts ValueScoreH50 and H50A_JSONL=H52d before calling run_fundamental_backtest.
- Does the diagnostic harness construct H30 and CSI500 `CN_PIT_FileSource` objects with IDENTICAL constructor args (just different file paths)? Any other diff (e.g., date range, schema option) would invalidate the comparison.
- Are the 6 hypothesis checks (H_A–H_F) genuinely independent, or do they depend on each other in ways that mask the true root cause?
- Does the diagnostic JSON's `first_divergence` field use a deterministic ordering (e.g., always check data_quality before deploy_blockers before metrics) so the same data produces the same root-cause classification on every run?
- If H30 BacktestResult unexpectedly shows `can_deploy=false`, does the harness raise hard (the comparison is invalidated; treating it as evidence would be wrong)?
- Is the report explicit that "ROOT_CAUSE_IDENTIFIED" only means "we found ONE divergence" — there may still be multiple downstream issues even if one is identified?
- Are tests deterministic and free of network calls?
