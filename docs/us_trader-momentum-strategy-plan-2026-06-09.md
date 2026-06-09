# 美股高动量中小盘成长策略 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development 或 executing-plans 逐任务实现。步骤用 `- [ ]` 勾选。
> **实现者:mac/hermes。验收:mac/claude-code。** 本计划是 agent-to-agent 交接:函数签名/数据契约/关键算法/测试断言均给全;路由型 I/O 胶水按契约实现即可,但**不得留 TODO/占位**。

**Goal:** 在 `~/.hermes/virtual-trader/us_trader/` 建一套与 A 股线隔离的美股高动量中小盘成长策略,本地 EOD 模拟撮合,每日次日 08:00(北京)推个人微信。

**Architecture:** 混合数据(tushare 价格 + yahooquery 市值/成长)→ 选股管线 → 风控(止损/移动止盈/组合熔断)→ 本地模拟撮合记账 → 微信日报。纯函数核心 + 薄 I/O 层,逐模块单测;失败走 health + 微信告警(mirror dashboard-sync)。

**Tech Stack:** Python 3(`/usr/bin/python3`,带 pandas/matplotlib);`tushare`;`yahooquery`;`pytest`;`hermes send --to weixin`;hermes cron。

设计稿:`docs/us_trader-momentum-strategy-design-2026-06-09.md`(§编号下引用即该稿)。

---

## 约定(所有任务通用)

- 测试:`us_trader/tests/`,跑 `cd ~/.hermes/virtual-trader && /usr/bin/python3 -m pytest us_trader/tests/ -q`。
- 外部源(tushare/yahooquery/`hermes send`)在单测里**全部 mock**,不打真实网络。
- 数据形状契约:价格面板 = `pandas.DataFrame`,index=交易日(`YYYYMMDD` str 升序),columns=`ts_code`,值=后复权收盘价(float,缺失 `NaN`)。
- 代码风格、token 读取、日志,follow 现有 `scripts/`/`agents/` 习惯。
- 每任务末尾 commit;commit message 用中文 `feat(us_trader): ...` / `test(us_trader): ...`。
- 钱单位一律美元 float;手续费见 Task 6。

---

## Task 0:模块脚手架 + 配置加载

**Files:**
- Create: `us_trader/__init__.py`、`us_trader/config.json`、`us_trader/pipeline/__init__.py`、`us_trader/config.py`
- Create: `us_trader/.gitignore`(内容:`data/cache/`、`state/`、`reports/`、`__pycache__/`)
- Test: `us_trader/tests/test_config.py`

**config.json 默认值(设计 §4/§12 锁定):**
```json
{
  "initial_capital": 100000,
  "holdings_n": 8,
  "max_position_pct": 0.18,
  "stop_loss_pct": -0.10,
  "take_profit_arm_pct": 0.30,
  "take_profit_giveback_pct": 0.10,
  "portfolio_dd_halt_pct": -0.20,
  "max_new_buys_per_day": 3,
  "turnover_warn": 0.40,
  "turnover_block": 0.75,
  "universe": { "price_min": 5.0, "adv_min_usd": 2000000,
                "mcap_min": 300000000, "mcap_max": 10000000000 },
  "growth": { "rev_yoy_min": 0.15, "earn_yoy_min": 0.0 },
  "momentum": { "lookback_3m": 63, "lookback_6m": 126, "w_3m": 0.5, "w_6m": 0.5 },
  "fill": { "price": "close", "commission_per_share": 0.005, "commission_min": 1.0 },
  "weixin_target": "weixin",
  "tz_run_hour_bjt": 6, "tz_notify_hour_bjt": 8
}
```

- [ ] **Step 1: 写失败测试** `test_config.py`
```python
from us_trader.config import load_config
def test_defaults_present():
    c = load_config()
    assert c["holdings_n"] == 8
    assert c["stop_loss_pct"] == -0.10
    assert c["universe"]["mcap_min"] == 300_000_000
    assert c["weixin_target"] == "weixin"
def test_override(tmp_path):
    p = tmp_path/"c.json"; p.write_text('{"holdings_n": 5}')
    c = load_config(str(p))
    assert c["holdings_n"] == 5            # 覆盖项
    assert c["max_position_pct"] == 0.18   # 未覆盖回落默认
```
- [ ] **Step 2: 跑测试确认 FAIL**（`ModuleNotFoundError: us_trader.config`）
- [ ] **Step 3: 实现** `config.py`:`load_config(path=None)` 读内置默认(= 上面 json),若给 path 则 `dict` 深合并覆盖;返回合并后 dict。建 `config.json` 落盘默认值。
- [ ] **Step 4: 跑测试确认 PASS**
- [ ] **Step 5: Commit** `git add us_trader/ && git commit -m "feat(us_trader): 模块脚手架 + 配置加载"`

---

## Task 1:价格/日历数据(tushare)

**Files:**
- Create: `us_trader/pipeline/fetch_prices.py`
- Test: `us_trader/tests/test_fetch_prices.py`

**契约:**
- `get_trading_days(end_yyyymmdd, n) -> list[str]`:用 `us_tradecal` 取截至 end 的最近 n 个开市日(升序 `YYYYMMDD`)。
- `is_trading_day(yyyymmdd) -> bool`:`us_tradecal` 当日 `is_open==1`。
- `fetch_price_panel(codes, start, end) -> DataFrame`:对每个 code 调 `us_daily`,拼成价格面板(契约见"约定")。单票失败 → 记 warning、该列全 `NaN`,不抛。
- token 复用 `scripts/.tushare_token`/`TUSHARE_TOKEN`(参照 `scripts/h49a_build_tushare_sw_industry.py:get_tushare_token`)。

- [ ] **Step 1: 写失败测试**(mock `tushare.pro_api`):
```python
import pandas as pd
from unittest.mock import patch, MagicMock
from us_trader.pipeline import fetch_prices as fp

def _fake_pro():
    pro = MagicMock()
    def query(api, **kw):
        if api == "us_tradecal":
            return pd.DataFrame({"cal_date":["20260601","20260602","20260603"],
                                 "is_open":[1,0,1]})
        if api == "us_daily":
            return pd.DataFrame({"trade_date":["20260601","20260603"],
                                 "close":[10.0, 11.0], "ts_code":[kw["ts_code"]]*2})
        return pd.DataFrame()
    pro.query.side_effect = query
    return pro

def test_trading_days_filters_open():
    with patch.object(fp, "_pro", return_value=_fake_pro()):
        days = fp.get_trading_days("20260603", 5)
    assert days == ["20260601","20260603"]      # 02 闭市被滤掉

def test_price_panel_shape():
    with patch.object(fp, "_pro", return_value=_fake_pro()):
        panel = fp.fetch_price_panel(["AAPL","MSFT"], "20260601","20260603")
    assert list(panel.columns) == ["AAPL","MSFT"]
    assert panel.loc["20260603","AAPL"] == 11.0
```
- [ ] **Step 2: 跑测试确认 FAIL**
- [ ] **Step 3: 实现** `fetch_prices.py`:`_pro()` 返回缓存的 `ts.pro_api()`(set_token);三个函数按契约;`fetch_price_panel` 用 `pd.concat` 对齐 trade_date。
- [ ] **Step 4: PASS**
- [ ] **Step 5: Commit** `test(us_trader): 价格/日历 + 实现`

---

## Task 2:市值/成长基本面(yahooquery)

**Files:**
- Create: `us_trader/pipeline/fetch_fundamentals.py`
- Test: `us_trader/tests/test_fetch_fundamentals.py`

**契约:**
- `fetch_fundamentals(codes, batch=50) -> dict[str, dict]`:每 code →
  `{"market_cap": float|None, "rev_yoy": float|None, "earn_yoy": float|None, "sector": str|None}`。
- 用 yahooquery `Ticker(codes).summary_detail / key_stats / income_statement` 批量取;
  单票/单字段缺失 → `None`(不抛);整批异常 → 记 warning 返回已得部分。
- ts_code → yahoo symbol 映射:tushare 美股 `ts_code` 形如 `AAPL`(无后缀)或 `AAPL.O`;
  实现 `to_yahoo_symbol(ts_code)`(去掉 `.O/.N` 等交易所后缀)。

- [ ] **Step 1: 写失败测试**(mock yahooquery 的 `Ticker`):
```python
from unittest.mock import patch, MagicMock
from us_trader.pipeline import fetch_fundamentals as ff

def test_symbol_mapping():
    assert ff.to_yahoo_symbol("AAPL.O") == "AAPL"
    assert ff.to_yahoo_symbol("BRK.A") == "BRK.A"   # 不误删非交易所后缀

def test_fetch_handles_missing():
    fake = MagicMock()
    fake.summary_detail = {"AAPL": {"marketCap": 3.0e12},
                           "BAD": "Quote not found"}     # yahooquery 缺失返回 str
    with patch.object(ff, "_ticker", return_value=fake), \
         patch.object(ff, "_growth", return_value={"AAPL":(0.2,0.1),"BAD":(None,None)}):
        out = ff.fetch_fundamentals(["AAPL.O","BAD"])
    assert out["AAPL"]["market_cap"] == 3.0e12
    assert out["AAPL"]["rev_yoy"] == 0.2
    assert out["BAD"]["market_cap"] is None
```
- [ ] **Step 2: FAIL** → **Step 3: 实现**(`to_yahoo_symbol` 只剥 `.O/.N/.A?` 交易所后缀白名单:`{O,N,K,P}`,保留 `BRK.A/BRK.B` 这类;`_ticker`/`_growth` 薄封装便于 mock;成长率从 income_statement 同比算或取 key_stats 现成增长字段)→ **Step 4: PASS** → **Step 5: Commit**

---

## Task 3:票池种子(universe.csv)

**Files:**
- Create: `us_trader/data/universe.csv`(列:`ts_code,name`)
- Create: `us_trader/pipeline/universe.py`
- Test: `us_trader/tests/test_universe.py`

**做法(MVP):** 不对全 6000 只拉基本面。维护**静态种子票池**(中小盘成长候选,
约 300–500 只,Hermes 从公开小盘成长票池/常见小盘成长名单整理,逐行 `ts_code,name`)。
- `load_universe() -> list[dict]`:读 csv → `[{"ts_code","name"}]`。
- `refresh_universe_from_tushare(classify="EQ")`(可选工具):用 `us_basic` 过滤普通股写种子,**不在每日链路调用**,仅人工刷新。

- [ ] **Step 1: 测试** `load_universe` 至少返回 N>50 行且字段完整;csv 无重复 ts_code。
- [ ] **Step 2-4:** 实现 + 落一份**真实种子 csv**(≥300 行,Hermes 整理;若一时不全先放 ≥50 行可跑通,并在 handoff 回执注明"种子待扩充")。
- [ ] **Step 5: Commit** `feat(us_trader): 票池种子 + 加载`

---

## Task 4:选股管线(纯函数,可单测)

**Files:**
- Create: `us_trader/pipeline/select.py`
- Test: `us_trader/tests/test_select.py`

**契约:** `select(panel, fundamentals, config, as_of) -> list[dict]`
返回**已排序**候选(动量分降序),每项:
`{"ts_code","momentum_score","rev_yoy","earn_yoy","market_cap","passed":bool,"reasons":[...]}`,
仅 `passed=True` 进入目标持仓;取前 `holdings_n` 个 passed。

**算法(spell out):**
1. 流动性:`price = panel[code].iloc[-1]`;`price >= price_min` 且
   `adv = (panel[code].diff().abs()` 不是额…) →` **用收盘价×假定量不可得**,故 ADV 用
   `panel` 无量。**改:流动性仅用 `price_min`**(无量数据时);量过滤留待升级到带量源。
   → reasons 记 `"price<min"` 时剔除。
2. 中小盘:`mcap_min <= market_cap <= mcap_max`,缺失 `market_cap` → 剔除(reason `"mcap missing"`)。
3. 成长:`rev_yoy >= rev_yoy_min` 且 `earn_yoy >= earn_yoy_min`,缺失视作不满足剔除。
4. 动量分:
   `ret_3m = price/panel[code].iloc[-1-lookback_3m] - 1`(数据不足该 code 剔除,reason `"insufficient history"`);
   `ret_6m` 同理;`momentum_score = w_3m*ret_3m + w_6m*ret_6m`。
5. 对全部 `passed=True` 按 `momentum_score` 降序;调用方取前 N。

- [ ] **Step 1: 写失败测试**(构造确定性输入):
```python
import pandas as pd, numpy as np
from us_trader.pipeline.select import select
from us_trader.config import load_config

def _panel():
    days = [f"2026{m:02d}{d:02d}" for m in (1,2,3,4,5,6) for d in (1,15)]  # 12 行
    idx = sorted(days)
    # A 强动量, B 弱动量, C 大盘(应被市值剔除)
    return pd.DataFrame({
        "A":[10+ i for i in range(12)],     # 单调上涨
        "B":[20- 0.1*i for i in range(12)],  # 下跌
        "C":[50]*12,
    }, index=idx)

def test_select_ranks_and_filters():
    c = load_config(); c["momentum"]["lookback_3m"]=3; c["momentum"]["lookback_6m"]=6
    fund = {"A":{"market_cap":1e9,"rev_yoy":0.3,"earn_yoy":0.2,"sector":"Tech"},
            "B":{"market_cap":1e9,"rev_yoy":0.3,"earn_yoy":0.2,"sector":"Tech"},
            "C":{"market_cap":5e10,"rev_yoy":0.3,"earn_yoy":0.2,"sector":"Tech"}}
    out = select(_panel(), fund, c, as_of="20260615")
    passed = [r["ts_code"] for r in out if r["passed"]]
    assert passed[0] == "A"           # 最强动量排第一
    assert "C" not in passed          # 市值超上限被剔除
    crow = next(r for r in out if r["ts_code"]=="C")
    assert "mcap" in " ".join(crow["reasons"]).lower()

def test_missing_growth_excluded():
    c = load_config(); c["momentum"]["lookback_3m"]=3; c["momentum"]["lookback_6m"]=6
    fund = {"A":{"market_cap":1e9,"rev_yoy":None,"earn_yoy":0.2,"sector":"Tech"}}
    out = select(_panel()[["A"]], fund, c, as_of="20260615")
    assert all(not r["passed"] for r in out)
```
- [ ] **Step 2: FAIL** → **Step 3: 实现** select(按算法)→ **Step 4: PASS** → **Step 5: Commit** `feat(us_trader): 选股管线`

---

## Task 5:风控(止损/移动止盈/组合熔断)

**Files:**
- Create: `us_trader/pipeline/risk.py`
- Test: `us_trader/tests/test_risk.py`

**契约:**
- `position_exit_signals(positions, prices, config) -> dict[code,{"action":"sell"|None,"reason":str}]`
  - positions: `{code:{"shares","cost","high_watermark"}}`;prices:`{code: 现价}`。
  - 止损:`(price-cost)/cost <= stop_loss_pct` → sell,reason `"stop_loss"`。
  - 移动止盈:浮盈曾达 `take_profit_arm_pct`(用 high_watermark 判断:`(hw-cost)/cost >= arm`)
    且现价自 hw 回落 `(hw-price)/hw >= giveback` → sell,reason `"trailing_take_profit"`。
  - 否则 `action=None`。
- `portfolio_halted(nav_history, config) -> bool`:从峰值算当前回撤 `<= portfolio_dd_halt_pct` → True(禁新开仓)。
- `update_watermark(positions, prices)`:`high_watermark = max(high_watermark, price)`(纯函数返回新 dict)。

- [ ] **Step 1: 写失败测试**:
```python
from us_trader.pipeline.risk import position_exit_signals, portfolio_halted, update_watermark
from us_trader.config import load_config
c = load_config()
def test_stop_loss():
    pos={"A":{"shares":100,"cost":10,"high_watermark":10}}
    sig=position_exit_signals(pos, {"A":8.9}, c)   # -11% < -10%
    assert sig["A"]["action"]=="sell" and sig["A"]["reason"]=="stop_loss"
def test_trailing_tp():
    pos={"A":{"shares":100,"cost":10,"high_watermark":14}}  # 曾 +40% 已 arm(>30%)
    sig=position_exit_signals(pos, {"A":12.5}, c)  # 自 14 回落 ~10.7% > 10%
    assert sig["A"]["action"]=="sell" and sig["A"]["reason"]=="trailing_take_profit"
def test_tp_not_armed():
    pos={"A":{"shares":100,"cost":10,"high_watermark":11}}  # 仅 +10%,未 arm
    assert position_exit_signals(pos, {"A":10.2}, c)["A"]["action"] is None
def test_portfolio_halt():
    assert portfolio_halted([{"nav":100},{"nav":120},{"nav":95}], c) is True   # 自 120 回撤 -20.8%
    assert portfolio_halted([{"nav":100},{"nav":110}], c) is False
```
- [ ] **Step 2: FAIL** → **Step 3: 实现** → **Step 4: PASS** → **Step 5: Commit** `feat(us_trader): 风控止损/移动止盈/熔断`

---

## Task 6:本地模拟撮合 + 记账

**Files:**
- Create: `us_trader/pipeline/simulate.py`
- Test: `us_trader/tests/test_simulate.py`

**契约:** `step(state, target_codes, prices, exit_signals, config, date) -> (new_state, trades)`
- `state`:`{"cash","positions":{code:{shares,cost,high_watermark}},"nav_history":[...]}`。
- 顺序:① 先执行 `exit_signals` 的 sell(止损/止盈)与"跌出 target 的持仓"卖出;
  ② 若未 `portfolio_halted`,对 target 中未持仓的按**等权目标**买入,受 `max_position_pct`、
  `max_new_buys_per_day`、现金、`turnover_block` 限制;③ 重算 nav。
- 撮合价 = `prices[code]`(收盘价,配置可换次日开盘)。
- 手续费 = `max(commission_min, commission_per_share*shares)`;买扣现金、卖加现金。
- 卖出算 `realized_pnl=(price-cost)*shares-费`;买入设 `cost=price`、`high_watermark=price`。
- trades:`[{date,code,side,shares,price,amount,commission,realized_pnl,reason}]`。
- nav_history 追加 `{date, nav=cash+Σshares*price, ret, cum_ret, drawdown}`。

- [ ] **Step 1: 写失败测试**(确定性小场景):
```python
from us_trader.pipeline.simulate import step
from us_trader.config import load_config
c=load_config(); c["holdings_n"]=2; c["max_new_buys_per_day"]=5
def test_buy_then_stop_loss_sell():
    st={"cash":100000,"positions":{},"nav_history":[]}
    st,tr=step(st,["A","B"],{"A":10,"B":20},{},c,"20260601")
    assert set(st["positions"])=={"A","B"}
    # 单仓不超 18%
    assert st["positions"]["A"]["shares"]*10 <= 100000*0.18 + 1
    # 次日 A 触发止损卖出
    st,tr=step(st,["A","B"],{"A":8.5,"B":20},
               {"A":{"action":"sell","reason":"stop_loss"}},c,"20260602")
    assert "A" not in st["positions"]
    assert any(t["side"]=="sell" and t["reason"]=="stop_loss" for t in tr)
def test_nav_recorded():
    st={"cash":100000,"positions":{},"nav_history":[]}
    st,_=step(st,["A"],{"A":10},{},c,"20260601")
    assert abs(st["nav_history"][-1]["nav"] - (st["cash"]+st["positions"]["A"]["shares"]*10)) < 1e-6
```
- [ ] **Step 2: FAIL** → **Step 3: 实现** → **Step 4: PASS** → **Step 5: Commit** `feat(us_trader): 本地模拟撮合记账`

---

## Task 7:微信日报组装 + 发送

**Files:**
- Create: `us_trader/pipeline/notify.py`
- Test: `us_trader/tests/test_notify.py`

**契约:**
- `build_digest(state, trades, selection, health, date) -> str`(Markdown,设计 §6 五段)。
- `send_weixin(subject, body, config)`:`subprocess.run(["~/.local/bin/hermes"(展开),"send","--to",config["weixin_target"],"--subject",subject], input=body, ...)`;`capture_output`,**校验 returncode!=0 时记 warning 并返回 False**(修正 A 股版只吞不查的问题);异常不抛。
- 发送目标固定 `config["weixin_target"]`(默认 `"weixin"`,**非 wecom**)。

- [ ] **Step 1: 写失败测试**:
```python
from unittest.mock import patch, MagicMock
from us_trader.pipeline import notify
def test_digest_has_sections():
    st={"cash":50000,"positions":{"A":{"shares":100,"cost":10,"high_watermark":12}},
        "nav_history":[{"nav":100000,"ret":0,"cum_ret":0,"drawdown":0},
                       {"nav":101000,"ret":0.01,"cum_ret":0.01,"drawdown":0}]}
    md=notify.build_digest(st,[{"date":"20260602","code":"A","side":"buy","shares":100,
                                "price":10,"reason":"new"}],
                           [{"ts_code":"A","momentum_score":0.3,"passed":True}],
                           {"success":True}, "20260602")
    for kw in ["复盘","持仓","变动","选股","风险"]:
        assert kw in md
def test_send_checks_returncode():
    with patch.object(notify.subprocess,"run",return_value=MagicMock(returncode=1,stderr="x")):
        assert notify.send_weixin("s","b",{"weixin_target":"weixin"}) is False
```
- [ ] **Step 2: FAIL** → **Step 3: 实现** → **Step 4: PASS** → **Step 5: Commit** `feat(us_trader): 微信日报组装+发送(校验返回码)`

---

## Task 8:每日编排 + health + 失败告警

**Files:**
- Create: `us_trader/pipeline/run_daily.py`
- Create: `us_trader/state/`(运行时生成,gitignore)
- Test: `us_trader/tests/test_run_daily.py`

**契约:** `run_daily(date=None, config_path=None) -> dict`
- 步骤:`is_trading_day` 判空→非交易日直接 return `{"skipped":True}`;
  否则 fetch_prices → fetch_fundamentals → select → load state → risk → simulate →
  持久化 state/trades/nav/positions → build_digest+send_weixin → 写 health。
- 每步包 try;任一步失败 → `write_health(success=False, failed_step, error)` +
  `notify.send_weixin("[us-trader] ❌ <step>", <摘要>, config)`(失败告警),return。
- 成功:`write_health(success=True)`;若上轮 health=False 则发一次 `"[us-trader] ✅ recovered"`(去重,mirror dashboard-sync)。
- `write_health(...)` 写 `us_trader/state/health.json`:`{ts,success,failed_step,error}`。

- [ ] **Step 1: 写失败测试**(全 mock 各 pipeline 函数):
```python
from unittest.mock import patch
from us_trader.pipeline import run_daily as rd
def test_happy_path_writes_health(tmp_path, monkeypatch):
    monkeypatch.setattr(rd,"STATE_DIR",str(tmp_path))
    with patch.object(rd.fetch_prices,"is_trading_day",return_value=True), \
         patch.object(rd.fetch_prices,"fetch_price_panel"), \
         patch.object(rd.fetch_fundamentals,"fetch_fundamentals",return_value={}), \
         patch.object(rd,"_select",return_value=[]), \
         patch.object(rd.notify,"send_weixin",return_value=True) as snd:
        out=rd.run_daily("20260605")
    import json,os
    h=json.load(open(os.path.join(tmp_path,"health.json")))
    assert h["success"] is True
def test_fetch_failure_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(rd,"STATE_DIR",str(tmp_path))
    with patch.object(rd.fetch_prices,"is_trading_day",return_value=True), \
         patch.object(rd.fetch_prices,"fetch_price_panel",side_effect=RuntimeError("net")), \
         patch.object(rd.notify,"send_weixin",return_value=True) as snd:
        rd.run_daily("20260605")
    import json,os
    h=json.load(open(os.path.join(tmp_path,"health.json")))
    assert h["success"] is False and h["failed_step"]=="fetch_prices"
    assert any("❌" in c.args[0] for c in snd.call_args_list)   # 发了告警
```
- [ ] **Step 2: FAIL** → **Step 3: 实现** → **Step 4: PASS** → **Step 5: Commit** `feat(us_trader): 每日编排+health+失败告警`

---

## Task 9:cron 接入(hermes)

**Files:**
- Modify: `~/.hermes/cron/jobs.json`(新增 1 个 job)
- Create: `us_trader/run.sh`(`set -euo pipefail`;`cd` 仓库;`/usr/bin/python3 -m us_trader.pipeline.run_daily`)

**做法:** 新增 cron `美股-每日选股复盘`,`cron 0 22 * * 0-4`(UTC 22:00 = 北京次日 06:00,工作日夜=对应美股交易日;`run_daily` 内部再用 `us_tradecal` 兜底判交易日),驱动 `run.sh`。通知在 `run_daily` 内 06:xx 直接发(若用户坚持 08:00 再拆独立 notify job;设计 §8 允许)。失败由 §Task8 的微信告警覆盖。

- [ ] **Step 1:** 写 `run.sh`,`chmod +x`。
- [ ] **Step 2:** 用 hermes cron 既有方式新增 job(search→存在则更新→不存在则建,参照 jobs.json 现有结构;**搜索去重避免重复 job**)。
- [ ] **Step 3:** 干跑 `bash us_trader/run.sh`(非交易日应 skip,交易日应跑通并写 health)。
- [ ] **Step 4: Commit**(`run.sh` 入库;`jobs.json` 在 ~/.hermes 非本仓,记录在回执)。

---

## Task 10:集成干跑 + 验收交接(claude-code)

- [ ] **Step 1:** 历史区间干跑:对最近 ~10 个美股交易日顺序跑 `run_daily`(可临时把发送 mock 掉或指向自己),检查 nav_history 单调记录、trades 自洽、选股 reasons 合理、无异常栈。
- [ ] **Step 2:** **故障注入**:临时让 `fetch_price_panel` 抛错跑一次 → 确认 `state/health.json` `success=false/failed_step=fetch_prices` 且**个人微信收到 `[us-trader] ❌ fetch_prices`**;恢复后再跑成功 → 收到 `✅ recovered`。
- [ ] **Step 3:** 真实当日全流程跑一次 → 确认**个人微信(weixin,非 wecom)**收到完整五段日报。
- [ ] **Step 4:** 确认**未触碰** A 股 `agents/coordinator.py`/`accounts/`/`trades/`(A 股账本)/tushare A 股脚本;`git status` 里 us_trader 之外无意外改动。
- [ ] **Step 5:** 全量测试 `/usr/bin/python3 -m pytest us_trader/tests/ -q` 全绿;贴结果。

---

## 落盘回执(Hermes 写到 `docs/us_trader-impl-report-2026-06-09.md`)
- 各 Task 的测试结果、关键 diff、最终 `pytest` 摘要。
- 票池种子实际行数(若 <300,标注待扩充)。
- cron job 的 id/schedule。
- Task 10 验收 5 项逐项结果(尤其故障注入告警截图/日志、微信收到日报的证据)。
- 任何与本计划不符之处(如 yahooquery 字段名差异)→ 在回执纠正并说明取舍。

## Self-review(已过)
- Spec 覆盖:§1 架构→Task0/8;§2 数据→Task1/2/3;§3 选股→Task4;§4 风控/仓位→Task5/6;§5 撮合→Task6;§6 通知→Task7;§7 health/告警→Task8;§8 调度→Task9;§9 测试→各 Task+Task10;§10 安全→Task0(.gitignore)+Task1(token);§11 升级路径=非目标不实现。✓
- 占位:无 TODO;票池种子允许先 ≥50 行但要求回执标注(非占位,是显式可交付降级)。✓
- 类型一致:`positions[code]` 统一 `{shares,cost,high_watermark}`;`select` 输出键、`step` 签名、`health` 形状跨 Task 一致。✓
- 已知取舍:流动性 ADV 过滤因 tushare 无量数据**降级为仅 price_min**,量过滤留待升级带量源(Task4 注明)。
