# Hermes Virtual Trader Architecture Review Draft

Date: 2026-05-16
Status: Draft for cross-review

## Context

This draft captures the initial architecture observations before external cross-review. Hermes virtual-trader now has a hardened ledger consistency layer after the 2026-05-15 incident:

- `scripts/validate_ledger_consistency.py`
- `tests/audit_layer/test_ledger_consistency.py`
- `tests/audit_layer/test_backfill_regression.py`
- `docs/data-backfill-runbook.md`
- `docs/hardening-plan-2026-05-15.md`

The remaining architecture question is not another narrow data repair. It is how to make the full system harder to silently degrade across trading workflow, risk action execution, strategy iteration, public export, website display, and local admin operations.

## Initial Top Risks

1. Ledger source of truth is still diffuse.
   `accounts/*.json`, `trades/`, `strategies/performance_history.json`, `reports/`, and `agents/workflows/` are now cross-validated, but they still behave like multiple synchronized fact stores. Long term, `trades` should become or feed an append-only event ledger, while accounts, performance history, reports, charts, and public snapshots become derived artifacts.

2. Risk actions lack a lifecycle.
   `risk_controller` can produce reduce-only actions, and `coordinator` can surface them as workflow warnings, but there is no first-class action queue with statuses such as `proposed`, `approved`, `executed`, `settled`, `reviewed`, and `closed`.

3. Health is still mostly technical, not business-level.
   `/health` reports the console service as online. The system also needs business health: latest successful settlement, latest validator result, workflow freshness, degraded/failed workflow count, audit provider health, and public snapshot freshness.

4. Public export should be gated by the validated ledger.
   `console/export_adapter.py` sanitizes sensitive fields, but public export should explicitly require a passing ledger validator run and include the validator result/hash in the manifest.

5. Local admin write operations lack an action log.
   Console POST operations such as backtest runs, snapshot writes, and site imports should append to an `admin_actions` log with timestamp, operation, request summary, changed files, and outcome.

6. Localhost is not a full permission model.
   The console binds to `127.0.0.1`, but write operations should still use a nonce or local confirmation token to reduce accidental or cross-origin misuse.

7. Site import is not fully transactional.
   `site_bridge_adapter` backs up existing files and scans after import, but if post-scan fails it does not automatically revert. Import should write to temporary files, scan, then atomically replace; failure should restore the previous snapshot.

8. LLM provider health and fallback policy are scattered.
   Multiple agents initialize LLM clients independently. The system needs a single provider health surface so workflows can distinguish `LLM unavailable`, `provider degraded`, and `policy blocked`.

9. Strategy audit lacks post-merge outcome review.
   The audit layer gates strategy changes before merge, but T+30 actual-effect review remains a follow-up. The system should learn whether approved changes actually worked.

10. Website display should show trust state, not just performance.
    The public site should surface data freshness, validator status, backfill markers, latest audit decision, and recent workflow degraded/failed state.

## Recommended Two-Week Plan

1. Add an `actions/` queue for risk reduce-only actions.
   Start with a JSON schema and file-backed queue. Do not build a full UI first. The immediate goal is to prevent risk actions from disappearing inside workflow warnings.

2. Add business health.
   Implement a small health summary that includes ledger validator status, last settlement date, latest workflow status, audit health, action queue counts, and public snapshot freshness.

3. Gate public export.
   Require `validate_ledger_consistency.py --strict` before `write_public_snapshot()` can write. Include validator metadata in `public-export/manifest.json`.

4. Add `admin_actions` logging for console writes.
   Log backtest run requests, snapshot writes, site imports, and future action approvals.

## One-to-Two-Month Direction

- Move from multi-file synchronized state toward append-only ledger events plus derived artifacts.
- Introduce a first-class action lifecycle for risk actions, backfill jobs, audit retries, and export jobs.
- Add T+30 strategy effect evaluation and write `actual_effect` back to audit logs.
- Make the public site consume only validated, versioned snapshots.
- Add an operator dashboard for business health rather than only raw data browsing.

## Avoid For Now

- Do not introduce a database until JSONL/event files hit real limits.
- Do not add more autonomous agents before clarifying state ownership.
- Do not build a heavy permission system before local nonce + admin log exists.
- Do not make the public website read live runtime files.
- Do not overbuild a full workflow orchestrator before basic action queue and health metrics exist.

## Questions For Cross-Review

1. Should `trades/*.json` evolve directly into the event ledger, or should a new `ledger/events/*.jsonl` be introduced while keeping `trades/` as a derived compatibility artifact?
2. Should risk reduce-only actions require human approval, automatic approval under thresholds, or both depending on severity?
3. Should public export be blocked by any workflow degraded state, or only by ledger validator failure?
4. What is the smallest admin action log that materially improves operability without building a full backend?
