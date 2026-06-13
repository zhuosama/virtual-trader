# 现金拖累后续：F2 / F3 / F5 实施判断

> 背景：2026-06-10 现金拖累问题有三层根因（见 commit `c2ec0da` / `e73eadb`）。
> F1（买入动作自带 stop_loss，解开 G0/MODIFY 死锁）已完成并合入 main。
> 本文给出 F5 草案 + F2/F3 实施判断。F4（`detect_iteration_stall`）已验证工作正常：
> 它只在「10 日 NO_CHANGES **且** 跑输 HS300」时告警；近 10 日 main 累计 +0.11%
> 跑赢下跌的 HS300（−3.43%），故按设计未触发——它监控的是**策略自迭代停滞**，
> 不是**资金部署停滞**，后者正是 F5 要补的盲区。

---

## 推荐落地顺序

1. **F1（已完成）** — 移除死锁成因。
2. **观察 2–3 个 canary 交易日** — 确认看涨日现在确实 `APPROVED` 且 ≥1 笔买入落地。
3. **F5（可立即做，独立）** — 资金部署停滞告警，廉价安全网。
4. **F2a** — 真实趋势/流动性入场过滤（在放宽 canary 上限之前）。
5. **F3** — 适度放宽 canary 上限，再按书面标准毕业到 `live`。
6. **F2b（later）** — 叠加基本面闸门（ROE/股息/负债率）。

> 关键依赖：**F2a 必须先于 F3**。否则放宽上限只会让资金更快地灌进**未过滤**的
> 候选——错误的顺序。

---

## F5：资金部署停滞告警（草案，可立即实施）

### 盲区
现有告警没有任何一条监控「仓位长期远低于目标/下限」。死锁期间（6/8→6/12，恰好
5 个交易日）主账户卡在 ~16–17%，目标下限 50%，却**无人被告警**——只能靠人工排查
发现。F4 因为「跑赢基准」而正确地保持沉默，但部署停滞本身无人看守。

### 设计（完全对标 F4 的纯函数 + 盘后调用模式）

**1. 探测器**（新增到 `agents/strategy_maintainer.py`，紧挨 `detect_iteration_stall`）：
```python
def detect_deployment_stall(position_pct_history, floor,
                            min_stall_days=5, margin_pp=5.0):
    """资金部署停滞探测。position_pct_history: most-recent-last 的主账户
    日度仓位（百分数，如 17.4）。floor: 策略 total_position_floor（小数，如 0.50）。
    触发：最近 min_stall_days 天仓位全部 < (floor*100 - margin_pp)。返回告警串或 None。

    min_stall_days=5：部署缺口比策略停滞更该快速行动；6/8→6/12 正好 5 日。
    margin_pp=5：留出「无信号日合理低仓」缓冲（策略 cash_drag_alert 允许无信号时
    保持现金），连续 5 日低于 45% 才算真卡死，而非单日无信号。"""
```

**2. 持久化**（1 处，约 2 行）：`agents/coordinator.py:~1235` 的
`_update_performance_history` 已经读 `accounts/main.json`（其中含 `position_pct`），
追加：
```python
entry["main_position_pct"] = accounts.get("main", {}).get("position_pct", entry.get("main_position_pct"))
entry["lab_position_pct"]  = accounts.get("lab",  {}).get("position_pct", entry.get("lab_position_pct"))
```
history 仅向前累积（与 F4 一致，往回无 position_pct 数据）。

**3. 调用点**：盘后 workflow 警告装配处（`coordinator.py:~1770` 附近），新增
`_deployment_stall_warning()` 防御式调用。**注意：不要**像 `_iteration_stall_warning`
那样挂在 `audit_decision == 'NO_CHANGES'` 分支下——部署停滞与审计决策无关，应无条件评估。

**4. floor 来源**：`active.json` → `main_strategy.rules.position_sizing.total_position_floor`（0.50）。

**5. 测试（TDD）**：连续 N 日低于阈值→告警；达标/超过→沉默；历史不足→沉默；
floor 从策略读取；margin 边界。

**工作量**：~2 行持久化 + ~15 行探测器 + 调用点 + 测试。**今天即可做。**

> 定位：F5 是**监控**不是修复——把问题暴露给人。F1（除根因）+ F2（供真实候选）
> 落地后部署应自愈；F5 是不自愈时的安全网。

---

## F2：真实入场过滤（判断：三者中最大，分阶段）

### 现状缺陷
`agents/execution_planner.py:~395` `_check_entry_conditions` 永远 `return True` →
watchlist 中 tag 匹配的 28 只全部入选。F1 之后，看涨日会把 28 只全推进闸门
（canary 只留 1 笔，但**留哪笔靠顺序/规模，质量为零**）。

### 入场规格（来自 `active.json` `main_strategy.rules.entry`）
非ST + 日成交额>3亿 + (ROE>12% 或 股息率>3%) + 资产负债率<60% +
(MA5>MA20 或 MA20>MA60) + 板块估值合理。

### 数据可用性判断（关键）
- **watchlist 条目不带任何指标/基本面字段**（仅 code/name/sector/type/tag/reason）。
  stub 无法仅靠 watchlist 填实。
- 仓库**有数据基建**：`data/cn_pit/`（PIT 基本面 ROE/负债 via h50a；qfq 价格 via h47），
  `scripts/h47_build_tushare_qfq_prices.py`、`scripts/h50a_build_tushare_pit_quality.py`。
  **但这些是为回测/研究线（h30–h53）建的，从未接进 live `execution_planner`。**
- 所以 F2 = 把已有 PIT 质量表 + 价格表接进 `_check_entry_conditions`。

### 实施判断：分两阶段
- **F2a（最小可用，先做）**：用**价格数据**做趋势 + 流动性过滤——MA5>MA20（qfq 价格）
  + 日成交额>3亿。仅此就消灭「全部 True」问题，且数据轻（只要 OHLCV）。
  缺数据时 **fail-closed（拒绝，绝不 default-True）**。
- **F2b（later）**：叠加基本面闸门（ROE/股息/负债率）from PIT 质量表，需为 watchlist
  名称建**每日基本面刷新**（PIT 表是研究快照，需确认覆盖这些大盘股且每日更新，
  或新增 ingest 步骤）。

### 测试性改造（顺带修坏味道）
抽出纯函数 `_passes_entry(stock, indicators, strategy) -> bool`，吃**注入的**
indicators dict → 无 I/O 可单测；薄 loader 负责取数。现 stub 什么都不读、无法测。

### 工作量与风险
- **不是小修。** 三者中最大。依赖：(a) 28 只 watchlist 的有新鲜度保证的日度数据，
  (b) 精确阈值，(c) 缺数据 fail-closed。
- **若跳过**：F1 后看涨日把资金部署进任意名称（canary 1/日限制损害，但选股随机）。
  故 F2a 应在 F1 后**较快**跟进，**且必须在放宽 canary 上限（F3）之前**。

---

## F3：canary 上限校准（判断：策略决策 + 极小 config 改动，非代码）

### 现状（`config/execution.json`）
mode=canary；per_order 2%、max_buys 1、max_sells 1、daily_turnover 4%。
毕业：`mode=live`（跳过 canary 上限）；急停：`EXECUTION_HALT` 文件 或 mode=halt。

### 数学
F1 解锁后，floor=50%、当前 ~17.4%，缺口 ≈33pp。≤2%/笔、1 买/日 → 填满约 **17 个
交易日（≈3.5 周）**。

### 实施判断
两个杠杆：
- **上限大小**：`max_buys` 1→2~3、`per_order` 2%→3~4%、`daily_turnover` 4%→8~10%。
  把爬坡缩到 ~5–8 个交易日。
- **毕业到 live**：定一条**书面**标准（如 N 个 canary 干净日：INV-1..7 账本 PASS +
  无 kill-switch + 对账 byte-exact）再 `mode=live`。

### 推荐
- **F1+F2a 落地并观察数日干净之前，不要放宽上限。** F1 单独（无 F2）配更松上限，
  只会更快灌进未过滤候选——错误顺序。
- 顺序：F1（已完成）→ 观察 2–3 个 canary 日确认真买入 → F2a → F3 适度放宽
  （max_buys=2、per_order=3%、turnover=6%）→ 观察 → 按书面标准毕业到 live。
- 保持保守：canary 的全部意义就是有界爆炸半径，慢爬在验证期是**特性不是 bug**。
  按用户决策偏好（学习价值 > 长期回报 > 效率），稳健分阶段优于快速部署。
- **就绪后只改 config**：编辑 `config/execution.json` 的 `canary` 块，无代码。
  `per_account` 留有 main/lab 差异化覆盖位。

---

## 一句话判断

- **F5**：今天就能做，廉价独立安全网，对标 F4 模式。
- **F2**：最大的一块；先做 F2a（价格趋势+流动性，fail-closed），F2b（基本面）押后；
  必须先于 F3。
- **F3**：不是代码是策略；F1+F2a 干净观察前不放宽；放宽时只改一段 config，并配
  书面的 live 毕业标准。
