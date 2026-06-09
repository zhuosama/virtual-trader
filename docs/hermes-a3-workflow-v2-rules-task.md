# Hermes Task — A3: Add Spike, Hermes-Scope, and Bug-Not-Slice Rules to workflow.md

## Context

The current `docs/agents/workflow.md` describes the Hxx lifecycle but does not encode three rules whose absence drove the H49–H52 failure (~3 weeks, 0 deployable strategies, 3 Hermes Hard-Prohibition incidents):

1. **Spike → Hxx**: new ideas must be hand-spiked (≤2h, no brief, no Hermes) and only signal-positive spikes are promoted to Hxx.
2. **Hermes scope = bulk I/O only**: Hermes must NOT author new strategy scripts, monkey-patch, run acceptance gates, or self-verify verdicts. Past 3 incidents (H49a silent success, H52c date format, H52h fabrication) all came from violating this.
3. **Bug ≠ Slice**: bugfixes are normal commits referencing the affected Hxx; they do NOT receive a new Hxx number. H52a→b→c→d→e→f→g→h was 8 slices when only 2–3 were research claims.

## Objective

Produce ONE file: `docs/drafts/workflow-DRAFT.md`. The file is a COPY of the current `docs/agents/workflow.md` with three new sections appended (in the order listed below) and one small edit to the existing Hermes role bullet list. Do NOT modify the live `docs/agents/workflow.md`.

## Inputs (read-only — do not modify)

- `docs/agents/workflow.md` (current live workflow — base for the draft)
- `docs/strategy-optimization-sync.md` (§ "H52 Universe-Expansion Line — KILLED" for incident vocabulary; § H42–H52h verdicts for evidence of the failure mode)
- `AGENTS.md` (§ "Hard Prohibitions (Always Applicable to All Agents)" and § "Incident History" — must align with the rules added here)
- This brief

## Output

Single file: `docs/drafts/workflow-DRAFT.md`

### Required edit 1 — extend the Hermes role bullet list

In the existing `### Hermes` subsection of the draft, ADD these bullets after the existing bullets (preserve all existing bullets verbatim). Place them BEFORE the "**Must obey `AGENTS.md`...**" bullet so the new scope rules are immediately visible:

```markdown
- **Scope is BULK I/O ONLY** — Hermes may run data fetch / pagination / rate-limited transform jobs against pre-existing, reviewed scripts. Hermes may also draft Markdown documents under `docs/drafts/`. Hermes may NOT author new strategy scripts, may NOT monkey-patch any path or function, may NOT execute backtests or acceptance gates, may NOT self-issue a research verdict. Past incidents H49a (silent success), H52c (date format bug surfaced 4 slices late), and H52h (silent fabrication of sector row) all came from violating this scope. See `docs/strategy-optimization-sync.md` § "H52 Universe-Expansion Line — KILLED" for details.
- **Must not self-verify acceptance gates** — Hermes can run a verification command, but cannot decide PASS/FAIL. The decision goes back to the user or Claude. If a verification command exits 0 but the underlying numerical assertion is unmet, surface and STOP.
```

### Required new sections — append at the end of the draft (after all existing content)

#### Section 1: Spike → Hxx

```markdown
## Spike → Hxx Two-Stage Rule

New research ideas do NOT start as Hxx briefs. They start as **spikes**:

- **Definition:** A spike is a single human-driven (Claude Code or Codex, NOT Hermes) experiment, ≤2 wall hours, no brief, no review cycle, no dispatch. Output is a single Markdown file under `docs/spikes/YYYY-MM-DD-<slug>.md` with: question, decision threshold, answer (Y/N or a number), and the evidence inline.
- **Promotion rule:** A spike is promoted to an Hxx slice ONLY IF it shows positive signal against the Charter's threshold (or a Charter-approved diagnostic threshold). A negative spike is closed in place — it does NOT become a slice, it does NOT spawn follow-ups, it does NOT get an Hxx number. The spike file itself is the postmortem.
- **Re-running an answered question is forbidden.** Before starting a spike, search `docs/spikes/` and `docs/strategy-optimization-sync.md` for the same question. If H48-style answer already exists, cite it and stop.
- **What's not a spike:** anything that requires editing `scripts/`, `data/`, `backtest/`, or `tests/` is not a spike — it's a slice. Spikes can only READ artifacts and call EXISTING entry points.
```

#### Section 2: Bug ≠ Slice

```markdown
## Bug ≠ Slice Rule

A bug fix is NOT a research slice. Bug fixes:

- Get a normal commit (or PR), with a title prefixed by `fix(hXX): ...` where `hXX` is the affected Hxx number.
- Do NOT receive a new Hxx number.
- Do NOT appear under `## H...` in `docs/agents/next-slices.md`.
- Do NOT generate a `reports/h...md` artifact (the fix itself is in the commit; if a coverage/sha256 number changes, update the existing affected Hxx's section in `docs/strategy-optimization-sync.md` with a brief inline note).

A research slice IS an Hxx if and only if it makes a NEW research claim (new signal, new universe, new gate variant, new costs model, etc.). Restating a previous claim with a fixed denominator is a bug fix, not a slice.

This rule prevents the H52a→H52h slice proliferation, where 5 of 8 slices were bug fixes promoted into research-claim slots.
```

#### Section 3: Engine ≠ Strategy

```markdown
## Engine ≠ Strategy PR Rule

A single PR / commit must NOT simultaneously change the engine (or loader, or data ingest pipeline) AND make a strategy claim. If a slice discovers that the engine needs a change:

1. STOP the slice.
2. Open an engine-only PR with: the change, unit tests, and a note about which slice needed it. No strategy claim, no `reports/h...md`.
3. After the engine PR is merged AND the `engine-frozen-vN` git tag is bumped, RESUME the slice as a strategy-only PR that uses the new engine version.

Mixing the two makes it impossible to attribute outcome changes to data, engine, or signal. Past H49–H52 work mixed these freely; this is part of why no clean verdict emerged.
```

### What to preserve from the live workflow

EVERY OTHER SECTION of `docs/agents/workflow.md` MUST be copied into the draft VERBATIM — including "Roles" (Codex, Claude, and Hermes — except for the new bullets added per edit 1), "Required Hxx Lifecycle", "Promotion Rule", and any other existing section. Do NOT rewrite, reorder, abbreviate, or "improve" any pre-existing section.

## Hard Prohibitions

### Always Applicable (from AGENTS.md § Hard Prohibitions)

- **No data fabrication.** If a referenced incident ID, section name, or path is not findable in the inputs, write `TBD — user to verify` rather than guess.
- **No source provenance forgery.** Not applicable.
- **Symmetric restore.** Not applicable.
- **Original ingestion verdicts immutable.** Not applicable.
- **Exit-code is not acceptance.** Task complete only when draft file exists, contains 3 new sections + 2 new Hermes bullets, and `diff -u live draft` shows only insertions (zero removed lines).
- **Modification reporting.** Final response MUST enumerate every file created/modified.
- **No silent workarounds.** If the live workflow is missing, STOP and surface.

### Task-Specific Prohibitions

- **DRAFT MODE ONLY.** Do NOT modify `docs/agents/workflow.md`, do NOT modify any other file under `docs/agents/`, do NOT modify `AGENTS.md`, do NOT modify any Hxx brief, do NOT modify anything under `scripts/`, `data/`, `backtest/`, `reports/`, `tests/`, project root.
- ALL output goes to `docs/drafts/workflow-DRAFT.md`. Overwrite if exists; create `docs/drafts/` if missing.
- Do NOT add additional sections beyond the three specified. Do NOT modify pre-existing prohibitions or promotion rules — the Charter governs those.
- Do NOT execute scripts. Do NOT make network calls.

## Smoke / Full Command

N/A — doc-editing task.

## Verification

Hermes runs these BEFORE declaring complete:

```bash
test -f docs/drafts/workflow-DRAFT.md || { echo "MISSING DRAFT"; exit 1; }

# Live sections preserved verbatim
for live_marker in "## Roles" "### Codex" "### Hermes" "### Claude" "## Required Hxx Lifecycle" "## Promotion Rule"; do
  grep -q "$live_marker" docs/drafts/workflow-DRAFT.md || { echo "MISSING LIVE SECTION: $live_marker"; exit 1; }
done

# 3 new sections present
for new_marker in "## Spike → Hxx Two-Stage Rule" "## Bug ≠ Slice Rule" "## Engine ≠ Strategy PR Rule"; do
  grep -q "$new_marker" docs/drafts/workflow-DRAFT.md || { echo "MISSING NEW SECTION: $new_marker"; exit 1; }
done

# New Hermes bullets present
grep -q "Scope is BULK I/O ONLY" docs/drafts/workflow-DRAFT.md || { echo "MISSING HERMES BULK I/O BULLET"; exit 1; }
grep -q "Must not self-verify acceptance gates" docs/drafts/workflow-DRAFT.md || { echo "MISSING HERMES NO-SELF-VERIFY BULLET"; exit 1; }

# Diff must show only insertions
removed=$(diff -u docs/agents/workflow.md docs/drafts/workflow-DRAFT.md | grep -E '^-[^-]' | wc -l | tr -d ' ')
[ "$removed" = "0" ] || { echo "DRAFT DELETED LIVE CONTENT ($removed removals — must be 0)"; exit 1; }

echo "VERIFICATION PASS"
```

If any check fails, fix and re-verify.

## Acceptance Gate

- [ ] `docs/drafts/workflow-DRAFT.md` exists.
- [ ] All pre-existing live sections preserved verbatim.
- [ ] Two new Hermes bullets inserted into existing `### Hermes` subsection (before the "Must obey AGENTS.md" bullet).
- [ ] Three new sections appended at end: "Spike → Hxx Two-Stage Rule", "Bug ≠ Slice Rule", "Engine ≠ Strategy PR Rule".
- [ ] `diff -u live draft` shows ZERO removed lines.
- [ ] No file outside `docs/drafts/` modified.
- [ ] Final response enumerates every file created/modified.

## Closure

After verification PASS, write: `Draft ready for user review: docs/drafts/workflow-DRAFT.md`. Do NOT update sync doc; do NOT update next-slices.
