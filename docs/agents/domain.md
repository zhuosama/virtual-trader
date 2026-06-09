# Domain Docs

Layout: single-context repo.

## Primary Domain Areas

- Strategy research and backtesting: `backtest/`, `scripts/h3x_*.py`, `scripts/h4x_*.py`, `reports/`
- Point-in-time China market data: `data/cn_pit/`, `scripts/ingest_cn_pit_data.py`, `docs/h38_price_source_policy.md`
- Value account and paper/shadow trading: `value_account/`, `scripts/h35_shadow_account_executor.py`
- Multi-agent workflow: `docs/agents/`, `docs/strategy-optimization-sync.md`
- Ledger and daily audit: `scripts/validate_ledger_consistency.py`, `tests/audit_layer/`

## Consumer Rules

- Read `docs/agents/workflow.md` before starting any Hxx task.
- Read `docs/strategy-optimization-sync.md` before changing strategy research state.
- Read `docs/h38_price_source_policy.md` before using H38+ price artifacts.
- Treat H38-H42 strategy outputs as research-only unless a later task explicitly passes all promotion gates.

