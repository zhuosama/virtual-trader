# Account Data

The `accounts/` directory contains live portfolio state files (`main.json`, `lab.json`)
which are excluded from version control via `.gitignore` because they contain real
portfolio values.

## Demo Files

Two demo account files are included for reference:

- `main.demo.json` — Main strategy account (value-trend hybrid, 100k demo units)
- `lab.demo.json` — Lab strategy account (sector-rotation momentum, 30k demo units)

**These are anonymized demo data.** Stock codes and prices are real public market data.
Portfolio values are scaled to demo equivalents (real values ÷ 10).

## Structure

Each account file contains:
- `id` / `name` — account identifier
- `initial_capital` — starting capital
- `cash` — available cash
- `positions[]` — current holdings (code, name, shares, cost, P&L)
- `portfolio_market_value` — total position value
- `total_value` — cash + positions
- `total_pnl` / `total_pnl_pct` — cumulative profit/loss
- `max_drawdown` — maximum peak-to-trough decline
- `position_pct` — invested percentage
- `trade_count` — total trades executed
- `win_rate` — percentage of profitable positions
- `inception_date` — account creation date
