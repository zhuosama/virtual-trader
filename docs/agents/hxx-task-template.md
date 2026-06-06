# Hxx Task Template

## Context

Summarize the prior Hxx state and why this task exists.

## Objective

One sentence describing the exact outcome.

## Charter Reference (MANDATORY — fill all 4 fields)

Every Hxx brief must declare these four fields explicitly. A brief missing any of these is REJECTED at review.

- **Charter:** `docs/research-charter-v1.md` (or successor) — the active Research Charter under which this slice runs. If no Charter is active, the brief must not be dispatched.
- **Question (decision-grade):** One sentence. Must be answerable Y/N or with a concrete number against the Charter's threshold. Not "explore X" or "investigate Y."
- **Threshold (pass criterion):** Numerical, copied or referenced from the Charter. Example: `beat_HS300_windows ≥ 2/5 AND deploy_excess > 0 AND gate_pass_count ≥ 1`. State the exact value.
- **Budget:** Two numbers — `max_wall_hours` and `max_revisions` (re-dispatches after review). Default: `max_wall_hours = 4`, `max_revisions = 1`. Briefs exceeding either auto-pause for user decision.
- **kill_when:** One sentence stating the condition that ends this slice without promotion. Example: `kill_when = "all 4 sub-runs report gate_pass_count = 0 AND best beat_HS300_windows ≤ 1/5"`. Slices without a kill_when become open-ended (the H52 failure mode).

If a brief is a bug fix rather than a research claim, do NOT create a new Hxx number — open a normal commit referencing the affected Hxx in the commit message. See `docs/agents/workflow.md` § "Bug ≠ Slice."

## Inputs

- Input file or data source 1
- Input file or data source 2

## Outputs

- `scripts/...`
- `backtest/runs/...json`
- `reports/...md`
- docs/sync updates

## Hard Prohibitions

### Always Applicable (Standard Boilerplate — copy verbatim into every brief, do not delete)

These rules come from `AGENTS.md` § Hard Prohibitions (Always Applicable to All Agents). Restate them in every brief so the executing agent reads them with the brief, not just by reference.

- **No data fabrication**: do NOT add, modify, "complete", or "round up" rows in any protected artifact (anything under `data/cn_pit/`, any prior Hxx run JSON, any prior Hxx report). If a gap/NaN/missing row is encountered, SURFACE as a finding and STOP — do NOT silently patch. Original verdicts (e.g., `CANDIDATE_DATASET 99.91%`) are authoritative.
- **No source provenance forgery**: any data this task writes must have its `source_provider` / `source_url` fields reflect the ACTUAL source. If no real provenance exists, raise rather than fake it.
- **Symmetric restore**: any optional file modification (monkey-patch, runtime patch, backup) MUST use `try/finally`. Restore conditional on exit code is forbidden — leaks modifications on success paths.
- **Original ingestion verdicts immutable**: once a prior Hxx closes with a verdict + coverage numbers, that record is historical. A later Hxx cannot "fix" prior coverage by editing prior artifacts — flag a finding for a separate fix slice, or sideload supplemental data as a NEW artifact.
- **Exit-code is not acceptance**: do NOT declare success on `exit 0` alone. Every Acceptance Gate criterion must be physically verifiable (file exists, numerical assertion holds, sha256 matches). If any criterion fails, stop and surface.
- **Modification reporting**: final response MUST enumerate every file created or modified, calling out any protected files specifically.
- **No silent workarounds**: missing tokens, missing endpoints, schema mismatches → STOP and surface. Do not invent workarounds.
- **sha256 audit hooks**: any data-mutation or data-comparison brief must include sha256 audit hooks (pre + post). Audits raise hard on mismatch, never silent log.

Concrete incidents these prevent: H49a V1 (silent success without ran ingestion); H52h (silent fabrication of `689009.SS` in `sector_metadata_h52b_csi500.csv` with invented industry code `640000.SI`). See `docs/strategy-optimization-sync.md` for full incident records.

### Task-Specific Prohibitions

- Do not modify production configs unless explicitly required.
- Do not place live orders.
- Do not use network unless explicitly required.
- Do not overwrite canonical prior Hxx artifacts.
- (Add task-specific items here)

## Smoke Command

```bash
python scripts/task_script.py --small-limit --output-run /tmp/hxx_smoke.json --output-report /tmp/hxx_smoke.md
```

Expected smoke result:

- Exits 0
- Exercises the same code path as the full run
- Produces disposable artifacts

## Full Command

```bash
python scripts/task_script.py --bounded-limit N
```

Expected full result:

- Exits 0
- Writes machine-readable JSON
- Writes human-readable report
- Flushes progress

## Verification

```bash
python scripts/validate_agent_workflow.py
pytest relevant/tests -q
python scripts/validate_ledger_consistency.py --strict
```

## Acceptance Gate

- [ ] Gate condition 1
- [ ] Gate condition 2
- [ ] JSON/report consistency checked
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings

## Review Prompt

Use `docs/agents/review-prompt-template.md`.

## Closure Note

Record final verdict in `docs/strategy-optimization-sync.md`.

## Charter Cross-Reference

This template governs the SLICE level. Project-level question, frozen validation layer, total slice budget, and KILL protocol live in the active Research Charter (`docs/research-charter-v1.md` or successor). A slice cannot weaken or override the Charter; it can only refine the slice-level threshold within the Charter's bounds.
