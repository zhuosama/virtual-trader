# H46 Monitor Patch Task

## Objective
Update `scripts/h46_paper_forward_monitor.py` to include the best candidates from H49b and H50b into the daily paper forward observation list.

## Inputs
- `scripts/h46_paper_forward_monitor.py`
- `backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json`
- `backtest/runs/fundamental_value_h50b_quality_value_search.json`

## Outputs
- Modified `scripts/h46_paper_forward_monitor.py` (additive: 2 new RUN constants + 2 new factory functions + 1 line in `collect_candidates`).
- Modified `tests/test_h46_paper_forward_monitor.py` (additive: smoke tests for `candidate_from_h49b` and `candidate_from_h50b`).
- Regenerated `backtest/runs/fundamental_value_h46_paper_forward_monitor.json` (full-run side-effect; new candidates appended to tracked list; schema unchanged).
- Regenerated `reports/h46_paper_forward_monitor_report.md` (full-run side-effect).

## Hard Prohibitions
- Do NOT modify the underlying monitor scoring / evaluation logic (`PaperCandidate` dataclass, daily-metric computation, ledger interactions). This is a candidate-list extension only.
- Do NOT modify `candidate_from_h39` or `candidate_from_h42` (existing loaders stay verbatim).
- Do NOT modify `scripts/validate_hxx_artifacts.py` source.
- Do NOT modify `backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json` or `fundamental_value_h50b_quality_value_search.json` (read-only sources for the new loaders).
- Do not author commits as `codex` or `claude-code`.
- Do not loosen `tests/test_validate_hxx_artifacts.py`.

## Execution Instructions
1. Add `H49B_RUN` and `H50B_RUN` constants pointing to their JSON artifacts (style identical to `H39_RUN` / `H42_RUN`).
2. Implement `candidate_from_h49b()` and `candidate_from_h50b()` loader functions analogous to existing H39/H42 loaders.
   - For H49b, grab the best candidate from `top_candidates_multi_window` (already ranked by D6: beat_HS300 desc + deploy_excess desc).
   - For H50b, same — grab `top_candidates_multi_window[0]`.
   - Each `PaperCandidate` MUST carry a `registered_at: "2026-05-23"` field marking the forward-monitor start date. H39/H42 candidates keep their existing registration dates; the H46 report must distinguish "historical paper data" (H39/H42) from "forward-only paper data" (H49b/H50b) so the reader doesn't compare a 3-week-old paper history to a same-day-registered one. If `PaperCandidate` dataclass does not yet have a `registered_at` field, add it with default = original H46 epoch for backward-compat; do NOT change other fields.
3. Update `collect_candidates()` to append these two new candidates to the returned list.
4. The monitor must cleanly evaluate all 4+ candidates when run, raising no exceptions.

## Smoke Command
```bash
python scripts/h46_paper_forward_monitor.py --candidates-only --output-run /tmp/h46_smoke.json --output-report /tmp/h46_smoke.md
```
(Or equivalent dry-run flag if available; if none exists, run the full path against `/tmp/` outputs without overwriting the canonical `backtest/runs/` and `reports/` paths.)

Expected smoke result:
- Exits 0.
- Confirms `candidate_from_h49b()` and `candidate_from_h50b()` return valid `PaperCandidate` objects with `registered_at="2026-05-23"`.
- Does NOT touch `backtest/runs/fundamental_value_h46_paper_forward_monitor.json` or `reports/h46_paper_forward_monitor_report.md`.

## Full Command
```bash
python scripts/h46_paper_forward_monitor.py
```
Regenerates the canonical JSON + report with 4 tracked candidates.

## Verification
```bash
python scripts/validate_hxx_artifacts.py --artifact h46
python scripts/validate_hxx_artifacts.py            # all 11 artifacts still pass
pytest tests/test_h46_paper_forward_monitor.py tests/test_validate_hxx_artifacts.py -q
git status --short backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json backtest/runs/fundamental_value_h50b_quality_value_search.json
```
- `validate_h46` (existing) must still pass after JSON regenerates with the new candidates.
- Full validator suite (11/11) must stay green.
- The two H49b/H50b source JSONs must show as untouched (only h46 outputs may change).

## Closure Note
Append a one-line entry to `docs/strategy-optimization-sync.md` under the existing H46 section: "H46 monitor extended to track H49b + H50b best candidates registered 2026-05-23." No change to `docs/agents/next-slices.md` (H46 was already DONE).
