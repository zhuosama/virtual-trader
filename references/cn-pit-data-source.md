# A-share point-in-time data source

This file defines the audited local format consumed by
`backtest/experiments/fundamental_backtest.py::CN_PIT_FileSource`.

## Purpose

The value backtest must not read current fundamentals while simulating an older
date. Hermes should ingest official disclosure and constituent data into local
append-only files first, then run the backtest against those files.

Primary disclosure sources:

- CNINFO: `https://www.cninfo.com.cn/`
- SSE regular reports: `https://www.sse.com.cn/disclosure/listedinfo/regular/`
- SZSE disclosure: `https://www.szse.cn/disclosure/`

## Directory

Default root:

```text
~/.hermes/virtual-trader/data/cn_pit/
```

Required files:

```text
universe.jsonl
fundamentals.jsonl
prices.csv
```

If any required file is missing or metadata validation fails,
`CN_PIT_FileSource` stays `research_only=true` and deployment remains blocked.

## universe.jsonl

One JSON object per constituent membership interval.

```json
{
  "ticker": "600519.SS",
  "effective_date": "2025-01-01",
  "end_date": "",
  "source_url": "https://www.csindex.com.cn/",
  "ingested_at": "2026-05-19T00:00:00Z"
}
```

Rules:

- `ticker` may also be supplied as bare `code`; it will be normalized to
  yfinance style (`600519.SS`, `000858.SZ`).
- `effective_date` is required.
- `end_date` is optional; empty means still active.
- `source_url` and `ingested_at` are required for auditability.
- `get_universe(as_of_date)` returns only records active on that date.
- `get_price_universe(start, end)` returns the union of records overlapping the
  whole backtest window so historical prices can be prefetched without freezing
  the universe at the start date.

## fundamentals.jsonl

One JSON object per company filing period.

```json
{
  "ticker": "600519.SS",
  "report_period": "2024-12-31",
  "filing_date": "2025-04-20",
  "source_url": "https://www.cninfo.com.cn/",
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

Rules:

- `report_period`, `filing_date`, `source_url`, and `ingested_at` are required.
- A record is visible only when `filing_date <= as_of_date`.
- If multiple records are visible, the latest by `(filing_date, report_period)`
  is used.
- Missing valuation metrics are allowed, but a stock is not ranked unless at
  least two of `roe`, `fcf_yield`, and `debt_to_equity` are present.

## prices.csv

Wide daily close table:

```csv
date,600519.SS,000858.SZ,000300.SS
2025-01-02,100.0,80.0,4000.0
```

`000300.SS` is used as the HS300 benchmark column when present.

## Deployment gate

A clean local PIT source sets all `DataQuality` flags to `false` only when:

- universe membership is date-ranged and source-linked;
- fundamentals have filing-date gates and source links;
- local historical prices exist;
- validation has no missing required metadata.

Even with clean data, deployment still requires the ordinary strategy gates:
minimum trading days, minimum closed trades, non-negative total return, and
non-negative Sharpe.
