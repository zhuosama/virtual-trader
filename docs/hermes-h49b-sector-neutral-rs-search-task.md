# H49b — Sector-Neutral Relative Strength Search

## Context

H42 (`reports/h42_strategy_redesign_search_report.md`) and H48 (`reports/h48_unified_qfq_h42_rerun_report.md`) both ended at `RESEARCH_ONLY`. The binding constraint in both runs was `beat_HS300_windows = 0/5` for every top deploy-window candidate — the strategies could be positive in absolute terms but never beat the benchmark across the multi-window robustness check.

H45 PRD §Candidate Alpha Directions, item 1 (Sector-Neutral Relative Strength) is the next slice. H49a delivered the required data:

- 481 H30 tickers → SW L1 industry (100% mapped, snapshot 2026-05-23, src=SW2021)
- 30 distinct SW L1 industries present; largest is 医药生物 at 10.6% of the universe
- 122 tickers (25.4%) had multi-mapped raw rows; H49a applied latest-wins to pick a single primary industry per ticker

H49b runs a search with two new dimensions: **(a) intra-sector relative-strength ranking** instead of cross-universe ranking, and **(b) explicit sector-concentration caps** at portfolio construction. The hypothesis: the H42 family failed `beat_HS300` because winners clustered in 1-2 sectors that the benchmark already overweighted, leaving no relative edge. Sector caps + intra-sector RS test that hypothesis.

H49b is a research slice — `RESEARCH_ONLY` is the expected outcome unless a candidate clears the full H42 gate. The slice's primary information value is the **delta in `beat_HS300_windows` count** vs. H42/H48 best candidates.

## Objective

Run a benchmark-relative-strength search with sector-neutral selection over H47 unified-qfq prices + H49a SW L1 sector metadata, and report whether sector constraints close any portion of the H42/H48 `beat_HS300_windows` gap.

## Inputs

- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (H47, 481 tickers + HS300, 1544 days)
- `data/cn_pit/universe_h30_candidate.jsonl`
- `data/cn_pit/universe_snapshots_h30_candidate.jsonl`
- `data/cn_pit/sector_metadata_sw_l1.csv` (H49a, columns: ticker, industry_code, industry_name, source_provider, snapshot_date, ingested_at)
- `scripts/h42_strategy_redesign_search.py` (reused as a library for backtest + window evaluation; NOT modified)
- `backtest/runs/fundamental_value_h42_strategy_redesign_search.json` (baseline for comparison)
- `backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json` (baseline for comparison)

## Outputs

- `scripts/h49b_sector_neutral_rs_search.py` — new search script (not a wrapper; needs sector-aware selection logic)
- `backtest/runs/fundamental_value_h49b_sector_neutral_rs_search.json`
- `reports/h49b_sector_neutral_rs_search_report.md`
- `tests/test_h49b_sector_neutral_rs_search.py`
- `scripts/validate_hxx_artifacts.py` — register `h49b` with `validate_h49b` that mirrors `validate_h42` and additionally enforces a `data_sources` provenance block listing H47 prices SHA256 + H49a sector metadata SHA256
- H49b row appended to `docs/strategy-optimization-sync.md`
- `docs/agents/next-slices.md` H49b entry flipped from `Status: BLOCKED` to `Status: DONE` at closure

## Design Decisions (locked unless overridden before dispatch)

### D1. Sector-neutrality mechanism: **sector_max_weight cap**

Pick top-N stocks by signal globally, then enforce a per-sector portfolio-weight cap at construction time. Drop the next-ranked candidate from over-cap sectors and substitute the next-ranked candidate from any under-cap sector.

**Why**: smallest deviation from H42's selection skeleton; preserves signal quality ordering; per-sector top_k would force portfolio size to ≈ sector_count × k and add many positions with weak signals; equal-weight buckets discard signal magnitude.

**Alternatives considered**: per-sector top_k; equal-weight sector buckets. Surface in the report's "Design choices" section so the verdict reads cleanly.

### D2. New overlay families (added to H42's overlay set, not replacing)

- `intra_sector_rs20`: rank stock 20-day return within its SW L1 sector, keep top quartile
- `intra_sector_rs60`: same, 60-day window
- `intra_sector_rs20_and_rel60_ge_0`: combine intra-sector RS with cross-universe beat-HS300 over 60d
- `intra_sector_rs60_and_rel20_ge_0`: dual-horizon combo

Existing H42 overlays (rel20_ge_0, rel60_ge_0, price_gt_ma60, etc.) also run under sector caps — they form the baseline that isolates the contribution of the sector cap from the contribution of intra-sector ranking.

### D3. New parameter grid axes (multiplicative with H42 base grid)

- `sector_max_weight_pct` ∈ {0.20, 0.25, 0.30, 0.40, 1.00}  (1.00 = no cap = H42 control)
- `min_sectors_in_portfolio` ∈ {1, 5, 7}  (1 = no constraint)

Base H42 axes (top_n, max_position_pct, stop_loss_pct, take_profit_pct, quality_filter, rebalance_freq_days) remain. To keep wall clock bounded, `--stage-b-limit 200` default with the same Stage A → Stage B → Stage C pipeline H42 uses. Sector-axis combinations enter the Stage A overlay screening, so the grid does not multiply uncontrollably.

### D4. Multi-mapped handling

Use the single primary industry per ticker from `sector_metadata_sw_l1.csv` (one row per ticker). Alternates from H49a's `multi_mapped` field are ignored in H49b. The report must state this and call out: if H49b candidates concentrate in industries that are heavily represented in `multi_mapped`, the result needs follow-up.

### D5. Acceptance gate

Use the H42 gate VERBATIM (deploy not blocked, ≥30 sells, terminal streak <5, positive windows ≥4/5, unblocked ≥3/5, beat-HS300 ≥2/5, deploy excess >0, MaxDD > −8%). **Do not tighten or loosen.** The information value of H49b is in measuring whether sector neutrality moves `beat_HS300_windows` upward, not in moving the goalposts.

### D6. Ranking

Stage C ranks candidates by `beat_HS300_windows` count (descending), then by `deploy_excess_return` (descending) as tiebreaker. H42's default rank was `deploy_sharpe`; H49b changes the rank because the binding constraint is `beat_HS300`. This makes the top of the ranked table immediately answer "did sector neutrality help?"

## Hard Prohibitions

- Do not modify `scripts/h42_strategy_redesign_search.py` search logic, gate thresholds, or window definitions. Import functions from it; do not edit it. If a function is private and needs to be public, add a re-export at the bottom of `scripts/h49b_sector_neutral_rs_search.py` rather than editing the H42 file.
- Do not overwrite any input artifact: H30 universe, H38 prices, H47 prices, H47 coverage JSON, H49a sector metadata CSV, H49a coverage JSON, H42/H48 run JSONs, H42/H48 reports.
- Do not modify production trading config.
- Do not place live orders.
- No network: do not refetch prices, do not call Tushare or yfinance.
- Do not author commits as `codex` or `claude-code`.
- Do not loosen `tests/test_validate_hxx_artifacts.py` — preserve the strict "missing files = FAIL" guarantee. Add `test_h49b_artifact_selection` mirroring h47/h48/h49a.
- Do not silently fall back to "no sector cap" when sector data for a ticker is missing. Either the H49a CSV covers a ticker, or the ticker is excluded from the run with the reason logged in the JSON. (H49a has 100% coverage; this is a defensive guarantee.)

## Provenance Block (must appear in H49b run JSON)

```json
{
  "data_sources": {
    "prices": {
      "task": "h47",
      "file": "data/cn_pit/prices_h47_tushare_qfq_candidate.csv",
      "sha256": "<recomputed>"
    },
    "sector_metadata": {
      "task": "h49a",
      "file": "data/cn_pit/sector_metadata_sw_l1.csv",
      "sha256": "<recomputed>",
      "snapshot_date": "<from H49a coverage>",
      "provider": "tushare:index_classify+index_member"
    },
    "universe": {
      "file": "data/cn_pit/universe_h30_candidate.jsonl",
      "sha256": "<recomputed>"
    }
  }
}
```

`validate_h49b` must assert all three `task`/`file`/`sha256` fields are present and that `prices.task == "h47"` and `sector_metadata.task == "h49a"`.

## Smoke Command

```bash
python scripts/h49b_sector_neutral_rs_search.py \
  --stage-a-limit 3 \
  --stage-b-limit 3 \
  --top-k 2 \
  --output-run /tmp/h49b_smoke.json \
  --output-report /tmp/h49b_smoke.md
```

Expected smoke result:

- Exits 0.
- Reads H47 prices + H49a sector metadata (proven by `data_sources` block in the smoke JSON).
- Writes disposable artifacts to `/tmp/`.
- Does not touch `backtest/runs/` or `reports/`.
- Smoke must include at least one run with `sector_max_weight_pct < 1.0` to exercise the new code path; do not let the small Stage A/B limits skip the sector-cap branch.

## Full Command

```bash
python scripts/h49b_sector_neutral_rs_search.py
```

Expected full result:

- Same `--stage-b-limit 200` default as H42.
- Exits 0 in ~15–35 min on this machine (wider than H42 because of sector-axis combos; if it threatens to exceed 45 min, narrow Stage A intelligently — do not pad the search budget).
- Writes the canonical H49b JSON + Markdown report.
- Flushes progress.
- H42 / H48 / H47 / H49a / H30 inputs untouched.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h42
python scripts/validate_hxx_artifacts.py --artifact h47
python scripts/validate_hxx_artifacts.py --artifact h48
python scripts/validate_hxx_artifacts.py --artifact h49a
python scripts/validate_hxx_artifacts.py --artifact h49b
pytest tests/test_h49b_sector_neutral_rs_search.py tests/test_validate_hxx_artifacts.py -q
python scripts/validate_ledger_consistency.py --strict
git status --short \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/sector_metadata_sw_l1.csv \
  backtest/runs/fundamental_value_h42_strategy_redesign_search.json \
  backtest/runs/fundamental_value_h48_unified_qfq_h42_rerun.json \
  scripts/h42_strategy_redesign_search.py
```

The last command must print nothing.

## Acceptance Gate

H49b verdict reports the H42 gate verbatim:

- `CANDIDATE_FOR_FORWARD_TRIAL` if at least one candidate passes ALL H42 conditions.
- `RESEARCH_ONLY` otherwise.

H49b closure checklist:

- [ ] Wrapper proves H47 + H49a data sources via the provenance block; `validate_h49b` enforces it.
- [ ] Originals untouched (`git status` clean for the six paths above).
- [ ] All five validator artifact families pass.
- [ ] Report contains an explicit comparison table: H42 verdict + gate-pass + best `beat_HS300_windows` vs. H48 vs. H49b.
- [ ] Report's top-15 ranking is by `beat_HS300_windows` descending (with deploy excess as tiebreaker), per D6.
- [ ] Report's "Design choices" section names the three sector-neutrality mechanisms considered and why D1 was chosen.
- [ ] `validate_h49b` registered in `scripts/validate_hxx_artifacts.py`.
- [ ] `docs/strategy-optimization-sync.md` updated with the H49b row.
- [ ] `docs/agents/next-slices.md` H49b flipped to `Status: DONE`.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Report Contents

`reports/h49b_sector_neutral_rs_search_report.md` must include:

- One-line summary of the question H49b is answering.
- Data sources block (H47 prices + H49a sector metadata with sha256 + snapshot_date).
- Search space summary: H42 overlays + new H49b overlays; H42 base grid + new sector axes; total runs.
- Design choices section: D1 mechanism (with the two rejected alternatives stated), D2 new overlays, D3 new axes, D4 multi-mapped handling, D5 gate-unchanged rationale, D6 ranking change.
- **H42 vs H48 vs H49b comparison table** (verdict, gate-pass, max `beat_HS300_windows` across top-15, best deploy excess).
- Top 15 H49b candidates ranked by `beat_HS300_windows` desc, with sector-cap parameters surfaced as columns.
- Per-window detail for the best 3 H49b candidates.
- Sector distribution of the best candidate's selected stocks at deploy-window start, deploy-window end (sanity: cap actually held).
- If any candidate's selected stocks concentrate in industries from H49a's `multi_mapped` list, surface that as a "needs follow-up" note (per D4).
- Explicit final verdict + one-line answer to: "Did sector-neutral selection move the `beat_HS300_windows` count vs. H42/H48?"

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:

- Is the sector cap actually enforced at construction (not just post-hoc filtered)?
- Does the new ranking (D6) correctly handle the all-zero `beat_HS300_windows` case (ties broken by deploy excess descending)?
- Are intra-sector RS overlays computed using only data available as-of each rebalance date (no future leakage)?
- Does the provenance block read SHA256 of the actual files used by the run (not hardcoded)?
- Are tests deterministic and free of network calls?

## Closure Note

Record final verdict in `docs/strategy-optimization-sync.md`. Frame the result around `beat_HS300_windows` delta:

- If verdict is `CANDIDATE_FOR_FORWARD_TRIAL`: state the candidate's sector-cap parameters and pass it to paper-only monitoring next.
- If verdict is `RESEARCH_ONLY`: state the best `beat_HS300_windows` achieved. If it is still 0/5 across all candidates, escalate to the next H45 PRD alpha direction (Quality-Value composite redesign or Benchmark-Relative Objective). If it is 1/5 or 2/5, propose a tighter H49c grid around the best-performing sector_max_weight value.
