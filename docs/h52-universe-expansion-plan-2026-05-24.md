# H52 Universe Expansion Plan (CSI500)

**Date**: 2026-05-24
**Objective**: Expand from the exhausted H30 (HS300) universe to the CSI500 mid-cap universe to unlock new cross-sectional factor alpha space (Quality/Value). 
**Key Enabler**: Utilize **Axis-Flip Bulk Ingestion** to compress the data ingestion schedule from 4-6 weeks to 4-6 days.

## Architectural Shift: Axis-Flip Ingestion

Previous ingestions (H38, H50a) looped over tickers `for ticker in universe: fetch(ticker, start_date, end_date)`. For a 500-ticker universe across multiple endpoints, this results in tens of thousands of API calls, hitting Tushare rate limits (429) and taking weeks.

**Axis-Flip** reverses the query driver from `ticker` to `time_index`:
- We loop over time (e.g., `for date in trade_dates: fetch(date)`).
- Tushare bulk endpoints return the entire A-share market for a given date/period in a single call.
- We then filter the resulting DataFrame locally against our CSI500 universe pool.
- **Result**: API calls drop from `O(Tickers)` to `O(Time Indices)`, avoiding rate limits and finishing in minutes/hours.

## Execution Slices (H52a — H52f)

| Slice | Description | Mechanism & Axis-Flip Target | Est. Time |
|-------|-------------|------------------------------|-----------|
| **H52a** | CSI500 PIT Universe History | Bulk fetch `index_weight` monthly snapshots for CSI500 (index code `000905.SH` or `399905.SZ`). Axis flip: query by `trade_date` (month ends). Defines the canonical ticker pool for all subsequent slices. | 0.5 day |
| **H52b** | CSI500 Sector Metadata (SW L1) | Bulk fetch SW L1 classification for the H52a universe pool. One-time snapshot (similar to H49a). | 0.5 day |
| **H52c** | CSI500 Daily Fact Data (Prices + ADTV) | **Two-endpoint axis flip per `trade_date`** (~1500 days × 2 = ~3000 calls): `daily` returns raw (不复权) close + vol + amount; `adj_factor` returns daily复权因子. Local join → compute qfq close in script. Daily-endpoint `amount` field is also persisted as ADTV (H51a-style). NOT one-pass: `daily` alone does NOT return qfq-adjusted prices. | 0.5–1 day |
| **H52d** | CSI500 PIT Fundamentals | Bulk fetch `fina_indicator`, `income`, `balancesheet`, `cashflow`. Axis flip: query by `period` (26 quarter-ends). Per-period response may still contain multiple `(ts_code, end_date)` rows due to update_flag / ann_date restatements; **the H50a V2 sort + drop_duplicates(keep='last') + assert + join 5-step pipeline still applies after axis-flipped fetch** — axis flip changes the query shape, not the dedup semantics. | 0.5 day |
| **H52e** | Search Framework Smoke Test | Load the new H52a-d datasets into the existing H42/H50b/H51b search scripts with **ZERO** logic changes to verify schema compatibility and pipeline health. | 0.5 day |
| **H52f** | H42 → H51b Full Pipeline Rerun | Execute the Parameter, Sector Neutral, Quality-Value, and Risk Model searches on the new CSI500 dataset. **Acceptance gate stays HS300-only** (H42 verbatim, beat_HS300_windows ≥ threshold). `beat_CSI500_windows` is persisted as a **diagnostic-only field** in the run JSON (does NOT affect verdict) to track whether the strategy at least beats its own selection universe. | 2-3 days |

**Total Estimated Duration**: ~4-6 days (compared to original 4-6 weeks).

## Parallel Monitoring (Path C)

While the H52 ingestion and search pipeline executes, the legacy H30 universe's best candidates (from H49b and H50b) will continue their 60-day out-of-sample forward observation via the H46 Paper Monitor. This ensures zero wasted time and preserves the H30 baseline.
