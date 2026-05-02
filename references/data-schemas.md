# 数据文件 Schema 参考

## accounts/*.json

```json
{
  "id": "main | lab",
  "name": "账户中文名",
  "initial_capital": 1000000,
  "cash": 823500,
  "positions": [
    {
      "code": "600519",
      "name": "贵州茅台",
      "type": "stock | etf | convertible_bond",
      "shares": 100,
      "avg_cost": 1765.0,
      "current_price": 1802.3,
      "unrealized_pnl": 3730,
      "open_date": "2026-04-10",
      "sector": "白酒"
    }
  ],
  "total_value": 1003730,
  "total_pnl": 3730,
  "total_pnl_pct": 0.373,
  "max_drawdown": -1.2,
  "trade_count": 15,
  "win_count": 9,
  "loss_count": 6,
  "win_rate": 0.6,
  "sharpe_ratio": 1.5,
  "inception_date": "2026-04-14",
  "updated_at": "2026-04-14T15:35:00"
}
```

## trades/YYYY-MM/YYYY-MM-DD.json

见 SKILL.md 中"交易记录格式"一节。

## strategies/active.json

```json
{
  "main": {
    "name": "策略名称",
    "description": "策略简述",
    "parameters": {
      "key": "value — 具体参数由策略类型决定"
    },
    "last_updated": "2026-04-14"
  },
  "lab": {
    "name": "实验策略名称",
    "description": "实验策略简述",
    "parameters": {},
    "last_updated": "2026-04-14"
  }
}
```

## strategies/changelog.json

```json
[
  {
    "date": "2026-04-14",
    "account": "main | lab",
    "level": "parameter_tweak | rule_iteration | strategy_replacement",
    "change": "变更描述",
    "reason": "变更原因（基于什么数据/观察）",
    "expected_effect": "预期效果",
    "actual_effect": null
  }
]
```

`actual_effect` 在后续复盘中回填。

## market-data/watchlist.json

```json
{
  "stocks": [
    {"code": "600519", "name": "贵州茅台", "sector": "白酒", "added": "2026-04-14", "reason": "行业龙头"}
  ],
  "etfs": [
    {"code": "510300", "name": "沪深300ETF", "sector": "宽基", "added": "2026-04-14", "reason": "大盘指标"}
  ],
  "convertible_bonds": [
    {"code": "127000", "name": "某转债", "sector": "XX", "added": "2026-04-14", "reason": "低溢价"}
  ],
  "last_updated": "2026-04-14"
}
```
