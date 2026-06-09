# H48 — Unified-QFQ H42 Strategy Rerun

## Context

H47 produced a production-candidate price matrix built from a single adjustment source: Tushare `pro_bar(adj="qfq")` for stocks plus Tushare `index_daily` for the HS300 benchmark. The matrix lives at `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` and was registered as CANDIDATE_DATASET in `reports/h47_tushare_qfq_price_rebuild_report.md`. H47 did not by itself decide whether any prior strategy candidate is deployable.

H42 (`reports/h42_strategy_redesign_search_report.md`, 2026-05-22) ran a multi-family search on the H38 research price matrix and ended at `RESEARCH_ONLY` — 0/X candidates cleared the multi-window acceptance gate. H42 was bound to `prices_h38_candidate.csv`, which mixed yfinance-adjusted stock prices with a narrow Tushare benchmark patch.

H48 asks one question: under a uniformly adjusted Tushare qfq price source (H47), do any H42 candidates flip from RESEARCH_ONLY to passing the gate? The expected and acceptable outcome is still RESEARCH_ONLY — H48 is a price-source sensitivity check, not a tuning pass.

## Objective

Rerun the H42 redesign search verbatim, swapping only the price file to the H47 unified-qfq matrix, and compare the verdict against the H42 baseline.

## Inputs

- `scripts/h42_strategy_redesign_search.py` (reused via wrapper)
- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv`
- `data/cn_pit/universe_h30_candidate.jsonl`
- `data/cn_pit/universe_snapshots_h30_candidate.jsonl`
- `backtest/runs/fundamental_value_h42_strategy_redesign_search.json` (baseline for comparison)
- `reports/h42_strategy_redesign_search_report.md` (baseline for comparison)

## Outputs

- `scripts/h48_unified_qfq_h42_rerun.py` — thin wrapper that invokes the H42 search with H47 prices and H48 output paths
- `backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json`
- `reports/h48_unified_qfq_h42_rerun_report.md`
- `tests/test_h48_unified_qfq_h42_rerun.py` — at minimum, asserts the wrapper resolves to H47 prices and H48 output paths and runs an `--stage-b-limit 1 --top-k 1` smoke without error
- `scripts/validate_hxx_artifacts.py` — register `h48` artifact family with a `validate_h48` checker that mirrors `validate_h42` and additionally enforces the H47 price-source provenance field (see below)
- One H48 row appended to `docs/strategy-optimization-sync.md`
- H48 entry status updated to `DONE` in `docs/agents/next-slices.md` at closure

## Hard Prohibitions

- Do not overwrite `backtest/runs/fundamental_value_h42_strategy_redesign_search.json`.
- Do not overwrite `reports/h42_strategy_redesign_search_report.md`.
- Do not overwrite `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` or any H38–H47 input artifact.
- Do not modify `scripts/h42_strategy_redesign_search.py` search logic, gate thresholds, window definitions, or overlay families. The wrapper composes; it does not edit. If the wrapper needs values the script does not expose via CLI, add a single new CLI flag and document it; do not inline-modify search behavior.
- Do not modify production trading config.
- Do not place live orders.
- No network: do not refetch prices, do not call Tushare/yfinance.
- Do not author commits as `codex` or `claude-code`.

## Provenance Requirements (must appear in H48 JSON)

The H48 wrapper must inject a `price_source` object into the run JSON before writing, with at least:

```json
{
  "price_source": {
    "task": "h47",
    "file": "data/cn_pit/prices_h47_tushare_qfq_candidate.csv",
    "sha256": "<recomputed at run time>",
    "provider": "tushare:pro_bar:qfq",
    "benchmark_provider": "tushare:index_daily",
    "rows": 1544,
    "ticker_columns": 481
  }
}
```

`validate_h48` must assert `price_source.task == "h47"` and `price_source.file` ends with `prices_h47_tushare_qfq_candidate.csv`. This is the audit hook that proves the rerun used H47 prices and not H38.

## Smoke Command

```bash
python scripts/h48_unified_qfq_h42_rerun.py \
  --stage-a-limit 2 \
  --stage-b-limit 2 \
  --top-k 1 \
  --output-run /tmp/h48_smoke.json \
  --output-report /tmp/h48_smoke.md
```

Expected smoke result:

- Exits 0.
- Reads `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (confirmed by `price_source.file` in the JSON).
- Writes disposable artifacts to `/tmp`.
- Does not touch `backtest/runs/` or `reports/`.

## Full Command

```bash
python scripts/h48_unified_qfq_h42_rerun.py
```

Expected full result:

- Same `--stage-b-limit` default and search space as H42 (do not narrow the grid).
- Exits 0 in roughly the same wall-clock as H42 (~15–25 min on this machine).
- Writes `backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json` and `reports/h48_unified_qfq_h42_rerun_report.md`.
- Flushes progress.
- H38 and H42 originals untouched (verify via `git status` after the run).

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h42
python scripts/validate_hxx_artifacts.py --artifact h47
python scripts/validate_hxx_artifacts.py --artifact h48
pytest tests/test_h48_unified_qfq_h42_rerun.py tests/test_validate_hxx_artifacts.py -q
python scripts/validate_ledger_consistency.py --strict
git status --short data/cn_pit/prices_h38_candidate.csv \
                   data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
                   backtest/runs/fundamental_value_h42_strategy_redesign_search.json \
                   reports/h42_strategy_redesign_search_report.md
```

Last command must print nothing (no modifications to canonical inputs or H42 originals).

## Acceptance Gate

The H48 verdict is reported using the H42 gate logic verbatim:

- A candidate passes only if ALL H42 conditions hold (deploy not blocked, zero warnings, ≥30 sells, terminal losing streak <5, positive windows ≥4/5, unblocked windows ≥3/5, beat-HS300 windows ≥2/5, deploy excess >0, MaxDD > -8%).
- Verdict: `CANDIDATE_FOR_FORWARD_TRIAL` only if at least one candidate passes; otherwise `RESEARCH_ONLY`.

H48 closure checklist:

- [ ] Wrapper resolves to H47 prices; provenance recorded in JSON.
- [ ] Originals untouched (`git status` clean for the four files above).
- [ ] H42 and H48 reports both present; consistency validator passes for both.
- [ ] `validate_h48` enforces price-source provenance.
- [ ] Report includes an explicit side-by-side comparison: H42 verdict + gate-pass count + best deploy candidate vs. H48 verdict + gate-pass count + best deploy candidate.
- [ ] `docs/strategy-optimization-sync.md` updated with the H48 row.
- [ ] `docs/agents/next-slices.md` H48 entry flipped to `Status: DONE`.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Report Contents

`reports/h48_unified_qfq_h42_rerun_report.md` must include:

- One-line summary of the question H48 is answering.
- Provenance block (price source, sha256, row/column counts).
- Search space summary (identical to H42).
- H42 vs H48 verdict comparison table.
- Top 15 H48 candidates (same columns as H42).
- Per-window detail for the best 3 H48 candidates.
- Side-by-side row for any candidate that appears in both H42 top-15 and H48 top-15 (overlay/params identical), so the reader can see how the same strategy behaves under the two price sources.
- Explicit final verdict.
- Next recommended action.

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas to call out in the review prompt:

- Does the wrapper actually load H47 prices, or does it silently fall back to the H42 default?
- Is the provenance block written before any verdict comparison is computed?
- Are there any inadvertent edits to the H42 search code (overlay families, gate thresholds, window definitions)?
- Is the H42 vs H48 comparison faithful (same gate logic, same windows, same params)?

## Closure Note

Record the final verdict — `CANDIDATE_FOR_FORWARD_TRIAL` or `RESEARCH_ONLY` — in `docs/strategy-optimization-sync.md`, alongside a one-line interpretation of what the H42→H48 delta means for the H38 price-source policy.
