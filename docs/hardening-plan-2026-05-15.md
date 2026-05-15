# 2026-05-15 Hardening Plan — 虚拟盘账本一致性防线

> 状态：DRAFT，待审后实施

## 一、Postmortem

### 根因链

```
系统停摆(5/2-5/15)
  ├─ coordinator 不执行交易/结算（只做复盘+策略迭代）
  │    └─ 无人察觉，因为 cron status 永远 "success"
  ├─ 风控死锁(5/8 起)
  │    ├─ risk_controller: 集中度超限 → REJECTED（不生成 sell action）
  │    └─ strategy_maintainer: 收紧 max_single_position（对存量无效）
  ├─ LLM 初始化失败 → self.llm = None
  │    └─ audit_subagent.call(None, ...) → TypeError → INFRA_ERROR
  │         └─ PENDING_RETRY（无重试机制，静默丢弃）
  └─ audit_layer 相对导入崩溃（5/14 Codex 合并引入）
       └─ coordinator try/except 吞掉 → status 仍 "success"

回补阶段二次污染
  ├─ 回补脚本删除已有 5/6、5/7 绩效记录
  ├─ HS300 写入 0/NaN（API 无数据时未标 reason）
  ├─ 三套账分叉（perf/trade/report 各自独立计算）
  └─ 测试写真实 repo 数据（settlement 覆盖 accounts/）
```

### 每个根因 → 自动发现机制

| 根因 | Guardrail | 实现位置 |
|------|-----------|----------|
| cron status 永远 success | workflow 必须区分 success/degraded/failed | coordinator.py (已修) |
| 风控只 REJECTED 不减仓 | risk_controller 必须生成 sell action | risk_controller.generate_risk_reduction_actions() (已修) |
| LLM=None 传入 audit | audit_layer 入口 guard + coordinator guard | audit_layer.review() + coordinator (已修) |
| 三套账分叉 | validate_ledger_consistency.py | scripts/ (新增) |
| 回补删历史数据 | regression test: 回填不得删已有日期 | test_backfill_regression.py (新增) |
| HS300 写 0/NaN | invariant: hs300_pct != 0 unless non_trading_day | validate_ledger_consistency.py |
| 测试写真实数据 | test 用 tempdir + mock settlement | test_coordinator_audit_flow.py (已修) |

## 二、数据一致性 Invariant

新增 `scripts/validate_ledger_consistency.py`，检查以下规则：

```
INV-1: 每个交易日必须有 performance_history entry
       检查: trades/ 中有文件的日期，performance_history 中必须有对应 entry
       异常: MISSING_PERF_ENTRY

INV-2: 有 trade file 的日期必须有 daily report
       检查: trades/2026-05/2026-05-08.json 存在 → reports/daily/2026-05-08.md 必须存在
       异常: MISSING_DAILY_REPORT

INV-3: 三套账数值一致
       检查: 对每个 date，
         performance_history[date].main_pct == trades[date].account_snapshots.main.daily_pnl_pct
         performance_history[date].lab_pct == trades[date].account_snapshots.lab.daily_pnl_pct
         报告中主/实验账户日收益与 performance_history 一致（容差 ±0.02%）
       异常: PNL_MISMATCH

INV-4: HS300 benchmark 不能为 0/null（交易日）
       检查: performance_history[date].hs300_pct != 0 AND != null
       排除: 标记为 non_trading_day 的日期
       异常: MISSING_BENCHMARK

INV-5: performance_history 无重复 date
       检查: len(set(dates)) == len(dates)
       异常: DUPLICATE_DATE

INV-6: performance_history 不丢历史日期
       检查: trades/ 中最老日期至今，performance_history 覆盖率 ≥ 95%
       异常: MISSING_HISTORY

INV-7: live accounts 与 performance 一致
       检查: accounts/*.json updated_at 为最新交易日时
             daily_pnl_pct 必须与当日 performance_history 一致（容差 ±0.05%）
       如果 accounts 处于 runtime 状态（非结算后），标注 IGNORED
       异常: ACCOUNT_PERF_MISMATCH
```

输出格式:
```
[PASS] INV-1: 所有交易日均有 performance_history entry
[PASS] INV-2: 所有 trade file 均有对应 daily report
[FAIL] INV-3: 2026-05-08 main_pct 分叉: perf=-0.10% trade=0.00% report=0.00%
[PASS] INV-4: HS300 benchmark 均有效
[PASS] INV-5: 无重复日期
[PASS] INV-6: 历史覆盖率 100%
[PASS] INV-7: live accounts 与 performance 一致

Result: 6/7 PASS, 1 FAIL
```

退出码: 0 = 全部 PASS, 1 = 有 FAIL

## 三、CI 集成

新增 `tests/audit_layer/test_ledger_consistency.py`:
- 调用 validate_ledger_consistency.py 的核心逻辑（import，不 subprocess）
- 每个 INV 一个 test case
- unittest discover 自动跑

约束: 这个测试读真实 repo 数据（它是数据校验，不是功能测试），
但绝不写任何文件。只读模式。

## 四、回补流程 Runbook

新增 `docs/data-backfill-runbook.md`:

### 4.1 数据来源优先级
1. 交易记录: 优先从 cron workflow output / coordinator save_workflow_result 恢复
2. 行情数据: yfinance（历史）> 腾讯 API（当日）> 新浪 API（备选）
3. 基准数据: yfinance 000300.SS，缺失时标注 reason

### 4.2 交易状态标注
回补产生的交易必须在 trade file 中标注 `execution_type`:
- `"executed"`: cron 当日实际执行（正常路径）
- `"backfill"`: 回补时从策略信号回放生成（模拟执行）
- `"simulated"`: 假设性推演（不做实际影响）

在 trades[].execution_type 字段中记录。

### 4.3 无交易日处理
- trade file: `"trades": []`, `"is_trading_day": true`
- daily report: "今日操作：无交易"
- performance_history: 仍然必须有 entry（mark-to-market）
- 不能跳过

### 4.4 Benchmark 缺失处理
- yfinance 无数据: 从腾讯 API 补，或标注 `"hs300_pct_source": "estimated"`
- 如果完全无法获取: `"hs300_pct": null, "hs300_pct_reason": "data_unavailable"`
- 绝不写 0 占位

### 4.5 回补后必须执行
```bash
# 1. 数据一致性校验
python3 scripts/validate_ledger_consistency.py

# 2. 测试
python3 -m unittest discover tests/audit_layer

# 3. 对账表（人工确认）
python3 scripts/generate_reconciliation_table.py 2026-05-06 2026-05-15

# 4. 精确提交（不用 git add .）
git add scripts/validate_ledger_consistency.py ...
git commit -m "[fix] ..."
```

## 五、防覆盖 Regression Tests

新增 `tests/audit_layer/test_backfill_regression.py`:

```python
class TestBackfillRegression:
    def test_backfill_preserves_existing_dates(self):
        """回填 5/8-5/15 不得删除 5/6、5/7 的 performance entry"""

    def test_hs300_never_zero_on_trading_day(self):
        """交易日 hs300_pct 不能为 0"""

    def test_report_matches_performance(self):
        """日报日收益必须与 performance_history 一致"""

    def test_settlement_does_not_write_real_repo(self):
        """run_settlement 测试不得写真实 repo 数据"""
```

## 六、Learned Lessons

写入 strategies/active.json learned_lessons:

1. "任何历史数据修复必须先定义 ledger invariants，修复后跑校验"
2. "测试不得使用真实 VTRADER_HOME，必须 tempdir + mock"
3. "workflow status 不能永远 success，必须区分 success/degraded/failed"
4. "回补交易必须标注 execution_type: executed/backfill/simulated"

## 实施顺序

1. 写 postmortem（本文档 → docs/postmortem-2026-05-15.md）
2. 写 validate_ledger_consistency.py + 跑一次确认当前数据 PASS
3. 写 test_ledger_consistency.py + test_backfill_regression.py
4. 写 docs/data-backfill-runbook.md
5. 写 scripts/generate_reconciliation_table.py
6. 更新 learned_lessons
7. 全量测试 + 校验 + 提交

## 验收清单

- [ ] python3 -m unittest discover tests/audit_layer PASS
- [ ] python3 scripts/validate_ledger_consistency.py PASS (exit 0)
- [ ] git diff --check PASS
- [ ] 5/6-5/15 对账表输出，四套账全部一致
- [ ] 所有新增文件只用精确路径 git add
