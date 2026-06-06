# H43 — Agent Workflow Repair Plan

## Problem

The H38-H42 strategy work reached the right final conclusion, but the workflow showed avoidable failure modes:

- Hermes ran long tasks without a reliable smoke-first contract.
- One H42 artifact initially claimed work had completed while the underlying JSON had no evaluated candidates.
- Review happened too late for some runtime errors.
- `py_compile` was treated as too strong a signal until direct behavior tests were added.
- Claude CLI could not be used from this environment because it was not logged in, but the workflow had no explicit fallback rule.

The fix is to turn the agent collaboration process into repo artifacts and validators, not a memory-based habit.

## Goals

1. Make future Hxx tasks bounded, reproducible, and reviewable.
2. Keep Codex/Hermes/Claude responsibilities explicit.
3. Require smoke runs before long runs.
4. Require independent artifact validation before any strategy conclusion.
5. Make review fallback explicit when Claude is unavailable.
6. Prevent live/config promotion unless the gate and review status both pass.

## Non-Goals

- Do not search for new trading strategies in H43.
- Do not change production trading configuration.
- Do not place live orders.
- Do not delete stray workspace files.
- Do not create GitHub issues automatically in H43.

## Workflow Contract

Every Hxx task must have these files or equivalents:

1. Task brief
   - Objective
   - Inputs
   - Outputs
   - Hard prohibitions
   - Acceptance gate
   - Smoke command
   - Full command
   - Verifier command

2. Smoke run
   - Must be small enough to run quickly.
   - Must exercise the same code path as the full run.
   - Must write to `/tmp` or a clearly disposable artifact unless the task is itself a fixture generator.

3. Full run
   - Must be bounded by explicit CLI limits, dates, or iteration counts.
   - Must flush progress.
   - Must write machine-readable JSON plus human-readable report.

4. Independent validation
   - Must inspect artifacts after the run.
   - Must check JSON/report consistency.
   - Must include behavior tests for new gates, loaders, and search logic.

5. Review
   - Claude is preferred for readonly review when available.
   - If Claude is unavailable, record the exact failure and use Hermes readonly review as fallback.
   - Any BLOCKER/HIGH/MEDIUM finding must be fixed or explicitly waived before the task is closed.

## Agent Roles

- Codex owns task design, final judgment, code edits, and validation.
- Hermes owns bounded dirty work: long searches, data backfills, report generation, and readonly fallback review.
- Claude owns readonly adversarial review when local login state allows it.

No agent may self-certify its own long-run output as complete without Codex running independent checks.

## Closure Criteria For H43

- Add repo workflow docs under `docs/agents/`.
- Add a reusable Hxx task template.
- Add a reviewer prompt template.
- Add a workflow validator script.
- Add tests or smoke checks for the validator.
- Update `docs/strategy-optimization-sync.md` with H43 closure.
- Run validation:
  - H42/H43 workflow validator
  - H42 and PIT tests
  - strict ledger validation
  - final readonly review

## Next Slices After H43

- H44: JSON/report consistency validator for all Hxx strategy artifacts.
- H45: strategy PRD for next alpha source instead of another parameter grid.
- H46: paper-only forward monitor for rejected-but-interesting candidates.
- H47: production-grade adjusted price rebuild with one consistent source.

