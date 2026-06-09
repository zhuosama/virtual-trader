# Spike: Close-Only Factor Families CSI300 IC Bench

Date: 2026-05-30
Status: DRAFT — ready for Hermes dispatch
Charter: `docs/research-charter-v1.md` (v1.0-DRAFT) — Charter §5 hypothesis #4 path (cross-sectional composite rank), restricted to close-only factors
Spike budget: **≤2 wall-hours** (Charter §3)
Predecessor: `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike.md` (SIGNAL_NEGATIVE — schema gap, see `docs/strategy-optimization-sync.md` § A1 Spike postmortem)
Parent spec: `docs/superpowers/specs/2026-05-30-vibe-trading-borrow-plan.md` § 5.3 → Recommendation (2)
Worker scope: ALLOWED — bulk I/O, sha256 audit, read-only on protected data, write to `/tmp/spike_close_only/` and `docs/spikes/`. FORBIDDEN — modify protected artifacts, write strategy scripts.

> **Why this spike exists.** A1 spike (gtja191) discovered the H47 frozen price matrix is close-only — 90% of gtja191 factors structurally uncomputable. User decision 2026-05-30: pivot to close-only factor families per A1 spike report § 10 Recommendation (2). This spike validates the cross-sectional composite rank hypothesis under the close-only constraint, reusing the adapter framework + IC pipeline from A1.

---

## 0. Decision-grade Question (Y/N)

> Do at least 3 close-only single factors achieve `|mean_ic| > 0.03 AND IR > 0.5` on the H47 frozen CSI300 universe (period 2025-01-01 → 2026-05-18), using only the adjusted close prices already present in `data/cn_pit/prices_h47_tushare_qfq_candidate.csv`?

Same thresholds and tagging as A1 spike (PROPOSED_THRESHOLD, pending Charter alignment per parent spec Q3).

A `YES` → write next spike for multi-factor composition or escalate to H53 brief if ≥1 factor truly stands out.
A `NO` → append postmortem like A1; either propose adding OHLV supplemental layer (engine PR, Charter §4 Kill Crit 2), or kill the entire cross-sectional rank hypothesis under current Charter.

## 1. Hard Prereqs

- [ ] A2 engine PR (`docs/superpowers/plans/2026-05-30-loader-registry-engine-pr.md`) executed locally — DONE 2026-05-30 (AG-1/AG-2/AG-5 PASS)
- [ ] A1 spike artifacts available (`/tmp/spike_alpha_zoo/adapters.py` base operators reusable) — DONE 2026-05-30. **Note:** if `/tmp/spike_alpha_zoo/` has been cleaned, reimplement base operators inline (rank, ts_std, ts_mean, ts_corr, safe_div, ts_max, ts_min) — they're <60 LOC total.

## 2. Scope

### 2.1 In scope — Factor families (target ~12 factors)

**Family A — Momentum on close (cross-sectional return rank)**
1. `mom_5d`: 5-day return rank
2. `mom_20d`: 20-day return rank
3. `mom_60d`: 60-day return rank
4. `mom_252d`: 252-day return rank (skip-last-month variant; momentum = ret(252) − ret(21))

**Family B — Reversal on close**
5. `rev_1d`: −1 × 1-day return (short-term reversal)
6. `rev_5d`: −1 × 5-day return

**Family C — Volatility on close**
7. `vol_20d`: 20-day rolling std of daily returns (raw)
8. `vol_20d_inv`: inverse of vol_20d (low-vol anomaly proxy, since low-vol historically outperforms in CN A-shares per H51b notes)
9. `vol_60d`: 60-day rolling std of daily returns

**Family D — alpha101 close-only subset**
10. `alpha101_close_a`: first alpha101 factor depending only on close (formula varies; if alpha101 zoo follows the same upstream pattern as gtja191, expect to find 5-10 such formulas)
11. `alpha101_close_b`: second close-only alpha101 factor
12. `alpha101_close_c`: third close-only alpha101 factor

If alpha101 close-only subset has fewer than 3 items, drop the slot — minimum 9 factors is enough to validate hypothesis.

### 2.2 Out of scope (DO NOT bleed into spike)

- Multi-factor composition (=next spike if this one passes)
- OHLV-dependent factors (= A1 spike already killed under current schema)
- CSI500 universe (= Charter §2 frozen on CSI300 + H47)
- Cost / turnover modeling
- Backtest engine integration
- Any modification to `agents/`, `strategies/`, `backtest/factors/`

## 3. Hard Prohibitions — Always Applicable

(Same boilerplate as A1 spike § 3. The executor reads them with the brief.)

- **No data fabrication**: do NOT add, modify, "complete", or "round up" rows in any protected artifact.
- **No source provenance forgery**: any borrowed alpha101 factor must retain upstream copyright + commit SHA reference. Hand-implemented momentum/reversal/volatility factors are local; cite formula source if from literature.
- **Symmetric restore**: any temp script must use try/finally.
- **Original ingestion verdicts immutable**: do NOT modify any prior Hxx run JSON.
- **Exit-code is not acceptance**: every IC number is verifiable (sha256 lock on prices.csv).
- **No silent workarounds**: if a factor refs a column not in close-only schema, STOP and reclassify as out-of-scope, do not invent substitute.
- **sha256 audit hooks**: prices_h47_tushare_qfq_candidate.csv sha = `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc` must be unchanged at spike close.

### 3.1 Spike-Specific Prohibitions

- Do NOT modify ANY file under `data/cn_pit/` (read-only access only)
- Do NOT modify `agents/`, `strategies/`, `backtest/factors/` (does not exist; do NOT create it)
- Do NOT install new pip packages (numpy + pandas are sufficient; scipy was missing in A1, use numpy.corrcoef equivalent if scipy missing)
- Do NOT exceed 2 wall-hours total
- Do NOT promote spike artifacts to durable layer beyond `docs/spikes/<this-spike>-report.md`

## 4. Task Breakdown

### Task 1 — Factor Specification

**Budget**: 20 min

- [ ] Confirm local PIT schema from `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` header (expect: 1 date col + ~482 ticker cols, all qfq close prices)
- [ ] For Family A/B/C (9 factors): each is hand-coded from formula in § 2.1 (no upstream fetch needed)
- [ ] For Family D (3 alpha101 factors): fetch upstream alpha101 source from HKUDS/Vibe-Trading (commit pinned at spike time, log SHA in report). Locate alpha101 directory — likely `zoo/alpha101/` (parallel to `zoo/gtja191/` per A1 spike finding). Grep for factors using ONLY close column. If fewer than 3 close-only factors exist in alpha101, drop slots and proceed with 9 factors (acceptable per spike plan § 2.1).
- [ ] Document factor list, formulas, source, sha256 (for upstream files) in `/tmp/spike_close_only/factors_selected.md`

### Task 2 — Adapter / Implementation

**Budget**: 25 min

- [ ] Create `/tmp/spike_close_only/adapters.py` with:
  - Base operators (rank, ts_std, ts_mean, safe_div, ts_max, ts_min) — copy from A1 `/tmp/spike_alpha_zoo/adapters.py` if available, otherwise reimplement (~60 LOC)
  - One adapter function per factor in § 2.1
  - Each adapter takes `prices: pd.DataFrame` (wide, index=date, columns=ticker, values=close) and returns `pd.DataFrame` of same shape (factor values)
- [ ] All factors must produce values for at least 80% of (date, ticker) cells in the analysis window; if not, factor is `STATUS=COMPUTE_THIN`

### Task 3 — IC Computation

**Budget**: 50 min

- [ ] Load CSI300 H47 frozen prices, slice to `2025-01-01 → 2026-05-18` (matches H28 baseline period, matches A1 spike)
- [ ] For each factor:
  - Compute factor values (Task 2 adapters)
  - Compute forward 1-day returns from prices (`prices.pct_change().shift(-1)`)
  - Cross-sectional Pearson rank IC per date (`factor_row.rank().corrwith(ret_row.rank())`)
  - Aggregate: mean_ic, std_ic, ir = mean_ic / std_ic, n_obs (= number of valid dates), rolling 60d IR
- [ ] Output `/tmp/spike_close_only/ic_results.csv` (one row per factor; same schema as A1 spike for diff comparison)

### Task 4 — Decision + Report

**Budget**: 25 min

- [ ] Apply spike plan § 0 thresholds: |mean_ic| > 0.03 AND IR > 0.5
- [ ] Count passing factors:
  - ≥3 → `SIGNAL_POSITIVE_PROPOSED`
  - 1-2 → `SIGNAL_WEAK` (a stronger outcome than A1, but not enough to escalate; user decides)
  - 0 → `SIGNAL_NEGATIVE`
- [ ] Write `docs/spikes/2026-05-30-close-only-factors-csi300-ic-spike-report.md` mirroring A1 report structure:
  - Decision
  - Selected factors (table)
  - Adapter notes (reuse from A1 + new family adapters)
  - IC results table
  - Provenance (prices sha, upstream alpha101 SHA, factor sha)
  - Per-family analysis (which family produced any signal? if any)
  - Comparison to A1 (A1 had 1 computable factor with IC -0.005; do close-only families beat that?)
  - Time spent vs budget
  - Recommendation for next step (escalate / next spike / kill hypothesis)

## 5. kill_when (single-sentence exit)

```
kill_when = "(a) 2-hour wall budget exhausted with fewer than 6 factors completing IC compute, OR (b) prices_h47 sha256 changes (= protected artifact violation, immediate STOP and surface as BLOCKER), OR (c) alpha101 close-only subset has 0 matches AND user-defined slots cannot be filled by additional hand-coded factors without changing scope."
```

## 6. Acceptance Gates (for the spike itself)

| # | Check | Expected |
|---|---|---|
| SG-1 | All selected factors have rationale + sha256 (where applicable) logged | Y/N |
| SG-2 | `prices_h47_tushare_qfq_candidate.csv` sha256 unchanged | `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc` |
| SG-3 | `ic_results.csv` has ≥9 rows, status field populated | Y/N |
| SG-4 | Spike report includes loader provenance + factor sha256 + comparison to A1 | Y/N |
| SG-5 | Wall-clock ≤ 2h documented | Y/N |

## 7. Promotion Rule (spike → next action)

| Spike result | Next action |
|---|---|
| SIGNAL_POSITIVE_PROPOSED (≥3 pass) | Propose H53 brief (single-factor or simple equal-weight composite) per Charter §5 → Hxx promotion; OR next spike for multi-factor composition |
| SIGNAL_WEAK (1-2 pass) | User decides: drill into the 1-2 candidates with a deeper spike, OR pivot to different family |
| SIGNAL_NEGATIVE (0 pass) | Append postmortem to `docs/strategy-optimization-sync.md`; recommend (a) propose OHLV engine PR for full zoo access, or (b) kill cross-sectional composite rank hypothesis under current Charter |

## 8. Files Created / Modified (executor fills at spike close)

**Created (ephemeral, /tmp/spike_close_only/)**:
- `factors_selected.md`
- `adapters.py`
- `ic_results.csv`
- `errors.md` (if any compute failures)
- alpha101 upstream copies (if fetched)

**Created (durable)**:
- `docs/spikes/2026-05-30-close-only-factors-csi300-ic-spike-report.md`

**Modified**: NONE expected. Any modification outside `/tmp/` or `docs/spikes/` is a BLOCKER.

**Protected files NOT touched**:
- ALL of `data/cn_pit/`
- `strategies/active.json`
- `agents/audit_layer.py`
- `backtest/market_data.py` (frozen post-A2 PR)
- `backtest/factors/` (does NOT exist; do NOT create)

## 9. Time Budget Summary

| Task | Estimate |
|---|---|
| 1. Factor specification | 20 min |
| 2. Adapter / implementation | 25 min |
| 3. IC computation | 50 min |
| 4. Report | 25 min |
| **Total** | **2 h** |

If Task 3 overruns >50% (>75 min), kill_when (a) fires.
