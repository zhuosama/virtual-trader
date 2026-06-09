# Hermes H53-FIX Task — Tushare-qfq OHLCV+amount Re-ingest & gtja191 IC Re-bench

Date: 2026-05-31
Status: READY for Hermes dispatch
Owner: claude-code (PM/reviewer) / Hermes (executor)
Classification: **Bug fix completing H53 (Bug ≠ Slice — `docs/agents/workflow.md`). NOT a new Hxx number, does NOT consume a Charter §3 slice.** Restating H53's claim on a corrected (complete) factor set with a consistent data source is a denominator fix, not a new research claim.

## Context

H53 (2026-05-31, `reports/h53_gtja191_ic_bench_report.md`) declared SIGNAL_NEGATIVE (0/191) but the run had three data defects that make the "0/191 → kill" headline premature. Review findings (claude-code, 2026-05-31):

1. **Data source silently degraded to YFinance.** Brief intended Tushare→Akshare→YFinance; actual run hit `Tushare blocked (TUSHARE_TOKEN missing)` → Akshare disconnect → YFinance fallback. Root cause: `scripts/ingest_cn_pit_ohlv.py:145` reads `os.environ.get("TUSHARE_TOKEN")` with NO launchctl fallback (unlike `scripts/h33_execution_audit.py:87`). The token IS present in `launchctl getenv TUSHARE_TOKEN` (validated 2026-05-31). Prior tushare scripts (h47/h51a) worked only because they ran under launchd cron which inherits launchctl env; an interactively-dispatched H53 did not.
2. **`amount` column 100% NaN** (verified: `ohlv_h47_supplement.csv` amount non-null = 0). 34 of 191 factors (18%) are COMPUTE_THIN purely because they need `amount`/turnover — an **entire volume/turnover factor sub-family was never actually tested.** The strongest computable factor (alpha_080) is itself a volume factor (IR=0.258), so the untested family is not obviously weak.
3. **Universe + adjustment mismatch.** YFinance OHLV panel = 697–793 raw (un-adjusted) tickers; frozen H47 close matrix = 482 qfq tickers. Mixing raw OHLC with qfq close breaks methodology; misalignment causes the 18 COMPUTE_FAILED broadcast-shape errors and drifting `n_tickers_mean` per factor.

Pre-validated by claude-code (2026-05-31): `ts.pro_bar(ts_code="600519.SH", adj="qfq", ...)` returns open/high/low/close/vol/**amount** — same provider+adjustment (`tushare:pro_bar:qfq`) as the frozen H47 close matrix. This fixes all three defects at once.

## Objective

Rebuild the OHLCV+amount panel from **tushare `pro_bar` qfq** for the **exact 482 H47 universe tickers**, aligned to the frozen H47 close matrix, then re-run the gtja191 IC bench over the same IC period (2025-01-01 → 2026-05-18) and regenerate JSON + report + a corrected sync-doc note.

## Charter Reference

- **Charter:** `docs/research-charter-v1.md` (v1.0-DRAFT).
- **Classification:** Bug fix (denominator correction), NOT a slice. No slice budget consumed. H53's original kill_when is re-evaluated on the corrected factor set.
- **Question (decision-grade):** On the **tushare-qfq OHLCV+amount** panel for the 482 H47 universe over 2025-01-01 → 2026-05-18, do ≥3 gtja191 factors achieve `|mean_ic| > 0.03 AND IR > 0.5`, AND how many of the previously-34 COMPUTE_THIN (amount-dependent) factors now become computable?
- **Threshold:** `|mean_ic| > 0.03 AND IR > 0.5` per factor; ≥3 must pass (inherited from H53, PROPOSED_THRESHOLD, RESEARCH_ONLY-PERMANENT tag).
- **Budget:** `max_wall_hours = 3`, `max_revisions = 1`.
- **kill_when:** "If, on the COMPLETE factor set (amount-family now computable), still <3 factors pass → H53 SIGNAL_NEGATIVE is upheld on a complete dataset; claude-code records the corrected closure. If ≥3 pass → surface to claude-code for review; do NOT auto-escalate to H54."

## Token Plumbing (MANDATORY — do this first)

The dispatch wrapper exports the token before running:
```bash
export TUSHARE_TOKEN="$(launchctl getenv TUSHARE_TOKEN)"
```
If `TUSHARE_TOKEN` is empty/unset at script start → **STOP and surface** (do NOT fall through to Akshare/YFinance — a degraded-source rerun defeats the purpose of this fix). This is a hard gate, not a fallback.

## Inputs

- `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` — frozen close matrix, sha `34f3e38f1245ffd8...`, **READ-ONLY**. Its 482 column tickers define the exact universe to fetch.
- Tushare `pro_bar(adj="qfq")` per ticker, 2025-01-01 → 2026-05-18 (warmup: fetch from 2024-10-01 so factors needing ≤60-bar lookback have history at period start).
- Upstream gtja191 factor files @ commit `bfcf848826750d5f74d0daa636eaffe02b894fad` — REUSE the already-borrowed copies from H53 (`backtest/factors/gtja191/` + `MANIFEST.sha256`); verify sha, do NOT re-fetch unless missing.
- Existing IC bench harness from H53 (the script that produced `backtest/runs/h53_gtja191_ic_bench.json`) — REUSE verbatim, only swap the OHLV input path.

## Outputs (all NEW files — do not overwrite H53 originals)

- `data/cn_pit/ohlcv_h53fix_tushare_qfq.csv` — new qfq OHLCV+amount panel, 482 tickers, long format (date,ticker,open,high,low,close,volume,amount), `source_provider=tushare:pro_bar:qfq`.
- `data/cn_pit/ohlcv_coverage_h53fix.json` — coverage report (ticker count, rows, amount non-null %, per-ticker fetch status, date span).
- `backtest/runs/h53fix_gtja191_ic_bench.json` — re-bench results (one row/factor + summary).
- `reports/h53fix_gtja191_ic_bench_report.md` — corrected report: decision, top-10 by IR, **explicit before/after vs H53** (esp. how many of the 34 COMPUTE_THIN are now OK, how many of the 18 COMPUTE_FAILED resolve under aligned universe), per-family analysis incl. the now-tested amount family.
- `docs/drafts/h53fix-sync-note.md` — DRAFT sync-doc note for claude-code to review before it lands in `strategy-optimization-sync.md` (Hermes drafts under `docs/drafts/` per Charter §6; Hermes does NOT edit the canonical sync doc directly).

## Hard Prohibitions

### Always Applicable (verbatim from `AGENTS.md`)
- **No data fabrication**: if a ticker returns empty/partial from tushare, record it in the coverage JSON as a finding and continue; do NOT invent rows, do NOT "round up" coverage. If frozen `prices_h47...csv` sha changes for any reason → revert + STOP.
- **No source provenance forgery**: `source_provider` MUST be the actual provider. If a ticker silently came from a non-tushare path, do NOT label it `tushare`.
- **Symmetric restore**: any optional/runtime patch uses `try/finally`.
- **Original ingestion verdicts immutable**: do NOT modify `h53_gtja191_ic_bench.json`, `h53...report.md`, `ohlv_h47_supplement.csv`, or any prior Hxx artifact. This task writes NEW `h53fix_*` files only.
- **Exit-code is not acceptance**: every acceptance gate below must be physically verified (file exists + numeric assertion + sha match). Do NOT declare success on exit 0.
- **Modification reporting**: final response enumerates every file created/modified.
- **No silent workarounds**: missing token, tushare permission/quota error, schema mismatch → STOP and surface. Specifically: NO YFinance/Akshare fallback in this task.
- **sha256 audit hooks**: pre+post sha of frozen `prices_h47_tushare_qfq_candidate.csv` (expect `34f3e38f1245ffd8...` unchanged); raise hard on mismatch.

### Task-Specific
- Do NOT modify `data/cn_pit/prices_h47_tushare_qfq_candidate.csv` (Charter §2 frozen).
- Do NOT touch `strategies/active.json`, `agents/coordinator.py`, `backtest/backtest_engine.py`, or any production trading config — this is factor-level IC research only.
- Do NOT promote any factor anywhere. RESEARCH ONLY.
- Do NOT self-decide PASS/FAIL of the Charter threshold — produce numbers, surface to claude-code.
- Do NOT install pip packages (tushare/pandas/numpy already available).

## Smoke Command (run before the full fetch)

```bash
export TUSHARE_TOKEN="$(launchctl getenv TUSHARE_TOKEN)"
python3 -c "
import os,tushare as ts
t=os.environ['TUSHARE_TOKEN']; assert t, 'NO TOKEN — STOP'
df=ts.pro_bar(ts_code='600519.SH',adj='qfq',start_date='20250102',end_date='20250110')
assert df is not None and not df.empty and 'amount' in df.columns, 'pro_bar smoke failed'
print('SMOKE OK rows',len(df),'amount_nonnull',int(df.amount.notna().sum()))
"
```
Expect: `SMOKE OK rows≈6 amount_nonnull≈6`. If it fails → STOP, surface.

## Full Command

Fetch 482 tickers (be polite: tushare pro_bar is rate-limited per credit tier; batch with a small sleep, flush progress every ~50 tickers, save incrementally so an interruption keeps completed tickers). Then run the reused H53 IC harness with `--ohlv data/cn_pit/ohlcv_h53fix_tushare_qfq.csv`.

## Acceptance Gates (ALL must hold; claude-code verifies independently)

| # | Check | Expected |
|---|---|---|
| FIX-AG-1 | frozen close sha unchanged | `34f3e38f1245ffd8...` pre==post |
| FIX-AG-2 | universe alignment | ohlcv panel ticker set == 482 H47 close-matrix tickers (report any tushare fetch gaps) |
| FIX-AG-3 | amount populated | amount non-null ≥ 95% of rows (vs H53's 0%) |
| FIX-AG-4 | provider provenance | coverage JSON `source_provider == tushare:pro_bar:qfq` for all fetched rows |
| FIX-AG-5 | THIN resolved | COMPUTE_THIN count materially drops vs H53's 34 (report exact before/after) |
| FIX-AG-6 | bench completeness | ≥150 factors with status OK (vs H53's 139) |
| FIX-AG-7 | before/after table | report contains explicit H53 vs H53-FIX per-factor and per-family comparison |
| FIX-AG-8 | unit tests | `python3 -m unittest discover tests/audit_layer/` still green |

## Hermes Scope Confirmation (Charter §6)

IN scope: bulk I/O (tushare fetch), sha256 audit, reuse of existing reviewed harness, Markdown drafting under `docs/drafts/`. NOT doing: authoring new strategy scripts, monkey-patching, running the H42 acceptance gate, deciding PASS/FAIL, modifying protected artifacts. The IC harness is REUSED from H53, not newly authored.

## Known follow-up (NOT in this task — for claude-code)
- `scripts/ingest_cn_pit_ohlv.py` should gain a `launchctl getenv` fallback for `TUSHARE_TOKEN` (mirror `h33_execution_audit.py:87`) so future runs don't silently degrade. One-line bugfix commit, separate from this rerun.
- Engine PR split: the uncommitted ~791-line diff in `backtest/market_data.py` + `agents/coordinator.py` must be committed as a standalone engine PR + `engine-frozen-vN` tag before this rerun's result is formally merged into the frozen layer.
