# Data Backfill Runbook

> 适用场景：系统停摆后补运行历史交易日

## 1. 数据来源优先级

| 优先级 | 来源 | 用途 |
|--------|------|------|
| 1 | cron workflow output / coordinator save_workflow_result | 交易记录、账户快照 |
| 2 | yfinance (历史) / 腾讯 API (当日) | 收盘价、HS300 |
| 3 | 新浪 API (备选) | 收盘价 |

## 2. 交易 execution_type 标注

所有 trade item 必须包含：

```json
{
  "account": "main",
  "action": "sell",
  "code": "601088",
  "name": "中国神华",
  "price": 45.18,
  "shares": 2000,
  "execution_type": "backfill",
  "source": "yfinance_replay",
  "generated_at": "2026-05-15T23:00:00",
  "rationale": "策略回放: 时间止损22天无涨幅"
}
```

| execution_type | 含义 | 使用场景 |
|----------------|------|----------|
| `executed` | cron 当日实际执行 | 正常每日运行 |
| `backfill` | 回补时从策略信号回放生成 | 系统停摆后补运行 |
| `simulated` | 假设性推演（不影响账户） | 策略测试、what-if 分析 |

| source | 含义 |
|--------|------|
| `live_cron` | cron agent 实时执行 |
| `yfinance_replay` | yfinance 历史数据回放 |
| `manual_repair` | 人工手动修复 |

## 3. 无交易日处理

- trade file: `"trades": []`, `"is_trading_day": true`
- daily report: "今日操作：无交易"
- performance_history: 仍然必须有 entry（mark-to-market）
- 非交易日（节假日）: performance_history 加 `"non_trading_day": true`

## 4. Benchmark 缺失处理

- **绝不写 0 占位**
- yfinance 无数据 → 腾讯 API 补
- 完全无法获取 → `"hs300_pct": null, "hs300_pct_reason": "data_unavailable"`
- 真实 0% → `"hs300_pct": 0, "benchmark_source": "yfinance", "benchmark_verified": true`

## 5. 回补步骤

```bash
# 1. 用 yfinance 拉历史价格
python3 -c "import yfinance as yf; ..."

# 2. 策略信号回放（生成 trade records）
python3 scripts/backfill_replay.py --start 2026-05-08 --end 2026-05-15

# 3. 更新 performance_history（按日期 upsert，不 append）
python3 -c "..."

# 4. 生成日报
python3 scripts/generate_daily_reports.py --dates 2026-05-08 2026-05-15

# 5. 数据一致性校验（必须 PASS）
python3 scripts/validate_ledger_consistency.py --strict

# 6. 全量测试（必须 PASS）
python3 -m unittest discover tests/audit_layer

# 7. 生成对账表
python3 scripts/generate_reconciliation_table.py 2026-05-06 2026-05-15

# 8. 精确提交（不用 git add .）
git add scripts/validate_ledger_consistency.py ...
git commit -m "[fix] ..."
```

## 6. 回补后必须验证

```bash
python3 scripts/validate_ledger_consistency.py --strict   # exit 0
python3 -m unittest discover tests/audit_layer              # 91/91 PASS
git diff --check                                            # 无 whitespace 错误
```
