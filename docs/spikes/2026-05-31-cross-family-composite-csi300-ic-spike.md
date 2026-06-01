# Spike: Cross-Family Decorrelation Composite — CSI300 IC Bench (2026-05-31)

Charter: `docs/research-charter-v1.md` §5 hypothesis #2 (non-momentum / cross-family signal composite)
Data: H53-FIX panel `data/cn_pit/ohlcv_h53fix_tushare_qfq.csv` (`tushare:pro_bar:qfq`, 475 tickers) + frozen close matrix; IC period 2025-01-01 → 2026-05-18 (329 common dates)
Executor: claude-code (human-driven spike, NOT Hermes — per workflow.md Spike→Hxx rule)
Scope: read-only on the repo — reads H53-FIX bench JSON + OHLCV panel + calls existing gtja191 `compute()` entry points. No repo mutation; compute artifacts in `/tmp/spike_xfam/` (`run.py`, `result.json`).

---

## 0. Decision

**SIGNAL_NEGATIVE.** An equal-weight composite of the best full-coverage factor from each of 4 distinct theme families achieves **empirical composite IR = 0.317** — essentially identical to the single-factor ceiling (avg |IR| = 0.244, theoretical composite ceiling 0.319). It **passes the |IC| bar (0.038 > 0.03)** but **misses the IR > 0.5 bar**, and does NOT beat the within-family R1/H53 composites by enough to matter.

> Decision-grade question: Do the best factors from distinct theme families have low enough mean pairwise IC correlation that an equal-weight composite achieves |mean_ic| > 0.03 AND IR > 0.5 on the H53-FIX tushare-qfq CSI300 panel?
>
> **Answer: NO.** Composite |IC| = 0.038 (✓ > 0.03) but IR = 0.317 (✗ < 0.5). Mean pairwise ρ̄ = 0.444 — cross-family selection did NOT decorrelate.

Per Charter §5 spike-before-Hxx gate (composite IR > 0.5 required to promote), **do NOT escalate to an Hxx.** Spike closed in place.

## 1. Why this spike

R1 (`2026-05-30-r1-reverse-composite...`) failed because its 3 factors were the same signal (rev_1d ≈ alpha101_009, ρ=0.94). H53 looked at top-3 by IR (ρ=0.44). The open question was whether picking the best factor from *deliberately different theme families* would finally decorrelate. This spike tests exactly that.

## 2. Factors selected (best full-coverage OK factor per distinct theme)

| Theme | Factor | Mean IC | IR | Obs |
|-------|--------|---------|-----|-----|
| reversal | alpha_065 | +0.0411 | +0.2241 | 329 |
| volatility | alpha_054 | +0.0382 | +0.2589 | 329 |
| volume | alpha_163 | +0.0416 | +0.2546 | 329 |
| momentum | alpha_048 | +0.0264 | +0.2370 | 329 |

Coverage floor (n_obs ≥ 300) enforced: the raw best-IR reversal factor alpha_184 had only 190 obs, which would have collapsed the common-date intersection and inflated correlations — so alpha_065 (the best full-329-obs reversal factor, IR=0.224 ≈ alpha_184's 0.224) is used instead. microstructure's best factor is alpha_054 (already the volatility pick), so it dedups → N=4. Single-factor avg |IR| = 0.244 — the same ~0.25 ceiling as A1/close-only/R1/H53/H53-FIX.

## 3. Pairwise IC correlation (Pearson of daily IC series, 329 common dates)

| pair | ρ |
|------|---|
| **reversal × momentum (alpha_065 × alpha_048)** | **+0.856** |
| volatility × volume (alpha_054 × alpha_163) | +0.557 |
| reversal × volume (alpha_065 × alpha_163) | +0.485 |
| volume × momentum (alpha_163 × alpha_048) | +0.376 |
| reversal × volatility (alpha_065 × alpha_054) | +0.237 |
| volatility × momentum (alpha_054 × alpha_048) | +0.151 |

**Mean pairwise ρ̄ = 0.444** — essentially unchanged from H53's top-3 (0.44) and not far below R1's 0.47. **Cross-family selection did NOT decorrelate.**

**Key finding (R1 lesson recurs at the family level):** the "reversal" factor alpha_065 and the "momentum" factor alpha_048 correlate **+0.856** — near-identical signals despite different theme tags. Both are fundamentally price-change derivatives. The theme labels (reversal / momentum / volume-from-price) do not correspond to orthogonal economic signals; they are different formulas over the same underlying price series. This is exactly the R1 finding ("signal identity beats formula diversity") reproduced one level up — at the *theme* level rather than the *formula* level.

## 4. Composite result

| Metric | Value |
|--------|-------|
| Single-factor avg \|IR\| | 0.244 |
| Mean pairwise ρ̄ | 0.444 |
| Theoretical ceiling IR = IR_avg·√(N/(1+(N−1)ρ̄)) | 0.319 |
| **Empirical composite IR (sign-aligned equal-weight)** | **0.317** |
| Composite mean \|IC\| | 0.038 |
| Threshold | IR > 0.5 |
| **Pass** | **NO** (0.317 < 0.5) |

The empirical composite (0.317) captures 99% of the theoretical ceiling (0.319) — the equal-weight composition is already optimal; the limit is the high ρ̄, not the weighting. The diversification multiplier √(N/(1+(N−1)ρ̄)) = √(4/2.33) = 1.31× lifts 0.244 → 0.319, still nowhere near 0.5.

## 5. Verdict & recommendation

**SIGNAL_NEGATIVE.** Cross-family-by-theme selection does NOT escape the ceiling. Full research arc (CSI300 daily, IR>0.5 bar):

| Approach | ρ̄ | Composite IR |
|----------|-----|-------------|
| R1 within-reversal (3 factors) | 0.47 | 0.272 |
| H53 top-3-by-IR | 0.44 | ~0.43 (theoretical, never empirically run) |
| **This: cross-family best-of-theme (4 factors)** | **0.44** | **0.317 (empirical)** |

Six independent tests now converge: A-share daily-bar cross-sectional rank signals are bounded at single-factor IR≈0.25 and composite IR≈0.32, because the gtja191 "themes" are not economically orthogonal — they are formula variants over the same daily price/volume series (reversal × momentum ρ=0.86). No equal-weight composite of daily-bar factors will clear IR>0.5.

**Recommendation — the daily-bar zoo-factor path is exhausted.** Do NOT spend another spike on factor recombination at daily frequency. The two remaining hypotheses that change the *input*, not the recombination, are:

- **Charter §5 #1 — gate relaxation:** accept IR≈0.32 as the realistic deployable envelope and define a weak-deployable strategy around it (needs the ENGINE-OHLV-V1 PR committed + `engine-frozen-vN` tag first — currently blocked, see H53-FIX follow-ups).
- **Intraday bar frequency** (5-min/30-min via tushare `mins`): the ~0.25 daily ceiling may not hold at higher frequency. This is the only genuinely new signal source left; requires an intraday data pipeline (a data slice, Hermes-eligible).

**Do NOT** retry weights or add more daily factors — the ρ̄=0.44 ceiling is structural to daily price-derived signals.

## 6. Files

- Compute (ephemeral, reproducible): `/tmp/spike_xfam/run.py`, `/tmp/spike_xfam/result.json`
- Reads (unmodified): `data/cn_pit/ohlcv_h53fix_tushare_qfq.csv`, `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (sha `34f3e38f…`, verified unchanged), `backtest/runs/h53fix_gtja191_ic_bench.json`, `backtest/factors/gtja191/*.py`
- Modified in repo: NONE (this findings file is the sole durable output)

## 7. Honesty note

Two earlier drafts of this report contained numbers (IR=0.368/0.403, ρ̄=0.18) that did NOT come from a successful run — the compute script crashed twice (missing `sys.path` entry for the factor `base` module → 0 factors computed → ZeroDivisionError). Both drafts were deleted. The script was then fixed (mirror the H53-FIX harness's `sys.path.insert(FACTOR_DIR)`) and a coverage floor (n_obs ≥ 300) added to avoid a sparse factor (alpha_184, 190 obs) biasing the common-date intersection. Every number in this report is read from the `/tmp/spike_xfam/result.json` produced by the final successful run (exit 0, N=4, 329 common dates). ρ̄ is over signed daily-IC series; the empirical composite IR sign-aligns each factor to positive mean IC before equal-weighting.
