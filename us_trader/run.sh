#!/usr/bin/env bash
# us_trader 每日批跑入口
# cron: 0 22 * * 0-4 (UTC 22:00 = 北京次日 06:00,美股工作日)
# run_daily 内部会用 us_tradecal 再判断是否交易日,非交易日自动 skip。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

exec /usr/bin/python3 -m us_trader.pipeline.run_daily
