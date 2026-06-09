# 设计稿:美股高动量中小盘成长策略(本地模拟 · Hermes 并行模块)

- 日期:2026-06-09
- 设计:mac/claude-code
- 执行:mac/hermes(claude-code 验收)
- 状态:已与用户确认设计方向,待 spec review → 实现计划

## 0. 目标与范围

在现有 A 股 `virtual-trader` 之外,**并行**增加一套**美股**策略:高风险偏好、
高动量中小盘成长选股 + 交易策略(含止盈止损、仓位控制),每日**收盘后次日早晨**
把复盘/持仓/选股推送到**个人微信**。

**纯本地模拟**(零真实资金),与 A 股线完全隔离,仅复用三样现成基建:
个人微信通道、cron 调度、风控门数学。

### 已锁定决策
| 维度 | 决策 |
|---|---|
| 执行/记账 | 纯本地模拟(EOD 撮合,写模拟账本,零真实资金) |
| 归属/执行者 | Hermes virtual-trader 的并行美股模块;Hermes 实现,claude-code 验收 |
| 选股风格 | 高动量中小盘成长(动量 + 中小盘市值 + 成长基本面) |
| 数据源 | **混合**:tushare(价格/动量/日历) + yahooquery(市值/成长基本面) |
| 通知 | 个人微信 `hermes send --to weixin`,北京时间次日 **08:00** |

### 非目标(YAGNI,本版不做)
- 不做期权、日内、杠杆 ETF(本版风格定为中小盘成长股,不叠杠杆 ETF)。
- 不做真实资金/真券商下单。
- 不接 moomoo/Alpaca(见 §11 升级路径,仅作未来路标)。

## 1. 架构:`us_trader/` 并行模块

新目录 `~/.hermes/virtual-trader/us_trader/`,自成体系,**不碰** A 股的
`agents/coordinator.py`、`scripts/*tushare*`(A 股部分)、`accounts/`、`trades/`。

```
us_trader/
  config.json            # 策略参数(见 §4),可热改
  data/
    universe.csv         # 票池(ts_code,name,先静态种子→可刷新)
    cache/               # tushare 价格 + yahooquery 基本面 日缓存(gitignore)
  pipeline/
    fetch_prices.py      # tushare us_daily/us_tradecal → 价格面板
    fetch_fundamentals.py# yahooquery → 市值/成长 快照
    select.py            # 选股管线(§3)
    risk.py              # 仓位/止盈止损/熔断(§4),照搬 A 股 G3/G4 思路
    simulate.py          # 本地 EOD 撮合 + 记账(§5)
    notify.py            # 组装每日微信文案 + hermes send --to weixin(§6)
    run_daily.py         # 编排:fetch→select→risk→simulate→notify;写 health
  state/
    positions.json       # 当前持仓
    nav_history.json     # 净值序列(画图/复盘)
    trades/YYYY-MM.jsonl # 模拟成交账本
    health.json          # 运行健康(mirror dashboard-sync)
  reports/
    YYYY-MM-DD.md        # 每日复盘(微信正文同源)
  tests/                 # 单测(§9)
```

**复用而非重写**:
- 通知:直接 `hermes send --to weixin`(已确认该通道存在,§6)。
- 风控数学:止损/移动止盈/组合熔断的阈值逻辑,参照 `agents/execution_model.py`
  的 G3/G4 思想用 `risk.py` 重写成美股版(独立,不 import A 股代码以免耦合)。
- 调度:沿用 hermes cron;失败走 §7 的 health + 微信告警(与 dashboard-sync 同构)。

## 2. 数据层(混合源)

### 2.1 价格/日历 — tushare(已验证可用)
- `us_tradecal(start,end)` → 美股交易日,判断"昨夜是否开市/最近交易日"。
- `us_basic()` → 6000 只美股清单(EQ/ADR/GDR),做票池底表(字段仅
  ts_code/name/classify/list_date,**无市值无基本面** → 由 2.2 补)。
- `us_daily(ts_code,start,end)` → 日 OHLCV + pct_change,算动量。
- 优点:已有 key、稳定、不限流,扛每日全票池历史拉取。
- token 读取沿用现有方式(`scripts/.tushare_token` 或 `TUSHARE_TOKEN`,保持 gitignore)。

### 2.2 市值/成长基本面 — yahooquery
- 每日对票池做一次快照:`marketCap`、营收同比、盈利同比/EPS 增长、行业。
- 仅取"慢变量",每日一次、分批限速,降低 Yahoo 限流风险。
- 取不到的票当日剔出候选(graceful skip),不让单票拉取失败拖垮整轮。

### 2.3 缓存与失败隔离
- 价格/基本面落 `data/cache/`(gitignore),按交易日键控;重跑当日走缓存。
- 任一源整体失败 → 该轮标记 DEGRADED,写 health,推微信告警,**不静默**(§7)。

## 3. 选股管线(每日 EOD 重排)

`select.py`,纯函数、可单测,顺序过滤后打分取 top N:

1. **流动性门槛**:price > $5(排除仙股)、近 20 日日均成交额 > 阈值(配置)。
2. **中小盘过滤**:marketCap ∈ [下限, 上限](默认约 $3亿–$100亿,配置可调)。
3. **成长过滤**:营收同比 > 阈值 且 盈利/EPS 同比 > 阈值(配置)。
4. **动量打分**:3M/6M 相对强度 + 近期是否突破 N 日高;合成动量分排序。
5. 取 top N(= 持仓数,默认 8)作为**目标持仓**。

每步剔除原因写进当日 reasons,便于复盘与验收。

## 4. 交易 & 风控参数(激进默认,`config.json` 可调)

| 参数 | 默认 | 含义 |
|---|---|---|
| initial_capital | $100,000 | 模拟初始资金 |
| holdings_n | 8 | 目标持仓数(集中=激进) |
| max_position_pct | 18% | 单仓上限 |
| stop_loss_pct | **-10%** | 个股止损(相对成本),触发即平 |
| take_profit | **+30% 移动止盈** | 浮盈达 +30% 后启动移动止盈,自高点回落 10% 落袋 |
| portfolio_dd_halt | **-20%** | 组合回撤达 -20% 暂停加仓(只许减),复用 G4 思路 |
| rebalance | 每日 EOD | 按动量重排,卖出跌出 top N 的,买入新进 top N 的 |
| max_new_buys_per_day | 3 | 单日最多新开仓数(去抖,防换手爆炸) |
| daily_turnover_warn/block | 40% / 75% | 换手预警/阻断 |

`risk.py` 在 `simulate.py` 下单前做门禁:止损/止盈优先于调仓;触发熔断时
只允许减仓方向。所有判定记 decision 日志(verdict/gate/reason)。

## 5. 本地撮合(simulate.py)

- 撮合价:默认昨夜**收盘价**(可配置改为次日开盘价更真实)。
- 成交写 `state/trades/YYYY-MM.jsonl`(含手续费近似:美股按每股/笔佣金近似,配置)。
- 更新 `positions.json`(成本、份额、最高价 watermark 供移动止盈)与
  `nav_history.json`(每日净值、日收益、累计收益、回撤)。
- 与 A 股 canary 一样 `executed` 真写**模拟**账本,但零真实资金、零真券商。

## 6. 每日微信通知(北京时间次日 08:00)

- 通道:`hermes send --to weixin`(已确认存在 `weixin:...@im.wechat` dm 通道;
  **不发企业微信 wecom**)。
- 正文(Markdown,与 `reports/YYYY-MM-DD.md` 同源):
  1. **昨夜复盘**:组合净值、日涨跌、累计收益、最大回撤。
  2. **当前持仓**:每只 代码/名称/成本/现价/浮盈%/距止损止盈。
  3. **今日变动**:买入、卖出、止损触发、移动止盈触发(各列原因)。
  4. **今日 top N 选股**:入选票 + 动量分 + 关键过滤项。
  5. **风险提示**:熔断状态、换手、数据 DEGRADED 提示(若有)。

## 7. 健康与失败告警(mirror dashboard-sync)

- 每轮写 `state/health.json`:`{ts, success, failed_step, error}`。
- 任一步(fetch/select/simulate/notify)失败 → 写 health + **推一条微信告警**
  `[us-trader] ❌ <step>`,不 fail-silent。
- 连续失败去抖:与 dashboard-sync 同思路,失败转成功时发一次"✅ recovered"。

## 8. 调度(hermes cron)

- **跑批**:美股收盘后(北京约 06:00,按夏/冬令时偏移;用 `us_tradecal` 确认昨夜开市,
  非交易日跳过)→ `run_daily.py` 全流程。
- **通知**:北京 **08:00** 推送(若跑批已含通知,则跑批安排在 08:00 前完成;
  否则拆成两个 job)。
- 夏令时:美东 16:00 收盘 = 北京次日 04:00(夏)/05:00(冬);跑批设 06:00 留缓冲。

## 9. 测试与验收

**单测(Hermes 写)**:
- `select.py`:给定构造的价格+基本面,断言过滤与排序结果(含边界:市值临界、成长缺失剔除)。
- `risk.py`:止损/移动止盈/组合熔断触发与否的判定。
- `simulate.py`:撮合记账、成本/watermark/净值更新、手续费。
- `notify.py`:文案组装(不真发,mock `hermes send`)。

**集成/验收(claude-code 独立复核)**:
1. 用一段历史区间干跑 N 个交易日,检查账本/净值/选股链路自洽,无异常。
2. **故障注入**:临时让 fetch 失败 → 确认 health=DEGRADED、微信收到 `[us-trader] ❌` 告警、恢复后告警停止(对照 dashboard-sync 验收法)。
3. 跑一次真实当日全流程,确认个人微信(weixin,**非 wecom**)收到完整日报。
4. 确认未触碰 A 股 coordinator/accounts/trades,两线互不污染。

## 10. 数据/凭证安全

- 不新增明文密钥入库;tushare token 保持 gitignore;`data/cache/`、`state/` 运行产物
  全部 gitignore(参照 repo-hygiene:可再生数据不入版控)。
- yahooquery 无需 key。

## 11. 升级路径(未来,本版不做)

- **moomoo / Futu OpenAPI**:用户已有账号倾向时,可一步切到"真模拟盘"——
  同时替掉数据源(实时行情)与执行器(真实模拟盘挂单),需跑 OpenD 网关。
- 或 Alpaca Paper(免费 key)。届时仅替换 `fetch_*` 与 `simulate.py`,
  选股/风控/通知层不变。

## 12. 开放参数(请用户在 spec review 时确认/微调)

- 风控默认值:止损 -10% / 移动止盈 +30%(回落 10%)/ 8 只 / 单仓 18% / 初始 $100k。
- 中小盘市值区间默认 $3亿–$100亿。
- 成长阈值(营收/盈利同比)的具体数值。
- 撮合价用收盘价还是次日开盘价。
