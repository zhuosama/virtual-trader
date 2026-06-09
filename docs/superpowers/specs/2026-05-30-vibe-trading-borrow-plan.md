# Vibe-Trading Borrow Plan v2

Date: 2026-05-30
Status: DRAFT v2 — pending user activation under Charter v1
Author: claude-code (session)
Source upstream: https://github.com/HKUDS/Vibe-Trading (MIT)
Charter: `docs/research-charter-v1.md` (v1.0-DRAFT)
Supersedes: v1 of this file (2026-05-30 morning) + Hermes review §9 (inline merged below)

## 1. Purpose

调研 HKUDS/Vibe-Trading，识别**可直接搬运 / 可借鉴架构 / 谨慎搬 / 不搬**的模块，
形成 Charter v1 框架下的可执行落地路径，避免再次出现 H42 / H50b / H51b
"自研因子搜索脚手架"重复造轮、IC 仍不及成熟因子库的失败模式。

Non-goal：本文件**不**修改代码、不分派 Hxx、不下任何活订单。落地分两条
通道：(a) engine/loader PR（按 Charter §4 Kill Criterion 2，不占 slice budget），
(b) spike → Hxx slice（按 Charter §5 hypothesis pipeline）。

## 2. Project Characterization

### 2.1 virtual-trader (本地)

- A 股专精；CSI300 + CSI500 universe；PIT fundamentals + sha256 immutable
- 5-agent 硬编码：`agents/coordinator.py` (686 LOC) →
  `market_analyst.py` / `execution_planner.py` / `risk_controller.py` /
  `review_agent.py` / `strategy_maintainer.py`
- `agents/audit_layer.py` 三道闸（Overfitting / Risk / Cost & Execution）
  门禁 `strategies/active.json` 写入
- `backtest/backtest_engine.py` + `strategy_simulator.py` + `oos_window.py`
- `value_account/` 仿真账户（paper-only），不接真券商
- 当前 ingestion: `scripts/ingest_cn_pit_data.py` (1733 LOC) 用 yfinance 直采
  且**未**接入 `backtest/market_data.py` 已有的 provider 抽象层；Tushare qfq
  prices 由 H47/H48 单独脚本生成；provider 层（`MarketDataProvider` / `FallbackMarketDataProvider`
  / Akshare/BaoStock/Tushare/YFinance Provider）已存在但仅服务于 backtest 路径
- Charter v1 §2 frozen 着 universe / price / benchmark / gate / engine tag
- License: MIT (Copyright 2026 zhuosama)

### 2.2 Vibe-Trading (upstream)

- 多市场（A / HK / US / Crypto / Futures / Forex / Options）+ Composite engine
- DAG swarm orchestrator + 29 个 YAML preset
- **Alpha Zoo 452 因子** (qlib158 / alpha101 / **gtja191** / academic)，
  AST-only lookahead 检查 + lazy compute + IC/IR bench
- Loader Registry 自动回退链（Tushare → AKShare → mootdx 等）
- FTS5 持久记忆 + 5 层 context compression
- 13+ LLM provider 抽象（DeepSeek / Qwen / Zhipu / Kimi / MiniMax / Ollama …）
- FastAPI + React 19/Vite SSE 前端 + MCP stdio server
- 77 个 YAML-frontmatter Python skill 模块
- License: MIT

### 2.3 License 兼容性 (Q1 closed)

双方均为 MIT。搬运时需在每个移入文件保留上游版权 header + 在 `LICENSE` 同级
新增 `THIRD_PARTY_NOTICES.md` 列出 Vibe-Trading 的 MIT 声明、commit SHA、
搬运日期。无 GPL/copyleft 风险。

## 3. Tier 评估

### Tier A — 直接搬运（高 ROI、与现有纪律不冲突）

| # | 模块 | 上游路径 | 本地落点 | Charter 通道 | 关键约束 |
|---|---|---|---|---|---|
| A1 | gtja191 + alpha101 因子定义 | `agent/src/factors/` | 新增 `backtest/factors/` | **§5 #4 spike → H53** | AST-only 加载；IC 预筛 gate（见 §5.3） |
| A2 | Loader Registry + provenance hardening | `agent/backtest/loaders/registry.py` | **硬化现有 `backtest/market_data.py`**（已存在 9 个 Provider 类）+ 重构 `scripts/ingest_cn_pit_data.py` (1733 LOC) 接入 provider 层 | **§4 Kill Crit 2 engine PR**（不占 slice budget） | fallback 必须写真实 provenance；STOP-on-missing；sha256 双层校验 |
| A3 | Monte Carlo bootstrap CI + walk-forward | Vibe-Trading metrics | 增强 `backtest/oos_window.py` | 一般 commit (additive) | 输出置信带写入 run JSON |
| A4 | Run-card 输出格式 | `backtest/runs/*.json + *.md` | 替换 `backtest/runs/` 散文件 | 一般 commit | console 渲染兼容 |
| A5 | mootdx 免 token A 股 loader | `agent/backtest/loaders/mootdx.py` | A2 之后追加 | **§4 Kill Crit 2 engine PR follow-up** | vs H47 qfq sha256 比对 → 不达标只做 dev fallback |

### Tier B — 借鉴架构（思路抄，代码重写）

| # | 模式 | 替代当前什么 | 收益 | 备注 |
|---|---|---|---|---|
| B1 | DAG swarm + YAML preset | 隐式硬编码 5-agent 流程 | 外部可观察；煤炭/科技子 preset 不动 coordinator 686 LOC | 大重构，Charter v1 之外，留 v2 Charter |
| B2 | YAML-frontmatter skill 模式 | `agents/audit_prompts/` 散文件 + `references/risk-rules.md` | review_agent 可声明式引用 | — |
| B3 | 多 LLM provider 抽象 + reviewer/generator 分离 | `agents/llm_client.py` 单 provider | **anti-collusion**：audit_layer 用独立模型审查 strategy_maintainer 的产出，防止"自己批改自己作业" | 这是动机，不是节省 token |
| B4 | FTS5 跨会话持久记忆 | `records/events/` 纯文件追加 | review_agent 语义召回"上次同 sector 判错点" | — |
| B5 | 5 层 context compression | review_agent / coordinator 各自手写截断 | 一致 token budget | — |

### Tier C — 谨慎搬（与现有纪律有冲突，须改造）

| # | 模块 | 冲突点 | 改造前置 |
|---|---|---|---|
| C1 | `skill_writer_tool.py` agent 自我 CRUD | 绕开 `strategies/active.json` 必经 audit_layer | write 通道接 `strategies/proposals/` + audit_layer 三道闸 |
| C2 | Shadow Account 从券商流水反解 | 当前 `value_account/` 是仿真，不是真账户 | 等接真券商再说 |
| C3 | React 19 + Vite SSE 前端 | 已有 `console/` FastAPI + 模板 | 仅借鉴 RunDetail 卡片排版 |

### Tier D — 不要搬（与本仓核心纪律冲突）

- **D1**：ReAct loop 运行时自我进化 skill — 与 sha256 immutable 协议冲突
- **D2**：Robinhood agentic trading 集成 — 本仓显式 paper-only
- **D3**：上游"loader 静默回退"语义 — 必须改造成 A2 的**显式 provenance 写真**

## 4. AGENTS.md Hard Prohibitions 兼容性矩阵

| 条款 | A1 因子 | A2 回退链 | A3 OOS | A4 run-card | A5 mootdx |
|---|---|---|---|---|---|
| No data fabrication | OK | OK | OK | OK | OK |
| No source provenance forgery | OK | **§4.1 强制** | OK | OK | **§4.1 强制** |
| Symmetric restore | N/A | OK | N/A | N/A | OK |
| Original ingestion verdicts immutable | OK | OK | OK | OK | OK |
| Exit-code is not acceptance | OK | OK | OK | OK | OK |
| No silent workarounds | OK | **§4.1 强制** | OK | OK | **§4.1 强制** |
| sha256 audit hooks | 不涉及 data mutation | **§4.1 必新增** | OK | OK | **§4.1 必新增** |

### 4.1 A2/A5 Provenance Hardening 实施细则（吸收 Hermes review Issue 2）

落地时必须满足以下四点，缺一不可：

**(a) `source_provider` 枚举值域**

```python
SOURCE_PROVIDERS = {
    "tushare:pro_bar:qfq",      # H47 baseline
    "tushare:daily",
    "tushare:cn_stock_daily",
    "akshare:stock_zh_a_hist",
    "akshare:stock_zh_a_hist_qfq",
    "mootdx:std",
    "yfinance:download",         # legacy, A2 前路径
    # 新增 provider 必须改这里 + 测试
}
```

**(b) Fallback 触发的日志 + run JSON 格式**

```python
# 日志（stderr，line-prefixed BLOCKER / WARN）
[WARN loader] primary=tushare:pro_bar:qfq missing token; trying fallback=akshare:stock_zh_a_hist
[OK   loader] fallback=akshare:stock_zh_a_hist succeeded; provider written through

# run JSON 的 data_sources 块
{
  "data_sources": {
    "prices": {
      "fallback_chain": ["tushare:pro_bar:qfq", "akshare:stock_zh_a_hist"],
      "selected": "akshare:stock_zh_a_hist",
      "sha256": "<actual>",
      "rows": 123456,
      "fallback_reason": "TUSHARE_TOKEN env var missing"
    }
  }
}
```

**(c) STOP 实现**

- token 缺失 / endpoint 拒绝 / schema 不兼容 → `raise LoaderBlockedError(reason)` →
  调用方退出码 != 0 + stderr 打 `BLOCKER:` 前缀行
- **绝不**静默 catch 并 fallback
- fallback 是 loader **声明**支持的备用源；fail-over 是错误状态（必须 surface）

**(d) sha256 校验失败行为**

- 写入前：计算 sha256；与上次 run JSON 中的 sha256 比对；mismatch → `raise
  ChecksumMismatchError(prev, curr)` → exit != 0
- 单纯 log 不算合规（这是 H49a 的教训）

## 5. 落地路径

### 5.1 A2 — Loader Registry + Provenance Hardening (Engine PR)

**Charter 通道**：§4 Kill Criterion 2 — engine/loader 工作必须拆独立 PR，
不消耗 slice budget。

**详细计划**：见 `docs/superpowers/plans/2026-05-30-loader-registry-engine-pr.md`
（同步创建，可直接交付 Hermes 执行）。

**优先级**：P0（其他所有 Tier A 项目都依赖 A2 的数据可信度）。

**回滚 SOP（吸收反 review HIGH-A）**：

| 层 | 触发条件 | 动作 |
|---|---|---|
| 代码层 | engine PR 合并后发现 regression | `git revert <merge sha>` 一条命令恢复 |
| Artifact 层 | A2 跑过 ingestion 产生新 run JSON | **不删，标 deprecated**：在 `data/cn_pit/metadata.json` 加 `deprecated_runs: [...]` 字段，保留历史可追溯 |

回滚定义包含**两层**——禁止"为了回滚去 rm artifact"（H52h 类违规）。

### 5.2 A5 — mootdx Loader (Engine PR follow-up)

**Charter 通道**：§4 Kill Criterion 2，跟在 A2 后追加 commit。

**质量门槛（吸收 Q5）**：

1. mootdx 跑出 HS300 universe 全量 qfq prices，sha256 与 H47 `prices_h47_tushare_qfq_candidate.csv` 比对
2. row-level diff: 缺失行 ≤ 0.5%，价格 abs diff ≤ 0.5%（A 股 qfq 调整可能微差）
3. **达标** → 注册为 prod fallback；**不达标** → 只做 dev fallback（环境变量 `LOADER_ALLOW_MOOTDX_PROD=1` 才启用）

**计划**：A2 合并后 1 周内，单独 spike `docs/spikes/2026-06-XX-mootdx-vs-h47-diff.md`。

### 5.3 A1 — Alpha Zoo Spike → H53 (if signal-positive)

**Charter 通道**：§5 hypothesis #4 (cross-sectional composite rank, Hermes-proposed) —
**必须先 spike，阳性才能升 Hxx**。

**Step 0 — Spike (≤2 小时，Charter §3 spike budget)**

- 落点：`docs/spikes/2026-05-XX-alpha-zoo-csi300-ic-spike.md`
- 范围：仅 gtja191 中**最常用的 10 个**因子（参考上游 README 标的）
- 数据：A2 合并后的 loader 输出 + H47 qfq prices
- 输出：10 因子的 IC、IR、与 HS300 的 rank correlation
- 阳性判据：**至少 3 个因子的 IC abs > 0.03，IR > 0.5**（占位阈值；最终阈值见 §5.3 IC gate 子节）

**Step 1 — H53 brief (if spike +)**

- 命名：`docs/hermes-h53-alpha-zoo-ic-bench-task.md`
- 内容：把上游 gtja191 + alpha101 全量（~300 因子）跑 CSI300 + CSI500 IC bench
- 必须包含本文件 §4.1 (provenance) + §5.5 (sha256 manifest) 双重审计

**IC 预筛 gate 子节（吸收 Hermes Issue 1 + 反 review BLOCKER-A）**

- Hermes Issue 1 建议 `|IC| > 0.03, IR > 0.5` — **这是占位阈值，不是 Charter 阈值**
- Charter v1 § 2 frozen gate 是 9 条件，其中第 7 条 `beat_HS300_windows >= 2/5`
  是策略级阈值，**不直接对应因子级 IC**
- **决策（待 user 确认）**：H53 brief 起草前，user 需在 Charter 里加一条**因子级
  阈值**作为新 §2 frozen 条目，或在 H53 brief 自身声明（标 `RESEARCH_ONLY`）
- 在此之前**不**用 0.03 / 0.5 这两个数字作为 spike 阳性判据；spike 报告里写
  "PROPOSED_THRESHOLD: 0.03 / 0.5 — pending Charter alignment"

**时间预算（吸收反 review LOW-B）**

| Step | wall-hours | Charter budget 影响 |
|---|---|---|
| A2 engine PR (§5.1) | 6h–10h | 不占 slice budget |
| A5 follow-up (§5.2) | 2h spike + 4h impl | 不占 slice budget |
| A1 spike (§5.3 Step 0) | ≤2h | 不占 slice budget（Charter §3） |
| A1 H53 (§5.3 Step 1, if +) | ~2 周 | **占 1/6 slice** |
| 总计 | ~3 周 wall-clock | == Charter §3 wall-clock budget |

**关键 trade-off**：A1 落地 = 燃尽全部 Charter v1 wall-clock 预算。如果 user
打算在 Charter v1 内还要做其他 hypothesis（§5 #1/#2/#3），A1 必须削范围。

### 5.4 A3 / A4 — 一般 commit

- **A3** (`backtest/oos_window.py` 加 walk-forward + bootstrap CI)：additive，
  旧调用不破坏。`fix(h26): add bootstrap CI to oos_window`
- **A4** (run-card 格式)：console 渲染端兼容旧 schema 一个版本后再 deprecated。
  `feat(console): unified run-card format from Vibe-Trading`

### 5.5 因子版本管理 (吸收 Hermes review §9.4)

A1 落地时同步建：

- `backtest/factors/MANIFEST.sha256` — 每个因子文件的 sha256 + 上游 commit SHA
  + 搬运日期
- `backtest/factors/UPSTREAM_DIFF.md` — 与上游差异点（如本地 schema adapter
  修改）
- Cherry-pick 上游更新时必须更新 MANIFEST，CI 检查 MANIFEST 与文件实际
  sha256 一致

### 5.6 上游 fork 策略 (吸收 Hermes Issue 6 + 反 review MEDIUM-A)

- **Fork**: `zhuosama/Vibe-Trading`（与 `hermes-agent` fork 同模式）
- **基线 commit**: 待 fork 后填入 `THIRD_PARTY_NOTICES.md`，所有搬运动作引用
  这个 SHA
- **升级路径**: 手动 cherry-pick，**禁止**自动 merge；每次升级触发 MANIFEST
  全文件 sha256 diff
- **断链场景**: 若上游 force-push 改写历史，fork 上的基线 SHA 仍可定位

## 6. Open Questions

### Closed in v2

- ~~Q1 License~~ → §2.3 close (双方 MIT 兼容)
- ~~Q4 Charter vs 基建~~ → §5 close (A2 = engine PR per §4 Kill Crit 2;
  A1 = spike → H53 per §5 #4)

### Open

| # | 问题 | 阻塞什么 | 谁来定 |
|---|---|---|---|
| Q2 | gtja191 / alpha101 公式的列名与本仓 PIT schema 对齐方式（adapter 层？） | A1 spike Step 0 | data exploration |
| Q3 | 因子级 IC 阈值要不要进 Charter §2 frozen 层？现 spike 用 0.03 / 0.5 仅作占位 | A1 spike 阳性判据 | **user** |
| Q5 | mootdx 数据质量比对（已在 §5.2 定义判据） | A5 是否进 prod fallback | A5 spike 输出 |
| Q6 | Vibe-Trading 上游具体锁定哪个 commit SHA？ | A1/A2 任何搬运动作 | fork 时填 |
| Q7 (new) | gtja191 横截面 ranking 因子 vs coordinator sector-aware analyst：融合方式？(a) 全 universe rank → 切 sector (b) sector 内 rank (c) ranking 作为 analyst 输入维度 | H53 brief 范围 | **user** + architectural 讨论 |
| Q8 (new) | Charter v1 是否激活？现状 "DRAFT — pending user review"；本计划假设激活并按其约束设计 | 全部 P0/P1 时序 | **user** |

## 7. Decision Log

- 2026-05-30 morning: v1 文件创建（initial Tier 评估）
- 2026-05-30 noon: Hermes review §9 写入 v1（6 个 review notes + §9.3/9.4/9.5）
- 2026-05-30 afternoon: claude-code 反 review（ACCEPT-WITH-NOTES，3 个 Hermes 自身盲点 + 2 个未覆盖问题）
- 2026-05-30 v2: 吸收上述全部 review；按 Charter v1 §4/§5 重整通道；关闭 Q1/Q4；增加 Q7/Q8；Section 8 (Out of Scope) 移到末尾正确位置
- 2026-05-30 evening: A2 engine PR (`docs/superpowers/plans/2026-05-30-loader-registry-engine-pr.md`) **EXECUTED locally**, dispatched Task 1-6 to Hermes oneshot via `hermes -z --yolo`, each task audited by claude-code with sha256 守门 + git diff + 独立复跑 acceptance. Task 7 self-executed (plan smoke command字面不可执行，已修正)。 Outcome:
  - **6/7 Tasks COMPLETE**, AG-1 / AG-2 / AG-5 PASS, AG-3/AG-4 DEFERRED (no live ingestion path — verifies on first user-triggered fetch-prices)
  - Three plan literal GAPs surfaced & corrected in v2 of the PR plan: `fetch_historical_prices`→`fetch_prices` (Task 5.2), runbook English→Chinese §7 (Task 6.2), Task 7 smoke command flags non-existent (replaced with module + unit smoke)
  - Hermes behavior notably improved over H49a/H52h baseline: 1 BLOCKED correctly raised (Task 6 §6.2), all UNEXPECTED ADDITIONS surfaced, no silent workarounds detected
  - Rollback gap fixed: `scripts/ingest_cn_pit_data.py` was untracked → `git add` to enable `git revert`
  - Scope: local use only, NO remote PR push (per user 2026-05-30)
- 2026-05-30 night: A1 Alpha Zoo CSI300 IC spike dispatched per `docs/spikes/2026-05-30-alpha-zoo-csi300-ic-spike.md` (Charter §5 hypothesis #4 path) → **SIGNAL_NEGATIVE** (gtja191 90% multi-column factors uncomputable on H47 close-only matrix; postmortem in `docs/strategy-optimization-sync.md § A1 Spike`)
- 2026-05-30 night: pivoted to Recommendation (2) — close-only factor families spike (`docs/spikes/2026-05-30-close-only-factors-csi300-ic-spike.md`) → **SIGNAL_NEGATIVE** (12/12 factors computable, 6 pass |IC|>0.03, 0 pass IR>0.5; best rev_1d IC=0.042 IR=0.242)
- 2026-05-30 night: R1 reverse-composite spike (`docs/spikes/2026-05-30-r1-reverse-composite-csi300-ic-spike.md`) → **SIGNAL_IMPROVED_BUT_INSUFFICIENT** (composite IR=0.272 vs theoretical max 0.273 = 99.5% captured; close-only hypothesis #4 KILLED on architectural ceiling)
- 2026-05-31 early: OHLV supplemental engine PR (`docs/superpowers/plans/2026-05-30-ohlv-supplemental-engine-pr.md`) **EXECUTED locally** per Charter §4 Kill Criterion 2 path. 6/7 Tasks Hermes-dispatched + audited. Task 6 smoke (limit-tickers 10) verified 7/8 AGs PASS: 5 baseline-frozen file shas守门 unchanged + metadata.json 8 existing keys verbatim preserved + new ohlv_layer block (11 fields) added correctly + Akshare fallback triggered correctly + 224 unit tests pass. OG-5 row count fell short (42 rows vs ~200 expected) because AkshareProvider.get_ohlcv silently drops 8/10 tickers — provider-level surface bug, NOT a PR failure. Three spike postmortems (A1 / close-only / R1) in `docs/strategy-optimization-sync.md` document the architectural ceiling that motivated this engine PR.
- 2026-05-31 mid: `fix(engine-ohlv-v1): surface per-ticker akshare failures` Hermes-dispatched. Root cause: 4 provider classes all had identical `except Exception: pass` + `if data.empty: continue` silent-swallow anti-pattern PLUS `sources_used` provenance forgery (falsely claiming all-ticker success). Fix touched 4 providers + FallbackMarketDataProvider missing_pairs propagation. 5 new unit tests added (per-ticker failure visibility, INFRA_ERROR threshold at >50% failure). 229/229 tests pass. Smoke re-run **revealed** Akshare network instability: Akshare returned RemoteDisconnected for 10/10 tickers, fallback chain auto-promoted to YFinance which delivered 200/200 rows — confirming the engine PR's fallback design works as intended under provider-level instability. Engine layer now ready for H53.
- 2026-05-31 mid: H53 brief drafted (`docs/hermes-h53-gtja191-ic-bench-task.md`) — first true cross-sectional rank Hxx slice consuming 1/6 Charter §3 budget. Re-tests A1 spike (gtja191 zoo) with OHLV unlock.

## 8. Out of Scope (反向声明)

本计划**不**包含：

- 修改任何 `data/cn_pit/` 下的 protected artifact
- 修改 `strategies/active.json` 或 `audit_layer.py`
- 调用任何外网 API（fork、cherry-pick 由 user 手动操作）
- 接真券商或下任何真实订单
- 把 Vibe-Trading 的"自动技能进化"机制接入本仓
- 在 user 答 Q8 前激活任何 Charter v1 约束之外的工作
