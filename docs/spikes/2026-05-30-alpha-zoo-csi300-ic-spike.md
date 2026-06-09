# Spike: Alpha Zoo gtja191 Top-10 IC on CSI300 — Pre-H53

Date: 2026-05-30
Status: DRAFT — pending A2 engine PR merge (loader provenance must be hardened first)
Charter: `docs/research-charter-v1.md` (v1.0-DRAFT) — Charter §5 hypothesis #4 (cross-sectional composite rank, Hermes-proposed)
Spike budget: **≤2 wall-hours** (Charter §3)
Parent spec: `docs/superpowers/specs/2026-05-30-vibe-trading-borrow-plan.md` § 5.3
Worker scope: ALLOWED — bulk I/O, Markdown drafting, sha256 audit, read-only diffs (Charter §6). FORBIDDEN — modifying protected artifacts, writing strategy scripts.

> **Spike, not slice.** Per Charter §5: "All hypotheses must spike before becoming slices." This document defines the spike. Promotion to H53 (full Alpha Zoo IC bench on CSI300 + CSI500) requires this spike to be signal-positive AND user approval.

---

## 0. Decision-grade Question (Y/N)

> Do at least 3 of the gtja191 top-10 most-cited factors achieve `|IC| > X` AND `IR > Y` on the H47 frozen CSI300 universe over the period `2025-01-01 → 2026-05-18`, with all data sourced through the hardened loader registry (post-A2)?

**X, Y values: PENDING** — see Q3 in parent spec. Until user pins Charter-aligned thresholds, the spike report writes `PROPOSED_THRESHOLD: |IC| > 0.03 AND IR > 0.5` as occupancy values, tagged `RESEARCH_ONLY-PERMANENT` (not promotion-eligible).

A `YES` answer (signal-positive) → propose H53 brief with full ~300-factor bench.
A `NO` answer → write 1-page postmortem to `docs/strategy-optimization-sync.md`, kill the hypothesis, return budget to Charter pool.

## 1. Hard Prereqs (block this spike from starting until met)

- [ ] **A2 engine PR merged** (`docs/superpowers/plans/2026-05-30-loader-registry-engine-pr.md` Tasks 1-7 all green, AG-1..AG-6 all pass). Spike depends on auditable provenance — running on unhardened ingestion repeats H49a's "silent success" anti-pattern.
- [ ] **Vibe-Trading fork created** at `zhuosama/Vibe-Trading` with locked commit SHA. Spike borrows 10 factor definitions; without a pinned SHA, "borrow gtja191" is ambiguous.
- [ ] **`THIRD_PARTY_NOTICES.md` populated** with the actual upstream commit SHA + MIT text. (A2 PR Task 6.3 seeded the file; this spike fills the SHA.)

If any prereq is unmet, this spike's `kill_when` fires immediately — surface as a finding, do not work around.

## 2. Scope

### 2.1 In scope

- Borrow 10 specific gtja191 factor definitions from upstream `agent/src/factors/gtja191/` (which 10: see § 4 Task 1)
- Adapt each factor to local PIT schema (column names: `close`, `open`, `high`, `low`, `volume`, `amount` — confirm vs upstream Q2 in parent spec)
- Compute IC (Pearson rank correlation between factor t and forward 1-day return t+1) for each factor on the CSI300 H47 frozen universe
- Compute IR (mean IC / std IC) over rolling 60-day windows
- Single-factor analysis only — NO multi-factor composition in this spike (out of scope per § 2.2)

### 2.2 Out of scope

- Remaining ~180 gtja191 factors (= full H53 if spike +)
- alpha101 factors entirely (= full H53)
- Multi-factor IC composition / ranking strategies
- CSI500 universe (= full H53 only after CSI300 + per Charter §2 frozen-universe rule)
- Cost/turnover modeling (= separate Charter §5 #3 hypothesis)
- Backtest engine integration — IC compute is read-only on price + factor frames

## 3. Hard Prohibitions — Always Applicable (copy from `AGENTS.md`)

(Copy from `AGENTS.md` § Hard Prohibitions — same boilerplate as A2 PR plan § 3. The executor reads them with the brief, not just by reference.)

- **No data fabrication**: do NOT add, modify, "complete", or "round up" rows in any protected artifact.
- **No source provenance forgery**: borrowed factor files must retain upstream copyright header + upstream commit SHA reference.
- **Symmetric restore**: any temp script must use try/finally.
- **Original ingestion verdicts immutable**: do NOT modify any prior Hxx run JSON.
- **Exit-code is not acceptance**: every claim in the spike report must be physically verifiable (sha256 matches, IC number is reproducible).
- **No silent workarounds**: if a borrowed factor formula references a column that doesn't exist in local PIT schema, STOP and surface — do not invent a substitute.
- **sha256 audit hooks**: pin upstream commit SHA + sha256 of each borrowed factor file. mismatch = `BLOCKER`.

### 3.1 Spike-Specific Prohibitions

- Do **NOT** run on any data source other than the post-A2 hardened loader output. (If A2 fallback to YFinance, the spike report writes `selected_provider = yfinance:download` and the IC numbers are tagged `LOADER_FALLBACK_PROVENANCE_WARN`.)
- Do **NOT** write any factor file directly to `backtest/factors/` — spike artifacts live ONLY in `/tmp/spike_alpha_zoo/` or `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike-artifacts/`
- Do **NOT** modify `agents/audit_layer.py` or `strategies/active.json`
- Do **NOT** install new pip packages
- Do **NOT** exceed 2 wall-hours total

## 4. Task Breakdown

### Task 1 — Pick the 10 Factors

**Budget**: 15 min

- [ ] Read upstream `agent/src/factors/gtja191/registry.py` for factor metadata + `bench_runner.py` for any "frequently-used" / "highlighted" flag
- [ ] If upstream provides a ranking → pick top-10 by upstream score; if not → pick 10 with shortest formula AST (proxy for "simpler = more cited")
- [ ] Document selection rationale in spike report § Selected Factors
- [ ] Each factor entry MUST include: upstream file path, upstream commit SHA, sha256 of file at that SHA

**Output**: `/tmp/spike_alpha_zoo/factors_selected.md` (10 entries)

### Task 2 — Local Schema Adapter

**Budget**: 30 min

- [ ] Confirm local PIT schema column names from `data/cn_pit/prices.csv` header
- [ ] For each of the 10 factors, write a shim function that maps upstream column references to local column names. ONE function per factor. NO global rewrite.
- [ ] If ANY factor references a column not in local schema (e.g. `amount` if local doesn't have it) → STOP, write that factor to a `unsupported.md` list with the missing column, continue with the remaining factors. (Per Charter §6 ALLOWED — surfacing structural gaps.)

**Output**: `/tmp/spike_alpha_zoo/adapters.py` (Python; ≤ 200 LOC)

### Task 3 — IC Computation

**Budget**: 60 min

- [ ] Load CSI300 prices from `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (Charter §2 frozen, immutable)
- [ ] For each supported factor:
  - Compute factor values on `2025-01-01 → 2026-05-18` (H28 baseline period, see `virtual_trader_h28_baseline.md`)
  - Compute forward 1-day returns from the same price frame
  - Compute Pearson rank IC per cross-section (date)
  - Aggregate: mean IC, std IC, IR = mean / std
  - Aggregate: rolling 60-day IR distribution
- [ ] If a factor compute raises (NaN propagation, divide-by-zero, etc.) → log to `errors.md`, mark factor `COMPUTE_FAILED`, continue with others

**Output**: `/tmp/spike_alpha_zoo/ic_results.csv` (one row per factor: factor_id, mean_ic, std_ic, ir, n_obs, status)

### Task 4 — Decision + Spike Report

**Budget**: 15 min

- [ ] Compute decision: count factors with `|mean_ic| > 0.03 AND ir > 0.5` (PROPOSED_THRESHOLD, see § 0)
- [ ] If count >= 3 → spike result `SIGNAL_POSITIVE_PROPOSED` (decision pending threshold finalization)
- [ ] If count < 3 → spike result `SIGNAL_NEGATIVE`
- [ ] Write `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike-report.md` containing:
  - Selected factors (with sha256, upstream SHA)
  - Local adapter notes
  - IC results table
  - Provenance block (loader fallback_chain, selected_provider, prices.csv sha256)
  - Decision result + which factors passed
  - Time spent vs 2h budget

**Output**: `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike-report.md`

## 5. kill_when (single-sentence exit)

```
kill_when = "(a) A2 prereqs in § 1 unmet, OR (b) 2-hour wall budget exhausted with
fewer than 5 factors completing IC compute, OR (c) loader provenance block in the
spike report shows fallback_reason indicating Tushare unavailable AND user has not
authorized YFinance-tagged RESEARCH_ONLY results."
```

## 6. Acceptance Gates (for the spike itself — not for H53 promotion)

| # | Check | Expected |
|---|---|---|
| SG-1 | All 10 selected factors have upstream commit SHA + sha256 logged | Y/N |
| SG-2 | `prices.csv` sha256 unchanged after spike | `5efc8ec7...` (H28 baseline) |
| SG-3 | IC results CSV has 10 rows, status field non-empty | Y/N |
| SG-4 | Spike report enumerates loader provenance from the post-A2 metadata | Y/N |
| SG-5 | Wall-clock <= 2h documented in spike report | Y/N |
| SG-6 | If spike result is `SIGNAL_NEGATIVE`, postmortem section drafted ready for `docs/strategy-optimization-sync.md` | Y/N |

## 7. Promotion Rule (spike → H53)

This spike does NOT auto-promote. To open H53 brief:

1. Spike report shows `SIGNAL_POSITIVE_PROPOSED`
2. User reviews and pins Charter-aligned thresholds (closes parent spec Q3)
3. User explicitly authorizes "promote to H53"
4. H53 brief drafted at `docs/hermes-h53-alpha-zoo-ic-bench-task.md` per `docs/agents/hxx-task-template.md`
5. H53 consumes 1 Charter slice budget (Charter §3)

## 8. Files Created / Modified (executor fills at spike close)

**Created (under `/tmp/spike_alpha_zoo/`, ephemeral)**:
- `factors_selected.md`
- `adapters.py`
- `ic_results.csv`
- `errors.md` (if any)
- `unsupported.md` (if any)

**Created (durable, in repo)**:
- `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike-report.md`

**Modified**: NONE expected. If any file outside `/tmp/` or `docs/spikes/` is modified, that's a BLOCKER finding.

**Protected files NOT touched** (verify with `git diff`):
- ALL of `data/cn_pit/`
- `strategies/active.json`
- `agents/audit_layer.py`
- `backtest/market_data.py` (post-A2 frozen for this spike)
- `backtest/factors/` (does not exist yet; spike does NOT create it — H53 does)

## 9. Time Budget Summary

| Task | Estimate |
|---|---|
| 1. Pick 10 factors | 15 min |
| 2. Local schema adapter | 30 min |
| 3. IC computation | 60 min |
| 4. Spike report | 15 min |
| **Total** | **2 h** (Charter §3 ceiling) |

If any task overruns its budget by >50%, the spike's `kill_when` clause (b) fires — STOP, write partial report, return budget.
