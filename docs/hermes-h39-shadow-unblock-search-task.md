# H39 — Shadow Strategy Unblock Search

## Background

H38 extended the shadow account price data through `2026-05-21` and reran the H35 shadow executor on H38-specific paths.

Current H38 state:

- report: `reports/h38_shadow_rerun_report.md`
- state: `value_account/reports/h38_shadow_state.json`
- trades: `value_account/logs/h38_shadow_trades.jsonl`
- price file: `data/cn_pit/prices_h38_candidate.csv`
- universe: `data/cn_pit/universe_h30_candidate.jsonl`
- snapshots: `data/cn_pit/universe_snapshots_h30_candidate.jsonl`
- status: `blocked`
- blocker: `consecutive_losing_sells:5 >= 5`
- warning: `annualized_turnover:1.80x > 1.5x`

H36 already showed that a small grid over `stop_loss_pct`, `take_profit_pct`, `max_position_pct`, and `quality_filter` found 0/48 combinations clearing all H35 gates. H39 must therefore test additional strategy overlays, not simply loosen the gate.

## Goal

Find one or more non-destructive candidate strategy overlays that clear the H35/H38 execution blocker without changing the H34 stop-condition thresholds.

Success means:

- terminal losing sell streak `< 5`,
- no execution blockers under existing H34 gates,
- data quality remains clean,
- total return stays positive,
- Sharpe stays `>= 1.0`,
- closed sells `>= 30` unless explicitly reported as an insufficient-trade compromise,
- annualized turnover warning ideally clears or at least improves.

## Non-Destructive Rules

Do not modify:

- `value_account/h34_shadow_account_config.json`
- `accounts/*.json`
- `trades/**`
- `reports/daily/**`
- `strategies/active.json`
- any H35/H36/H37/H38 canonical artifact
- `backtest/experiments/fundamental_backtest.py` unless a tiny bugfix is explicitly required and covered by tests

Allowed H39 artifacts:

- `scripts/h39_shadow_unblock_search.py`
- `backtest/runs/fundamental_value_h39_unblock_search.json`
- `reports/h39_shadow_unblock_search_report.md`
- optional focused tests under `tests/` if useful

## Candidate Overlays To Test

Implement H39 in a separate script by copying/adapting the current fundamental backtest loop, not by changing production strategy config.

At minimum test these overlays, individually and in small combinations:

1. Entry momentum filter:
   - require 20D return >= `-5%`, `0%`, or `+3%`
   - require 60D return >= `-10%`, `-5%`, or `0%`
2. Trend filter:
   - require price > MA20 or MA60 at entry
3. Volatility filter:
   - exclude candidates with 20D daily volatility above `3%`, `4%`, or `5%`
4. Market regime filter:
   - skip new buys when HS300 is below MA60 or its 20D return is negative
5. Rebalance/entry throttles:
   - `rebalance_freq_days` in `[63, 84, 126]`
   - top_n in `[5, 6, 8]`
   - max_position_pct in `[0.05, 0.06, 0.08]`
6. Existing base params around H38:
   - stop_loss_pct in `[0.08, 0.10, 0.12]`
   - take_profit_pct in `[0.18, 0.22, 0.25]`
   - quality_filter in `[0.30, 0.35, 0.40, 0.45]`

Keep the grid bounded. Use staged search if necessary:

- Stage A: broad one-overlay screen.
- Stage B: combine the top 20 overlay families.
- Stage C: rank candidates with all gates applied.

## Metrics To Report

For each top candidate:

- params + overlay settings,
- total return,
- annual return,
- Sharpe,
- max drawdown,
- HS300 return,
- excess return,
- closed sells,
- win rate,
- profit factor,
- terminal losing sell streak,
- monthly one-way turnover,
- annualized turnover,
- execution blockers,
- execution warnings,
- last 8 closed sells with date/ticker/exit_reason/pnl_pct.

## Required Comparisons

Compare best candidates against H38 baseline:

- return delta,
- Sharpe delta,
- max drawdown delta,
- trade-count delta,
- streak delta,
- turnover delta,
- whether the stop-loss cluster from `2026-01-16` entries is avoided or only delayed.

## Acceptance

- Write JSON and Markdown reports.
- If no candidate clears all gates, say `KEEP BLOCKED` and list the least-bad alternatives.
- If candidates clear all gates, say `CANDIDATE_FOUND` but do not modify H34 config; present a recommended dry-run-only candidate and required next validation.
- Run at least:

```bash
python scripts/h39_shadow_unblock_search.py
python scripts/validate_ledger_consistency.py --strict
```

If a new test is added, run the focused test too.
