# H51a V2 — Units Fix + Per-Window ADTV Breakdown

## Context

H51a V1 closed `CANDIDATE_DATASET` with 4/4 gates PASS, but post-dispatch audit found two latent issues:

1. **Units bug (critical)**: column `amount_rmb` is misnamed — it actually carries Tushare `daily.amount` raw values which are in **千元 (thousands of RMB)** per Tushare doc. Verified on 000001.SZ (Ping An Bank): persisted `amount_rmb=1075742`, `vol_shares=115836645` → implied avg price = 0.0093 RMB, vs actual ~10 RMB/share. The column is off by a factor of 1000. H51b's ADTV 5%/10% cap would be inflated by 1000×, rendering the liquidity constraint meaningless.

2. **Brief履约 gap**: V1 brief required per-window ADTV computability breakdown across H42 windows (cal_2024 / h1_2025 / h2_2025 / ytd_2026 / deploy_2025_2026); V1 only surfaced the aggregate gate. Without per-window numbers, we don't know if any single backtest window has degraded coverage.

V2 fixes both surgically: it re-derives the H51a outputs from the existing raw cache (no network re-fetch), with the units corrected and the per-window breakdown surfaced.

## Objective

Re-derive `data/cn_pit/liquidity_h51a_daily_amount.csv`, `liquidity_coverage_h51a.json`, and the report from the existing raw cache, with:
- `amount_rmb` column = Tushare `daily.amount × 1000` (actual RMB).
- New `adtv_computability_per_window` field in coverage JSON.
- Strengthened `validate_h51a` asserting both fixes.

No new network calls. No raw cache rebuilds.

## Inputs

- `data/cn_pit/raw/h51a_tushare_daily/<ts_code>.csv` — V1 raw cache (481 files; PRESERVE as-is).
- `data/cn_pit/universe_h30_candidate.jsonl`
- `scripts/h51a_build_tushare_daily_amount.py` — V1 script; V2 EDITS it (additive fix on the persist path; do NOT touch fetch logic).
- `data/cn_pit/liquidity_h51a_daily_amount.csv` — V1 output; V2 overwrites (units fixed).
- `data/cn_pit/liquidity_coverage_h51a.json` — V1 output; V2 overwrites (per-window added).
- `reports/h51a_daily_amount_ingestion_report.md` — V1 output; V2 overwrites.
- `scripts/validate_hxx_artifacts.py` — V2 strengthens `validate_h51a`.
- `tests/test_h51a_build_tushare_daily_amount.py` — V2 adds two tests.

## Outputs

All paths same as V1; V2 overwrites with corrected data. No new file paths.

## Hard Prohibitions

- Do NOT delete, rebuild, or modify `data/cn_pit/raw/h51a_tushare_daily/` (481 V1 cache files; immutable record of V1 fetch state).
- Do NOT make any network call. V2 is a re-derivation from cache only.
- Do NOT modify `data/cn_pit/universe_h30_candidate.jsonl`, `liquidity_h33_daily_amount.csv`, `liquidity_h40_h39_candidate_daily_amount.csv`.
- Do NOT touch the fetch logic in `scripts/h51a_build_tushare_daily_amount.py` (the Tushare call path, retry/backoff, cache write). Edit ONLY the persistence layer where the raw `amount` column becomes the output `amount_rmb` column.
- Do NOT modify production trading config; do not place live orders.
- Do NOT print or store the Tushare token.
- Do NOT author commits as `codex` or `claude-code`.
- Do NOT loosen `tests/test_validate_hxx_artifacts.py`.

## Required Edits

### Edit 1 — Script persist path (the units fix)

In `scripts/h51a_build_tushare_daily_amount.py`, locate where the Tushare daily DataFrame's `amount` column is mapped to the output `amount_rmb`. Apply:

```python
# Tushare daily.amount unit per docs: 千元 (thousand RMB).
# Convert to absolute RMB at persistence time.
df['amount_rmb'] = df['amount'].astype(float) * 1000.0
```

Add a one-line comment explaining the unit conversion. Preserve all other column derivations (vol_shares = vol × 100 stays).

### Edit 2 — Coverage JSON `adtv_computability_per_window` field

In the coverage-computation path, compute and emit:

```json
{
  "adtv_computability_per_window": {
    "cal_2024":         {"start": "2024-01-01", "end": "2024-12-31",  "ticker_date_pairs": <int>, "computable_pct": <float>},
    "h1_2025":          {"start": "2025-01-01", "end": "2025-06-30",  "ticker_date_pairs": <int>, "computable_pct": <float>},
    "h2_2025":          {"start": "2025-07-01", "end": "2025-12-31",  "ticker_date_pairs": <int>, "computable_pct": <float>},
    "ytd_2026":         {"start": "2026-01-01", "end": "2026-05-21",  "ticker_date_pairs": <int>, "computable_pct": <float>},
    "deploy_2025_2026": {"start": "2025-01-01", "end": "2026-05-21",  "ticker_date_pairs": <int>, "computable_pct": <float>}
  }
}
```

For each window: count (ticker, eval_date) pairs where a 20-day trailing ADTV (exclusive of eval_date) can be computed from the H51a data. `computable_pct = computable_pairs / total_eligible_pairs * 100`.

The existing aggregate gate `adtv_computable_ge_95pct` stays; ADD a stronger per-window gate `adtv_computable_per_window_ge_95pct` that asserts ALL 5 windows are ≥ 95%.

### Edit 3 — `validate_h51a` strengthens

Add two assertions to `validate_h51a` in `scripts/validate_hxx_artifacts.py`:

```python
# Unit sanity: implied avg price = amount_rmb / vol_shares should be in [0.5, 5000] RMB
# Sample 100 non-NULL rows; if median implied price < 1.0 or > 5000.0, fail with diagnostic.
# (Median is robust to outliers; ST stocks and a few large-caps live at the extremes.)

# Per-window coverage: every window in adtv_computability_per_window must have computable_pct >= 95.
```

### Edit 4 — Tests

Add to `tests/test_h51a_build_tushare_daily_amount.py`:

1. `test_amount_rmb_unit_conversion`: synthesizes a Tushare-shaped DataFrame with `amount=1234.5` and asserts the persisted `amount_rmb == 1234500.0`.
2. `test_amount_rmb_implied_price_sanity`: loads the actual CSV (or a fixture); asserts median of `amount_rmb / vol_shares` falls in [0.5, 5000] RMB for non-NULL rows.

Both tests are deterministic, no network.

## Smoke Command

```bash
python scripts/h51a_build_tushare_daily_amount.py --rederive-only --output-csv /tmp/h51a_v2_smoke.csv --output-coverage /tmp/h51a_v2_cov_smoke.json --output-report /tmp/h51a_v2_rep_smoke.md
```

(If a `--rederive-only` flag doesn't exist in V1, add it: skips network entirely, reads only from raw cache, writes to specified output paths.)

Expected smoke result:
- Exits 0.
- /tmp CSV's median implied price falls in [0.5, 5000] RMB (proves units fix).
- /tmp coverage JSON has `adtv_computability_per_window` with all 5 windows present.
- Does NOT touch `data/cn_pit/` or `reports/`.

## Full Command

```bash
python scripts/h51a_build_tushare_daily_amount.py --rederive-only
```

Re-derives from cache to the canonical output paths. Wall clock: ~30-60 seconds (no network).

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h51a
python scripts/validate_hxx_artifacts.py                           # 12/12 family must still pass
pytest tests/test_h51a_build_tushare_daily_amount.py tests/test_validate_hxx_artifacts.py -q
git status --short \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/liquidity_h33_daily_amount.csv \
  data/cn_pit/liquidity_h40_h39_candidate_daily_amount.csv \
  data/cn_pit/raw/h51a_tushare_daily/                              # 4-path check; must print nothing
# Unit sanity (manual quick check):
python3 -c "
import csv
prices = []
for row in csv.DictReader(open('data/cn_pit/liquidity_h51a_daily_amount.csv')):
    vs = float(row['vol_shares'])
    ar = float(row['amount_rmb'])
    if vs > 0:
        prices.append(ar/vs)
prices.sort()
median = prices[len(prices)//2]
print(f'sample N={len(prices)}, median implied price = {median:.2f} RMB')
assert 0.5 < median < 5000, f'units bug not fixed: median {median}'
print('UNITS OK')
"
```

Required outcomes:
- Validator [PASS] h51a (with strengthened assertions).
- 12/12 family pass.
- Manual unit check prints "UNITS OK" with median price in 5-100 RMB range (typical for HS300 stocks).
- Cache dir + h33/h40 + universe all untouched.

## Acceptance Gate

- [ ] `amount_rmb` median implied price in [0.5, 5000] RMB (units fixed).
- [ ] `adtv_computability_per_window` present with all 5 H42 windows; each ≥ 95%.
- [ ] V1 raw cache (481 files) untouched.
- [ ] Validator + tests all green; 12/12 family pass.
- [ ] No network call during V2 run.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Closure Note

- Append a one-line entry under the existing H51a section in `docs/strategy-optimization-sync.md`: "H51a V2 (2026-05-23): fixed `amount_rmb` unit bug (×1000 from Tushare 千元) and added per-window ADTV breakdown. No re-fetch."
- Do NOT change `docs/agents/next-slices.md` H51a status (was DONE; still DONE).
- H51b dispatch can proceed once V2 closes.
