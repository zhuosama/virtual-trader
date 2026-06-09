# H51b — Risk Model Overlay Search

## Context
Blocked by H51a. Once the ADTV data is present, we implement H45 PRD Direction #4 (Risk Model Overlay) on top of the H50b Quality-Value composite signal. 

## Objective
Substitute the naive equal-weight position sizing in H42's fundamental backtester with a risk-aware, volatility-scaled weighting scheme featuring single-name caps, active name minimums, and liquidity constraints.

## Inputs
- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv`
- `data/cn_pit/universe_h30_candidate.jsonl`
- `data/cn_pit/sector_metadata_sw_l1.csv`
- `data/cn_pit/fundamentals_h50a_pit_quality.jsonl`
- `data/cn_pit/liquidity_h51a_daily_amount.csv` (from H51a)
- `scripts/h50b_quality_value_search.py` (for `ValueScoreH50` logic)
- `backtest/experiments/fundamental_backtest.py` (read-only)
- `scripts/h42_strategy_redesign_search.py` (read-only)

## Outputs
- `scripts/h51b_risk_model_search.py`
- `backtest/runs/fundamental_value_h51b_risk_model_search.json`
- `reports/h51b_risk_model_search_report.md`
- `tests/test_h51b_risk_model_search.py`
- `scripts/validate_hxx_artifacts.py` — register `h51b`

## Hard Prohibitions
- Do not modify `scripts/h42_strategy_redesign_search.py` or `backtest/experiments/fundamental_backtest.py`.
- Do not modify `scripts/h50b_quality_value_search.py`.
- Do not overwrite or modify any input artifact, run JSON, or report.
- Do not modify production trading config.
- Do not place live orders.
- No network: do not refetch prices, fundamentals, or sector data.
- Do not print or store the Tushare token.
- Do not author commits as `codex` or `claude-code`.
- Do not loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT skip the runtime substitution restore (`finally` block must run).
- Do NOT add `min_sectors_in_portfolio` back as a search axis.

## Design Decisions

### D1. ValueScoreH50 Reuse
Import `ValueScoreH50` from the `h50b` module and use an identical monkey-patching mechanism as H50b to inject it into the H42 search loop.

### D2. Sizing Mechanism Substitution (concrete strategy)

`backtest/experiments/fundamental_backtest.py:877-883` is inline sizing code inside `run_fundamental_backtest`, not a callable function — so a function-level monkey-patch is not possible. The required mechanism is:

**Replace the entire `run_fundamental_backtest` function via `unittest.mock.patch.object`** at the module level. H51b script:

1. Defines `_run_fundamental_backtest_h51b(...)` with **identical signature** as the original, containing:
   - The same outer loop structure (date iteration, rebalance check, stop-loss / take-profit / quality filter logic).
   - All side-effect helpers reused via direct import: `COMMISSION_RATE`, `STAMP_TAX_RATE`, `TRANSFER_FEE_RATE`, `SLIPPAGE_BPS`, `MIN_TRADE_COUNT`, `MIN_TRADING_DAYS`, `HS300_TICKER`, `DataQuality`, `BacktestResult`.
   - **Only the sizing block** (the 7 lines from `n_slots = ...` to `target_amount = ...`) is replaced with the D3+D4 vol-scaled + cash-buffer + ADTV-cap logic.
2. Capture original: `_FB_RUN_V1 = fundamental_backtest.run_fundamental_backtest`.
3. Patch: `fundamental_backtest.run_fundamental_backtest = _run_fundamental_backtest_h51b`. Also patch the import-time binding in h42 search if it imports the function by name: `h42_strategy_redesign_search.run_fundamental_backtest = _run_fundamental_backtest_h51b` (verify via `grep -n "run_fundamental_backtest" scripts/h42_strategy_redesign_search.py` before substitution; if not imported, skip the h42 patch).
4. Both patches are restored in a `finally` block.
5. Record in run JSON `sizing_substitution` provenance: original function repr, replacement function repr, patched modules list, restored_after_run=true, and the **sizing-block delta in unified-diff format** so reviewers can audit exactly which lines changed.

This mechanism intentionally copy-pastes most of `run_fundamental_backtest` body. The trade-off: ~250 LOC duplication for surgical sizing change vs. modifying a protected file. The H42 regression test (`pytest tests/test_h42_strategy_redesign_search.py`) must still pass after H51b module loads — proves the substitution is process-local and reverted.

### D3. Volatility-Scaled Sizing (PIT-safe, formula pinned)

Per-ticker realized volatility at rebalance date `t`:

```
returns_i = log(close_{i} / close_{i-1})  for i in {t-60, ..., t-1}     # exclusive of t (PIT-safe)
vol_i_annualized = std(returns_i) * sqrt(252)
```

Requirements:
- Window: trailing 60 trading days, EXCLUSIVE of as_of_date (must not look at as_of_date's close, which would be future-leak if rebalance happens at open).
- Minimum non-null return count: ≥40 (~67% of 60). Below threshold → ticker excluded at that rebalance; recorded under `exclusion_stats.vol_insufficient_data`.
- Return basis: log returns (NOT simple returns) — more symmetric for the std calculation.
- Annualization factor: `sqrt(252)` (A-share trading calendar ~252 days/year).

Per-ticker target weight at rebalance date `t`:

```
raw_weight_i = (target_portfolio_vol / vol_i_annualized) / N_eligible
            where N_eligible = count of tickers passing vol-data-sufficient filter at t
```

This implements "Target Volatility" sizing: low-vol stocks get larger weight; high-vol stocks get smaller weight; the constant `target_portfolio_vol / N_eligible` normalizes so that an equal-vol universe would yield `1/N` weights.

The raw_weight is then run through D4 (single-name cap, cash buffer, ADTV cap) to produce final weights.

### D4. Risk Model Constraints (pinned semantics)

Applied in this exact order to D3's raw_weight vector:

**Step 1 — Single-Name Cap (the smaller of two ceilings):**
`weight_i = min(raw_weight_i, D4_single_name_cap, D5_max_position_pct=0.08)`
Both caps apply; D4 axis sweeps the risk overlay cap, D5 max_position_pct is the engine-level hard ceiling. Always use the smaller of the two.

**Step 2 — ADTV Participation Cap:**
For each ticker with target `weight_i`:
- Compute `trade_delta_rmb = abs(weight_i × portfolio_value - current_position_value_rmb)`. This is the actual trade size at rebalance, NOT the target position size — turning over only part of a position should not be capped by absolute size.
- Compute `adtv_20d_rmb = mean(amount_rmb_{t-20..t-1})` from H51a data (trailing 20 trading days, EXCLUSIVE of as_of_date — PIT-safe).
- If `trade_delta_rmb > D4_adtv_cap_pct × adtv_20d_rmb`: truncate `weight_i` so that the trade exactly equals the cap. Remainder of intended weight returns to the cash bucket; do NOT redistribute to other names.
- If `adtv_20d_rmb` is NaN (insufficient H51a coverage): exclude ticker at this rebalance; record under `exclusion_stats.adtv_insufficient_data`.

**Step 3 — Min Active Names (with explicit cash buffer):**
- Assert at script start: `top_n >= min_active_names = 5`. Pinned `top_n = 8` (per D5), `min_active_names = 5`. 8 ≥ 5 ✓.
- Count `n_active = count of tickers with weight_i > 0 after Steps 1-2`.
- If `n_active < min_active_names = 5`: hold the entire portfolio as cash (set all `weight_i = 0`). Record under `exclusion_stats.min_active_names_violated_count` per rebalance. Do NOT concentrate the remaining weight into fewer names.

**Step 4 — Cash-buffer normalization (NO leverage):**
- Compute `total_weight = sum(weight_i)`.
- If `total_weight > 1.0`: scale all weights by `1.0 / total_weight` (this is a degenerate case after Steps 1-3 but defensive).
- If `total_weight < 1.0`: the residual `1.0 - total_weight` stays as cash. Do NOT scale up to fill — that would defeat the risk overlay's purpose.
- The engine `BacktestResult.equity_curve` must reflect the cash drag honestly.

**Liquidity / cash audit:** every rebalance writes a `risk_overlay_trace` line in the run JSON with `{date, n_active, n_eligible, total_weight, cash_pct, capped_by_single_name_count, capped_by_adtv_count}`. Surface aggregated stats in the report's risk-overlay section.

### D5. Grid Definitions
Pin the best base parameters from H50b (verified against `fundamental_value_h50b_quality_value_search.json` top_candidates_multi_window[0]):
- `overlay`: `rel20_ge_0_and_ma60`
- `top_n`: 8 (must be >= `min_active_names`; assert at script start)
- `max_position_pct`: 0.08 (H50b best; this is the engine-level hard ceiling, distinct from D4 single-name cap which is the risk-overlay cap; H51b script MUST pin both and apply min(engine_cap, overlay_cap) at sizing time)
- `stop_loss_pct`: 0.08
- `take_profit_pct`: 0.25
- `quality_filter`: 0.40
- `rebalance_freq_days`: 63
- `sector_max_weight_pct`: 0.20

Stage A = 1 overlay × 1 sector_cap = 1 base configuration (everything else pinned).
Stage B sweep — 18 Risk Combos:
- Target Portfolio Volatility: {0.15, 0.20, 0.25}
- Single-name Max Weight: {0.10, 0.15, 0.20}
- ADTV Participation Cap: {0.05, 0.10}

Total runs = 1 × 18 = 18. No `--stage-b-limit` cap needed (already tiny). The point is to vary the risk overlay alone; everything else is frozen from H50b best to keep the comparison clean.

### D6. Stage C Ranking
Rank by `beat_HS300_windows` descending, then `deploy_excess` descending (tiebreaker).

### D7. Report 5-Way Comparison
`reports/h51b_risk_model_search_report.md` must include a 5-way comparison table mapping Verdict, Gate-pass, Max `beat_HS300_windows`, and Best Deploy Excess across H42, H48, H49b, H50b, and H51b.

### D8. Provenance Block (required in run JSON; validate_h51b enforces)

```json
{
  "data_sources": {
    "prices":           {"task": "h47",  "file": "...", "sha256": "..."},
    "sector_metadata":  {"task": "h49a", "file": "...", "sha256": "..."},
    "fundamentals":     {"task": "h50a", "file": "...", "sha256": "...", "rows": 12398},
    "adtv_liquidity":   {"task": "h51a", "file": "data/cn_pit/liquidity_h51a_daily_amount.csv", "sha256": "..."},
    "universe":         {"file": "...", "sha256": "..."}
  },
  "scorer_substitution": {
    "from": "fundamental_backtest.ValueScore",
    "to":   "h50b_quality_value_search.ValueScoreH50",
    "reused_from": "h50b",
    "patched_modules": ["backtest.experiments.fundamental_backtest", "scripts.h42_strategy_redesign_search"],
    "restored_after_run": true
  },
  "sizing_substitution": {
    "from": "fundamental_backtest.run_fundamental_backtest",
    "to":   "h51b_risk_model_search._run_fundamental_backtest_h51b",
    "patched_modules": ["backtest.experiments.fundamental_backtest", "...(h42 if applicable)..."],
    "restored_after_run": true,
    "sizing_block_diff": "...unified-diff of the replaced 7 lines vs the new sizing logic..."
  },
  "risk_model_design": {
    "target_portfolio_vol": <0.15 | 0.20 | 0.25>,
    "single_name_cap_pct":  <0.10 | 0.15 | 0.20>,
    "adtv_cap_pct":         <0.05 | 0.10>,
    "min_active_names":     5,
    "vol_window_days":      60,
    "vol_min_data_points":  40,
    "vol_return_basis":     "log",
    "vol_annualization":    "sqrt(252)",
    "adtv_window_days":     20,
    "adtv_window_inclusive_of_today": false,
    "cash_buffer_policy":   "no_leverage_on_underweight"
  },
  "exclusion_stats": {
    "rebalances_total": <int>,
    "vol_insufficient_data": <int>,
    "adtv_insufficient_data": <int>,
    "min_active_names_violated_count": <int>
  }
}
```

`validate_h51b` asserts:
- All 5 `data_sources` entries present, each with valid 64-char sha256.
- `data_sources.adtv_liquidity.task == "h51a"`.
- `scorer_substitution.restored_after_run == true` AND `sizing_substitution.restored_after_run == true`.
- `risk_model_design.min_active_names == 5` AND `vol_window_days == 60` AND `adtv_window_days == 20` AND `vol_return_basis == "log"`.
- `exclusion_stats` present with all 4 sub-fields (no silent skip).

## Acceptance Gate
- `CANDIDATE_FOR_FORWARD_TRIAL`: At least one candidate clears ALL H42 execution gates (9 conditions verbatim) AND `beat_HS300_windows >= 2/5`.
- `RESEARCH_ONLY`: Otherwise.

**Closure Checklist:**
- [ ] Provenance block includes H51a ADTV sha256 + sizing substitution + risk model design.
- [ ] Monkey-patches (ValueScore + Sizing) restored in `finally` block.
- [ ] H42 regression test passes after H51b module load.
- [ ] 10-file `git status` clean (no upstream files modified).
- [ ] 5-way comparison table included in report.
- [ ] Sync doc updated and next-slices flipped.

## Commands
**Smoke Command**:
```bash
python scripts/h51b_risk_model_search.py --stage-b-limit 3 --output-run /tmp/h51b_smoke.json --output-report /tmp/h51b_smoke.md
```
Smoke MUST: exercise both substitutions (scorer + sizing); both `restored_after_run=true` in the provenance; exercise at least one rebalance with `n_active >= 5` AND at least one with vol-cap or ADTV-cap triggered (so the constraints get tested under load).

**Full Command**:
```bash
python scripts/h51b_risk_model_search.py
```
18 runs; wall clock ~15-25 min.

**Verification**:
```bash
python scripts/validate_hxx_artifacts.py --artifact h51b
python scripts/validate_hxx_artifacts.py                              # full family must still be 12/12 PASS
pytest tests/test_h51b_risk_model_search.py \
       tests/test_validate_hxx_artifacts.py \
       tests/test_h42_strategy_redesign_search.py \
       tests/test_h50b_quality_value_search.py -q                     # H42 + H50b regressions prove process-local patching
python scripts/validate_ledger_consistency.py --strict
git status --short \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/sector_metadata_sw_l1.csv \
  data/cn_pit/fundamentals_h50a_pit_quality.jsonl \
  data/cn_pit/fundamentals.jsonl \
  data/cn_pit/liquidity_h51a_daily_amount.csv \
  backtest/runs/fundamental_value_h42_strategy_redesign_search.json \
  backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json \
  backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json \
  backtest/runs/fundamental_value_h50b_quality_value_search.json \
  scripts/h42_strategy_redesign_search.py \
  scripts/h50b_quality_value_search.py \
  backtest/experiments/fundamental_backtest.py
```
The last command MUST print nothing — all 13 protected files unchanged.
