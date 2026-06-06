# Research Charter v1

**Version:** 1.0-DRAFT
**Date:** 2026-05-26
**Status:** DRAFT — pending user review
**Owner:** User

## 1. Decision-grade question

On the current HS300 universe with the H47 unified-qfq price source, can we identify and deploy a strategy that simultaneously passes the H42 9-condition acceptance gate (verbatim, below) within a fixed total budget of 6 slices and 3 weeks?

## 2. Frozen validation layer

The following artifacts are FROZEN for the duration of Charter v1. No slice under this Charter may modify them. Any slice that requires modifying these artifacts is paused and split per Kill Criterion 2.

- **Universe:** `data/cn_pit/universe_h30_candidate.jsonl` (+ snapshot files). HS300 PIT candidate universe.
- **Price matrix:** `data/cn_pit/prices_h47_tushare_qfq_candidate.csv`. sha256 `34f3e38f...` (see `reports/h48_unified_qfq_h42_rerun_report.md` for full hash). Provider: `tushare:pro_bar:qfq`.
- **Benchmark:** HS300 via `tushare:index_daily`.
- **Gate definition:** The H42 9-condition acceptance gate, copied verbatim from `reports/h48_unified_qfq_h42_rerun_report.md` § Acceptance Gate:

  1. Deploy window: not blocked by H34 stop conditions
  2. Deploy window: zero execution warnings
  3. Deploy window: closed sells >= 30
  4. Deploy window: terminal losing streak < 5
  5. Positive windows: >= 4/5
  6. Unblocked windows: >= 3/5
  7. Beat HS300 windows: >= 2/5
  8. Deploy excess return > 0
  9. Max drawdown > -8%

- **Engine git tag:** `engine-frozen-v1` (to be created by user; Charter references it). No engine or loader edits are allowed under this Charter without splitting into a separate engine PR (see Kill Criterion 2).

## 3. Total slice budget

- Max slices under this Charter: **6**
- Max wall-clock weeks: **3**
- Spike budget per new hypothesis (before promoting to slice): **≤2 hours**
- A hypothesis that does not produce a signal-positive spike result within budget is killed and recorded per §9.

## 4. Kill criteria

The Charter is KILLED and a replacement Charter is required if ANY of these fire:

1. **Ceiling unbroken.** After 3 slices, the maximum `beat_HS300_windows` across all candidates does not exceed the current floor of **1/5**. Outcome: Charter KILLED; new Charter required with a different question, universe, or gate.
2. **Engine/loader contamination.** If any slice requires editing the engine OR data loader, that slice is paused and split into (a) an engine PR (makes no strategy claim) + (b) a strategy PR (uses the new engine). The slice consumes budget but the engine PR does not.
3. **Hard Prohibition violation.** If any slice produces a violation of `AGENTS.md` § Hard Prohibitions (data fabrication, provenance forgery, asymmetric restore, verdict mutation, silent success, silent workaround), the Charter pauses for incident review before the next slice runs. A second violation within the same Charter KILLs it.

## 5. Hypothesis pipeline

These are SUGGESTIONS drawn from `docs/strategy-optimization-sync.md` § H52 KILLED postmortem + `docs/spikes/2026-05-26-h42-on-h47-hs300-spike.md` § Implications. User makes the final call on which (if any) to spike. All hypotheses must spike before becoming slices.

1. **Gate-relaxation variant.** Lower the gate threshold to `beat_HS300_windows >= 1/5` + positive deploy excess on the H48 top-15. Tag all results RESEARCH_ONLY-PERMANENT. Accept that the H42 9-condition gate cannot be passed under HS300+H47; document what the "weak-deployable" envelope looks like.
2. **Non-momentum signal family.** Test a signal from outside the overlay family: low-volatility (minimum-variance selection), quality-momentum composite (not momentum-only), or event-driven (earnings surprise / analyst revision). Sketch the signal in a spike before committing to an Hxx.
3. **Cost-sensitive re-evaluation.** Re-run H48 top-15 with realistic round-trip cost assumptions (stamp duty 0.05%, commission 0.025%, slippage stress 5–20 bps). Determine if deploy excess becomes positive when costs are subtracted from the raw excess calculation.
4. **Cross-sectional composite rank (Hermes-proposed).** Replace the overlay-screening pipeline with a direct cross-sectional composite rank (e.g., equally weighted z-score across momentum + quality + value factors) that selects top-N stocks each rebalance. The overlay approach has hit the same 1/5 ceiling across H42/H48/H49b/H50b/H51b; this tests whether a rank-based selection escapes it. Spike only — no Hxx until signal-positive.

## 6. Hermes Scope Under This Charter

Copied from the A1 task brief (`docs/hermes-a1-research-charter-v1-draft-task.md` § Hermes scope):

- **ALLOWED:** bulk I/O (fetch, paginate, transform with frozen scripts), Markdown drafting, sha256 audit, read-only diffs.
- **FORBIDDEN:** writing new strategy scripts, monkey-patching, running acceptance-gate verdicts, modifying protected data artifacts.

## 7. Promotion rule

Restated from `docs/agents/workflow.md` § Promotion Rule:

A strategy cannot be promoted to live/config changes unless all are true:
- Data-quality gate passes.
- Execution gate passes.
- Multi-window robustness gate passes.
- JSON/report artifacts are consistent.
- Tests pass.
- Read-only review has no unresolved BLOCKER/HIGH/MEDIUM findings.

No new promotion rules are introduced by this Charter.

## 8. Pivot / KILL protocol

When a hypothesis is killed under this Charter, the postmortem MUST be appended to `docs/strategy-optimization-sync.md` under a heading:

```
## <name> — KILLED (YYYY-MM-DD)
```

Follow the template established by `docs/strategy-optimization-sync.md` § "H52 Universe-Expansion Line — KILLED (2026-05-26)": state the original question, what was delivered, sunk cost (slice count + wall time), root causes, what is kept vs. thrown away, and codified lessons.

## 9. References

This Charter was drafted from (read-only):
- `docs/strategy-optimization-sync.md` § H52 Universe-Expansion Line — KILLED (postmortem structure)
- `docs/spikes/2026-05-26-h42-on-h47-hs300-spike.md` (closed-original-question verdict)
- `docs/agents/workflow.md` (Promotion Rule, Hermes scope)
- `docs/agents/hxx-task-template.md` (slice format)
- `reports/h48_unified_qfq_h42_rerun_report.md` (frozen gate definition)
- `AGENTS.md` (Hard Prohibitions)

All Hard Prohibitions from `AGENTS.md` § "Always Applicable to All Agents" apply to every slice under this Charter. Briefs may ADD prohibitions; they never weaken the global set.

## 10. Approval line

```
Approved by user: [ ] YYYY-MM-DD
```
