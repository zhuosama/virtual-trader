# H42 — Strategy Redesign Search Task

## Context

H39 found a candidate that clears the immediate H38 stop-streak blocker:

- Overlay: `price_gt_ma20`
- Params: `top_n=8`, `max_position_pct=0.08`, `stop_loss_pct=0.08`, `take_profit_pct=0.25`, `quality_filter=0.30`, `rebalance_freq_days=63`
- Deploy window: `+8.35%`, Sharpe `1.15`, MaxDD `-3.36%`, closed sells `30`, terminal losing streak `4`

H40 then cleared execution realism after fresh Tushare liquidity:

- Missing liquidity: `0`
- Execution blockers: none
- Execution warnings: none

H41 rejected it for live promotion:

- Positive windows: `3/5`
- Unblocked windows: `1/5`
- Beat HS300 windows: `0/5`
- Deploy window HS300 excess: `-16.85%`

Conclusion: do not keep local tuning around H39. We need a broader redesign search whose objective includes benchmark-relative robustness.

## Hard Rules

- Do not modify production strategy configs.
- Do not modify H34/H35 canonical shadow account state.
- Do not place live orders.
- Use only PIT file sources already in this repo:
  - `data/cn_pit/prices_h38_candidate.csv`
  - `data/cn_pit/universe_h30_candidate.jsonl`
  - `data/cn_pit/universe_snapshots_h30_candidate.jsonl`
- No yfinance fallback.
- No Tushare/network needed for H42.
- Write new H42 artifacts only.

## Required Output

Create:

- `scripts/h42_strategy_redesign_search.py`
- `backtest/runs/fundamental_value_h42_strategy_redesign_search.json`
- `reports/h42_strategy_redesign_search_report.md`

Also append a short H42 row/snapshot to `docs/strategy-optimization-sync.md`.

## Candidate Families To Explore

Build on `scripts/h39_shadow_unblock_search.py`, but do not just rerun the same grid. Add broader families:

1. Benchmark-relative momentum
   - rank or filter by stock return minus HS300 return over 20/60/120 trading days.
   - examples: `rel20 >= 0`, `rel60 >= 0`, `rel120 >= -0.03`.

2. Drawdown and trend quality
   - filter stocks above MA20/MA60/MA120.
   - filter by distance from recent 60-day high, for example current price no worse than 15%-25% below 60-day high.

3. Exit discipline
   - test trailing-stop style exits, e.g. sell when price falls below MA20/MA60 after entry.
   - keep stop-loss/take-profit variants, but include combinations that avoid clustered stop-loss exits.

4. Portfolio/risk throttles
   - top_n in `[5, 6, 8, 10]`
   - max_position_pct in `[0.05, 0.06, 0.08]`
   - rebalance days in `[42, 63, 84, 126]`
   - optional max new buys per rebalance in `[2, 3, 4]`.

5. Robust objective
   - Evaluate every promising candidate across these windows:
     - `cal_2024`: `2024-01-01 -> 2024-12-31`
     - `h1_2025`: `2025-01-01 -> 2025-06-30`
     - `h2_2025`: `2025-07-01 -> 2025-12-31`
     - `ytd_2026`: `2026-01-01 -> 2026-05-21`
     - `deploy_2025_2026`: `2025-01-01 -> 2026-05-21`

## Acceptance Gate

A candidate can be called `CANDIDATE_FOR_FORWARD_TRIAL` only if:

- deploy window execution is not blocked by H34 stop conditions.
- deploy window has no execution warnings.
- deploy closed sells >= 30.
- deploy terminal losing streak < 5.
- positive windows >= 4/5.
- unblocked windows >= 3/5.
- beat HS300 windows >= 2/5.
- deploy excess return > 0.
- max drawdown is not worse than `-8%`.

If none pass, report `RESEARCH_ONLY`.

## Efficiency

The previous H39 grid took too long. Please:

- cache price features per window where practical.
- flush progress output.
- support `--stage-a-limit`, `--stage-b-limit`, and `--top-k` CLI flags.
- default to a bounded run that finishes in under ~30 minutes on this machine.

## Report Contents

The report should include:

- search space summary.
- acceptance gate definition.
- top 15 ranked candidates.
- per-window table for the best 3 candidates.
- explicit verdict.
- next recommended action.

