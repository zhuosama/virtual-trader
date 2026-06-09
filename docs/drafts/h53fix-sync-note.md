## H53-FIX — Tushare-qfq OHLCV+amount Rerun — SIGNAL_NEGATIVE upheld (2026-05-31)

**Verdict:** SIGNAL_NEGATIVE upheld on a COMPLETE dataset. DRAFT — pending claude-code closure into this doc.
**Classification:** Bug fix completing H53 (denominator correction). NOT a new Hxx number, does NOT consume a Charter §3 slice.

### Original question (restated)

On the tushare-qfq OHLCV+amount panel for the 482 H47 universe over 2025-01-01 → 2026-05-18, do ≥3 gtja191 factors achieve |mean_ic| > 0.03 AND IR > 0.5, AND how many of the previously-34 COMPUTE_THIN (amount-dependent) factors become computable?

**Answer: 0 factors pass (unchanged from H53). 33/34 amount-THIN + 18/18 FAILED now computable (1 still THIN: alpha_138).**

### What was delivered

- Re-ingested OHLCV+amount from tushare `pro_bar(adj="qfq")`: 475/482 H47 tickers, 183,732 rows, amount 100% non-null, source_provider=`tushare:pro_bar:qfq` (7 tickers returned empty, recorded in coverage JSON).
- Re-ran the full gtja191 IC bench (191 factors) with the same harness, only swapping the OHLV input to the aligned tushare-qfq panel.
- **Computability: 139 OK (H53) → 190 OK (H53-FIX).** COMPUTE_FAILED 18→0, COMPUTE_THIN 34→1 (alpha_138).
- **Verdict unchanged: 0/191 pass |IC|>0.03 & IR>0.5.** Best |IR|=0.259 (alpha_054) vs H53's 0.258 (alpha_080).
- Amount/volume families now fully tested: amount-theme max |IR|=0.254, volume max |IR|=0.255 — both below ceiling.

### Root cause (of H53's data defects)

1. **Token plumbing:** `scripts/ingest_cn_pit_ohlv.py:149` reads `os.environ.get("TUSHARE_TOKEN")` with no launchctl fallback; the token lives in `launchctl getenv TUSHARE_TOKEN`. Cron-launched scripts (h47/h51a) inherit it; the interactively-dispatched H53 did not → silent degrade to YFinance.
2. **No amount in YFinance** → 34 factors 100% untested in H53.
3. **Raw OHLV (697–793 tickers) mixed with qfq close (482)** → 18 broadcast-shape COMPUTE_FAILED + drifting effective universe.

### What we keep

- gtja191 factor files unchanged (commit `bfcf8488`); IC harness reused verbatim.
- H53 report + JSON preserved as historical record (untouched, mtime unchanged).
- New `ohlcv_h53fix_tushare_qfq.csv` (tushare-qfq, amount-complete) — reusable OHLCV layer for any future OHLV-dependent slice, superseding the YFinance `ohlv_h47_supplement.csv`.
- Five-test convergence (A1 / close-only / R1 / H53 / H53-FIX) on IR≈0.25 ceiling — now on the strongest data footing.

### What we throw away

- `ohlv_h47_supplement.csv` as a usable factor source (retained on disk, superseded; amount all-NaN, universe mismatched).
- Any remaining doubt that H53's kill was a data-coverage artifact — falsified: full coverage (190 OK) gives the same 0/191.

### Lessons codified

- **Token plumbing:** ingest scripts must check `launchctl getenv` as fallback (mirror `h33_execution_audit.py:87`), else interactive dispatch silently degrades the data source. One-line bugfix queued (separate commit).
- **tushare pro_bar returns `.SH`; H47 matrix uses `.SS`** — suffix normalization is required for universe join.
- **`ts.pro_bar()` works for qfq OHLCV+amount; `pro.pro_bar()` does not.**
- **A clean kill needs a clean dataset.** The H53 "0/191" was correct but rested on 139/191 computable + degraded source; the defensible kill is H53-FIX's 0/191 on 190/191 computable + intended source. Restating a verdict on a corrected denominator is a bug fix, not a new slice (workflow.md Bug≠Slice).
- **exit-0 ≠ acceptance (again):** Hermes wrote a correct bench script but never ran it and exited clean, leaving PENDING templates. Acceptance must verify the JSON exists with real numbers, not trust process exit.

### Next move

H53 SIGNAL_NEGATIVE upheld on complete data → Charter §5 #4 kill stands on stronger evidence. claude-code to close this note into `strategy-optimization-sync.md` and recommend the next Charter §5 hypothesis (gate relaxation to IR>0.3 / non-momentum family / cost-sensitive re-eval / intraday frequency). Daily-bar zoo-factor path is exhausted across both close-only and full-OHLCV configurations.
