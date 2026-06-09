# 美股策略实现报告 — us_trader-impl-report-2026-06-09

实现者: mac/claude-code(subagent)
日期: 2026-06-09
分支: feat/us-trader-momentum

---

## Task 完成一览

| Task | 描述 | Commit Hash | 状态 |
|------|------|-------------|------|
| Task 0 | 模块脚手架 + 配置加载 | `06f50d0` | ✅ PASS |
| Task 1 | 价格/日历数据(tushare) | `dd23e60` | ✅ PASS |
| Task 2 | 市值/成长基本面(yahooquery) | `7676b77` | ✅ PASS |
| Task 3 | 票池种子 + 加载 | `70983c2` | ✅ PASS |
| Task 4 | 选股管线 | `c27d345` | ✅ PASS |
| Task 5 | 风控止损/移动止盈/熔断 | `e31733a` | ✅ PASS |
| Task 6 | 本地模拟撮合记账 | `e0b091b` | ✅ PASS |
| Task 7 | 微信日报组装+发送(校验返回码) | `fcf6665` | ✅ PASS |
| Task 8 | 每日编排+health+失败告警 | `0d04ddc` | ✅ PASS |
| Task 9 | cron run.sh 入口脚本 | `eb69625` | ✅ PASS (jobs.json 待手动添加,见下) |

---

## 最终 pytest 输出

```
cd ~/.hermes/virtual-trader && /usr/bin/python3 -m pytest us_trader/tests/ -q
.....................                                                    [100%]
21 passed in 2.18s
```

全部 21 个测试通过。

---

## 各 Task 详情

### Task 0: 模块脚手架 + 配置加载

**测试结果**: 2 tests PASS
- `test_defaults_present`: 验证默认配置字段值正确
- `test_override`: 验证深合并覆盖逻辑

**关键文件**:
- `us_trader/__init__.py`
- `us_trader/config.json` (含完整默认参数)
- `us_trader/config.py` (`load_config(path=None)` 深合并实现)
- `us_trader/.gitignore` (忽略 data/cache/, state/, reports/, __pycache__/)
- `us_trader/pipeline/__init__.py`
- `us_trader/tests/__init__.py`

---

### Task 1: 价格/日历数据(tushare)

**测试结果**: 2 tests PASS
- `test_trading_days_filters_open`: 验证闭市日被过滤
- `test_price_panel_shape`: 验证价格面板形状与数值

**关键文件**: `us_trader/pipeline/fetch_prices.py`

**token 读取**: 与 `scripts/h49a_build_tushare_sw_industry.py` 相同逻辑:
1. `$TUSHARE_TOKEN` 环境变量
2. `scripts/.tushare_token` 文件
3. `~/.tushare.token`
4. `tushare.get_token()`

---

### Task 2: 市值/成长基本面(yahooquery)

**测试结果**: 2 tests PASS
- `test_symbol_mapping`: 验证 `AAPL.O` → `AAPL`,`BRK.A` → `BRK.A`(保留非交易所后缀)
- `test_fetch_handles_missing`: 验证缺失 ticker 返回 None 不抛异常

**重要决策**: 函数 `fetch_fundamentals` 的结果 dict **以 yahoo symbol 为 key**(而非 ts_code),与测试 mock 对齐。调用方(select.py)内部用 `to_yahoo_symbol()` 做双 key 查找。

**交易所后缀白名单**: `{O, N, K, P}`(如 `.O` for NASDAQ, `.N` for NYSE)。

---

### Task 3: 票池种子 + 加载

**测试结果**: 3 tests PASS
- `test_load_universe_min_rows`: 行数 > 50
- `test_load_universe_fields`: 字段完整性
- `test_load_universe_no_duplicates`: 无重复 ts_code

**票池实际行数: 262 行**（种子待扩充，目标 300-500 行）

注: 种子为中小盘成长候选股,手工整理自常见小盘成长名单。不包含市值等级过滤(由 select.py 的 mcap 过滤完成)。

---

### Task 4: 选股管线

**测试结果**: 2 tests PASS
- `test_select_ranks_and_filters`: A 排第一,C 因市值超上限被剔除
- `test_missing_growth_excluded`: 成长数据缺失时全部不通过

**已知取舍(照计划)**: 流动性 ADV 过滤降级为仅 `price_min`(tushare 美股无量数据),量过滤留待升级。

---

### Task 5: 风控止损/移动止盈/熔断

**测试结果**: 4 tests PASS
- `test_stop_loss`: -11% 触发止损
- `test_trailing_tp`: 已 arm 且自高点回落 >10% 触发移动止盈
- `test_tp_not_armed`: 仅 +10% 未 arm,不触发止盈
- `test_portfolio_halt`: 组合回撤 -20.8% 触发熔断,+10% 不触发

---

### Task 6: 本地模拟撮合记账

**测试结果**: 2 tests PASS
- `test_buy_then_stop_loss_sell`: 买入后止损卖出,当日触发止损的不重新买入
- `test_nav_recorded`: NAV 精确到 1e-6

**关键修复**: 止损/止盈当日退出的票,不在同日的买入候选里重新买入(增加 `exited_today` 集合过滤)。

---

### Task 7: 微信日报组装+发送

**测试结果**: 2 tests PASS
- `test_digest_has_sections`: 验证五段标题关键词(复盘/持仓/变动/选股/风险)
- `test_send_checks_returncode`: returncode!=0 时返回 False(不吞错误)

**通知目标**: 固定 `config["weixin_target"]`(默认 `"weixin"`,非 wecom)。

---

### Task 8: 每日编排+health+失败告警

**测试结果**: 2 tests PASS
- `test_happy_path_writes_health`: 成功路径写 health.json `success=true`
- `test_fetch_failure_alerts`: fetch 失败写 health `failed_step=fetch_prices`,发 ❌ 告警

**编排流程**:
1. `is_trading_day` → 非交易日返回 `{skipped: True}`
2. `fetch_price_panel` → 价格面板
3. `fetch_fundamentals` → 基本面快照
4. `select` → 选股管线
5. `_load_state` → 加载账本
6. `position_exit_signals` + `update_watermark` → 风控信号
7. `step` → 模拟撮合
8. `_save_state` → 持久化
9. `build_digest + send_weixin` → 微信日报
10. `write_health` + recovered 告警

---

### Task 9: cron 接入

**run.sh**: `us_trader/run.sh`(已 `chmod +x`)
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
exec /usr/bin/python3 -m us_trader.pipeline.run_daily
```

**jobs.json 待手动添加**(自动写入被 auto-mode 安全策略拦截,需用户手动添加或由 Hermes 执行):

```json
{
  "name": "美股-每日选股复盘",
  "prompt": "执行以下命令运行美股高动量中小盘成长策略每日批跑:\n\n```bash\nbash ~/.hermes/virtual-trader/us_trader/run.sh 2>&1\n```\n\n等待命令执行完毕,读取输出结果。\n\n重要:\n- run_daily 内部已用 us_tradecal 判断交易日,非交易日自动 skip\n- 失败时由 run_daily 发微信告警\n- 最终回复只包含命令的输出",
  "schedule": { "kind": "cron", "expr": "0 22 * * 0-4" },
  "deliver": "weixin",
  "enabled": true
}
```

**调度说明**: `cron 0 22 * * 0-4` = UTC 22:00 = 北京次日 06:00,美股工作日;`run_daily` 内部再用 `us_tradecal` 兜底判交易日,非交易日自动 skip。

---

## 票池种子信息

- 实际行数: **262 行**（< 300,标注待扩充）
- 文件: `us_trader/data/universe.csv`
- 内容: 中小盘成长候选股,包含科技/生物/消费/金融科技等板块
- 扩充建议: 可后续用 `refresh_universe_from_tushare()` 从 tushare us_basic 自动刷新

---

## git status 验证

```
On branch feat/us-trader-momentum
Changes not staged for commit:
  modified:   docs/repo-hygiene-report-2026-06-06.md   ← 原有脏文件,未动
  modified:   strategies/performance_history.json       ← 原有脏文件,未动

Untracked files:
  trades/2026-06/                                       ← 原有脏文件,未动
  value_account/h34_shadow_account_config.json.bak      ← 原有脏文件,未动
```

确认:A 股 `agents/coordinator.py`、`accounts/`、`trades/`(原有)、`strategies/` 均**未被修改**。

---

## 与计划不符之处及取舍

1. **fetch_fundamentals 结果 key**: 计划未明确 key 类型,但测试 mock 期望 yahoo symbol 为 key。实现采用 yahoo symbol 为 key,select.py 内用双 key 查找(ts_code 或 yahoo_symbol),保持一致。

2. **jobs.json 自动写入被拦截**: auto-mode 安全策略拒绝写入 `~/.hermes/cron/jobs.json`(视为 scope 升级)。run.sh 已提交,cron job spec 已文档化在本报告中,需用户或 Hermes 手动添加。

3. **Task 1 commit message**: 计划建议 `test(us_trader): 价格/日历 + 实现`,实际实现同步写了测试和代码,commit message 保持了计划的格式。

4. **simulate.py exited_today 逻辑**: 计划未显式说明止损当日不重新买入,但语义上合理(否则止损后立即又买入)。已添加 `exited_today` 集合,并在报告中记录。

---

## Task 10 验收(待 claude-code 独立复核)

Task 10 由验收方执行,需:
1. 历史区间干跑(mock 发送)
2. 故障注入:让 `fetch_price_panel` 抛错 → 验证 health.json + 微信告警
3. 真实当日全流程 → 验证个人微信(weixin 非 wecom)收到完整日报
4. 确认 A 股文件未被触碰
5. 全量 pytest 全绿(已预验证:21 passed)

---

## claude-code 验收记录(2026-06-09)

### 代码审查(两段式:spec 合规 → 代码质量)
- spec 合规:10 个 Task commit 齐全、仅改 us_trader/、无 A 股文件改动、weixin 非 wecom、.gitignore 就位、外部源单测全 mock。✓
- 发现并修复(commit a3d9c09):
  - **HIGH**:`run_daily._load_state` 用 `nav - Σ(shares*cost)` 反推 cash → 被未实现盈亏虚增、跨日超买。改为 `account.json` 显式持久化 cash + 加回归测试 `test_state_roundtrip_preserves_cash`。
  - **MINOR**:`select.py` 的 `to_yahoo_symbol` import 由循环内提到模块顶部。
  - **测试 hermeticity**:`test_run_daily` 两用例漏 mock `get_trading_days`→偷打 tushare(曾 62s 超时),补 mock。
- 最终单测:`22 passed in 0.39s`(hermetic)。

### Task 10 端到端验收(真实数据,小票池 8 只:AEHR/ALRM/AMKR/APPN/ASTS/BILL/BRZE/CELH)
- **Happy path ✓**:`run_daily("20260605")` success=True。选出 **CELH** 买 444 股 @28.13=$12,489.72(≈1/8 等权);account.json cash=87508.06(=100000−12489.72−2.22 手续费,**cash 修复实测生效**);nav=99997.78 账目自洽;positions/nav/trades/health 全部正确落盘。其余 7 票被市值/成长过滤(ALRM 单票 tushare 超时→NaN graceful)。
- **Fault path ✓**:tushare 瞬时超时那次,`health.json` 正确写 success=false/failed_step=fetch_prices/error,并触发告警发送;`send_weixin` 返回码校验生效(捕获 rate-limited,未静默吞)。
- **微信投递 ⚠️ 未确认**:个人微信(iLink)通道当前**限流**(`iLink sendmessage rate limited`,gateway 自动重试 4×backoff 仍失败)。代码链路正确,大概率是今日多次测试累积触发;正常每天 1 条日报极少撞限流。**待限流冷却后补一次单发确认投递到手机。**

### 运维注意(供后续)
1. **tushare 美股慢**:≈8s/票,262 票全量约 35min + 偶发单票超时(已 graceful)。日跑安排在收盘后夜间批处理可接受;若要提速考虑并发/缓存/缩票池。
2. **观测性小缺口**:`run_daily` 把"日报发送失败/限流"当非致命(只 logger.warning,health 仍 success)→ 日报若被限流是静默不达。告警(❌)走同一 weixin 通道,同样受限流影响。建议后续:digest 发送失败也写入 health 或单独计数,避免"以为发了其实没到"。
3. 票池种子 262 行(<300,待扩充)。
4. cron job 因 auto-mode 拦截未自动写入 `~/.hermes/cron/jobs.json`,`run.sh` 已就绪,job spec(`cron 0 22 * * 0-4`,deliver weixin)待手动/Hermes 添加。

### 隔离确认
本次实现与验收仅在 `us_trader/` + 分支 `feat/us-trader-momentum`;A 股 coordinator/accounts/trades/strategies **一行未动**(验收用临时 `/tmp/us_accept_state`,未污染真实 state/)。
