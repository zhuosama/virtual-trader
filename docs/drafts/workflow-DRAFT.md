# Agent Workflow

## Roles

### Codex

Codex owns planning, edits, validation, and final judgment.

- Writes or approves each Hxx task brief.
- Performs local code edits.
- Runs independent artifact checks.
- Decides whether a candidate can move forward.
- Keeps `docs/strategy-optimization-sync.md` current.

### Hermes

Hermes is used for bounded dirty work.

- Runs data collection, search grids, report generation, and long read-only audits.
- Must receive a concrete task brief and explicit output paths.
- Must run smoke commands before long commands when the task creates code.
- Must not modify production trading config unless the task brief explicitly says so.
- Must not place live orders.
- **Scope is BULK I/O ONLY** — Hermes may run data fetch / pagination / rate-limited transform jobs against pre-existing, reviewed scripts. Hermes may also draft Markdown documents under `docs/drafts/`. Hermes may NOT author new strategy scripts, may NOT monkey-patch any path or function, may NOT execute backtests or acceptance gates, may NOT self-issue a research verdict. Past incidents H49a (silent success), H52c (date format bug surfaced 4 slices late), and H52h (silent fabrication of sector row) all came from violating this scope. See `docs/strategy-optimization-sync.md` § "H52 Universe-Expansion Line — KILLED" for details.
- **Must not self-verify acceptance gates** — Hermes can run a verification command, but cannot decide PASS/FAIL. The decision goes back to the user or Claude. If a verification command exits 0 but the underlying numerical assertion is unmet, surface and STOP.
- **Must obey `AGENTS.md` § Hard Prohibitions (Always Applicable to All Agents)** — these rules apply to every Hermes invocation, in addition to brief-specific prohibitions. Briefs add prohibitions; they never weaken the global set.
- **Must not silently fabricate, complete, or "round up" data** in protected artifacts. If a data gap is encountered, surface as a finding and stop; do NOT patch silently. (See AGENTS.md for full rule + incident history.)
- **Must not declare `exit 0` as success** when the brief's Acceptance Gate criteria are not physically verifiable (file presence + numerical assertions + sha256 match). Surface the failure mode and stop.
- **Restore mechanisms must be symmetric** (`try/finally`), never conditional on exit code. A conditional restore leaks modifications on success paths.

### Claude

Claude is preferred for adversarial read-only review when available.

- Reviews code, artifacts, and gate logic.
- Reports findings as BLOCKER/HIGH/MEDIUM/LOW/NIT.
- Does not edit files during review.

If Claude CLI is unavailable, record the exact failure and use Hermes read-only review as fallback.

## Required Hxx Lifecycle

1. Plan
   - Create a task brief or plan under `docs/`.
   - Define inputs, outputs, hard prohibitions, smoke command, full command, and verifier.

2. Smoke
   - Run the smallest command that exercises the real code path.
   - Prefer `/tmp` outputs for smoke artifacts.

3. Implement
   - Keep edits scoped to the task.
   - Add behavior tests for new gate/search/loader/accounting logic.

4. Full Run
   - Use bounded parameters.
   - Flush progress.
   - Produce JSON plus Markdown report.

5. Validate
   - Run artifact consistency checks.
   - Run relevant tests.
   - Run strict ledger validation if trading/accounting artifacts changed.

6. Review
   - Claude read-only review if available.
   - Hermes read-only fallback if Claude is blocked.
   - Fix or document every BLOCKER/HIGH/MEDIUM item.

7. Close
   - Update sync docs.
   - State final verdict: deployable, paper-only, research-only, or rejected.

## Promotion Rule

A strategy cannot be promoted to live/config changes unless all are true:

- Data-quality gate passes.
- Execution gate passes.
- Multi-window robustness gate passes.
- JSON/report artifacts are consistent.
- Tests pass.
- Read-only review has no unresolved BLOCKER/HIGH/MEDIUM findings.

## Spike → Hxx Two-Stage Rule

New research ideas do NOT start as Hxx briefs. They start as **spikes**:

- **Definition:** A spike is a single human-driven (Claude Code or Codex, NOT Hermes) experiment, ≤2 wall hours, no brief, no review cycle, no dispatch. Output is a single Markdown file under `docs/spikes/YYYY-MM-DD-<slug>.md` with: question, decision threshold, answer (Y/N or a number), and the evidence inline.
- **Promotion rule:** A spike is promoted to an Hxx slice ONLY IF it shows positive signal against the Charter's threshold (or a Charter-approved diagnostic threshold). A negative spike is closed in place — it does NOT become a slice, it does NOT spawn follow-ups, it does NOT get an Hxx number. The spike file itself is the postmortem.
- **Re-running an answered question is forbidden.** Before starting a spike, search `docs/spikes/` and `docs/strategy-optimization-sync.md` for the same question. If H48-style answer already exists, cite it and stop.
- **What's not a spike:** anything that requires editing `scripts/`, `data/`, `backtest/`, or `tests/` is not a spike — it's a slice. Spikes can only READ artifacts and call EXISTING entry points.

## Bug ≠ Slice Rule

A bug fix is NOT a research slice. Bug fixes:

- Get a normal commit (or PR), with a title prefixed by `fix(hXX): ...` where `hXX` is the affected Hxx number.
- Do NOT receive a new Hxx number.
- Do NOT appear under `## H...` in `docs/agents/next-slices.md`.
- Do NOT generate a `reports/h...md` artifact (the fix itself is in the commit; if a coverage/sha256 number changes, update the existing affected Hxx's section in `docs/strategy-optimization-sync.md` with a brief inline note).

A research slice IS an Hxx if and only if it makes a NEW research claim (new signal, new universe, new gate variant, new costs model, etc.). Restating a previous claim with a fixed denominator is a bug fix, not a slice.

This rule prevents the H52a→H52h slice proliferation, where 5 of 8 slices were bug fixes promoted into research-claim slots.

## Engine ≠ Strategy PR Rule

A single PR / commit must NOT simultaneously change the engine (or loader, or data ingest pipeline) AND make a strategy claim. If a slice discovers that the engine needs a change:

1. STOP the slice.
2. Open an engine-only PR with: the change, unit tests, and a note about which slice needed it. No strategy claim, no `reports/h...md`.
3. After the engine PR is merged AND the `engine-frozen-vN` git tag is bumped, RESUME the slice as a strategy-only PR that uses the new engine version.

Mixing the two makes it impossible to attribute outcome changes to data, engine, or signal. Past H49–H52 work mixed these freely; this is part of why no clean verdict emerged.
