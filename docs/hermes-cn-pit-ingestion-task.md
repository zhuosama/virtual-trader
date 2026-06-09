# Hermes Task: CN PIT Data Ingestion Dirty Work

Status: assigned to Hermes for data ingestion and cleaning.

Do not change live strategy parameters or deployment gates in this task.

## Objective

Build a repeatable A-share point-in-time data ingestion pipeline that outputs the
local files consumed by `CN_PIT_FileSource`:

- `~/.hermes/virtual-trader/data/cn_pit/universe.jsonl`
- `~/.hermes/virtual-trader/data/cn_pit/fundamentals.jsonl`
- `~/.hermes/virtual-trader/data/cn_pit/prices.csv`

The current value-account backtests must remain `research_only` until these
files pass validation.

## Reference Projects And Docs

Use these as references, not as blindly copied dependencies.

1. AKShare
   - Docs: https://akshare.akfamily.xyz/data/stock/stock.html
   - Useful interfaces:
     - `stock_zh_a_disclosure_report_cninfo`
     - `stock_report_disclosure`
     - `stock_industry_change_cninfo`
   - Purpose: CNINFO announcement search, disclosure dates, public A-share data.

2. Tushare
   - Docs examples:
     - https://tushare.pro/document/2?doc_id=36
     - https://www.tushare.pro/document/2?doc_id=176
   - Useful idea: financial tables expose `ann_date` / announcement date.
   - Purpose: field naming and announcement-date semantics.

3. BaoStock
   - PyPI: https://pypi.org/project/baostock/
   - Purpose: free A-share historical K-line and financial indicator reference.
   - Caveat: verify whether each field is point-in-time safe before use.

4. Microsoft Qlib PIT design
   - Docs: https://qlib.org.cn/en/latest/advanced/PIT.html
   - Purpose: reference design for point-in-time financial data storage.

5. CNINFO crawler references
   - https://github.com/Interstellar1217/CNInfoHedgeCrawler
   - https://github.com/tr1s7an/CnInfoReports
   - https://github.com/gaodechen/cninfo_process
   - Purpose: announcement query/download mechanics and PDF handling patterns.

6. SSE/SZSE official disclosure entry points
   - SSE regular reports: https://www.sse.com.cn/disclosure/listedinfo/regular/
   - SZSE disclosure: https://www.szse.cn/disclosure/
   - CNINFO: https://www.cninfo.com.cn/

## Output Schema

Follow `references/cn-pit-data-source.md` exactly.

### universe.jsonl

One constituent interval per line:

```json
{
  "ticker": "600519.SS",
  "effective_date": "2025-01-01",
  "end_date": "",
  "source_url": "https://...",
  "ingested_at": "2026-05-19T00:00:00Z"
}
```

Requirements:

- Use historical constituent snapshots or interval records.
- Do not use a static current blue-chip list as deployable PIT universe.
- Missing `source_url` or `ingested_at` must fail validation.

### fundamentals.jsonl

One filing-period record per line:

```json
{
  "ticker": "600519.SS",
  "report_period": "2024-12-31",
  "filing_date": "2025-04-20",
  "source_url": "https://...",
  "ingested_at": "2026-05-19T00:00:00Z",
  "roe": 30.0,
  "fcf_yield": 5.0,
  "debt_to_equity": 0.2,
  "pe_ratio": 20.0,
  "pb_ratio": 5.0,
  "dividend_yield": 2.0,
  "market_cap": 2000000000
}
```

Requirements:

- `filing_date` must be the public disclosure date, not report period end date.
- `source_url` must point to the announcement/report source.
- Use `null` or omit metrics that cannot be verified.
- Never backfill current metrics into earlier `filing_date` records.

### prices.csv

Wide adjusted daily close table:

```csv
date,600519.SS,000858.SZ,000300.SS
2025-01-02,100.0,80.0,4000.0
```

Requirements:

- Include `000300.SS` benchmark when available.
- Record adjustment source in a sidecar metadata file if adjustment method is not
  obvious.

## Pipeline Tasks

1. Create `scripts/ingest_cn_pit_data.py`.
2. Add idempotent modes:
   - `--fetch-universe`
   - `--fetch-disclosures`
   - `--fetch-prices`
   - `--validate`
   - `--all`
3. Store raw downloaded artifacts under:
   - `data/cn_pit/raw/`
4. Store cleaned files under:
   - `data/cn_pit/`
5. Add validation checks:
   - required files exist;
   - JSONL parses;
   - required metadata fields exist;
   - no duplicate `(ticker, report_period, filing_date)`;
   - `filing_date <= ingested_at[:10]`;
   - universe intervals have valid date ranges;
   - at least one price column overlaps universe tickers;
   - `CN_PIT_FileSource(root).data_quality.is_clean` is true for cleaned output.
6. Add a dry-run summary:
   - count universe rows;
   - count fundamental rows;
   - count tickers;
   - date ranges;
   - validation errors;
   - whether deployment data-quality blockers are cleared.

## Acceptance Criteria

Run these commands successfully:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_fundamental_pit_source.py tests/test_value_account.py
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python -m py_compile backtest/experiments/fundamental_backtest.py scripts/ingest_cn_pit_data.py
```

Then run:

```bash
/Users/zhuosama/.hermes/hermes-agent/venv/bin/python scripts/ingest_cn_pit_data.py --validate
```

Report:

- files created/updated;
- validation summary;
- data-quality blocker status;
- remaining blockers, if any;
- exact command to rerun the fundamental value backtest.
