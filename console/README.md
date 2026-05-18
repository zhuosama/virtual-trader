# Hermes Local Console

本机管理台 Gateway，只监听 127.0.0.1:8765。

## Quick start

```bash
cd ~/.hermes/virtual-trader
python3 console/server.py
```

## 实施记录

### T1: Gateway 最小骨架 (2026-05-12)

- ThreadingHTTPServer 绑定 127.0.0.1:8765
- GET /health → JSON 状态
- GET /console/virtual-trader → 首页 UI shell
- signal handler 用 KeyboardInterrupt 避免死锁
- normalize_version 兼容 main_strategy/lab_strategy 字段

### T2: Read-only 数据与策略视图 (2026-05-12)

- GET /api/virtual-trader/accounts → 账户列表 (非 demo)
- GET /api/virtual-trader/accounts/:id → 单账户详情
- GET /api/virtual-trader/strategies → 策略列表
- GET /api/virtual-trader/strategies/:id → 单策略详情
- GET /api/virtual-trader/workflows → workflow 元数据
- GET /console/virtual-trader/data → Data Manager UI
- GET /console/virtual-trader/strategies → Strategy Manager UI
- 敏感字段 (api_key/secret/token/password/broker) 自动 mask
- workflow final_output 不返回

### T3A: Backtest Center 最小闭环 (2026-05-12)

- POST /api/virtual-trader/backtests → 创建并运行回测
- GET /api/virtual-trader/backtests → run 列表 (摘要)
- GET /api/virtual-trader/backtests/:runId → 完整结果
- GET /console/virtual-trader/backtests → Backtest Center UI
- backtest_adapter.py 桥接 backtest_engine.py
- 结果写入 backtest/runs/<run-id>.json
- 只读调用引擎，不修改 accounts/strategies

### T3A.1: Hardening (2026-05-12)

- endDate 边界: dailySeries/reportText/tradeSummary 不超过 endDate
- 隐私标记: list API 不返回 reportText, detail API 返回 privateOnly=true
- winRate 一致性: tradeCount=0 时 winRatePct=null
- UI: reportText 区域标注 "Local private detail — never exported publicly"

### T3B: Backtest Result Viewer (2026-05-12)

- GET /console/virtual-trader/backtests/:runId → 详情页
- 4 个原生 SVG 图表:
  - Cumulative Return: 策略 vs benchmark 累计收益曲线
  - Daily Return Bars: 每日收益柱状图 (绿正红负)
  - Drawdown: 回撤面积图 + maxDrawdownPct 标注
  - Position Count: 持仓数量随时间变化
- 10 个指标卡 (Status/Strategy/Period/Return/Benchmark/Excess/MaxDD/WinRate/Trades/Sharpe)
- Trade Summary 表格 (private-only 标注)
- hover tooltip 显示日期和数值
- failed/empty run 有合理状态提示
- View Result 链接从列表页跳转详情页

### T4: Health + Public Export Hardening (2026-05-16)

- `GET /health` 返回业务健康：latest workflow、ledger validation、
  pending risk actions、pending audit retries
- ledger validation 带 30 秒 TTL cache，并用 lock 防止并发 health probe
  打爆 validator
- health 响应不暴露 validator 原始失败文本或绝对路径
- 404 响应不回显 query string
- POST body 超过 1MiB 返回 413 并关闭连接；负数 Content-Length 返回 400
- public site import 在 copy 或 post-scan 失败时自动 rollback
- `get_summary()` 不返回本机 `VTRADER_HOME` 绝对路径

### T5: Truthful Health + Write Guards (2026-05-18)

- 首页使用业务健康状态，不再把 HTTP 在线误报成交易系统健康
- 首页展示 pending reduce-only 风控动作：账户、代码、卖出股数、过期时间
- `GET /api/virtual-trader/risk-actions` 返回脱敏 action queue
- workflow 最新记录按文件修改时间排序，兼容 `timestamp` 字段
- inline JSON 注入会转义 `<`, `>`, `&`, U+2028, U+2029
- 所有 POST 写入口要求 `X-Hermes-Console-Nonce`
- public export 写入前必须通过 strict ledger gate

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Gateway + business health check |
| GET | `/console/virtual-trader` | 首页 |
| GET | `/console/virtual-trader/data` | Data Manager |
| GET | `/console/virtual-trader/strategies` | Strategy Manager |
| GET | `/console/virtual-trader/backtests` | Backtest Center |
| GET | `/api/virtual-trader/summary` | 系统摘要 |
| GET | `/api/virtual-trader/accounts` | 账户列表 |
| GET | `/api/virtual-trader/accounts/:id` | 账户详情 |
| GET | `/api/virtual-trader/strategies` | 策略列表 |
| GET | `/api/virtual-trader/strategies/:id` | 策略详情 |
| GET | `/api/virtual-trader/workflows` | workflow 列表 |
| GET | `/api/virtual-trader/risk-actions` | 待执行风控动作 |
| GET | `/api/virtual-trader/backtests` | 回测 run 列表 |
| POST | `/api/virtual-trader/backtests` | 创建回测 |
| GET | `/api/virtual-trader/backtests/:runId` | 回测结果 |
| POST | `/api/virtual-trader/export/public-snapshot` | 写入本地 public-export 快照 |
| POST | `/api/virtual-trader/export/import-to-site` | 导入本地网站数据目录 |

## Security

- 只绑定 127.0.0.1，不绑定 0.0.0.0
- 敏感字段自动 mask (api_key/secret/token/password/broker)
- workflow final_output 不返回
- 回测结果 reportText 标记为 local-private
- 只读调用引擎，不修改 accounts/strategies
- `/health` 和 summary API 不返回本机绝对路径
- POST body 有 1MiB 上限，异常长度会关闭连接
- public export import 失败会自动回滚已写入站点文件
- POST 写入口要求页面注入的 per-process nonce；无 nonce 返回 403
- public export 写入前执行 `validate_ledger_consistency.py --strict`

## Files

```
console/
├── server.py              # HTTP server (Phase 1+2+3A+3B)
├── backtest_adapter.py    # 回测引擎 adapter
├── export_adapter.py      # public-export 快照生成与 ledger gate
├── site_bridge_adapter.py # 本地网站导入 adapter
├── templates/
│   ├── index.html         # 首页
│   ├── data.html          # Data Manager
│   ├── strategies.html    # Strategy Manager
│   ├── backtests.html     # Backtest Center (list + create)
│   ├── export.html        # Public Export UI
│   └── backtest-detail.html # Backtest Result Viewer (4 SVG charts)
├── static/                # (reserved)
└── README.md              # This file
```

## Phase roadmap

- T1: Gateway 骨架 ✅
- T2: 数据与策略视图 ✅
- T3A: Backtest Center 闭环 ✅
- T3A.1: Hardening ✅
- T3B: Backtest Result Viewer 图表与交互 ✅
- T4: Public Export + health hardening ✅
- T5: Truthful Health + write guards ✅
