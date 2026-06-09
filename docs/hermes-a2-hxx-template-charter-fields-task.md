# Hermes Task — A2: Add Charter Reference Fields to hxx-task-template

## Context

Hxx briefs currently lack mandatory upfront declarations of `question / threshold / budget / kill_when`. As a result, slices proliferate (H52a–h was 8 slices without an explicit kill criterion) and bug fixes get treated as research claims. Charter v1 (A1) introduces these as project-level concepts; this task makes them MANDATORY in every Hxx brief by extending the brief template.

## Objective

Produce ONE file: `docs/drafts/hxx-task-template-DRAFT.md`. The file is a COPY of the current `docs/agents/hxx-task-template.md` with a new "Charter Reference" section inserted near the top (after "Objective", before "Inputs") and a short cross-reference at the end. Do NOT modify the live `docs/agents/hxx-task-template.md`.

## Inputs (read-only — do not modify)

- `docs/agents/hxx-task-template.md` (current live template — base for the draft)
- `docs/strategy-optimization-sync.md` (read § "H52 Universe-Expansion Line — KILLED" for vocabulary)
- This brief

## Output

Single file: `docs/drafts/hxx-task-template-DRAFT.md`

### Required new section

Insert this section verbatim after the `## Objective` section and before `## Inputs`:

```markdown
## Charter Reference (MANDATORY — fill all 4 fields)

Every Hxx brief must declare these four fields explicitly. A brief missing any of these is REJECTED at review.

- **Charter:** `docs/research-charter-v1.md` (or successor) — the active Research Charter under which this slice runs. If no Charter is active, the brief must not be dispatched.
- **Question (decision-grade):** One sentence. Must be answerable Y/N or with a concrete number against the Charter's threshold. Not "explore X" or "investigate Y."
- **Threshold (pass criterion):** Numerical, copied or referenced from the Charter. Example: `beat_HS300_windows ≥ 2/5 AND deploy_excess > 0 AND gate_pass_count ≥ 1`. State the exact value.
- **Budget:** Two numbers — `max_wall_hours` and `max_revisions` (re-dispatches after review). Default: `max_wall_hours = 4`, `max_revisions = 1`. Briefs exceeding either auto-pause for user decision.
- **kill_when:** One sentence stating the condition that ends this slice without promotion. Example: `kill_when = "all 4 sub-runs report gate_pass_count = 0 AND best beat_HS300_windows ≤ 1/5"`. Slices without a kill_when become open-ended (the H52 failure mode).

If a brief is a bug fix rather than a research claim, do NOT create a new Hxx number — open a normal commit referencing the affected Hxx in the commit message. See `docs/agents/workflow.md` § "Bug ≠ Slice."
```

### Required cross-reference

Append this paragraph at the very end of the draft (after "Closure Note"):

```markdown
## Charter Cross-Reference

This template governs the SLICE level. Project-level question, frozen validation layer, total slice budget, and KILL protocol live in the active Research Charter (`docs/research-charter-v1.md` or successor). A slice cannot weaken or override the Charter; it can only refine the slice-level threshold within the Charter's bounds.
```

### What to preserve from the live template

EVERY OTHER SECTION of `docs/agents/hxx-task-template.md` MUST be copied into the draft VERBATIM — including the full "Hard Prohibitions / Always Applicable" boilerplate, "Smoke Command", "Full Command", "Verification", "Acceptance Gate", "Review Prompt", and "Closure Note" sections. Do NOT rewrite, reorder, abbreviate, or "improve" any pre-existing section.

## Hard Prohibitions

### Always Applicable (from AGENTS.md § Hard Prohibitions)

- **No data fabrication.** Do not invent any pre-existing template content. If the live template's content differs from what you expect, copy what IS there verbatim — do not "fix" it.
- **No source provenance forgery.** Not directly applicable; do not fabricate file references in the new section.
- **Symmetric restore.** Not applicable (no monkey-patches).
- **Original ingestion verdicts immutable.** Not applicable (no data ingestion).
- **Exit-code is not acceptance.** Task complete only when the draft file exists, contains the new section in the correct position, ends with the cross-reference, and a `diff -u` against the live template shows ONLY the two insertions.
- **Modification reporting.** Final response MUST enumerate every file created/modified.
- **No silent workarounds.** If the live template is missing, STOP and surface.

### Task-Specific Prohibitions

- **DRAFT MODE ONLY.** Do NOT modify `docs/agents/hxx-task-template.md`, do NOT modify any other file under `docs/agents/`, do NOT modify any Hxx brief, do NOT modify anything under `scripts/`, `data/`, `backtest/`, `reports/`, `tests/`, project root.
- ALL output goes to `docs/drafts/hxx-task-template-DRAFT.md`. Overwrite if exists; create `docs/drafts/` if missing.
- Do NOT add additional sections beyond the two specified. Scope creep here is the exact failure mode this task is fighting.
- Do NOT execute scripts. Do NOT make network calls.

## Smoke / Full Command

N/A — doc-editing task. Verified by diff structure.

## Verification

Hermes runs these BEFORE declaring complete:

```bash
test -f docs/drafts/hxx-task-template-DRAFT.md || { echo "MISSING DRAFT"; exit 1; }

# The draft must contain the live template content (verbatim sections preserved)
for live_marker in "Hard Prohibitions" "Always Applicable" "Smoke Command" "Full Command" "Verification" "Acceptance Gate" "Review Prompt" "Closure Note"; do
  grep -q "$live_marker" docs/drafts/hxx-task-template-DRAFT.md || { echo "MISSING LIVE SECTION: $live_marker"; exit 1; }
done

# The draft must contain the new section
grep -q "Charter Reference (MANDATORY — fill all 4 fields)" docs/drafts/hxx-task-template-DRAFT.md || { echo "MISSING CHARTER REFERENCE SECTION"; exit 1; }
grep -q "Charter Cross-Reference" docs/drafts/hxx-task-template-DRAFT.md || { echo "MISSING CROSS-REFERENCE PARA"; exit 1; }

# The new section must come BEFORE "## Inputs"
charter_line=$(grep -n "Charter Reference (MANDATORY" docs/drafts/hxx-task-template-DRAFT.md | head -1 | cut -d: -f1)
inputs_line=$(grep -n "^## Inputs" docs/drafts/hxx-task-template-DRAFT.md | head -1 | cut -d: -f1)
[ -n "$charter_line" ] && [ -n "$inputs_line" ] && [ "$charter_line" -lt "$inputs_line" ] || { echo "CHARTER SECTION OUT OF ORDER (must precede ## Inputs)"; exit 1; }

# Diff must show ONLY insertions (no deletions of live content)
removed=$(diff -u docs/agents/hxx-task-template.md docs/drafts/hxx-task-template-DRAFT.md | grep -E '^-[^-]' | wc -l | tr -d ' ')
[ "$removed" = "0" ] || { echo "DRAFT DELETED LIVE CONTENT ($removed removals — must be 0)"; exit 1; }

echo "VERIFICATION PASS"
```

If any check fails, fix and re-verify.

## Acceptance Gate

- [ ] `docs/drafts/hxx-task-template-DRAFT.md` exists.
- [ ] All pre-existing live sections preserved verbatim.
- [ ] New "Charter Reference (MANDATORY — fill all 4 fields)" section inserted between "## Objective" and "## Inputs".
- [ ] "Charter Cross-Reference" paragraph appended at end.
- [ ] `diff -u live draft` shows ZERO removed lines (only insertions).
- [ ] No file outside `docs/drafts/` modified.
- [ ] Final response enumerates every file created/modified.

## Closure

After verification PASS, write: `Draft ready for user review: docs/drafts/hxx-task-template-DRAFT.md`. Do NOT update sync doc; do NOT update next-slices.
