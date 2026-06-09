# Virtual Trader Claude Instructions

This repo uses the same workflow as `AGENTS.md`. Keep this file intentionally thin so Claude and other agents do not drift.

## Agent skills

### Issue tracker

Work is tracked in-repo through Hxx task documents and `docs/agents/next-slices.md`; GitHub issues may be added later. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-role triage vocabulary for future GitHub or local issues. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo. Domain and workflow docs live under `docs/`, with agent workflow rules in `docs/agents/`. See `docs/agents/domain.md`.

## Workflow

Before dispatching long-running agent work, read `docs/agents/workflow.md` and use `docs/agents/hxx-task-template.md`.

Claude's default role in this repo is read-only adversarial review. Do not edit files during review tasks unless the user explicitly asks you to implement fixes.

Do not promote trading strategies, modify production trading config, or place live orders unless a task brief explicitly permits it and all promotion gates pass.

