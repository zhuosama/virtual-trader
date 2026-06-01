# H53-FIX gtja191 IC Bench Report — Tushare-qfq OHLCV+amount (2026-05-31)

**Status:** COMPLETE
**Classification:** Bug fix (denominator correction) — NOT a new Hxx, NOT consuming a Charter §3 slice.
**Executed by:** Hermes (data ingest) + claude-code (ran the bench script Hermes left un-executed; see Execution Note).

---

## 1. Decision

**SIGNAL_NEGATIVE upheld on a COMPLETE, consistent dataset.** 0 of 191 gtja191 factors pass `|mean_ic| > 0.03 AND IR > 0.5`.

The H53 verdict (0/191) now stands on a dataset free of the three defects that compromised the original run: amount column is fully populated (was 100% NaN), universe is aligned (qfq-vs-qfq, was raw-vs-qfq), and the data source is the intended tushare:pro_bar:qfq (was degraded YFinance fallback). The previously-untested amount/volume factor family is now fully measured and does **not** break the ceiling.

Threshold: |mean_ic| > 0.03 AND IR > 0.5, ≥3 factors must pass. **Result: 0 pass.** This is surfaced for claude-code review, not auto-decided.

## 2. Before/After Comparison (H53 vs H53-FIX)

| Metric | H53 (YFinance-degraded) | H53-FIX (tushare-qfq) | Delta |
|--------|------------------------|----------------------|-------|
| Data source | YFinance fallback (degraded) | tushare:pro_bar:qfq | source fixed |
| Amount non-null | 0% | 100.0% | +100pp |
| Universe alignment | Mismatch (raw 697–793 vs qfq 482) | Aligned (475 qfq ∩ 482 close) | fixed |
| Total factors | 191 | 191 | — |
| OK (computable) | 139 | **190** | +51 |
| COMPUTE_FAILED | 18 | **0** | −18 |
| COMPUTE_THIN | 34 | **1** | −33 |
| UNSUPPORTED_COLUMN | 0 | 0 | — |
| Passing threshold | 0 | **0** | 0 |
| Best \|IR\| | 0.258 (alpha_080) | 0.259 (alpha_054) | +0.001 |

**The data fix changed computability dramatically (139→190 OK) but did NOT change the verdict (0 pass).** This is the decisive point: H53's kill was not an artifact of missing data.

## 3. Amount-Family Resolution

The 34 H53 COMPUTE_THIN factors were amount-dependent. With tushare-qfq providing 100%-populated amount, **33 of 34 are now computable (33/34 → OK; 1 still THIN: alpha_138)**; all 18 COMPUTE_FAILED (universe-mismatch broadcast errors) also resolve (18/18 → OK).

- Amount-themed factors now measured (n=18): best is **alpha_163, IR=0.254** — below the 0.5 bar and below the overall best (0.259). Mean |IR| of the amount theme is just 0.107.
- The entire previously-dark factor family is now lit, and it confirms rather than challenges the ceiling.

## 4. Per-Theme Analysis (190 OK factors; themes overlap, a factor may carry several tags)

| Theme | N | Max \|IR\| | Mean \|IR\| |
|-------|---|-----------|------------|
| microstructure | 18 | 0.259 | 0.130 |
| volatility | 26 | 0.259 | 0.104 |
| volume | 81 | 0.255 | 0.126 |
| amount | 18 | 0.254 | 0.107 |
| momentum | 63 | 0.237 | 0.108 |
| reversal | 38 | 0.224 | 0.107 |
| sentiment | 1 | 0.113 | 0.113 |
| liquidity | 2 | 0.049 | 0.043 |

Every theme converges to a max |IR| of ~0.05–0.26. No family — including the now-fully-tested amount/volume families — shows a structural path above 0.5. This reinforces the H53 finding that the ~0.25 ceiling is market-structure-imposed, not a data-coverage artifact.

## 5. Top-5 by |IR|

| Rank | Factor | Mean IC | IR | Obs | Status | Theme | Columns |
|------|--------|---------|-----|-----|--------|-------|---------|
| 1 | alpha_054 | +0.0382 | +0.2590 | 329 | OK | volatility, microstructure | close, open |
| 2 | alpha_163 | +0.0416 | +0.2545 | 329 | OK | reversal | close |
| 3 | alpha_080 | −0.0293 | −0.2519 | 329 | OK | volume | volume |
| 4 | alpha_168 | +0.0327 | +0.2502 | 329 | OK | volume | close, volume |
| 5 | alpha_102 | −0.0292 | −0.2378 | 329 | OK | volume | close, volume |

Best |IR| = 0.259, essentially identical to H53's 0.258. Five independent tests (A1, close-only, R1, H53, H53-FIX) now converge on IR≈0.25.

## 6. Passing Factors

None. 0 of 191 meet |IC|>0.03 AND IR>0.5.

## 7. Data Integrity

- Frozen close matrix sha256: `34f3e38f1245ffd8...` — **unchanged (pre==post)** ✓
- OHLCV source: `tushare:pro_bar:qfq`, 475/482 tickers (7 empty: 000413.SZ, 000671.SZ, 000961.SZ, …), 183,732 rows, amount 100% non-null
- Universe: aligned to H47 frozen close matrix; `.SH→.SS` suffix normalization applied so ticker sets join (475 overlap of 482)
- IC period: 2025-01-01 → 2026-05-18 (329 dates)
- H53 originals (`h53_gtja191_ic_bench.json`, report) untouched ✓

## 8. Charter Impact

H53 SIGNAL_NEGATIVE is **upheld on a complete dataset**. Charter §5 #4 (cross-sectional composite rank from single zoo factors) kill stands — now on stronger evidence (190 vs 139 computable factors, intended data source, aligned universe). Slice budget unchanged: this is a bug fix, not a slice. Surface to claude-code; recommend next Charter §5 hypothesis (gate relaxation / non-momentum family / cost-sensitive / intraday frequency).

## Execution Note (process transparency)

Hermes ingested the tushare-qfq panel and authored the IC bench script (`scripts/h53fix_run_ic_bench.py`, complete and correct) but exited (exit 0) **without running it** — the bench JSON was absent and this report + the sync-note were left as PENDING templates. This repeats the "exit-0 ≠ acceptance" pattern (cf. H49a). Per user authorization, claude-code executed the unmodified script to produce `backtest/runs/h53fix_gtja191_ic_bench.json` and filled this report and the sync-note draft with the real numbers. The script itself was Hermes's work, run verbatim — no logic changes.
