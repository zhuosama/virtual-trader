# OHLV Supplemental Data Layer — Engine PR Plan

Date: 2026-05-30 (drafted) / 2026-05-31 (executed)
Status: EXECUTED 2026-05-31 — 6/7 Tasks done + per-ticker silent-drop fix complete. Task 6 smoke (limit-tickers 10 × 1 month): post-fix smoke re-run shows YFinance fallback delivered 200/200 rows (Akshare was network-flaky, RemoteDisconnected for 10/10 tickers — provider chain saved the day, confirming fallback design). All 5 baseline-frozen file shas守门 unchanged across both smoke runs. 229 unit tests pass post-fix (224 + 5 new per-ticker failure tests). Per-ticker fix touched 4 providers (Akshare/BaoStock/Tushare/YFinance) — all had identical silent-swallow anti-pattern + `sources_used` provenance forgery. Local-use scope (no remote PR per user 2026-05-30). **Engine ready for H53** (gtja191 full zoo IC bench on OHLV-unlocked data); H53 brief drafted next.
Charter: `docs/research-charter-v1.md` (v1.0-DRAFT) — this PR is **§4 Kill Criterion 2 engine PR**, does NOT consume slice budget
Plan ID: ENGINE-OHLV-V1 (no Hxx number per Charter §4)
Parent spec: `docs/superpowers/specs/2026-05-30-vibe-trading-borrow-plan.md` § 5.3
Predecessor PRs / Spikes:
- ENGINE-LOADER-V1 (`2026-05-30-loader-registry-engine-pr.md`) — DONE; provides Provider registry + LoaderBlockedError + sha256 hook
- A1 spike — SIGNAL_NEGATIVE (H47 close-only blocks 90% gtja191)
- Close-only spike — SIGNAL_NEGATIVE (single-factor IR ceiling 0.25)
- R1 reverse-composite spike — SIGNAL_IMPROVED_BUT_INSUFFICIENT (composite IR 0.27 = 99.5% theoretical max)

Worker scope: ALLOWED — engine/loader edits per Charter §4. FORBIDDEN — touching strategy artifacts, Charter §2 frozen `prices_h47_tushare_qfq_candidate.csv` (must keep sha `34f3e38f...`).

> **Why this PR exists.** Three spikes proved: (a) gtja191/alpha101 zoo is 90% multi-column → uncomputable on H47 close-only matrix; (b) close-only single factors max out at IR ≈ 0.25; (c) close-only composite hits theoretical ceiling at IR ≈ 0.27. Re-opening Charter §5 hypothesis #4 (cross-sectional composite rank) requires OHLV columns. This PR adds an OHLV supplemental data layer **without touching the H47 close-only frozen layer** — they coexist.

> **For Hermes (worker)**: This plan is checkbox-tracked. Implement task-by-task. Do NOT batch. Each task has independent acceptance — run it before moving on. STOP and surface any plan-vs-reality gap; do NOT silently work around (cf. A2 PR plan had 3 literal GAPs — Hermes correctly BLOCKED on Task 6.2 last time).

---

## 0. Goal

Add a **supplemental OHLV daily-bar layer** under `data/cn_pit/ohlv_h47_supplement.csv` (long-format), populated through the existing A2 Provider registry (Tushare → Akshare → YFinance fallback chain), with full provenance + sha256 audit + unit tests. The close-only H47 matrix stays untouched and authoritative for close-based research; the OHLV layer joins on (date, ticker) for any factor needing open/high/low/volume/amount.

## 1. Scope

### 1.1 In scope

- Extend `MarketDataProvider` base class in `backtest/market_data.py` with a `get_ohlcv()` method (default `NotImplementedError`)
- Implement `get_ohlcv()` for 4 concrete providers (Tushare/Akshare/BaoStock/YFinance) leveraging existing precheck + LoaderBlockedError + sha256 hook from A2 PR
- Create `scripts/ingest_cn_pit_ohlv.py` (new script, ~300-500 LOC, modeled on `ingest_cn_pit_data.py` but scoped to OHLV only)
- Create `data/cn_pit/ohlv_h47_supplement.csv` (long format: `date,ticker,open,high,low,volume,amount`)
- Extend `data/cn_pit/metadata.json` with a new `ohlv_layer` top-level key (sha256 + provider chain + fetch timestamp); DO NOT modify other existing keys
- Add unit tests: `tests/audit_layer/test_ohlv_provider.py`
- Update `docs/h38_price_source_policy.md` with an OHLV section

### 1.2 Out of scope (split if needed)

- Refactoring `prices_h47_tushare_qfq_candidate.csv` (= H47 frozen, immutable per Charter §2)
- Modifying any close-based factor or backtest code
- Building backtest/factors/ directory (= H53 territory after PR merges)
- Real-time / intraday OHLV (= future work)
- Rerunning gtja191 IC bench (= post-merge spike per parent spec § 5.3)
- mootdx provider integration (= separate PR; was deferred from A2)

## 2. Current State Inventory

After ENGINE-LOADER-V1 merge (local):
- `backtest/market_data.py` has `MarketDataProvider` base + 4 concrete providers (Tushare/Akshare/BaoStock/YFinance) + Static/Cached/Fallback
- All providers expose `get_close_prices(tickers, start, end) -> ProviderResult` (close-only path)
- All providers have `precheck()` (raises `LoaderBlockedError` on missing token/library)
- `FallbackMarketDataProvider` rewires precheck + STOP-on-exhausted semantics
- `SOURCE_PROVIDERS` enum + `__init_subclass__` enforcement
- `ChecksumMismatchError` + `compute_prices_sha256()` helpers exist

This PR EXTENDS the same provider classes with a parallel `get_ohlcv()` method. The two return modes (close-only vs OHLV) are independent — old callers (backtest/ingestion close path) keep working unchanged.

## 3. Hard Prohibitions — Always Applicable

(Copy from `AGENTS.md` § Hard Prohibitions — verbatim. Quote here so executor reads them with the plan.)

- **No data fabrication**: never invent rows in any protected artifact under `data/cn_pit/`.
- **No source provenance forgery**: write actual provider name to metadata, not aspirational primary.
- **Symmetric restore**: try/finally on any temp patches.
- **Original ingestion verdicts immutable**: do NOT modify `prices_h47_tushare_qfq_candidate.csv`, `prices.csv`, `universe.jsonl`, `fundamentals.jsonl`, `universe_snapshots.jsonl`, or any existing key in `metadata.json`.
- **Exit-code is not acceptance**: every AG verifiable.
- **Modification reporting**: final response enumerates every file created/modified.
- **No silent workarounds**: gap between plan and reality → STOP + BLOCKED.
- **sha256 audit hooks**: pre + post for any data mutation.

### 3.1 Task-Specific Prohibitions

- Do NOT modify `prices_h47_tushare_qfq_candidate.csv` — Charter §2 frozen, sha must stay `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc`
- Do NOT modify `prices.csv` — H28 baseline, sha must stay `5efc8ec7ef4a6b7064010e67e0a9b9fdad77ca1c8d6cc907e47532738dc1a50c`
- Do NOT modify `universe.jsonl`, `fundamentals.jsonl`, `universe_snapshots.jsonl`
- Do NOT modify any existing key in `data/cn_pit/metadata.json` — only ADD the new `ohlv_layer` key
- Do NOT modify `agents/`, `strategies/active.json`, `backtest/backtest_engine.py`, `backtest/oos_window.py`
- Do NOT install pip packages (numpy/pandas/existing provider libs only)
- Do NOT proceed to next Task until current Task's acceptance passes
- Do NOT rename existing `get_close_prices` method or change `ProviderResult` dataclass field order

## 4. Task Breakdown (checkbox-tracked)

### Task 1 — `OHLCVResult` Dataclass + Base Method

**Files**: `backtest/market_data.py`

**Subtasks**:

- [ ] **1.1** Add new dataclass at module level (next to existing `ProviderResult`):
  ```python
  @dataclass
  class OHLCVResult:
      status: str                           # "OK" | "INFRA_ERROR"
      ohlcv: pd.DataFrame                   # long format: cols = [date, ticker, open, high, low, volume, amount]
      sources_tried: List[str] = field(default_factory=list)
      sources_used: Dict[str, str] = field(default_factory=dict)
      missing_pairs: List[tuple] = field(default_factory=list)   # (date, ticker) pairs with NaN
      fallback_chain: List[str] = field(default_factory=list)
      fallback_reason: Optional[str] = None
      precheck_log: List[str] = field(default_factory=list)
      sha256: Optional[str] = None          # sha256 of the ohlcv DataFrame
      adjustment: str = "qfq"
      reason: Optional[str] = None
  ```

- [ ] **1.2** Add abstract method on `MarketDataProvider`:
  ```python
  def get_ohlcv(self, tickers: Iterable[str], start: str, end: str) -> OHLCVResult:
      """Return long-format OHLCV for (tickers, [start, end]).
      Default: NotImplementedError. Subclasses override if they support OHLCV."""
      raise NotImplementedError(f"{self.name} does not implement get_ohlcv")
  ```

- [ ] **1.3** Add helper `compute_ohlcv_sha256(ohlcv: pd.DataFrame) -> str`:
  ```python
  def compute_ohlcv_sha256(ohlcv: pd.DataFrame) -> str:
      """Stable sha256 over a long-format OHLCV DataFrame. Sorts by (date, ticker) before hashing."""
      import hashlib
      sorted_df = ohlcv.sort_values(["date", "ticker"]).reset_index(drop=True)
      payload = sorted_df.to_csv(index=False).encode("utf-8")
      return hashlib.sha256(payload).hexdigest()
  ```

**Acceptance**:
```bash
python3 -c "from backtest.market_data import OHLCVResult, MarketDataProvider, compute_ohlcv_sha256; \
    import pandas as pd; \
    df = pd.DataFrame({'date': ['2026-01-01'], 'ticker': ['000001.SZ'], 'open': [10.0], 'high': [10.5], 'low': [9.5], 'volume': [1000], 'amount': [10250]}); \
    h = compute_ohlcv_sha256(df); print('OK', h[:12])"
# Expected: OK <12-char hex prefix>
```

---

### Task 2 — Provider get_ohlcv Implementations

**Files**: `backtest/market_data.py`

**Subtasks**:

- [ ] **2.1** Implement `TushareProvider.get_ohlcv()` using Tushare `daily` endpoint (returns OHLCV natively); reuse precheck (TUSHARE_TOKEN gate). Map Tushare schema → canonical `(date, ticker, open, high, low, volume, amount)`. Tushare uses `vol` for volume and `amount` already; normalize column names.

- [ ] **2.2** Implement `AkshareProvider.get_ohlcv()` using `akshare.stock_zh_a_hist(symbol, adjust="qfq")`. Returns daily OHLCV per symbol; loop tickers, concat to long-format, reuse precheck.

- [ ] **2.3** Implement `BaoStockProvider.get_ohlcv()` using `bs.query_history_k_data_plus`. fields="date,code,open,high,low,close,volume,amount". Reuse precheck.

- [ ] **2.4** Implement `YFinanceProvider.get_ohlcv()` using `yf.download(ticker, interval="1d")`. Returns OHLCV (volume in shares, amount NOT provided by YFinance → fill with NaN, document in field). Reuse precheck.

- [ ] **2.5** Implement `FallbackMarketDataProvider.get_ohlcv()`: mirror the `get_close_prices` rewrite — precheck loop, first-success-wins, STOP-on-exhausted, populate fallback_chain/fallback_reason/precheck_log. Compute sha256 on successful result. Symbol surfacing rules match close-path.

- [ ] **2.6** `StaticPriceProvider.get_ohlcv()`: optional, only needed if any test needs static OHLCV. If yes, implement; if no, leave as default NotImplementedError.

- [ ] **2.7** `CachedPriceProvider.get_ohlcv()`: skip in this PR (cache layer for OHLCV is a future PR; current scope is fresh fetch). Default NotImplementedError is fine.

**Acceptance**:
```bash
python3 -c "from backtest.market_data import TushareProvider, AkshareProvider, BaoStockProvider, YFinanceProvider; \
    for cls in [TushareProvider, AkshareProvider, BaoStockProvider, YFinanceProvider]: \
        assert 'get_ohlcv' in vars(cls), f'{cls.__name__} missing get_ohlcv'; \
    print('OK all 4 concrete providers have get_ohlcv')"
```

---

### Task 3 — Ingestion Script `scripts/ingest_cn_pit_ohlv.py`

**Files**: NEW `scripts/ingest_cn_pit_ohlv.py`

**Subtasks**:

- [ ] **3.1** Script structure mirrors `ingest_cn_pit_data.py`:
  - argparse: `--fetch-ohlv`, `--validate`, `--start`, `--end`, `--limit-tickers`, `--skip-existing`
  - Universe loaded from `data/cn_pit/universe.jsonl` (HS300 H47 PIT membership, read-only access)
  - Output: `data/cn_pit/ohlv_h47_supplement.csv` (long format)
  - Provider: build `FallbackMarketDataProvider([Tushare, Akshare, YFinance])` via `_build_fallback_provider()` helper (reuse pattern from ingest_cn_pit_data.py Task 5.1)

- [ ] **3.2** Fetch logic: read universe.jsonl → list of tickers (≈482 for HS300 H47) → call `provider.get_ohlcv(tickers, start, end)` → write to CSV in long format sorted by (date, ticker)

- [ ] **3.3** Metadata write: read existing `data/cn_pit/metadata.json`, **add ONLY** a new top-level key `ohlv_layer`:
  ```json
  "ohlv_layer": {
    "file": "data/cn_pit/ohlv_h47_supplement.csv",
    "sha256": "<computed>",
    "rows": <int>,
    "tickers": <int>,
    "date_range": ["YYYY-MM-DD", "YYYY-MM-DD"],
    "fetch_timestamp": "ISO8601",
    "fallback_chain": [...],
    "selected_provider": "tushare:daily",
    "fallback_reason": null,
    "precheck_log": [...],
    "columns": ["date", "ticker", "open", "high", "low", "volume", "amount"]
  }
  ```
  CRITICAL: do NOT modify any existing key in metadata.json (existing keys: `adjustment`, `benchmark`, `end`, `fetch_date`, `rows`, `source`, `start`, `ticker_count` — leave UNCHANGED).

- [ ] **3.4** Validation logic: `--validate` reads `ohlv_h47_supplement.csv`, recomputes sha256, compares to metadata, validates row count + (date, ticker) uniqueness + non-empty OHLV fields where universe is active. Emits `validation_report_ohlv.json`.

- [ ] **3.5** **CRITICAL safeguard**: script MUST raise + exit ≠ 0 if any of these are detected:
  - Existing `prices_h47_tushare_qfq_candidate.csv` sha changes during execution
  - Existing `prices.csv` sha changes
  - Existing keys in `metadata.json` change
  - Any write attempt to a file under `data/cn_pit/` other than `ohlv_h47_supplement.csv` or appending to `metadata.json`

**Acceptance (module-level smoke only — no live ingestion)**:
```bash
python3 -c "from scripts.ingest_cn_pit_ohlv import _build_fallback_provider; \
    p = _build_fallback_provider(); print(type(p).__name__, [x.name for x in p.providers])"
# Expected: FallbackMarketDataProvider [...providers...]
```

---

### Task 4 — Unit Tests

**Files**: NEW `tests/audit_layer/test_ohlv_provider.py`

**Subtasks**:

- [ ] **4.1** Test: `MarketDataProvider.get_ohlcv` default raises `NotImplementedError`
- [ ] **4.2** Test: `compute_ohlcv_sha256` stable (same DataFrame → same hash; cell change → different hash); sort-invariant (different input row order → same hash)
- [ ] **4.3** Test: `OHLCVResult` dataclass instantiates with all expected fields, default values correct
- [ ] **4.4** Test: `FallbackMarketDataProvider.get_ohlcv` with mock providers — first-success-wins + STOP-on-exhausted (mirror tests from `test_market_data_provider.py` get_close_prices tests but for OHLCV path)
- [ ] **4.5** Test: precheck integration — Tushare missing TUSHARE_TOKEN → LoaderBlockedError → fallback path triggered

**Acceptance**:
```bash
python3 -m unittest tests.audit_layer.test_ohlv_provider -v 2>&1 | tail -20
# Expected: 5+ tests OK
```

---

### Task 5 — Documentation

**Files**: `docs/h38_price_source_policy.md` (append section), `docs/data-backfill-runbook.md` (append section)

**Subtasks**:

- [ ] **5.1** Append `## OHLV Supplemental Layer (ENGINE-OHLV-V1)` section to `docs/h38_price_source_policy.md` describing:
  - File: `data/cn_pit/ohlv_h47_supplement.csv` long format, columns documented
  - Provider chain: Tushare daily → Akshare → YFinance (with caveat: YFinance returns `amount` NaN)
  - Joining with H47 close: `df_close.merge(df_ohlv, on=["date", "ticker"])`
  - sha256 audit via `metadata.json.ohlv_layer.sha256`
  - Reference this plan path

- [ ] **5.2** Append `## 8. OHLV Supplemental Layer 行为` section to `docs/data-backfill-runbook.md` (Chinese, parallel to existing `## 7. Loader Registry Fallback 行为` added by A2 PR Task 6.2)

**Acceptance**:
```bash
grep -l "ENGINE-OHLV-V1" docs/h38_price_source_policy.md
grep -l "OHLV Supplemental Layer" docs/data-backfill-runbook.md
```

---

### Task 6 — Live Ingestion + Acceptance Gates

**This Task involves real network fetches and writes to `data/cn_pit/`. Highest-risk Task in this PR.**

**Smoke Command**:
```bash
# Small fast path: ingest 5 tickers, 1 month, write to /tmp NOT data/cn_pit
python3 scripts/ingest_cn_pit_ohlv.py --fetch-ohlv --limit-tickers 5 \
    --start 2026-04-01 --end 2026-04-30 \
    --output-file /tmp/ohlv_smoke.csv \
    --output-metadata /tmp/ohlv_smoke_metadata.json
# Expected: exits 0; /tmp/ohlv_smoke.csv has ~5 tickers × ~20 trading days = ~100 rows;
# /tmp/ohlv_smoke_metadata.json has fallback_chain non-empty
```

**Full Command (only after smoke + sha-anchor check)**:
```bash
# Anchor pre-run sha
shasum -a 256 data/cn_pit/prices_h47_tushare_qfq_candidate.csv > /tmp/pre_run_protected_shas.txt
shasum -a 256 data/cn_pit/prices.csv >> /tmp/pre_run_protected_shas.txt
shasum -a 256 data/cn_pit/universe.jsonl >> /tmp/pre_run_protected_shas.txt

# Full HS300 H47 OHLV ingestion (writes data/cn_pit/ohlv_h47_supplement.csv)
python3 scripts/ingest_cn_pit_ohlv.py --fetch-ohlv \
    --start 2020-01-02 --end 2026-05-18

# Anchor post-run sha (must MATCH pre)
shasum -a 256 data/cn_pit/prices_h47_tushare_qfq_candidate.csv > /tmp/post_run_protected_shas.txt
shasum -a 256 data/cn_pit/prices.csv >> /tmp/post_run_protected_shas.txt
shasum -a 256 data/cn_pit/universe.jsonl >> /tmp/post_run_protected_shas.txt

# MUST diff empty
diff /tmp/pre_run_protected_shas.txt /tmp/post_run_protected_shas.txt
# Expected: no diff output (exit 0)
```

**Acceptance Gates (ALL must hold)**:

| # | Check | Expected |
|---|---|---|
| OG-1 | `prices_h47_tushare_qfq_candidate.csv` sha unchanged | `34f3e38f1245ffd8d8f3c392a256f61ca754a70036dcc40cd4f925a53f83c1bc` |
| OG-2 | `prices.csv` sha unchanged | `5efc8ec7ef4a6b7064010e67e0a9b9fdad77ca1c8d6cc907e47532738dc1a50c` |
| OG-3 | `universe.jsonl` / `fundamentals.jsonl` sha unchanged | (anchor at start of Task 6, compare at end) |
| OG-4 | `metadata.json` existing keys unchanged (only `ohlv_layer` added) | python diff check on keys before/after |
| OG-5 | New file `data/cn_pit/ohlv_h47_supplement.csv` exists, ≥ 482 tickers × ≥ 1500 trading days ≈ 700K rows | row count + ticker count check |
| OG-6 | metadata.json has new `ohlv_layer` block with non-empty `sha256`, `fallback_chain`, `selected_provider` ∈ SOURCE_PROVIDERS | python json validation |
| OG-7 | All unit tests pass | `python3 -m unittest discover tests/audit_layer/` exits 0 |
| OG-8 | YFinance fallback scenario tested (TUSHARE_TOKEN unset → AKShare or YFinance picks up) | precheck_log shows tushare blocked, selected_provider is akshare or yfinance |

If ANY gate fails → STOP, do NOT declare success.

---

## 5. Rollback SOP (dual-layer)

**Code layer (always available)**:
```bash
git revert <merge-commit-sha>
git push  # local-use, skip per user 2026-05-30
# Reverts new files: scripts/ingest_cn_pit_ohlv.py, tests/audit_layer/test_ohlv_provider.py
# Reverts edits: backtest/market_data.py, docs/h38_price_source_policy.md, docs/data-backfill-runbook.md
```

**Artifact layer (additive, never destructive)**:
- New file `data/cn_pit/ohlv_h47_supplement.csv`: leave on disk if PR reverted (does not interfere with close-path)
- New `metadata.json.ohlv_layer` key: leave in metadata, mark in `deprecated_runs` per A2 PR rollback pattern; do NOT delete the key

## 6. Time Budget

| Task | Estimate (claude PM est.) | Hermes actual (historical 5-10x faster) |
|---|---|---|
| 1. OHLCVResult + base method | 30 min | ~5 min |
| 2. 4 provider get_ohlcv impl | 90 min | ~20 min |
| 3. Ingestion script | 90 min | ~20 min |
| 4. Unit tests | 45 min | ~10 min |
| 5. Documentation | 20 min | ~5 min |
| 6. Live ingestion + AGs | 60 min (network + audit) | ~30 min |
| **Total** | **~5.5 h** | **~1.5 h Hermes wall** |

Charter §3 spike budget does not apply (this is engine PR per §4 Kill Crit 2). Wall budget: `max_wall_hours=8`, `max_revisions=1`.

## 7. kill_when

```
kill_when = "Any of: (a) OG-1 (prices_h47 sha) cannot be preserved across Task 6 ingestion;
(b) OG-4 (existing metadata.json keys) cannot be preserved during metadata write;
(c) Provider precheck unable to find ANY working OHLV source (all 4 providers fail precheck) —
indicates environmental data-access issue, requires user-side credential setup before PR can proceed."
```

## 8. Files Created / Modified Summary

This section the executor fills at PR close:

**Created**:
- `scripts/ingest_cn_pit_ohlv.py`
- `tests/audit_layer/test_ohlv_provider.py`
- `data/cn_pit/ohlv_h47_supplement.csv` (new, large file — track in git only after sha-anchor confirms quality)

**Modified**:
- `backtest/market_data.py` (Tasks 1-2: + OHLCVResult, + get_ohlcv on 4 providers, + Fallback.get_ohlcv, + compute_ohlcv_sha256)
- `docs/h38_price_source_policy.md` (Task 5.1: ENGINE-OHLV-V1 section)
- `docs/data-backfill-runbook.md` (Task 5.2: §8 Chinese section)
- `data/cn_pit/metadata.json` — ADD `ohlv_layer` key ONLY (existing keys unchanged; if any modified, BLOCKER per AGENTS.md)

**Protected files NOT touched** (verify with diff):
- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (Charter §2 frozen)
- `data/cn_pit/prices.csv`
- `data/cn_pit/universe.jsonl`
- `data/cn_pit/fundamentals.jsonl`
- `data/cn_pit/universe_snapshots.jsonl`
- `strategies/active.json`
- `agents/audit_layer.py`
- `backtest/backtest_engine.py`
- `backtest/oos_window.py`

## 9. Post-Merge Follow-ups

- **gtja191 IC bench re-spike** (the original A1 hypothesis, now unblocked by OHLV layer) — 2h spike, Charter §5 hypothesis #4 path
- **alpha101 full zoo IC bench** (not just close-only subset) — 2h spike
- **mootdx provider OHLCV implementation** — separate engine PR (currently NotImplementedError under StaticPriceProvider/CachedPriceProvider for OHLCV)
- **OHLCV cache layer** (`CachedPriceProvider.get_ohlcv()`) — separate engine PR if cache hit rate matters
