# Audit Layer Verification Plan

- Date: 2026-05-15
- Branch: `codex-audit-coordinator-workflow`
- Scope: verify (1) OOS market data + audit integration (committed per spec), and (2) `record_synthesizer` (out-of-spec scope creep) before merge to `main`.
- Author: Claude (review) — sourced from spec/impl gap analysis after commits `3a8e86f..e8d5083`.

## Why this document exists

Unit tests pass (54/54), but unit tests verify *code correctness*, not *production behavior* on real data with real providers and real changelog state. This plan defines five graduated verification layers (L1 → L5) for each subject, plus rollback procedures.

**Do not merge to `main` until at least L1+L2+L3 pass for both subjects.** L4 and L5 are for post-merge production readiness.

---

## Part A — OOS market data + audit integration

Scope: `backtest/market_data.py`, `backtest/oos_window.py`, `backtest/strategy_simulator.py`, `agents/coordinator.py` (new audit flow), tests under `tests/audit_layer/`.

### A-L1: Static review — **done**

Findings from review of commits `3a8e86f..e8d5083`:

- **Pass** — spec coverage complete: 6 provider classes, ProviderResult, Fallback, CachedPriceProvider freshness metadata, `compute_oos_window` pure function, `simulate_strategy` + `build_oos_evidence`, `_build_oos_backtest_evidence` integration in coordinator.
- **Pass** — `INFRA_ERROR` discipline strict: `OOS_IMPORT_FAILED`, `BAD_OOS_WINDOW`, `NO_PRICE_DATA`, `STRATEGY_NOT_FOUND`, `UNSUPPORTED_STRATEGY_DIFF` all distinct.
- **Pass** — `current != proposed` guaranteed: non-numeric diff → `UNSUPPORTED_STRATEGY_DIFF` rather than fake identical metrics.
- **Pass** — `test_oos_backtest.py` no longer hits live yfinance; uses `StaticPriceProvider`.

Known limitations (acceptable for v1, watch in production):

- **Simulator only models breakout entry + 4 exits.** Lab strategy fits this shape; main strategy (value-trend hybrid with ROE/dividend/MA20/MA60) does **not**. For main proposals, the audit signal is "parameter sensitivity proxy", not "what the strategy would actually do". Direction usually aligns, magnitude doesn't.
- **`test_coordinator_audit_flow.py` is shallow** — 1 test, uses `FakeReviewAgent` + `FakeStrategyMaintainer`, does not exercise `_build_oos_backtest_evidence`. Missing: PENDING_RETRY / AUTO_REJECT / UNSUPPORTED_STRATEGY_DIFF / real-provider end-to-end.

### A-L2: Synthetic isolated smoke (mocked prices, no real network)

**Purpose:** verify simulator produces different current/proposed metrics for SUPPORTED diffs and INFRA_ERROR for UNSUPPORTED.

**Run:** `scripts/smoke_audit_v1.py` (create as part of this verification work). See contents below.

```python
# scripts/smoke_audit_v1.py
import json, sys, os
import numpy as np
import pandas as pd

WORKTREE = os.path.expanduser(
    "~/.config/superpowers/worktrees/virtual-trader/audit-coordinator-workflow"
)
sys.path.insert(0, WORKTREE)
from backtest.strategy_simulator import build_oos_evidence

# 21 trading days, 5 stocks + benchmark, deterministic seed
days = pd.date_range("2026-04-15", periods=21, freq="B")
np.random.seed(42)
prices = pd.DataFrame(
    {t: 100 * (1 + np.random.normal(0.001, 0.02, 21)).cumprod()
     for t in ["600519.SS", "601088.SS", "000858.SZ", "300750.SZ", "002230.SZ", "000300.SS"]},
    index=days,
)
current = {
    "parameters": {"max_single_position": 0.10, "take_profit_pct": 15,
                   "stop_loss_pct": 7, "breakout_lookback": 5, "time_stop_days": 10},
    "rules": {"position_sizing": {"total_position_limit": 0.8}},
}
watchlist = {"stocks": [{"code": "600519", "tag": "main"},
                        {"code": "601088", "tag": "main"},
                        {"code": "000858", "tag": "main"}]}
window = {"status": "OK", "start": "2026-04-15", "end": "2026-05-13", "trading_days": 21}

# SUPPORTED — numeric parameter diff
sup = {"account": "main",
       "diff": [{"path": "main_strategy.parameters.take_profit_pct", "old": 15, "new": 12}]}
ev = build_oos_evidence(current, sup, watchlist, prices, window)
print("=== SUPPORTED ===")
print(json.dumps(ev, indent=2, default=str))
assert ev["status"] == "OK", "FAIL: supported numeric diff should be OK"
assert ev["current"] != ev["proposed"], "FAIL: simulator produced identical metrics (audit blind)"

# UNSUPPORTED — prose rule diff
unsup = {"account": "main",
         "diff": [{"path": "main_strategy.rules.exit.take_profit",
                   "old": "涨幅达15%", "new": "涨幅达12%"}]}
ev2 = build_oos_evidence(current, unsup, watchlist, prices, window)
print("\n=== UNSUPPORTED ===")
print(json.dumps(ev2, indent=2))
assert ev2["status"] == "INFRA_ERROR"
assert ev2["reason"] == "UNSUPPORTED_STRATEGY_DIFF"

print("\n✅ A-L2 pass")
```

**Pass criterion:** both asserts hold, and `current["sharpe"] != proposed["sharpe"]` numerically (not equal at 6-decimal precision).

**Common failure modes:**
- `current == proposed` exactly → simulator not differentiating parameters (likely watchlist empty or breakout never triggered)
- Both equal to NaN → all positions hit time_stop on day 10; bump `breakout_lookback` to 3 or extend window
- `UNSUPPORTED_STRATEGY_DIFF` for SUPPORTED diff → `SUPPORTED_PARAMETER_FIELDS` whitelist missing the field

### A-L3: Real-data dry-run (read-only, no commit)

**Purpose:** verify provider chain works on this machine + coordinator's `_build_oos_backtest_evidence` produces real OOS evidence with real changelog state.

**Step A-L3.a — provider availability check:**

```bash
cd /Users/zhuosama/.config/superpowers/worktrees/virtual-trader/audit-coordinator-workflow

python3 -c "
from backtest.market_data import AkshareProvider, BaoStockProvider, TushareProvider, YFinanceProvider
for P in [AkshareProvider, BaoStockProvider, TushareProvider, YFinanceProvider]:
    p = P()
    r = p.get_close_prices(['600519.SS', '000300.SS'], '2026-04-20', '2026-05-10')
    print(f'{P.__name__:25s} status={r.status:12s} sources_used={r.sources_used} missing={r.missing_symbols}')
"
```

**Pass criterion:** at least 2 providers return `status=OK` with `missing_symbols=[]`. If all fail, the provider chain cannot operate — diagnose dependency installs / network / tokens before proceeding.

**Step A-L3.b — coordinator dry-run with synthetic SUPPORTED proposal:**

```python
# Save as scripts/dryrun_audit_v1.py
import sys, os, json
sys.path.insert(0, os.path.expanduser(
    "~/.config/superpowers/worktrees/virtual-trader/audit-coordinator-workflow"))
from agents.coordinator import VirtualTraderCoordinator

coord = VirtualTraderCoordinator()
maintainer = coord.agents['strategy_maintainer']

adjustments = [{
    "type": "parameter_adjustment", "strategy": "main",
    "parameter": "take_profit_pct", "old_value": 15, "new_value": 12,
    "reason": "L3 dry-run",
}]
proposal = maintainer.propose(adjustments)
print(f"proposal_id: {proposal['proposal_id']}")

fake_review = {"accounts": {"main": {"net_value": 1000000}}}
evidence = coord._build_oos_backtest_evidence(maintainer, proposal, fake_review)

print(f"\nstatus: {evidence.get('status')}")
print(f"window: {evidence.get('window')}")
print(f"sources_used: {evidence.get('data', {}).get('sources_used')}")
if evidence.get("status") == "OK":
    print(f"current:  {evidence['current']}")
    print(f"proposed: {evidence['proposed']}")
    print(f"DIFFERENT? {evidence['current'] != evidence['proposed']}")
else:
    print(f"reason: {evidence.get('reason')}")
```

**Pass criterion:**
- `status: OK`
- `sources_used` non-empty (at least one real provider returned data)
- `current != proposed`

**Stop here — do NOT proceed to `audit_layer.review()` or `commit_approved()`.** This dry-run is read-only; running review would write to `audit_log.json` and clutter the audit trail.

**Cleanup after L3:** the proposal was persisted to `strategies/proposals/<id>.json`. Delete it:

```bash
rm -f strategies/proposals/$(date -u +%Y-%m-%dT)*-*.json
# Or surgically: rm strategies/proposals/<specific-id>.json
```

### A-L4: Cron environment readiness

**Purpose:** confirm the host where cron runs (default Hermes profile) has all deps + paths.

Save as `scripts/check_audit_env.sh`:

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

echo "=== Python deps ==="
python3 -c "import pandas, numpy" && echo "  pandas/numpy ✓"
for pkg in akshare baostock tushare yfinance; do
  python3 -c "import $pkg" 2>/dev/null && echo "  $pkg ✓" || echo "  $pkg ✗ (provider will skip)"
done

echo "=== env tokens ==="
[ -n "$TUSHARE_TOKEN" ] && echo "  TUSHARE_TOKEN ✓" || echo "  TUSHARE_TOKEN ✗ (Tushare skip)"

echo "=== cache dir ==="
mkdir -p ~/.hermes/virtual-trader/market-data/cache && echo "  cache dir ✓"

echo "=== trading_calendar derivation ==="
python3 -c "
from agents.coordinator import VirtualTraderCoordinator
c = VirtualTraderCoordinator()
cal = c._derive_trading_calendar()
print(f'  {len(cal)} trading days, last 3: {cal[-3:]}')
"

echo "=== changelog ==="
python3 -c "
import json
with open('strategies/changelog.json') as f: cl = json.load(f)
print(f'  {len(cl)} entries, last: {cl[-1][\"date\"]}')
"

echo "=== audit_log ==="
[ -f strategies/audit_log.json ] && echo "  audit_log.json ✓" || echo "  audit_log.json ✗ — create as []"
```

**Pass criterion:** at minimum `pandas/numpy ✓`, `cache dir ✓`, `trading_calendar` returns ≥40 trading days, `changelog` reads OK. Provider-level ✗ acceptable as long as ≥2 are ✓.

### A-L5: Weekly production observability

**Purpose:** detect silent failure modes after this lands in cron.

Save as `scripts/audit_weekly_health.py`:

```python
import json
from collections import Counter
from datetime import datetime, timedelta

with open("strategies/audit_log.json") as f:
    log = json.load(f)
cutoff = (datetime.now() - timedelta(days=7)).isoformat()
recent = [e for e in log if e.get("audited_at", "") >= cutoff]
n = max(len(recent), 1)

print(f"=== Audit health (last 7 days, n={n}) ===\n")
decisions = Counter(e["decision"] for e in recent)
for d, count in decisions.most_common():
    print(f"  {d:20s} {count:3d}  {100*count/n:.1f}%")

alerts = []
if decisions.get("PENDING_RETRY", 0) / n > 0.3:
    alerts.append(f"⚠ PENDING_RETRY rate {100*decisions['PENDING_RETRY']/n:.0f}% > 30% — provider chain unstable")
if decisions.get("AUTO_MERGE", 0) == 0 and n >= 5:
    alerts.append(f"⚠ 0 AUTO_MERGE over {n} proposals — strategy iteration stalled")
unsupported = sum(1 for e in recent if "UNSUPPORTED_STRATEGY_DIFF" in json.dumps(e))
if unsupported / n > 0.5:
    alerts.append(f"⚠ {100*unsupported/n:.0f}% UNSUPPORTED_STRATEGY_DIFF — simulator scope too narrow")

if alerts:
    print("\n=== ALERTS ===")
    for a in alerts: print(a)
else:
    print("\n✅ no alerts")
```

**Cron schedule recommendation:** weekly, Sunday 09:00 Beijing time, deliver to wecom.

**3-week canary:** if no `AUTO_MERGE` decision in the first 3 weeks, the audit layer is effectively blocking 100% of strategy iteration. Investigate.

---

## Part B — `record_synthesizer` (scope-creep handling)

Scope: `agents/record_synthesizer.py`, `tests/test_record_synthesizer.py`, `records/`, `docs/record-synthesis-cron.md`, and the two associated spec/plan docs.

### B-R0: Scope decision — required before any L-level verification

`record_synthesizer` was **not in the OOS market data spec** that was approved. It is a separate feature (long-term record digest + optional agent review subprocess + append-only candidate writer). Three options:

| Option | Action | When to pick |
|---|---|---|
| **Accept** | Treat as part of this merge after verification | Only if you actually want the feature now |
| **Defer** | `git revert e5e14f8 7332dbe` (or cherry-pick the rest, then merge); keep on a separate branch for later review | Default. Avoids merging unvetted scope |
| **Reject** | Remove files entirely from `codex-audit-coordinator-workflow` before merge | If the feature design itself is wrong |

Decide first. The verification below applies if **Accept** is chosen.

### B-R1: Static review

What it does (from `agents/record_synthesizer.py`):

- Reads (no writes): `agents/workflows/workflow_*.json`, `strategies/audit_log.json`, `strategies/changelog.json`, `reports/daily/*.md`, `reports/weekly/*.md`, `records/events/*.jsonl`
- Builds a digest, synthesizes candidates of 5 types: `memory_candidates`, `career_log_candidates`, `public_story_candidates`, `risk_note_candidates`, `followup_candidates`
- Optionally runs configured reviewer subprocess commands (`hermes chat -q ...`, `codex exec ...`)
- Writes append-only to `records/synthesis/candidates.jsonl`

Concerns (must verify in L-levels):

1. **Containment claim**: spec says "remains the only writer" — verify via static AST scan that NO `open(..., "w")` or `open(..., "a")` paths target anywhere except `records/synthesis/`
2. **Visibility leakage**: events can be marked `visibility="public"`. If audit_log contains private trade details, they could leak to public-story candidates
3. **Reviewer subprocess risk**: `command: [...]` is run as subprocess with digest piped to stdin. A malicious or buggy reviewer config could run arbitrary commands
4. **Unbounded growth**: `candidates.jsonl` appends forever; no rotation
5. **Reviewer command failure handling**: subprocess timeout / non-zero exit not surveyed in this review

### B-R2: Synthetic isolated smoke

Run with empty data dir to confirm it doesn't fabricate candidates from nothing:

```python
# scripts/smoke_record_synthesizer.py
import sys, os, json, tempfile
sys.path.insert(0, os.path.expanduser(
    "~/.config/superpowers/worktrees/virtual-trader/audit-coordinator-workflow"))
from agents.record_synthesizer import RecordSynthesizer

with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, "strategies"))
    os.makedirs(os.path.join(tmp, "agents", "workflows"))
    os.makedirs(os.path.join(tmp, "reports", "daily"))
    # Minimal seeds — empty audit_log, empty changelog
    with open(os.path.join(tmp, "strategies/audit_log.json"), "w") as f: f.write("[]")
    with open(os.path.join(tmp, "strategies/changelog.json"), "w") as f: f.write("[]")
    syn = RecordSynthesizer(data_dir=tmp, since_days=7)
    batch = syn.run(write=False)
    print(json.dumps(batch, indent=2, default=str)[:2000])
    # Pass criterion: should produce mostly empty candidates, NOT fabricate.
    total = sum(len(batch.get(k, [])) for k in (
        "memory_candidates", "career_log_candidates", "public_story_candidates",
        "risk_note_candidates", "followup_candidates"))
    print(f"\ntotal candidates with empty inputs: {total}")
    assert total <= 1, "FAIL: synthesizer fabricated candidates from empty data"
```

**Pass criterion:** with empty inputs, at most 1 candidate emitted (perhaps a "no recent activity" followup). 2+ candidates from empty data = hallucination risk.

### B-R3: Real-data dry-run (no write)

```bash
cd /Users/zhuosama/.config/superpowers/worktrees/virtual-trader/audit-coordinator-workflow
python3 agents/record_synthesizer.py --dry-run --since-days 7 | tee /tmp/rs-dryrun.json
```

(Assumes `record_synthesizer.py`'s `main()` supports `--dry-run`; if not, call `RecordSynthesizer(...).run(write=False)` interactively.)

Inspect the output:

**Pass criteria:**
- Each candidate has `source_refs` pointing to real files
- Candidates' `body` content references factual data points (proposal IDs, dates, decision values), not generic prose
- `visibility="public"` candidates **do not** contain: account net values, position sizes, specific ticker buy/sell prices, audit_log decision details for failed proposals

Read each `public_story_candidate.body` manually. If any contains a number or ticker that shouldn't be public, the visibility classification is broken.

### B-R4: Containment + write-target audit

```bash
# Static AST scan: every open() call in record_synthesizer.py
python3 -c "
import ast
with open('agents/record_synthesizer.py') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == 'open':
            print(f'line {node.lineno}: Path().open(...)')
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'open':
        print(f'line {node.lineno}: open(...)')
"
```

Manually inspect each write-mode `open(...)` call (`"w"` or `"a"`). **Pass criterion:** every write path is under `records/synthesis/` only. Any write outside that directory is a containment violation — escalate.

### B-R5: Reviewer subprocess sandboxing

Reviewers are configured via `records/reviewer_config.example.json` and currently all `enabled: false`. Before enabling any:

**B-R5.a — review the command:**
```bash
# Reviewer command must not exceed read-only ops on stdin.
# Verify each reviewer command does NOT contain: rm, mv, > /, curl, wget, ssh, scp
grep -E "rm |mv |> /|curl |wget |ssh |scp " records/reviewer_config.json 2>/dev/null && echo "DANGEROUS" || echo "command surface looks read-only"
```

**B-R5.b — sandbox the subprocess call site:**

Read `agents/record_synthesizer.py` `_apply_agent_reviews` (or whatever runs subprocess). Confirm:
- Timeout is set (default `subprocess.run(..., timeout=...)`)
- Stdout/stderr captured (not inherited)
- Non-zero exit handled (reviewer error doesn't poison the batch)

If timeout absent → patch before enabling any reviewer.

**B-R5.c — first reviewer enable should be `hermes chat -q` only** (not `codex exec`, which can edit files by design). Run with one reviewer enabled and observe.

### B-R6: Production observability

If accepted into cron, watch:

- `records/synthesis/candidates.jsonl` file size growth (alert if >50MB)
- Each batch's candidate count distribution; if a single batch has >30 candidates, the synthesizer is being noisy
- Public-visibility candidate count per week; any week with a public candidate referencing a private number → review classification logic

---

## Part C — Combined sanity checks (cross-feature)

If both subjects accepted:

**C-1: No interference between subsystems**

```bash
# Run audit smoke + record_synthesizer smoke in one shot; both should pass independently
python3 scripts/smoke_audit_v1.py && python3 scripts/smoke_record_synthesizer.py
```

**C-2: Confirm `record_synthesizer` does not accidentally read or write `strategies/active.json`**

```bash
grep -nE "active\.json|active_json" agents/record_synthesizer.py
```

Expected: zero matches. `record_synthesizer` should be 100% read-from-history (audit_log, changelog) + write-to-records; touching the live strategy file is out of scope.

**C-3: Test cross-pollination — no shared global state**

Run both test suites back-to-back:
```bash
python3 -m unittest discover tests 2>&1 | tail -5
```

Expected: 54 tests (or whatever the current count is) all pass. If `record_synthesizer` tests leave temp dirs or modify global state that breaks audit_layer tests, investigate.

---

## Part D — Rollback procedures

If verification fails after merge:

**D-1: Rollback OOS audit integration only (keep audit_layer v1 from yesterday)**

```bash
cd ~/.hermes/virtual-trader
git revert ecbfe40 209de41 5797a9b e298b90 3a8e86f e8d5083
git push  # if relevant
```

This restores the old coordinator path (direct `apply_adjustments`) while keeping `audit_layer` modules in place. Strategy iteration resumes pre-audit-gate behavior.

**D-2: Rollback record_synthesizer**

```bash
cd ~/.hermes/virtual-trader
git revert e5e14f8 7332dbe
rm -rf records/  # if no other writes happened
```

**D-3: Full rollback to pre-OOS state (audit_layer-v1 tag)**

```bash
cd ~/.hermes/virtual-trader
git reset --hard audit-layer-v1
# ⚠ destructive; coordinate with collaborators
```

Skip `--hard` if you want to keep the codex branch intact for forensics.

---

## Sign-off checklist

Before merging `codex-audit-coordinator-workflow` to `main`:

- [ ] A-L1 reviewed (this document is the artifact)
- [ ] A-L2 `scripts/smoke_audit_v1.py` written and passes
- [ ] A-L3.a provider availability ≥2 providers OK
- [ ] A-L3.b coordinator dry-run produces `current != proposed` real evidence
- [ ] A-L3 cleanup: proposal scratch files removed from `strategies/proposals/`
- [ ] A-L4 `scripts/check_audit_env.sh` passes on the production host (default Hermes profile)
- [ ] A-L5 `scripts/audit_weekly_health.py` written and ready for cron
- [ ] B-R0 scope decision made (Accept / Defer / Reject) — explicit choice recorded here:

      Decision: _________________

      Rationale: _________________

If **Defer/Reject** for record_synthesizer:
- [ ] Commits `e5e14f8` and `7332dbe` reverted on the merge branch

If **Accept** for record_synthesizer:
- [ ] B-R1 static review acknowledged
- [ ] B-R2 empty-input smoke passes (no fabrication)
- [ ] B-R3 dry-run inspection — no PII in public-visibility candidates
- [ ] B-R4 containment scan — all writes inside `records/synthesis/`
- [ ] B-R5 reviewer config audited; no reviewers enabled yet, OR first reviewer is `hermes chat -q` only with timeout enforced
- [ ] B-R6 size monitoring plan documented (or weekly health script extended)

Cross-feature:
- [ ] C-1 combined smoke passes
- [ ] C-2 record_synthesizer doesn't reference active.json
- [ ] C-3 full test suite still 54/54 (or current count) passing

Post-merge:
- [ ] A-L5 weekly health cron registered (recommend Sunday 09:00 Beijing)
- [ ] First 3-week canary watched; investigate if 0 AUTO_MERGE decisions

---

## Notes

This plan is the **first audit checkpoint where the audit layer can actually do work**. Until L2 and L3 pass on this branch, the only thing yesterday's `audit-layer-v1` work proved is that the *plumbing* exists. With this work merged + verified, the audit gate becomes a real production constraint.

Treat the first 3 weeks of live operation as a **calibration window**, not a deployment. Watch the alert thresholds in A-L5. Adjust simulator scope, provider preference, OOS window length based on what shows up. Don't expect the first audit decision to be correct in absolute terms — expect it to be *consistent and inspectable*.
