# Virtual Trader Agent Instructions

This repo uses a shared agent workflow documented under `docs/agents/`.

## Agent skills

### Issue tracker

Work is tracked in-repo through Hxx task documents and `docs/agents/next-slices.md`; GitHub issues may be added later. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-role triage vocabulary for future GitHub or local issues. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo. Domain and workflow docs live under `docs/`, with agent workflow rules in `docs/agents/`. See `docs/agents/domain.md`.

## Workflow

Before dispatching long-running agent work, read `docs/agents/workflow.md` and use `docs/agents/hxx-task-template.md`.

Do not promote trading strategies, modify production trading config, or place live orders unless a task brief explicitly permits it and all promotion gates pass.

## Hard Prohibitions (Always Applicable to All Agents)

These rules apply to every agent in every task in this repo (Hermes, Claude, Codex, any future agent). Briefs can ADD more prohibitions, never weaken these. Violating any of these is treated as a contract violation regardless of intent or "improvement" rationale.

### Data Discipline

- **No data fabrication.** Do NOT add, modify, "complete", "round up", or otherwise invent rows in any protected artifact (anything under `data/cn_pit/`, any prior Hxx run JSON, any prior Hxx report, any input passed to a brief). If a gap, NaN, or missing row is encountered during execution, SURFACE it as a finding in the report and STOP — do NOT silently patch. Original verdicts (e.g., `CANDIDATE_DATASET 99.91%`) are authoritative; do NOT "improve" coverage by inventing entries.
- **No source provenance forgery.** If a script writes/adds data, the `source_provider` / `provider` / `source_url` fields must reflect the ACTUAL source. Do not write `"tushare:..."` when the data did not come from Tushare. If no real provenance exists, raise an error rather than write a fake.
- **Symmetric restore.** Any optional file modification (e.g., runtime patches, monkey-patches, file backups) MUST use `try/finally`. Conditional restore based on exit code (e.g., "restore only if exit != 0") is forbidden — it leaks modifications on success paths.
- **Original ingestion verdicts immutable.** Once an Hxx ingestion closes with a verdict, that verdict + its coverage numbers are historical record. A later Hxx CANNOT "fix" the prior coverage by editing the prior artifact; the only legitimate paths are (a) flag a finding for a separate fix slice, or (b) sideload supplemental data as a NEW artifact with its own provenance.

### Success Honesty

- **Exit-code is not acceptance.** Do NOT declare success based on `exit 0` alone. Every Acceptance Gate criterion in the brief must be physically verifiable: file exists, numerical assertion holds, sha256 matches expected. If any criterion is not met, surface the failure mode explicitly and STOP — do not paper over with self-justification.
- **Modification reporting.** Final response of any task MUST enumerate every file the task created or modified. Touching a protected file (per Data Discipline above) without explicit brief authorization is a BLOCKER, regardless of intent.
- **No silent workarounds.** If you encounter an obstacle (missing token, missing endpoint, schema mismatch, etc.) that prevents completing the brief as written, STOP and surface the obstacle. Do not silently invent a workaround that satisfies the letter but not the spirit of the contract.

### Audit Discipline

- **sha256 audit hooks.** Every brief that mutates or compares data artifacts must include sha256 audit hooks (pre-run + post-run). The audit must raise hard on mismatch, not silently log.
- **Provenance writes through.** When wrapping/monkey-patching a downstream script's data sources, the resulting run JSON's `data_sources` block must reflect the ACTUAL files loaded (via sha256). If you patch a path but the downstream script writes a stale sha256, the patch did not work — surface as BLOCKER.

### Incident History (concrete examples of what these rules prevent)

- **H49a V1 (2026-05-23, Hermes "silent success"):** Hermes wrote script + tests + validator but never ran the ingestion (Tushare token missing); declared `exit 0` without surfacing the missing prerequisite. Fixed by user-supplied token + re-dispatch.
- **H52h (2026-05-25, Hermes "silent fabrication"):** Hermes silently modified the protected `sector_metadata_h52b_csi500.csv` to add a row for ticker `689009.SS` with a fabricated SW L1 industry code `640000.SI` (real codes are `801XXX.SI`), falsely tagging source as Tushare. Restore-on-failure logic was asymmetric (only triggered on exit≠0). User reverted both the CSV and the corresponding coverage JSON.

Both incidents recorded in `docs/strategy-optimization-sync.md` under their respective Hxx sections. Future briefs MUST embed the Always Applicable boilerplate (see `docs/agents/hxx-task-template.md`) to keep these rules in front of the executing agent.

