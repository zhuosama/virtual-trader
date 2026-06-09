# H49a — SW L1 Industry Classification Ingestion

## Context

H42 and H48 both ended at `RESEARCH_ONLY` with `beat_HS300_windows = 0/5` for every top candidate. The H45 PRD prescribed four alpha directions; the chosen first direction is **Sector-Neutral Relative Strength**. That direction requires a sector-classification dataset that currently does not exist in this repo:

- `data/cn_pit/fundamentals.jsonl` has ROE / debt_to_equity only — no industry column.
- `data/cn_pit/universe_h30_candidate.jsonl` has weight / effective_date but no industry mapping.
- Industry strings found elsewhere (`market-data/watchlist.json`, `strategies/active.json`) are non-PIT operational metadata, unsuitable for backtest selection.

H49a is the data slice. It produces a stable, audit-tagged sector metadata file from Tushare 申万一级 (Shenwan L1) classification for every ticker in the H30 universe. H49b (sector-neutral RS search) is blocked on this output.

H45 PRD §Data Requirements allows "point-in-time **or historically stable** classification with source/provider metadata". SW L1 is stable enough at the universe level over the H42 windows that a single snapshot is acceptable provided the report records the provider, snapshot date, and any mid-window reclassifications observed.

## Objective

Ingest Shenwan L1 industry classification from Tushare for every ticker in `universe_h30_candidate.jsonl`, write a stable sector metadata file + coverage report, and register the H49a artifact family in the validator.

## Inputs

- `data/cn_pit/universe_h30_candidate.jsonl` (514 lines, 481 unique tickers per H47)
- Tushare token from local config / environment (same provider used by H47)
- Tushare `index_classify` (level=L1, src=SW2021) for the canonical SW L1 code list
- Tushare `index_member` per L1 code for ticker → industry mapping

## Outputs

- `scripts/h49a_build_tushare_sw_industry.py` — ingestion script with smoke flags
- `data/cn_pit/sector_metadata_sw_l1.csv` — columns: `ticker, industry_code, industry_name, source_provider, snapshot_date, ingested_at`
- `data/cn_pit/sector_coverage_h49a.json` — coverage report with provider, snapshot_date, mapped_count, unmapped_tickers, industry_histogram
- `reports/h49a_sw_industry_ingestion_report.md`
- `tests/test_h49a_build_tushare_sw_industry.py`
- `scripts/validate_hxx_artifacts.py` — register `h49a` with a `validate_h49a` checker

## Hard Prohibitions

- Do not overwrite or modify any H30 / H38 / H47 input artifact, including `universe_h30_candidate.jsonl` and `prices_h47_tushare_qfq_candidate.csv`.
- Do not modify production trading config.
- Do not place live orders.
- Do not print or store the Tushare token.
- Do not author commits as `codex` or `claude-code`.
- Do not infer industry from yfinance, ticker prefix, or stock name; use only Tushare `index_classify` + `index_member`.
- If a ticker maps to multiple SW L1 codes (legitimate edge case during reclassification), prefer the snapshot active at the latest snapshot_date; record the alternates in the coverage report under `multi_mapped`.

## Smoke Command

```bash
python scripts/h49a_build_tushare_sw_industry.py \
  --universe data/cn_pit/universe_h30_candidate.jsonl \
  --limit 5 \
  --raw-dir /tmp/h49a_raw_smoke \
  --output-metadata /tmp/h49a_sector_smoke.csv \
  --output-coverage /tmp/h49a_coverage_smoke.json \
  --output-report /tmp/h49a_report_smoke.md
```

Expected smoke result:

- Exits 0.
- Fetches SW L1 index_classify once and a bounded ticker sample.
- Writes disposable artifacts to `/tmp/`.
- Does not touch `data/cn_pit/` or `reports/`.

## Full Command

```bash
python scripts/h49a_build_tushare_sw_industry.py
```

Expected full result:

- Exits 0.
- Writes the canonical sector metadata CSV, coverage JSON, and Markdown report.
- 100% of universe tickers either mapped to exactly one SW L1 code or recorded under `unmapped_tickers` with a stated reason (e.g., delisted before snapshot date, missing from any L1 index_member set).
- `multi_mapped` ticker count printed in the report.

## Coverage Acceptance

- `mapped_count >= 0.98 * universe_ticker_count` (≥98% of the 481 tickers must have a single SW L1 industry).
- `unmapped_tickers` list must include `reason` for each entry.
- `industry_histogram` must show all SW L1 codes present in the universe (sanity: no single industry > 40% of the universe).
- `provenance` block records `provider="tushare:index_classify+index_member"`, `level="L1"`, `src="SW2021"`, `snapshot_date=<actual fetch date>`.

## Verification

```bash
python scripts/h49a_build_tushare_sw_industry.py --smoke-only --validate
python scripts/validate_hxx_artifacts.py --artifact h49a
pytest tests/test_h49a_build_tushare_sw_industry.py tests/test_validate_hxx_artifacts.py -q
python scripts/validate_ledger_consistency.py --strict
git status --short data/cn_pit/universe_h30_candidate.jsonl data/cn_pit/prices_h47_tushare_qfq_candidate.csv data/cn_pit/fundamentals.jsonl
```

The last command must print nothing (no modifications to inputs).

## Acceptance Gate

- [ ] Uses Tushare `index_classify` (L1, SW2021) + `index_member` as the only mapping source.
- [ ] CSV has exactly one row per universe ticker (plus `unmapped_tickers` recorded separately in JSON).
- [ ] Coverage JSON includes provenance, snapshot_date, mapped_count, unmapped list with reasons, industry_histogram, multi_mapped log.
- [ ] No H30/H38/H47 input artifact modified.
- [ ] `h49a` registered in `scripts/validate_hxx_artifacts.py`.
- [ ] Tests cover: load+parse, multi-mapped fallback, coverage threshold violation behavior, CSV schema.
- [ ] No unresolved BLOCKER / HIGH / MEDIUM review findings.

## Report Contents

`reports/h49a_sw_industry_ingestion_report.md` must include:

- One-line objective.
- Provenance block (provider, snapshot_date, source URLs).
- Coverage summary: mapped / unmapped / multi-mapped counts; pass/fail of the 98% threshold.
- Industry histogram (code, name, count, % of universe).
- Top 10 industries by universe weight (joined to H30 `weight` column).
- Listed unmapped tickers with reason each.
- Verdict: `CANDIDATE_DATASET` if coverage passes; otherwise explain blockers.
- Note: H49a is a data slice, not a strategy promotion.

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- Is the snapshot_date recorded? (mandatory for "historically stable" claim)
- Are unmapped tickers traceable to a concrete reason (delisted / IPO after snapshot / missing from member)?
- Could the multi_mapped fallback hide a true classification ambiguity?
- Are the test fixtures small and deterministic (no network in tests)?

## Closure Note

Record final verdict in `docs/strategy-optimization-sync.md`. State the next dependency: H49b sector-neutral RS search is unblocked once H49a is closed.
