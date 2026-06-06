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

## 7. Loader Registry Fallback 行为

> 适用场景：Tushare token 不可用或数据源出现可用性问题时

### 原 runbook 建议

此 runbook 此前建议在 Tushare token 不可用时，手动将数据源切换为 `yfinance` 并重跑 `ingest_cn_pit_data.py`。

### 新行为 (ENGINE-LOADER-V1)

从 2026-05-30 起，`backtest/market_data.py` 中的 `FallbackMarketDataProvider` 会在 Tushare token 缺失时**自动**按优先链路回退：

```
Tushare → Akshare → YFinance
```

- 每个 loader 通过 `precheck()` 验证自身可用性（token 检查、库安装检查、endpoint 检查）
- precheck 失败的 loader 会被跳过，记录到 `precheck_log`
- **所有 loader 都不可用时**：`FallbackMarketDataProvider` 直接 `raise LoaderBlockedError`，**不会**静默 fallback 回空数据——这是 AGENTS.md 要求的 STOP-on-missing 语义

### 溯源写入

每次价格拉取完成后，`data/cn_pit/metadata.json` 中 `data_sources.prices` 块记录完整的溯源信息：

| 字段 | 含义 |
|------|------|
| `fallback_chain` | 按尝试顺序排列的所有 provider 名称 |
| `selected_provider` | 实际成功返回数据的 loader |
| `fallback_reason` | 回退原因（如 `precheck-blocked: tushare:pro_bar:qfq`） |
| `precheck_log` | 每个 provider 的 precheck 结果明细（含失败原因） |
| `sha256` | 最终价格 DataFrame 的 sha256（用于审计比对） |
| `rows` | 价格 DataFrame 行数 |

### 排障指南

- **优先检查** `data/cn_pit/metadata.json` → `data_sources.prices.fallback_reason`，它直接告诉你哪个 loader 挂了、为什么
- 如果 `selected_provider` 不是 Tushare，说明 Tushare token 缺失或 Tushare endpoint 不可达
- 如果 `selected_provider` 是 YFinance 且 `fallback_chain` 长度为 3，说明 Tushare 和 Akshare 都不可用

### 相关文档

- Plan: `docs/superpowers/plans/2026-05-30-loader-registry-engine-pr.md`
- 源码: `backtest/market_data.py` → `FallbackMarketDataProvider`

## 8. OHLV Supplemental Layer 行为

> 适用场景：需要 open/high/low/volume/amount 等 OHLCV 多列数据进行因子计算（gtja191、alpha101 等），而 H47 close-only 矩阵不包含这些字段

### 作用

OHLV supplemental data layer 是一个**补充层**，不替代 H47 close-only 矩阵。H47 close-only 矩阵仍然是 authoritative close-price 来源；OHLV 层通过 `(date, ticker)` join 提供额外的 open/high/low/volume/amount 字段，解锁需要多列输入的因子计算。

- 无 OHLV 层：gtja191 因子库中约 90% 的因子无法计算（需要多列输入）
- 有 OHLV 层：gtja191、alpha101 全量因子可计算，close-only 单因子 IC 上限从 ~0.25 突破

### 触发命令

```bash
# 全量 HS300 H47 OHLV 拉取
python3 scripts/ingest_cn_pit_ohlv.py --fetch-ohlv --start 2020-01-02 --end 2026-05-18

# 小批量验证（5 tickers, 1 month, 写入 /tmp）
python3 scripts/ingest_cn_pit_ohlv.py --fetch-ohlv --limit-tickers 5 \
    --start 2026-04-01 --end 2026-04-30 \
    --output-file /tmp/ohlv_smoke.csv
```

### 输出文件

| 字段 | 含义 |
|------|------|
| 文件路径 | `data/cn_pit/ohlv_h47_supplement.csv` |
| 格式 | long format（每行一个 `(date, ticker)` pair） |
| 列 | `date, ticker, open, high, low, volume, amount` |

### metadata.json.ohlv_layer 字段

每次 OHLV 拉取完成后，`data/cn_pit/metadata.json` 中 `ohlv_layer` 块记录完整的溯源信息：

| 字段 | 含义 |
|------|------|
| `fallback_chain` | 按尝试顺序排列的所有 provider 名称 |
| `selected_provider` | 实际成功返回数据的 loader |
| `sha256` | OHLV DataFrame 的 sha256（用于审计比对） |
| `rows` | OHLV DataFrame 行数 |
| `fallback_reason` | 回退原因（如 Tushare precheck 失败） |
| `precheck_log` | 每个 provider 的 precheck 结果明细 |
| `fetch_timestamp` | 拉取时间（ISO 8601） |
| `columns` | 输出列名列表 |

### 验证命令

```bash
# 验证 OHLV 数据完整性（sha256 比对 + row count + (date,ticker) 唯一性）
python3 scripts/ingest_cn_pit_ohlv.py --validate
```

### 排障指南

- 如果 `selected_provider` 是 YFinance，注意其 `amount` 列恒为 NaN（YFinance 不提供成交额）
- 如果 `selected_provider` 不是 Tushare，说明 Tushare token 缺失或 Tushare endpoint 不可达
- 验证失败时检查 `precheck_log` 确认哪个 loader 的 precheck 未通过

### 相关文档

- Plan: `docs/superpowers/plans/2026-05-30-ohlv-supplemental-engine-pr.md`
- 源码: `scripts/ingest_cn_pit_ohlv.py`
- Provider 实现: `backtest/market_data.py` → `get_ohlcv()` on Tushare/Akshare/YFinance providers
