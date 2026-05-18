# Virtual Trader: 5-Agent Autonomous A-Share Trading System

> A production-grade multi-agent system for Chinese A-share trading,
> running autonomously on Hermes Agent since 2026-04-13.
> 19 days · 24 trades · W18: both accounts beat CSI 300.

![Cumulative Returns](docs/screenshots/cumulative_returns.png)

---

## Why This Exists

A 5-agent autonomous trading system for Chinese A-shares. Inspired by
the multi-agent architectures explored in [tradingagents-fork](https://github.com/zhuosama/tradingagents-ashare-ri/tree/feature/regular-investment), but built ground-up for production deployment with
sector-aware analysis and self-iterating strategy refinement.

## The Coal Price Story (Origin of v1.0.5 / v1.0.6)

While analyzing **中国神华 (Shenhua Energy, 601088 — coal mining)**, the
original single-agent design produced a recommendation based purely on
price-volume + technical indicators, **ignoring coal commodity prices** —
the dominant fundamental driver for coal mining valuations.

**This was not a bug. It was an architectural blind spot**: a single
agent applying generic price-volume logic across all sectors will always
miss sector-specific factors (coal stocks → coal prices; bank stocks →
interest rates; new energy → lithium prices).

The fix had two parts:

1. **Multi-agent sector routing**: the coordinator (`coordinator.py`,
   686 LOC) now routes sector-tagged stocks to specialized analyst
   sub-pipelines. Coal-sector tickers invoke a commodity-aware analyst
   that ingests coal futures + inventory data as additional inputs.

2. **Self-iteration module**: the review agent (`review_agent.py`, 409 LOC)
   post-mortems every decision, identifies factors that were not in the
   input but turned out to be market-relevant, and auto-proposes the next
   strategy version into `strategies/changelog.json`.

**Evidence**: see `analysis/601088-shenhua-commodity-analysis.md` and
`analysis/601088-shenhua-comprehensive-analysis.md`.

**The lesson**: a trading system's value is not how often it's right
on day one — it's whether it can detect its own blind spots.

---

## Architecture

```mermaid
graph TD
    COORD[coordinator.py<br/>686 LOC] --> MA[market_analyst.py<br/>356 LOC]
    COORD --> EP[execution_planner.py<br/>372 LOC]
    COORD --> RC[risk_controller.py<br/>408 LOC]
    COORD --> RA[review_agent.py<br/>409 LOC]
    COORD --> SM[strategy_maintainer.py<br/>439 LOC]
    
    MA -->|sector routing| COAL[Coal Analyst<br/>commodity-aware]
    MA -->|sector routing| TECH[Tech Analyst<br/>momentum-aware]
    
    RA -->|auto-proposes| SM
    SM -->|updates| STRAT[strategies/active.json]
    
    BT[backtest_engine.py<br/>748 LOC] -.->|offline validation| STRAT
```

### Audit Layer (v2026-05-14)

Strategy proposals from `strategy_maintainer.py` are gated by an independent
audit layer before any change reaches `active.json`:

- **Overfitting Auditor**: rejects single-event-triggered strategy changes
  and OOS-degrading proposals (strategy type only)
- **Risk Auditor**: rejects proposals that weaken stops or violate
  `references/risk-rules.md` (all types)
- **Cost & Execution Auditor**: rejects T+0 assumptions, turnover increases
  without signal-strength compensation, or impossible liquidity assumptions
  (all types)

Decision rule: **3/3 unanimous approve → auto-merge**; 2/3 → human review;
≤1/3 → auto-reject with feedback to `review_agent`. INFRA_ERROR (timeout /
malformed LLM response) → pending retry, never fed back as strategy signal.

See `~/.hermes/specs/2026-05-14-virtual-trader-audit-layer-design.md` for
the full design.

### Risk Automation (v2026-05-16)

Reduce-only risk controls are deterministic and do not require manual
judgment:

- single-position concentration breaches generate board-lot sell sizes
  (`sell_shares`) to bring the holding back under its account limit;
- stop-loss and time-stop rules generate deterministic full-position sell
  actions, never buy actions;
- trading-day post-market workflows auto-execute deterministic
  `auto_execute=true` sell actions in the virtual ledger;
- non-trading-day workflows persist those actions to `actions/pending.json`
  for the next trading-day workflow instead of pretending a weekend fill;
- all executed risk reductions are recorded as `source=auto_risk_reduction`
  in the daily trade file.

The current limits are 10% single-stock exposure for `main` and 20% for
`lab`; see `agents/risk_controller.py` and `references/risk-rules.md`.
Open reduce-only actions are visible in the local console health payload and
`GET /api/virtual-trader/risk-actions`. Humans should not decide whether to
execute concentration/stop/time-stop reductions; if a deterministic action is
`auto_execute=true`, the next trading-day post-market workflow executes it or
records an explicit failure.

### Runtime Health

The local console `/health` endpoint reports business health, not just HTTP
liveness. It degrades when the latest workflow failed/degraded, ledger
validation fails, risk actions are pending, or audit retry proposals remain.
Ledger validation is cached briefly and protected by a lock so health probes
do not overload the validator.

`/api/virtual-trader/health` is an alias for the same business-health payload.
The console management pages render that health strip on Data, Strategies,
Backtests, and Export so operators do not lose system context while drilling
into a page. Console write actions (`backtests`, `public-snapshot`,
`import-to-site`) append sanitized summaries to `logs/admin_actions.jsonl`.
The health payload also includes `publicExport` freshness so the console and
website can distinguish fresh, stale, missing, and invalid public snapshots.

Public export is gated by the same strict ledger validator. If
`scripts/validate_ledger_consistency.py --strict` fails, the console export
adapter refuses to write `public-export/public-snapshot.json`; the website must
not publish a snapshot that the local ledger cannot reconcile. The export
manifest records the ledger validation summary used for that write.

### LLM Configuration

Agent LLMs first read `DEEPSEEK_API_KEY` from the process environment, then
`agents/config.json`, then Hermes user configuration (`~/.hermes/config.yaml`
and `~/.hermes/.env`). This keeps API keys out of repo-local files while
allowing cron-launched workflows to initialize the audit layer.

5 specialized agents coordinated by a central coordinator:

| Agent | LOC | Responsibility |
|---|---|---|
| `coordinator.py` | 686 | Workflow orchestration, sector routing |
| `market_analyst.py` | 356 | Market analysis (with sector-aware sub-routes) |
| `execution_planner.py` | 372 | Trade plan generation |
| `risk_controller.py` | 408 | Position sizing + risk gating |
| `review_agent.py` | 409 | Post-trade review + blind-spot detection |
| `strategy_maintainer.py` | 439 | Strategy version iteration |

Plus `backtest/backtest_engine.py` (748 LOC) for offline strategy validation.

**Total: 3,611 lines of Python across 11 files.**

---

## Real Performance (Through 2026-04-30)

| Metric | Main Account | Lab Account | Combined |
|---|---|---|---|
| Capital | 100k (demo) | 30k (demo) | 130k |
| Net Value | 101,167 | 31,478 | 132,645 |
| Cumulative Return | **+1.17%** | **+4.93%** | **+2.04%** |
| Max Drawdown | 0.78% | 0.44% | — |
| Positions | 9 | 2 | 11 |
| Trade Count | 12 | 11 | 23 |
| Days Running | 19 | 19 | — |

**W18 (4/27–4/30): both accounts beat CSI 300 for the first time.**

Best single trade: 寒武纪 (Cambricon, 688256) **+15.96%** over 16 days.

### Charts

| Cumulative Returns | Daily P&L | Drawdown | Position Distribution |
|---|---|---|---|
| ![Cumulative](docs/screenshots/cumulative_returns.png) | ![PnL](docs/screenshots/daily_pnl.png) | ![Drawdown](docs/screenshots/drawdown.png) | ![Positions](docs/screenshots/position_distribution.png) |

### Backtest (2026-04-13 ~ 2026-04-23)

| Metric | Main | Lab | CSI 300 |
|---|---|---|---|
| Cumulative return | -0.10% | +3.84% | +3.02% |
| Annualized return | -2.83% | +187.37% | +129.85% |
| Sharpe ratio | -0.06 | 0.53 | 0.60 |
| Max drawdown | 0.78% | 0.43% | 0.34% |
| Daily win rate | 75% | 75% | 62% |

---

## Tech Stack

- **Language**: Python 3 (3,611 LOC, 11 files)
- **Charting**: matplotlib (4 performance charts, auto-generated)
- **Data Sources**: Tencent Finance API (primary) → Sina Finance (backup) → yfinance (fallback)
- **Orchestration**: Hermes Agent cron jobs (5 scheduled tasks)
- **Storage**: JSON files (intentional — transparent, easy backup, no DB dependency)
- **Backup**: Shell scripts with automatic rotation (10 versions)

---

## Strategy Iteration

### Main Strategy (Value-Trend Hybrid): v1.0.0 → v1.0.5

| Version | Change | Trigger |
|---|---|---|
| v1.0.0 | Initial: high-ROE blue-chip + MA20/60 trend filter | — |
| v1.0.1 | Position floor (avoid going fully flat) | Cash drag identified |
| v1.0.2 | Min lot size (avoid micro-trades) | Fee drag on small positions |
| v1.0.3 | Clear-and-rebuild logic | Stale positions identified |
| v1.0.4 | **Commodity dimension input** | **Shenhua blind spot** |
| v1.0.5 | Refined entry filters + position floor 50% | Backtest cash drag -3.12% |

### Lab Strategy (Sector-Rotation Momentum): v1.0.0 → v1.0.6

| Version | Change | Trigger |
|---|---|---|
| v1.0.0 | Initial: volume breakout + MACD + sector rotation | — |
| v1.0.1 | Volume ratio 1.5→1.3 | Too few signals |
| v1.0.2 | Trailing stop 5%→6% | Premature exits |
| v1.0.3 | Volume ratio 1.3→1.4 | False breakout (阳光电源) |
| v1.0.4 | Parameter optimization | Performance review |
| v1.0.5 | Sector confirmation + turnover filter | False breakout pattern |
| v1.0.6 | **Scaled take-profit for high-beta sectors** | **Cambricon +20% missed** |

Each version triggered by a specific real-trading lesson — see
`strategies/changelog.json` for full rationale chain.

---

## How to Run

```bash
# Backup
scripts/backup.sh

# Ledger and invariant check
python3 scripts/validate_ledger_consistency.py --strict

# Unit tests
python3 -m unittest discover tests/audit_layer

# Local console
python3 console/server.py --port 8765

# Trading workflows
cd agents
python3 coordinator.py --workflow pre_market
python3 coordinator.py --workflow post_market
python3 coordinator.py --workflow weekly_review

# Generate performance charts (and upload)
python3 ~/.hermes/scripts/generate_charts.py [--upload]

# Run backtest
python3 backtest/backtest_engine.py

# Execute trade (single trade; manual operational tool)
python3 scripts/execute_trade.py

# Update accounts and performance
python3 scripts/update_accounts.py
python3 scripts/update_perf.py
```

### Operational Rules

- Run `post_market` only after the A-share close. It performs settlement,
  executes pending deterministic risk reductions, retries audit proposals, and
  then runs strategy maintenance through the audit layer.
- Do not hand-edit account, trade, performance, or report files to make a
  validator pass. Fix the source workflow or run the documented backfill
  process in `docs/data-backfill-runbook.md`.
- `workflow_pre_market_*` artifacts are not ledger settlement dates. Ledger
  coverage is enforced from trade files, daily reports, performance history,
  and settlement-bearing post-market workflows.
- Public export is a two-step local process: write a sanitized snapshot into
  `public-export/`, then import it into the local site. Neither step deploys or
  pushes to GitHub.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VTRADER_HOME` | `~/.hermes/virtual-trader` | Root directory for all data files |

### Hermes Cron Schedule (Beijing Time)

| Time | Task | Schedule |
|---|---|---|
| 08:00 | Daily finance update | Every day |
| 08:10 | Pre-market analysis | Mon–Fri |
| 15:30 | Post-market review | Mon–Fri |
| 08:00 Sat | Weekend full review | Saturday |

---

## Project Journey

This is the **production deployment**.

The research started with a fork of the [TradingAgents framework](https://github.com/zhuosama/tradingagents-ashare-ri/tree/feature/regular-investment)
(LangGraph-based multi-agent trading research project). After studying
its architecture and running exploratory analyses on US stocks, I built
this from scratch as a production-focused, A-share-specialized,
self-iterating system. The fork lives at [tradingagents-fork](https://github.com/zhuosama/tradingagents-ashare-ri/tree/feature/regular-investment).

The two-project arc:

| Project | Role | What it shows |
|---|---|---|
| `tradingagents-fork` | Research playground | Reading and adapting open-source frameworks |
| `virtual-trader` (this) | Production system | Building from scratch, operating in real conditions, iterating from real lessons |

---

## Repository Layout

```
virtual-trader/
├── agents/         # 5 specialized agents + coordinator
│   ├── coordinator.py
│   ├── market_analyst.py
│   ├── execution_planner.py
│   ├── risk_controller.py
│   ├── review_agent.py
│   ├── strategy_maintainer.py
│   ├── config.json
│   └── workflows/     # Workflow execution records
├── accounts/       # Portfolio state (real data excluded, demo files included)
├── analysis/       # Per-stock deep analysis reports
├── backtest/       # Offline backtest engine (748 LOC)
├── insights/       # Daily insights (15 files)
├── market-data/    # Watchlist (cached data excluded)
├── references/     # Data schemas, API specs, risk rules
├── reports/        # Daily / weekly / backtest reports + charts
├── strategies/     # Active strategy + changelog + performance history
├── trades/         # Per-day trade records (14 trading days)
├── docs/           # Architecture, user guide, optimization docs
└── scripts/        # Operational scripts (backup, restore, charts, exec)
```

---

## Disclaimer

Demo / paper trading data. Not investment advice. Account values shown
are anonymized to demo equivalents (e.g., 100k demo units ≈ 1M RMB real),
not real money. Stock symbols are public market identifiers. Trade
decisions reflect the system's actual reasoning at the time, retained
verbatim for educational transparency.

---

## License

MIT

---

## Contact

GitHub: [@zhuosama](https://github.com/zhuosama)
