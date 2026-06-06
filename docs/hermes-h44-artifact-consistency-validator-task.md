# H44 — Hxx Artifact Consistency Validator

## Context

H42 exposed a workflow failure: an early artifact set claimed a search had completed, while the JSON showed no evaluated candidates. H43 added workflow guardrails, but artifact consistency should be validated by a dedicated script, not by manual inspection.

## Objective

Create a reusable validator for H39-H42 strategy research artifacts that compares run JSON with Markdown reports.

## Inputs

- `backtest/runs/fundamental_value_h39_unblock_search.json`
- `reports/h39_shadow_unblock_search_report.md`
- `backtest/runs/fundamental_value_h40_h39_candidate_audit.json`
- `reports/h40_h39_candidate_audit_report.md`
- `backtest/runs/fundamental_value_h41_candidate_robustness_sweep.json`
- `reports/h41_candidate_robustness_sweep_report.md`
- `backtest/runs/fundamental_value_h42_strategy_redesign_search.json`
- `reports/h42_strategy_redesign_search_report.md`

## Outputs

- `scripts/validate_hxx_artifacts.py`
- `tests/test_validate_hxx_artifacts.py`
- `docs/strategy-optimization-sync.md` update

## Hard Prohibitions

- Do not modify H39-H42 run JSON unless a mismatch is found and explicitly repaired.
- Do not rerun long strategy searches.
- Do not modify production trading config.
- Do not place live orders.

## Smoke Command

```bash
python scripts/validate_hxx_artifacts.py --artifact h42
```

Expected result:

- Exits 0.
- Validates H42 JSON/report consistency.

## Full Command

```bash
python scripts/validate_hxx_artifacts.py
```

Expected result:

- Exits 0.
- Validates H39-H42.
- Prints one PASS/FAIL line per artifact family.

## Verification

```bash
python scripts/validate_hxx_artifacts.py
pytest tests/test_validate_hxx_artifacts.py tests/test_validate_agent_workflow.py -q
python scripts/validate_ledger_consistency.py --strict
```

## Acceptance Gate

- [ ] H39 status and search counts match report.
- [ ] H40 status, robustness counts, and execution audit status match report.
- [ ] H41 candidate count and verdict match report.
- [ ] H42 verdict, run counts, clean-deploy count, and gate-pass count match report.
- [ ] Tests fail on an intentionally mismatched report fixture.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

