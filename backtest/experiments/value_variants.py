"""
Value Strategy 变体定义

四种价值策略变体 (使用simulator支持的参数):
1. quality_value: 集中持股+紧止损+高止盈 (模拟高ROE/低负债筛选)
2. deep_value: 宽止损+大仓位+高止盈 (模拟深度价值)
3. qarp: 均衡质量+高仓位+适中止盈 (模拟质量溢价合理)
4. fcf_strength: 分散+紧止损+中等仓位 (模拟FCF+资产负债表防御)
"""

VALUE_VARIANTS = {
    "quality_value": {
        "diff": [
            {"path": "parameters.take_profit_pct", "old": 15, "new": 20},
            {"path": "parameters.stop_loss_pct", "old": 7, "new": 5},
            {"path": "rules.position_sizing.max_single_position", "old": 0.08, "new": 0.10},
            {"path": "rules.position_sizing.initial_position", "old": 0.08, "new": 0.10},
            {"path": "parameters.max_single_batch", "old": 0.08, "new": 0.10},
        ],
        "hooks": [],
        "rationale": "Quality Value: 集中持仓(10%) + 紧止损(5%) + 高止盈(20%)。模拟高ROE+低负债的精选组合。",
        "hypothesis": "更严格质量筛选提升胜率，集中持仓放大收益"
    },
    "deep_value": {
        "diff": [
            {"path": "parameters.take_profit_pct", "old": 15, "new": 25},
            {"path": "parameters.stop_loss_pct", "old": 7, "new": 8},
            {"path": "rules.position_sizing.max_single_position", "old": 0.08, "new": 0.12},
            {"path": "rules.position_sizing.initial_position", "old": 0.08, "new": 0.12},
            {"path": "parameters.max_single_batch", "old": 0.08, "new": 0.12},
        ],
        "hooks": [],
        "rationale": "Deep Value: 大仓位(12%) + 宽止损(8%) + 高止盈(25%)。模拟低PE/PB+高股息的深度价值。",
        "hypothesis": "低估值+高仓位=均值回归收益"
    },
    "qarp": {
        "diff": [
            {"path": "parameters.stop_loss_pct", "old": 7, "new": 6},
            {"path": "parameters.base_position_target", "old": 0.65, "new": 0.70},
            {"path": "parameters.min_single_batch", "old": 0.04, "new": 0.05},
            {"path": "rules.position_sizing.max_single_position", "old": 0.08, "new": 0.08},
            {"path": "rules.position_sizing.initial_position", "old": 0.08, "new": 0.07},
        ],
        "hooks": [],
        "rationale": "QARP: 高质量(紧止损6%) + 合理仓位(70%总仓, 7-8%单股) + 分批建仓。平衡攻击与防御。",
        "hypothesis": "质量溢价+高仓位=稳健超额收益"
    },
    "fcf_strength": {
        "diff": [
            {"path": "parameters.stop_loss_pct", "old": 7, "new": 5},
            {"path": "parameters.take_profit_pct", "old": 15, "new": 18},
            {"path": "rules.position_sizing.max_single_position", "old": 0.08, "new": 0.06},
            {"path": "rules.position_sizing.initial_position", "old": 0.08, "new": 0.06},
            {"path": "parameters.base_position_target", "old": 0.65, "new": 0.55},
            {"path": "parameters.min_single_batch", "old": 0.04, "new": 0.05},
        ],
        "hooks": [],
        "rationale": "FCF+BS: 保守(紧止损5%) + 分散(6%单股) + 中等仓位(55%) + 适度止盈(18%)。防御型价值。",
        "hypothesis": "低回撤+正收益，适合震荡/熊市"
    },
}
