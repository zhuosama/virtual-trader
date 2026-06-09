# H46 - Paper-Only Forward Monitor

## Context

H39-H42 found candidates that can improve deploy-window behavior, but none passed temporal and HS300-relative robustness gates. H45 concluded that further work should not be another parameter-only tuning run. H46 keeps the interesting candidates visible in a paper-only monitor while preserving the research-only boundary.

Public WorldQuant BRAIN materials are useful as a design reference: alphas are simulated from historical market data and predefined operators; data categories include price/volume, fundamentals, analyst, sentiment, options, model, insider transactions, and short interest; examples emphasize delay, neutralization, decay, universe coverage, and per-stock capital limits. H46 borrows those ideas as monitor metadata only and does not use WorldQuant private data.

## Objective

Create a paper-only monitor artifact for H39/H42 candidates that tracks HS300 excess return, drawdown, trade count, losing streak, gate status, and WorldQuant-inspired data readiness gaps.

## Inputs

- `backtest/runs/fundamental_value_h39_unblock_search.json`
- `backtest/runs/fundamental_value_h42_strategy_redesign_search.json`
- `docs/hermes-h45-next-alpha-prd.md`

## Outputs

- `scripts/h46_paper_forward_monitor.py`
- `backtest/runs/fundamental_value_h46_paper_forward_monitor.json`
- `reports/h46_paper_forward_monitor_report.md`
- `tests/test_h46_paper_forward_monitor.py`
- `scripts/validate_hxx_artifacts.py` H46 registration
- docs/sync updates

## Hard Prohibitions

- Do not place live orders.
- Do not modify production trading config.
- Do not write value-account positions or trades.
- Do not rewrite H39-H42 artifacts.
- Do not use WorldQuant private/proprietary data.

## Smoke Command

```bash
python scripts/h46_paper_forward_monitor.py --top-n 2 --output-run /tmp/h46_smoke.json --output-report /tmp/h46_smoke.md
```

Expected smoke result:

- Exits 0.
- Writes disposable JSON and Markdown.
- Labels every candidate `RESEARCH_ONLY`.

## Full Command

```bash
python scripts/h46_paper_forward_monitor.py
```

Expected full result:

- Exits 0.
- Writes machine-readable JSON.
- Writes human-readable report.
- Includes H39 best clean candidate and H42 top candidates.

## Verification

```bash
python scripts/h46_paper_forward_monitor.py --top-n 2 --output-run /tmp/h46_smoke.json --output-report /tmp/h46_smoke.md
python scripts/h46_paper_forward_monitor.py
python scripts/validate_hxx_artifacts.py --artifact h46
pytest tests/test_h46_paper_forward_monitor.py tests/test_validate_hxx_artifacts.py tests/test_validate_agent_workflow.py -q
python scripts/validate_ledger_consistency.py --strict
```

## Acceptance Gate

- [ ] Writes daily/weekly paper metrics.
- [ ] Tracks HS300 excess return, drawdown, trade count, losing streak, and gate status.
- [ ] Labels output as research-only.
- [ ] Registers H46 in `scripts/validate_hxx_artifacts.py`.
- [ ] Does not modify production config or value-account state.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

