# H45 - Next Alpha Source PRD

Status: READY_FOR_IMPLEMENTATION

## Problem Statement

H39-H42 proved that the current value-trend family can produce clean deploy-window behavior, but it does not beat HS300 across time windows. The remaining problem is not a missing stop-loss or a slightly better rebalance frequency. The strategy needs a new alpha source that can explain benchmark-relative excess return before any live or production-shadow promotion is considered.

## Solution

Build the next alpha research track around benchmark-relative stock selection, sector/risk control, and a redesigned quality/value score. The first implementation slice should produce a research-only alpha candidate that can be evaluated with point-in-time fundamentals, a consistent production-grade price matrix, and explicit out-of-sample windows.

The PRD rejects parameter-only tuning as the primary path. Parameter sweeps are allowed only after a new signal design is defined and frozen.

## User Stories

1. As the strategy owner, I want a new alpha source, so that further work is not trapped in local parameter fitting.
2. As the strategy owner, I want every candidate measured against HS300 excess return, so that absolute positive return does not hide benchmark underperformance.
3. As the strategy owner, I want sector-aware selection, so that the portfolio does not accidentally become a concentrated sector bet.
4. As the strategy owner, I want risk-adjusted position sizing, so that volatile names do not dominate drawdown.
5. As the strategy owner, I want a redesigned quality/value score, so that stale valuation fields or one-period accounting noise do not drive trades.
6. As the strategy owner, I want point-in-time financial statement visibility, so that backtests cannot read future disclosures.
7. As the strategy owner, I want production-grade adjusted prices, so that price methodology does not change mid-series.
8. As the strategy owner, I want a fixed train/validation/test split, so that candidate selection and acceptance are separated.
9. As the strategy owner, I want walk-forward windows, so that a candidate must survive different market regimes.
10. As the strategy owner, I want explicit non-goals, so that Hermes does not burn compute on another broad parameter grid.
11. As the strategy owner, I want machine-readable run artifacts, so that reports can be independently validated.
12. As the strategy owner, I want a paper-only fallback path, so that interesting but unpromotable candidates can still collect forward evidence.
13. As the strategy owner, I want all gates recorded in the sync file, so that review findings do not disappear.
14. As the strategy owner, I want Claude or Hermes read-only review before closure, so that gate logic is challenged by another agent.
15. As the strategy owner, I want no production config change from this PRD, so that research cannot leak into live trading.

## Data Requirements

- Universe: point-in-time HS300 membership or a clearly labeled replacement universe with historical constituent evidence.
- Fundamentals: filing-date gated records only. A record is visible only when `filing_date <= as_of_date`.
- Fundamentals schema: no ungated market-derived valuation fields such as current PE, PB, dividend yield, market cap, or FCF yield unless the data source proves point-in-time availability.
- Prices: one consistent adjusted A-share OHLCV source for all stock columns over the complete experiment window.
- Benchmark: HS300 benchmark from the same provider family as the production price matrix.
- Sector classification: point-in-time or historically stable classification with source/provider metadata.
- Liquidity: daily amount or turnover fields sufficient to estimate tradability and execution warnings.
- Audit metadata: every dataset must expose source provider, effective date range, coverage, missingness, and data-quality blockers.

## Candidate Alpha Directions

### Sector-Neutral Relative Strength

Rank stocks by relative strength within sector, then select only names that also beat HS300 momentum over medium horizons. This tests whether H42's relative momentum signal becomes more robust when sector concentration is controlled.

### Quality-Value Composite Redesign

Replace the current broad value score with a smaller, auditable composite:

- profitability quality
- balance-sheet strength
- cash-flow conversion when available
- valuation only when point-in-time market data is proven
- penalties for missing or stale fields

The score must report component contribution per selected ticker.

### Benchmark-Relative Objective

Optimize the research objective around excess return, information ratio, downside capture, and beat-HS300 window count. Absolute return and Sharpe are secondary diagnostics.

### Risk Model Overlay

Add simple constraints before any complex optimizer:

- sector max weight
- single-name max weight
- volatility-scaled target weight
- liquidity participation cap
- minimum number of active names

## Implementation Decisions

- Build a dedicated alpha module with a small interface: compute candidate scores from PIT fundamentals, PIT universe, sector metadata, and price features as of a date.
- Keep the backtest engine responsible for execution, fees, slippage, and account accounting. Do not bury trading simulation inside the alpha module.
- Add a data readiness validator before any strategy run. It should fail closed when universe, prices, benchmark, fundamentals, sector, or liquidity coverage is insufficient.
- Produce JSON and Markdown artifacts for every research run. Add the new artifact family to `scripts/validate_hxx_artifacts.py` before declaring final results.
- Keep H39-H42 artifacts immutable. H45 implementation should create new H46+ or H45-run artifacts instead of rewriting prior reports.
- Use bounded search. First freeze the alpha design, then run a small parameter grid for risk limits and rebalance cadence.
- Label every output `RESEARCH_ONLY` until the full deployment gates below pass.

## Experiment Design

- Development window: use older data only for feature design and sanity checks.
- Validation windows: use multiple rolling or calendar windows to reject fragile candidates.
- Test window: reserve the newest complete window as out-of-sample and do not use it for tuning.
- Forward path: candidates that pass research gates but not deployment gates move to paper-only monitoring before any production-shadow change.
- Baselines: compare against HS300, H42 best ranked candidate, H39 clean candidate, and a simple equal-weight sector-neutral baseline.
- Primary metrics: HS300 excess return, information ratio, beat-HS300 window count, max drawdown, closed sells, losing streak, turnover, liquidity warnings.
- Minimum acceptance sample: at least 30 closed sells in the deploy/test window unless a future PRD explicitly changes the execution gate.

## Deployment Gates

A candidate cannot be promoted to production shadow or live config unless all are true:

- Data-quality gate passes with no survivorship, future-function, filing-delay, or ungated-fundamentals blockers.
- Price-source gate passes on a single adjusted provider over the full experiment window.
- Execution gate passes with no blockers and no unresolved liquidity warnings.
- Multi-window robustness passes with positive return in at least 4/5 windows.
- Benchmark robustness passes with HS300 excess return positive in the deploy/test window and beat-HS300 in at least 2/5 windows.
- Turnover is below the configured warning threshold or is explicitly justified by net excess return after cost.
- JSON/report consistency validator passes for the new artifact family.
- Unit and behavior tests pass.
- Claude or Hermes read-only review has no unresolved BLOCKER/HIGH/MEDIUM findings.

## Testing Decisions

- Test the alpha module through public score outputs, not internal helper ordering.
- Test data readiness as a fail-closed contract with missing sector data, ungated fundamentals, insufficient price coverage, and benchmark gaps.
- Test benchmark-relative objective separately from absolute-return ranking.
- Test sector caps and volatility scaling with small deterministic fixtures.
- Test artifact validator failure on intentional JSON/report mismatches.
- Reuse existing PIT, H42, and H44 test patterns where possible.

## Out of Scope

- Live trading or production config changes.
- Another broad parameter-only grid over the current H42 signal family.
- Rewriting H39-H42 historical artifacts.
- Using current yfinance or current market-derived fundamentals as if they were PIT-safe.
- Complex portfolio optimization before simple sector/risk constraints are proven useful.
- Options, intraday data, margin, shorting, or leverage.

## Further Notes

H45 is a design checkpoint, not a deployment request. The next implementation slice should be either H46 paper-only forward monitoring or H47 production price rebuild, unless the user explicitly chooses to implement the new alpha module first.

