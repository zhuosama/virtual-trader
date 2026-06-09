# H52h — H52c Date Format Fix + H52e Smoke Re-Run

## Context

H52g diagnostic identified the root cause of H52f's `CSI500_REGRESSION` verdict: **H52c's prices and liquidity CSV files store dates as int64 `20200102` instead of ISO string `2020-01-02`**. `pd.to_datetime(int_value)` interprets ints as nanoseconds-since-epoch → all CSI500 prices land in 1970 → backtest finds 0 days in any 2024+ window → 0 trades → 0 candidates across H52e/H52f.

The fix is a one-line pandas transform applied to two CSV files; no Tushare re-fetch needed. H52c raw cache (3089 files in `data/cn_pit/raw/h52c_tushare_*/`) is unaffected because the bug is at the persistence step in `h52c_build_csi500_daily_facts.py`, not at the cache layer.

Per the user's option-C scope decision (2026-05-24), H52h bundles the data fix with an H52e smoke re-run as **proof the fix actually makes backtest data flow**. H52f re-run becomes a separate H52j slice (deferred — ~95-130 min wall, deserves its own brief).

## Objective

Convert H52c's two CSV files from int64 dates to ISO YYYY-MM-DD strings in place, update the H52c coverage JSON's sha256 field, strengthen `validate_h52c` with a date-format assertion (regression prevention), then re-run H52e smoke to verify backtest actually consumes real CSI500 data.

## Inputs

- `data/cn_pit/prices_h52c_csi500_qfq.csv` (will be rewritten in place)
- `data/cn_pit/liquidity_h52c_csi500_daily_amount.csv` (will be rewritten in place)
- `data/cn_pit/price_coverage_h52c.json` (sha256 field will be updated)
- `scripts/validate_hxx_artifacts.py` (`validate_h52c` strengthened)
- `tests/test_h52c_build_csi500_daily_facts.py` (add date-format regression test)
- `scripts/h52e_csi500_framework_smoke.py` (READ-ONLY; re-invoked in Phase 2)

## Outputs

- `scripts/h52h_csi500_date_format_fix.py` — the fix script (~150 LOC)
- Modified `data/cn_pit/prices_h52c_csi500_qfq.csv` (dates: int → ISO)
- Modified `data/cn_pit/liquidity_h52c_csi500_daily_amount.csv` (dates: int → ISO)
- Modified `data/cn_pit/price_coverage_h52c.json` (sha256 fields recomputed)
- Modified `scripts/validate_hxx_artifacts.py` (`validate_h52c` strengthened + `h52h` registered)
- Modified `tests/test_h52c_build_csi500_daily_facts.py` (1 new date-format test)
- New `data/cn_pit/h52h_fix_diagnostic.json` (before/after sha256 + row counts + sanity rechecks)
- New `reports/h52h_csi500_date_fix_report.md`
- New `tests/test_h52h_csi500_date_format_fix.py`
- Phase 2 (re-run H52e — overwrites existing paths):
  - `backtest/runs/fundamental_value_h52e_csi500_smoke_{h42,h50b,h51b}.json` regenerated
  - `reports/h52e_csi500_framework_smoke_report.md` regenerated

## Hard Prohibitions

- Do NOT re-fetch from Tushare — pure CSV transform. No network.
- Do NOT modify `scripts/h52c_build_csi500_daily_facts.py` source itself (fix lives in H52h script that operates on the OUTPUT artifacts; we don't need to re-run H52c ingestion). If user later wants the H52c script itself patched to prevent the bug, that's a separate H52c-V2 brief.
- Do NOT delete or rebuild `data/cn_pit/raw/h52c_tushare_*/` cache. The cache is correct (raw Tushare returns are int dates by Tushare convention); the bug was in how H52c persisted them. Cache stays for audit.
- Do NOT modify H30 prices/liquidity (`prices_h47_tushare_qfq_candidate.csv`, `liquidity_h51a_daily_amount.csv`). H30 stays untouched.
- Do NOT modify H52a/H52b/H52d artifacts.
- Do NOT modify H42/H49b/H50b/H51b scripts or `fundamental_backtest.py`.
- Do NOT modify H42/H48/H49b/H50b/H51b/H52f run JSONs (kept as historical reference — they ran on the broken data; sync doc annotates this).
- Do NOT loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT mark Phase 2 success unless H52e re-run's H50b sub-JSON shows `clean_deploy_count > 0` (proving the fix actually made backtest flow real data; the previous shallow SMOKE_PASS is no longer acceptable).
- Do NOT author commits as `codex` or `claude-code`.
- Do NOT delete `tests/test_h42_strategy_redesign_search.py` regression coverage; H52e re-run must still pass H42/H50b/H51b regression tests.

## Design Decisions

### D1. Fix Mechanism — Idempotent In-Place Transform

The fix script does the following sequence:

1. **Sanity-check current state**: read header + 3 rows of each CSV; assert date column is currently int-format (e.g., regex `^\d{8}$`). If already ISO format, log "already-ISO; skip" and exit 0 (idempotency).
2. **Atomic write**: for each of the 2 CSVs:
   - `df = pd.read_csv(target_path, dtype={'date': str})` — read date as string (avoids pandas auto-conversion ambiguity). Do NOT set `index_col` — date stays as a regular data column.
   - Convert: `df['date'] = pd.to_datetime(df['date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')`
   - **`df.to_csv(target_path.tmp, index=False)`** — `index=False` is MANDATORY. Pandas' default `to_csv()` prepends an unnamed integer index column (0, 1, 2, ...) as the first column, which would inflate the prices CSV from 1076 columns to 1077 and corrupt the long-format liquidity CSV's 5-column schema to 6 columns. Both corruptions trigger immediate `validate_h52c` failures (column-count assertions).
   - `os.replace(target_path.tmp, target_path)` (atomic on POSIX).
3. **Post-write column-count assertion** (mandatory, even though `validate_h52c` would catch it later — fail fast):
   - For prices CSV: assert exactly 1076 columns (1 date + 1074 universe + 1 HS300).
   - For liquidity CSV: assert exactly 5 columns (`date, ticker, amount_rmb, vol_shares, source`).
   - If column count mismatches, surface clearly and abort H52h — do NOT proceed to sha256 recomputation or Phase 2.
4. **Recompute sha256s**: for both CSVs, compute new sha256; update the `data_sources` section in `data/cn_pit/price_coverage_h52c.json` (the coverage JSON has 2 sha256 fields — `prices.sha256` and `liquidity.sha256` if it tracks ADTV; or whatever fields exist — match the EXACT field structure in the existing coverage JSON).
5. **Verification**: after fix, the date columns must satisfy regex `^\d{4}-\d{2}-\d{2}$` for ALL rows (not just sampled).

Idempotency requirement: re-running H52h script after fix must be a no-op (already-ISO detection skips the transform, exits 0).

### D2. `validate_h52c` Strengthening (regression prevention)

Add two assertions to `validate_h52c` in `scripts/validate_hxx_artifacts.py`:

```python
# After existing checks:

# Date-format assertion: prices CSV header row + 5 sample data rows
import pandas as pd, re
prices_path = ROOT / "data/cn_pit/prices_h52c_csi500_qfq.csv"
header = open(prices_path).readline().strip()
if not header.startswith("date,"):
    errors.append("h52c prices CSV missing date header")
df_sample = pd.read_csv(prices_path, nrows=5, dtype={'date': str})
iso_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
for v in df_sample['date']:
    if not iso_pattern.match(str(v)):
        errors.append(f"h52c prices CSV date column not ISO YYYY-MM-DD: got {v!r}")
        break

# Same for liquidity CSV (date column same position)
liq_path = ROOT / "data/cn_pit/liquidity_h52c_csi500_daily_amount.csv"
df_liq_sample = pd.read_csv(liq_path, nrows=5, dtype={'date': str})
for v in df_liq_sample['date']:
    if not iso_pattern.match(str(v)):
        errors.append(f"h52c liquidity CSV date column not ISO YYYY-MM-DD: got {v!r}")
        break
```

This catches the same bug if it ever reappears (e.g., from a future H52c-V2 re-ingestion that forgets the format conversion).

### D3. Test Coverage (H52h's own + regression for H52c)

- New `tests/test_h52h_csi500_date_format_fix.py`:
  - `test_fix_converts_int_to_iso_dates`: synthetic fixture with int dates → assert post-fix matches ISO regex
  - `test_fix_is_idempotent`: run fix twice → second run is no-op, sha256 unchanged
  - `test_fix_updates_coverage_sha256`: after fix, coverage JSON's prices.sha256 == file_sha256(prices_path)
- Add to existing `tests/test_h52c_build_csi500_daily_facts.py`:
  - `test_date_format_regression`: load actual H52c CSV; assert all dates match ISO regex — this protects against future ingestion bugs

### D4. Phase 2 — H52e Re-Run with Real-Data Assertion

After Phase 1 fix completes and `validate_h52c` passes with new sha256s, the harness invokes:

```bash
python scripts/h52e_csi500_framework_smoke.py
```

Smoke uses minimal params (1 overlay × 3 stage_b × top-k 1) — same as before. But the ACCEPTANCE for H52h Phase 2 is stricter than H52e's original:

- All 3 sub-JSONs exist (same as H52e)
- All 3 have verdict field
- **H50b sub-JSON's `clean_deploy_count > 0`** (NEW — proves backtest now consumes real CSI500 dates, vs the previous all-zero result from broken dates)
- **H51b sub-JSON's `rebalances_total > 0`** (NEW — proves the engine made at least one rebalance, vs the previous 0)
- Provenance sha256s match the NEW (post-fix) H52c sha256s

If Phase 2 fails (e.g., backtest still produces 0 clean_deploy), there's a deeper bug beyond date format — surface clearly and stop. Do NOT mark H52h success.

### D5. Sync Doc / next-slices Updates

`docs/strategy-optimization-sync.md`:
- Append H52h section: fix description + before/after sha256 + Phase 2 H52e re-run results
- Mark H52f's `CSI500_REGRESSION` verdict explicitly as **INVALIDATED by H52h** with a one-line cross-reference; do NOT delete the H52f section (historical reference).
- Mark H52e's original `SMOKE_PASS` as **stale (pre-H52h)** — note the Phase 2 H52e re-run produced the load-bearing smoke result.

`docs/agents/next-slices.md`:
- Flip H52h entry to DONE
- Add new H52j entry: "CSI500 H42→H51b Full Pipeline Re-Run (post-H52h)" as OPEN — it's H52f re-dispatched on the fixed data; brief reuses H52f's design + same harness; expected ~95-130 min wall

## Acceptance Gate

- [ ] H52h fix script exists; runs cleanly; idempotent.
- [ ] H52c prices + liquidity CSVs have ALL dates matching ISO `\d{4}-\d{2}-\d{2}` regex.
- [ ] H52c coverage JSON's sha256 fields match the actual new file sha256s.
- [ ] `validate_h52c` strengthened with the date-format assertion + still PASSes.
- [ ] All 21 family validators PASS (20 existing + h52h).
- [ ] H42 + H50b + H51b regression tests pass.
- [ ] H52e re-run completes (Phase 2); all 3 sub-JSONs exist.
- [ ] H50b sub-JSON has `clean_deploy_count > 0` (real data flow proof).
- [ ] H51b sub-JSON has `rebalances_total > 0` (real data flow proof).
- [ ] All 6 H52c-dependent provenance sha256s in the new H52e sub-JSONs match new H52c file sha256s.
- [ ] H52a/H52b/H52d artifacts untouched.
- [ ] H30 + H42/H48/H49b/H50b/H51b run JSONs untouched.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM findings.

## Smoke Command

```bash
python scripts/h52h_csi500_date_format_fix.py --dry-run --output-dir /tmp/h52h_smoke
```

Dry-run: detects current date format (int), simulates conversion, computes would-be sha256s, but does NOT write to data/ paths. /tmp only.

## Full Command

Sequence (both must succeed):
```bash
python scripts/h52h_csi500_date_format_fix.py             # Phase 1: fix
python scripts/h52e_csi500_framework_smoke.py             # Phase 2: re-run H52e
```

Or single command if H52h script orchestrates both:
```bash
python scripts/h52h_csi500_date_format_fix.py --with-h52e-rerun
```

Wall: ~1 min fix + ~12 sec H52e re-run = under 2 min total.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h52c
python scripts/validate_hxx_artifacts.py --artifact h52e
python scripts/validate_hxx_artifacts.py --artifact h52h
python scripts/validate_hxx_artifacts.py                                # all 21 must PASS
pytest tests/test_h52h_csi500_date_format_fix.py \
       tests/test_h52c_build_csi500_daily_facts.py \
       tests/test_validate_hxx_artifacts.py \
       tests/test_h42_strategy_redesign_search.py \
       tests/test_h50b_quality_value_search.py \
       tests/test_h51b_risk_model_search.py -q
git status --short \
  data/cn_pit/universe_h52a_csi500.jsonl \
  data/cn_pit/universe_snapshots_h52a_csi500.jsonl \
  data/cn_pit/sector_metadata_h52b_csi500.csv \
  data/cn_pit/fundamentals_h52d_csi500_pit_quality.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/liquidity_h51a_daily_amount.csv \
  scripts/h42_strategy_redesign_search.py \
  scripts/h50b_quality_value_search.py \
  scripts/h51b_risk_model_search.py \
  scripts/h52c_build_csi500_daily_facts.py \
  backtest/experiments/fundamental_backtest.py \
  backtest/runs/fundamental_value_h42_strategy_redesign_search.json \
  backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json \
  backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json \
  backtest/runs/fundamental_value_h50b_quality_value_search.json \
  backtest/runs/fundamental_value_h51b_risk_model_search.json
# Above MUST print nothing — 16 protected paths unchanged.

# Manual sanity (inline):
python3 -c "
import pandas as pd
for p in ['data/cn_pit/prices_h52c_csi500_qfq.csv', 'data/cn_pit/liquidity_h52c_csi500_daily_amount.csv']:
    df = pd.read_csv(p, nrows=3, dtype={'date': str})
    print(f'{p}: first dates = {df[\"date\"].tolist()}')
    assert all('-' in d for d in df['date']), f'date format check failed for {p}'
print('DATE FORMAT OK')
"
```

## Closure Note

- Append H52h row to `docs/strategy-optimization-sync.md` under new `## H52h — H52c Date Format Fix + H52e Re-Run Snapshot` heading: before/after sha256 + Phase 2 results (H50b clean_deploy_count + H51b rebalances_total).
- Add cross-reference notes:
  - To H52e section: "Original SMOKE_PASS was on broken dates; H52h Phase 2 re-run produced the load-bearing smoke verdict."
  - To H52f section: "CSI500_REGRESSION verdict INVALIDATED by H52g + H52h; re-run pending H52j."
- Flip `docs/agents/next-slices.md` H52h → DONE; add H52j entry as OPEN: "CSI500 H42→H51b Full Pipeline Re-Run (post-H52h date fix)".

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- **(HIGH fix verification)** Does every `df.to_csv(...)` call in the H52h script include `index=False`? Forgetting this inflates the prices CSV from 1076 → 1077 cols (unnamed integer index prepended) and corrupts the long-format liquidity CSV's 5-col schema. Verify by reading post-fix CSV headers: first column must be exactly `date` (no leading unnamed column).
- Does the post-write column-count assertion fire BEFORE sha256 recomputation, so a corrupted CSV doesn't propagate a wrong sha256 into the coverage JSON?
- Does the H52h fix script correctly handle BOTH CSVs (prices + liquidity), not just prices?
- Is the fix idempotent (re-running after fix produces no diff)?
- Does the fix use atomic write (temp file + os.replace) to avoid corrupting the CSV on partial failure?
- Does the coverage JSON sha256 update use the SAME field structure as the existing JSON (no schema drift)?
- Does the validate_h52c strengthening check ALL rows or just a sample? (Sampling 5 rows is OK; checking all is wasteful but correct.)
- Does Phase 2 H52e re-run assert `clean_deploy_count > 0` for H50b AND `rebalances_total > 0` for H51b — both are real-data-flow proofs?
- If Phase 2 fails despite the fix (e.g., 0 clean_deploy still), does the harness surface clearly that the bug is deeper than date format?
- Are tests deterministic and free of network calls?
