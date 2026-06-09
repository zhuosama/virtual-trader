# Spike Report: Close-Only Factor Families CSI300 IC Bench

Date: 2026-05-30
Spike plan: `docs/spikes/2026-05-30-close-only-factors-csi300-ic-spike.md`
Predecessor: `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike.md` (SIGNAL_NEGATIVE)
Charter: `docs/research-charter-v1.md` §5 hypothesis #4 (cross-sectional composite rank, close-only restriction)
Executor: Hermes (deepseek-v4-pro)

---

## 0. Decision

**SIGNAL_NEGATIVE** — 0 of 12 close-only factors pass both PROPOSED_THRESHOLD criteria (`|mean_ic| > 0.03 AND |ir| > 0.5`).

However, this is a **much stronger outcome than the A1 spike**. Four factors clear the |IC| bar alone (>0.03), and the best factor (rev_1d: IC=0.042, IR=0.242) has 8.4× the predictive power of A1's sole computable factor (gtja191_010: IC=-0.005, IR=-0.028). The bottleneck is IR — no factor achieves the IR>0.5 bar, with the best IR at 0.242.

> Decision-grade question: Do at least 3 close-only single factors achieve `|mean_ic| > 0.03 AND IR > 0.5` on the H47 frozen CSI300 universe (2025-01-01 → 2026-05-18)?
>
> **Answer: NO.** See § 4 for per-factor results.

---

## 1. Selected Factors

| # | Factor ID | Family | Formula | Source |
|---|-----------|--------|---------|--------|
| 1 | mom_5d | A — Momentum | close.pct_change(5) | Jegadeesh-Titman (1993) |
| 2 | mom_20d | A — Momentum | close.pct_change(20) | Jegadeesh-Titman (1993) |
| 3 | mom_60d | A — Momentum | close.pct_change(60) | Jegadeesh-Titman (1993) |
| 4 | mom_252d | A — Momentum | close.pct_change(252) − close.pct_change(21) | Carhart (1997) MOM |
| 5 | rev_1d | B — Reversal | −close.pct_change(1) | Jegadeesh (1990) |
| 6 | rev_5d | B — Reversal | −close.pct_change(5) | Jegadeesh (1990) |
| 7 | vol_20d | C — Volatility | close.pct_change().rolling(20).std(ddof=1) | Realized vol |
| 8 | vol_20d_inv | C — Volatility | −vol_20d (low-vol proxy) | Baker-Haugen (2012) |
| 9 | vol_60d | C — Volatility | close.pct_change().rolling(60).std(ddof=1) | Realized vol |
| 10 | alpha101_001 | D — alpha101 | rank(ts_argmax(signed_power((ret<0)?std(ret,20):close,2),5))−0.5 | Kakushadze (2015) #1 |
| 11 | alpha101_009 | D — alpha101 | piecewise delta(close,1) momentum gate | Kakushadze (2015) #9 |
| 12 | alpha101_046 | D — alpha101 | piecewise acceleration-based ternary | Kakushadze (2015) #46 |

**Alpha101 selection rationale:** 11 of 103 alpha101 factors are close-only. Selected 3 for theme diversity: #1 (reversal+volatility), #9 (momentum piecewise, warmup=6), #46 (momentum piecewise, warmup=21).

---

## 2. Adapter Notes

### Base Operators (from A1 + extensions)

| Operator | Source | Description |
|----------|--------|-------------|
| `rank()` | A1 adapters | Cross-sectional percentile rank (ties=average, pct=True) |
| `ts_std(n)` | A1 adapters | Rolling sample std (ddof=1) |
| `ts_max(n)` | A1 adapters | Rolling max |
| `ts_min(n)` | New | Rolling min — needed for alpha101_009 |
| `ts_mean(n)` | New | Rolling mean |
| `safe_div(a,b)` | A1 adapters | a / (b + eps·sign(b)) |
| `signed_power(x,a)` | New | sign(x)·|x|^a — needed for alpha101_001 |
| `ts_argmax(x,w)` | New | Rolling argmax normalised to [-1,1] — needed for alpha101_001 |
| `delta(x,d)` | New | x − x.shift(d) — needed for alpha101_009, alpha101_046 |

### Implementation Stats

- File: `/tmp/spike_close_only/adapters.py` (sha256: `914a9cf10f30...`)
- 12 adapter functions, ~200 LOC
- No external dependencies beyond numpy + pandas
- All adapters return `pd.DataFrame` of factor values (same shape as input or subset)
- 1 factor (mom_252d) flagged COMPUTE_THIN (22.8% valid cells — 252-day lookback requires full price history beyond analysis window)

---

## 3. IC Results

| Factor ID | Mean IC | Std IC | IR | N Obs | Valid Pct | Mean Tickers | Status |
|-----------|---------|--------|-----|-------|-----------|-------------|--------|
| **rev_1d** | **0.041969** | 0.173253 | **0.2422** | 327 | 98.4% | 475.6 | OK |
| rev_5d | 0.037935 | 0.184261 | 0.2059 | 323 | 97.2% | 475.6 | OK |
| alpha101_009 | 0.033021 | 0.155467 | 0.2124 | 327 | 97.7% | 472.5 | OK |
| vol_20d_inv | 0.030775 | 0.254804 | 0.1208 | 308 | 92.7% | 475.6 | OK |
| alpha101_046 | 0.024499 | 0.145792 | 0.1680 | 327 | 97.7% | 472.5 | OK |
| mom_252d | 0.002225 | 0.187643 | 0.0119 | 76 | 22.8% | 475.0 | COMPUTE_THIN |
| mom_60d | -0.016416 | 0.217584 | -0.0754 | 268 | 80.6% | 475.5 | OK |
| vol_60d | -0.018322 | 0.277402 | -0.0660 | 268 | 80.6% | 475.5 | OK |
| alpha101_001 | -0.018636 | 0.128100 | -0.1455 | 312 | 91.3% | 462.2 | OK |
| mom_20d | -0.025876 | 0.200546 | -0.1290 | 308 | 92.7% | 475.6 | OK |
| vol_20d | -0.030775 | 0.254804 | -0.1208 | 308 | 92.7% | 475.6 | OK |
| mom_5d | -0.037935 | 0.184261 | -0.2059 | 323 | 97.2% | 475.6 | OK |

**Top 5 by |IC|:** rev_1d (0.042), mom_5d (−0.038), rev_5d (0.038), alpha101_009 (0.033), vol_20d/vol_20d_inv (±0.031)

**Top 5 by |IR|:** rev_1d (0.242), alpha101_009 (0.212), rev_5d (0.206), mom_5d (−0.206), alpha101_046 (0.168)

### Threshold Check

| Criterion | Passing Factors | Best |
|-----------|----------------|------|
| \|mean_ic\| > 0.03 | 4 (rev_1d, rev_5d, alpha101_009, mom_5d) + 1 borderline (vol_20d_inv) | rev_1d: 0.042 |
| \|ir\| > 0.5 | 0 | rev_1d: 0.242 |
| **Both** | **0** | — |

---

## 4. Per-Family Analysis

### Family A — Momentum (4 factors)

**None pass.** Momentum on close shows weak reversal behavior at short horizons (mom_5d IC=−0.038) that decays to near-zero at long horizons (mom_252d IC=0.002). This is consistent with the well-documented short-term reversal effect in A-shares — the "momentum" factor at 5d is actually reversal.

mom_252d is COMPUTE_THIN: the 252-day lookback consumes most of the 329-day analysis window, leaving only 76 valid cross-sections. A longer price history or shorter lookback (e.g., 120d) would be needed for proper evaluation.

### Family B — Reversal (2 factors) ★ BEST FAMILY

**Both pass |IC| bar, zero pass |IR| bar.** This is the standout family. rev_1d is the single best factor in the entire panel (IC=0.042, IR=0.242). rev_5d is the runner-up (IC=0.038, IR=0.206). The signal is directionally correct (positive IC = buying recent losers beats recent winners) and surprisingly stable given the simple formula.

The IR bottleneck (0.242 vs 0.5 required) means the signal is noisy day-to-day, but the direction is consistent. A composite of multiple reversal-type factors might improve IR through diversification.

### Family C — Volatility (3 factors)

**None pass.** vol_20d shows negative IC (−0.031), meaning high-volatility stocks tend to underperform — consistent with the low-volatility anomaly. vol_20d_inv is the exact mirror. However, IR is very weak (0.121), indicating high day-to-day variability in the low-vol premium.

### Family D — alpha101 (3 factors)

**One passes |IC| bar, zero pass |IR| bar.** alpha101_009 (IC=0.033, IR=0.212) is effectively a piecewise momentum-gated reversal factor — its core is `delta(close,1)`, same base as rev_1d. alpha101_001 (reversal+vol composite) underperforms its simpler constituent parts. alpha101_046 (acceleration-based) shows borderline signal.

---

## 5. Comparison to A1 Spike

| Metric | A1 (gtja191_010 only) | This Spike (rev_1d) | Improvement |
|--------|----------------------|---------------------|-------------|
| Best |IC| | 0.005 | 0.042 | **8.4×** |
| Best |IR| | 0.028 | 0.242 | **8.6×** |
| Factors with |IC|>0.03 | 0 | 4 | ∞ |
| Factors passing both | 0 | 0 | same |
| Structure gap? | 9/10 uncomputable | 0/12 uncomputable | Resolved |

**Key takeaway:** Switching from gtja191 (OHLCV-dependent) to close-only families completely resolved the structural gap (12/12 factors computable vs 1/10) and produced meaningfully stronger signals. But the signal is still too weak/noisy to clear the IR bar under the current PROPOSED_THRESHOLD.

---

## 6. Provenance Block

### Protected File Integrity

| File | SHA256 | Status |
|------|--------|--------|
| `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (before) | `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc` | ✓ |
| `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (after) | `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc` | ✓ UNCHANGED |

### Upstream alpha101 Provenance

| File | Upstream SHA | Commit |
|------|-------------|--------|
| `zoo/alpha101/alpha_001.py` | `e3cbd08ed16a...` | `bfcf848826750d5f74d0daa636eaffe02b894fad` |
| `zoo/alpha101/alpha_009.py` | `4a29aeae339d...` | `bfcf848826750d5f74d0daa636eaffe02b894fad` |
| `zoo/alpha101/alpha_046.py` | `d090a104e25d...` | `bfcf848826750d5f74d0daa636eaffe02b894fad` |

Source: HKUDS/Vibe-Trading @ `bfcf848826750d5f74d0daa636eaffe02b894fad` (MIT License)

### Local Artifacts

| File | SHA256 |
|------|--------|
| `/tmp/spike_close_only/adapters.py` | `914a9cf10f30e85b61ab02c096b60b8fd61aec0193f09368e6b16b4defd22133` |
| `/tmp/spike_close_only/factors_selected.md` | `5c3a90bb23cc99498c0d94efe955d18e5adfae91cbad3f21c14f1f1819f6c8d3` |

---

## 7. Time Spent vs Budget

| Task | Budget | Actual | Status |
|------|--------|--------|--------|
| 1. Factor specification | 20 min | ~3 min | Complete |
| 2. Adapter implementation | 25 min | ~5 min | Complete |
| 3. IC computation | 50 min | <1 min | Complete |
| 4. Report | 25 min | ~5 min | Complete |
| **Total** | **2 h** | **~14 min** | Under budget |

~1h 46min returned to Charter pool.

---

## 8. Recommendation for Next Step

### Recommended: Cross-Sectional Reversal Composite Spike

The data clearly shows which family works: **reversal**. With IR ~0.2 for single factors, the path to IR>0.5 requires diversification across multiple reversal-type factors. A composite of rev_1d + rev_5d + alpha101_009 (three factors with IC>0.03, all reversal/momentum-gated) could plausibly reduce IC volatility enough to push IR above 0.5.

**Proposed next spike (R1):** Equal-weight composite of the 3 best reversal-type factors (rev_1d, rev_5d, alpha101_009). If composite IR > 0.5 → promote to H53 brief.

**Alternative (if Charter allows):** Drill into the 60-day rolling IR time series of rev_1d to identify regimes where signal is strong vs weak. If China A-share reversal is regime-dependent (stronger in certain market conditions), this could inform a conditional strategy.

**Do NOT:**
- Continue alpha101 exploration — the simple hand-coded factors outperform the complex zoo formulas on this universe
- Escalate to H53 under current thresholds — 0 factors pass, gating correctly prevents Hxx waste
- Add OHLV data layer yet — the best signal (reversal) uses only close prices; OHLV would enable more factors but hasn't been shown to add signal where close-only already works

### Recommendation Ranking

1. **R1 Spike: reversal composite** — highest signal-to-effort ratio, builds directly on this spike's finding
2. Kill the cross-sectional rank hypothesis entirely — if IR=0.242 is the ceiling after exhausting close-only families, the hypothesis may be fundamentally limited
3. Propose OHLV supplemental data pipeline — enables full factor zoo but requires engine changes (Charter §4 Kill Crit 2)

User decision required.

---

## 9. Files Created / Modified

**Created (ephemeral, /tmp/spike_close_only/):**
- `factors_selected.md`
- `adapters.py`
- `ic_results.csv`
- `errors.md` (not created — no errors)

**Created (upstream fetch, /tmp/spike_close_only_vt_fetch/):**
- Full HKUDS/Vibe-Trading clone @ `bfcf848`

**Created (durable, in repo):**
- `docs/spikes/2026-05-30-close-only-factors-csi300-ic-spike-report.md` (this file)

**Modified:** NONE.

**Protected files integrity:** CONFIRMED — `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` SHA unchanged.

---

## 10. Acceptance Gates

| # | Check | Expected | Actual | Status |
|---|-------|----------|--------|--------|
| SG-1 | All selected factors have rationale + sha256 logged | Y/N | Y | PASS |
| SG-2 | prices_h47 sha256 unchanged | `34f3e...` | `34f3e...` | PASS |
| SG-3 | ic_results.csv has ≥9 rows, status field populated | Y/N | 12 rows, all populated | PASS |
| SG-4 | Spike report includes loader provenance + factor sha + comparison to A1 | Y/N | Y (§ 5, § 6) | PASS |
| SG-5 | Wall-clock ≤ 2h documented | Y/N | ~14 min (§ 7) | PASS |

**All 5 acceptance gates PASS.**

---

## Appendix: Full IC Time Series Statistics

```
Factor            Mean_IC   Std_IC    IR      Min_IC   Max_IC   Skew     Kurtosis
rev_1d            0.0420    0.1733    0.242   -0.484   0.535    -0.01    3.14
rev_5d            0.0379    0.1843    0.206   -0.528   0.526    -0.08    3.27
alpha101_009      0.0330    0.1555    0.212   -0.464   0.539    0.05     3.99
vol_20d_inv       0.0308    0.2548    0.121   -0.730   0.676    -0.17    3.01
alpha101_046      0.0245    0.1458    0.168   -0.439   0.527    0.09     3.41
mom_252d          0.0022    0.1876    0.012   -0.500   0.670    0.67     4.77
mom_60d          -0.0164    0.2176   -0.075   -0.657   0.590    0.01     3.26
vol_60d          -0.0183    0.2774   -0.066   -0.765   0.769    0.03     3.53
alpha101_001     -0.0186    0.1281   -0.146   -0.396   0.317    0.02     3.05
mom_20d          -0.0259    0.2005   -0.129   -0.619   0.509    0.06     3.44
vol_20d          -0.0308    0.2548   -0.121   -0.676   0.730   -0.17     3.01
mom_5d           -0.0379    0.1843   -0.206   -0.526   0.528   -0.08     3.27
```
