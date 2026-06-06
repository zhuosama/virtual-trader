# H50b — Quality-Value Composite Redesign Search

## Context

H42, H48, H49b all closed `RESEARCH_ONLY` with `beat_HS300_windows` capped at 1/5 across all top-15 candidates. H49b's post-mortem (S19 in `docs/strategy-optimization-sync.md`) confirmed: sector neutrality is not the binding constraint; the **alpha signal itself** doesn't beat HS300 across multi-window. H42's existing `ValueScore` (`backtest/experiments/fundamental_backtest.py:710`) is a 5-component scorer that silently degrades to 2 features (ROE + D/E) because the H28 `fundamentals.jsonl` only carries those two PIT-safe fields.

H50a (V2) just delivered a parallel PIT fundamentals file with **10 score-component fields** (profitability / balance-sheet / cash-flow) plus 6 audit intermediates. 100% ticker coverage, accruals_ratio 93.3%, gross_margin 99.2%, ROE overlap 900/900 within 0.5pp tolerance.

H50b is the first slice that exercises the new alpha signal: design a `ValueScoreH50` composite from H50a fields, run the H42 search framework with the new scorer swapped in, and measure whether `beat_HS300_windows` moves from H42/H48/H49b's 1/5 floor.

H50b is a research slice. `RESEARCH_ONLY` is the expected outcome unless a candidate clears all H42 gate conditions. The slice's primary information value is the **delta in `beat_HS300_windows` count** vs. all prior research runs.

## Objective

Define a 3-component PIT-safe `ValueScoreH50` composite from H50a fundamentals, runtime-substitute it into the H42 search framework via module-level monkey-patch (no file edits to `h42_strategy_redesign_search.py` or `fundamental_backtest.py`), run the search with H47 prices + H49a sectors + new scorer, and report the gate-pass + `beat_HS300_windows` delta.

## Inputs

- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (H47)
- `data/cn_pit/universe_h30_candidate.jsonl`
- `data/cn_pit/universe_snapshots_h30_candidate.jsonl`
- `data/cn_pit/sector_metadata_sw_l1.csv` (H49a)
- `data/cn_pit/fundamentals_h50a_pit_quality.jsonl` (H50a V2; 12,398 rows × 481 tickers × 26 quarters; 10 score fields + 6 intermediates)
- `scripts/h42_strategy_redesign_search.py` — reused as a library; **NOT modified**
- `backtest/experiments/fundamental_backtest.py` — reused as a library; **NOT modified**
- Baselines for comparison: `fundamental_value_h42_strategy_redesign_search.json`, `fundamental_value_h48_unified_qfq_h42_rerun.json`, `fundamental_value_h49b_sector_neutral_rs_search.json`

## Outputs

- `scripts/h50b_quality_value_search.py` — new search script. Owns `ValueScoreH50` class + monkey-patch installer + H42 search invocation.
- `backtest/runs/fundamental_value_h50b_quality_value_search.json`
- `reports/h50b_quality_value_search_report.md`
- `tests/test_h50b_quality_value_search.py`
- `scripts/validate_hxx_artifacts.py` — register `h50b` with `validate_h50b` mirroring `validate_h42` + enforcing the H50b-specific provenance block (data_sources sha256 for 4 files + scorer_substitution audit).
- H50b row appended to `docs/strategy-optimization-sync.md`
- `docs/agents/next-slices.md` H50b flipped from `BLOCKED` to `DONE` at closure

## Design Decisions (locked unless overridden before dispatch)

### D1. ValueScoreH50 composition: 3 components, no valuation

The composite has **three** components, not the four named in H45 PRD:

- **Profitability**: ROE (roe_waa), ROA, gross_margin, operating_margin
- **Balance-sheet strength**: current_ratio, quick_ratio, debt_to_equity (inverted — lower is better)
- **Cash-flow quality**: operating_cash_flow_to_revenue, free_cash_flow, accruals_ratio (inverted — **Sloan anomaly**: lower raw signed accruals_ratio = higher earnings quality; do NOT take absolute value)

The **valuation** component (PE/PB/dividend_yield/market_cap) is dropped because H45 PRD §Data Requirements + H50a explicitly exclude these (no PIT-safe source). Adding non-PIT valuation would re-introduce the H38 price-source compromise that H47 was built to fix.

### D2. Component aggregation: PIT-safe quantile rank, equal weight

For each rebalance date, for each available stock in the H30 universe:

1. **Field selection per component**: drop sub-fields where the stock has NULL.
2. **Per-component minimum**: each component requires `>= ceil(N/2)` non-NULL sub-fields, where N = component cardinality. Profitability needs ≥2 of 4; balance-sheet needs ≥2 of 3; cash-flow needs ≥2 of 3. Below threshold → stock excluded from selection at that rebalance.
3. **Per-sub-field score**: winsorize at (1st, 99th) percentiles computed over the cross-section of stocks **available as of that rebalance date** (PIT-safe). Then rank within that cross-section to [0, 1].
4. **Per-component score**: simple mean of available sub-field ranks (each in [0, 1]).
5. **Total score**: equal-weight average of three component scores → [0, 1] composite.

Justification: equal weighting at both component and sub-field levels minimizes overfitting risk for the first iteration. If H50b's verdict is `RESEARCH_ONLY` with positive `beat_HS300_windows` improvement, H50c can re-weight using cross-sectional regression on out-of-sample returns. If H50b stays at 0/5, the scorer design is not the bottleneck and re-weighting won't save it.

Inverted fields:
- `debt_to_equity`: rank raw value asc, then `score = 1 - rank` (lower leverage = higher quality).
- `accruals_ratio`: rank **raw signed value** asc (do NOT take absolute value), then `score = 1 - rank`. This follows the Sloan accruals anomaly: companies with high (positive) accruals have systematically lower future returns; companies with low or negative accruals have higher earnings quality. Taking `abs()` would conflate aggressive earnings management with conservative ones — wrong.

Both inversions are applied AFTER the cross-sectional rank (step 4), not on the raw value before ranking.

**Cross-sectional rank cache (required — the engine API is single-ticker, not panel):**

The H42 engine calls `ValueScoreH50.from_fundamentals(ticker, _)` one ticker at a time. A single-ticker call cannot know its own cross-sectional rank — the rank requires the full cross-section at that rebalance date.

Mechanism (mandatory, not optional):

1. `ValueScoreH50` carries a class-level cache `_xs_cache: dict[as_of_date_str, dict[ticker, dict]]`.
2. On every `from_fundamentals(ticker, _)` call:
   a. Read `as_of_date = AS_OF_DATE_REF[0]` (set by the per-rebalance hook in D4-A; must be a real calendar date, never None).
   b. If `as_of_date not in _xs_cache`: trigger a one-shot cross-section computation for that date (lazy precompute):
      - For every ticker in the H30 universe, look up the latest H50A_PANEL row with `filing_date <= as_of_date` (PIT-safe).
      - Build a DataFrame of 10 raw score fields × N tickers.
      - Winsorize each column at (p1, p99) of THIS cross-section only.
      - Rank each column to [0, 1] within THIS cross-section only.
      - Apply inversions for `debt_to_equity` and `accruals_ratio` (per above).
      - Apply D2 step 2 minimum sub-field rule per component; exclude tickers below threshold by setting them to None in the cache.
      - For surviving tickers, compute per-component mean of available sub-field ranks; total = mean of 3 components.
      - Store `_xs_cache[as_of_date][ticker] = {profitability, balance_sheet, cash_flow, total, components_used}` (or None for excluded tickers).
   c. Return the cached entry for `(as_of_date, ticker)`. None if ticker was excluded.
3. The cache is process-local and ephemeral — cleared in the `finally` block of D3 alongside scorer restoration.
4. Memory budget: N_tickers (~481) × N_rebalance_dates (~120 over deploy window) × ~6 floats per row ≈ 350 KB. Fits easily.

Audit hook: every call must verify `AS_OF_DATE_REF[0] is not None` before lookup. If None (D4-A hook didn't fire), raise loudly — do NOT silently use last-known date.

### D3. Scorer substitution mechanism: runtime monkey-patch with provenance

`ValueScore.from_fundamentals` is called at two sites:

- `backtest/experiments/fundamental_backtest.py:841` inside `run_fundamental_backtest`
- `scripts/h42_strategy_redesign_search.py:470` inside the search selection loop

Both files are protected by H42's hard prohibition. H50b must therefore swap the scorer **at runtime** without editing source. The mechanism:

1. `h50b_quality_value_search.py` defines `ValueScoreH50` with the **same interface** as `ValueScore` (`@classmethod from_fundamentals(cls, ticker, fundamentals_dict) -> ValueScoreH50 | None`; the returned object exposes `.total: float` and per-component score attributes).
2. At script entry, before calling H42's search:
   - `import backtest.experiments.fundamental_backtest as _fb`
   - `import scripts.h42_strategy_redesign_search as _h42`
   - Capture originals: `_FB_VALUE_SCORE_V1 = _fb.ValueScore`, `_H42_VALUE_SCORE_V1 = _h42.ValueScore`
   - Patch: `_fb.ValueScore = ValueScoreH50; _h42.ValueScore = ValueScoreH50`
   - Record both module ids + class object ids in the provenance block (see below).
3. After the search completes, restore the originals in a `finally` block. The patch is process-local; no other module persists the change.

Justification: this respects the spirit of the H42 prohibition (no edits to canonical search/engine logic) while enabling the alpha-source swap. The substitution is auditable via the provenance block and verifiable via `validate_h50b`'s assertion that the run JSON declares the substitution.

### D4. Fundamentals loading: H50a panel + per-rebalance date hook (Option A only)

H50a fundamentals enter via a closure-captured panel, NOT through H42's `CN_PIT_FileSource`. The H50b script owns this panel for the duration of the run.

**Panel structure (matches D2 cache contract; locked):**

```python
# H50A_PANEL: dict[ticker, list[row]] where each ticker's list is sorted by filing_date ASC.
# Each row carries all 10 score fields + 6 intermediates + ann_date + end_date + filing_date + report_period.
H50A_PANEL: dict[str, list[dict]] = load_h50a_jsonl_sorted_by_filing_date()
```

PIT-safe lookup for a (ticker, as_of_date) pair: walk `H50A_PANEL[ticker]` from the end, return the first row with `filing_date <= as_of_date`. None if no such row.

**Date hook mechanism (Option A — MANDATORY; alternatives explicitly forbidden):**

`ValueScoreH50.from_fundamentals(ticker, _)` cannot infer the current rebalance date from its arguments. The H50b script must wire in a per-rebalance hook that updates `AS_OF_DATE_REF[0]` to the **true calendar rebalance date** before any per-ticker scoring call fires.

Concrete implementation:

1. At dispatch, scan `scripts/h42_strategy_redesign_search.py` and `backtest/experiments/fundamental_backtest.py` to find a function called **exactly once per rebalance** with the rebalance date as a parameter (e.g., a `select_candidates(date, ...)` or `evaluate_window(start_date, ...)` style entry).
2. Monkey-patch that function: wrap it so the wrapper writes `AS_OF_DATE_REF[0] = rebal_date` before delegating to the original. Use `functools.wraps`.
3. The patched modules go into the `scorer_substitution.patched_modules` provenance block (alongside the ValueScore patch).
4. Smoke run MUST assert `AS_OF_DATE_REF` got updated multiple times (at least once per rebalance in the smoke window). If `AS_OF_DATE_REF[0]` is still None after one rebalance call, the hook failed to wire — abort.

**Explicitly FORBIDDEN alternatives (PIT-leak risk):**

- **NOT ALLOWED**: derive as_of_date from the `fundamentals_dict` passed by the engine (e.g., reading the dict's max `report_period`). H28 `fundamentals.jsonl` and H50a Tushare have different filing_date for the same report_period (different ingestion vintages — Apr 20 vs Apr 25 for the same Q1 report). If H28 declares a report visible on day X but Tushare's filing_date is X+5, treating the engine's "this report is in the dict, so it must be visible" signal as a PIT timestamp for H50a leaks ~5 days of future data. This is the same class of leak that H30+H38+H47 chain of work was built to prevent.
- **NOT ALLOWED**: use system clock / today() as as_of_date — backtest runs on historical dates.
- **NOT ALLOWED**: silently default AS_OF_DATE_REF to any value if the hook didn't fire — must raise loudly.

If H42's search module exposes no patchable per-rebalance function, this is a BLOCKER for H50b — surface the failure and stop. Do NOT fall back to PIT-leaky alternatives. The fix in that case is a separate slice that adds a hook in H42 with explicit review.

This keeps H42's source layer untouched while preserving PIT discipline. Memory budget: H50A_PANEL = 481 tickers × ~26 rows × ~22 fields × ~50 bytes ≈ 14 MB. Fits.

### D5. Sector mechanism: hard cap, S19 fix applied

Inherit H49b's `sector_max_weight_pct` cap (cap-during-construction, drop-and-substitute next-ranked). **Drop the `min_sectors_in_portfolio` knob entirely** — H49b diagnostics showed it was silent soft-fail (declared 7, realized 4-5). The H50b search grid has no `min_sectors` axis.

H50b reports realized sector count at deploy start + end as a sanity diagnostic in the run JSON (no gate; informational only). If realized sector count of the best candidate is < 4 at any point, surface as a `## Sector Concentration` warning in the report.

### D6. Search grid: focused on alpha signal, not param sweep

H45 PRD §Out of Scope explicitly forbids "another broad parameter-only grid". H50b's grid is narrow:

- **Overlay set** (Stage A): H42's top-performing overlays only — `rel20_ge_0_and_ma60`, `rel60_ge_0`, `price_gt_ma120`, `intra_sector_rs60`, and a baseline `none` overlay (pure ValueScoreH50). Total: **5 overlays**.
- **Sector axis** (Stage A): `sector_max_weight_pct ∈ {0.20, 0.25, 0.30, 1.00}` (1.00 = control). 4 values.
- **Param grid** (Stage B): top_n ∈ {8, 10}; max_position_pct ∈ {0.05, 0.06, 0.08}; stop_loss_pct ∈ {0.08, 0.10}; take_profit_pct ∈ {0.22, 0.25}; quality_filter ∈ {0.30, 0.40, 0.50}; rebalance_freq_days ∈ {42, 63}. Total: 2×3×2×2×3×2 = **144 combos**.
- **Total Stage B runs**: 5 overlays × 4 sector caps × 144 params × ... but apply `--stage-b-limit 200` cap. The point is to vary the alpha signal (already varied by D1's new scorer), not to re-explore the H42 grid.

Total wall clock budget: 15–35 min on this machine.

### D7. Acceptance gate: H42 verbatim, no change

H42 gate (all 9 conditions). `CANDIDATE_FOR_FORWARD_TRIAL` iff at least one candidate passes; else `RESEARCH_ONLY`.

### D8. Stage C ranking: beat_HS300_windows desc, deploy_excess desc tiebreaker

Inherit H49b's D6 ranking. The binding constraint is the metric to rank on; ranking by Sharpe (H42 default) would hide whether the new scorer moved the needle.

## Hard Prohibitions

- Do not modify `scripts/h42_strategy_redesign_search.py` (search logic, gate thresholds, overlay families, window definitions).
- Do not modify `backtest/experiments/fundamental_backtest.py` (engine, original `ValueScore` class, `BacktestResult`, anything).
- Do not overwrite or modify any input artifact: H30 universe, H38 prices, H47 prices, H47 coverage, H49a CSV, H49a coverage, **H50a JSONL (12,398 rows)**, H50a coverage, original fundamentals.jsonl, H42/H48/H49b run JSONs, all prior reports.
- Do not modify production trading config.
- Do not place live orders.
- No network: do not refetch prices, fundamentals, or sector data.
- Do not author commits as `codex` or `claude-code`.
- Do not loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT skip the runtime substitution restore (`finally` block must run; otherwise other tests in the same process may see the patched class).
- Do NOT silently fall back to the original `ValueScore` if `ValueScoreH50.from_fundamentals` returns None for many tickers. Excluded tickers are the design intent (D2 step 2); track the exclusion rate in the report.
- Do NOT add `min_sectors_in_portfolio` back as a search axis (S19 lesson).
- Do NOT add valuation (PE/PB/etc.) sub-fields to ValueScoreH50 without first ingesting them PIT-safe (another Hxx slice).

## ValueScoreH50 Schema

```python
@dataclass
class ValueScoreH50:
    ticker: str
    as_of_date: str            # YYYY-MM-DD; the rebalance date this score was computed for
    filing_date: str           # YYYY-MM-DD; latest H50a filing_date <= as_of_date used
    profitability_score: float # [0, 1]
    balance_sheet_score: float # [0, 1]
    cash_flow_score: float     # [0, 1]
    total: float               # equal-weight mean of the three components, [0, 1]
    components_used: dict      # {component_name: [sub_fields_used]} — audit
    
    @classmethod
    def from_fundamentals(cls, ticker, fundamentals_dict) -> "ValueScoreH50 | None":
        # fundamentals_dict is ignored at call-time; the class looks up from a closure-captured
        # H50a panel + the as_of_date wired in at substitution time.
        # Returns None if any component fails the ≥ceil(N/2) sub-field minimum.
        ...
```

Per-ticker per-rebalance score lookup pattern:

```python
# at substitution time:
# H50A_PANEL: {ticker: list[row sorted by filing_date ASC]}; each row carries filing_date and report_period.
# This shape matches D4's locked panel structure and supports PIT-safe scan (walk from end until filing_date <= as_of_date).
H50A_PANEL: dict[str, list[dict]] = load_h50a_jsonl_sorted_by_filing_date()
AS_OF_DATE_REF: list[str | None] = [None]  # mutable holder updated by D4's per-rebalance hook; None until first hook fire.

ValueScoreH50._panel = H50A_PANEL
ValueScoreH50._as_of_ref = AS_OF_DATE_REF
ValueScoreH50._xs_cache = {}  # per-as_of_date cross-section cache; D2-mandated; cleared in finally.
```

The substitution-time wiring above is consumed by the lazy cross-section cache from D2: `from_fundamentals(ticker, _)` reads `_as_of_ref[0]`, lazily builds `_xs_cache[as_of_date]` on first encounter of a new date, and returns the cached per-ticker entry.

D4 (above) is authoritative on how `_as_of_ref[0]` gets updated — Option A (per-rebalance hook in the H42 search module) is the ONLY allowed mechanism. Options B and C are FORBIDDEN per the PIT-leak analysis in D4.

## Provenance Block (must appear in H50b run JSON)

```json
{
  "data_sources": {
    "prices": {"task": "h47", "file": "...", "sha256": "..."},
    "sector_metadata": {"task": "h49a", "file": "...", "sha256": "..."},
    "fundamentals": {"task": "h50a", "file": "data/cn_pit/fundamentals_h50a_pit_quality.jsonl", "sha256": "...", "rows": 12398},
    "universe": {"file": "...", "sha256": "..."}
  },
  "scorer_substitution": {
    "from": "fundamental_backtest.ValueScore",
    "to": "h50b_quality_value_search.ValueScoreH50",
    "patched_modules": ["backtest.experiments.fundamental_backtest", "scripts.h42_strategy_redesign_search"],
    "restored_after_run": true,
    "v1_class_repr": "<class 'fundamental_backtest.ValueScore'>",
    "v2_class_repr": "<class 'h50b_quality_value_search.ValueScoreH50'>"
  },
  "scorer_design": {
    "components": ["profitability", "balance_sheet", "cash_flow"],
    "component_weights": [0.333, 0.333, 0.334],
    "field_aggregation": "winsorize_p1_p99 + cross_sectional_rank + equal_weight_mean",
    "valuation_omitted_reason": "no PIT-safe source per H45 PRD"
  },
  "exclusion_stats": {
    "rebalances_total": <int>,
    "tickers_seen": <int>,
    "exclusion_rate_pct": <float>,
    "exclusion_reasons": {"profitability_below_min": <int>, "balance_sheet_below_min": <int>, "cash_flow_below_min": <int>}
  }
}
```

`validate_h50b` must assert:
- All four `data_sources` entries present with valid sha256.
- `scorer_substitution.from == "fundamental_backtest.ValueScore"` and `.to` starts with "h50b_".
- `scorer_substitution.restored_after_run == true`.
- `scorer_design.components` is exactly `["profitability", "balance_sheet", "cash_flow"]` (no valuation).
- `exclusion_stats.exclusion_rate_pct` is present (no silent skip).

## Smoke Command

```bash
python scripts/h50b_quality_value_search.py \
  --stage-a-limit 2 \
  --stage-b-limit 3 \
  --top-k 1 \
  --output-run /tmp/h50b_smoke.json \
  --output-report /tmp/h50b_smoke.md
```

Expected smoke result:

- Exits 0.
- Reads H50a JSONL + H47 prices + H49a sectors (proven by data_sources block).
- Writes disposable artifacts to `/tmp/`.
- Does NOT touch `backtest/runs/` or `reports/`.
- Smoke MUST exercise the scorer substitution path (provenance block populated; restored_after_run=true).
- Smoke MUST exercise sector_max_weight_pct < 1.0 at least once.

## Full Command

```bash
python scripts/h50b_quality_value_search.py
```

Expected full result:

- Same `--stage-b-limit 200` cap as H42.
- Exits 0 in 15–35 min on this machine.
- Writes canonical H50b JSON + Markdown report.
- Flushes progress.
- All inputs untouched; H42/H48/H49b/H47/H49a/H50a originals untouched; `scripts/h42_strategy_redesign_search.py` and `backtest/experiments/fundamental_backtest.py` untouched.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h42
python scripts/validate_hxx_artifacts.py --artifact h47
python scripts/validate_hxx_artifacts.py --artifact h48
python scripts/validate_hxx_artifacts.py --artifact h49a
python scripts/validate_hxx_artifacts.py --artifact h49b
python scripts/validate_hxx_artifacts.py --artifact h50a
python scripts/validate_hxx_artifacts.py --artifact h50b
pytest tests/test_h50b_quality_value_search.py tests/test_validate_hxx_artifacts.py -q
pytest tests/test_h42_strategy_redesign_search.py -q   # regression: H42 still passes after H50b script loaded
python scripts/validate_ledger_consistency.py --strict
git status --short \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/sector_metadata_sw_l1.csv \
  data/cn_pit/fundamentals_h50a_pit_quality.jsonl \
  data/cn_pit/fundamentals.jsonl \
  backtest/runs/fundamental_value_h42_strategy_redesign_search.json \
  backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json \
  backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json \
  scripts/h42_strategy_redesign_search.py \
  backtest/experiments/fundamental_backtest.py
```

The last `git status --short` must print nothing — all ten files unchanged.

The H42-test regression check is essential: it proves the substitution is process-local and doesn't bleed into other test runs.

## Acceptance Gate

H50b verdict per H42 gate:

- `CANDIDATE_FOR_FORWARD_TRIAL` iff at least one candidate passes ALL H42 conditions.
- `RESEARCH_ONLY` otherwise.

H50b closure checklist:

- [ ] Provenance block in run JSON proves H47 + H49a + H50a + universe data sources via sha256.
- [ ] Scorer substitution block proves runtime patching + restoration.
- [ ] Exclusion rate < 30% (sanity: if >30% of (ticker × rebalance) pairs get excluded, the scorer is too strict or H50a coverage is the bottleneck — surface as warning).
- [ ] Realized sector count of best candidate ≥ 4 at deploy start and end (informational; no hard gate).
- [ ] Inputs + upstream files all untouched (10-file `git status` clean).
- [ ] `scripts/h42_strategy_redesign_search.py` and `backtest/experiments/fundamental_backtest.py` mtimes unchanged from before H50b dispatch.
- [ ] `validate_hxx_artifacts.py` shows `[PASS] h50b`.
- [ ] H42 regression test passes after H50b script has been loaded.
- [ ] Report includes comparison: H42 / H48 / H49b / H50b (verdict + gate-pass + max `beat_HS300_windows` + best deploy excess).
- [ ] Report includes ValueScoreH50 component contribution table for the best candidate's deploy holdings.
- [ ] `docs/strategy-optimization-sync.md` updated with H50b row.
- [ ] `docs/agents/next-slices.md` H50b flipped to `Status: DONE`.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Report Contents

`reports/h50b_quality_value_search_report.md` must include:

- One-line summary of the question H50b is answering.
- Data sources block (H47 + H49a + H50a + universe sha256).
- Scorer substitution block (V1 → V2 with class repr).
- ValueScoreH50 design block (D1–D2 verbatim).
- Search space summary (D6: 5 overlays × 4 sector caps × bounded param grid).
- Exclusion stats: rebalances total, tickers seen, exclusion rate, per-component exclusion counts.
- Acceptance gate definition (H42 verbatim).
- **H42 vs H48 vs H49b vs H50b comparison table** (verdict, gate-pass, max `beat_HS300_windows`, best deploy excess).
- Top 15 H50b candidates ranked by `beat_HS300_windows` desc (D8) with sector-cap columns.
- Per-window detail for the best 3 candidates.
- **ValueScoreH50 component contribution** for the best candidate's deploy-start holdings: per ticker show `(profitability_score, balance_sheet_score, cash_flow_score, total)` and which sub-fields contributed to each.
- Realized sector count at deploy start + end for the best candidate (S19 sanity).
- Explicit final verdict + one-line answer to: "Did the new alpha source move the `beat_HS300_windows` count vs H49b?"

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- Does the monkey-patch actually replace BOTH module-level `ValueScore` references (fundamental_backtest AND h42_strategy_redesign_search)?
- Is the substitution restored in a `finally` block? Does the H42 regression test prove this?
- Are component sub-field minimums (≥ceil(N/2)) actually enforced? Test with a synthetic ticker missing 3 of 4 profitability fields → must return None.
- Is the winsorize+rank computed per-rebalance from the cross-section available **at that rebalance** (PIT-safe), or accidentally using future data?
- Is the `as_of_date → filing_date` lookup using the latest filing_date ≤ as_of_date (PIT-safe), or accidentally future-leaking?
- Are inverted fields (debt_to_equity, accruals_ratio) actually inverted (higher composite = better quality)?
- Does the exclusion rate land at a sensible value (probably 5-15%)? > 30% means H50b is too strict or H50a data is the bottleneck.
- Tests deterministic and free of network calls?

## Closure Note

Record final verdict in `docs/strategy-optimization-sync.md`. Frame the result around the `beat_HS300_windows` delta:

- If verdict is `CANDIDATE_FOR_FORWARD_TRIAL`: state the candidate's params + ValueScoreH50 weights (here just equal) + pass to paper-only monitoring as next step.
- If verdict is `RESEARCH_ONLY` AND `beat_HS300_windows` improved over H49b's 1/5: state the magnitude; propose H50c tightening (re-weight ValueScoreH50 components based on H50b's per-component contribution analysis).
- If verdict is `RESEARCH_ONLY` AND `beat_HS300_windows` stayed at ≤1/5: the alpha redesign did not solve the binding constraint. Escalate to H45 PRD direction #4 (Risk Model Overlay — single-name max weight, volatility-scaled targets, liquidity participation cap, min active names) as the next Hxx; document that "quality-value composite + sector cap + price unification all failed to beat HS300 on the H30 universe" as a project-level finding worth a discussion.
