# H47 - Production Price Rebuild

## Context

H38-H46 used research/shadow price files derived from the H30 yfinance-adjusted matrix, with a narrow Tushare benchmark patch. H38 policy says production deployment requires a full rebuild from one consistent adjusted source. H47 creates that candidate matrix without overwriting H38.

## Objective

Rebuild the HS300 PIT universe price matrix from Tushare `pro_bar(adj="qfq")` plus Tushare HS300 `index_daily`, then produce coverage artifacts that can be used by H42-or-later reruns.

## Inputs

- `data/cn_pit/universe_h30_candidate.jsonl`
- `data/cn_pit/universe_snapshots_h30_candidate.jsonl`
- Tushare token from local config/environment

## Outputs

- `scripts/h47_build_tushare_qfq_prices.py`
- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv`
- `data/cn_pit/price_coverage_h47.json`
- `reports/h47_tushare_qfq_price_rebuild_report.md`
- `tests/test_h47_build_tushare_qfq_prices.py`
- `scripts/validate_hxx_artifacts.py` H47 registration

## Hard Prohibitions

- Do not overwrite `data/cn_pit/prices_h38_candidate.csv`.
- Do not overwrite H39-H46 run/report artifacts.
- Do not modify production trading config.
- Do not place live orders.
- Do not print or store the Tushare token.

## Smoke Command

```bash
python scripts/h47_build_tushare_qfq_prices.py \
  --start 2025-01-01 \
  --end 2025-01-10 \
  --limit 3 \
  --raw-dir /tmp/h47_raw_smoke \
  --output-prices /tmp/h47_prices_smoke.csv \
  --output-coverage /tmp/h47_coverage_smoke.json \
  --output-report /tmp/h47_report_smoke.md
```

Expected smoke result:

- Exits 0.
- Fetches a bounded ticker sample plus HS300 benchmark.
- Writes disposable artifacts.

## Full Command

```bash
python scripts/h47_build_tushare_qfq_prices.py \
  --start 2020-01-01 \
  --end 2026-05-21
```

Expected full result:

- Exits 0.
- Writes the H47 candidate price matrix.
- Writes coverage JSON and Markdown report.
- Does not overwrite H38.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h47
pytest tests/test_h47_build_tushare_qfq_prices.py tests/test_validate_hxx_artifacts.py tests/test_validate_agent_workflow.py -q
python scripts/validate_ledger_consistency.py --strict
```

## Acceptance Gate

- [ ] Uses one consistent adjustment methodology for stock columns: Tushare `pro_bar(adj="qfq")`.
- [ ] Includes HS300 benchmark from Tushare `index_daily`.
- [ ] Produces a coverage report.
- [ ] Does not overwrite H38 research data.
- [ ] Registers H47 in `scripts/validate_hxx_artifacts.py`.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.
