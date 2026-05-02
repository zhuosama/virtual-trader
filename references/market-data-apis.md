# A股行情数据 API 参考

## 东方财富 push2 API（推荐 — 免费、稳定、无需密钥）

### 批量查询股票/ETF/指数行情
```
GET https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids}
```

**secid 格式**: `market.code`
- `1.600519` = 上海主板（600/601/603/605 开头）
- `1.688981` = 科创板（688 开头）
- `1.510300` = 上海 ETF（510/512/513 开头）
- `1.110058` = 上海可转债（110/113/123 开头，需确认）
- `0.000858` = 深圳主板（000/001 开头）
- `0.002475` = 深圳中小板（002 开头）
- `0.300750` = 创业板（300 开头）
- `0.159915` = 深圳 ETF（159 开头）
- `0.127015` = 深圳可转债（127/128 开头）

**返回字段**:
- `f2` = 最新价
- `f3` = 涨跌幅(%)
- `f4` = 涨跌额
- `f12` = 证券代码
- `f14` = 证券名称

**示例**:
```python
import subprocess, json

url = "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f12,f14&secids=1.600519,0.300750,1.510300"
result = subprocess.run(["curl", "-s", "-H", "User-Agent: Mozilla/5.0", url], capture_output=True, text=True, timeout=15)
data = json.loads(result.stdout)
for item in data["data"]["diff"]:
    print(f"{item['f12']} {item['f14']}: {item['f2']} ({item['f3']}%)")
```

### 主要指数
- 上证指数: `1.000001`
- 深证成指: `0.399001`
- 创业板指: `0.399006`
- 科创50: `1.000688`

### 单股详情
```
GET https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f43,f44,f45,f46,f57,f58,f169,f170
```
- `f43` = 最新价（需除以100）
- `f170` = 涨跌幅（需除以100）
- `f57` = 代码, `f58` = 名称

### 注意事项
- 可转债 market code 可能需要尝试 `110` 或其他值；push2 的批量查询对可转债可能返回空
- 北向资金和成交额接口不稳定，建议用备用方案（网页搜索）
- 数据是延迟的盘中/盘后数据，非实时 tick

## Yahoo Finance API（全球市场、期货、外汇）

### 指数/期货
```
GET https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d
```

**常用 ticker**:
- `^GSPC` = S&P 500
- `^DJI` = 道琼斯
- `^IXIC` = 纳斯达克
- `ES=F` = 标普500期货
- `NQ=F` = 纳指期货
- `CL=F` = WTI原油
- `GC=F` = 黄金
- `000001.SS` = 上证指数

**返回解析**:
```python
data = json.loads(result.stdout)
meta = data["chart"]["result"][0]["meta"]
price = meta["regularMarketPrice"]
prev_close = meta.get("chartPreviousClose")  # 区间起始日收盘
change_pct = (price - prev_close) / prev_close * 100
```

**注意**: `chartPreviousClose` 是 range 起始日的收盘价（5d 则为 5 天前），计算的是区间涨跌幅而非日涨跌幅。

## 交易日判断

手动判断规则：
- 周末（周六/周天）= 非交易日
- 法定节假日（元旦、春节、清明、五一、端午、中秋、国庆）= 非交易日
- 其他日期 = 交易日

2026 年主要假日：
- 元旦: 1/1
- 春节: 2/17-2/23（待确认具体调休）
- 清明: 4/5（周日），调休 4/6 周一休息
- 五一: 5/1-5/3
- 端午: 6/14（周日）
- 中秋: 9/27（周日）
- 国庆: 10/1-10/7

## 已知问题与故障排除

### 新浪财经 API 返回 Forbidden
**症状**: `hq.sinajs.cn` 返回 `Forbidden`。
**原因**: 该接口已增加反爬/访问限制，在非浏览器环境直接 curl 会被拒绝。
**解决方案**: 降级到腾讯财经 API (`qt.gtimg.cn`)，或使用浏览器爬取。

### 东方财富 push2 API 可能返回空响应
**症状**: `subprocess.run([\"curl\", ...])` 返回空 stdout，无错误信息。
**原因**: 该 API 可能有反爬/地域限制，某些环境下被拦截。macOS sandbox 环境下尤其常见。
**解决方案**:
1. 优先切换到腾讯财经 API (`qt.gtimg.cn`) — 最可靠的免费备选（注意 GBK 编码）
2. 用 `browser_navigate` 浏览东方财富网页获取数据
3. 尝试不同 User-Agent 或添加 Referer header
4. 如果是获取新闻/指数数据，直接爬取东方财富新闻网页

### 腾讯财经 API 返回 GBK 编码
**症状**: `subprocess.run(["curl", ...], text=True)` 抛出 `UnicodeDecodeError: 'gbk' codec`。
**原因**: `qt.gtimg.cn` 返回 GBK 编码，Python 的 `text=True` 默认用 UTF-8 解码。
**解决方案**: 不用 `text=True`，改用 `capture_output=True` 后手动解码：
```python
result = subprocess.run(["curl", "-s", url], capture_output=True)
text = result.stdout.decode("gbk", errors="replace")
```

### 数据获取失败的 fallback 流程
当首选 API 失败时，按以下优先级尝试：

**获取全球市场数据（美股、期货、外汇）**:
```python
# 方案1: Yahoo Finance（推荐）
url = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"

# 方案2: 浏览器访问 investing.com 或 eastmoney.com/global
```

**获取 A 股行情数据**:
```python
# 方案1: 东方财富 push2 API
# 如果失败 →

# 方案2: 新浪财经 API
url = "https://hq.sinajs.cn/list=sh600519,sz300750"

# 方案3: 腾讯财经 API
url = "https://qt.gtimg.cn/q=sh600519"

# 方案4: 浏览器爬取东方财富个股页面
# 注意：盘前/盘后可能显示 "-"，需在交易时间内获取

# 方案5: AKShare Python 库（需先 pip install akshare）
```

**获取 A 股指数**:
```python
# Yahoo Finance 支持上证指数
url = "https://query1.finance.yahoo.com/v8/finance/chart/000001.SS?range=5d&interval=1d"
# 深证成指: 399001.SZ
# 创业板指: 399006.SZ
```

### 浏览器获取数据的注意事项
- 东方财富个股页面在盘前/盘后显示 "-"，交易时间才显示实时数据
- 全球市场数据在东方财富行情中心首页可见（global market section）
- 新闻数据可从东方财富财经新闻列表页获取

## 备选数据源（完整列表）

如果所有 API 均失效：
1. 新浪财经 API: `https://hq.sinajs.cn/list=sh600519,sz300750`
2. 腾讯财经 API: `https://qt.gtimg.cn/q=sh600519`
3. AKShare Python 库（需先 `pip install akshare`）
4. 浏览器直接访问财经网站获取数据
