# Hermes Task — A1: Draft Research Charter v1

## Context

Project has run 20+ Hxx slices (H39 → H52h) over ~4 weeks without producing a deployable strategy. Postmortem in `docs/strategy-optimization-sync.md` § "H52 Universe-Expansion Line — KILLED" identifies the root cause as the Hxx framework lacking a project-level **Research Charter** that declares the decision-grade question, success threshold, total slice budget, and kill criteria up front. Without a Charter, every closed-out slice spawns "the next question" instead of forcing a hard pivot or KILL.

This task drafts that Charter as a single page. It is reviewed by the user; do NOT promote it to live until merged.

## Objective

Produce ONE file: `docs/drafts/research-charter-v1-DRAFT.md`. The file is a complete Charter v1 draft, ready for user review. Do NOT modify `docs/strategy-optimization-sync.md`, do NOT modify any file under `docs/agents/`, do NOT modify any file in the project root.

## Inputs (read-only — do not modify)

- `docs/strategy-optimization-sync.md` (latest postmortem section "H52 Universe-Expansion Line — KILLED" — read for context on what failed)
- `docs/spikes/2026-05-26-h42-on-h47-hs300-spike.md` (read for the closed-out original question and current "signal exhausted on HS300" verdict)
- `docs/agents/workflow.md` (read for current Hxx lifecycle vocabulary so the Charter aligns)
- `docs/agents/hxx-task-template.md` (read for the slice format the Charter governs)
- `reports/h48_unified_qfq_h42_rerun_report.md` (read for the actual gate definition that should be the Charter's frozen threshold)
- `AGENTS.md` (read for Hard Prohibitions vocabulary the Charter must reference)

## Output

Single file: `docs/drafts/research-charter-v1-DRAFT.md`

### Required sections (in order)

1. **Header block** — version, date, status (`DRAFT — pending user review`), owner.
2. **Decision-grade question** — ONE sentence stating the project's binary success question. Suggested draft (adjust if you see a better fit from inputs): "On the current HS300 universe with the H47 unified-qfq price source, can we identify and deploy a strategy that simultaneously passes the H42 9-condition acceptance gate (verbatim) within a fixed total budget?"
3. **Frozen validation layer** — list of artifacts that are FROZEN for the duration of Charter v1. Must include:
   - Universe: `data/cn_pit/universe_h30_candidate.jsonl` (+ snapshots)
   - Price matrix: `data/cn_pit/prices_h47_tushare_qfq_candidate.csv`
   - Benchmark: HS300 via tushare:index_daily
   - Gate definition: the 9 conditions from H42, copied verbatim from the H48 report § "Acceptance Gate"
   - Engine git tag: `engine-frozen-v1` (to be created by user; Charter references it)
4. **Total slice budget** — concrete numbers:
   - Max slices under this Charter: **6**
   - Max wall-clock weeks: **3**
   - Spike budget per new hypothesis (before promoting to slice): **≤2h**
5. **Kill criteria** — at least 3 explicit conditions. Suggested seeds (refine + add):
   - If after 3 slices the **maximum `beat_HS300_windows`** across all candidates does not exceed the current floor of 1/5, the Charter is KILLED and a new Charter is required.
   - If any slice requires editing the engine OR loader, that slice is paused and split into (a) engine PR (no claim) + (b) strategy PR (uses new engine).
   - If any slice produces a Hard Prohibition violation (per AGENTS.md), the Charter pauses for incident review before the next slice runs.
6. **Hypothesis pipeline** — list of 3–5 candidate hypotheses to spike under this Charter. Suggested seeds (refine using `docs/strategy-optimization-sync.md` H49b/H50b/H51b verdicts and the spike's "what was not tested"):
   - Gate-relaxation variant (`beat_HS300_windows ≥ 1/5` + positive deploy excess) on existing H48 top-15 — RESEARCH_ONLY-PERMANENT tag
   - Non-momentum signal family (low-vol, quality-momentum, or event-driven) — sketch only
   - Cost-sensitive re-evaluation of H48 top-15 (round-trip cost / slippage stress)
   - One genuinely new direction Hermes proposes based on reading the spike + sync doc
7. **Hermes scope under this Charter** — copy verbatim from this brief:
   - ALLOWED: bulk I/O (fetch, paginate, transform with frozen scripts), Markdown drafting, sha256 audit, read-only diffs.
   - FORBIDDEN: writing new strategy scripts, monkey-patching, running acceptance-gate verdicts, modifying protected data artifacts.
8. **Promotion rule** — restate from `docs/agents/workflow.md` § Promotion Rule. No new promotion rules.
9. **Pivot / KILL protocol** — when a hypothesis is killed under this Charter, the postmortem MUST be appended to `docs/strategy-optimization-sync.md` under a `## <name> — KILLED (YYYY-MM-DD)` heading following the H52 KILLED template.
10. **Approval line** — placeholder `Approved by user: [ ] YYYY-MM-DD`.

### Style

- One page max when rendered (target ~120 lines).
- No emojis.
- Reference inputs by path; do not duplicate their content.
- Hypothesis seeds are SUGGESTIONS; user makes final call. Make this explicit in section 6.

## Hard Prohibitions

### Always Applicable (from AGENTS.md § Hard Prohibitions — copy verbatim into the Charter's references; do NOT violate)

- **No data fabrication.** Do not invent file paths, sha256 values, gate condition numbers, or report references. Every concrete value in the Charter must come from a file you actually read; if a value is unknown, write `TBD — user to fill` instead of guessing.
- **No source provenance forgery.** Charter cites paths only as `data/cn_pit/...`, `backtest/runs/...`, etc.; do not invent tushare endpoints or sha256 values.
- **Symmetric restore.** Not applicable (no monkey-patches in this task).
- **Original ingestion verdicts immutable.** Charter must not redefine H42's 9-condition gate; copy verbatim from `reports/h48_unified_qfq_h42_rerun_report.md` § Acceptance Gate.
- **Exit-code is not acceptance.** Task is complete only when the DRAFT file exists, is well-formed Markdown, ≤140 lines, and contains all 10 required sections.
- **Modification reporting.** Final response MUST enumerate every file created or modified.
- **No silent workarounds.** If any required input file is missing, STOP and surface — do not invent content.

### Task-Specific Prohibitions

- **DRAFT MODE ONLY.** Do NOT modify `docs/research-charter-v1.md` (live target), do NOT modify any file under `docs/agents/`, do NOT modify any Hxx brief, do NOT modify any file under `scripts/`, `data/`, `backtest/`, `reports/`, `tests/`, project root.
- ALL output goes to `docs/drafts/research-charter-v1-DRAFT.md`. If the file exists, OVERWRITE it; if its parent dir does not exist, create only `docs/drafts/`.
- Do NOT create the `engine-frozen-v1` git tag. The Charter references it; user creates it.
- Do NOT execute any backtest, search, or strategy script.
- Do NOT make network calls (no Tushare, no GitHub).

## Smoke / Full Command

N/A — doc-writing task. Verification by file existence + structural grep.

## Verification

Hermes runs these BEFORE declaring complete:

```bash
test -f docs/drafts/research-charter-v1-DRAFT.md || { echo "MISSING DRAFT"; exit 1; }
[ "$(wc -l < docs/drafts/research-charter-v1-DRAFT.md)" -le 140 ] || { echo "TOO LONG (>140 lines)"; exit 1; }
for section in "Decision-grade question" "Frozen validation layer" "Total slice budget" "Kill criteria" "Hypothesis pipeline" "Hermes scope" "Promotion rule" "Pivot / KILL protocol" "Approval line"; do
  grep -q "$section" docs/drafts/research-charter-v1-DRAFT.md || { echo "MISSING SECTION: $section"; exit 1; }
done
echo "VERIFICATION PASS"
```

If any check fails, fix and re-verify. Do NOT declare exit 0 on failure.

## Acceptance Gate

- [ ] `docs/drafts/research-charter-v1-DRAFT.md` exists.
- [ ] ≤140 lines.
- [ ] Contains all 10 required sections by name.
- [ ] Gate definition is identical to H42 9-condition gate from H48 report (numerical thresholds match).
- [ ] No file outside `docs/drafts/` is modified.
- [ ] Final response enumerates every file created/modified.

## Closure

After verification PASS, write a single line to the final response: `Draft ready for user review: docs/drafts/research-charter-v1-DRAFT.md`. Do NOT update `docs/strategy-optimization-sync.md`. Do NOT update `docs/agents/next-slices.md`.
