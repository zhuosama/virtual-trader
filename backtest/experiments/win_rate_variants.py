"""
Win-Rate 实验变体定义

每个变体是一个 dict: {"diff": [...], "hooks": [...]}
- diff: 参数级别的变更 (可被 strategy_simulator.apply_supported_diff 处理)
- hooks: 行为级别的变更 (通过 simulate_strategy_with_hooks 注入)
"""

WIN_RATE_VARIANTS = {
    "market_regime_filter": {
        "diff": [],
        "hooks": ["market_regime_filter"],
        "rationale": "仅当HS300在MA20之上时交易，过滤熊市和震荡市中的入场信号",
        "hypothesis": "市场环境过滤减少假突破，提高胜率>40%"
    },
    "signal_confirmation": {
        "diff": [],
        "hooks": ["signal_confirmation"],
        "rationale": "要求短期MA(5) > 中期MA(10)作为入场确认，减少单信号误判",
        "hypothesis": "双重确认减少噪音交易，胜率提升+10pp"
    },
    "trade_quality_score": {
        "diff": [],
        "hooks": ["trade_quality_score"],
        "rationale": "用四维质量评分（动量/一致性/强度/波动率）过滤低分入场",
        "hypothesis": "质量过滤筛选优质入场，盈亏比改善"
    },
    "volatility_adjusted_stop": {
        "diff": [],
        "hooks": ["volatility_stop"],
        "rationale": "用ATR动态调整止损幅度，高波动时放宽止损避免噪音止损",
        "hypothesis": "动态止损减少过早止损，实现盈亏改善"
    },
    "cooldown_after_loss": {
        "diff": [],
        "hooks": ["cooldown_after_loss"],
        "rationale": "亏损交易后强制冷却3个交易日，避免情绪化连续交易",
        "hypothesis": "冷却期减少连亏概率，总亏损降低"
    },
    "tighter_stop": {
        "diff": [
            {"path": "parameters.stop_loss_pct", "old": 7, "new": 5},
        ],
        "hooks": [],
        "rationale": "止损收紧至5%，控制单笔最大亏损",
        "hypothesis": "更紧止损减少单笔亏损，但可能增加交易频率"
    },
    "wider_stop": {
        "diff": [
            {"path": "parameters.stop_loss_pct", "old": 7, "new": 10},
        ],
        "hooks": [],
        "rationale": "止损放宽至10%，给持有标更多恢复空间",
        "hypothesis": "更宽止损减少止损触发的假信号，但单笔亏损更大"
    },
    "smaller_positions": {
        "diff": [
            {"path": "rules.position_sizing.max_single_position", "old": 0.08, "new": 0.05},
            {"path": "rules.position_sizing.initial_position", "old": 0.08, "new": 0.05},
        ],
        "hooks": [],
        "rationale": "仓位缩小至5%，分散风险提高组合稳定性",
        "hypothesis": "分散持仓降低波动，夏普比率提升"
    },
    "combined_best": {
        "diff": [
            {"path": "parameters.stop_loss_pct", "old": 7, "new": 10},
        ],
        "hooks": ["market_regime_filter", "signal_confirmation", "cooldown_after_loss"],
        "rationale": "组合最佳hook: 市场过滤+双重信号+冷却期+更宽止损",
        "hypothesis": "多维度过滤叠加提升综合表现"
    },
}
