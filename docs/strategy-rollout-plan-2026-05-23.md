# Strategy Rollout Plan — 2026-05-23

**Scope:** the path from the current H49a-closed / H49b-running state to a researched candidate becoming the active `main_strategy` slot in `strategies/active.json`, picked up by the daily `pre_market` / `post_market` Hermes agent workflows.

**Definition of "落地" (in this repo):**

| Layer | What it means here |
|---|---|
| Virtual-trader project | `strategies/active.json` `main_strategy` slot replaced; daily `agents/workflows/workflow_pre_market_*.json` consumes the new rules; `value_account/h34_shadow_account_config.json` aligned; ledger validator stays clean. |
| NOT in scope | Real-money trading. This repo is paper / shadow only by design (`AGENTS.md` line 23, `CLAUDE.md` line 26). |

This plan is **a forecast, not a commitment**. Plans rot. Each Hxx slice's actual verdict can rewrite the branch downstream. Re-read this file before each phase boundary.

---

## Current state snapshot

- Done (closed Hxx): H39, H40, H41, H42, H43, H44, H45, H46, H47, H48, H49a
- Active right now: H49b (dispatched 2026-05-23 ~02:00, expected ~30 min wall clock)
- Pending: everything below
- Active production strategy (untouched by H42–H49 line): `main_strategy` = "价值趋势混合策略" v1.0.5 in `strategies/active.json`
- Daily workflow surfaces that need eventual touch: `agents/workflows/workflow_pre_market_*`, `workflow_post_market_*`, `workflow_weekly_review_*`; `agents/execution_planner.py`, `agents/strategy_maintainer.py`, `agents/risk_controller.py`; `value_account/h34_shadow_account_config.json`
- Paper-monitor harness already in place: `scripts/h46_paper_forward_monitor.py` + run/report artifacts (so we don't need to build a new monitor for any new candidate)

The H42 acceptance gate has been the binding constraint throughout: `beat_HS300_windows = 0/5` for every top candidate in H42 and H48. H49b is the first attempt that changes the alpha shape (sector neutrality), not just the parameters.

---

## Phase decomposition

### Phase A — Alpha discovery (current)

| ID | Description | Calendar | Notes |
|---|---|---|---|
| **H49b** | Sector-neutral RS search; H42 gate; reports `beat_HS300_windows` delta | 1 day | dispatched; verdict expected today |
| **H49c** (conditional) | Tighten grid around best H49b sector-cap params, only if H49b shows `beat_HS300_windows ≥ 1/5` at any candidate | 2–3 days | skipped if H49b stays 0/5 |
| **H50** | Quality-Value composite redesign (H45 PRD direction #2): replace value-score with auditable components (profitability / balance-sheet / cash-flow / PIT-safe valuation only) | 5–7 days | always needed; provides a second alpha signal independent of sector RS |
| **H51** | Risk Model Overlay (H45 PRD direction #4): single-name max weight, volatility-scaled targets, liquidity participation cap, min active names | 3–5 days | composes with H49b/H49c/H50 outputs |
| **H52** | Walk-forward validation (H45 PRD §Experiment Design): rolling train/validate/test windows; locks the candidate against overfit | 3–5 days | gate before any promotion talk |

Phase A exit criterion: at least one candidate that passes the H42 gate (all 9 conditions) AND survives walk-forward without parameter re-fit. If no candidate clears after H49b+H50+H51, the project escalates to "alpha source not findable in current universe/feature space" — see "Pessimistic path" below.

### Phase B — Paper-only forward observation

| ID | Description | Calendar | Notes |
|---|---|---|---|
| **H53** | Register the Phase-A winning candidate with `scripts/h46_paper_forward_monitor.py`; daily/weekly metric collection wired into existing cron | 1–2 days | small integration work; harness exists |
| **H54** | Forward paper trading window | **60 trading days** (~3 calendar months) | calendar-bound; cannot be compressed |

Phase B exit criterion: candidate's forward `beat_HS300_windows` ≥ 1/3 of completed weekly checkpoints; forward MaxDD ≤ −10%; zero `BLOCKED` execution states; turnover within H34 thresholds.

If forward results diverge sharply from backtest (forward Sharpe < 0.5× backtest Sharpe, OR forward beat_HS300 < backtest expectation), abort to Phase A with a documented post-mortem (`H55`).

### Phase C — Promotion engineering

| ID | Description | Calendar | Notes |
|---|---|---|---|
| **H56** | Translate the winning candidate's rules into `strategies/active.json` `lab_strategy` slot; do NOT touch `main_strategy` yet | 1–2 days | rules-as-JSON; agent loaders already consume `lab_strategy` |
| **H57** | Update `value_account/h34_shadow_account_config.json` if H49b/H50 changed top_n, max_position_pct, stop_loss_pct, take_profit_pct, rebalance_freq_days. Bump config version; keep `.bak` for rollback | 0.5 day | tightly scoped edit |
| **H58** | Pre/post-market workflow validators (`agents/execution_planner.py`, `agents/risk_controller.py`) sanity-check the new rules on a dry-run pre-market | 1 day | catch schema/field drift before live cron pickup |
| **H59** | Promotion-gate audit run: full deployment-gate sweep per H45 PRD §Deployment Gates (9 gates: data-quality / price-source / execution / multi-window / benchmark / turnover / artifact consistency / tests / read-only review) | 1–2 days | hard pre-promotion checkpoint |

Phase C exit criterion: every one of the 9 H45 PRD deployment gates passes; `validate_ledger_consistency.py --strict` clean; Claude or Hermes read-only review with zero unresolved BLOCKER/HIGH/MEDIUM. If any gate fails, do not advance.

### Phase D — Activation in virtual trader

| ID | Description | Calendar | Notes |
|---|---|---|---|
| **H60** | Promote `lab_strategy` → `main_strategy`. Snapshot the prior v1.0.5 `main_strategy` as `prior_strategy` for one-click rollback | 0.5 day | the actual "落地" moment |
| **H61** | First 5 daily `pre_market` cron runs under the new rules: review each `workflow_pre_market_*.json` artifact for execution warnings, position drift, intent vs realized trade gaps | 1 week | calendar-bound; daily check-ins |
| **H62** | Closeout review: 30-day post-promotion checkpoint. Does live shadow performance match Phase-B forward paper baseline? | 1 day at +30d | sets up rollback or continuation |

Phase D exit criterion: 30 calendar days post-promotion with no rollback trigger fired (see "Abort & rollback" below).

---

## Calendar estimate

Bottom-up sum, assuming each focused Hxx slice takes the listed working days and there is no waiting between them:

| Phase | Optimistic | Realistic | Pessimistic |
|---|---|---|---|
| A — Alpha discovery | 6 wd | 11 wd | 18 wd |
| B — Forward paper | 60 td (~3 months) | 60 td | 60 td |
| C — Promotion engineering | 3.5 wd | 5 wd | 7 wd |
| D — Activation + closeout | 0.5 wd + 30 cd | 0.5 wd + 30 cd | 0.5 wd + 30 cd |

`wd` = working days (focused; assumes 1 active slice at a time, no calendar gaps).
`td` = trading days. `cd` = calendar days.

Phase B is the dominant cost — 60 trading days of forward observation is non-compressible without sacrificing the gate. **Total calendar time from today to fully-activated `main_strategy` ≈ 4.5–6 months**, of which only ~4–6 weeks is researcher-active work; the rest is paper observation.

If the user accepts a shorter Phase-B (e.g., 30 trading days) on the grounds that this is paper-only and rollback is trivial, total compresses to ~3 months. Trade-off: weaker statistical confidence the candidate didn't overfit.

---

## Path branching

**Optimistic path** (P ~10%): H49b finds `beat_HS300_windows ≥ 2/5` and clears H42 gate. Skip H49c (no tightening needed); H50/H51 run as planned for ensemble strength; promote H49b candidate. Total ~4.5 months.

**Realistic path** (P ~50%): H49b stays `RESEARCH_ONLY` but shows `beat_HS300_windows = 1/5` somewhere — sector neutrality directionally helps. H49c tightens. H50 redesigns value score; combined with H49c the ensemble crosses the gate. Total ~5–6 months.

**Pessimistic path** (P ~30%): H49b + H49c + H50 + H51 still all `RESEARCH_ONLY` with `beat_HS300_windows = 0/5` everywhere. Diagnosis: H30 HS300 universe is too narrow, or alpha is in a feature class we don't have (intraday flow, options skew, news sentiment, alternative data). Either expand the universe (CSI500 / CSI1000) — adds H62-H65 ingestion work, 2–3 months — or accept that paper-only monitoring of the best `RESEARCH_ONLY` candidate is the terminal state. The legacy `main_strategy` v1.0.5 stays active.

**Abort path** (P ~10%): a structural problem surfaces (e.g., H49a multi-mapped = 25.4% causes systematic sector mis-classification; H47 qfq adjustment has a flaw H42 framework can't see). Halt Phase A, fix the data layer, re-run from H42.

These probabilities are gut-feel anchors, not derived. Update them after H49b lands.

---

## Critical gating points

These are the moments where this plan should be re-evaluated, not just executed:

1. **After H49b** — does sector neutrality move `beat_HS300_windows` at all? If yes by any amount → realistic path. If still 0/5 across all 200 runs → skip H49c, jump to H50.
2. **After H50** — does the redesigned value score show component-level explanatory power, or is it just re-fitting? If components don't matter, abort H50 and reconsider universe expansion.
3. **After H52 (walk-forward)** — does the candidate survive on the held-out test window? If walk-forward Sharpe collapses to <50% of in-sample, candidate is overfit; cycle back to Phase A with a tighter regularization.
4. **At Phase B day 20** — early forward divergence check. If forward MaxDD already > backtest's worst window MaxDD by day 20, abort early.
5. **At H59 promotion-gate audit** — if any of the 9 H45 PRD gates fails, do NOT proceed to H60. Document the failed gate and either fix or restart.
6. **At H62 closeout** — 30-day live shadow vs Phase-B paper baseline divergence. Sets up rollback or "v2.0.0 strategy" stamping.

---

## Abort & rollback

Always-on rollback levers (do not require new infrastructure):

- `strategies/active.json` — keep `prior_strategy` snapshot from H60; one-edit revert
- `value_account/h34_shadow_account_config.json.bak` — already exists; restore with `cp`
- Daily workflow JSON artifacts are write-only audit; rollback is just "next day's cron uses the reverted active.json"

Rollback triggers during Phase D:

- Two consecutive workflow_pre_market_* with `execution_blocked = true`
- 5-trading-day cumulative drawdown > −5% AND > 2× HS300 same-window drawdown
- Ledger validator fails on any INV check
- Any agent (risk_controller, audit_subagent) raises a BLOCKER

If any trigger fires, revert `main_strategy` to `prior_strategy` within one trading day; record the trigger and write an Hxx post-mortem before any re-promotion attempt.

---

## What this plan does NOT cover

- Real-money / non-paper trading. Out of scope per project policy.
- Strategy ensembling logic across `main_strategy` + `lab_strategy` simultaneously (current schema runs one at a time).
- Alternative-data ingestion (news, sentiment, intraday flow). Would require its own H6x track if pessimistic path triggers.
- Multi-account allocation (current scheme is single `main` account + demo accounts).
- Cost-model refinement beyond the existing commission / slippage / stamp tax constants.

---

## Files this plan expects to touch (eventually, not now)

Read-only by this plan; only modified inside their corresponding Hxx slices:

- `strategies/active.json` — at H60
- `value_account/h34_shadow_account_config.json` — at H57
- `agents/execution_planner.py` — possible at H58 if schema drift
- `agents/risk_controller.py` — possible at H58 if new risk constraints
- `agents/strategy_maintainer.py` — possible at H58 if new rule shape
- `scripts/h46_paper_forward_monitor.py` — at H53 (add new candidate to the monitor's tracked list)
- `~/.hermes/cron/*` — at H60 verify the daily cron still picks up the new rules; usually no edit needed

Hard rules from `AGENTS.md` / `CLAUDE.md` (apply to every step):

- Do not modify production trading config unless the Hxx task brief explicitly permits it AND all promotion gates pass.
- Do not place live orders.
- Each promotion is a separate Hxx with its own review.
