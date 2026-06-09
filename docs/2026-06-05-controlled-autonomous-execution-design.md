# 受控自主执行（Controlled Autonomous Execution）设计 v2

Date: 2026-06-05
Author: claude-code (mac)，参考开源思路综合
Status: COMPLETE (Phase 0–4 实现并合并，2026-06-05；默认 mode=shadow，canary/live 已实现+测试但未激活)。测试：870 passed / 5 skipped。
Repo: `~/.hermes/virtual-trader`
Review: 见本文件 §13「评审与修订」

## 1. 背景与问题

虚拟盘自 2026-05-26 起零成交。git 定位：自主下单（`source=live_cron`，末次 2026-05-07）本是 LLM agent 实时行为、无受控代码；`10a396a`（2026-05-18）主动把执行收口成"仅确定性 reduce-only 卖出"，从此结构上无自主买卖。盘前 `execution_planner` 每天产 APPROVED 计划但无人消费。本设计补上缺失的 Execution 段，且不重开 `10a396a/eee8151` 焊死的 bypass。

## 2. 评审纠正的根本前提（必读）

原 v1 假设了**系统并不产出的输入**。经 subagent + Hermes 复核并由作者读码确认：

- **planner 不产 target_weights**：`execution_planner.generate_trading_plan` 只返回命令式 `actions` + 聚合 `total_position`，`sector_allocation` 为空 `{}`（`execution_planner.py:136-220`）。
- **盘前 workflow JSON 不含计划**：`run_pre_market_workflow` 从不把 `trading_plan` 放进 `workflow_result`（`coordinator.py:637-711`），落盘只有 `steps[].output` 文本（实测 `workflow_pre_market_20260605_*.json` 无 `trading_plan`/`target_weights`）。
- **每日计划的 APPROVED 未签名/未落盘**：`risk_controller.validate_trading_plan` 返回临时 dict（`risk_controller.py:134-194`）；审计签名（`commit_approved`→`audit_log.json` `AUTO_MERGE`，`strategy_maintainer.py:401-425`）只签**策略提案**，套不到每日计划。

**结论**：必须先建数据契约（Phase 0），end-to-end 在 shadow 验证后，再加闸门。否则空输入会通过所有闸门 → 假"executed 0 单"绿。

## 3. 目标 / 非目标

**目标**：补 LEAN 式缺失的 Execution 段消费 APPROVED 计划；自主单过既有 ledger 不变量 + 新增逐层闸门，任一不过则否决留痕；main 与 lab 均全自主（参数松紧不同）；起步 shadow，可升 canary→live，带 kill-switch；修掉"假绿"。

**非目标（YAGNI）**：不接真券商/真钱；不做人工确认闸（用户选全自主）；不改因子线；不改 market_analyst/risk_controller 的分析逻辑，只补"计划→成交"。

## 4. 参考开源范式（各取一层）

| 来源 | 借鉴 | 落点 |
|---|---|---|
| QuantConnect LEAN | `Alpha→PortfolioConstruction→RiskManagement→Execution` 分段 | 新增 Execution 段 |
| freqtrade | `dry_run`；逐单否决；protections（Cooldown/MaxDrawdown/StoplossGuard）；kill-switch | mode 开关 + 逐单闸门 + 熔断 |
| vnpy RiskManager | 下单前硬校验：每日单数/单笔/价格偏离/白名单 | pre-trade 防呆 |
| Microsoft Qlib（因子线在用）| Strategy 出目标仓位 / Executor 执行 分离 | planner 出目标权重、executor 算差额 |
| 本仓 h35 影子盘 | paper/`auto_live_orders` + 回撤/亏损/换手熔断的**阈值数学** | G4 复用其阈值函数（非直接调用，见 M1）|

## 5. 架构（修订）

```
盘前 workflow (run_pre_market_workflow)
  market_analyst → execution_planner → risk_controller(APPROVED)
        │  [Phase 0] planner.compute_target_weights(account) 产 {code: weight}
        └─► [Phase 0] 把完整 trading_plan(含 target_weights + decision) 持久化进
            pre_market workflow JSON 的 plan 字段

盘后 workflow (run_post_market_workflow)：settlement 后、strategy_maintainer 前
  （插入点 coordinator.py:1015 之后、1026 之前；try/except 包裹，绝不崩 workflow）
   ┌──────────────────────────── ExecutionModel ────────────────────────────┐
   │ G0 计划契约校验: 读 pre_market workflow JSON 的 plan；校验 schema/当日新鲜  │
   │                 度/decision==APPROVED（结构性校验，**非签名复用**，见 B3）  │
   │ G1 目标→差额  : 现持仓(accounts/*.json) vs target_weights → 候选订单；      │
   │                 diff 本身幂等(重跑→空差额)，这是唯一幂等来源(去掉 pending)   │
   │ G2 RiskMgmt   : 按 active.json 夹逼——单票≤max_single_position、行业≤        │
   │                 max_sector_exposure、总仓≤total_position_limit、现金下限    │
   │ G3 pre-trade  : config/execution.json 阈值——每日≤max_orders、单笔≥min、     │
   │                 |价-昨收|≤偏离、白名单                                       │
   │ G4 protections: 复用 h35 阈值数学(回撤>-12%停新买/月亏>-8%/连亏卖≥5/换手),    │
   │                 喂【实时账户权益序列+trades 已实现盈亏】而非 backtest 对象    │
   │ G5 soft-brake : 单账户单日净卖出>15% NAV → 该账户当日降级 alert-only(Q3)     │
   │ G6 kill-switch: 存在 EXECUTION_HALT 文件 / mode∉{canary,live} → 全否决       │
   │ G7 执行+记账  : shadow→写 value_account 影子账本，不碰 accounts/*.json；      │
   │                 canary/live→原子写 accounts+trades，**随即重跑 settlement     │
   │                 绩效 upsert + _ensure_daily_report**(修 H2)，再                │
   │                 _run_ledger_validation(strict=True 才覆盖 INV-1..7)，          │
   │                 失败回滚该账户 + 标 degraded(复用 10a396a 机制)               │
   └────────────────────────────────────────────────────────────────────────┘
  每单(通过/夹逼/否决+原因+gate) → workflow JSON.execution_decisions + 企业微信
```

确定性 reduce-only（`_process_risk_actions`）**不动**，是地板，也是唯一**无条件**自动写盘的路径；G0–G7 是执行前安全层，**不替代**任何审批（修 H3/B3）。

## 6. 组件与接口

新增 `agents/execution_model.py`，职责 = "把 APPROVED 目标权重在闸门内变成账本变更"。

```python
class ExecutionModel:
    def __init__(self, data_dir, exec_config, mode): ...        # mode: shadow|canary|live|halt
    def execute_plan(self, account, plan, prices, account_state) -> ExecutionReport
    # 纯函数（可单测、不碰盘）：
    def diff_to_orders(self, positions, target_weights, prices, account) -> list[Order]   # G1
    def apply_risk_limits(self, orders, account_state, rules) -> list[Order]              # G2
    def pretrade_check(self, order, day_state, cfg) -> GateResult                         # G3
    def protections(self, order, equity_series, realized_pnl, cfg) -> GateResult          # G4
    def soft_brake(self, orders, account_state, cfg) -> list[Order]                       # G5
```

`ExecutionReport = {account, mode, candidates, decisions:[{order,verdict,gate,reason}], executed, ledger_validation}`。
execute_plan 永不抛未捕获异常进 workflow（内部 try/except→降级 alert）。

## 7. 数据流与落盘

- 输入：pre_market workflow JSON 的 `plan.target_weights`（Phase 0 产出）；`accounts/<acct>.json`（settlement 已更新 `current_price`，`coordinator.py:1185-1191`）；注意 settlement 返回值不含 per-symbol 收盘价，executor 从 `accounts/*.json` 读 `current_price`（L2）。
- 配置：新建 `config/execution.json`（`config/` 目录当前不存在，本设计新建）：`{mode, per_account:{main:{...},lab:{...}}, gates:{max_orders,min_order,price_deviation,cooldown_days,whitelist,turnover_warn,turnover_block,drawdown_stop:-0.12,monthly_loss_stop:-0.08,consec_losing_sells:5,soft_brake_net_sell:0.15}, canary:{per_order:0.02,max_buys:1,max_sells:1,daily_turnover:0.04}, kill_switch_path:"EXECUTION_HALT"}`。
- 输出：shadow→`value_account/logs/exec_shadow_<acct>.jsonl` + `value_account/reports/exec_shadow_state.json`；canary/live→`accounts/<acct>.json`、`trades/<月>/<日>.json`、`reports/daily/<日>.md`（复用现有写入器）。决策→workflow JSON `execution_decisions` + 企业微信。

## 8. 模式与灰度（起步 shadow）

| mode | 行为 | 升档条件 |
|---|---|---|
| `shadow`（默认）| 算单+过全闸+写影子账本，**不碰真实账户** | 连续 ≥5 交易日影子无异常、INV 全 PASS |
| `canary` | 真实成交，G3 夹到极小（单笔≤2% NAV、每账户每日≤1买+1卖、日换手≤4%）| 连续 ≥5 日 canary 无 INV 失败/无 protections 误触 |
| `live` | 闸门内全量真实成交 | — |
| `halt` | kill-switch，全否决，回退仅 reduce-only | — |

## 9. 错误处理

- 闸门否决：跳过该单、记 `verdict/gate/reason`，不影响其他单。
- canary/live 落盘后 `_run_ledger_validation` 失败：回滚该账户本次写入、标 `degraded_reason`、告警（复用 10a396a `final_ledger_validation`）。
- execute_plan 内部异常：捕获→降级"本日不执行+告警"，绝不崩 workflow 或假成功。

## 10. 测试（沿用 `virtual-trader-audit-testing` 纪律）

- 纯函数单测：diff_to_orders（**幂等性**：同输入重跑空差额）、apply_risk_limits（夹逼上限）、pretrade/protections/soft_brake 各阈值边界。
- 否决用例：超仓/超行业/价格偏离/冷却/回撤熔断/软刹车/kill-switch 各一条，断言"否决且不写盘"。
- shadow 不污染：断言 shadow 下 `accounts/*.json` mtime 不变。
- canary/live 记账完整性：执行后 INV-1..7 全 PASS（`strict=True`），含绩效 upsert + 日报。
- 真实签名校验（autospec）：执行链对 LLMClient/审计层 autospec 防漂移。

## 11. 复用清单与新建物

复用：h35 阈值数学（`check_stop_conditions`/`compute_*_turnover`/`worst_monthly_return`，`h35_shadow_account_executor.py:82-241`——**作纯阈值函数**，喂实时权益序列，见 M1）；`active.json` 风控参数（G2）；`validate_ledger_consistency.py`（INV-1..7，`strict=True` 才含 INV-7）；`_atomic_write_json`/`_run_ledger_validation`/`_ensure_daily_report_for_date`（`coordinator.py:45/57/73`）；value_account 目录习惯。
新建（不假设已存在）：`config/execution.json`、`EXECUTION_HALT` kill-switch、G3/G4 阈值键。

## 12. 构建顺序（评审重排：先契约，后闸门）

- **Phase 0（前置·数据契约）**：① `execution_planner.compute_target_weights(account)`；② `run_pre_market_workflow` 把完整 `trading_plan`（含 target_weights）持久化进 workflow JSON。纯加法、不改执行行为。TDD。
- **Phase 1（shadow 骨架·无闸门）**：`ExecutionModel.diff_to_orders` + `_commit(shadow)` 写影子账本；接入 post_market（settlement 后）；end-to-end 跑通 shadow，断言不碰真实账户。
- **Phase 2（闸门链 G0–G6）**：契约校验/风控夹逼/pre-trade/h35 protections/软刹车/kill-switch。
- **Phase 3（canary/live + 记账）**：G7 真实写 + 绩效 upsert + 日报重生成（修 H2）+ 模式升档。
- **Phase 4（可观测）**：`execution_decisions` 进 workflow JSON + 企业微信摘要（修假绿）。

## 13. 评审与修订（v1→v2）

REQUEST_CHANGES（subagent + Hermes，全部经作者读码确认）→ 已处理：
- **B1/B2（BLOCKER）**：target_weights 与持久化计划均不存在 → 新增 Phase 0 数据契约，作为一切前置。
- **B3（BLOCKER）**：审计签名只签策略提案 → G0 改为对**新持久化计划**的结构/新鲜度/decision 校验，不声称签名复用。
- **H1（HIGH）**：`pending.json` 去重既冗余又危险（会被 `_process_pending_risk_actions` 自动执行）→ **去掉 pending 复用**，幂等只靠 diff。
- **H2（HIGH）**：execution 在 settlement 之后写会破 INV-1/2/3/6 → G7 写后**重跑绩效 upsert + 日报**再校验。
- **H3（HIGH）**：shadow→live 切换未定义 → §5/§8 明确 G0–G7 是执行前安全层、非审批替代，reduce-only 仍是唯一无条件自动写。
- **M1**：h35 函数吃 backtest 结果 → 只复用阈值数学、喂实时权益序列。
- **M2/M3**：G3/G4 阈值键与 `EXECUTION_HALT` 不在现有 config → 归入新建 `config/execution.json`。
- **L1/L2**：`_run_ledger_validation(strict=False)` 实为 INV-1..6（INV-7 需 strict=True）；插入点 1015↔1026；收盘价从 `accounts/*.json` 读。

## 14. 验收标准

- shadow 连续 5 交易日：每日产 `execution_decisions`、影子账本更新、`accounts/*.json` 不变、INV-1..7 全 PASS、企业微信显示"计划N→过M→(影子)成交K + 否决原因"。
- kill-switch 在则当日 0 自主成交、回退 reduce-only。
- 全部新单测通过 + 既有回归不破。
