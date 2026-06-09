# Changelog Schema

> `strategies/changelog.json` 的字段约定。
> 引入版本：2026-05-14（P0.2）。

## 为什么有这份 schema

之前 `change_type` 字段被多个 writer 混用，出现 10 种取值（`parameter_adjustment`、`strategy_upgrade`、`param`、`risk_control`、`weekly_review`、`review`、`trade_review`、`init`、`restore`、`<missing>`）。结果：**无法从 changelog 一眼看出"策略真的迭代了多少次"vs"只是修执行 bug"**。

修正：用受控词汇表，让"策略版本号是否应当 bump"成为可查询的事实。

## 字段约定

每条 changelog entry 必须有：

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | string `YYYY-MM-DD` | 变更日期 |
| `account` | `"main"` \| `"lab"` \| `"both"` | 影响哪个账户 |
| `change_type` | enum（见下） | 变更类型，决定是否触发版本号递增 |
| `description` | string | 一句话描述变更内容 |
| `reason` | string | 触发本次变更的具体事件或观察 |
| `expected_effect` | string | 预期效果（事前） |
| `actual_effect` | string | 实际效果（事后回填，初次写入留空） |

可选字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `backfilled` | bool | 是否为历史数据回填，默认 false |
| `legacy_change_type` | string | 历史词汇表中的原值，仅在 2026-05-14 之前的条目上出现 |
| `triggering_event_count` | int | 触发本次变更的不同事件数。`1` 表示单事件触发，是 overfitting 高风险信号 |
| `oos_validated` | bool | 该变更是否通过过 OOS 窗口验证；用于审核层评估 |
| `backtest_evidence` | object | 历史回测证据摘要；仅用于保留人工回填上下文 |

## `change_type` 受控词汇表

| 值 | 用法 | 是否 bump 版本号 |
|---|---|---|
| `strategy` | 策略逻辑或参数的真实变更（入场条件、止盈止损、仓位规则等） | **是** |
| `execution` | 修执行 bug，逻辑/参数未变（如分批建仓没分批、订单超限） | 否 |
| `risk` | 修改 `references/risk-rules.md` 里的硬性风控规则 | 否（独立轴） |
| `data` | 数据源、字段、抓取频率变更 | 否 |
| `system` | 系统级事件：初始化、恢复、回滚、热修复 | 否 |

**版本号触发规则**：只有 `change_type == "strategy"` 且 `oos_validated == True` 的变更才允许 `strategy_maintainer` 递增 `active.json` 的 `version` 字段。其他类型变更只记录，不动版本。

## 历史值映射（用于回填 2026-05-14 之前的条目）

| 旧值 | 映射到 | 备注 |
|---|---|---|
| `parameter_adjustment` | `strategy` | strategy_maintainer 历史上对所有自动写入硬编码用了这个值；保守按 strategy 归类 |
| `strategy_upgrade` | `strategy` | 同上 |
| `param` | `strategy` | typo |
| `risk_control` | `risk` | 字面对应 |
| `weekly_review` / `review` / `trade_review` | `system` | 复盘记录不是变更本身，但保留在 changelog 里；归 system 不 bump 版本 |
| `init` / `restore` | `system` | 字面对应 |
| `<missing>` | 逐条人工判断 | 已在回填中处理 |

回填保留原值在 `legacy_change_type` 字段，以便后续可重新分类。

## Writer 约定

任何写 changelog 的代码必须：

1. 提供 `change_type`，且值必须在受控词汇表内；如果缺省，writer 可默认 `strategy` 并加 `triggering_event_count` 字段；如果显式提供了非法值，必须拒绝写入
2. 不允许写入未在 schema 中声明的新字段（forward-compat 由本文档管理）
3. 写入前调用 `_validate_changelog_entry(entry)` 校验

当前唯一的程序化 writer 是 `agents/strategy_maintainer.py:_create_changelog_entry`，已更新为按本 schema 工作。
