# H52a — CSI500 PIT Universe History

## Context
As documented in the H52 Universe Expansion Plan (`docs/h52-universe-expansion-plan-2026-05-24.md`), the H30 universe has been fully exhausted across all H45 PRD attack vectors (Parameter, Price, Sector Neutrality, Quality-Value, Risk Model). We are now expanding to the CSI500 (中证500) mid-cap universe to unlock new cross-sectional alpha.

H52a is the foundational slice. It builds the PIT (Point-In-Time) universe history for CSI500. All subsequent H52 slices will use this ticker pool.

## Objective
Fetch historical monthly constituent weights for the CSI500 index (`000905.SH`) from Tushare `index_weight`, covering `2019-01-01` to `2026-05-21`. Output a unified ticker list and a monthly PIT snapshot file.

## Inputs
- Tushare token (standard resolution chain: env → scripts/.tushare_token → ~/.tushare.token → agents/config.yaml → tushare built-in)
- Tushare endpoints: `index_weight` (membership weights per snapshot) + `trade_cal` (for month-end discovery)
- Index code: **`000905.SH`** — Tushare standard SSE-listed CSI500 index code. PINNED; do NOT substitute with `399905.SZ` (that is an SZSE-listed ETF tracking CSI500, NOT the index itself; calling index_weight with that code will fail or return wrong data).

## Outputs

- `scripts/h52a_build_csi500_universe.py`
- `data/cn_pit/universe_h52a_csi500.jsonl` — **PINNED schema, one row per (ticker, membership_interval), matches `universe_h30_candidate.jsonl` shape exactly** for downstream `CN_PIT_FileSource` compatibility. Same ticker re-joining CSI500 after a removal gets a NEW row (different effective_date/end_date interval).

  Each row:
  ```json
  {"ticker": "000001.SZ", "code": "000001", "name": "", "effective_date": "2019-01-31",
   "end_date": "", "source_url": "https://tushare.pro/document/2?doc_id=96",
   "ingested_at": "<UTC ISO>", "index_code": "000905.SH",
   "weight": <latest_weight_in_this_interval_or_avg>, "source_provider": "tushare:index_weight",
   "snapshot_count": <count_of_snapshots_in_which_this_ticker_appeared>}
  ```
  Notes: `effective_date` = first snapshot ticker appears in (after any prior exit); `end_date` = "" if still member at window end, else first snapshot date after the ticker disappeared. `weight` = latest known weight within the interval (or simple average — pin choice in script, document in report).

- `data/cn_pit/universe_snapshots_h52a_csi500.jsonl` — **PINNED schema, one row per (snapshot_date, ticker), matches `universe_snapshots_h30_candidate.jsonl` shape exactly**.

  Each row:
  ```json
  {"index_code": "000905.SH", "con_code": "000001.SZ", "code": "000001",
   "ticker": "000001.SZ", "trade_date": "2019-01-31", "weight": 0.234,
   "source_provider": "tushare:index_weight",
   "source_url": "https://tushare.pro/document/2?doc_id=96",
   "ingested_at": "<UTC ISO>", "source_row": <int>}
  ```
  `con_code` is the Tushare-native ts_code (string equal to ticker but explicitly preserved as audit field; mirrors H30 H30 schema).

- `data/cn_pit/universe_coverage_h52a.json` — coverage report (see Provenance Block section).
- `reports/h52a_csi500_universe_report.md`
- `tests/test_h52a_build_csi500_universe.py`
- `scripts/validate_hxx_artifacts.py` — register `h52a` with `validate_h52a` (assertions listed under Provenance Block).
- Raw cache at `data/cn_pit/raw/h52a_tushare_index_weight/<snapshot_date>.csv` (one file per month-end). Add `data/cn_pit/raw/h52a_tushare_index_weight/` to `.gitignore` BEFORE the full run.

## Hard Prohibitions
- Do NOT modify the **SHA256-protected H28 baseline files**: `data/cn_pit/universe.jsonl`, `data/cn_pit/universe_snapshots.jsonl`, `data/cn_pit/fundamentals.jsonl`. These are immutable per project memory (memory notes their sha256 anchors).
- Do NOT modify the **H30 universe files**: `data/cn_pit/universe_h30_candidate.jsonl`, `data/cn_pit/universe_snapshots_h30_candidate.jsonl` — H30 entire pipeline (H42/H48/H49b/H50b/H51b) depends on these stable.
- Do NOT modify any prior Hxx data artifact (H47 prices, H49a sectors, H50a fundamentals, H51a liquidity).
- Do NOT fetch tickers OUTSIDE the CSI500 index_weight response. The whole point of axis-flip is "Tushare returns only the CSI500 constituents per snapshot date" — do not paginate to other tickers.
- Do not modify production trading config.
- Do not place live orders.
- Do not print or store the Tushare token.
- Do not author commits as `codex` or `claude-code`.
- Do not loosen `tests/test_validate_hxx_artifacts.py`.

## Design Decisions

### D1. Axis-Flip Ingestion
Query `index_weight` by `index_code='000905.SH'` and `trade_date`. Do NOT loop over individual tickers.
1. Fetch all CN A-share trading days via Tushare `trade_cal(exchange='SSE', is_open=1, start_date='20190101', end_date='20260521')` — **1 call**.
2. Filter the trade calendar for **last trading day of each month** (resilient to month-ends that fall on weekends/holidays; pick the latest `is_open=1` day with `cal_date.month == M`).
3. For each month-end trade_date, call `pro.index_weight(index_code='000905.SH', trade_date=YYYYMMDD)` — **~89 calls** for ~89 months (2019-01 → 2026-05).
4. **Total: ~90 API calls** (1 trade_cal + 89 index_weight). At 5 calls/sec hard cap → ~18 seconds pure network + Tushare server overhead. Resumable: cache each month's response at `data/cn_pit/raw/h52a_tushare_index_weight/<snapshot_date>.csv`.

### D2. Ticker Normalization (both formats persisted)
- `ticker` field uses Yahoo format (`000001.SZ`, `600000.SS`) for downstream backtester join compatibility.
- `con_code` field in snapshots file persists the Tushare-native `ts_code` (which for A-shares happens to equal the Yahoo format, but the field is preserved verbatim from Tushare response for audit). Mirrors H30 schema.
- Universe-level file: `code` field = numeric-only ticker (`000001`) for legacy code-only joins; `ticker` = Yahoo format.

### D3. Membership-Interval Reconstruction
A ticker may exit CSI500 and rejoin later (typical for borderline-market-cap names). The universe file must capture this:
1. Pass 1: for each ticker, gather all (snapshot_date, weight) pairs where it appeared.
2. Pass 2: detect contiguous membership intervals — a ticker present in snapshots `t_i`, `t_{i+1}`, ..., `t_j` but absent at `t_{j+1}` closes one interval; if it reappears at `t_k` (k > j+1), opens a NEW interval row.
3. Each interval row gets its own `effective_date` (first snapshot in interval) and `end_date` (first snapshot date the ticker disappeared, or "" if still member at last snapshot).
4. `weight` per interval = mean of weights across snapshots in the interval (document this choice in the report).
5. `snapshot_count` per interval = number of snapshots the ticker appeared in within that interval.

### D4. Data Quality Assertions (fail-loud during ingestion)
- Per snapshot: assert `len(response) >= 480` (allow ~4% tolerance below the nominal 500; if a snapshot returns fewer, log to `fetch_failures` and re-attempt up to 2 times before recording as quality anomaly).
- Per snapshot: assert no `NaN` weights for present tickers. NaN → record in `data_quality_anomalies` with `(ticker, snapshot_date, raw_weight)`; do NOT silently drop.
- Empty response (0 rows): treat as Tushare hiccup. Retry once; if still empty, add to `fetch_failures` and SKIP that month — do NOT abort the whole run.
- All `trade_date` values must parse as YYYYMMDD and fall within the requested window. Out-of-range → abort.

### D5. Rate-Limit / Retry Policy
- Per-snapshot raw cache: skip the API call if the cache file exists and is non-empty.
- HTTP 429 / Tushare error_code → exponential backoff with jitter (initial 2s, doubling, 60s cap, max 5 retries per snapshot) → then add to `fetch_failures`.
- Hard-cap base rate at 5 calls/sec across all calls.
- Per-snapshot failure NEVER aborts the run; just adds `{snapshot_date, reason}` to `fetch_failures`.

## Provenance Block (in `universe_coverage_h52a.json`; validate_h52a enforces)

```json
{
  "provenance": {
    "provider": "tushare:index_weight",
    "index_code": "000905.SH",
    "endpoints_used": ["index_weight", "trade_cal"],
    "snapshot_date_range": "2019-01-31 → 2026-04-30",
    "snapshot_cadence": "monthly_last_trading_day",
    "snapshot_timestamp": "<UTC ISO at run time>"
  },
  "total_snapshots": <int, expect ~89>,
  "unique_tickers_count": <int, expect ~700-900 due to turnover>,
  "membership_intervals_count": <int, expect ~900-1200>,
  "avg_members_per_snapshot": <float, expect ~500>,
  "min_members_per_snapshot": <int, expect >= 480>,
  "fetch_failures": [{"snapshot_date": "...", "reason": "..."}],
  "data_quality_anomalies": [{"ticker": "...", "snapshot_date": "...", "raw_weight": null}],
  "verdict": "CANDIDATE_DATASET | BLOCKED"
}
```

`validate_h52a` asserts:
- `provenance.provider == "tushare:index_weight"`.
- `provenance.index_code == "000905.SH"`.
- `total_snapshots >= 80` (allow ~10% tolerance below ~89 expected).
- `min_members_per_snapshot >= 480`.
- `len(fetch_failures) <= 5` (allow rare Tushare hiccups).
- Universe file row count matches `membership_intervals_count`.
- Snapshots file row count == sum across snapshots of members present.

## Acceptance Gate (Coverage)

- `CANDIDATE_DATASET` if ALL of:
  - `provenance.snapshot_date_range` covers at least `2019-01-31 → 2026-04-30`.
  - `total_snapshots >= 80`.
  - `min_members_per_snapshot >= 480` AND `avg_members_per_snapshot >= 490`.
  - `len(fetch_failures) <= 5`.
  - `unique_tickers_count >= 700` (sanity: CSI500 turns over substantially over 6+ years; if <700, ingestion missed something).
  - Schema of both jsonl files matches the H30 reference shape exactly (validated structurally).
- `BLOCKED` otherwise — surface specific failing assertion(s) with numerical values.

## Commands

**Smoke Command:**
```bash
python scripts/h52a_build_csi500_universe.py \
  --start 20240101 --end 20240331 \
  --raw-dir /tmp/h52a_raw_smoke \
  --output-universe /tmp/h52a_uni.jsonl \
  --output-snapshots /tmp/h52a_snap.jsonl \
  --output-coverage /tmp/h52a_cov.json \
  --output-report /tmp/h52a_rep.md
```
Expected smoke result:
- Exits 0.
- Writes 3 month-end snapshots (Jan/Feb/Mar 2024) under /tmp/.
- Each snapshot has >= 480 members.
- universe file has membership_intervals_count > 0.
- Coverage JSON's `provenance.snapshot_cadence == "monthly_last_trading_day"`.
- Does NOT touch `data/cn_pit/`, `reports/`, or any production path.

**Full Command:**
```bash
python scripts/h52a_build_csi500_universe.py
```
~18 seconds pure network + Tushare overhead; total wall ~1-3 minutes.

**Verification:**
```bash
python scripts/validate_hxx_artifacts.py --artifact h52a
python scripts/validate_hxx_artifacts.py                              # all 14 artifacts (13 existing + h52a) must PASS
pytest tests/test_h52a_build_csi500_universe.py tests/test_validate_hxx_artifacts.py -q
git status --short \
  data/cn_pit/universe.jsonl \
  data/cn_pit/universe_snapshots.jsonl \
  data/cn_pit/fundamentals.jsonl \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/universe_snapshots_h30_candidate.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/sector_metadata_sw_l1.csv \
  data/cn_pit/fundamentals_h50a_pit_quality.jsonl \
  data/cn_pit/liquidity_h51a_daily_amount.csv
# Last command MUST print nothing — all 9 protected files unchanged.
```

## Closure Note
- Append a one-line H52a row to `docs/strategy-optimization-sync.md` under a new `## H52a — CSI500 PIT Universe History Snapshot` heading: verdict, total_snapshots, unique_tickers_count, membership_intervals_count, fetch_failures count.
- Flip `docs/agents/next-slices.md` H52a entry from `OPEN` to `DONE`; flip H52b entry from `BLOCKED` to `OPEN`.
