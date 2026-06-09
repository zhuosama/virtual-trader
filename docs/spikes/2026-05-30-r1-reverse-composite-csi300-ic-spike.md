# Spike: R1 Reverse-Composite CSI300 IC Bench

Date: 2026-05-30
Status: DRAFT — ready for Hermes dispatch
Charter: Charter §5 hypothesis #4 (cross-sectional composite rank), close-only branch
Spike budget: **≤2 wall-hours** (Charter §3); expected actual ~15-30 min based on close-only spike velocity
Predecessor: `docs/spikes/2026-05-30-close-only-factors-csi300-ic-spike.md` (SIGNAL_NEGATIVE — 6 factors pass |IC|>0.03 but all IR<0.5)
Parent spec: `docs/superpowers/specs/2026-05-30-vibe-trading-borrow-plan.md` § 5.3

> **Why this spike exists.** Close-only spike found short-term reversal as the strongest direction (rev_1d IC=0.042/IR=0.242; rev_5d IC=0.038/IR=0.206; alpha101_009 IC=0.033/IR=0.212). All single-factor IR < 0.5. If the 3 factors have low IC correlation, equal-weight composition could lift IR above 0.5 (theoretical: IR_composite ≈ IR_avg × √(N/(1+(N-1)·ρ)) where ρ = mean pairwise IC correlation). This spike tests that theory.

---

## 0. Decision-grade Question (Y/N)

> Does an equal-weight composite of `rev_1d`, `rev_5d`, and `alpha101_009` achieve `|mean_ic| > 0.03 AND IR > 0.5` on the H47 frozen CSI300 universe (period 2025-01-01 → 2026-05-18)?

`YES` → propose H53 brief (3-factor equal-weight composite or weighted variant); first cross-sectional rank-style result that passes Charter §5 bar.
`NO` → diversification limit hit (IC correlations too high). Append postmortem; recommend either drilling into single best factor with longer-horizon variant, or pivot off cross-sectional rank entirely.

## 1. Hard Prereqs

- [ ] Close-only spike complete (`/tmp/spike_close_only/adapters.py`, `/tmp/spike_close_only/ic_results.csv` available)
- [ ] Charter §2 frozen artifact sha unchanged (`prices_h47` = `34f3e38f...`)

## 2. Scope

### 2.1 In scope

- **3 single factors** (already implemented in close-only spike — directly reuse):
  - `rev_1d`: −1 × 1-day return
  - `rev_5d`: −1 × 5-day return
  - `alpha101_009`: upstream HKUDS/Vibe-Trading `zoo/alpha101/alpha_009.py` (close-only subset, sha pinned via close-only spike)

- **1 composite factor**:
  - `composite_eq3`: equal-weighted z-score normalized sum of the 3 single factors per cross-section

- **Diagnostics**:
  - Pairwise IC time-series correlation (3×3 matrix of `corr(IC_i_t, IC_j_t)`) → diversification benefit estimator
  - Composite IC, IR, rolling 60d IR
  - Comparison to single-factor IR and theoretical diversification ceiling

### 2.2 Out of scope

- Optimized weights (IR-weighted, mean-variance-weighted) — if equal-weight passes, that's enough for Charter §5 promotion; weighted variants are H53 territory
- Adding 4th+ factor to composite — if 3-factor equal-weight fails, adding more factors of similar type is unlikely to help (diversification asymptotes)
- Cost / turnover modeling
- Out-of-period validation (= H53 territory)
- Backtest engine integration

## 3. Hard Prohibitions

(Same boilerplate as preceding spikes — verbatim from AGENTS.md.)

### 3.1 Spike-Specific Prohibitions

- Do NOT modify ANY file under `data/cn_pit/`
- Do NOT modify `agents/`, `strategies/`, `backtest/`, `scripts/`
- Do NOT install new pip packages
- Do NOT exceed 2 wall-hours
- Do NOT promote spike artifacts beyond `/tmp/spike_r1_composite/` + `docs/spikes/<this-spike>-report.md`
- **CRITICAL**: do NOT fabricate composite IC if any single-factor compute fails. If a precondition factor (rev_1d / rev_5d / alpha101_009) cannot be recomputed in this spike's adapter, STOP and surface as BLOCKED.

## 4. Task Breakdown

### Task 1 — Reload single factors

**Budget**: 15 min

- [ ] Copy `/tmp/spike_close_only/adapters.py` to `/tmp/spike_r1_composite/adapters.py`
- [ ] Verify rev_1d, rev_5d, alpha101_009 adapter functions still work on `data/cn_pit/prices_h47_tushare_qfq_candidate.csv`
- [ ] Reproduce single-factor IC values from close-only spike (should match to 6 sig figs); if not, STOP and surface — input data must be deterministic

### Task 2 — Compute pairwise IC correlation matrix

**Budget**: 15 min

- [ ] For each pair (i,j) in {rev_1d, rev_5d, alpha101_009}: compute time-series Pearson correlation of daily IC(i,t) and IC(j,t)
- [ ] Output 3×3 matrix to `/tmp/spike_r1_composite/ic_correlation.csv`
- [ ] Interpretation note in report:
  - ρ_mean → 1: full overlap, equal-weight = single-factor IR (no benefit)
  - ρ_mean → 0: independent, IR_composite ≈ IR_avg × √3 ≈ 1.73 × IR_avg
  - For our case: IR_avg ≈ 0.22 → max possible composite IR ≈ 0.38 if ρ≈0; with ρ>0, less

### Task 3 — Composite IC bench

**Budget**: 25 min

- [ ] Per cross-section (each date), z-score-normalize each of 3 factors, sum them, divide by √3 (this preserves unit variance assumption)
- [ ] Compute composite factor IC vs forward 1-day return (same as close-only spike methodology)
- [ ] Aggregate: mean_ic, std_ic, ir, rolling 60d IR mean+last
- [ ] Output `/tmp/spike_r1_composite/composite_ic.csv` (single row)
- [ ] Compute "theoretical diversification ceiling" IR = IR_avg × √(3/(1+2ρ_mean)) and compare to actual composite IR

### Task 4 — Decision + Report

**Budget**: 15 min

- [ ] Apply threshold: |composite_mean_ic| > 0.03 AND composite_ir > 0.5
- [ ] Outcome:
  - PASS → `SIGNAL_POSITIVE_PROPOSED` (first cross-sectional rank result to pass under Charter §5 thresholds)
  - composite_ir > max(single_factor_ir) but < 0.5 → `SIGNAL_IMPROVED_BUT_INSUFFICIENT`
  - composite_ir ≤ max(single_factor_ir) → `SIGNAL_NO_DIVERSIFICATION_BENEFIT` (high correlation evidence)
- [ ] Write `docs/spikes/2026-05-30-r1-reverse-composite-csi300-ic-spike-report.md`:
  - Decision
  - Single-factor reproduction table (IC + IR for rev_1d, rev_5d, alpha101_009)
  - Pairwise IC correlation matrix
  - Composite factor IC + IR
  - Theoretical vs actual diversification benefit
  - Recommendation for next step (per § 7 promotion rule)
  - Time spent

## 5. kill_when

```
kill_when = "(a) 2-hour wall budget exhausted, OR (b) prices_h47 sha changes, OR
(c) any single-factor IC fails to reproduce close-only spike values to 6 sig figs
(indicates non-deterministic input or adapter regression — STOP, do NOT compute composite on questionable inputs)."
```

## 6. Acceptance Gates

| # | Check | Expected |
|---|---|---|
| RG-1 | Single-factor IC reproducibility | match close-only spike to 6 sig figs |
| RG-2 | prices_h47 sha unchanged | `34f3e38f...` |
| RG-3 | Pairwise IC correlation matrix has 9 entries (3×3, symmetric) | Y/N |
| RG-4 | Composite IC + IR computed and compared to theoretical ceiling | Y/N |
| RG-5 | Verdict in {SIGNAL_POSITIVE_PROPOSED, SIGNAL_IMPROVED_BUT_INSUFFICIENT, SIGNAL_NO_DIVERSIFICATION_BENEFIT, BLOCKED} | Y/N |

## 7. Promotion Rule

| Verdict | Next action |
|---|---|
| SIGNAL_POSITIVE_PROPOSED | Recommend H53 brief: 3-factor equal-weight reversal composite as candidate signal; would consume 1 Charter slice |
| SIGNAL_IMPROVED_BUT_INSUFFICIENT | Diversification works but not enough. Next spike: try longer horizon (rev_20d, alpha101_022 if close-only) to find lower-correlation reversal signal |
| SIGNAL_NO_DIVERSIFICATION_BENEFIT | High IC correlation means these 3 factors are essentially the same signal. Postmortem; recommend either pivot off close-only or escalate to OHLV engine PR |

## 8. Files

**Ephemeral (/tmp/spike_r1_composite/)**: adapters.py, ic_correlation.csv, composite_ic.csv, errors.md if any

**Durable**: `docs/spikes/2026-05-30-r1-reverse-composite-csi300-ic-spike-report.md`

**Protected NOT touched**: ALL of `data/cn_pit/`, `strategies/`, `agents/`, `backtest/market_data.py`, `backtest/factors/`

## 9. Time Budget

| Task | Estimate |
|---|---|
| 1. Reload + verify | 15 min |
| 2. Pairwise IC correlation | 15 min |
| 3. Composite IC bench | 25 min |
| 4. Report | 15 min |
| **Total** | **70 min** (well under 2h budget) |
