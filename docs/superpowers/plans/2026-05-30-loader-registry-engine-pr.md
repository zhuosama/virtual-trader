# A2 Loader Registry + Provenance Hardening — Engine PR Plan

Date: 2026-05-30
Status: EXECUTED 2026-05-30 — 6/7 Tasks Hermes-dispatched + audited; AG-1/AG-2/AG-5 PASS; AG-3/AG-4 deferred to first live ingestion. Local-use scope (no remote PR per user 2026-05-30). See execution log in spec v2 § 7.
Charter: `docs/research-charter-v1.md` (v1.0-DRAFT) — this PR is **§4 Kill Criterion 2 engine PR**, does NOT consume slice budget
Plan ID: ENGINE-LOADER-V1 (no Hxx number per Charter §4)
Parent spec: `docs/superpowers/specs/2026-05-30-vibe-trading-borrow-plan.md` § 5.1
Worker scope: ALLOWED — engine/loader edits per Charter §4. FORBIDDEN — touching `data/cn_pit/` artifacts, `strategies/active.json`, `audit_layer.py`, or any strategy claim.

> **For Hermes (worker):** This plan is checkbox-tracked. Implement task-by-task. Do NOT batch. Each task has its own acceptance check — run it before moving on. If a task's acceptance check fails, STOP and surface the failure mode; do not silently work around.

---

## 0. Goal

Harden the existing `backtest/market_data.py` provider layer so it can serve **both** the backtest path (current) and the ingestion path (currently bypasses it via direct `yf.download`), with provenance write-through, STOP-on-missing semantics, and sha256 audit hooks that comply with AGENTS.md Hard Prohibitions.

This is the prerequisite for the A1 Alpha Zoo spike (see parent spec §5.3) — the IC bench must run on data whose source is auditable.

## 1. Scope

### 1.1 In scope

- Harden `backtest/market_data.py` (9 existing Provider classes; see § 2 inventory)
- Refactor `scripts/ingest_cn_pit_data.py` (1733 LOC) to call providers instead of direct `yf.download`
- Add tests under `tests/audit_layer/` for the new semantics
- Update `data/cn_pit/metadata.json` schema to include `fallback_chain` + `selected_provider` + `sha256` per data file (NEW fields; existing fields unchanged → backward compatible)

### 1.2 Out of scope (split if needed)

- mootdx provider (= A5, follow-up PR after this one merges)
- AKShare provider implementation polish (already exists; only enum + precheck added here)
- Coordinator-level changes (already done in 2026-05-15 plan)
- `strategies/active.json` or `audit_layer.py` (Charter §6 FORBIDDEN)
- Any change to PIT artifacts (`data/cn_pit/*.jsonl`, `prices.csv`, `universe.jsonl`, `fundamentals.jsonl`) — AGENTS.md immutable

## 2. Current State Inventory

`backtest/market_data.py` already defines:

| Line | Symbol | Status |
|---|---|---|
| 14 | `ProviderResult` (dataclass) | KEEP, EXTEND (add `fallback_reason: str`, `precheck_log: list[str]`) |
| 81 | `MarketDataProvider` (base) | KEEP, EXTEND (add `precheck() -> None` abstract method) |
| 88 | `StaticPriceProvider` | KEEP (test fixture) |
| 114 | `CachedPriceProvider` | KEEP, audit only |
| 172 | `FallbackMarketDataProvider` | **REWRITE** — currently silent-skips on provider failure (= §4.1 violation) |
| 245 | `AkshareProvider` | KEEP, add precheck |
| 300 | `BaoStockProvider` | KEEP, add precheck |
| 362 | `TushareProvider` | KEEP, add precheck (token check) |
| 414 | `YFinanceProvider` | KEEP, add precheck |

`scripts/ingest_cn_pit_data.py` uses `yf.download` directly at line ~945 and writes `metadata.json` with `"source": "yfinance"` literal at ~969. The provider layer at `backtest/market_data.py` is currently invisible to ingestion.

## 3. Hard Prohibitions — Always Applicable (copy from `AGENTS.md`)

These rules apply to every task in this PR. Quoting verbatim so the executor reads them with the plan:

- **No data fabrication**: do NOT add, modify, "complete", or "round up" rows in any protected artifact (anything under `data/cn_pit/`, any prior Hxx run JSON, any prior Hxx report). If a gap/NaN/missing row is encountered, SURFACE as a finding and STOP — do NOT silently patch.
- **No source provenance forgery**: any data this task writes must have its `source_provider` / `source_url` fields reflect the ACTUAL source. If no real provenance exists, raise rather than fake it.
- **Symmetric restore**: any optional file modification (monkey-patch, runtime patch, backup) MUST use `try/finally`. Restore conditional on exit code is forbidden — leaks modifications on success paths.
- **Original ingestion verdicts immutable**: once a prior Hxx closes with a verdict + coverage numbers, that record is historical. A later Hxx cannot "fix" prior coverage by editing prior artifacts.
- **Exit-code is not acceptance**: do NOT declare success on `exit 0` alone. Every Acceptance Gate criterion must be physically verifiable (file exists, numerical assertion holds, sha256 matches).
- **Modification reporting**: final response MUST enumerate every file created or modified, calling out any protected files specifically.
- **No silent workarounds**: missing tokens, missing endpoints, schema mismatches → STOP and surface. Do not invent workarounds.
- **sha256 audit hooks**: any data-mutation or data-comparison brief must include sha256 audit hooks (pre + post). Audits raise hard on mismatch, never silent log.

### 3.1 Task-Specific Prohibitions

- Do **NOT** modify any file under `data/cn_pit/` except `metadata.json` (and even there: ADD new keys, do NOT modify existing ones)
- Do **NOT** modify `prices.csv`, `universe.jsonl`, `fundamentals.jsonl`, `universe_snapshots.jsonl` (sha256-protected per `virtual_trader_h28_baseline` memory)
- Do **NOT** use network beyond what existing providers already use; do **NOT** install new pip packages without surfacing the requirement first
- Do **NOT** weaken the existing `FallbackMarketDataProvider` test coverage; ADD coverage, don't replace
- Do **NOT** rename `MarketDataProvider.get_close_prices` (downstream `backtest_engine.py` depends on it)
- Do **NOT** install or invoke `mootdx` in this PR (= A5 follow-up)

## 4. Task Breakdown (checkbox-tracked)

### Task 1 — Provider Name Enumeration

**Files**:
- Edit: `backtest/market_data.py`
- Edit: `tests/audit_layer/test_market_data_provider.py` (already exists)

**Subtasks**:

- [ ] **1.1** Add at top of `backtest/market_data.py` (after imports):
  ```python
  SOURCE_PROVIDERS: frozenset[str] = frozenset({
      "tushare:pro_bar:qfq",
      "tushare:daily",
      "akshare:stock_zh_a_hist",
      "akshare:stock_zh_a_hist_qfq",
      "baostock:query_history_k_data_plus",
      "yfinance:download",
      "static:in_memory",          # for tests
      "cache:local_csv",
  })
  ```
  Future loaders (e.g. mootdx) MUST add their identifier here in their own PR before being usable.

- [ ] **1.2** Change each Provider class's `name` class attribute from short ("akshare", "yfinance"…) to the canonical enumerated value above. Update all string compares in `FallbackMarketDataProvider` accordingly.

- [ ] **1.3** Add assertion in `MarketDataProvider.__init_subclass__` that `cls.name in SOURCE_PROVIDERS`. Raises `ValueError("provider name not in SOURCE_PROVIDERS")` on import-time violation.

**Acceptance**:
```bash
python -c "from backtest.market_data import SOURCE_PROVIDERS, AkshareProvider; assert AkshareProvider.name in SOURCE_PROVIDERS"
python -m unittest tests.audit_layer.test_market_data_provider -v
```
Expected: both exit 0; test suite count >= current count (no test deletions).

---

### Task 2 — Precheck (STOP-on-missing)

**Files**:
- Edit: `backtest/market_data.py`
- Create: `tests/audit_layer/test_provider_precheck.py`

**Subtasks**:

- [ ] **2.1** Add abstract method:
  ```python
  class MarketDataProvider:
      name: str = ""
      def precheck(self) -> None:
          """Raise LoaderBlockedError if this provider can't run (missing token, missing library, missing endpoint).
          Default: no-op. Subclasses override."""
          return None

  class LoaderBlockedError(RuntimeError):
      def __init__(self, provider: str, reason: str):
          self.provider = provider
          self.reason = reason
          super().__init__(f"[BLOCKER:loader] provider={provider} reason={reason}")
  ```

- [ ] **2.2** Implement `precheck()` for each concrete provider:
  - `TushareProvider`: `if not os.environ.get("TUSHARE_TOKEN"): raise LoaderBlockedError(self.name, "TUSHARE_TOKEN env var missing")`
  - `AkshareProvider`: `try: import akshare except ImportError: raise LoaderBlockedError(self.name, "akshare library not installed")`
  - `BaoStockProvider`: same pattern, `import baostock`
  - `YFinanceProvider`: `try: import yfinance except ImportError: raise LoaderBlockedError(self.name, "yfinance library not installed")`
  - `StaticPriceProvider`, `CachedPriceProvider`: no precheck (in-memory / local file)

- [ ] **2.3** Test cases in `test_provider_precheck.py`:
  - Unset `TUSHARE_TOKEN` → `TushareProvider().precheck()` raises `LoaderBlockedError` with `"TUSHARE_TOKEN env var missing"` in str
  - Patch out `akshare` module → `AkshareProvider().precheck()` raises `LoaderBlockedError`
  - Set `TUSHARE_TOKEN=test` → `TushareProvider().precheck()` returns `None` (no raise)

**Acceptance**:
```bash
python -m unittest tests.audit_layer.test_provider_precheck -v
```
Expected: all tests pass, including the 3 raise-cases above.

---

### Task 3 — FallbackMarketDataProvider Rewrite (no silent skip)

**Files**:
- Edit: `backtest/market_data.py` (rewrite the class at line 172)
- Edit: `tests/audit_layer/test_market_data_provider.py` (extend)

**Subtasks**:

- [ ] **3.1** Extend `ProviderResult` dataclass with 3 new fields (additive, default-valued so existing constructors don't break):
  ```python
  fallback_reason: Optional[str] = None     # populated when fallback was triggered
  precheck_log: List[str] = field(default_factory=list)  # one line per provider precheck attempt
  fallback_chain: List[str] = field(default_factory=list)  # provider names attempted in order
  ```

- [ ] **3.2** Rewrite `FallbackMarketDataProvider.get_close_prices`:
  - Loop providers in order; for each, call `precheck()` first
  - If precheck raises `LoaderBlockedError`: log `[WARN loader] precheck-blocked={p.name} reason={e.reason}` to stderr; record in `precheck_log`; **continue** to next provider (precheck failure IS a valid fallback signal — token missing is a known fallback trigger)
  - If precheck succeeds: call `provider.get_close_prices(...)`. If result has data, RETURN with `selected_provider = p.name`, `fallback_reason = ...` (filled if any precheck failed earlier), `fallback_chain = [...attempted]`
  - If all providers exhaust (no precheck succeeded OR none returned data): `raise LoaderBlockedError("fallback", f"all providers exhausted: {precheck_log}")`. **DO NOT** return `INFRA_ERROR` silently as the current implementation does — that's the §4.1 (c) STOP requirement.

- [ ] **3.3** Test cases (extend existing test file):
  - All providers precheck OK, primary returns data → result has `fallback_chain=[primary]`, `selected_provider=primary`, `fallback_reason=None`
  - Primary precheck fails, secondary succeeds → result has `fallback_chain=[primary, secondary]`, `selected_provider=secondary`, `fallback_reason="precheck-blocked: primary"`, `precheck_log` has 1 entry
  - All providers fail precheck → `LoaderBlockedError` raised (test with `assertRaises`)
  - Primary precheck OK but returns empty data, secondary returns data → fallback triggered with `fallback_reason="empty-data: primary"`

**Acceptance**:
```bash
python -m unittest tests.audit_layer.test_market_data_provider tests.audit_layer.test_provider_precheck -v
```
Expected: all tests pass; the "all exhausted" test confirms STOP-on-missing semantics; the legacy `INFRA_ERROR` return path is GONE.

---

### Task 4 — sha256 Audit Hook

**Files**:
- Edit: `backtest/market_data.py` (add helper)
- Create: `tests/audit_layer/test_provider_sha256.py`

**Subtasks**:

- [ ] **4.1** Add module-level helper:
  ```python
  def compute_prices_sha256(prices: pd.DataFrame) -> str:
      """Stable sha256 over a price frame. Used for audit at write and at compare."""
      import hashlib
      payload = prices.to_csv(index=True).encode("utf-8")
      return hashlib.sha256(payload).hexdigest()

  class ChecksumMismatchError(RuntimeError):
      def __init__(self, expected: str, actual: str, context: str):
          super().__init__(f"[BLOCKER:checksum] context={context} expected={expected[:12]}... actual={actual[:12]}...")
  ```

- [ ] **4.2** In `FallbackMarketDataProvider.get_close_prices`, after assembling the final prices DataFrame, compute its sha256 and write to `ProviderResult.sources_used["__sha256"] = sha`. (Use a reserved key prefix `__` so it doesn't collide with provider names.)

- [ ] **4.3** Test cases in `test_provider_sha256.py`:
  - Same DataFrame yields same sha256 across two calls (stability)
  - Modifying a single cell yields different sha256
  - `ChecksumMismatchError` raises with the expected message format

**Acceptance**:
```bash
python -m unittest tests.audit_layer.test_provider_sha256 -v
```
Expected: 3 tests pass.

---

### Task 5 — Refactor ingest_cn_pit_data.py to use Provider Layer

**Files**:
- Edit: `scripts/ingest_cn_pit_data.py` (around line 921 `fetch_historical_prices` and write path at ~969)
- DO NOT edit: existing fixtures, existing protected artifacts

**Subtasks**:

- [ ] **5.1** Add a new function `_build_fallback_provider() -> FallbackMarketDataProvider` that wires `[TushareProvider(), AkshareProvider(), YFinanceProvider()]` in that order (Tushare primary per H47/H48 precedent). Reads provider configs from env vars (`TUSHARE_TOKEN`, etc.).

- [ ] **5.2** Locate the existing `fetch_prices` function (around line 921 in pre-edit file; was incorrectly named `fetch_historical_prices` in plan v1 — actual function is `fetch_prices`, surfaced by Hermes dispatch on 2026-05-30). Refactor it to:
  - Call `provider = _build_fallback_provider()`
  - Call `result = provider.get_close_prices(tickers, start, end)`
  - Wrap in `try/except LoaderBlockedError as e: print(str(e), file=sys.stderr); sys.exit(2)`
  - The legacy direct `yf.download` path becomes the `YFinanceProvider` inside the registry, no longer called directly

- [ ] **5.3** Update `metadata.json` write at ~line 969:
  - REMOVE the hardcoded `"source": "yfinance"`
  - ADD a new `data_sources` block:
    ```python
    metadata["data_sources"]["prices"] = {
        "fallback_chain": result.fallback_chain,
        "selected_provider": result.sources_used.get("__selected", ""),
        "sha256": result.sources_used.get("__sha256", ""),
        "rows": len(result.prices),
        "fallback_reason": result.fallback_reason,
        "precheck_log": result.precheck_log,
    }
    ```
  - DO NOT remove other existing keys in `metadata.json`. ADD `data_sources` alongside.

- [ ] **5.4** **CRITICAL**: this refactor must NOT change the bytes written to `prices.csv`. The H28 baseline memory + Charter §2 frozen state both expect `prices.csv` sha256 = `5efc8ec7ef4a6b7064010e67e0a9b9fdad77ca1c8d6cc907e47532738dc1a50c` (per `virtual_trader_h28_baseline.md`). Confirm by:
  ```bash
  # Before any code change
  sha256sum data/cn_pit/prices.csv > /tmp/prices_sha_before.txt
  # After refactor, run a smoke ingestion (see Task 7 smoke command)
  # then
  sha256sum data/cn_pit/prices.csv > /tmp/prices_sha_after.txt
  diff /tmp/prices_sha_before.txt /tmp/prices_sha_after.txt
  ```
  Expected: **identical**. If different, STOP — surface as `BLOCKER` finding (likely TushareProvider returns differently formatted data; provider needs format-parity adjustment, NOT data overwrite).

**Acceptance**:
```bash
# (1) Module-level smoke: imports & function signature
python -c "from scripts.ingest_cn_pit_data import _build_fallback_provider, fetch_historical_prices; p = _build_fallback_provider(); print(p.providers)"

# (2) sha256 invariance check (see 5.4)
sha256sum data/cn_pit/prices.csv  # must match H28 baseline
```

---

### Task 6 — Documentation

**Files**:
- Edit: `docs/h38_price_source_policy.md` (existing source policy doc — add a section pointing to the new provider layer)
- Edit: `docs/data-backfill-runbook.md` (add a section on fallback chain semantics)
- Create: `THIRD_PARTY_NOTICES.md` (in repo root) — NOT for this PR's loader code (it's all local), but seed the file for the upcoming A1 Alpha Zoo borrow

**Subtasks**:

- [ ] **6.1** Append to `h38_price_source_policy.md` a section "## Loader Registry (ENGINE-LOADER-V1)":
  - Reference `SOURCE_PROVIDERS` enumeration in `backtest/market_data.py`
  - Reference the precheck / fallback / sha256 contracts
  - Reference this plan: `docs/superpowers/plans/2026-05-30-loader-registry-engine-pr.md`

- [ ] **6.2 CORRECTED**: Plan v1 assumed `data-backfill-runbook.md` had an English "When Tushare token is unavailable" section. Actual runbook is 6 Chinese sections (`## 1. 数据来源优先级` ... `## 6. 回补后必须验证`). **Hermes correctly BLOCKED** on first dispatch refusing to fabricate the section (2026-05-30). PM decision: APPEND a new Chinese section `## 7. Loader Registry Fallback 行为` at the end of the runbook, covering:
  - Old runbook suggested "Tushare token unavailable → manually swap to yfinance + rerun"
  - Now: provider layer auto-falls-back Tushare → Akshare → YFinance with precheck gating
  - `metadata.json.data_sources.prices` schema: `fallback_chain`, `selected_provider`, `fallback_reason`, `precheck_log`, `sha256`
  - Debug: 优先检查 `data_sources.prices.fallback_reason`
  - Reference plan path + `backtest/market_data.py` → `FallbackMarketDataProvider`

- [ ] **6.3** Create `THIRD_PARTY_NOTICES.md` in repo root with placeholder:
  ```markdown
  # Third-Party Notices

  ## Vibe-Trading (HKUDS/Vibe-Trading)

  Upstream: https://github.com/HKUDS/Vibe-Trading
  License: MIT
  Borrowed-at commit: <pending — fork & pin in A1 PR>

  Permission notice from upstream LICENSE:
  Copyright (c) HKUDS contributors
  Permission is hereby granted, free of charge ... (full MIT text here when first file is borrowed)
  ```
  This PR does NOT borrow code from upstream yet, but the file is required by A1.

**Acceptance**: files exist; `grep -l "ENGINE-LOADER-V1" docs/h38_price_source_policy.md` matches.

---

### Task 7 — Full Run + Acceptance Gates

**Smoke Command (CORRECTED 2026-05-30)**:

Plan v1 wrote `--limit 5 --output-prices /tmp/... --output-metadata /tmp/...`. **None of these three flags exist** in `scripts/ingest_cn_pit_data.py`. Actual flags: `--limit-tickers` (not `--limit`); no sandbox output path — any `--fetch-prices` writes directly to prod `data/cn_pit/prices.csv` and `metadata.json` (= H28 baseline corruption).

Replacement smoke = module-level + unit-test based (no live ingestion needed):

```bash
# (a) Module smoke: provider chain wires correctly
python3 -c "from scripts.ingest_cn_pit_data import _build_fallback_provider; \
    p = _build_fallback_provider(); print(type(p).__name__, [x.name for x in p.providers])"
# Expected: FallbackMarketDataProvider ['tushare:daily', 'akshare:stock_zh_a_hist_qfq', 'yfinance:download']

# (b) Unit smoke: all 14 new + extended tests pass
python3 -m unittest tests.audit_layer.test_market_data_provider \
    tests.audit_layer.test_provider_precheck \
    tests.audit_layer.test_provider_sha256
# Expected: Ran 14 tests OK
```

**Full Command (validate-only, does NOT touch prices.csv)**:

```bash
# Validate-only mode: reads prices.csv, writes validation_report.json
python3 scripts/ingest_cn_pit_data.py --validate
# Expected: exits 0; status BLOCKED; blockers includes 'price_coverage';
# 2 failed checkpoints (2020-01-02, 2021-01-04); matches H28 baseline.
# Additional blockers (survivorship_bias / research_only) are baseline drift since
# 2026-05-11, NOT introduced by this PR.
```

**Live-ingestion validation (DEFERRED to user-triggered run)**: AG-3 (metadata `data_sources` block populated) and AG-4 (TUSHARE_TOKEN-unset fallback chain triggers correctly) cannot be verified without an actual fetch-prices run, which would overwrite prod `prices.csv`. These are deferred. First user-triggered `--fetch-prices` after merge will exercise the new path and populate `data_sources` automatically — if schema is wrong, that's the catch point.

**Acceptance Gates (ALL must hold)**:

| # | Check | Expected |
|---|---|---|
| AG-1 | `data/cn_pit/prices.csv` sha256 unchanged | `5efc8ec7ef4a6b7064010e67e0a9b9fdad77ca1c8d6cc907e47532738dc1a50c` |
| AG-2 | All tests pass | `python -m unittest discover tests/audit_layer/ -v` exits 0 |
| AG-3 | `metadata.json` has new `data_sources.prices` block with `fallback_chain` non-empty, `selected_provider` ∈ `SOURCE_PROVIDERS`, `sha256` length 64 hex |
| AG-4 | TUSHARE_TOKEN unset scenario: `TUSHARE_TOKEN= python scripts/ingest_cn_pit_data.py --fetch-prices --limit 1` → `precheck_log` has `precheck-blocked=tushare:pro_bar:qfq`, `selected_provider` becomes Akshare or YFinance |
| AG-5 | `validation_report.json` `status` == `BLOCKED`, `blockers` == `["price_coverage"]`, `price_coverage_failed_checkpoints >= 2` (matches H28 baseline memory) |
| AG-6 | Final response to user enumerates every file created/modified per AGENTS.md "Modification reporting" |

If ANY gate fails → STOP, do NOT declare success. Report which gate, the actual value, and suspected cause.

---

## 5. Rollback SOP (dual-layer per parent spec §5.1)

**Code layer (always available)**:
```bash
git revert <merge-commit-sha>
git push
# Single command, restores all 9 Provider classes to pre-PR state.
```

**Artifact layer (additive, never destructive)**:
- If `metadata.json` has been written with new `data_sources` block and PR is reverted:
  - DO NOT delete `data_sources` block (= immutability per AGENTS.md)
  - Instead: append to `data/cn_pit/metadata.json` a top-level key:
    ```json
    "deprecated_runs": [
      {"date": "2026-05-30", "reason": "loader-registry-engine-pr reverted", "affected_keys": ["data_sources.prices"]}
    ]
    ```
- Validation scripts (`scripts/validate_*.py`) should learn to skip keys listed in `deprecated_runs[*].affected_keys` for consistency checks. (This step is a follow-up commit, NOT part of rollback execution.)

The dual-layer design enforces: code can be reverted instantly; artifacts are append-only and remain auditable across reverts.

## 6. Time Budget

| Task | Estimate |
|---|---|
| 1. Provider name enumeration | 30 min |
| 2. Precheck | 45 min |
| 3. FallbackMarketDataProvider rewrite | 90 min (most logic + tests) |
| 4. sha256 audit hook | 30 min |
| 5. Refactor ingest_cn_pit_data.py | 90 min |
| 6. Documentation | 30 min |
| 7. Full run + Acceptance Gates | 30 min |
| **Total** | **5.75 h** (rounded to 6h budget) |

Charter §3 spike budget is ≤2h per hypothesis; this is NOT a spike, it's an engine PR per §4 Kill Crit 2, so the spike budget does not apply. Wall budget here: `max_wall_hours=8`, `max_revisions=1`.

## 7. kill_when (single-sentence exit condition)

```
kill_when = "AG-1 (prices.csv sha256) cannot be preserved across the refactor —
i.e. routing yfinance through YFinanceProvider produces a different byte stream
than direct yf.download. In that case, the provider parity gap is a separate
fix slice; this PR is paused and reverted."
```

## 8. Files Created / Modified Summary

This section the executor must fill in at PR close, per AGENTS.md "Modification reporting":

**Created**:
- `tests/audit_layer/test_provider_precheck.py`
- `tests/audit_layer/test_provider_sha256.py`
- `THIRD_PARTY_NOTICES.md`

**Modified**:
- `backtest/market_data.py` (Tasks 1-4)
- `scripts/ingest_cn_pit_data.py` (Task 5)
- `tests/audit_layer/test_market_data_provider.py` (Task 1, 3 extensions)
- `docs/h38_price_source_policy.md` (Task 6.1)
- `docs/data-backfill-runbook.md` (Task 6.2)
- `data/cn_pit/metadata.json` — ADD `data_sources` key ONLY (existing keys untouched; if any existing key is modified, that's a BLOCKER per AGENTS.md immutability)

**Protected files NOT touched** (verify with `git diff`):
- `data/cn_pit/prices.csv`
- `data/cn_pit/universe.jsonl`
- `data/cn_pit/fundamentals.jsonl`
- `data/cn_pit/universe_snapshots.jsonl`
- `strategies/active.json`
- `agents/audit_layer.py`

## 9. Post-Merge Follow-ups (NOT in this PR)

- A5 mootdx provider — separate plan, depends on this PR's `SOURCE_PROVIDERS` enum
- A1 Alpha Zoo spike — separate spike doc, depends on auditable data provenance from this PR
- Future: validation script update to honor `deprecated_runs` (mentioned in §5 rollback)
