# H52c — CSI500 Daily Fact Data (QFQ Prices + ADTV)

## Context

H52a (universe, 1074 unique tickers + 88 monthly snapshots) and H52b (sector, 99.91% mapped, 31 SW L1 industries) are closed. H52c is the heaviest data slice in the H52 track: the price + liquidity matrix that downstream H52d/H52e/H52f all depend on.

This slice fuses what H47 (prices) and H51a V2 (ADTV) accomplished separately for the H30 universe, into a **single axis-flip pass** for CSI500. Two changes from H47/H51a:

1. **Axis flip** — query Tushare per `trade_date` instead of per ticker. Collapses ~10,000 per-ticker pro_bar calls to ~3,200 per-date calls.
2. **Local qfq compute** — Tushare's per-trade_date `daily` endpoint returns raw (不复权) close. qfq adjustment is computed locally from a paired per-trade_date `adj_factor` pull. (Tushare's `pro_bar(adj="qfq")` per-ticker convenience does NOT have a trade_date axis; we re-derive its output locally.)

The dual output of this slice replaces both H47's wide-format prices and H51a's long-format liquidity in one ingestion run.

## Objective

Ingest daily fact data (raw close + vol + amount + adjustment factor) for all 1074 H52a unique tickers across 2020-01-02 → 2026-05-21 (~1593 A-share trading days). Locally compute snapshot-qfq close prices. Persist two output files:

1. H47-style wide-format qfq price matrix (`data/cn_pit/prices_h52c_csi500_qfq.csv`) with the HS300 benchmark as a column.
2. H51a-style long-format liquidity (`data/cn_pit/liquidity_h52c_csi500_daily_amount.csv`) with correct unit conventions (千元 → RMB; 手 → shares).

## Inputs

- `data/cn_pit/universe_h52a_csi500.jsonl` (1074 unique tickers; H52a output)
- `data/cn_pit/universe_snapshots_h52a_csi500.jsonl` (PIT membership per snapshot; H52a output — used by downstream H52f but H52c reads it only for sanity check on per-date active membership)
- Tushare token (standard chain)
- Tushare endpoints:
  - `daily(trade_date='YYYYMMDD')` — bulk: returns one row per A-share for that trade_date with `ts_code, open, high, low, close, pre_close, change, pct_chg, vol, amount`. Filter locally to H52a universe ∪ {HS300 benchmark}.
  - `adj_factor(trade_date='YYYYMMDD')` — bulk: returns `ts_code, trade_date, adj_factor` for all A-shares.
  - `index_daily(ts_code='000300.SH', start_date='20200102', end_date='20260521')` — single bulk call returns full HS300 history (~1593 rows). One call, not axis-flipped (index history is small).

## Outputs

- `scripts/h52c_build_csi500_daily_facts.py` — ingestion script.
- `data/cn_pit/prices_h52c_csi500_qfq.csv` — **PINNED schema matches `prices_h47_tushare_qfq_candidate.csv` exactly**: wide-format, first column `date` (YYYY-MM-DD), then one column per ticker in H52a universe (1074 columns) plus one trailing column `000300.SS` for the HS300 benchmark (1076 columns total). Values are snapshot-qfq close prices (RMB, float). Non-trading days or pre-listing / post-delisting cells = empty (NaN serialized as empty string by pandas default).

  ```
  date,000001.SZ,000002.SZ,...,688981.SS,000300.SS
  2020-01-02,16.32,28.91,...,,4159.27
  ```

- `data/cn_pit/liquidity_h52c_csi500_daily_amount.csv` — **PINNED schema matches `liquidity_h51a_daily_amount.csv` exactly**: long-format, 5 columns `date, ticker, amount_rmb, vol_shares, source`. One row per (ticker, trade_date) where the ticker traded. `amount_rmb` MUST be `Tushare.amount × 1000` (千元 → RMB conversion; H51a V2 lesson). `vol_shares` MUST be `Tushare.vol × 100` (手 → shares conversion; H51a V1 lesson). `source` = `"tushare:daily"`.

  ```
  date,ticker,amount_rmb,vol_shares,source
  2020-01-02,000001.SZ,772959353.0,69582809.0,tushare:daily
  ```

- `data/cn_pit/price_coverage_h52c.json` — coverage report (see Provenance Block section).
- `reports/h52c_csi500_daily_facts_ingestion_report.md`
- `tests/test_h52c_build_csi500_daily_facts.py`
- `scripts/validate_hxx_artifacts.py` — register `h52c` with `validate_h52c` checker.
- Raw cache (gitignored):
  - `data/cn_pit/raw/h52c_tushare_daily/<YYYYMMDD>.csv` — one file per trade_date.
  - `data/cn_pit/raw/h52c_tushare_adj_factor/<YYYYMMDD>.csv` — one file per trade_date.
  - `data/cn_pit/raw/h52c_tushare_index_daily/000300_SH.csv` — single HS300 history file.
  - Add `data/cn_pit/raw/h52c_tushare_*/` wildcard to `.gitignore` BEFORE the full run.

## Hard Prohibitions

- Do NOT modify `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (H30 prices; must stay intact for H42/H48/H49b/H50b/H51b reproducibility).
- Do NOT modify `data/cn_pit/price_coverage_h47.json` (H47 coverage immutable).
- Do NOT modify `data/cn_pit/liquidity_h51a_daily_amount.csv` (H30 ADTV; immutable for H51b reproducibility).
- Do NOT modify `data/cn_pit/liquidity_coverage_h51a.json`.
- Do NOT modify `data/cn_pit/liquidity_h33_daily_amount.csv` or `liquidity_h40_h39_candidate_daily_amount.csv` (legacy small files).
- Do NOT modify `data/cn_pit/universe_h52a_csi500.jsonl` or `universe_snapshots_h52a_csi500.jsonl`.
- Do NOT modify `data/cn_pit/sector_metadata_h52b_csi500.csv` or `sector_coverage_h52b.json`.
- Do NOT modify SHA256-protected H28 baselines (universe.jsonl, universe_snapshots.jsonl, fundamentals.jsonl).
- Do NOT modify any H30/H47/H49a/H50a/H51a artifact.
- Do NOT use `pro_bar(adj='qfq')` per ticker — that defeats axis-flip. qfq MUST be computed locally from `daily` + `adj_factor`.
- Do NOT use yfinance, eastmoney, sina, or any non-Tushare price source.
- Do NOT modify production trading config; do not place live orders.
- Do NOT print or store the Tushare token value.
- Do NOT author commits as `codex` or `claude-code`.
- Do NOT loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT silently shrink the universe column count below 1074 + 1 benchmark.

## Design Decisions (locked unless overridden before dispatch)

### D1. Three-Endpoint Axis Flip per trade_date (with index_daily as 1-shot bulk)

```
for trade_date in trading_days_2020-01-02_to_2026-05-21:
    if not cached:
        df_d = pro.daily(trade_date=trade_date)          # ~5000 rows, filter to H52a ∪ {benchmark}
        df_a = pro.adj_factor(trade_date=trade_date)     # ~5000 rows, filter to H52a
        save_raw_cache(trade_date, df_d, df_a)

df_hs300 = pro.index_daily(ts_code='000300.SH', start_date='20200102', end_date='20260521')   # 1 bulk call, full benchmark history
```

Call count budget: 2 × 1593 + 1 = **~3187 calls**. At 5 calls/sec hard cap → ~11 minutes pure network. Resumable across both per-date endpoints.

### D2. Local QFQ Computation (snapshot convention)

Per-ticker snapshot-qfq close formula (matches H47 / Tushare `pro_bar(adj='qfq')` convention):

```
adj_factor_terminal[ticker] = adj_factor at the LATEST trade_date for that ticker in the dataset (typically the run's end_date or the ticker's last active day)
qfq_close_t = raw_close_t × adj_factor_t / adj_factor_terminal[ticker]
```

Implementation:

1. Pass 1 (per-trade_date axis-flip): fetch all (trade_date, ticker, raw_close, adj_factor) tuples.
2. Pass 2 (post-fetch, per-ticker): for each ticker, find its `adj_factor_terminal` (the adj_factor at its latest non-NaN row); compute qfq_close per row.
3. **Benchmark special case**: `000300.SS` (HS300) has NO `adj_factor` (it's an index level, not a stock; we never fetched its adj_factor). The qfq math MUST be bypassed for this ticker: `qfq_close[benchmark] = raw_close[benchmark]` (i.e., the published index level as-is from `index_daily`). If the script naively iterates `for col in df.columns: qfq = col_close * adj / adj_terminal`, the benchmark column will hit KeyError on `adj_factor[benchmark]` OR divide by NaN. Guard with an explicit early-return for `ticker == HS300_TICKER`.
4. Pivot to wide-format with `date` as index, tickers + benchmark as columns. Apply force-reindex per D5 to pin column count at 1076.

PIT note: the "snapshot qfq" convention uses a SINGLE terminal divisor for each ticker's entire history. The divisor cancels in returns, so cumulative return calculations are unaffected even when the divisor incorporates future splits. This matches industry standard (Tushare `pro_bar(adj='qfq')` works this way) and is what H47 produces; H52c MUST match.

### D3. Universe Filtering

After pulling raw daily data per trade_date, filter locally:

```python
keep = set(h52a_unique_tickers) | {HS300_TICKER}     # 1074 + 1 = 1075 tickers max
df_d = df_d[df_d.ts_code.isin(keep)]
df_a = df_a[df_a.ts_code.isin(keep_stocks_only)]      # adj_factor only for stocks; HS300 doesn't need it
```

Do NOT fetch additional tickers outside this set. Tushare's response already returns ALL A-shares per trade_date; we just locally subset.

Per-date row count expectation: typically 500-800 of the 1075 (since CSI500 PIT membership is ~500 + delisted segments give NaN). Median day should have ≥500 active stock rows.

### D4. Ticker Normalization

- Tushare `ts_code` format: `000001.SZ` for SZSE, `600000.SH` for SSE.
- Yahoo format in output files: `000001.SZ` for SZSE, `600000.SS` for SSE.
- Conversion at output time: `ticker = ts_code.replace('.SH', '.SS')` for the prices column headers and the liquidity ticker column.
- HS300 benchmark: stored as `000300.SS` in the prices column (matches H47 convention).

### D5. NaN / Missing Day Handling + Column Dimension Pinning (CRITICAL)

- Raw close = NaN for any (ticker, trade_date) pair where the ticker didn't trade (suspended, pre-listing, post-delisting). Carries through to qfq as NaN. Persisted as empty string in CSV (pandas default).
- vol / amount = NaN → DO NOT emit a liquidity row for that (ticker, trade_date). Liquidity CSV is sparse — only contains rows where the ticker actually traded. (Schema strictly 5 cols; NaN handling documented in coverage JSON, not per-row.)
- Wide-format prices CSV: ALL trade_dates × ALL tickers grid; empty cell = no trade.

**Column-dimension pinning (must do, not optional):**

H52a covers 2019-01-31 → 2026-04-30 (1074 unique tickers). H52c only pulls 2020-01-02 → 2026-05-21. **Tickers that were in CSI500 ONLY during 2019** (entered + exited within 2019) will have **0 rows** of price data in H52c's date range. Pandas `pivot`/`unstack` silently DROP these all-empty columns by default → resulting CSV has <1076 columns → `validate_h52c` assertion `wide_columns == 1076` fails.

After the pivot, the script MUST force-reindex:

```python
universe_tickers = sorted(h52a_unique_ticker_set)   # 1074 ordered tickers (Yahoo format)
wide_prices = wide_prices.reindex(columns=universe_tickers + ['000300.SS'])
# Tickers with zero price data now have an all-NaN column (still serialized as empty cells).
```

The `tickers_with_no_h52c_data` count goes into the coverage JSON `anomalies` block — these are H52a-known tickers that simply didn't trade during the H52c window (legitimate; not a fetch failure). validate_h52c allows up to 60 such all-NaN columns (5% of 1074, generous for the ~574 historical-CSI500-member tail).

### D6. Unit Conventions (CRITICAL — H51a V1 lesson)

- `amount_rmb` MUST be `Tushare.amount × 1000` (千元 → RMB).
- `vol_shares` MUST be `Tushare.vol × 100` (手 → shares).
- Document both conversions in the script with inline comment.
- validate_h52c enforces unit sanity (median implied price check + per-window ADTV check; see Provenance Block).

### D7. PIT Anomaly Surfacing (fail-loud during ingestion)

- Per trade_date: if `daily` returns N rows but `adj_factor` returns M rows where |N - M| > 50 (after filtering to stock universe), flag as anomaly. (Some tickers may legitimately have daily but not adj_factor on the same day during e.g. corporate-action transitions.)
- Per ticker after Pass 2: if `adj_factor_terminal` is NaN (zero non-NaN adj_factor rows for that ticker), the ticker has NO qfq series; record under `tickers_with_no_qfq` and emit NaN-only column.
- Per ticker: if pct_chg between two consecutive trading days is >50% or <-50%, **log to `extreme_pct_chg_anomalies` for audit; this is NOT a blocking gate**. A-share IPO首日 has no price limit; STAR Market / ChiNext 前 5 个交易日 also no limit; long suspension复牌首日 has no limit. Across 1074 tickers × 6.5 years, 100-300 such legitimate events are expected. The coverage JSON still surfaces the count and a sample, but no Acceptance Gate assertion fires on it. Hard abort only triggers if the count exceeds 500 (truly extreme — suggests systematic data corruption, not normal A-share behavior).

### D8. Rate-Limit / Retry Policy

Same pattern as H51a V2:

- Per-(endpoint, trade_date) raw cache: skip if file exists and non-empty.
- HTTP 429 / Tushare error_code → exponential backoff with jitter (initial 2s, doubling, 60s cap, max 5 retries per (endpoint, trade_date)) → then add to `fetch_failures`.
- Hard-cap base rate at 5 calls/sec ACROSS all endpoints combined (single counter; Tushare bills total).
- Failed (endpoint, trade_date) → record `{endpoint, trade_date, reason}` in coverage JSON; do NOT abort.
- Fetch order: `daily` per trade_date FIRST (so prices+ADTV available), then `adj_factor` for same trade_date (qfq computable once adj_factor lands). Index_daily fetched ONCE upfront before the per-date loop.

### D9. ADTV Per-Window Coverage (H51a V2 lesson)

Coverage JSON MUST include `adtv_computability_per_window` with the 5 H42 windows (cal_2024 / h1_2025 / h2_2025 / ytd_2026 / deploy_2025_2026). For each window, count (ticker × eval_date) pairs where 20-day trailing ADTV (exclusive of eval_date) is computable. Each window must hit ≥95%.

## Provenance Block (in `price_coverage_h52c.json`; validate_h52c enforces)

```json
{
  "generated_at": "<UTC ISO>",
  "task": "H52c",
  "status": "CANDIDATE_DATASET | BLOCKED",
  "provenance": {
    "stock_provider": "tushare:daily",
    "adjustment_provider": "tushare:adj_factor",
    "qfq_method": "snapshot_qfq_local_compute",
    "benchmark_provider": "tushare:index_daily",
    "benchmark_ticker": "000300.SS",
    "universe_source": "data/cn_pit/universe_h52a_csi500.jsonl",
    "date_range_requested": "20200102 -> 20260521",
    "date_range_actual": "<from data>",
    "snapshot_timestamp": "<UTC ISO>"
  },
  "coverage": {
    "trade_dates_observed": <int, expect ~1593>,
    "trade_dates_with_full_data": <int, dates with both daily AND adj_factor success>,
    "ticker_coverage_pct": <float>,
    "universe_ticker_count": 1074,
    "tickers_with_no_qfq": [<list of tickers with NaN adj_factor_terminal>],
    "avg_trade_days_per_ticker": <float>,
    "min_trade_days_per_ticker": <int>,
    "median_implied_qfq_price_rmb": <float, must be in [0.5, 5000] for sanity>,
    "p10_p50_p90_qfq_price_rmb": [<float>, <float>, <float>],
    "adtv_computability_per_window": {
      "cal_2024":         {"start": "2024-01-01", "end": "2024-12-31",  "ticker_date_pairs": <int>, "computable_pct": <float>},
      "h1_2025":          {"start": "2025-01-01", "end": "2025-06-30",  "ticker_date_pairs": <int>, "computable_pct": <float>},
      "h2_2025":          {"start": "2025-07-01", "end": "2025-12-31",  "ticker_date_pairs": <int>, "computable_pct": <float>},
      "ytd_2026":         {"start": "2026-01-01", "end": "2026-05-21",  "ticker_date_pairs": <int>, "computable_pct": <float>},
      "deploy_2025_2026": {"start": "2025-01-01", "end": "2026-05-21",  "ticker_date_pairs": <int>, "computable_pct": <float>}
    },
    "benchmark_coverage_pct": <float, HS300 non-NaN days / total days>
  },
  "fetch_failures": [{"endpoint": "...", "trade_date": "...", "reason": "..."}],
  "anomalies": {
    "tickers_with_no_qfq": <int>,
    "daily_vs_adj_factor_row_count_skew_days": <int, days with |N-M|>50>,
    "extreme_pct_chg_anomalies": <int, |pct_chg|>0.5 events; AUDIT-ONLY field, no gate firing under 500>,
    "extreme_pct_chg_sample": [<up to 5 sample tuples (ticker, trade_date, pct_chg)>],
    "tickers_with_no_h52c_data": <int, H52a-listed tickers with zero rows in H52c window — all-NaN columns after force-reindex>
  },
  "verdict": "CANDIDATE_DATASET | BLOCKED"
}
```

`validate_h52c` asserts:
- `provenance.stock_provider == "tushare:daily"`
- `provenance.adjustment_provider == "tushare:adj_factor"`
- `provenance.qfq_method == "snapshot_qfq_local_compute"`
- `provenance.benchmark_provider == "tushare:index_daily"`
- `provenance.benchmark_ticker == "000300.SS"`
- `provenance.universe_source == "data/cn_pit/universe_h52a_csi500.jsonl"` (proves H52a is the dependency)
- `universe_ticker_count == 1074`
- `ticker_coverage_pct >= 98.0`
- `min_trade_days_per_ticker >= 60` (allow new listings and short-lived members; below 60 days is unusable for vol calculations)
- `0.5 <= median_implied_qfq_price_rmb <= 5000.0` (unit sanity)
- All 5 `adtv_computability_per_window.computable_pct >= 95.0`
- `benchmark_coverage_pct >= 99.0` (HS300 should be essentially 100% — failure here means index_daily fetch failed)
- `len(fetch_failures) <= 20`
- `anomalies.extreme_pct_chg_anomalies <= 500` (legitimate IPO首日 / STAR / ChiNext / 复牌首日 events expected; gate only catches truly extreme data corruption)
- `anomalies.tickers_with_no_h52c_data <= 60` (5% of 1074; H52a historical members that never traded during H52c's 2020+ window — legitimate, not fetch failure)
- Wide-format CSV column count == 1 (date) + 1074 (universe) + 1 (HS300) = 1076 — **enforced via post-pivot force-reindex per D5; tickers with zero rows still appear as all-NaN columns**
- Long-format CSV column count == 5 (date, ticker, amount_rmb, vol_shares, source)

## Coverage Acceptance

`CANDIDATE_DATASET` if ALL of:
- `ticker_coverage_pct >= 98.0`
- `min_trade_days_per_ticker >= 60`
- `median_implied_qfq_price_rmb ∈ [0.5, 5000]`
- All 5 ADTV-per-window computable ≥ 95%
- `benchmark_coverage_pct >= 99.0`
- `fetch_failures count ≤ 20`
- `extreme_pct_chg_anomalies ≤ 500` (audit-only style; only catches systematic corruption, not legitimate IPO/复牌 events)
- `tickers_with_no_qfq count ≤ 10` (very rare; usually new IPOs without full adj_factor history)
- `tickers_with_no_h52c_data count ≤ 60` (H52a historical members never traded in 2020+; forced into all-NaN columns to preserve 1076-column width)

`BLOCKED` otherwise — surface specific failing assertion with numerical value.

## Smoke Command

```bash
python scripts/h52c_build_csi500_daily_facts.py \
  --universe data/cn_pit/universe_h52a_csi500.jsonl \
  --start 20240601 --end 20240630 \
  --raw-dir /tmp/h52c_raw_smoke \
  --output-prices /tmp/h52c_prices.csv \
  --output-liquidity /tmp/h52c_liquidity.csv \
  --output-coverage /tmp/h52c_cov.json \
  --output-report /tmp/h52c_rep.md
```

Expected smoke result:
- Exits 0.
- Fetches ~21 trade_dates (June 2024) × 2 endpoints = 42 calls + 1 HS300 call.
- /tmp prices CSV has 1076 columns (verifies wide-format shape).
- /tmp liquidity CSV has 5 columns (verifies long-format shape).
- /tmp coverage JSON has provenance block with `universe_source` pointing at H52a.
- Median implied qfq price ∈ [0.5, 5000] RMB on the smoke sample (proves unit conversions).
- Does NOT touch `data/cn_pit/`, `reports/`, or any production path.

## Full Command

```bash
python scripts/h52c_build_csi500_daily_facts.py
```

~11 minutes pure network + Tushare overhead; total wall ~15-25 min depending on throttling.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h52c
python scripts/validate_hxx_artifacts.py                                # all 16 artifacts (15 existing + h52c) must PASS
pytest tests/test_h52c_build_csi500_daily_facts.py tests/test_validate_hxx_artifacts.py -q
git status --short \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/price_coverage_h47.json \
  data/cn_pit/liquidity_h51a_daily_amount.csv \
  data/cn_pit/liquidity_coverage_h51a.json \
  data/cn_pit/universe_h52a_csi500.jsonl \
  data/cn_pit/sector_metadata_h52b_csi500.csv \
  data/cn_pit/universe.jsonl \
  data/cn_pit/fundamentals.jsonl
# Above MUST print nothing — 8 protected files unchanged.

# Manual unit sanity check (inline command):
python3 -c "
import pandas as pd
df = pd.read_csv('data/cn_pit/prices_h52c_csi500_qfq.csv', index_col='date')
print(f'shape: {df.shape}')
print(f'columns: {len(df.columns)} (expect 1076 = 1074 + 1 HS300 + 1 date-as-index)')
# Spot-check: 600519.SH (茅台) is in CSI300 not CSI500, so NOT expected.
# Pick a known CSI500 ticker; e.g. 600104.SS 上汽集团 (often in CSI500 mid-cap range).
for t in ['600104.SS', '000725.SZ', '601020.SS']:
    if t in df.columns:
        sample = df[t].dropna().tail(5)
        print(f'  {t} last 5 closes:')
        print(sample)
        break
"
```

## Acceptance Gate

- [ ] All 4 outputs exist (script, prices CSV, liquidity CSV, coverage JSON, report).
- [ ] Coverage gates met (8 gates listed under Coverage Acceptance).
- [ ] Prices CSV schema matches H47 reference shape exactly (1 date col + 1074 ticker cols + 1 HS300 col).
- [ ] Liquidity CSV schema matches H51a reference (5 cols: date, ticker, amount_rmb, vol_shares, source).
- [ ] `amount_rmb` median implied price ∈ [0.5, 5000] RMB (units fixed; H51a V2 lesson).
- [ ] `vol_shares` is in absolute shares, not Tushare 手.
- [ ] H47, H51a, H52a, H52b files all untouched.
- [ ] `validate_h52c` registered and passing.
- [ ] All 16 family validators PASS.
- [ ] Tests cover: per-trade_date axis-flip fetch, qfq local computation (synthetic 5-day fixture: known adj_factor → known qfq), unit conversions (amount × 1000, vol × 100), universe filtering, NaN handling, ADTV per-window threshold enforcement.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Closure Note

- Append H52c row to `docs/strategy-optimization-sync.md` under new `## H52c — CSI500 Daily Fact Data Snapshot` heading: verdict, ticker_coverage_pct, median_implied_qfq_price_rmb, ADTV per-window summary, fetch_failures count, extreme_pct_chg_anomalies count.
- Flip `docs/agents/next-slices.md` H52c entry to DONE; state that H52d (PIT Fundamentals) is unblocked once H52c completes since H52a was the other dependency (already DONE).

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- Does the local qfq computation use a per-ticker terminal divisor (NOT a global terminal date)? If a ticker's last trade is 2023-12-31 and the dataset ends 2026-05-21, its `adj_factor_terminal` should be the 2023-12-31 value, not a 2026 value.
- Does the universe filter use H52a's unique-tickers set, NOT H30 universe? (regression risk after multiple prior briefs touched H30 paths)
- Are `amount × 1000` and `vol × 100` conversions applied at the SAME point in the pipeline (persist time, not raw cache write)? Raw cache should mirror Tushare's raw response for full auditability.
- Is the HS300 column actually populated (not all NaN)? The single `index_daily` bulk call is easy to forget if axis-flip-only mindset takes over.
- Does the 5-calls/sec rate cap counter span BOTH endpoints (daily + adj_factor) and the index_daily call?
- Are tests deterministic and free of network calls?
- Could `extreme_pct_chg_anomalies` legitimately exceed 50 (e.g., ChiNext / STAR Market ±20% limit days during 2020 epidemic volatility)? If so, the threshold may need to be raised; document the rationale.
