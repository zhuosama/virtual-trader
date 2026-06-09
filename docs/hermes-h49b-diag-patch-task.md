# H49b — Diagnostic Patch (Sector Distribution + Multi-Mapped Follow-Up)

## Context

H49b closed at `RESEARCH_ONLY` and the validator now reports `9/9 PASS`. However, the report at `reports/h49b_sector_neutral_rs_search_report.md` left two required sections as placeholders rather than real numbers:

1. `## Sector Distribution (Best Candidate)` — currently says "Sector distribution at deploy-window start/end is available in the detailed trade data" with no numbers.
2. `## Multi-Mapped Follow-Up` — currently a generic warning ("if best candidate's holdings concentrate in industries heavily represented in `multi_mapped`, a follow-up sector reclassification run is warranted") without checking whether this actually happened.

Both diagnostics are required by the H49b brief (`docs/hermes-h49b-sector-neutral-rs-search-task.md` Report Contents section). This patch backfills them. The substantive H49b verdict does not change.

## Objective

Compute real sector-distribution and multi-mapped-intersection numbers for H49b's best candidate (`top_candidates_multi_window[0]`), edit the two placeholder sections of the H49b report with the actual numbers, and add a single audit-only diagnostic script that does the computation.

## Inputs

- `backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json` — read-only. The best candidate is `top_candidates_multi_window[0]`: `rel20_ge_0_and_ma60`, `top_n=10, max_pos=0.06, SL=0.08, TP=0.22, QF=0.40, Rebal=63, sector_max_weight_pct=0.25, min_sectors=7`.
- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` — for the deploy window replay
- `data/cn_pit/sector_metadata_sw_l1.csv` — for sector join
- `data/cn_pit/sector_coverage_h49a.json` — `multi_mapped_tickers` list lives here
- `data/cn_pit/universe_h30_candidate.jsonl`, `universe_snapshots_h30_candidate.jsonl`
- `scripts/h49b_sector_neutral_rs_search.py` — read-only; re-import its overlay+param construction helpers
- `backtest/experiments/fundamental_backtest.py` — read-only; use `run_fundamental_backtest` to replay

## Outputs

- `scripts/h49b_sector_diagnostic.py` — new diagnostic script (~150–250 LOC). Loads H49b JSON, picks top-1 candidate, replays the deploy window with holdings capture, joins to H49a SW L1, and writes the two report sections via in-place markdown edit between explicit delimiters.
- Edits to `reports/h49b_sector_neutral_rs_search_report.md`: replace the two placeholder sections (between markers, see below) with real numbers.
- No other files modified.

## Hard Prohibitions

- Do not modify `scripts/h49b_sector_neutral_rs_search.py`.
- Do not modify `backtest/experiments/fundamental_backtest.py`.
- Do not modify `backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json` (no new fields, no re-write, no re-run of the full search).
- Do not modify `scripts/validate_hxx_artifacts.py` or `tests/test_validate_hxx_artifacts.py`.
- Do not touch any heading the validate_h49b function asserts on: `## Data Sources`, `## Design Choices`, `## H42 vs H48 vs H49b Comparison`, `## Did Sector-Neutral Selection Help?`, `## Final Verdict`. Headings of the two patched sections (`## Sector Distribution (Best Candidate)` and `## Multi-Mapped Follow-Up`) must remain identical strings; only the body under each heading changes.
- Do not modify any H30 / H38 / H47 / H49a input artifact.
- Do not modify production trading config.
- Do not place live orders.
- No network: no Tushare, no yfinance.
- Do not author commits as `codex` or `claude-code`.
- Do not loosen `tests/test_validate_hxx_artifacts.py`.

## Section Delimiters

Use unique HTML-comment markers around the patched bodies so the diagnostic script can be re-run idempotently:

```
## Sector Distribution (Best Candidate)
<!-- h49b-diag:sector-distribution:begin -->
...patched body...
<!-- h49b-diag:sector-distribution:end -->

## Multi-Mapped Follow-Up
<!-- h49b-diag:multi-mapped:begin -->
...patched body...
<!-- h49b-diag:multi-mapped:end -->
```

The diagnostic script must look for these markers; if absent on first run, it inserts them around the current placeholder content. On re-runs it replaces only the content between markers.

## Required Body Content

### Section 1 — Sector Distribution (Best Candidate)

For the best candidate's deploy window (2025-01-01 → 2026-05-21):

- **Holdings at deploy start** (first rebalance after 2025-01-01): list each held ticker + SW L1 industry name + portfolio weight; aggregate to a per-sector table with `count`, `total_weight`, `max_weight` columns.
- **Holdings at deploy end** (last rebalance before 2026-05-21): same table.
- **Cap-held check**: a one-line PASS/FAIL stating whether `max_weight` per sector ≤ `sector_max_weight_pct = 0.25 + tolerance` at both snapshots. Tolerance: 0.005 (50 bps) to allow for price-drift between rebalances. If FAIL, list the violating sector(s) and observed max_weight.
- **Sector count**: confirm `len(sectors_with_positions) >= min_sectors_in_portfolio = 7` at both snapshots.

### Section 2 — Multi-Mapped Follow-Up

- Load `data['multi_mapped_tickers']` from `data/cn_pit/sector_coverage_h49a.json` (list of ticker codes).
- For the best candidate's union of all-time deploy-window holdings (any ticker ever held during deploy), compute the intersection with the multi-mapped set.
- Report:
  - `holdings_count`: total unique tickers ever held during deploy
  - `multi_mapped_intersection_count`: how many of those are in H49a's multi_mapped list
  - `intersection_pct`: percentage
  - If `intersection_pct > 30%`: print "FOLLOW-UP REQUIRED" with the intersecting ticker list and their primary SW L1 industry codes.
  - If `intersection_pct <= 30%`: print "OK — multi-mapped contamination below threshold."

## Smoke Command

```bash
python scripts/h49b_sector_diagnostic.py --dry-run --output-report /tmp/h49b_report_smoke.md
```

Expected smoke result:

- Exits 0.
- `--dry-run` reads inputs, computes the two sections, writes a copy of the H49b report with patched sections to `/tmp/h49b_report_smoke.md`. Does NOT touch `reports/h49b_sector_neutral_rs_search_report.md`.

## Full Command

```bash
python scripts/h49b_sector_diagnostic.py
```

Expected full result:

- Exits 0.
- Edits `reports/h49b_sector_neutral_rs_search_report.md` in place: only the content between the two pairs of markers changes.
- Idempotent: a second run produces no diff (the patched content has stable formatting).

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h49b
pytest tests/test_validate_hxx_artifacts.py tests/test_h49b_sector_neutral_rs_search.py -q
python scripts/validate_ledger_consistency.py --strict
git status --short \
  scripts/h49b_sector_neutral_rs_search.py \
  backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json \
  backtest/experiments/fundamental_backtest.py \
  scripts/validate_hxx_artifacts.py \
  tests/test_validate_hxx_artifacts.py
# Verify the patched report markers exist and are paired:
grep -c "h49b-diag:sector-distribution:begin" reports/h49b_sector_neutral_rs_search_report.md
grep -c "h49b-diag:sector-distribution:end" reports/h49b_sector_neutral_rs_search_report.md
grep -c "h49b-diag:multi-mapped:begin" reports/h49b_sector_neutral_rs_search_report.md
grep -c "h49b-diag:multi-mapped:end" reports/h49b_sector_neutral_rs_search_report.md
# Each of the four grep -c commands must print exactly: 1
# Run diagnostic twice in a row, second run must produce zero diff:
python scripts/h49b_sector_diagnostic.py
python scripts/h49b_sector_diagnostic.py
git diff --quiet reports/h49b_sector_neutral_rs_search_report.md && echo "idempotent OK" || echo "FAIL: second run produced diff"
```

Required outcomes:

- `validate_hxx_artifacts.py --artifact h49b` → `[PASS]`
- `git status --short` for the 5 listed files prints nothing
- Each of the 4 marker `grep -c` lines prints `1`
- The idempotency check prints `idempotent OK`

## Acceptance Gate

- [ ] Both report sections contain real numbers (counts, weights, percentages) and the cap-held PASS/FAIL line.
- [ ] Multi-mapped intersection numbers are computed from the actual deploy-window holdings (not a placeholder count).
- [ ] Diagnostic script is idempotent (second run produces no diff).
- [ ] None of the six prohibited files are modified.
- [ ] `validate_h49b` still passes.

## Closure Note

Append a one-line entry to `docs/strategy-optimization-sync.md` under the H49b row: "H49b report sections backfilled by `scripts/h49b_sector_diagnostic.py` on <date>; cap held / FAIL; multi-mapped intersection <N>%."
