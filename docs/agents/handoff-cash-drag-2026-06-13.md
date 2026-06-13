# Handoff: Cash-Drag 修复线（claude-code → Hermes）

- **日期**：2026-06-13
- **执行 agent**：claude-code（mac），由 repo owner 直接授权
- **状态**：F1/F5/F2b 已实施、测试、提交并 push 到 `origin/main`；F3 不放宽（判断+就绪方案）。

## 背景

2026-06-08→06-12 主账户卡在 ~16-17% 仓位（目标下限 50%），"候选不足→无交易"。
根因不是候选不足，而是三层叠加：① G0/MODIFY 死锁、② 入场过滤是 `return True` stub、
③ canary 限速。本线修复 ①②，并补部署停滞监控。

## 已实施（已提交 + push，main）

| 项 | commit | 文件 | 测试 |
|---|---|---|---|
| F1 G0 死锁修复 | `c2ec0da` + merge `921e3d0` | `agents/execution_planner.py`（买入动作自带 `stop_loss`） | `tests/test_planner_stop_loss.py` |
| F5 部署停滞告警 | `9f92bb8` | `agents/strategy_maintainer.py`（`detect_deployment_stall`）、`agents/coordinator.py`（持久化 `position_pct` + `_deployment_stall_warning` + 盘后无条件调用） | `tests/test_deployment_stall.py` |
| F2b 真实入场过滤 | `3bd3f90` | `agents/execution_planner.py`（`compute_ma`/`passes_entry`/新浪日线 fetch/改造 `_check_entry_conditions`） | `tests/test_entry_conditions.py` |
| F3 + 文档 | `cd1bd86` + 本次 | `docs/cash-drag-followups-F2-F3-F5.md` | — |

全套：**923 passed, 5 skipped**。详见各 commit message。

## ⚠️ 请 Hermes 处理

### 1. coordinator.py 里你自己的未提交改动（必须 review + commit）

提交 F5 时，`agents/coordinator.py` 工作树已混有**你自己**的一段现金账户价格
更新改动（`@@ ~1922` hunk："Always update cash-only accounts (no positions)…"）。
我用 `git apply --cached` 把它与 F5 **逐 hunk 隔离**，F5 commit **不含**这段；它
**原样保留在工作树未提交**。请你 review 并自行提交——这是你的工作，我没有修改也
没有提交它。

验证：`git diff agents/coordinator.py` 应只剩这一个 hunk。

### 2. 其他工作树残留（系统/你自己的，我未触碰）

```
 M strategies/performance_history.json     # 你的 perf 写入
 M docs/repo-hygiene-report-2026-06-06.md  # 你的
?? trades/2026-06/                          # 交易记录
?? value_account/h34_shadow_account_config.json.bak
```
这些我全程未碰，留给你按常规处理。

## F2b 运行注意事项

- 入场过滤数据源 = 新浪免费日线（`money.finance.sina.com.cn/.../getKLineData`，无 token）。
  盘前对 watchlist 标的**逐个 curl**（同 code 当日缓存）。注意接口**限频**——28 只
  连续请求若被限，建议加重试/限速（属 F2b-later 优化）。
- **fail-closed**：接口失败/日线不足 20 根 → 该标的拒绝。极端情况（接口全挂）→ 0 候选
  → 无交易 → **F5 部署停滞告警会在 5 个交易日后触发**，形成闭环。这是有意的安全语义。
- 成交额用 `close*volume` 估算（新浪无 amount 字段），watchlist 均大盘股，判 3亿门量级足够。
- 基本面（ROE/股息/负债率）+ 板块强度仍为 **F2b-later**（需 PIT 接入 / 扩展 sector 覆盖）。

## F3：canary 不放宽（当前状态）

`config/execution.json` 保持 `mode=canary` + `per_order 2%/max_buys 1/turnover 4%`，
**未改**。理由：F2b 尚未经任何真实交易日观察 + AGENTS.md「不得擅改 production trading
config」。**建议**：观察 ≥3 个 canary 交易日确认 F1+F2b 后确实开始买入（账本 PASS、
`executed>0`、无 HALT），再按 `docs/cash-drag-followups-F2-F3-F5.md`「当前状态」小节的
确切 config diff 放宽。**放宽需 task brief 授权，本 handoff 不构成授权。**

---

## 验证结论（2026-06-13，Hermes 非破坏性重跑）

owner 反馈「主账户长期 16-17% 空仓太逆天」。Hermes 重跑验证（只读 + Sina API，
**未写任何生产文件**，accounts/main.json 仍 17.4%）结论：「空仓」是三层叠加，非单一 bug。

1. **6/12 那天空仓合理** —— 看跌日，策略设计降仓不开新仓。
2. **F1+F2b 已验证有效**（用看涨日 6/10 对比）：F1 → buy 自带 stop_loss → `APPROVED`
   → G0 放行（解除 6/10「候选27→否决27」死锁）；F2b → 弱市 watchlist 28→5 PASS
   （MA5>MA20+成交额，茅台/宁德走弱被拒），不再全进。
3. **canary 限速是结构瓶颈** —— 每天最多 1 笔 ≤2%，17.4%→50% 需 ~16 个交易日。

### ⚠️ 验证暴露的更深结构问题（→ F6）

`agents/execution_planner.py _generate_neutral_plan` **只生成一个 `hold`，完全不调
`_generate_main_account_actions`**。只有 `_generate_bullish_plan` 开新仓，neutral 只
hold、bearish 只 reduce。**后果**：主账户从 17%→50% 只能靠 bullish 日；6/8–6/12 全
中性/看跌 → 一直空着。而 `active.json` 的 `cash_drag_alert` 明确声明「仓位低于50%时
触发信号扫描」——代码从未实现此意图。这是「持续空仓」最深的结构原因。

**F6 修复（claude-code 已实施）**：`_generate_neutral_plan` 改为调
`_generate_main_account_actions`/`_generate_lab_account_actions`（经 F2b 过滤 +
自带 stop_loss），中性市低仓时温和建仓；无合格候选则保持 hold（对齐 cash_drag_alert
「不为执行而执行」）。受 `total_position=0.55` 上限 + F2b fail-closed + canary 限速
约束。这是实现策略已声明的意图，非 strategy change（active.json 规则/参数不变）。

真实冒烟（6/13 中性市）：修复前中性日只有 1 个 hold、**0 建仓**；F6 后产生 **5 个
main 候选**（招行/美的/长江电力/中国神华/陕煤，MA5>MA20+成交额达标，带 stop_loss）。
测试 `tests/test_neutral_plan.py` 3 个。全套 926 passed, 5 skipped。

> 影响：主账户不再只能靠 bullish 日部署，占多数的中性日也会建仓 → 配合 F3 放宽
> canary 后，17%→50% 的爬坡才真正可行（否则缺 bullish 日时 F3 放宽也无单可买）。
