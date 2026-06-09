# H52b — CSI500 SW L1 Sector Metadata Ingestion

## Context

H52a just closed `CANDIDATE_DATASET` with **1074 unique tickers** across 88 monthly CSI500 snapshots (2019-01-31 → 2026-04-30). H52b is the sector classification slice — the CSI500 analogue of H49a (which produced `sector_metadata_sw_l1.csv` for the 481-ticker H30 universe).

The H52f search chain will need PIT sector data for every ticker EVER held in the deploy window. Since the H52a panel covers 1074 tickers (574 more than CSI500's nominal 500 due to 6+ years of turnover), H52b's scope is ALL 1074 unique tickers — not just the current CSI500 members.

H49a's design (single snapshot + latest-wins multi-mapped + 98% gate) is a proven pattern. H52b reuses that pattern with one calibration: coverage gate lowered to **≥95%** to account for delisted historical CSI500 members whose SW L1 classification may have aged out of Tushare's active index_member responses.

## Objective

Ingest SW L1 sector classification from Tushare for every ticker in `data/cn_pit/universe_h52a_csi500.jsonl`, write a stable sector metadata CSV + coverage JSON, and register the H52b artifact family in the validator. Output files use the `h52b_csi500` suffix to avoid collision with H49a's H30 sector file.

## Inputs

- `data/cn_pit/universe_h52a_csi500.jsonl` (1074 unique tickers, 1207 membership intervals — from H52a)
- Tushare token (standard resolution chain)
- Tushare endpoints:
  - `index_classify(level='L1', src='SW2021')` — one call, returns the canonical SW L1 industry code list (~30 industries)
  - `index_member(index_code=<SW L1 code>)` — one call per SW L1 industry (~31 calls)

## Outputs

- `scripts/h52b_build_csi500_sw_industry.py` — ingestion script
- `data/cn_pit/sector_metadata_h52b_csi500.csv` — **PINNED schema, matches `sector_metadata_sw_l1.csv` (H49a output) exactly**:
  ```
  ticker,industry_code,industry_name,source_provider,snapshot_date,ingested_at
  000001.SZ,801780.SI,银行,tushare:index_classify+index_member,2026-05-24,2026-05-24T...Z
  ```
  One row per H52a ticker that successfully mapped.
- `data/cn_pit/sector_coverage_h52b.json` — coverage report, schema mirrors `sector_coverage_h49a.json`:
  - `provenance` (provider, level, src, snapshot_date, snapshot_timestamp)
  - `universe_ticker_count` (= 1074)
  - `mapped_count`, `unmapped_count`, `multi_mapped_count`, `coverage_pct`
  - `industry_histogram`: {sw_code: {name, count}}
  - `unmapped_tickers`: [{ticker, reason}]
  - `multi_mapped`: [{ticker, ts_code, selected: {industry_code, industry_name, in_date, out_date}, alternates: [...]}]
- `reports/h52b_csi500_sw_industry_ingestion_report.md`
- `tests/test_h52b_build_csi500_sw_industry.py`
- `scripts/validate_hxx_artifacts.py` — register `h52b` with `validate_h52b` checker (asserts listed under Provenance Block).
- Raw cache at `data/cn_pit/raw/h52b_tushare_sw_industry/<sw_l1_code>.csv` (one file per SW L1 industry). Add `data/cn_pit/raw/h52b_tushare_sw_industry/` to `.gitignore` BEFORE the full run.

## Hard Prohibitions

- Do NOT modify `data/cn_pit/sector_metadata_sw_l1.csv` (H49a's H30 sector file — separate artifact, must stay intact for H49b/H50b/H51b reproducibility).
- Do NOT modify `data/cn_pit/sector_coverage_h49a.json` (H49a coverage stays immutable).
- Do NOT modify `data/cn_pit/universe_h52a_csi500.jsonl` or `universe_snapshots_h52a_csi500.jsonl` (just produced; downstream dependency).
- Do NOT modify SHA256-protected H28 baseline files (universe.jsonl / universe_snapshots.jsonl / fundamentals.jsonl).
- Do NOT modify any H30/H47/H49a/H50a/H51a artifact.
- Do NOT modify production trading config; do not place live orders.
- Do NOT print or store the Tushare token value.
- Do NOT author commits as `codex` or `claude-code`.
- Do NOT loosen `tests/test_validate_hxx_artifacts.py`.
- Do NOT infer industry from yfinance, ticker prefix, stock name, or any non-Tushare source. If a ticker is unmapped by Tushare, record reason in `unmapped_tickers`; do NOT substitute.

## Design Decisions (locked unless overridden before dispatch)

### D1. Snapshot vs Panel — Single Snapshot (inherit H49a)

Take one snapshot at run time (today's date). Record `snapshot_date` in provenance. Same trade-off H49a accepted: H45 PRD §Data Requirements allows "PIT **or** historically stable" classification; SW L1 stability over the 6-year window justifies a single snapshot for the first iteration. Time-series classification panel is deferred (would be its own slice).

### D2. Multi-Mapped Handling — Latest-Wins (inherit H49a)

A ticker may legitimately appear in multiple SW L1 industries' `index_member` responses (re-classifications over time, or borderline cross-industry firms). H52b picks the most-recent industry by `in_date` as the `selected` industry; older entries persist in the `alternates` array of the `multi_mapped` list for audit.

### D3. Universe Scope — All 1074 H52a Unique Tickers

Iterate over the FULL `universe_h52a_csi500.jsonl` unique-ticker set, NOT just currently-active CSI500 members. Backtests need sector for any ticker EVER held during the deploy window; delisted/exited tickers must be classified the same way.

### D4. Coverage Gate — ≥95% Mapped (calibrated for CSI500 turnover)

H49a's H30 universe hit 100% mapped (481/481). H52b's CSI500 universe has 1074 tickers, of which ~574 are historical (now-exited) CSI500 members. Some of those may have been delisted years ago and aged out of Tushare's active SW L1 `index_member` responses.

Empirical guess: 95-99% coverage. Gate set at **≥95%** to absorb the historical-member margin without blocking. If actual coverage is <95%, surface the failure clearly and we'll either (a) extend the brief with a fallback resolution mechanism for the unmapped tail, or (b) accept the reduced universe with explicit `unmapped_tickers` documentation. Do NOT silently lower the gate.

### D5. Snapshot Source — Tushare SW2021 Only

Single source: `index_classify(level='L1', src='SW2021')` + `index_member`. No CSRC, no GICS, no yfinance fallback. The `provenance.provider` field hard-coded to `"tushare:index_classify+index_member"`.

## Rate-Limit / Retry / Cache Policy

- Per-(SW L1 industry) raw cache: skip the API call if cache file exists and is non-empty.
- HTTP 429 / Tushare error_code → exponential backoff with jitter (initial 2s, doubling, 60s cap, max 5 retries per industry) → then add to `fetch_failures` (`{industry_code, reason}` schema).
- Hard-cap base rate at 5 calls/sec.
- Per-industry failure NEVER aborts the run; just adds to `fetch_failures`.

## Provenance Block (in `sector_coverage_h52b.json`; validate_h52b enforces)

```json
{
  "provenance": {
    "provider": "tushare:index_classify+index_member",
    "level": "L1",
    "src": "SW2021",
    "snapshot_date": "<YYYY-MM-DD>",
    "snapshot_timestamp": "<UTC ISO>"
  },
  "universe_source": "data/cn_pit/universe_h52a_csi500.jsonl",
  "universe_ticker_count": 1074,
  "mapped_count": <int>,
  "unmapped_count": <int>,
  "multi_mapped_count": <int>,
  "coverage_pct": <float>,
  "industry_histogram": {"<sw_l1_code>": {"name": "<industry_name>", "count": <int>}},
  "unmapped_tickers": [{"ticker": "...", "reason": "..."}],
  "multi_mapped": [{"ticker": "...", "ts_code": "...", "selected": {...}, "alternates": [...]}],
  "fetch_failures": [{"industry_code": "...", "reason": "..."}],
  "verdict": "CANDIDATE_DATASET | BLOCKED"
}
```

`validate_h52b` asserts:
- `provenance.provider == "tushare:index_classify+index_member"`
- `provenance.level == "L1"` AND `provenance.src == "SW2021"`
- `provenance.snapshot_date` is a valid YYYY-MM-DD string
- `universe_source == "data/cn_pit/universe_h52a_csi500.jsonl"` (proves H52a is the dependency, not H30)
- `universe_ticker_count == 1074` (matches H52a unique count)
- `coverage_pct >= 95.0`
- `mapped_count + unmapped_count == universe_ticker_count`
- All `unmapped_tickers` entries have non-empty `reason` field
- `industry_histogram` non-empty and no single industry > 40% of universe (sanity)
- `len(fetch_failures) <= 3`
- CSV row count == `mapped_count`

## Coverage Acceptance

- `CANDIDATE_DATASET` if ALL of:
  - `coverage_pct >= 95.0` (≥1021 of 1074 tickers mapped).
  - `multi_mapped_count / universe_ticker_count <= 0.50` (Sanity check to prevent Cartesian explosion).
  - `len(unmapped_tickers)` ≤ 53, each with documented reason.
  - `industry_histogram` includes ≥25 distinct SW L1 industries (sanity: CSI500 spans the full SW L1 spectrum).
  - No single industry exceeds 40% of mapped tickers.
  - `len(fetch_failures) <= 3`.
- `BLOCKED` otherwise — surface specific failing assertion with numerical value.

## Smoke Command

```bash
python scripts/h52b_build_csi500_sw_industry.py \
  --universe data/cn_pit/universe_h52a_csi500.jsonl \
  --limit 20 \
  --raw-dir /tmp/h52b_raw_smoke \
  --output-metadata /tmp/h52b_meta.csv \
  --output-coverage /tmp/h52b_cov.json \
  --output-report /tmp/h52b_rep.md
```

Expected smoke result:
- Exits 0.
- Fetches all ~31 SW L1 industries (cheap), maps a 20-ticker sample from H52a.
- /tmp CSV has correct 6-column schema matching H49a reference.
- /tmp coverage JSON has provenance block populated and `universe_source` pointing at H52a.
- Does NOT touch `data/cn_pit/`, `reports/`, or any production path.

## Full Command

```bash
python scripts/h52b_build_csi500_sw_industry.py
```

~31 Tushare calls (1 index_classify + 30 industry index_members). At 5 calls/sec → ~6 seconds pure network + Tushare overhead; total wall ~1 minute.

## Verification

```bash
python scripts/validate_hxx_artifacts.py --artifact h52b
python scripts/validate_hxx_artifacts.py                                # all 15 artifacts (14 existing + h52b) must PASS
pytest tests/test_h52b_build_csi500_sw_industry.py tests/test_validate_hxx_artifacts.py -q
git status --short \
  data/cn_pit/sector_metadata_sw_l1.csv \
  data/cn_pit/sector_coverage_h49a.json \
  data/cn_pit/universe_h52a_csi500.jsonl \
  data/cn_pit/universe_snapshots_h52a_csi500.jsonl \
  data/cn_pit/universe_h30_candidate.jsonl \
  data/cn_pit/universe.jsonl \
  data/cn_pit/fundamentals.jsonl \
  data/cn_pit/prices_h47_tushare_qfq_candidate.csv \
  data/cn_pit/fundamentals_h50a_pit_quality.jsonl \
  data/cn_pit/liquidity_h51a_daily_amount.csv
# Last command MUST print nothing — 10 protected files unchanged.
```

## Acceptance Gate

- [ ] All 4 outputs exist (script, CSV, coverage JSON, report).
- [ ] Coverage gates met (≥95% mapped, ≥25 industries, no industry >40%).
- [ ] CSV schema exactly matches H49a's `sector_metadata_sw_l1.csv` (6 columns, same order).
- [ ] H49a's H30 sector file untouched.
- [ ] H52a's universe files untouched.
- [ ] `validate_h52b` registered and passing.
- [ ] All 15 family validators PASS.
- [ ] Tests cover: load+parse, multi-mapped latest-wins logic, coverage gate violation behavior, universe_source assertion.
- [ ] No unresolved BLOCKER/HIGH/MEDIUM review findings.

## Closure Note

- Append H52b row to `docs/strategy-optimization-sync.md` under new `## H52b — CSI500 SW L1 Sector Metadata Snapshot` heading: verdict, mapped count + %, unmapped count, multi-mapped count, top 5 industries by ticker share.
- Flip `docs/agents/next-slices.md` H52b entry to `DONE`; state that H52c (Daily Fact Data) is unblocked once H52b completes, as its other dependency (H52a) is already complete.

## Review Prompt

Use `docs/agents/review-prompt-template.md`. Focus areas:
- Is the universe-source assertion in validate_h52b actually checking H52a, not H30? (regression risk after H49a memory)
- Are unmapped tickers' reasons specific enough to inform a follow-up resolution slice? (e.g., "delisted before 2019" vs generic "not in any SW L1 member list")
- Does the multi-mapped latest-wins logic handle the edge case where ALL `in_date` values are equal? (deterministic tie-break required)
- Are tests deterministic and free of network calls?
