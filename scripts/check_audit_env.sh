#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

echo "=== Python deps ==="
python3 -c "import pandas, numpy" && echo "  pandas/numpy ok"
for pkg in akshare baostock tushare yfinance; do
  python3 -c "import $pkg" 2>/dev/null && echo "  $pkg ok" || echo "  $pkg missing (provider will skip)"
done

echo "=== env tokens ==="
[ -n "$TUSHARE_TOKEN" ] && echo "  TUSHARE_TOKEN ok" || echo "  TUSHARE_TOKEN missing (Tushare skip)"

echo "=== cache dir ==="
mkdir -p ~/.hermes/virtual-trader/market-data/cache && echo "  cache dir ok"

echo "=== trading_calendar derivation ==="
python3 -c "
import os, sys
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'agents'))
from coordinator import MultiAgentCoordinator
c = MultiAgentCoordinator()
cal = c._derive_trading_calendar()
print(f'  {len(cal)} trading days, last 3: {cal[-3:]}')
"

echo "=== changelog ==="
python3 -c "
import json
with open('strategies/changelog.json') as f:
    cl = json.load(f)
print(f'  {len(cl)} entries, last: {cl[-1][\"date\"]}')
"

echo "=== audit_log ==="
[ -f strategies/audit_log.json ] && echo "  audit_log.json ok" || echo "  audit_log.json missing - create as []"
